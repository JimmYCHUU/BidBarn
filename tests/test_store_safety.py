"""The store's own safety: one claim per vehicle, a pending check inside decide, and Reopen."""
import sqlite3

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
    b = _add(s, "R2"); s.decide(b, "approve", None, 1000, False)
    n = _add(s, "R2", guide=223)
    with pytest.raises(Refused) as exc:
        s.reopen(b)
    assert f"lot {n}" in str(exc.value)
    with pytest.raises(KeyError):
        s.reopen(9999)


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


def test_a_dry_result_written_before_phase_two_counts_as_done(tmp_path):
    s = Store(tmp_path / "b.sqlite"); a = _add(s)
    s.decide(a, "approve", None, 1000, False)
    s.conn.execute("UPDATE proposals SET placement_json=? WHERE id=?", (
        '{"ok": true, "message": "dry run", "screenshot": null, "dry_run": true, "at": "2026-10-01T00:00:00+00:00"}', a))
    s.conn.commit()
    assert s.approved_unplaced(10, dry_run=True) == [] and s.dry_done_count() == 1
