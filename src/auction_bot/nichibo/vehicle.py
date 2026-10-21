"""Fetch a Nichibo detail page, its auction-sheet translation and its images."""
from __future__ import annotations

import json
import time
from pathlib import Path

from auction_bot.config import Config
from auction_bot.nichibo.browser import Browser, NotLoggedIn
from auction_bot.nichibo.cache import NoCache, PageCache
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.nichibo.parsers import parse_detail_page

_XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01"}


def detail_url(cfg: Config, ref_no: str) -> str:
    base = cfg.nichibo.get("base_url", "https://autosearch.nichibojapan.com").rstrip("/")
    return f"{base}/auction/{ref_no}"


def fetch_facts(browser: Browser, cfg: Config, ref_no: str, cache: PageCache | None = None) -> VehicleFacts:
    cache = cache or NoCache()
    url = detail_url(cfg, ref_no)
    html = cache.get(url)
    if html is None:
        html = browser.goto(url)
        if "/login" in browser.page.url:
            raise NotLoggedIn("Nichibo redirected to the login page. Run `bot login`.")
        cache.put(url, html)
    facts = parse_detail_page(html, url)
    facts.translator = fetch_translation(browser, cfg, facts, cache)
    return facts


def fetch_translation(browser: Browser, cfg: Config, facts: VehicleFacts, cache: PageCache | None = None) -> dict | None:
    """Nichibo's own auction-sheet translator (English text of the sheet)."""
    if not facts.sheet_url:
        return None
    cache = cache or NoCache()
    key = "translator:" + facts.sheet_url
    cached = cache.get_json(key)
    if cached is not None:
        return cached or None
    base = cfg.nichibo.get("base_url", "https://autosearch.nichibojapan.com").rstrip("/")
    try:
        r = browser.get(f"{base}/auction/jsonAuctionSheetData", headers=_XHR, params={
            "AuctionSheet": facts.sheet_url,
            "AuctionHall": facts.auction_place,
            "ExhibitNo": facts.exhibit_no,
            "AuctionDate": facts.auction_date,
        })
        if not r.ok:
            return {"error": f"HTTP {r.status}"}
        data = json.loads(r.text())
    except Exception as exc:  # network / JSON problems are non-fatal
        return {"error": str(exc)[:200]}
    if data.get("Type") == "Error":
        return {"error": f"{data.get('Code')}: {data.get('Message')}"}
    words = data.get("WordsEN") or {}
    result = {k: v for k, v in words.items() if v} | ({"sheet_url": data.get("AuctionSheetURL")} if data.get("AuctionSheetURL") else {})
    cache.put_json(key, result)
    return result


_MAGIC = {
    b"\xff\xd8\xff": ("image/jpeg", ".jpg"),
    b"\x89PNG": ("image/png", ".png"),
    b"GIF8": ("image/gif", ".gif"),
    b"RIFF": ("image/webp", ".webp"),
}


def sniff_image(data: bytes) -> tuple[str, str] | None:
    for magic, info in _MAGIC.items():
        if data.startswith(magic):
            return info
    return None


def image_dims(data: bytes) -> tuple[int, int] | None:
    import struct
    if data[:3] == b"GIF" and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    return None


def is_placeholder(data: bytes) -> bool:
    """Nichibo serves a small 400x300 GIF ("image coming soon") until the auction house
    publishes the real photos and sheet. Real sheets are 100 KB+ JPEGs."""
    if len(data) < 12_000:
        return True
    dims = image_dims(data)
    return dims is not None and dims[0] <= 400 and dims[1] <= 300


def load_saved_evidence(cfg: Config, ref_no: str) -> dict | None:
    """The evidence bundle saved by an earlier download, if its files are still there."""
    out_dir = cfg.evidence_dir / _safe(ref_no)
    meta = out_dir / "evidence.json"
    if not meta.exists():
        return None
    try:
        ev = json.loads(meta.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if ev.get("sheet") and not Path(ev["sheet"]).exists():
        return None
    ev["photos"] = [p for p in ev.get("photos", []) if Path(p).exists()]
    return ev


def download_evidence(browser: Browser, cfg: Config, facts: VehicleFacts, max_photos: int,
                      cache: PageCache | None = None) -> dict:
    """Save sheet + photos under evidence_dir/<ref_no>/. Returns paths and status.

    A bundle downloaded within `cache_hours` that still has a real sheet is reused. When it holds fewer
    photos than wanted (and the page offers more) only the missing ones are fetched, and when the page
    names a different sheet than the saved one the new sheet is fetched beside the old one. Nothing
    already on disk is ever overwritten or removed.
    """
    out_dir = cfg.evidence_dir / _safe(facts.ref_no)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = cache or NoCache()
    meta = out_dir / "evidence.json"
    saved = None
    if cache.enabled:
        found = load_saved_evidence(cfg, facts.ref_no)
        if found and found.get("sheet") and meta.exists() and (time.time() - meta.stat().st_mtime) < cache.max_age:
            saved = found
    if saved is not None:
        _adopt_photos(saved, out_dir)
        known = set(saved.get("sheet_urls") or ([saved["sheet_url"]] if saved.get("sheet_url") else []))
        sheet_changed = bool(facts.sheet_url and known and facts.sheet_url not in known)  # a sheet seen before is not new
        if not sheet_changed and (max_photos == 0 or not facts.photo_urls
                                  or len(saved["photos"]) >= min(max_photos, len(facts.photo_urls))):
            before = dict(saved)
            saved["reused"] = True
            saved["extended"] = 0
            if facts.photo_urls:
                saved["photo_total"] = len(facts.photo_urls)
            if facts.sheet_url and not saved.get("sheet_url"):
                saved["sheet_url"] = facts.sheet_url
                saved["sheet_urls"] = [facts.sheet_url]
            if {k: v for k, v in saved.items() if k != "reused"} != {k: v for k, v in before.items() if k != "reused"}:
                meta.write_text(json.dumps(saved, indent=1), encoding="utf-8")
            return saved
    result: dict = saved if saved is not None else {
        "dir": str(out_dir), "sheet": None, "sheet_url": "", "photos": [], "failed": [], "placeholders": 0,
        "images_pending": False}

    def grab(url: str, stem: str) -> Path | None:
        result["failed"] = [f for f in result["failed"] if f.get("url") != url]  # this pass replaces older entries
        try:
            data, ctype = browser.get_bytes(url)
        except Exception as exc:
            result["failed"].append({"url": url, "error": str(exc)[:120]})
            return None
        kind = sniff_image(data)
        if kind is None:
            result["failed"].append({"url": url, "error": f"not an image ({len(data)} bytes, {ctype})"})
            return None
        if is_placeholder(data):
            result["failed"].append({"url": url, "error": f"placeholder image ({len(data)} bytes); not published yet"})
            return None
        path = out_dir / f"{stem}{kind[1]}"
        path.write_bytes(data)
        return path

    if saved is not None:
        if sheet_changed:
            n = 2
            while any(out_dir.glob(f"sheet_{n}.*")):
                n += 1
            p = grab(facts.sheet_url, f"sheet_{n}")
            if p:
                result["sheet"], result["sheet_url"] = str(p), facts.sheet_url
                result["sheet_urls"] = list(known) + [facts.sheet_url]
        _extend_bundle(result, out_dir, facts, max_photos, grab)
        result["reused"] = True
    else:
        if facts.sheet_url:
            p = grab(facts.sheet_url, "sheet")
            result["sheet"] = str(p) if p else None
            result["sheet_url"] = facts.sheet_url
            result["sheet_urls"] = [facts.sheet_url]
        for i, url in enumerate(facts.photo_urls[:max_photos]):
            p = grab(url, f"photo_{i:02d}")
            if p:
                result["photos"].append(str(p))
        result["extended"] = 0
    result["placeholders"] = sum(1 for f in result["failed"] if str(f.get("error", "")).startswith("placeholder image"))
    if saved is None:
        result["images_pending"] = result["sheet"] is None and result["placeholders"] > 0
    if facts.photo_urls:
        result["photo_total"] = len(facts.photo_urls)
    (out_dir / "facts.json").write_text(json.dumps(facts.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
    meta.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def _adopt_photos(result: dict, out_dir: Path) -> None:
    """List every photo_NN file on disk in the bundle, so a stale list never causes a second fetch."""
    known = {Path(p).name for p in result.get("photos", [])}
    for f in sorted(out_dir.glob("photo_*.*")):
        if f.name not in known:
            result.setdefault("photos", []).append(str(f))
    result["photos"] = sorted(result.get("photos", []), key=lambda x: Path(x).name)


def _extend_bundle(result: dict, out_dir: Path, facts: VehicleFacts, max_photos: int, grab) -> None:
    """Fetch the photos (up to `max_photos`) that have no file yet; `extended` counts only those newly fetched."""
    fetched = 0
    for i in range(min(max_photos, len(facts.photo_urls))):
        if any(out_dir.glob(f"photo_{i:02d}.*")):
            continue
        p = grab(facts.photo_urls[i], f"photo_{i:02d}")
        if p:
            result["photos"].append(str(p))
            fetched += 1
    result["photos"] = sorted(set(result["photos"]), key=lambda x: Path(x).name)
    result["extended"] = fetched


def photos_complete(evidence: dict, wanted: int) -> bool:
    """True when the bundle holds every photo the page offers, up to `wanted`. A bundle still waiting for
    Nichibo's images (or replay's placeholder for a missing bundle) never is."""
    if evidence.get("images_pending"):
        return False
    total = evidence.get("photo_total")
    if not total:
        return not evidence.get("failed")
    return len(evidence.get("photos", [])) >= min(wanted, total)


def _safe(ref_no: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in ref_no)[:120]
