"""Review telemetry (what the reviewer looked at) and the `bot insights` summaries."""
import json
import sqlite3

from fastapi.testclient import TestClient

from auction_bot.insights import propose_skip_lines, reason_keywords, render, summarise
from auction_bot.pipeline import scan
from auction_bot.review.app import create_app
from auction_bot.store import DECISION_COLUMNS, Store
from tests.test_pipeline_offline import FakeBrowser, cfg  # noqa: F401  (fixture)


def test_old_database_gains_the_new_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, ref_no TEXT, model_code TEXT, snapshot TEXT, status TEXT,
          stage TEXT, bid_jpy_k INTEGER, facts_json TEXT, created_at TEXT, decision TEXT DEFAULT 'pending',
          decision_reason TEXT, decided_at TEXT, approved_bid_jpy_k INTEGER);
        CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER, ref_no TEXT, model_code TEXT,
          action TEXT, reason TEXT, amount_jpy_k INTEGER, make_rule INTEGER DEFAULT 0, at TEXT);
        INSERT INTO proposals(ref_no, model_code, snapshot, status, stage, bid_jpy_k, facts_json, created_at)
          VALUES('R1', 'GDH206', 's', 'BID', 'hard', 1240, '{}', 't');
    """)
    conn.commit()
    conn.close()
    s = Store(path)
    cols = {r["name"] for r in s.conn.execute("PRAGMA table_info(decisions)")}
    assert set(DECISION_COLUMNS) <= cols and "guide_id" in {r["name"] for r in s.conn.execute("PRAGMA table_info(proposals)")}
    s.decide(1, "reject", "old db still works", None, False)  # no events logged: telemetry columns stay empty
    d = s.decision_for(1)
    assert d["decide_seconds"] is None and d["via"] == "detail" and d["proposed_jpy_k"] == 1240
    s.close()


def test_events_feed_the_decision_row(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    pids = [r["id"] for r in store.list_proposals(decision="pending")]
    store.close()
    client = TestClient(create_app(cfg))
    # Opening the detail page logs "open"; the page reports what was looked at.
    assert client.get(f"/p/{pids[0]}").status_code == 200
    assert client.post(f"/p/{pids[0]}/event", json={"event": "sheet_zoom"}).status_code == 204
    assert client.post(f"/p/{pids[0]}/event", json={"event": "amount_edit", "value": "1290"}).status_code == 204
    assert client.post(f"/p/{pids[0]}/event", json={"event": "bogus"}).status_code == 400
    assert client.post(f"/p/{pids[0]}/event", json={"event": "decide"}).status_code == 400  # only the server logs decisions
    assert client.post("/p/999999/event", json={"event": "open"}).status_code == 404
    r = client.post(f"/p/{pids[0]}/decide", data={"action": "approve", "amount": "1290", "via": "detail"}, follow_redirects=False)
    assert r.status_code == 303
    store = Store(cfg.db_path)
    d = store.decision_for(pids[0])
    assert d["opened_at"] and d["decide_seconds"] is not None and d["decide_seconds"] >= 0
    assert d["sheet_zoomed"] == 1 and d["photos_clicked"] == 0 and d["nichibo_opened"] == 0
    assert d["amount_edited"] == 1 and d["proposed_jpy_k"] == 1240 and d["amount_jpy_k"] == 1290 and d["via"] == "detail"
    assert [e["event"] for e in store.events(pids[0])] == ["open", "sheet_zoom", "amount_edit", "decide"]
    # The decided page shows the summary; a decided proposal is not re-opened for telemetry.
    r = client.get(f"/p/{pids[0]}")
    assert "sheet zoomed" in r.text and "amount changed from 1,240K" in r.text
    assert len(store.events(pids[0])) == 4
    # A reject via the JSON path from the triage view.
    r = client.post(f"/p/{pids[1]}/decide", data={"action": "reject", "reason": "10 seats", "via": "triage"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["decision"] == "rejected" and r.json()["next_id"] is None
    d = store.decision_for(pids[1])
    assert d["via"] == "triage" and d["amount_edited"] == 0 and d["decide_seconds"] is None  # never opened
    rows = store.decisions_with_facts("GDH206")
    assert len(rows) == 2 and rows[0]["facts"]["chassis"] == "GDH206V" and rows[0]["action"] == "reject"
    store.close()


def _row(action, reason=None, secs=None, sheet=0, photos=0, nichibo=0, edited=0, amount=None, proposed=1240, **facts):
    f = dict(ref_no="r", url="u", year=2020, make="Toyota", model="Hiace", variant_text="Van DX 4WD", km=100_000,
             grade_raw="4", grade=4.0, start_price_jpy=1_000_000, auction_place="CAA Gifu", colour="White", transmission="AT")
    f.update(facts)
    return {"model_code": "GDH206", "action": action, "reason": reason, "decide_seconds": secs, "sheet_zoomed": sheet,
            "photos_clicked": photos, "nichibo_opened": nichibo, "amount_edited": edited, "amount_jpy_k": amount,
            "proposed_jpy_k": proposed, "bid_jpy_k": proposed, "summary": f"{f['year']} {f['variant_text']}", "facts": f}


def test_reason_keywords():
    assert "seats" in reason_keywords("10 seats on the sheet")
    assert reason_keywords("Dump body, not a van") >= {"body type"}
    assert "start price" in reason_keywords("start above our bid")
    assert reason_keywords("looks tired") == {"looks tired"}
    assert reason_keywords("") == set()


def test_summarise_and_render():
    rows = [
        _row("reject", "10 seats", secs=12, sheet=1),
        _row("reject", "8 seats, welcab", secs=30, sheet=1),
        _row("approve", None, secs=20, sheet=1, edited=1, amount=1290),
        _row("approve", None, secs=8, sheet=0),
        _row("reject", "start above bid", secs=3, sheet=1),
    ]
    ins = summarise(rows)["GDH206"]
    assert ins.decided == 5 and ins.approved == 2 and ins.rejected == 3
    assert ins.median_seconds == 12 and ins.p90_seconds == 30
    assert ins.sheet_zoom_share == 0.8 and ins.photos_share == 0 and ins.photos_never_consulted
    assert ins.amount_edit_share == 0.5 and ins.median_amount_delta_k == 50
    assert ins.rejection_keywords["seats"] == 2 and ins.rejection_keywords["start price"] == 1
    text = render({"GDH206": ins}, [])
    assert "GDH206: 5 decided, 2 approved, 3 rejected" in text and "photos never consulted" in text and "seats 2" in text
    assert render({}, []).startswith("No decisions")


def test_propose_skip_lines_counts_support_and_conflicts():
    rows = [
        _row("reject", "dump body", variant_text="Van DUMP 4WD", auction_place="USS Kyushu"),
        _row("reject", "dump again", variant_text="Truck DUMP", auction_place="USS Kyushu"),
        _row("reject", "start above our bid", start_price_jpy=2_000_000, auction_place="USS Kyushu"),
        _row("approve", None, variant_text="Van DX", auction_place="CAA Gifu", colour="White"),
        _row("approve", None, variant_text="Van GL", auction_place="USS Kyushu", colour="White"),
    ]
    props = propose_skip_lines(rows, min_support=2)
    lines = {p.line: p for p in props}
    assert "skip: variant contains DUMP" in lines and lines["skip: variant contains DUMP"].support == 2
    assert "skip: hall is USS Kyushu" not in lines  # an approved lot came from that hall: conflict
    assert "skip: colour is White" not in lines  # approvals were white
    assert "skip: start >= bid" not in lines  # only one rejection supports it
    assert propose_skip_lines(rows, min_support=1)
    text = render(summarise(rows), props)
    assert 'bot skip-line GDH206 "skip: variant contains DUMP"' in text


def test_cli_insights(cfg, monkeypatch):
    from typer.testing import CliRunner
    import auction_bot.cli as cli
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    runner = CliRunner()
    res = runner.invoke(cli.app, ["insights"])
    assert res.exit_code == 0 and "No decisions" in res.output
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    pid = store.list_proposals(decision="pending")[0]["id"]
    store.decide(pid, "reject", "10 seats", None, False)
    store.close()
    res = runner.invoke(cli.app, ["insights", "-m", "gdh206", "--days", "1"])
    assert res.exit_code == 0 and "GDH206: 1 decided, 0 approved, 1 rejected" in res.output and "seats 1" in res.output
