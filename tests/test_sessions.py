"""Pause, resume and delete: which run wrote a lot, deleting without a decision, the paused session.

A delete is never a decision (CLAUDE.md, rules 0c and 1): the row stays, nothing goes to `decisions`,
and the next scan checks the vehicle again."""
import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from auction_bot.pipeline import scan
from auction_bot.review.app import create_app
from auction_bot.store import Store
from tests.test_pipeline_offline import FakeBrowser, cfg  # noqa: F401  (fixture)
from tests.test_triage import _seed

JSON = {"Accept": "application/json"}


def lot(store: Store, ref: str, run_id: int | None = None, status: str = "NEEDS_REVIEW") -> int:
    """A GDH206 lot waiting for review (or a pending SKIP), written by dashboard run `run_id` (None: from a console)."""
    pid = _seed(store, ref, "GDH206", "4", 50_000, status=status)
    store.conn.execute("UPDATE proposals SET run_id=? WHERE id=?", (run_id, pid))
    store.conn.commit()
    return pid


# -- the store -------------------------------------------------------------------------------------

def test_an_older_database_gets_the_new_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, ref_no TEXT NOT NULL, model_code TEXT NOT NULL, "
        "snapshot TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, created_at TEXT NOT NULL, "
        "decision TEXT NOT NULL DEFAULT 'pending');"
        "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, command TEXT NOT NULL, args_json TEXT NOT NULL, "
        "source TEXT NOT NULL DEFAULT 'dashboard', pid INTEGER, started_at TEXT NOT NULL, finished_at TEXT, "
        "status TEXT NOT NULL, exit_code INTEGER, error TEXT, progress_json TEXT, summary_json TEXT, log_path TEXT, "
        "cancel_requested_at TEXT);")
    con.close()
    s = Store(path)
    try:
        assert "run_id" in {r["name"] for r in s.conn.execute("PRAGMA table_info(proposals)")}
        assert {"session_id", "pause_requested_at", "deleted_at"} <= {r["name"] for r in s.conn.execute("PRAGMA table_info(runs)")}
        assert "ix_proposals_run" in {r["name"] for r in s.conn.execute("PRAGMA index_list(proposals)")}
    finally:
        s.close()


def test_the_paused_session_is_the_one_whose_newest_run_paused(cfg):
    s = Store(cfg.db_path)
    try:
        assert s.paused_session() is None
        first = s.start_run("bid", ["bid"])
        assert s.run(first)["session_id"] == first  # a new run starts its own session
        s.request_run_pause(first)
        assert s.run(first)["pause_requested_at"] and s.finish_run(first, "paused")
        assert s.paused_session()["id"] == first and s.paused_session()["session_id"] == first
        resumed = s.start_run("bid", ["bid"], session_id=first)  # Resume
        assert s.run(resumed)["session_id"] == first and s.paused_session() is None
        assert s.finish_run(resumed, "paused") and s.paused_session()["id"] == resumed
        s.delete_session(first)
        assert s.paused_session() is None and s.run(first)["deleted_at"] and s.run(resumed)["deleted_at"]
    finally:
        s.close()


def test_delete_takes_only_waiting_lots_and_writes_no_decision(cfg):
    s = Store(cfg.db_path)
    try:
        first = s.start_run("bid", ["bid"])
        resumed = s.start_run("bid", ["bid"], session_id=first)  # one session, two runs
        other = s.start_run("bid", ["bid"])
        a = lot(s, "A", first)
        b = lot(s, "B", resumed)
        skipped = lot(s, "S", first, status="SKIP")  # a skip does not wait for review
        approved = lot(s, "P", first)
        s.conn.execute("UPDATE proposals SET decision='approved', approved_bid_jpy_k=1000 WHERE id=?", (approved,))
        s.conn.commit()
        c = lot(s, "C", other)
        earlier = lot(s, "E")
        assert s.delete_lot(c) and not s.delete_lot(c) and not s.delete_lot(skipped) and not s.delete_lot(approved)
        assert s.delete_session(first) == 2 and s.delete_session(None) == 1
        got = {pid: s.proposal(pid)["decision"] for pid in (a, b, skipped, approved, c, earlier)}
        assert got == {a: "deleted", b: "deleted", skipped: "pending", approved: "approved", c: "deleted", earlier: "deleted"}
        assert s.proposal(a)["decision_reason"] == "session deleted from the review" and s.proposal(a)["decided_at"]
        assert s.proposal(c)["decision_reason"] == "deleted from the review"
        assert s.proposal(approved)["approved_bid_jpy_k"] == 1000
        assert s.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        assert s.conn.execute("SELECT COUNT(*) FROM review_events").fetchone()[0] == 0
        assert s.conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 6  # nothing removed (rule 0c)
        assert s.run(first)["deleted_at"] and s.run(resumed)["deleted_at"] and s.run(other)["deleted_at"] is None
    finally:
        s.close()


def test_a_lot_written_after_its_session_was_deleted_is_swept(cfg):
    s = Store(cfg.db_path)
    try:
        run = s.start_run("bid", ["bid"])
        s.delete_session(run)
        late = lot(s, "L", run)  # written by the run between the click and its end
        other = lot(s, "O")
        assert s.sweep_deleted_sessions() == 1
        assert s.proposal(late)["decision"] == "deleted" and s.proposal(other)["decision"] == "pending"
    finally:
        s.close()


def test_session_info_and_the_review_rows_name_their_session(cfg):
    s = Store(cfg.db_path)
    try:
        first = s.start_run("bid", ["bid"])
        s.update_run_progress(first, {"stage": "scan", "guide": 21, "guides": 46})
        s.finish_run(first, "paused")
        resumed = s.start_run("bid", ["bid"], session_id=first)
        lot(s, "A", first)
        lot(s, "B", resumed)
        lot(s, "S", resumed, status="SKIP")
        lot(s, "E")
        info = s.session_info(first)
        assert info == {"id": first, "command": "bid", "started_at": s.run(first)["started_at"], "run_id": resumed,
                        "status": "running", "progress": None, "deleted_at": None, "runs": [first, resumed]}
        assert s.session_info(resumed) is None  # a session is named by its first run
        assert s.session_waiting(first) == 2
        assert {r["ref_no"]: r["session_id"] for r in s.list_triage()} == {"A": first, "B": first, "E": None}
    finally:
        s.close()


def test_a_run_from_before_sessions_puts_its_lots_under_earlier_scans(cfg):
    s = Store(cfg.db_path)
    try:
        old = s.start_run("scan", ["scan"])
        s.conn.execute("UPDATE runs SET session_id=NULL, status='paused' WHERE id=?", (old,))  # a row from before the column
        s.conn.commit()
        pid = lot(s, "A", old)
        assert s.list_triage()[0]["session_id"] is None
        assert s.paused_session() is None  # a run with no session is never the paused one
        assert s.delete_session(None) == 1 and s.proposal(pid)["decision"] == "deleted"
    finally:
        s.close()


# -- the run side: the pause file, the run id, the scan -----------------------------------------------

def test_the_pause_file_pauses_and_the_cancel_file_wins(monkeypatch, tmp_path):
    from auction_bot import progress
    cancel, pause = tmp_path / "1.cancel", tmp_path / "1.pause"
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(cancel))
    monkeypatch.setenv("BIDBARN_PAUSE_FILE", str(pause))
    progress.check_cancel()  # neither file: the run carries on
    pause.write_text("pause", encoding="utf-8")
    with pytest.raises(progress.Paused, match="paused from the dashboard") as paused:
        progress.check_cancel()
    assert isinstance(paused.value, progress.Cancelled)  # every place that lets a stop through lets a pause through
    cancel.write_text("stop", encoding="utf-8")
    with pytest.raises(progress.Cancelled) as stopped:
        progress.check_cancel()
    assert not isinstance(stopped.value, progress.Paused)  # Stop wins


def test_the_run_id_comes_from_the_dashboard(monkeypatch):
    from auction_bot import progress
    monkeypatch.delenv("BIDBARN_RUN_ID", raising=False)
    assert progress.run_id() is None
    monkeypatch.setenv("BIDBARN_RUN_ID", "12")
    assert progress.run_id() == 12
    monkeypatch.setenv("BIDBARN_RUN_ID", "x")
    assert progress.run_id() is None


def test_a_paused_scan_stops_before_the_first_page(cfg, monkeypatch, tmp_path):
    from auction_bot import progress
    flag = tmp_path / "1.pause"
    flag.write_text("pause", encoding="utf-8")
    monkeypatch.setenv("BIDBARN_PAUSE_FILE", str(flag))
    store = Store(cfg.db_path)
    browser = FakeBrowser()
    with pytest.raises(progress.Paused):
        scan(cfg, browser, store, ["GDH206"], limit_per_model=1)
    assert browser.visited == []
    store.close()


def test_a_pause_that_lands_at_the_review_ends_the_wait(cfg, monkeypatch, tmp_path):
    from auction_bot import progress
    from auction_bot.run import wait_for_review
    flag = tmp_path / "1.pause"
    flag.write_text("pause", encoding="utf-8")  # clicked as the scan finished: the run is already waiting
    monkeypatch.setenv("BIDBARN_PAUSE_FILE", str(flag))
    monkeypatch.setenv("BIDBARN_FINISH_FILE", str(tmp_path / "1.finish"))
    with pytest.raises(progress.Paused):
        wait_for_review(cfg, echo=lambda _: None, poll_seconds=0.01)


def test_the_scan_writes_its_run_on_every_lot_and_replay_keeps_it(cfg, monkeypatch):
    from auction_bot.pipeline import reevaluate
    monkeypatch.setenv("BIDBARN_RUN_ID", "7")
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    assert {r["run_id"] for r in store.list_proposals(decision=None, limit=1000)} == {7}
    monkeypatch.setenv("BIDBARN_RUN_ID", "8")  # a replay run of its own: the lots stay in the session that found them
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["guides"][0]["tree"]["minGrade"] = 4.5
    data["guides"][0]["tree"]["updatedAt"] = "2026-09-15T00:00:00"
    snap.write_text(json.dumps(data), encoding="utf-8")
    assert reevaluate(cfg, store, ["GDH206"], apply=True).applied == 2
    assert {r["run_id"] for r in store.list_proposals(decision=None, limit=1000)} == {7}
    store.close()


def test_a_deleted_lot_is_checked_again_and_keeps_its_judgement(cfg, monkeypatch):
    import auction_bot.pipeline as pl
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    first = store.list_triage()[0]
    assert store.delete_lot(first["id"]) and store.list_triage() == []
    asked = []
    real = pl.evaluate_vehicle

    def spy(*args, **kwargs):
        asked.append((args[2].ref_no, kwargs.get("previous_soft")))
        return real(*args, **kwargs)

    monkeypatch.setattr(pl, "evaluate_vehicle", spy)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    again = store.list_triage()
    assert stats.opened == 1 and [r["ref_no"] for r in again] == [first["ref_no"]] and again[0]["id"] != first["id"]
    assert asked == [(first["ref_no"], json.loads(first["soft_json"]))]  # its judgement goes along, to be reused
    assert store.proposal(first["id"])["decision"] == "deleted"  # the deleted row stays as it was
    store.close()


def test_a_lot_left_superseded_by_a_forced_stop_is_checked_again(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    first = store.list_triage()[0]
    store.supersede_pending(first["ref_no"], first["guide_id"])  # the process was ended between the two writes
    assert store.list_triage() == []
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    assert stats.opened == 1 and [r["ref_no"] for r in store.list_triage()] == [first["ref_no"]]
    store.close()


# -- the review page: Delete and Delete session -------------------------------------------------------

def test_deleting_a_lot_records_no_decision_and_teaches_nothing(cfg):
    from auction_bot.dashboard import status as st
    from auction_bot.insights import propose_skip_lines
    s = Store(cfg.db_path)
    try:
        a, b, c = lot(s, "A"), lot(s, "B"), lot(s, "C")
    finally:
        s.close()
    client = TestClient(create_app(cfg))
    r = client.post(f"/p/{a}/delete", headers=JSON)
    assert r.status_code == 200 and r.json() == {"ok": True, "decision": "deleted", "next_id": c}
    assert client.post(f"/p/{a}/delete", headers=JSON).status_code == 409  # once only: it no longer waits
    assert client.post("/p/999/delete", headers=JSON).status_code == 404
    assert client.post(f"/p/{a}/decide", data={"action": "approve", "amount": "1000"}, headers=JSON).status_code == 409
    r = client.post(f"/p/{b}/delete", data={"amount": "1000", "reason": ""}, follow_redirects=False)  # the lot page's form
    assert r.status_code == 303 and r.headers["location"] == f"/p/{c}"
    s = Store(cfg.db_path)
    try:
        row = s.proposal(a)
        assert row["decision"] == "deleted" and row["decided_at"] and row["approved_bid_jpy_k"] is None
        assert s.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        assert s.conn.execute("SELECT COUNT(*) FROM judge_feedback").fetchone()[0] == 0
        assert propose_skip_lines(s.decisions_with_facts()) == [] and st.day_log(s, [])["today"] == []
        rows = {r["code"]: r for r in st.model_rows(cfg, s)}
        assert rows["GDH206"]["lots"] == 1 and rows["GDH206"]["to_review"] == 1  # the deleted lots are not counted
        assert [r["id"] for r in s.list_triage()] == [c]
    finally:
        s.close()
    page = client.get(f"/p/{a}").text
    assert "Deleted from the review" in page and "Your decision" not in page
    assert f'href="/p/{a}"' in client.get("/list?decision=deleted").text
    assert '<option value="deleted"' in client.get("/list").text
    assert 'formaction="/p/' in client.get(f"/p/{c}").text  # the lot page offers Delete beside Approve and Reject


def test_the_review_page_has_a_section_per_session_newest_first(cfg):
    from tests.test_dashboard_app import FakeJobs
    s = Store(cfg.db_path)
    try:
        older = s.start_run("bid", ["bid", "--no-browser"])
        s.update_run_progress(older, {"stage": "scan", "guide": 21, "guides": 46, "code": "GDH206"})
        s.finish_run(older, "paused")
        newer = s.start_run("scan", ["scan"])
        s.finish_run(newer, "ok")
        lot(s, "A", older)
        lot(s, "B", newer)
        lot(s, "C")
    finally:
        s.close()
    page = TestClient(create_app(cfg, jobs=FakeJobs(cfg))).get("/review").text
    assert re.findall(r'data-delete-session="(\w+)"', page) == [str(newer), str(older), "earlier"]
    assert "paused at guide 21 of 46" in page and "Earlier scans" in page and "3 lots in 1 model, 3 sessions" in page
    assert page.count("data-delete data-confirm") == 3 and "<kbd>d</kbd>" in page  # Delete on every lot, and its key


def test_deleting_a_session_takes_only_its_waiting_lots_and_stops_its_run(cfg):
    from tests.test_dashboard_app import FakeJobs
    s = Store(cfg.db_path)
    try:
        ended = s.start_run("bid", ["bid", "--no-browser"])
        s.finish_run(ended, "stopped")
        live = s.start_run("bid", ["bid", "--no-browser"])
        mine, rejected = lot(s, "A", ended), lot(s, "B", ended)
        skipped = lot(s, "S", ended, status="SKIP")
        s.conn.execute("UPDATE proposals SET decision='rejected', decision_reason='repaint' WHERE id=?", (rejected,))
        s.conn.commit()
        running_lot, earlier = lot(s, "C", live), lot(s, "D")
    finally:
        s.close()
    jobs = FakeJobs(cfg)
    jobs.live = live
    client = TestClient(create_app(cfg, jobs=jobs))
    assert client.post(f"/sessions/{ended}/delete", headers=JSON).json() == {"ok": True, "deleted": 1}
    assert client.post(f"/sessions/{ended}/delete", headers=JSON).json() == {"ok": True, "deleted": 0}  # twice is harmless
    assert jobs.cancelled == []  # an ended session has nothing to stop
    jobs.progress_is({"stage": "place", "i": 0, "total": 1})
    assert client.post(f"/sessions/{live}/delete", headers=JSON).status_code == 409  # never while it places bids
    jobs.progress_is({"stage": "scan", "guide": 3, "guides": 46})
    assert client.post(f"/sessions/{live}/delete", headers=JSON).json() == {"ok": True, "deleted": 1}
    assert jobs.cancelled == [live] and jobs.deleted == [live]
    assert client.post("/sessions/earlier/delete", headers=JSON).json() == {"ok": True, "deleted": 1}
    assert client.post("/sessions/999/delete", headers=JSON).status_code == 404
    assert client.post("/sessions/nope/delete", headers=JSON).status_code == 404
    r = client.post(f"/sessions/{ended}/delete", data={"next": "/"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    s = Store(cfg.db_path)
    try:
        got = {pid: s.proposal(pid)["decision"] for pid in (mine, rejected, skipped, running_lot, earlier)}
        assert got == {mine: "deleted", rejected: "rejected", skipped: "pending", running_lot: "deleted", earlier: "deleted"}
        assert s.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    finally:
        s.close()
