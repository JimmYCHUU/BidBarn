"""Pause, resume and delete: which run wrote a lot, deleting without a decision, the paused session.

A delete is never a decision (CLAUDE.md, rules 0c and 1): the row stays, nothing goes to `decisions`,
and the next scan checks the vehicle again."""
import sqlite3

from auction_bot.store import Store
from tests.test_pipeline_offline import cfg  # noqa: F401  (fixture)
from tests.test_triage import _seed


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
