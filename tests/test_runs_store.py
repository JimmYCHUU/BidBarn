"""The `runs` table: one row per command started from the dashboard."""
import json
import sqlite3

import pytest

from auction_bot.config import Config
from auction_bot.store import Store

OLD_SCHEMA = """
CREATE TABLE proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ref_no TEXT NOT NULL, guide_id INTEGER, model_code TEXT NOT NULL,
  snapshot TEXT NOT NULL, rule_updated_at TEXT, status TEXT NOT NULL, stage TEXT NOT NULL, bid_jpy_k INTEGER,
  base_jpy_k INTEGER, summary TEXT, facts_json TEXT, hard_json TEXT, soft_json TEXT, evidence_json TEXT,
  created_at TEXT NOT NULL, decision TEXT NOT NULL DEFAULT 'pending', decision_reason TEXT, decided_at TEXT,
  approved_bid_jpy_k INTEGER, placed_at TEXT, placement_json TEXT);
CREATE TABLE decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER NOT NULL, ref_no TEXT NOT NULL, model_code TEXT NOT NULL,
  action TEXT NOT NULL, reason TEXT, amount_jpy_k INTEGER, make_rule INTEGER DEFAULT 0, at TEXT NOT NULL);
CREATE TABLE bids (
  ref_no TEXT PRIMARY KEY, proposal_id INTEGER NOT NULL, model_code TEXT NOT NULL, bid_jpy_k INTEGER NOT NULL,
  placed_at TEXT NOT NULL, ok INTEGER NOT NULL, message TEXT, screenshot TEXT);
"""


def _tables(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_runs_table_appears_in_a_database_from_before(tmp_path):
    path = tmp_path / "bot.sqlite"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO proposals(ref_no, model_code, snapshot, status, stage, summary, created_at) "
                 "VALUES('REF1', 'GDH206', 'rules_x.json', 'NEEDS_REVIEW', 'manual', '2020 Toyota Hiace', '2026-09-20T01:00:00+00:00')")
    conn.commit()
    conn.close()
    assert "runs" not in _tables(path)
    store = Store(path)
    try:
        assert "runs" in _tables(path)
        assert store.list_runs() == [] and store.running_runs() == [] and store.last_runs_by_command() == {}
        assert store.proposal(1)["summary"] == "2020 Toyota Hiace"  # what was there is still there
        columns = {r["name"] for r in store.conn.execute("PRAGMA table_info(runs)")}
        assert columns == {"id", "command", "args_json", "source", "pid", "started_at", "finished_at", "status", "exit_code",
                           "error", "progress_json", "summary_json", "log_path", "cancel_requested_at",
                           "session_id", "pause_requested_at", "deleted_at"}
        assert store.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        store.close()
    Store(path).close()  # opening it again changes nothing
    assert "runs" in _tables(path)


def test_store_in_memory_still_opens():
    store = Store(":memory:")  # cannot be WAL: the pragma is not allowed to break it
    try:
        assert store.start_run("replay", []) == 1 and store.run(1)["status"] == "running"
    finally:
        store.close()


def test_run_from_start_to_finish(tmp_path):
    store = Store(tmp_path / "bot.sqlite")
    try:
        rid = store.start_run("scan", ["scan", "-m", "GDH206", "--headless"])
        row = store.run(rid)
        assert row["command"] == "scan" and json.loads(row["args_json"]) == ["scan", "-m", "GDH206", "--headless"]
        assert row["status"] == "running" and row["source"] == "dashboard" and row["started_at"] and row["pid"] is None
        assert row["finished_at"] is None and row["log_path"] is None and row["cancel_requested_at"] is None
        assert [r["id"] for r in store.running_runs()] == [rid]

        store.set_run_pid(rid, 4321)
        store.set_run_log(rid, tmp_path / "runs" / f"{rid}.log")
        store.update_run_progress(rid, {"stage": "scan", "guide": 3, "guides": 46, "message": "乗車定員"})
        row = store.run(rid)
        assert row["pid"] == 4321 and row["log_path"].endswith(f"{rid}.log")
        assert json.loads(row["progress_json"]) == {"stage": "scan", "guide": 3, "guides": 46, "message": "乗車定員"}

        assert store.finish_run(rid, "ok", exit_code=0, summary={"guides": 46, "review": 12}) is True
        row = store.run(rid)
        assert row["status"] == "ok" and row["exit_code"] == 0 and row["finished_at"] and row["error"] is None
        assert json.loads(row["summary_json"]) == {"guides": 46, "review": 12}
        assert json.loads(row["progress_json"])["guide"] == 3  # the last progress stays with the run
        assert store.running_runs() == []
        # A run is closed once: a late second close does not rewrite how it ended.
        assert store.finish_run(rid, "failed", exit_code=1, error="late") is False
        assert store.run(rid)["status"] == "ok" and store.run(rid)["error"] is None
        assert store.run(999) is None
    finally:
        store.close()


def test_failed_and_stopped_runs(tmp_path):
    store = Store(tmp_path / "bot.sqlite")
    try:
        a = store.start_run("fetch-rules", ["fetch-rules", "--headless"], pid=11)
        store.finish_run(a, "failed", exit_code=1, error="Carbarn dashboard did not load the bidding guides")
        assert store.run(a)["status"] == "failed" and store.run(a)["summary_json"] is None
        assert store.run(a)["error"].startswith("Carbarn dashboard") and store.run(a)["pid"] == 11

        b = store.start_run("scan", ["scan", "--headless"], source="cli")
        assert store.run(b)["source"] == "cli"
        store.request_run_cancel(b)
        asked = store.run(b)["cancel_requested_at"]
        assert asked and store.run(b)["status"] == "running"  # asking is not stopping
        store.request_run_cancel(b)
        assert store.run(b)["cancel_requested_at"] == asked  # the first click counts
        store.finish_run(b, "stopped", exit_code=1)
        assert store.run(b)["status"] == "stopped"
        store.request_run_cancel(a)
        assert store.run(a)["cancel_requested_at"] is None  # a run that has ended cannot be stopped

        for bad in ("running", "done", "", "OK"):
            with pytest.raises(ValueError):
                store.finish_run(store.start_run("replay", ["replay"]), bad)
    finally:
        store.close()


def test_list_and_last_by_command(tmp_path):
    store = Store(tmp_path / "bot.sqlite")
    try:
        s1 = store.start_run("scan", ["scan"])
        store.finish_run(s1, "ok", exit_code=0, summary={"guides": 1})
        c1 = store.start_run("check-login", ["check-login"])
        store.finish_run(c1, "ok", exit_code=0, summary={"nichibo": True, "carbarn": True})
        s2 = store.start_run("scan", ["scan"])
        store.finish_run(s2, "failed", exit_code=1, error="Rules snapshot is 13.0h old")
        c2 = store.start_run("check-login", ["check-login"])

        assert [r["id"] for r in store.list_runs()] == [c2, s2, c1, s1]  # newest first
        assert [r["id"] for r in store.list_runs(limit=2)] == [c2, s2]
        assert [r["id"] for r in store.list_runs(command="scan")] == [s2, s1]
        assert store.list_runs(command="place") == []

        last = store.last_runs_by_command()
        assert set(last) == {"scan", "check-login"} and last["scan"]["id"] == s2 and last["check-login"]["id"] == c2
        good = store.last_runs_by_command(status="ok")
        assert good["scan"]["id"] == s1 and good["check-login"]["id"] == c1
        assert json.loads(good["check-login"]["summary_json"]) == {"nichibo": True, "carbarn": True}
        assert [r["id"] for r in store.running_runs()] == [c2]
    finally:
        store.close()


def test_recent_decisions(tmp_path):
    store = Store(tmp_path / "bot.sqlite")
    try:
        assert store.recent_decisions() == []
        common = dict(snapshot="rules_x.json", status="NEEDS_REVIEW", stage="manual", facts_json={}, hard_json={"checks": []})
        a = store.add_proposal(ref_no="REF1", model_code="GDH206", bid_jpy_k=1240, summary="2020 Toyota Hiace", **common)
        b = store.add_proposal(ref_no="REF2", model_code="ZWR90", bid_jpy_k=2130, summary="2022 Toyota Noah", **common)
        # The decisions a person made on the review page, written here as that page's rows (tests only).
        store.conn.execute("INSERT INTO decisions(proposal_id, ref_no, model_code, action, reason, amount_jpy_k, at) "
                           "VALUES(?, 'REF1', 'GDH206', 'approve', NULL, 1240, '2026-09-28T01:00:00+00:00')", (a,))
        store.conn.execute("INSERT INTO decisions(proposal_id, ref_no, model_code, action, reason, amount_jpy_k, at) "
                           "VALUES(?, 'REF2', 'ZWR90', 'reject', 'sticker paint', NULL, '2026-09-28T01:05:00+00:00')", (b,))
        store.conn.commit()
        rows = store.recent_decisions()
        assert rows == [
            {"action": "reject", "amount": None, "reason": "sticker paint", "ref_no": "REF2", "model_code": "ZWR90",
             "at": "2026-09-28T01:05:00+00:00", "proposal_id": b, "summary": "2022 Toyota Noah"},
            {"action": "approve", "amount": 1240, "reason": None, "ref_no": "REF1", "model_code": "GDH206",
             "at": "2026-09-28T01:00:00+00:00", "proposal_id": a, "summary": "2020 Toyota Hiace"},
        ]
        assert [r["ref_no"] for r in store.recent_decisions(limit=1)] == ["REF2"]
        assert store.proposal(a)["decision"] == "pending"  # reading decisions decides nothing
    finally:
        store.close()


def test_runs_dir_is_beside_the_database(tmp_path):
    cfg = Config()
    cfg.db_path = tmp_path / "data" / "bot.sqlite"
    assert not (tmp_path / "data" / "runs").exists()
    assert cfg.runs_dir == tmp_path / "data" / "runs" and cfg.runs_dir.is_dir()
