"""The scan opens a lot's own page once, only when it reaches the review, to read its auction day and hall
(Jim, 2026-10-26)."""
import json

from auction_bot.pipeline import scan
from auction_bot.store import Store
from tests.test_card_sheet import _default_variant_snapshot
from tests.test_pipeline_offline import FakeBrowser, cfg  # noqa: F401  (fixture)


def _card_only(cfg, monkeypatch):
    import auction_bot.pipeline as pl
    from auction_bot.nichibo.sheet_ocr import SheetRead
    _default_variant_snapshot(cfg)
    monkeypatch.setattr(pl, "read_sheet", lambda path: SheetRead(available=True, seats=5, model_code="GDH206K", km=None, grade="4"))


def _lot_visits(browser):
    return [u for u in browser.visited if "/auction/" in u and "/auction?" not in u]


def test_a_card_only_lot_for_review_gets_its_auction_day(cfg, monkeypatch):
    _card_only(cfg, monkeypatch)
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=3)
    assert stats.errors == []
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 3 and stats.sheet_only == 3
    for r in rows:
        facts = json.loads(r["facts_json"])
        assert facts["auction_date"] == "20260915" and facts["auction_place"] == "CAA Gifu"
        assert facts["source"] == "card+sheet"  # judged from the card; only the day came from the page
    assert stats.pages == 0 and stats.day_pages == len(rows)
    store.close()


def test_a_lot_the_rules_skip_costs_no_page(cfg):
    from tests.test_instructions import _add_line
    _add_line(cfg, "skip: variant contains DARK PRIME2")
    browser = FakeBrowser()
    store = Store(cfg.db_path)
    scan(cfg, browser, store, ["GDH206"])
    skipped = [r["ref_no"] for r in store.list_proposals(decision="pending", status="SKIP", limit=1000)]
    assert skipped
    visited = " ".join(_lot_visits(browser))
    assert not any(f"/auction/{ref}" in visited for ref in skipped)
    store.close()


def test_a_waiting_lot_gets_its_day_on_the_next_scan(cfg, monkeypatch):
    _card_only(cfg, monkeypatch)
    store = Store(cfg.db_path)
    browser = FakeBrowser()
    scan(cfg, browser, store, ["GDH206"], limit_per_model=1)
    row = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]
    facts = json.loads(row["facts_json"])
    facts["auction_date"] = facts["auction_place"] = ""  # a proposal stored before the scan read days
    store.update_soft(row["id"], json.loads(row["soft_json"]), facts=facts)
    before = store.conn.execute("SELECT COUNT(*) FROM proposals WHERE ref_no=?", (row["ref_no"],)).fetchone()[0]
    soft_before = store.proposal(row["id"])["soft_json"]
    second = FakeBrowser()
    stats = scan(cfg, second, store, ["GDH206"], limit_per_model=1)
    assert stats.reused >= 1 and stats.day_pages == 2  # the waiting lot, and the one new lot this scan took
    after = store.proposal(row["id"])
    got = json.loads(after["facts_json"])
    assert got["auction_date"] == "20260915" and got["auction_place"] == "CAA Gifu"
    assert after["decision"] == "pending" and after["soft_json"] == soft_before
    assert store.conn.execute("SELECT COUNT(*) FROM proposals WHERE ref_no=?", (row["ref_no"],)).fetchone()[0] == before
    store.close()


def test_a_page_that_fails_leaves_the_day_unknown(cfg, monkeypatch):
    _card_only(cfg, monkeypatch)

    class Broken(FakeBrowser):
        def goto(self, url, wait_until="domcontentloaded"):
            if "/auction?" not in url:
                self.visited.append(url)
                raise RuntimeError("page timed out")
            return super().goto(url, wait_until)

    store = Store(cfg.db_path)
    stats = scan(cfg, Broken(), store, ["GDH206"], limit_per_model=2)
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 2 and stats.errors == [] and stats.day_pages == 0
    assert all(json.loads(r["facts_json"])["auction_date"] == "" for r in rows)
    store.close()


def test_the_page_is_read_once(cfg, monkeypatch):
    _card_only(cfg, monkeypatch)
    store = Store(cfg.db_path)
    first = FakeBrowser()
    scan(cfg, first, store, ["GDH206"], limit_per_model=2)
    assert len(_lot_visits(first)) == 2
    # A fresh store and the same page cache: the pages are not downloaded again.
    store2 = Store(cfg.db_path.with_name("other.sqlite"))
    second = FakeBrowser()
    scan(cfg, second, store2, ["GDH206"], limit_per_model=2)
    assert _lot_visits(second) == []
    store.close(); store2.close()
