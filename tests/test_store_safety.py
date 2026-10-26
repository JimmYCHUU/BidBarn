"""The store's own safety: one claim per vehicle, a pending check inside decide, and Reopen."""
import sqlite3
import time

import pytest

from auction_bot.dashboard.status import day_log
from auction_bot.store import Refused, Store, VehicleClaimed


def _add(store, ref="R1", guide=223, bid=1000):
    return store.add_proposal(
        ref_no=ref, guide_id=guide, model_code="GDH206", snapshot="s.json", rule_updated_at="u",
        status="NEEDS_REVIEW", stage="hard", bid_jpy_k=bid, summary=f"lot {ref}",
        facts_json={"ref_no": ref, "start_price_jpy": 0}, hard_json={}, soft_json={}, evidence_json={})


def test_an_approval_claims_the_vehicle(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False, via="desk")
    claim = s.intent_for("R1")
    assert claim["proposal_id"] == a and claim["amount_jpy_k"] == 1000


def test_a_second_approval_of_the_vehicle_is_refused_and_nothing_is_recorded(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s, guide=223); b = _add(s, guide=224)
    s.decide(a, "approve", None, 1000, False)
    with pytest.raises(VehicleClaimed) as e:
        s.decide(b, "approve", None, 900, False)
    assert e.value.holder == a and e.value.ref_no == "R1"
    assert f"lot {a}" in str(e.value)
    assert s.proposal(b)["decision"] == "pending"
    assert s.conn.execute("SELECT COUNT(*) FROM decisions WHERE proposal_id=?", (b,)).fetchone()[0] == 0


def test_a_rejection_does_not_claim(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s, guide=223); b = _add(s, guide=224)
    s.decide(a, "reject", "rust", None, False)
    assert s.intent_for("R1") is None
    s.decide(b, "approve", None, 900, False)
    assert s.intent_for("R1")["proposal_id"] == b


def test_a_lot_that_is_no_longer_pending_is_refused(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    with pytest.raises(Refused, match="already decided"):
        s.decide(a, "reject", "x", None, False)
    c = _add(s, "R2"); s.delete_lot(c)
    with pytest.raises(Refused, match="cleared"):
        s.decide(c, "approve", None, 1000, False)
    d = _add(s, "R3"); s.supersede_pending("R3", 223); e = _add(s, "R3", bid=1100)
    with pytest.raises(Refused) as exc:
        s.decide(d, "approve", None, 1000, False)
    assert f"lot {e}" in str(exc.value)
    assert s.intent_for("R3") is None


def test_reopen_returns_an_approval_to_waiting_and_frees_the_vehicle(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s, guide=223); b = _add(s, guide=224)
    s.decide(a, "approve", None, 1000, False)
    s.record_placement(a, "R1", "GDH206", 1000, True, "dry", None, dry_run=True)
    s.reopen(a)
    p = s.proposal(a)
    assert p["decision"] == "pending"
    assert p["decided_at"] is None and p["approved_bid_jpy_k"] is None
    assert p["decision_reason"] is None and p["placement_json"] is None
    assert s.intent_for("R1") is None
    assert s.conn.execute("SELECT undone_at FROM decisions WHERE proposal_id=?", (a,)).fetchone()["undone_at"]
    s.decide(b, "approve", None, 900, False)
    assert s.intent_for("R1")["proposal_id"] == b


def test_reopen_refuses_what_it_cannot_undo(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    with pytest.raises(Refused, match="waiting"):
        s.reopen(a)
    s.decide(a, "approve", None, 1000, False)
    s.record_placement(a, "R1", "GDH206", 1000, True, "ok", None, dry_run=False)
    with pytest.raises(Refused, match="placed live"):
        s.reopen(a)
    with pytest.raises(KeyError):
        s.reopen(9999)


def _frozen_pair(s, status="NEEDS_REVIEW"):
    """An approved lot that `bot scan --rescan` replaced with a newer one of the same vehicle and guide."""
    a = _add(s, "R2"); s.decide(a, "approve", None, 1000, False)
    n = _add(s, "R2", guide=223)
    if status != "NEEDS_REVIEW":
        s.conn.execute("UPDATE proposals SET status=? WHERE id=?", (status, n)); s.conn.commit()
    return a, n


@pytest.mark.parametrize("status", ["NEEDS_REVIEW", "SKIP"])
def test_reopen_retires_a_lot_a_newer_scan_replaced_and_frees_the_vehicle(tmp_path, status):
    s = Store(tmp_path / "b.sqlite"); a, n = _frozen_pair(s, status)
    s.record_placement(a, "R2", "GDH206", 1000, True, "dry ok", None, dry_run=True)
    assert s.reopen(a) == n
    p = s.proposal(a)
    assert p["decision"] == "superseded"
    assert p["decided_at"] is None and p["approved_bid_jpy_k"] is None
    assert p["decision_reason"] is None and p["placement_json"] is None
    assert s.intent_for("R2") is None
    assert s.conn.execute("SELECT undone_at FROM decisions WHERE proposal_id=?", (a,)).fetchone()["undone_at"]
    assert s.proposal(n)["decision"] == "pending" and s.proposal(n)["status"] == status
    s.decide(n, "approve", None, 1100, False)
    assert s.intent_for("R2")["proposal_id"] == n


def test_reopen_of_a_lot_that_waits_again_returns_nothing(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    assert s.reopen(a) is None
    assert s.proposal(a)["decision"] == "pending"


def test_a_replaced_lot_placed_live_still_cannot_be_reopened(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a, n = _frozen_pair(s)
    s.record_placement(a, "R2", "GDH206", 1000, True, "ok", None, dry_run=False)
    with pytest.raises(Refused, match="placed live"):
        s.reopen(a)
    assert s.proposal(a)["decision"] == "approved" and s.intent_for("R2")["proposal_id"] == a
    assert s.conn.execute("SELECT undone_at FROM decisions WHERE proposal_id=?", (a,)).fetchone()["undone_at"] is None


def test_an_undone_decision_is_not_learned_from_but_stays_in_the_day_log(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "reject", "rust", None, False)
    s.reopen(a)
    assert s.decisions_with_facts() == []
    assert s.past_decisions("GDH206") == []
    assert s.recent_decisions() == []
    assert s.decision_for(a) is None
    log = day_log(s, [])
    items = [i for e in log["today"] + log["earlier"] if e["kind"] == "review" for i in e["items"]]
    assert [i["proposal_id"] for i in items] == [a]


def test_an_old_database_gets_its_approvals_claimed_once(tmp_path):
    path = tmp_path / "b.sqlite"
    s = Store(path); a = _add(s, guide=223); b = _add(s, guide=224)
    s.conn.execute("UPDATE proposals SET decision='approved', approved_bid_jpy_k=bid_jpy_k, decided_at=?", ("2026-10-01T00:00:00+00:00",))
    s.conn.execute("DELETE FROM bid_intents")
    s.conn.commit(); s.close()
    s = Store(path)
    assert s.intent_for("R1")["proposal_id"] == a
    s.close()
    s = Store(path)
    assert s.conn.execute("SELECT COUNT(*) FROM bid_intents").fetchone()[0] == 1


def test_a_live_placement_that_lands_during_reopen_is_not_undone(tmp_path, monkeypatch):
    path = tmp_path / "b.sqlite"
    s = Store(path); a = _add(s)
    s.decide(a, "approve", None, 1000, False)

    def placed_meanwhile(ref_no):  # reopen's check passes, then another process places the lot live
        other = sqlite3.connect(str(path))
        other.execute("UPDATE proposals SET placed_at='2026-10-26T00:00:00+00:00' WHERE id=?", (a,))
        other.commit(); other.close()
        return False

    monkeypatch.setattr(s, "already_bid", placed_meanwhile)
    with pytest.raises(Refused, match="placed live"):
        s.reopen(a)
    row = s.proposal(a)
    assert row["decision"] == "approved" and row["placed_at"]
    assert s.intent_for("R1")["proposal_id"] == a
    assert s.conn.execute("SELECT undone_at FROM decisions WHERE proposal_id=?", (a,)).fetchone()["undone_at"] is None


def test_a_later_judgement_bumps_the_revision(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    assert s.proposal(a)["rev"] == 0
    s.update_soft(a, {"mode": "judge"})
    assert s.proposal(a)["rev"] == 1


def test_an_approval_of_an_older_revision_is_refused(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s); b = _add(s, "R2")
    s.update_soft(a, {"mode": "judge"})
    with pytest.raises(Refused, match="changed since you opened it"):
        s.decide(a, "approve", None, 1000, False, seen_rev=0)
    assert s.proposal(a)["decision"] == "pending" and s.intent_for("R1") is None
    s.decide(a, "approve", None, 1000, False, seen_rev=1)
    s.decide(b, "approve", None, 1000, False, seen_rev=None)
    assert s.proposal(a)["decision"] == "approved" and s.proposal(b)["decision"] == "approved"


def test_every_placement_attempt_is_kept(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    s.record_placement(a, "R1", "GDH206", 1000, True, "dry ok", None, dry_run=True)
    s.record_placement(a, "R1", "GDH206", 1000, False, "blocked", None, dry_run=True)
    rows = s.conn.execute("SELECT mode, ok FROM placements WHERE proposal_id=? ORDER BY id", (a,)).fetchall()
    assert [(r["mode"], r["ok"]) for r in rows] == [("dry", 1), ("dry", 0)]


def test_a_lot_placed_as_a_dry_run_is_not_typed_again(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s); b = _add(s, "R2")
    s.decide(a, "approve", None, 1000, False); s.decide(b, "approve", None, 1000, False)
    s.record_placement(a, "R1", "GDH206", 1000, True, "dry ok", None, dry_run=True)
    assert [r["id"] for r in s.approved_unplaced(10, dry_run=True)] == [b]
    assert [r["id"] for r in s.approved_unplaced(10)] == [a, b]  # a live run still takes both
    assert s.dry_done_count() == 1


def test_a_blocked_dry_run_is_tried_again(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    s.record_placement(a, "R1", "GDH206", 1000, False, "blocked", None, dry_run=True)
    assert [r["id"] for r in s.approved_unplaced(10, dry_run=True)] == [a]
    assert s.dry_done_count() == 0


def test_reopened_and_approved_again_is_typed_again(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    s.record_placement(a, "R1", "GDH206", 1000, True, "dry ok", None, dry_run=True)
    s.reopen(a)
    s.decide(a, "approve", None, 1000, False)
    assert [r["id"] for r in s.approved_unplaced(10, dry_run=True)] == [a]


def test_only_the_claim_holder_is_placed(tmp_path):
    path = tmp_path / "b.sqlite"
    s = Store(path); a = _add(s, guide=223); b = _add(s, guide=224)
    s.conn.execute("UPDATE proposals SET decision='approved', approved_bid_jpy_k=bid_jpy_k, decided_at=?", ("2026-10-01T00:00:00+00:00",))
    s.conn.execute("DELETE FROM bid_intents")
    s.conn.commit(); s.close()
    s = Store(path)  # the backfill gives the claim to the oldest
    assert [r["id"] for r in s.approved_unplaced(10)] == [a]
    assert s.still_approved(a) and not s.still_approved(b)


def test_a_dry_result_does_not_land_on_a_reopened_lot(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    s.reopen(a)
    s.record_placement(a, "R1", "GDH206", 1000, True, "dry ok", None, dry_run=True)
    assert s.proposal(a)["placement_json"] is None
    assert s.conn.execute("SELECT COUNT(*) FROM placements WHERE proposal_id=?", (a,)).fetchone()[0] == 1


def test_a_live_result_is_always_recorded(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    s.reopen(a)
    s.record_placement(a, "R1", "GDH206", 1000, True, "ok", None, dry_run=False)
    assert s.proposal(a)["placed_at"] is not None and s.already_bid("R1")
    assert s.conn.execute("SELECT mode FROM placements WHERE proposal_id=?", (a,)).fetchone()["mode"] == "live"


def test_decided_since_reads_only_recent_decisions(tmp_path):
    from datetime import datetime, timedelta, timezone
    s = Store(tmp_path / "b.sqlite"); a = _add(s); b = _add(s, "R2"); _add(s, "R3")
    s.decide(a, "approve", None, 1000, False)
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
    s.conn.execute("UPDATE proposals SET decided_at=? WHERE id=?", (old, a)); s.conn.commit()
    s.decide(b, "reject", "rust", None, False)
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    assert [r["id"] for r in s.decided_since(since)] == [b]


def test_a_judgement_written_between_the_read_and_the_write_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "b.sqlite"
    s = Store(path); a = _add(s)
    real = Store.telemetry

    def late_write(self, pid):  # another process updates the judgement after decide's checks, before its write
        other = sqlite3.connect(path)
        other.execute("UPDATE proposals SET rev=rev+1 WHERE id=?", (pid,)); other.commit(); other.close()
        return real(self, pid)

    monkeypatch.setattr(Store, "telemetry", late_write)
    with pytest.raises(Refused, match="changed since you opened it"):
        s.decide(a, "approve", None, 1000, False, seen_rev=0)
    assert s.proposal(a)["decision"] == "pending" and s.intent_for("R1") is None
    assert s.conn.execute("SELECT COUNT(*) FROM decisions WHERE proposal_id=?", (a,)).fetchone()[0] == 0


def test_a_dry_filter_counts_unreadable_placement_text_as_not_done(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    s.conn.execute("UPDATE proposals SET placement_json='not json' WHERE id=?", (a,)); s.conn.commit()
    assert [r["id"] for r in s.approved_unplaced(10, dry_run=True)] == [a]


def test_opening_the_store_does_not_wait_for_a_writer(tmp_path):
    path = tmp_path / "b.sqlite"
    s = Store(path); a = _add(s)
    s.decide(a, "approve", None, 1000, False)  # every approval holds its claim: the backfill has nothing to write
    s.close()
    writer = sqlite3.connect(str(path), isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")  # another process holds the write lock (a scan storing a lot)
    try:
        started = time.monotonic()
        s = Store(path)
        assert s.counts()["approved"] == 1
        s.close()
        assert time.monotonic() - started < 2  # WAL: a reader opens beside the writer, well inside the 5 s busy wait
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_a_vehicle_bid_on_is_not_approved_again(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s, guide=223); b = _add(s, guide=224)
    s.decide(a, "approve", None, 1000, False)
    s.record_placement(a, "R1", "GDH206", 1000, True, "ok", None, dry_run=False)
    with pytest.raises(Refused, match="This vehicle was bid on already. One bid per vehicle, so it is not approved again."):
        s.decide(b, "approve", None, 900, False)
    assert s.proposal(b)["decision"] == "pending"
    assert s.conn.execute("SELECT COUNT(*) FROM decisions WHERE proposal_id=?", (b,)).fetchone()[0] == 0
    s.decide(b, "reject", "bid on already", None, False)
    assert s.proposal(b)["decision"] == "rejected"


def test_a_lot_with_a_live_attempt_is_not_approved_again(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    s.reopen(a)
    s.record_placement(a, "R1", "GDH206", 1000, False, "error: x", None, dry_run=False)  # a live attempt landed anyway
    assert s.proposal(a)["placed_at"] and not s.already_bid("R1")
    with pytest.raises(Refused, match="This vehicle was bid on already."):
        s.decide(a, "approve", None, 1000, False)
    assert s.intent_for("R1") is None
    s.decide(a, "reject", "the attempt failed", None, False)
    assert s.proposal(a)["decision"] == "rejected"


def test_two_tabs_approving_the_same_lot_record_one_approval(tmp_path, monkeypatch):
    path = tmp_path / "b.sqlite"
    s = Store(path); a = _add(s)
    real = s.telemetry

    def other_tab_first(pid):  # the other tab's approval of this lot lands after this one's checks, before its write
        other = Store(path)
        other.decide(pid, "approve", None, 1000, False)
        other.close()
        return real(pid)

    monkeypatch.setattr(s, "telemetry", other_tab_first)
    with pytest.raises(Refused, match="This lot is already decided."):
        s.decide(a, "approve", None, 900, False)
    assert s.intent_for("R1")["proposal_id"] == a and s.intent_for("R1")["amount_jpy_k"] == 1000
    assert s.proposal(a)["approved_bid_jpy_k"] == 1000
    assert s.conn.execute("SELECT COUNT(*) FROM decisions WHERE proposal_id=?", (a,)).fetchone()[0] == 1


# proposals and decisions as main a1a903f (before phase 2) created them: no rev, no undone_at, no claims, no placements
_PRE_PHASE_2 = """
CREATE TABLE proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ref_no TEXT NOT NULL, guide_id INTEGER, model_code TEXT NOT NULL,
  snapshot TEXT NOT NULL, rule_updated_at TEXT, status TEXT NOT NULL, stage TEXT NOT NULL, bid_jpy_k INTEGER,
  base_jpy_k INTEGER, summary TEXT, facts_json TEXT, hard_json TEXT, soft_json TEXT, evidence_json TEXT,
  created_at TEXT NOT NULL, decision TEXT NOT NULL DEFAULT 'pending', decision_reason TEXT, decided_at TEXT,
  approved_bid_jpy_k INTEGER, placed_at TEXT, placement_json TEXT, run_id INTEGER
);
CREATE INDEX ix_proposals_ref ON proposals(ref_no);
CREATE TABLE decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER NOT NULL, ref_no TEXT NOT NULL, model_code TEXT NOT NULL,
  action TEXT NOT NULL, reason TEXT, amount_jpy_k INTEGER, make_rule INTEGER DEFAULT 0, at TEXT NOT NULL,
  opened_at TEXT, decide_seconds REAL, sheet_zoomed INTEGER DEFAULT 0, photos_clicked INTEGER DEFAULT 0,
  nichibo_opened INTEGER DEFAULT 0, amount_edited INTEGER DEFAULT 0, proposed_jpy_k INTEGER, via TEXT
);
CREATE TABLE bids (
  ref_no TEXT PRIMARY KEY, proposal_id INTEGER NOT NULL, model_code TEXT NOT NULL, bid_jpy_k INTEGER NOT NULL,
  placed_at TEXT NOT NULL, ok INTEGER NOT NULL, message TEXT, screenshot TEXT
);
"""


def test_a_database_from_before_phase_two_opens_with_its_approvals_claimed(tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(str(path))
    old.executescript(_PRE_PHASE_2)
    at = "2026-10-20T00:00:00+00:00"
    dry = '{"ok": true, "message": "dry run", "screenshot": null, "dry_run": true, "at": "2026-10-21T00:00:00+00:00"}'
    for ref, decision, placement in (("A1", "approved", None), ("P1", "pending", None), ("D1", "approved", dry)):
        old.execute(
            "INSERT INTO proposals(ref_no, guide_id, model_code, snapshot, status, stage, bid_jpy_k, summary, facts_json, "
            "hard_json, created_at, decision, decided_at, approved_bid_jpy_k, placement_json) "
            "VALUES(?,223,'GDH206','s.json','NEEDS_REVIEW','hard',1000,?,'{}','{}',?,?,?,?,?)",
            (ref, f"lot {ref}", at, decision, at if decision == "approved" else None,
             1000 if decision == "approved" else None, placement))
    for pid, ref in ((1, "A1"), (3, "D1")):
        old.execute("INSERT INTO decisions(proposal_id, ref_no, model_code, action, amount_jpy_k, at, via) "
                    "VALUES(?,?,'GDH206','approve',1000,?,'detail')", (pid, ref, at))
    old.commit(); old.close()
    s = Store(path)
    assert [r["rev"] for r in s.conn.execute("SELECT rev FROM proposals")] == [0, 0, 0]
    assert s.intent_for("A1")["proposal_id"] == 1 and s.intent_for("D1")["proposal_id"] == 3
    assert s.intent_for("P1") is None
    assert s.dry_done_count() == 1
    assert [r["id"] for r in s.approved_unplaced(10, dry_run=True)] == [1]
    assert [r["id"] for r in s.approved_unplaced(10)] == [1, 3]
    assert sorted(d["proposal_id"] for d in s.decisions_with_facts()) == [1, 3]
    assert s.conn.execute("SELECT COUNT(*) FROM placements").fetchone()[0] == 0


def test_a_dry_result_written_before_phase_two_counts_as_done(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    s.conn.execute("UPDATE proposals SET placement_json=? WHERE id=?", (
        '{"ok": true, "message": "dry run", "screenshot": null, "dry_run": true, "at": "2026-10-01T00:00:00+00:00"}', a))
    s.conn.commit()
    assert s.approved_unplaced(10, dry_run=True) == [] and s.dry_done_count() == 1
