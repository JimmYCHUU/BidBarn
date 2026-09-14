"""End-to-end scan in route-1 mode (no model) against the captured Nichibo pages.

A fake browser serves the search-results fixture for every search URL, the
detail fixture for every detail URL, a fake translator JSON and fake JPEG bytes.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auction_bot.config import Config
from auction_bot.pipeline import scan
from auction_bot.review.app import create_app
from auction_bot.rules.hints import keyword_hints
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.store import Store

FIX = Path(__file__).parent / "fixtures"
FAKE_JPEG = b"\xff\xd8\xff" + bytes(20_000)
PLACEHOLDER_GIF = b"GIF89a" + (400).to_bytes(2, "little") + (300).to_bytes(2, "little") + bytes(3_700)


class _Resp:
    def __init__(self, status=200, text="", body=b"", ctype="text/html"):
        self.status = status
        self.ok = 200 <= status < 300
        self._text = text
        self._body = body
        self.headers = {"content-type": ctype}

    def text(self):
        return self._text

    def json(self):
        return json.loads(self._text)

    def body(self):
        return self._body


class _Page:
    url = "https://autosearch.nichibojapan.com/auction"


class FakeBrowser:
    def __init__(self, image_bytes=FAKE_JPEG):
        self.image_bytes = image_bytes
        self.page = _Page()
        self.visited: list[str] = []
        self.search_html = (FIX / "search_gdh206.html").read_text(encoding="utf-8")
        self.detail_html = (FIX / "detail_gdh206v.html").read_text(encoding="utf-8")

    def goto(self, url, wait_until="domcontentloaded"):
        self.visited.append(url)
        self.page.url = url
        if "/auction?" in url:
            return self.search_html
        return self.detail_html

    def get(self, url, headers=None, params=None):
        if "jsonAuctionSheetData" in url:
            return _Resp(200, json.dumps({"Type": "OK", "AuctionSheetURL": "x",
                                          "WordsEN": {"Variant": "Van 5d 4WD DX", "Comments": "10 seater welcab"}}))
        return _Resp(200, "", self.image_bytes, "image/jpeg")

    def get_bytes(self, url, headers=None):
        r = self.get(url, headers)
        return r.body(), r.headers["content-type"]

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = Config()
    c.db_path = tmp_path / "bot.sqlite"
    c.evidence_dir = tmp_path / "evidence"
    c.override_dir = tmp_path / "overrides"
    c.snapshot_dir = tmp_path / "snapshots"
    c.pace_seconds = (0, 0)
    for d in (c.evidence_dir, c.override_dir, c.snapshot_dir):
        d.mkdir()
    tree = json.loads((FIX / "guide_223_tree.json").read_text(encoding="utf-8"))
    item = next(i for i in json.loads((FIX / "guide_list_subset.json").read_text(encoding="utf-8"))["content"] if i["id"] == 223)
    (c.snapshot_dir / "rules_test.json").write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(), "guides": [{"item": item, "tree": tree}]}), encoding="utf-8")
    (c.override_dir / "GDH206.md").write_text("# notes\n\n- (2026-09-14, jim) Skip ambulance conversions\n", encoding="utf-8")
    return c


def test_scan_route1_end_to_end(cfg):
    browser = FakeBrowser()
    store = Store(cfg.db_path)
    stats = scan(cfg, browser, store, ["GDH206"], no_model=None, limit_per_model=3)
    assert stats.guides == 1 and stats.cards == 30 and stats.errors == []
    assert stats.model_calls == 0
    # Card prefilter removes grade 0 / out-of-range cards without opening them.
    assert stats.opened == 3 and stats.review == 3
    assert stats.skip > 0
    # Search URL carries the guide's ranges.
    search_urls = [u for u in browser.visited if "/auction?" in u]
    assert len(search_urls) == 1 and "Chassis=GDH206" in search_urls[0]
    assert "YearFrom=2018" in search_urls[0] and "YearTo=2024" in search_urls[0] and "KMMax=160000" in search_urls[0]

    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 3
    p = rows[0]
    soft = json.loads(p["soft_json"])
    assert soft["mode"] == "manual"
    assert "No sticker paint" in soft["checklist"] and "Skip ambulance conversions" in soft["checklist"]
    assert any("welcab" in h["message"].lower() for h in soft["hints"])
    assert any("7 or more" in h["message"] for h in soft["hints"])
    assert p["bid_jpy_k"] == 1_240  # 2020 / 120K-130K = 1,160 + 80 (grade 4)
    ev = json.loads(p["evidence_json"])
    assert ev["sheet"] and Path(ev["sheet"]).exists() and len(ev["photos"]) >= 8
    assert (Path(ev["dir"]) / "rule.json").exists() and (Path(ev["dir"]) / "hard.json").exists()

    # Second scan re-uses pending proposals instead of re-opening pages, and the search
    # page comes from the disk cache, so only the next three detail pages are fetched.
    visited_before = len(browser.visited)
    stats2 = scan(cfg, browser, store, ["GDH206"], no_model=None, limit_per_model=3)
    assert stats2.opened == 3 and stats2.review == 3  # the next three candidates
    assert len(browser.visited) == visited_before + 3 and stats2.cache_hits >= 1
    stats3 = scan(cfg, browser, store, ["GDH206"], no_model=None, limit_per_model=100)
    assert stats3.reused >= 6

    # Review page renders the checklist and the sheet.
    client = TestClient(create_app(cfg))
    r = client.get(f"/p/{p['id']}")
    assert r.status_code == 200
    assert "Your checklist" in r.text and "No sticker paint" in r.text and "Skip ambulance conversions" in r.text
    assert "sheet.jpg" in r.text and "1,240" in r.text
    store.close()


def test_scan_refuses_stale_snapshot(cfg):
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["fetched_at"] = "2020-01-01T00:00:00+00:00"
    snap.write_text(json.dumps(data), encoding="utf-8")
    store = Store(cfg.db_path)
    with pytest.raises(RuntimeError, match="old"):
        scan(cfg, FakeBrowser(), store, ["GDH206"])
    store.close()


def test_keyword_hints():
    f = VehicleFacts(ref_no="r", url="u", variant_text="Van Welcab 4WD", equipment="ps pw",
                     translator={"Comments": "10 seater, sticker wrap"})
    hints = keyword_hints(f, ["4WD, long or super long wheelbase"])
    msgs = {h["message"] for h in hints}
    assert any("Welcab" in m for m in msgs)
    assert any("7 or more" in m for m in msgs)
    assert any("sticker" in m for m in msgs)
    assert "4WD mentioned" in msgs
    assert not any("nothing mentions it" in m for m in msgs)
    f2 = VehicleFacts(ref_no="r", url="u", variant_text="Van DX")
    assert any("nothing mentions it" in h["message"] for h in keyword_hints(f2, ["4WD only"]))
    assert not any("nothing mentions it" in h["message"] for h in keyword_hints(f2, ["White only"]))


def test_placeholder_images_are_pending_then_rechecked(cfg):
    from auction_bot.nichibo.vehicle import is_placeholder
    assert is_placeholder(PLACEHOLDER_GIF) and not is_placeholder(FAKE_JPEG)
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(PLACEHOLDER_GIF), store, ["GDH206"], limit_per_model=2)
    assert stats.review == 2
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 2 and all(json.loads(r["soft_json"])["images_pending"] for r in rows)
    assert all(json.loads(r["evidence_json"])["sheet"] is None for r in rows)
    first_ids = {r["id"] for r in rows}
    # Images arrive: the next scan re-opens those vehicles and supersedes the old proposals.
    stats2 = scan(cfg, FakeBrowser(FAKE_JPEG), store, ["GDH206"], limit_per_model=2)
    assert stats2.opened == 2
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 2 and not any(r["id"] in first_ids for r in rows)
    assert all(not json.loads(r["soft_json"])["images_pending"] for r in rows)
    assert store.conn.execute("SELECT COUNT(*) FROM proposals WHERE decision='superseded'").fetchone()[0] == 2
    store.close()


def test_sheet_ocr_seats_skip(cfg, monkeypatch):
    import auction_bot.pipeline as pl
    from auction_bot.nichibo.sheet_ocr import SheetRead
    monkeypatch.setattr(pl, "read_sheet", lambda path: SheetRead(available=True, seats=10, model_code="GDH206K",
                                                                   km=42_192, grade="4", is_4wd=True))
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    assert stats.review == 0 and stats.skip >= 2
    sheet_rows = [r for r in store.list_proposals(decision="pending", status="SKIP", limit=1000) if r["stage"] == "sheet"]
    assert len(sheet_rows) == 2
    soft = json.loads(sheet_rows[0]["soft_json"])
    assert soft["ocr"]["seats"] == 10 and "10 seats" in soft["reasons"][0]
    assert any("model code reads GDH206K" in h["message"] for h in soft["hints"])  # listing says GDH206V
    client = TestClient(create_app(cfg))
    r = client.get(f"/p/{sheet_rows[0]['id']}")
    assert r.status_code == 200 and "Skipped from the auction sheet" in r.text and "sheet.jpg" in r.text
    store.close()


def test_sheet_ocr_unavailable_goes_to_checklist(cfg, monkeypatch):
    import auction_bot.pipeline as pl
    from auction_bot.nichibo.sheet_ocr import SheetRead
    monkeypatch.setattr(pl, "read_sheet", lambda path: SheetRead(available=False, error="no engine"))
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    assert stats.review == 1
    soft = json.loads(store.list_proposals(decision="pending")[0]["soft_json"])
    assert soft["checklist"][0].startswith("Seats: read")
    assert soft["ocr"]["available"] is False
    store.close()


def test_replay_apply_reevaluates_offline(cfg):
    """Rule changes are re-applied to stored proposals with no browser at all."""
    from auction_bot.pipeline import reevaluate
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    before = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(before) == 2
    # Nothing changed: replay reports no changes and stores nothing.
    st = reevaluate(cfg, store, ["GDH206"], apply=True)
    assert st.checked >= 2 and st.changed == 0 and st.applied == 0
    # Tighten the guide: minimum grade 4.5 makes the grade-4 candidates skip.
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["guides"][0]["tree"]["minGrade"] = 4.5
    data["guides"][0]["tree"]["updatedAt"] = "2026-09-15T00:00:00"
    snap.write_text(json.dumps(data), encoding="utf-8")
    dry = reevaluate(cfg, store, ["GDH206"], apply=False)
    assert dry.changed == 2 and dry.applied == 0
    assert len(store.list_proposals(decision="pending", status="NEEDS_REVIEW")) == 2  # dry run stored nothing
    st = reevaluate(cfg, store, ["GDH206"], apply=True)
    assert st.changed == 2 and st.applied == 2
    assert store.list_proposals(decision="pending", status="NEEDS_REVIEW") == []
    skipped = [r for r in store.list_proposals(decision="pending", status="SKIP", limit=1000) if r["stage"] == "hard"]
    assert len(skipped) == 2 and all("below minimum 4.5" in json.dumps(json.loads(r["hard_json"])) for r in skipped)
    assert store.conn.execute("SELECT COUNT(*) FROM proposals WHERE decision='superseded'").fetchone()[0] == 2
    store.close()


def test_cache_reuses_downloaded_pages_and_evidence(cfg):
    from auction_bot.nichibo.cache import PageCache
    from auction_bot.config import cache_dir
    b1 = FakeBrowser()
    store = Store(cfg.db_path)
    scan(cfg, b1, store, ["GDH206"], limit_per_model=1)
    fetched = len(b1.visited)
    assert fetched == 2  # search + one detail page
    # A rescan with a browser that would fail if used: everything comes from the cache and saved evidence.
    class DeadBrowser(FakeBrowser):
        def goto(self, url, wait_until="domcontentloaded"):
            raise AssertionError("network used: " + url)
        def get_bytes(self, url, headers=None):
            raise AssertionError("download used: " + url)
    stats = scan(cfg, DeadBrowser(), store, ["GDH206"], limit_per_model=1, rescan=True)
    assert stats.errors == [] and stats.opened == 1 and stats.cache_hits >= 2
    # A fresh scan bypasses the cache and hits the network again.
    b2 = FakeBrowser()
    scan(cfg, b2, store, ["GDH206"], limit_per_model=1, rescan=True, fresh=True)
    assert len(b2.visited) == 2
    store.close()
