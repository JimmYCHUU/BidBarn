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
    c.playbook_dir = tmp_path / "playbook"
    c.snapshot_dir = tmp_path / "snapshots"
    c.pace_seconds = (0, 0)
    c.review = {"allow_hosts": ["testserver"]}  # the test client's name for this PC; config.yaml allows none
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
    assert ev["sheet"] and Path(ev["sheet"]).exists() and ev["photos"] == []  # photos are a Nichibo link now
    gdir = Path(ev["dir"]) / f"guide_{p['guide_id']}"
    assert (gdir / "rule.json").exists() and (gdir / "hard.json").exists()

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
    assert "sheet_2.jpg" in r.text and "1,240" in r.text and "Open on Nichibo" in r.text  # one link to Nichibo, in the head
    assert 'href="/guides/GDH206"' in r.text  # the lot links to its guide
    store.close()


def test_two_guides_keep_their_own_evaluation_files(cfg, tmp_path):
    from dataclasses import replace
    from auction_bot.carbarn.client import load_rules
    from auction_bot.pipeline import evaluate_vehicle
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], no_model=None, limit_per_model=1)
    row = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]
    store.close()
    facts = VehicleFacts.from_dict(json.loads(row["facts_json"]))
    rule = load_rules(cfg)[0][0]
    ev_dir = tmp_path / "ev" / "lot"
    loader = lambda: {"dir": str(ev_dir), "sheet": None, "photos": []}
    for g in (223, 224):
        out = evaluate_vehicle(cfg, replace(rule, guide_id=g), facts, no_model=True, evidence_loader=loader)
        assert out["stage"] == "manual"
    for g in (223, 224):
        gdir = ev_dir / f"guide_{g}"
        assert json.loads((gdir / "rule.json").read_text(encoding="utf-8"))["guide_id"] == g
        assert (gdir / "hard.json").exists()
    assert (ev_dir / "facts.json").exists() and not (ev_dir / "rule.json").exists()


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
    assert r.status_code == 200 and "Skipped from the auction sheet" in r.text and "sheet_2.jpg" in r.text
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


# -- runs started from the dashboard: progress lines, Stop, and a session that has expired ------------------

class SignedOut(FakeBrowser):
    """Nichibo answers every page with its login page."""

    def goto(self, url, wait_until="domcontentloaded"):
        self.visited.append(url)
        self.page.url = "https://autosearch.nichibojapan.com/login"
        return "<html><body>Please sign in</body></html>"


def _progress_lines(text: str) -> list[dict]:
    from auction_bot import progress
    parsed = [progress.parse(ln) for ln in text.splitlines()]
    return [p[1] for p in parsed if p and p[0] == "progress"]


def test_scan_prints_progress_for_the_dashboard(cfg, monkeypatch, capsys):
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    events = _progress_lines(capsys.readouterr().out)
    assert events[0] == {"stage": "scan", "guides": 1, "guide": 0, "codes": ["GDH206"], "marks": ""}
    assert events[1]["guide"] == 1 and events[1]["code"] == "GDH206" and events[1]["cards"] == 0
    last = events[-1]
    assert last["stage"] == "scan" and last["guide"] == last["guides"] == stats.guides == 1
    # One letter per finished guide: f lots for review, e nothing new, x an error. While a guide is going it has none.
    assert last == {"stage": "scan", "guides": 1, "guide": 1, "code": "GDH206", "codes": ["GDH206"], "marks": "f",
                    "cards": stats.cards, "opened": 2, "review": 2, "skip": stats.skip, "bid": 0, "reused": 0,
                    "pending": 0, "model_calls": 0, "errors": 0}
    assert all(e["marks"] == "" for e in events[:-1])
    assert [e["opened"] for e in events if e.get("opened")] == sorted(e["opened"] for e in events if e.get("opened"))
    assert {1, 2} <= {e.get("opened") for e in events}  # one line after each lot
    # Replay reports the same way.
    from auction_bot.pipeline import reevaluate
    st = reevaluate(cfg, store, ["GDH206"])
    events = _progress_lines(capsys.readouterr().out)
    assert events and all(e["stage"] == "replay" and e["total"] == st.checked for e in events)
    assert events[0]["checked"] == 0 and events[-1]["checked"] == st.checked and events[-1]["changed"] == 0
    # From a console (no variable) the same calls print no such line.
    monkeypatch.delenv("BIDBARN_PROGRESS")
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    reevaluate(cfg, store, ["GDH206"])
    out = capsys.readouterr().out
    assert "PROGRESS" not in out and "SUMMARY" not in out
    store.close()


def test_scan_stops_when_the_dashboard_asks(cfg, monkeypatch, tmp_path):
    from auction_bot.progress import Cancelled
    flag = tmp_path / "1.cancel"
    flag.write_text("stop", encoding="utf-8")
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(flag))
    store = Store(cfg.db_path)
    browser = FakeBrowser()
    with pytest.raises(Cancelled, match="stopped from the dashboard"):
        scan(cfg, browser, store, ["GDH206"], limit_per_model=2)
    assert browser.visited == []  # asked before the scan began: not one page was loaded
    assert store.list_proposals(decision=None, limit=1000) == []
    store.close()


def test_scan_stopped_halfway_keeps_what_it_stored(cfg, monkeypatch, tmp_path):
    from auction_bot.progress import Cancelled
    flag = tmp_path / "2.cancel"
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(flag))

    class StopDuringTheFirstLot(FakeBrowser):
        def get_bytes(self, url, headers=None):
            flag.write_text("stop", encoding="utf-8")  # Stop is clicked while the first sheet downloads
            return super().get_bytes(url, headers)

    store = Store(cfg.db_path)
    with pytest.raises(Cancelled):
        scan(cfg, StopDuringTheFirstLot(), store, ["GDH206"], limit_per_model=5)
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 1 and json.loads(rows[0]["soft_json"])["mode"] == "manual"  # the lot in hand was finished
    assert store.list_proposals(decision="pending", status="SKIP", limit=1000)  # and the cards before it
    flag.unlink()
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=5)  # the next scan carries on from there
    assert stats.errors == [] and stats.opened == 5 and stats.reused >= 1
    store.close()


def test_signed_out_scan_stops_at_the_first_guide(cfg):
    from auction_bot.nichibo.browser import NotLoggedIn
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["guides"] = data["guides"] * 3  # three guides to search
    snap.write_text(json.dumps(data), encoding="utf-8")
    store = Store(cfg.db_path)
    browser = SignedOut()
    with pytest.raises(NotLoggedIn, match="login page"):
        scan(cfg, browser, store, ["GDH206"])
    assert len(browser.visited) == 1  # not one search per guide with an error each
    assert store.list_proposals(decision=None, limit=1000) == []
    store.close()


def test_other_search_failures_are_still_counted_per_guide(cfg):
    class Flaky(FakeBrowser):
        def goto(self, url, wait_until="domcontentloaded"):
            raise TimeoutError("Nichibo timed out")

    store = Store(cfg.db_path)
    stats = scan(cfg, Flaky(), store, ["GDH206"])
    assert stats.guides == 1 and len(stats.errors) == 1 and "search failed: Nichibo timed out" in stats.errors[0]
    store.close()


def test_judge_pending_stops_when_signed_out_or_asked(cfg, monkeypatch, tmp_path, capsys):
    import auction_bot.judge.client as client_mod
    from auction_bot.nichibo.browser import NotLoggedIn
    from auction_bot.pipeline import judge_pending
    from auction_bot.progress import Cancelled

    cfg.sheet_check = "judge"
    cfg.judge = {"max_photos": 2, "inline": False}
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    asked = []
    monkeypatch.setattr(client_mod, "openai_transport", lambda p, messages, fmt, max_tokens: asked.append(p.name) or ("{}", None))
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 2 and all(json.loads(r["soft_json"])["judge_pending"] for r in rows)
    for r in rows:  # the photos are gone, so every lot needs its page again
        for photo in json.loads(r["evidence_json"])["photos"]:
            Path(photo).unlink()
    cfg.cache_hours = 0  # and the saved pages are too old to use
    browser = SignedOut()
    with pytest.raises(NotLoggedIn):
        judge_pending(cfg, store, browser=browser)
    assert len(browser.visited) == 1 and asked == []  # stopped at the first lot, not an error per lot

    flag = tmp_path / "3.cancel"
    flag.write_text("stop", encoding="utf-8")
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(flag))
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    capsys.readouterr()
    browser = FakeBrowser()
    with pytest.raises(Cancelled):
        judge_pending(cfg, store, browser=browser)
    assert browser.visited == [] and asked == []
    assert _progress_lines(capsys.readouterr().out) == []  # stopped before the first lot
    flag.unlink()
    st = judge_pending(cfg, store)  # no browser: both lots are looked at and left pending, as before
    events = _progress_lines(capsys.readouterr().out)
    assert st.checked == 2 and [e["i"] for e in events] == [1, 2, 2] and all(e["total"] == 2 and e["stage"] == "judge" for e in events)
    assert events[-1]["done"] is True and events[-1]["checked"] == 2 and events[-1]["judged"] == 0
    assert all(json.loads(r["soft_json"])["judge_pending"] for r in store.list_proposals(decision="pending", status="NEEDS_REVIEW"))
    store.close()


def test_manual_mode_carries_the_eligibility_result(cfg):
    cfg.eligibility = {"enabled": True, "judge_text": True, "stale_days": 90}
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], no_model=None, limit_per_model=1)
    soft = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])
    e = soft["eligibility"]
    assert e["pair"]["label"] == "SEV-000813 / MRE-000233" and e["verdict"] in ("eligible", "confirm", "cannot_import") and e["hash"]
    assert any(line.startswith("Import approval text (SEV-000813") and "4x4 Campervan only" in line for line in soft["checklist"])
    store.close()


def test_eligibility_off_by_default(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], no_model=None, limit_per_model=1)
    soft = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])
    assert soft.get("eligibility") is None
    store.close()


def test_a_missing_eligibility_config_raises_nothing(cfg):
    cfg.eligibility = None
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], no_model=None, limit_per_model=1)
    soft = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])
    assert soft.get("eligibility") is None
    store.close()


# ---- download_evidence: extension, adoption, changed sheet, completeness ----

class _ScriptedBrowser(FakeBrowser):
    """Counts fetches per URL; `bad` maps a URL to bytes served instead of the JPEG, `boom` raises."""
    def __init__(self):
        super().__init__()
        self.fetched: list[str] = []
        self.bad: dict[str, bytes] = {}
        self.boom: set[str] = set()

    def get_bytes(self, url, headers=None):
        self.fetched.append(url)
        if url in self.boom:
            raise RuntimeError("down")
        if url in self.bad:
            return self.bad[url], "image/gif"
        return FAKE_JPEG, "image/jpeg"


def _facts(n, sheet="https://x/sheet-a"):
    return VehicleFacts(url="https://x/lot", ref_no="R1", sheet_url=sheet, photo_urls=[f"https://x/p{i}" for i in range(n)])


def _cache(tmp_path):
    from auction_bot.nichibo.cache import PageCache
    return PageCache(tmp_path / "pc", 12.0)


def test_extension_adopts_files_on_disk_and_fetches_only_missing(cfg, tmp_path):
    from auction_bot.nichibo.vehicle import download_evidence
    b, cache = _ScriptedBrowser(), _cache(tmp_path)
    ev = download_evidence(b, cfg, _facts(6), 3, cache)
    assert len(ev["photos"]) == 3 and ev["extended"] == 0 and ev["photo_total"] == 6
    meta = Path(ev["dir"]) / "evidence.json"
    stale = json.loads(meta.read_text(encoding="utf-8"))
    stale["photos"] = []  # a card-stage download that never listed them
    meta.write_text(json.dumps(stale), encoding="utf-8")
    b.fetched.clear()
    ev = download_evidence(b, cfg, _facts(6), 40, cache)
    assert len(ev["photos"]) == 6 and ev["extended"] == 3 and ev["reused"]
    assert b.fetched == ["https://x/p3", "https://x/p4", "https://x/p5"]
    assert all(Path(p).exists() for p in ev["photos"]) and ev["photos"] == sorted(ev["photos"], key=lambda x: Path(x).name)
    assert json.loads(meta.read_text(encoding="utf-8"))["photo_total"] == 6
    b.fetched.clear()
    again = download_evidence(b, cfg, _facts(6), 40, cache)
    assert b.fetched == [] and again["extended"] == 0 and len(again["photos"]) == 6


def test_changed_sheet_is_fetched_beside_the_old_one(cfg, tmp_path):
    from auction_bot.nichibo.vehicle import download_evidence
    b, cache = _ScriptedBrowser(), _cache(tmp_path)
    first = download_evidence(b, cfg, _facts(0, "https://x/sheet-a"), 0, cache)
    old = Path(first["sheet"])
    ev = download_evidence(b, cfg, _facts(4, "https://x/sheet-b"), 3, cache)
    assert Path(ev["sheet"]).name.startswith("sheet_2") and ev["sheet_url"] == "https://x/sheet-b"
    assert old.exists() and Path(ev["sheet"]).exists() and len(ev["photos"]) == 3
    n = len(b.fetched)
    download_evidence(b, cfg, _facts(0, "https://x/sheet-a"), 0, cache)  # a sheet seen before is not fetched again
    assert len(b.fetched) == n
    ev = download_evidence(b, cfg, _facts(4, "https://x/sheet-c"), 3, cache)
    assert Path(ev["sheet"]).name.startswith("sheet_3") and old.exists()


def test_photo_total_is_kept_by_a_card_stage_download(cfg, tmp_path):
    from auction_bot.nichibo.vehicle import download_evidence
    b, cache = _ScriptedBrowser(), _cache(tmp_path)
    download_evidence(b, cfg, _facts(5), 2, cache)
    ev = download_evidence(b, cfg, _facts(0), 0, cache)
    assert ev["photo_total"] == 5


def test_placeholder_and_failed_photos_are_recorded_once_and_retried(cfg, tmp_path):
    from auction_bot.nichibo.vehicle import download_evidence
    b, cache = _ScriptedBrowser(), _cache(tmp_path)
    download_evidence(b, cfg, _facts(2), 2, cache)
    b.bad["https://x/p2"] = PLACEHOLDER_GIF
    b.boom.add("https://x/p3")
    ev = download_evidence(b, cfg, _facts(4), 4, cache)
    assert len(ev["photos"]) == 2 and ev["placeholders"] == 1 and len(ev["failed"]) == 2
    assert not list(Path(ev["dir"]).glob("photo_02.*"))
    ev = download_evidence(b, cfg, _facts(4), 4, cache)  # retried, entries replaced not appended
    assert len(ev["failed"]) == 2 and ev["extended"] == 0
    b.bad.clear(); b.boom.clear()
    ev = download_evidence(b, cfg, _facts(4), 4, cache)
    assert len(ev["photos"]) == 4 and ev["failed"] == [] and ev["placeholders"] == 0 and ev["extended"] == 2


def test_adoption_skips_a_file_that_is_not_an_image(cfg, tmp_path):
    from auction_bot.nichibo.vehicle import download_evidence, photos_complete
    b, cache = _ScriptedBrowser(), _cache(tmp_path)
    ev = download_evidence(b, cfg, _facts(3), 3, cache)
    junk = Path(ev["dir"]) / "photo_03.jpg"
    junk.write_bytes(b"<html>Service Unavailable</html>")   # a truncated download that never went through `grab`
    ev = download_evidence(b, cfg, _facts(4), 4, cache)
    assert len(ev["photos"]) == 3 and all(Path(p).name != "photo_03.jpg" for p in ev["photos"])
    assert junk.exists() and not photos_complete(ev, 4)       # left on disk, and the set reads short (review), not complete


def test_extension_paces_image_fetches(cfg, tmp_path, monkeypatch):
    import auction_bot.nichibo.vehicle as veh
    slept = []
    monkeypatch.setattr(veh.time, "sleep", slept.append)
    cfg.pace_seconds = (2.0, 2.0)
    b, cache = _ScriptedBrowser(), _cache(tmp_path)
    veh.download_evidence(b, cfg, _facts(4), 1, cache)
    assert slept == []
    ev = veh.download_evidence(b, cfg, _facts(4), 4, cache)
    assert ev["extended"] == 3 and slept == [0.5, 0.5]       # a quarter of a pace draw between the three fetches


def test_a_failing_eligibility_check_never_loses_the_lot(cfg, monkeypatch):
    import auction_bot.pipeline as pl
    cfg.eligibility = {"enabled": True, "judge_text": True, "stale_days": 90}
    monkeypatch.setattr(pl, "check_lot", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad approval data")))
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], no_model=None, limit_per_model=1)
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert stats.errors == [] and len(rows) == 1 and json.loads(rows[0]["soft_json"])["eligibility"] is None
    store.close()


def test_replay_carries_on_past_a_row_that_raises(cfg, monkeypatch):
    import auction_bot.pipeline as pl
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["guides"][0]["tree"]["minGrade"] = 4.5     # both stored lots now change
    snap.write_text(json.dumps(data), encoding="utf-8")
    real, calls = pl.evaluate_vehicle, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("corrupt facts")
        return real(*a, **k)
    monkeypatch.setattr(pl, "evaluate_vehicle", flaky)
    st = pl.reevaluate(cfg, store, ["GDH206"], apply=True)
    assert len(calls) == 2 and len(st.errors) == 1 and "corrupt facts" in st.errors[0]
    assert st.changed == 1 and st.applied == 1 and sum("corrupt facts" in c for c in st.changes) == 1
    assert len(store.list_proposals(decision="pending", status="NEEDS_REVIEW")) == 1   # the failed row is left as it was
    store.close()


def test_photos_complete_cases():
    from auction_bot.nichibo.vehicle import photos_complete
    assert photos_complete({"photos": []}, 8)
    assert not photos_complete({"photos": [], "failed": [{"url": "u"}]}, 8)
    assert photos_complete({"photo_total": 0, "photos": []}, 8)
    assert not photos_complete({"photo_total": 0, "photos": [], "failed": [{"url": "u"}]}, 8)
    assert not photos_complete({"photo_total": 10, "photos": list("abcdefgh")}, 40)
    assert photos_complete({"photo_total": 10, "photos": list("abcdefgh")}, 8)


def test_openai_route_still_downloads_max_photos_to_model_at_card_stage(cfg, monkeypatch):
    import auction_bot.pipeline as pl
    cfg.sheet_check = "openai"
    cfg.max_photos_to_model = 4
    seen = []
    real = pl.download_evidence

    def spy(browser, c, facts, n, cache=None):
        seen.append(n)
        return real(browser, c, facts, n, cache)
    monkeypatch.setattr(pl, "download_evidence", spy)
    store = Store(cfg.db_path)
    try:
        scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    except Exception:
        pass
    store.close()
    assert seen and seen[0] == 4
