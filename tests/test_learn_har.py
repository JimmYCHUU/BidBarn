"""`bot learn --har`: reading a manual session from a HAR and comparing it with the bot's store, offline."""
import socket
import sys
from pathlib import Path

import pytest

import auction_bot.learn as learn  # imported first: must not pull in the browser layer
from auction_bot.learn import join_session, parse_har, render_report, save_session
from auction_bot.store import Store
from tests.test_pipeline_offline import FIX, cfg  # noqa: F401  (fixture)

HAR = FIX / "har_manual_session.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def test_learn_module_is_offline():
    source = Path(learn.__file__).read_text(encoding="utf-8")
    imports = [ln.strip() for ln in source.splitlines() if ln.startswith(("import ", "from "))]
    for forbidden in ("nichibo.browser", "nichibo.vehicle", "nichibo.search", "playwright", "requests", "httpx", "urllib.request"):
        assert not any(forbidden in ln for ln in imports), forbidden


def test_parse_har_keeps_only_lots_bids_and_searches():
    s = parse_har(HAR)
    assert s.entries == 13 and s.ignored == 6  # image, translator, cart, stock, POST, other host
    assert [o.ref_no for o in s.opened] == ["T-REF-1", "T-REF-2", "T-REF-3"]  # the reload of T-REF-2 collapsed
    assert [(b.ref_no, b.yen, b.thousands) for b in s.bids] == [("T-REF-2", 1_240_000, 1_240), ("T-REF-9", 800_000, 800)]
    assert [x.chassis for x in s.searches] == ["GDH206"]
    assert s.started.strftime("%H:%M:%S") == "10:00:00" and s.ended.strftime("%H:%M:%S") == "10:02:00"
    assert s.name.startswith("har_manual_session @ 2026-09-15")
    for o in s.opened:  # nothing but ref, time and status is retained
        assert set(vars(o)) == {"ref_no", "at", "status"}


def _seed(store: Store):
    def facts(ref, grade, km, vid=None):
        return {"ref_no": ref, "url": f"https://autosearch.nichibojapan.com/auction/{ref}", "year": 2020, "make": "Toyota",
                "model": "Hiace", "km": km, "grade_raw": grade, "grade": float(grade), "start_price_jpy": 500_000, "vid": vid}

    store.upsert_vehicle("T-REF-1", "GDH206", facts("T-REF-1", "4.5", 50_000))
    store.add_proposal(ref_no="T-REF-1", guide_id=1, model_code="GDH206", snapshot="s", rule_updated_at="u", status="SKIP",
                       stage="hard", summary="2020 Toyota Hiace one", facts_json=facts("T-REF-1", "4.5", 50_000),
                       hard_json={"checks": [{"name": "grade", "ok": False, "detail": "grade 4.5 below minimum 5"}]},
                       created_at="2026-09-15T09:00:00+00:00")
    store.upsert_vehicle("T-REF-2", "GDH206", facts("T-REF-2", "4", 60_000))
    store.add_proposal(ref_no="T-REF-2", guide_id=1, model_code="GDH206", snapshot="s", rule_updated_at="u", status="NEEDS_REVIEW",
                       stage="manual", bid_jpy_k=1_190, summary="2020 Toyota Hiace two", facts_json=facts("T-REF-2", "4", 60_000),
                       hard_json={"checks": []}, created_at="2026-09-15T09:00:00+00:00")
    store.upsert_vehicle("T-REF-4", "GDH206", facts("T-REF-4", "4", 40_000))
    store.add_proposal(ref_no="T-REF-4", guide_id=1, model_code="GDH206", snapshot="s", rule_updated_at="u", status="BID",
                       stage="hard", bid_jpy_k=1_290, summary="2020 Toyota Hiace four", facts_json=facts("T-REF-4", "4", 40_000),
                       hard_json={"checks": []}, created_at="2026-09-15T09:00:00+00:00")
    store.upsert_vehicle("T-REF-5", "GDH206", facts("T-REF-5", "4", 40_000))
    store.add_proposal(ref_no="T-REF-5", guide_id=1, model_code="GDH206", snapshot="s", rule_updated_at="u", status="BID",
                       stage="hard", bid_jpy_k=1_290, summary="proposed after the session", facts_json=facts("T-REF-5", "4", 40_000),
                       hard_json={"checks": []}, created_at="2026-09-15T12:00:00+00:00")
    # T-REF-9 is known to the bot only by Nichibo's vid: the HAR bid names the vid.
    store.upsert_vehicle("REF-NINE", "ANH20W", facts("REF-NINE", "4", 30_000, vid="T-REF-9"))
    store.add_proposal(ref_no="REF-NINE", guide_id=2, model_code="ANH20W", snapshot="s", rule_updated_at="u", status="SKIP",
                       stage="card", summary="alphard nine", facts_json=facts("REF-NINE", "4", 30_000, vid="T-REF-9"),
                       hard_json={"checks": [{"name": "odo", "ok": False, "detail": "too many km"}]},
                       created_at="2026-09-15T09:00:00+00:00")


def test_join_session_buckets_disagreements_both_ways(cfg):
    store = Store(cfg.db_path)
    _seed(store)
    rep = join_session(store, parse_har(HAR))
    assert [it["ref_no"] for it in rep.agree] == ["T-REF-2"] and rep.agree[0]["delta_k"] == 50
    skipped = {it["ref_no"]: it for it in rep.bot_skipped_jim_bid}
    assert set(skipped) == {"T-REF-9"} and skipped["T-REF-9"]["model_code"] == "ANH20W" and "odo" in skipped["T-REF-9"]["reasons"][0]
    assert [it["ref_no"] for it in rep.unknown_to_bot] == ["T-REF-3"] and rep.unknown_to_bot[0]["model_code"] == "GDH206"  # from the search
    assert [it["ref_no"] for it in rep.bot_proposed_not_opened] == ["T-REF-4"]  # T-REF-5 came after the session
    assert {it["ref_no"] for it in rep.opened_no_bid} == {"T-REF-1"} and rep.opened_no_bid[0]["bot_status"] == "SKIP"
    g = rep.per_model["GDH206"]
    assert g["opened"] == 3 and g["bid"] == 1 and g["bot_proposed"] == 2 and g["overlap"] == 1
    assert g["median_gap_s"] == 40 and g["order_consistency"] == 1.0  # gaps 20 s and 60 s; grade 4.5 before 4
    text = render_report(rep)
    assert "Jim 800K" in text and "Both would bid (1)" in text and "T-REF-4" in text and "GDH206: 3 / 1 / 2 (1)" in text
    # --save keeps refs, times and amounts only, and is idempotent.
    assert save_session(store, parse_har(HAR)) == 5
    assert save_session(store, parse_har(HAR)) == 0
    rows = store.conn.execute("SELECT action, COUNT(*) FROM manual_actions GROUP BY action ORDER BY action").fetchall()
    assert [tuple(r) for r in rows] == [("bid", 2), ("open", 3)]
    store.close()


def test_cli_learn(cfg, monkeypatch):
    from typer.testing import CliRunner
    import auction_bot.cli as cli
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    store = Store(cfg.db_path)
    _seed(store)
    store.close()
    res = CliRunner().invoke(cli.app, ["learn", "--har", str(HAR), "-m", "GDH206", "--save"])
    assert res.exit_code == 0, res.output
    assert "Both would bid (1)" in res.output and "ANH20W" not in res.output and "Saved 5 new actions" in res.output
