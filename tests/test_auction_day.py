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
    facts.pop("auction_day_read", None)
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


def test_a_waiting_lot_that_has_its_day_is_not_read_again_even_with_a_cold_cache(cfg, monkeypatch):
    _card_only(cfg, monkeypatch)
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    again = FakeBrowser()
    stats = scan(cfg, again, store, ["GDH206"], limit_per_model=0, fresh=True)  # no new lot, cold cache
    assert stats.reused >= 2 and stats.day_pages == 0 and _lot_visits(again) == []
    store.close()


def test_a_stored_day_is_carried_forward_to_a_re_evaluated_lot(cfg, monkeypatch):
    from tests.test_pipeline_offline import PLACEHOLDER_GIF
    _card_only(cfg, monkeypatch)
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(PLACEHOLDER_GIF), store, ["GDH206"], limit_per_model=2)  # parked, day read
    parked = {r["ref_no"] for r in store.list_proposals(decision="pending", status="NEEDS_REVIEW")}
    assert len(parked) == 2
    again = FakeBrowser()
    stats = scan(cfg, again, store, ["GDH206"], limit_per_model=2, fresh=True)  # images arrive, cold cache
    assert stats.pending == 0 and stats.day_pages == 0 and _lot_visits(again) == []
    new = [r for r in store.list_proposals(decision="pending", status="NEEDS_REVIEW") if r["ref_no"] in parked]
    assert len(new) == 2
    for r in new:
        f = json.loads(r["facts_json"])
        assert f["auction_date"] == "20260915" and f["auction_place"] == "CAA Gifu"
    store.close()


def test_a_page_with_no_date_is_read_once_and_flagged(cfg, monkeypatch, caplog):
    _card_only(cfg, monkeypatch)

    class NoDate(FakeBrowser):
        def __init__(self):
            super().__init__()
            self.detail_html = self.detail_html.replace("AuctionDate: '20260915'", "AuctionDate: ''")

    store = Store(cfg.db_path)
    with caplog.at_level("WARNING", logger="auction_bot.scan"):
        first = NoDate()
        scan(cfg, first, store, ["GDH206"], limit_per_model=2)
    assert len(_lot_visits(first)) == 2 and "no auction date" in caplog.text
    for r in store.list_proposals(decision="pending", status="NEEDS_REVIEW"):
        f = json.loads(r["facts_json"])
        assert f["auction_date"] == "" and f["auction_day_read"] is True
    second = NoDate()
    stats = scan(cfg, second, store, ["GDH206"], limit_per_model=0, fresh=True)
    assert stats.day_pages == 0 and _lot_visits(second) == []
    store.close()


def test_a_stop_during_the_day_read_keeps_the_judged_lot(cfg, monkeypatch):
    from auction_bot.progress import Cancelled
    _card_only(cfg, monkeypatch)
    import auction_bot.pipeline as pl
    monkeypatch.setattr(pl, "auction_day", lambda *a, **k: (_ for _ in ()).throw(Cancelled("stopped")))
    store = Store(cfg.db_path)
    try:
        scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    except Cancelled:
        pass
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 1 and json.loads(rows[0]["facts_json"]).get("auction_date", "") == ""
    store.close()


# -- Final review fix T6: a known day is copied, and the read mark survives every rewrite of the facts ------

class _NoDate(FakeBrowser):
    """A lot page with no auction date."""
    def __init__(self):
        super().__init__()
        self.detail_html = self.detail_html.replace("AuctionDate: '20260915'", "AuctionDate: ''")


def test_a_waiting_lot_copies_a_day_another_row_already_holds(cfg, monkeypatch):
    _card_only(cfg, monkeypatch)
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    row = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]
    facts = json.loads(row["facts_json"])
    # The same vehicle under another guide already holds the day...
    store.add_proposal(ref_no=row["ref_no"], guide_id=987654, model_code="OTHER", snapshot="s", rule_updated_at="u",
                       status="SKIP", stage="card", summary="", facts_json=dict(facts), hard_json={"checks": []})
    # ...and the waiting row was stored before the scan read days.
    facts["auction_date"] = facts["auction_place"] = ""
    facts.pop("auction_day_read", None)
    store.update_soft(row["id"], json.loads(row["soft_json"]), facts=facts)
    again = FakeBrowser()
    stats = scan(cfg, again, store, ["GDH206"], limit_per_model=0, fresh=True)  # no new lot, cold cache
    assert stats.reused >= 1 and stats.day_pages == 0
    assert not any(u.endswith(f"/auction/{row['ref_no']}") for u in _lot_visits(again))
    got = json.loads(store.proposal(row["id"])["facts_json"])
    assert got["auction_date"] == "20260915" and got["auction_place"] == "CAA Gifu" and got["auction_day_read"] is True
    store.close()


def test_the_read_mark_round_trips_through_vehicle_facts():
    from auction_bot.nichibo.facts import VehicleFacts
    f = VehicleFacts.from_dict({"ref_no": "X", "url": "u", "auction_date": "", "auction_day_read": True})
    assert f.auction_day_read is True and f.to_dict()["auction_day_read"] is True
    assert VehicleFacts(ref_no="Y", url="u").to_dict()["auction_day_read"] is False


def test_the_read_mark_survives_replay_apply(cfg, monkeypatch):
    from auction_bot.pipeline import reevaluate
    from tests.test_instructions import _add_line
    _card_only(cfg, monkeypatch)
    store = Store(cfg.db_path)
    scan(cfg, _NoDate(), store, ["GDH206"], limit_per_model=2)
    refs = {r["ref_no"] for r in store.list_proposals(decision="pending", status="NEEDS_REVIEW")}
    assert len(refs) == 2
    _add_line(cfg, "skip: km > 0")  # every stored lot changes outcome, so replay rewrites both from their facts
    st = reevaluate(cfg, store, ["GDH206"], apply=True)
    assert st.applied == 2
    new = [r for r in store.list_proposals(decision="pending", status="SKIP", limit=1000) if r["ref_no"] in refs]
    assert len(new) == 2
    for r in new:
        f = json.loads(r["facts_json"])
        assert f["auction_date"] == "" and f["auction_day_read"] is True
    store.close()


def test_the_read_mark_survives_the_pending_judge_pass(cfg, monkeypatch):
    from pathlib import Path
    from auction_bot.pipeline import judge_pending
    from tests.test_judge_pipeline import _stub
    cfg.sheet_check = "judge"
    cfg.judge = {"max_photos": 2, "inline": False}
    _stub(monkeypatch, [])
    store = Store(cfg.db_path)
    scan(cfg, _NoDate(), store, ["GDH206"], limit_per_model=1)
    row = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]
    assert json.loads(row["facts_json"])["auction_day_read"] is True
    for photo in json.loads(row["evidence_json"])["photos"]:  # the pending pass must open the page for photos
        Path(photo).unlink()
    st = judge_pending(cfg, store, browser=_NoDate())
    assert st.pages == 1 and st.judged == 1
    f = json.loads(store.proposal(row["id"])["facts_json"])
    assert f["auction_date"] == "" and f["auction_day_read"] is True
    store.close()
