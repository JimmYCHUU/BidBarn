"""SQLite persistence: vehicles seen, proposals, human decisions, placed bids."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
  ref_no TEXT PRIMARY KEY,
  model_code TEXT,
  facts_json TEXT,
  first_seen TEXT,
  last_seen TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ref_no TEXT NOT NULL,
  guide_id INTEGER,                -- Carbarn bidding guide; model codes can be shared by two guides
  model_code TEXT NOT NULL,
  snapshot TEXT NOT NULL,
  rule_updated_at TEXT,
  status TEXT NOT NULL,            -- BID | SKIP | NEEDS_REVIEW
  stage TEXT NOT NULL,             -- card | hard | soft
  bid_jpy_k INTEGER,
  base_jpy_k INTEGER,
  summary TEXT,
  facts_json TEXT,
  hard_json TEXT,
  soft_json TEXT,
  evidence_json TEXT,
  created_at TEXT NOT NULL,
  decision TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected | superseded | deleted (out of the review, no decision)
  decision_reason TEXT,
  decided_at TEXT,
  approved_bid_jpy_k INTEGER,
  placed_at TEXT,
  placement_json TEXT,
  run_id INTEGER                   -- the dashboard run that wrote it (BIDBARN_RUN_ID); NULL from a console
);
CREATE INDEX IF NOT EXISTS ix_proposals_ref ON proposals(ref_no);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  proposal_id INTEGER NOT NULL,
  ref_no TEXT NOT NULL,
  model_code TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT,
  amount_jpy_k INTEGER,
  make_rule INTEGER DEFAULT 0,
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bids (
  ref_no TEXT PRIMARY KEY,
  proposal_id INTEGER NOT NULL,
  model_code TEXT NOT NULL,
  bid_jpy_k INTEGER NOT NULL,
  placed_at TEXT NOT NULL,
  ok INTEGER NOT NULL,
  message TEXT,
  screenshot TEXT
);
CREATE TABLE IF NOT EXISTS bid_intents (
  ref_no TEXT PRIMARY KEY,         -- one claim per vehicle: the proposal approved for it
  proposal_id INTEGER NOT NULL UNIQUE,
  amount_jpy_k INTEGER NOT NULL,
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  proposal_id INTEGER NOT NULL,
  ref_no TEXT,
  model_code TEXT,
  event TEXT NOT NULL,             -- open | sheet_zoom | photos_click | nichibo_click | amount_edit | decide
  value TEXT,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_review_events_pid ON review_events(proposal_id);
CREATE TABLE IF NOT EXISTS manual_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session TEXT NOT NULL,           -- HAR file stem and start time; the HAR itself is never copied
  ref_no TEXT NOT NULL,
  action TEXT NOT NULL,            -- open | bid
  bid_jpy INTEGER,
  bid_jpy_k INTEGER,
  at TEXT NOT NULL,
  UNIQUE(session, ref_no, action, at)
);
CREATE TABLE IF NOT EXISTS model_calls (
  provider TEXT NOT NULL,          -- free vision providers: calls per day, so quotas survive restarts
  day TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  tokens INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(provider, day)
);
CREATE TABLE IF NOT EXISTS judge_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  proposal_id INTEGER NOT NULL,
  ref_no TEXT,
  model_code TEXT,
  requirement_id TEXT,
  model_said TEXT,
  jim_said TEXT,
  note TEXT,
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command TEXT NOT NULL,
  args_json TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'dashboard',
  pid INTEGER,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  exit_code INTEGER,
  error TEXT,
  progress_json TEXT,
  summary_json TEXT,
  log_path TEXT,
  cancel_requested_at TEXT,
  session_id INTEGER,              -- the first run of its session: its own id, or the paused session it resumed
  pause_requested_at TEXT,
  deleted_at TEXT                  -- Delete session took the session's waiting lots out of the review
);
CREATE INDEX IF NOT EXISTS ix_runs_command ON runs(command, id);
"""

# A run started from the dashboard: running until its process ends, then one of the others. `paused` is a
# Bid run ended by Pause; Resume starts the next run in the same session (`runs.session_id`).
RUN_STATUSES = ("running", "ok", "failed", "stopped", "paused")
RUN_COLUMNS = {"session_id": "INTEGER", "pause_requested_at": "TEXT", "deleted_at": "TEXT"}
# A lot waiting for a person: what the review lists, and all that Delete may take out of it.
WAITING = "decision='pending' AND status IN ('BID','NEEDS_REVIEW')"

# What the reviewer did on the page, summarised per decision (added to `decisions` on first run).
DECISION_COLUMNS = {
    "opened_at": "TEXT", "decide_seconds": "REAL", "sheet_zoomed": "INTEGER DEFAULT 0",
    "photos_clicked": "INTEGER DEFAULT 0", "nichibo_opened": "INTEGER DEFAULT 0", "amount_edited": "INTEGER DEFAULT 0",
    "proposed_jpy_k": "INTEGER", "via": "TEXT",
    "undone_at": "TEXT",  # Reopen undid this decision: it stays in the day log but is not learned from
}
EVENTS = ("open", "sheet_zoom", "photos_click", "nichibo_click", "amount_edit", "decide")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Refused(Exception):
    """A decision the store will not record. `str(exc)` is a full sentence for the page."""


class VehicleClaimed(Refused):
    """Approving a vehicle that another proposal already holds the claim on."""
    def __init__(self, ref_no: str, holder: int):
        self.ref_no, self.holder = ref_no, holder
        super().__init__(f"This vehicle is already approved (lot {holder}). One bid per vehicle, so it is not approved again.")


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # The dashboard reads while a run writes: wait for a lock instead of failing, and let
        # readers and the writer work side by side (WAL). A database that cannot switch (in
        # memory, read only, busy at this moment) keeps the journal it has.
        self.conn.execute("PRAGMA busy_timeout=5000")
        try:
            self.conn.execute("PRAGMA journal_mode=WAL").fetchall()
        except sqlite3.Error:
            pass
        self.conn.executescript(SCHEMA)
        self._ensure_columns("proposals", {"guide_id": "INTEGER", "run_id": "INTEGER"})  # databases created before these
        self._ensure_columns("runs", RUN_COLUMNS)
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_proposals_run ON proposals(run_id)")  # after the column exists
        self.conn.commit()
        self._ensure_columns("decisions", DECISION_COLUMNS)
        self._ensure_columns("manual_actions", {"note": "TEXT"})  # typed reasons from watch sessions
        # Approvals made before the claim existed: the oldest approved lot of a vehicle holds it. Idempotent.
        # (A stub table without the decision columns has no approvals to claim.)
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(proposals)")}
        if {"approved_bid_jpy_k", "bid_jpy_k", "decided_at"} <= have:
            self.conn.execute(
                "INSERT OR IGNORE INTO bid_intents(ref_no, proposal_id, amount_jpy_k, at) "
                "SELECT ref_no, id, COALESCE(approved_bid_jpy_k, bid_jpy_k, 0), COALESCE(decided_at, created_at) "
                "FROM proposals WHERE decision='approved' ORDER BY id")
            self.conn.commit()

    def _ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        """Add the columns a newer version needs to a database created by an older one. Never drops anything."""
        have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in have:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- vehicles ---------------------------------------------------------
    def upsert_vehicle(self, ref_no: str, model_code: str, facts: dict) -> None:
        ts = now()
        self.conn.execute(
            "INSERT INTO vehicles(ref_no, model_code, facts_json, first_seen, last_seen) VALUES(?,?,?,?,?) "
            "ON CONFLICT(ref_no) DO UPDATE SET facts_json=excluded.facts_json, last_seen=excluded.last_seen, model_code=excluded.model_code",
            (ref_no, model_code, json.dumps(facts, ensure_ascii=False), ts, ts))
        self.conn.commit()

    def vehicle_facts(self, ref_no: str) -> dict:
        row = self.conn.execute("SELECT facts_json FROM vehicles WHERE ref_no=?", (ref_no,)).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row["facts_json"] or "{}")
        except json.JSONDecodeError:
            return {}

    def vehicles_by_vid(self) -> dict[str, str]:
        """Nichibo's numeric vid -> ref_no, for joins against captures that carry the vid."""
        out: dict[str, str] = {}
        for row in self.conn.execute("SELECT ref_no, facts_json FROM vehicles"):
            try:
                vid = json.loads(row["facts_json"] or "{}").get("vid")
            except json.JSONDecodeError:
                vid = None
            if vid:
                out[str(vid)] = row["ref_no"]
        return out

    # -- manual sessions (bot learn --har) -------------------------------------
    def add_manual_actions(self, session: str, actions: list[tuple]) -> int:
        """actions: (ref_no, action, bid_jpy, bid_jpy_k, at[, note]). Re-running the same session adds nothing."""
        rows = [(session, *a, *([None] * (6 - len(a)))) for a in actions]
        cur = self.conn.executemany(
            "INSERT OR IGNORE INTO manual_actions(session, ref_no, action, bid_jpy, bid_jpy_k, at, note) VALUES(?,?,?,?,?,?,?)",
            rows)
        self.conn.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0

    # -- model quota and feedback (judgement layer) ------------------------------
    def model_calls_today(self, provider: str, day: str | None = None) -> int:
        day = day or datetime.now().strftime("%Y-%m-%d")
        row = self.conn.execute("SELECT calls FROM model_calls WHERE provider=? AND day=?", (provider, day)).fetchone()
        return int(row["calls"]) if row else 0

    def record_model_call(self, provider: str, tokens: int = 0, day: str | None = None) -> None:
        day = day or datetime.now().strftime("%Y-%m-%d")
        self.conn.execute(
            "INSERT INTO model_calls(provider, day, calls, tokens) VALUES(?,?,1,?) "
            "ON CONFLICT(provider, day) DO UPDATE SET calls=calls+1, tokens=tokens+excluded.tokens",
            (provider, day, int(tokens or 0)))
        self.conn.commit()

    def manual_notes(self, model_code: str, limit: int = 15) -> list[str]:
        """Comments Jim typed in learn mode about lots of this model code, newest first, with the lot in brief."""
        rows = self.conn.execute(
            "SELECT m.note, v.facts_json FROM manual_actions m JOIN vehicles v ON v.ref_no=m.ref_no "
            "WHERE m.action='reason' AND m.note IS NOT NULL AND m.note<>'' AND v.model_code=? ORDER BY m.id DESC LIMIT ?",
            (model_code, limit)).fetchall()
        out = []
        for r in rows:
            try:
                f = json.loads(r["facts_json"] or "{}")
            except json.JSONDecodeError:
                f = {}
            lot = " ".join(str(x) for x in (f.get("year"), f.get("variant_text")) if x)
            km = f"{f['km']:,} km" if f.get("km") else ""
            grade = f"grade {f['grade_raw']}" if f.get("grade_raw") else ""
            brief = ", ".join(x for x in (lot, km, grade) if x)
            out.append(f"{r['note']}" + (f" [{brief}]" if brief else ""))
        return out

    def add_judge_feedback(self, pid: int, requirement_id: str | None, model_said: str | None, jim_said: str | None,
                           note: str | None) -> None:
        p = self.proposal(pid)
        if p is None:
            raise KeyError(pid)
        self.conn.execute(
            "INSERT INTO judge_feedback(proposal_id, ref_no, model_code, requirement_id, model_said, jim_said, note, at) "
            "VALUES(?,?,?,?,?,?,?,?)", (pid, p["ref_no"], p["model_code"], requirement_id, model_said, jim_said, note, now()))
        self.conn.commit()

    def judge_feedback(self, model_code: str | None = None, limit: int = 200) -> list[sqlite3.Row]:
        q = "SELECT f.*, p.summary FROM judge_feedback f JOIN proposals p ON p.id=f.proposal_id"
        args: list = []
        if model_code:
            q += " WHERE f.model_code=?"; args.append(model_code)
        q += " ORDER BY f.id DESC LIMIT ?"; args.append(limit)
        return self.conn.execute(q, args).fetchall()

    # -- proposals --------------------------------------------------------
    def latest_proposal(self, ref_no: str, guide_id: int | None = None) -> sqlite3.Row | None:
        if guide_id is None:
            return self.conn.execute(
                "SELECT * FROM proposals WHERE ref_no=? ORDER BY id DESC LIMIT 1", (ref_no,)).fetchone()
        return self.conn.execute(
            "SELECT * FROM proposals WHERE ref_no=? AND guide_id=? ORDER BY id DESC LIMIT 1", (ref_no, guide_id)).fetchone()

    def add_proposal(self, **row) -> int:
        row.setdefault("created_at", now())
        for k in ("facts_json", "hard_json", "soft_json", "evidence_json"):
            if k in row and not isinstance(row[k], (str, type(None))):
                row[k] = json.dumps(row[k], ensure_ascii=False, default=str)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cur = self.conn.execute(f"INSERT INTO proposals({cols}) VALUES({marks})", tuple(row.values()))
        self.conn.commit()
        return int(cur.lastrowid)

    def proposal(self, pid: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()

    def list_proposals(self, decision: str | None = "pending", status: str | None = None,
                       model_code: str | None = None, limit: int = 500) -> list[sqlite3.Row]:
        q = "SELECT * FROM proposals WHERE 1=1"
        args: list = []
        if decision:
            q += " AND decision=?"; args.append(decision)
        if status:
            q += " AND status=?"; args.append(status)
        if model_code:
            q += " AND model_code=?"; args.append(model_code)
        q += " ORDER BY CASE status WHEN 'BID' THEN 0 WHEN 'NEEDS_REVIEW' THEN 1 ELSE 2 END, model_code, id DESC LIMIT ?"
        args.append(limit)
        return self.conn.execute(q, args).fetchall()

    def intent_for(self, ref_no: str) -> sqlite3.Row | None:
        """The claim on a vehicle: the proposal approved for it, if any."""
        return self.conn.execute("SELECT * FROM bid_intents WHERE ref_no=?", (ref_no,)).fetchone()

    def _newer_than(self, p: sqlite3.Row) -> sqlite3.Row | None:
        """A later proposal for the same vehicle and guide, if there is one."""
        latest = self.latest_proposal(p["ref_no"], p["guide_id"])
        return latest if latest is not None and latest["id"] != p["id"] else None

    def not_pending_reason(self, row: sqlite3.Row) -> str | None:
        """None for a lot that waits for a decision; otherwise the sentence `decide` refuses with."""
        d = row["decision"]
        if d == "pending":
            return None
        if d in ("approved", "rejected"):
            return "This lot is already decided."
        if d == "deleted":
            return "This lot was cleared from the review."
        newer = self._newer_than(row)
        if newer is not None:
            return f"A newer scan replaced this lot. Decide the new one (lot {newer['id']})."
        return "A newer scan replaced this lot."

    def reopen(self, pid: int) -> None:
        """Return an approved or rejected lot that was not placed live to waiting, and free its vehicle's claim.
        Never approves anything. The undone decision stays in `decisions` (marked) for the day log."""
        p = self.proposal(pid)
        if p is None:
            raise KeyError(pid)
        if p["decision"] not in ("approved", "rejected"):
            raise Refused("Only an approved or rejected lot can be reopened; this one is waiting or out of the review.")
        if p["placed_at"] or self.already_bid(p["ref_no"]):
            raise Refused("This lot was placed live, so it cannot be reopened.")
        newer = self._newer_than(p)
        if newer is not None:
            raise Refused(f"A newer scan replaced this lot. Decide the new one (lot {newer['id']}).")
        self.conn.execute(
            "UPDATE proposals SET decision='pending', decision_reason=NULL, decided_at=NULL, approved_bid_jpy_k=NULL, "
            "placement_json=NULL WHERE id=?", (pid,))
        self.conn.execute("DELETE FROM bid_intents WHERE proposal_id=?", (pid,))
        self.conn.execute(
            "UPDATE decisions SET undone_at=? WHERE id=(SELECT MAX(id) FROM decisions WHERE proposal_id=? AND undone_at IS NULL)",
            (now(), pid))
        self.conn.commit()

    def decide(self, pid: int, action: str, reason: str | None, amount_jpy_k: int | None, make_rule: bool,
               via: str = "detail") -> None:
        """Record the human decision. The only caller is the review page (CLAUDE.md, rule 1). Refuses a lot that is
        no longer pending, and an approval of a vehicle another proposal already holds the claim on."""
        p = self.proposal(pid)
        if p is None:
            raise KeyError(pid)
        why = self.not_pending_reason(p)
        if why:
            raise Refused(why)
        decision = "approved" if action == "approve" else "rejected"
        ts = now()
        tele = self.telemetry(pid)
        seconds = None
        if tele.get("opened_at"):
            seconds = round((datetime.fromisoformat(ts) - datetime.fromisoformat(tele["opened_at"])).total_seconds(), 1)
        edited = int(decision == "approved" and amount_jpy_k is not None and amount_jpy_k != p["bid_jpy_k"])
        if decision == "approved":
            claim = amount_jpy_k if amount_jpy_k is not None else (p["bid_jpy_k"] or 0)
            try:
                self.conn.execute("INSERT INTO bid_intents(ref_no, proposal_id, amount_jpy_k, at) VALUES(?,?,?,?)",
                                  (p["ref_no"], pid, claim, ts))
            except sqlite3.IntegrityError:
                self.conn.rollback()
                held = self.intent_for(p["ref_no"])
                if held is None or held["proposal_id"] != pid:
                    raise VehicleClaimed(p["ref_no"], held["proposal_id"] if held else 0)
                # a claim already in this proposal's own name (a legacy backfill): this approval takes it over
                self.conn.execute("UPDATE bid_intents SET amount_jpy_k=?, at=? WHERE proposal_id=?", (claim, ts, pid))
        cur = self.conn.execute(
            "UPDATE proposals SET decision=?, decision_reason=?, decided_at=?, approved_bid_jpy_k=? "
            "WHERE id=? AND decision='pending'",
            (decision, reason, ts, amount_jpy_k if decision == "approved" else None, pid))
        if cur.rowcount != 1:  # another tab decided it first
            self.conn.rollback()
            raise Refused("This lot is already decided.")
        self.conn.execute(
            "INSERT INTO decisions(proposal_id, ref_no, model_code, action, reason, amount_jpy_k, make_rule, at, "
            "opened_at, decide_seconds, sheet_zoomed, photos_clicked, nichibo_opened, amount_edited, proposed_jpy_k, via) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, p["ref_no"], p["model_code"], action, reason, amount_jpy_k, int(make_rule), ts,
             tele.get("opened_at"), seconds, int(tele.get("sheet_zoomed", 0)), int(tele.get("photos_clicked", 0)),
             int(tele.get("nichibo_opened", 0)), edited, p["bid_jpy_k"], via))
        self.conn.commit()
        self.log_event(pid, "decide", decision)

    # -- review telemetry ------------------------------------------------------
    def log_event(self, pid: int, event: str, value: str | None = None) -> None:
        if event not in EVENTS:
            raise ValueError(f"unknown review event {event!r}")
        p = self.proposal(pid)
        if p is None:
            raise KeyError(pid)
        self.conn.execute(
            "INSERT INTO review_events(proposal_id, ref_no, model_code, event, value, at) VALUES(?,?,?,?,?,?)",
            (pid, p["ref_no"], p["model_code"], event, value, now()))
        self.conn.commit()

    def events(self, pid: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM review_events WHERE proposal_id=? ORDER BY id", (pid,)).fetchall()

    def telemetry(self, pid: int) -> dict:
        """What the reviewer did since the proposal was last opened."""
        rows = self.events(pid)
        opens = [i for i, r in enumerate(rows) if r["event"] == "open"]
        rows = rows[opens[-1]:] if opens else []
        out = {"opened_at": rows[0]["at"] if rows else None, "sheet_zoomed": 0, "photos_clicked": 0,
               "nichibo_opened": 0, "amount_edited": 0, "last_amount": None}
        for r in rows:
            if r["event"] == "sheet_zoom":
                out["sheet_zoomed"] = 1
            elif r["event"] == "photos_click":
                out["photos_clicked"] = 1
            elif r["event"] == "nichibo_click":
                out["nichibo_opened"] = 1
            elif r["event"] == "amount_edit":
                out["amount_edited"] = 1
                out["last_amount"] = r["value"]
        return out

    def decision_for(self, pid: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM decisions WHERE proposal_id=? AND undone_at IS NULL ORDER BY id DESC LIMIT 1", (pid,)).fetchone()

    def decisions_with_facts(self, model_code: str | None = None, since: str | None = None) -> list[dict]:
        """Every human decision joined with the proposal it was made on (facts parsed)."""
        q = ("SELECT d.*, p.summary, p.facts_json, p.hard_json, p.bid_jpy_k AS bid_jpy_k, p.guide_id, p.status "
             "FROM decisions d JOIN proposals p ON p.id=d.proposal_id WHERE d.undone_at IS NULL")
        args: list = []
        if model_code:
            q += " AND d.model_code=?"; args.append(model_code)
        if since:
            q += " AND d.at>=?"; args.append(since)
        q += " ORDER BY d.id DESC"
        out = []
        for r in self.conn.execute(q, args):
            d = dict(r)
            try:
                d["facts"] = json.loads(d.pop("facts_json") or "{}")
            except json.JSONDecodeError:
                d["facts"] = {}
            try:
                d["hard"] = json.loads(d.pop("hard_json") or "{}")
            except json.JSONDecodeError:
                d["hard"] = {}
            out.append(d)
        return out

    def list_triage(self, model_code: str | None = None) -> list[sqlite3.Row]:
        """Pending proposals a person still has to decide, each with the session of the run that wrote it
        (`session_id`, None when no dashboard run did: "Earlier scans")."""
        q = ("SELECT p.*, r.session_id AS session_id FROM proposals p LEFT JOIN runs r ON r.id = p.run_id "
             "WHERE p.decision='pending' AND p.status IN ('BID','NEEDS_REVIEW')")
        args: list = []
        if model_code:
            q += " AND p.model_code=?"; args.append(model_code)
        return self.conn.execute(q + " ORDER BY p.model_code, p.id DESC", args).fetchall()

    def update_soft(self, pid: int, soft: dict, facts: dict | None = None, evidence: dict | None = None) -> None:
        """Store a (new) judgement on an existing pending proposal without superseding it."""
        sets, args = ["soft_json=?"], [json.dumps(soft, ensure_ascii=False, default=str)]
        if facts is not None:
            sets.append("facts_json=?"); args.append(json.dumps(facts, ensure_ascii=False, default=str))
        if evidence is not None:
            sets.append("evidence_json=?"); args.append(json.dumps(evidence, ensure_ascii=False, default=str))
        args.append(pid)
        self.conn.execute(f"UPDATE proposals SET {', '.join(sets)} WHERE id=?", args)
        self.conn.commit()

    def set_auction_day(self, pid: int, date: str, hall: str) -> None:
        """Add the auction day and hall (and `auction_day_read`) to one proposal's stored facts; nothing else changes."""
        row = self.conn.execute("SELECT facts_json FROM proposals WHERE id=?", (pid,)).fetchone()
        if row is None:
            return
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except json.JSONDecodeError:
            return
        if not isinstance(facts, dict):
            return
        facts["auction_date"], facts["auction_place"], facts["auction_day_read"] = date, hall, True
        self.conn.execute("UPDATE proposals SET facts_json=? WHERE id=?",
                          (json.dumps(facts, ensure_ascii=False, default=str), pid))
        self.conn.commit()

    def known_day(self, ref_no: str, on_or_after: str) -> dict | None:
        """The auction day the newest earlier proposal for this vehicle (any guide) holds, if it has not passed
        (YYYYMMDD on or after `on_or_after`). A lot number seen again after its auction may be a new listing, so a
        past day is not carried and the page is read again (Jim, 2026-10-26). A page read with no date is not carried."""
        for r in self.conn.execute("SELECT facts_json FROM proposals WHERE ref_no=? ORDER BY id DESC", (ref_no,)):
            try:
                f = json.loads(r["facts_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(f, dict) or not (f.get("auction_date") or f.get("auction_day_read")):
                continue  # this row never had its day read: look further back
            day = str(f.get("auction_date") or "")
            if len(day) != 8 or not day.isdigit() or day < on_or_after:
                return None  # the newest read found no date, an odd one, or a day that has passed: read again
            return {"auction_date": day, "auction_place": f.get("auction_place") or "", "auction_day_read": True}
        return None

    def supersede_pending(self, ref_no: str, guide_id: int | None) -> int:
        """Retire earlier pending proposals for this vehicle+guide before a fresh evaluation is stored."""
        cur = self.conn.execute(
            "UPDATE proposals SET decision='superseded', decided_at=? WHERE ref_no=? AND decision='pending'"
            + (" AND guide_id=?" if guide_id is not None else ""),
            (now(), ref_no, guide_id) if guide_id is not None else (now(), ref_no))
        self.conn.commit()
        return cur.rowcount

    # -- out of the review without a decision (Delete) -------------------------------------
    def delete_lot(self, pid: int) -> bool:
        """Take one lot that waits for review out of it. Not a decision: nothing goes to `decisions`, so nothing
        is learned from it, and the next session's scan checks the vehicle again. The row stays (rule 0c)."""
        cur = self.conn.execute(f"UPDATE proposals SET decision='deleted', decided_at=?, decision_reason=? WHERE id=? AND {WAITING}",
                                (now(), "deleted from the review", pid))
        self.conn.commit()
        return cur.rowcount == 1

    def delete_session(self, session_id: int | None) -> int:
        """Every lot of a session that still waits for review, out of it, and the session marked deleted.
        `None` is the lots no dashboard run wrote ("Earlier scans"). Approved and rejected lots keep their
        decision. Returns how many lots left the review."""
        ts = now()
        if session_id is None:
            which, args = "(SELECT session_id FROM runs WHERE runs.id = proposals.run_id) IS NULL", ()
        else:
            which, args = "run_id IN (SELECT id FROM runs WHERE session_id=?)", (session_id,)
            self.conn.execute("UPDATE runs SET deleted_at=COALESCE(deleted_at, ?) WHERE session_id=?", (ts, session_id))
        cur = self.conn.execute(
            f"UPDATE proposals SET decision='deleted', decided_at=?, decision_reason=? WHERE {WAITING} AND {which}",
            (ts, "session deleted from the review", *args))
        self.conn.commit()
        return cur.rowcount

    def sweep_deleted_sessions(self) -> int:
        """Lots still waiting that a run wrote between Delete session and its own end, when this dashboard lost
        track of it. Only those: a lot `bot replay` brought into the review later keeps waiting. Called when the
        dashboard starts."""
        cur = self.conn.execute(
            f"UPDATE proposals SET decision='deleted', decided_at=?, decision_reason=? WHERE {WAITING} AND EXISTS "
            "(SELECT 1 FROM runs r WHERE r.id = proposals.run_id AND r.deleted_at IS NOT NULL "
            "AND proposals.created_at >= r.deleted_at AND (r.finished_at IS NULL OR proposals.created_at <= r.finished_at))",
            (now(), "session deleted from the review"))
        self.conn.commit()
        return cur.rowcount

    def approved_unplaced(self, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM proposals WHERE decision='approved' AND placed_at IS NULL ORDER BY id LIMIT ?", (limit,)).fetchall()

    def placed_since(self, since: str) -> list[sqlite3.Row]:
        """Proposals placed live at or after `since` (UTC ISO text as `now()` writes it), newest first. Read-only:
        the bid sheet's "Placed" section."""
        return self.conn.execute(
            "SELECT * FROM proposals WHERE placed_at IS NOT NULL AND placed_at >= ? ORDER BY placed_at DESC, id DESC",
            (since,)).fetchall()

    def record_placement(self, pid: int, ref_no: str, model_code: str, bid_jpy_k: int, ok: bool,
                         message: str, screenshot: str | None, dry_run: bool) -> None:
        ts = now()
        payload = json.dumps({"ok": ok, "message": message, "screenshot": screenshot, "dry_run": dry_run, "at": ts})
        if dry_run:
            self.conn.execute("UPDATE proposals SET placement_json=? WHERE id=?", (payload, pid))
        else:
            self.conn.execute("UPDATE proposals SET placed_at=?, placement_json=? WHERE id=?", (ts, payload, pid))
            if ok:
                self.conn.execute(
                    "INSERT INTO bids(ref_no, proposal_id, model_code, bid_jpy_k, placed_at, ok, message, screenshot) VALUES(?,?,?,?,?,?,?,?)",
                    (ref_no, pid, model_code, bid_jpy_k, ts, 1, message, screenshot))
        self.conn.commit()

    def already_bid(self, ref_no: str) -> bool:
        return self.conn.execute("SELECT 1 FROM bids WHERE ref_no=?", (ref_no,)).fetchone() is not None

    def past_decisions(self, model_code: str, limit: int = 8) -> list[dict]:
        rows = self.conn.execute(
            "SELECT d.action, d.reason, p.summary FROM decisions d JOIN proposals p ON p.id=d.proposal_id "
            "WHERE d.undone_at IS NULL AND d.model_code=? AND d.reason IS NOT NULL AND d.reason<>'' ORDER BY d.id DESC LIMIT ?",
            (model_code, limit)).fetchall()
        return [{"decision": r["action"], "reason": r["reason"], "summary": r["summary"]} for r in rows]

    def siblings(self, ref_no: str, exclude_id: int) -> list[sqlite3.Row]:
        """Other live proposals for the same vehicle (same code shared by two guides, e.g. Serena / Serena Campervan)."""
        return self.conn.execute(
            "SELECT id, guide_id, model_code, status, bid_jpy_k, decision FROM proposals "
            "WHERE ref_no=? AND id<>? AND decision IN ('pending','approved') ORDER BY id", (ref_no, exclude_id)).fetchall()

    def multi_guide_refs(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT ref_no FROM proposals WHERE decision IN ('pending','approved') GROUP BY ref_no HAVING COUNT(DISTINCT guide_id) > 1").fetchall()
        return {r["ref_no"] for r in rows}

    def counts(self) -> dict:
        row = self.conn.execute(
            "SELECT SUM(decision='pending' AND status='BID') AS bid, SUM(decision='pending' AND status='NEEDS_REVIEW') AS review, "
            "SUM(decision='approved' AND placed_at IS NULL) AS approved, (SELECT COUNT(*) FROM bids) AS placed FROM proposals").fetchone()
        return {k: (row[k] or 0) for k in row.keys()}

    def recent_decisions(self, limit: int = 10) -> list[dict]:
        """The latest human decisions, newest first, with the lot in brief (read only, for the overview)."""
        rows = self.conn.execute(
            "SELECT d.action, d.amount_jpy_k AS amount, d.reason, d.ref_no, d.model_code, d.at, d.proposal_id, p.summary "
            "FROM decisions d LEFT JOIN proposals p ON p.id=d.proposal_id WHERE d.undone_at IS NULL ORDER BY d.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # -- runs (commands started from the dashboard) ------------------------------
    def start_run(self, command: str, args: list[str], pid: int | None = None, source: str = "dashboard",
                  session_id: int | None = None) -> int:
        """A new running run. It starts its own session unless `session_id` names the paused one it resumes."""
        cur = self.conn.execute(
            "INSERT INTO runs(command, args_json, source, pid, started_at, status) VALUES(?,?,?,?,?,'running')",
            (command, json.dumps(list(args), ensure_ascii=False), source, pid, now()))
        run_id = int(cur.lastrowid)
        self.conn.execute("UPDATE runs SET session_id=? WHERE id=?", (session_id or run_id, run_id))
        self.conn.commit()
        return run_id

    def set_run_pid(self, run_id: int, pid: int | None) -> None:
        self.conn.execute("UPDATE runs SET pid=? WHERE id=?", (pid, run_id))
        self.conn.commit()

    def set_run_log(self, run_id: int, path: Path | str) -> None:
        self.conn.execute("UPDATE runs SET log_path=? WHERE id=?", (str(path), run_id))
        self.conn.commit()

    def update_run_progress(self, run_id: int, progress: dict) -> None:
        self.conn.execute("UPDATE runs SET progress_json=? WHERE id=?",
                          (json.dumps(progress, ensure_ascii=False, default=str), run_id))
        self.conn.commit()

    def request_run_cancel(self, run_id: int) -> None:
        """Note when Stop was clicked (the first click counts). The run itself ends through `finish_run`."""
        self.conn.execute("UPDATE runs SET cancel_requested_at=COALESCE(cancel_requested_at, ?) WHERE id=? AND status='running'",
                          (now(), run_id))
        self.conn.commit()

    def request_run_pause(self, run_id: int) -> None:
        """Note when Pause was clicked (the first click counts). The run itself ends through `finish_run`."""
        self.conn.execute("UPDATE runs SET pause_requested_at=COALESCE(pause_requested_at, ?) WHERE id=? AND status='running'",
                          (now(), run_id))
        self.conn.commit()

    def finish_run(self, run_id: int, status: str, exit_code: int | None = None, summary: dict | None = None,
                   error: str | None = None) -> bool:
        """Close a running run as ok, failed, stopped or paused. A run that is already closed is left as it is."""
        if status not in RUN_STATUSES or status == "running":
            raise ValueError(f"a run finishes as ok, failed, stopped or paused, not {status!r}")
        cur = self.conn.execute(
            "UPDATE runs SET status=?, finished_at=?, exit_code=?, error=?, summary_json=COALESCE(?, summary_json) "
            "WHERE id=? AND status='running'",
            (status, now(), exit_code, error, None if summary is None else json.dumps(summary, ensure_ascii=False, default=str),
             run_id))
        self.conn.commit()
        return cur.rowcount > 0

    def run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

    def list_runs(self, limit: int = 50, command: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM runs"
        args: list = []
        if command:
            q += " WHERE command=?"; args.append(command)
        q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
        return self.conn.execute(q, args).fetchall()

    def last_runs_by_command(self, status: str | None = None) -> dict[str, sqlite3.Row]:
        """The newest run of every command; with `status`, the newest one that ended that way."""
        q = "SELECT * FROM runs WHERE id IN (SELECT MAX(id) FROM runs" + (" WHERE status=?" if status else "") + " GROUP BY command)"
        rows = self.conn.execute(q, (status,) if status else ()).fetchall()
        return {r["command"]: r for r in rows}

    def running_runs(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM runs WHERE status='running' ORDER BY id").fetchall()

    # -- sessions: a Bid run and the runs that resumed it ----------------------------------
    def paused_session(self) -> sqlite3.Row | None:
        """The newest run of the paused session, or None: a session whose newest run ended paused and that
        was not deleted. Its `session_id` names the session; Resume starts the next run in it."""
        return self.conn.execute(
            "SELECT * FROM runs r WHERE status='paused' AND deleted_at IS NULL AND session_id IS NOT NULL "
            "AND id=(SELECT MAX(id) FROM runs WHERE session_id=r.session_id) ORDER BY id DESC LIMIT 1").fetchone()

    def session_info(self, session_id: int) -> dict | None:
        """A session as the pages name it: its first run (command, start) and its newest (id, status, progress,
        deleted). None when no run has that session."""
        rows = self.conn.execute("SELECT * FROM runs WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
        if not rows:
            return None
        first, last = rows[0], rows[-1]
        try:
            prog = json.loads(last["progress_json"]) if last["progress_json"] else None
        except json.JSONDecodeError:
            prog = None
        return {"id": session_id, "command": first["command"], "started_at": first["started_at"], "run_id": last["id"],
                "status": last["status"], "progress": prog if isinstance(prog, dict) else None,
                "deleted_at": last["deleted_at"], "runs": [r["id"] for r in rows]}

    def session_runs(self, run_id: int | None) -> set[int]:
        """The ids of every run in the session `run_id` belongs to: a lot deleted by one of them stays out of the
        review for the rest of that session. Empty from a console, or for a run from before sessions existed."""
        if run_id is None:
            return set()
        rows = self.conn.execute("SELECT id FROM runs WHERE session_id=(SELECT session_id FROM runs WHERE id=?)",
                                 (run_id,)).fetchall()
        return {r["id"] for r in rows}

    def session_waiting(self, session_id: int | None) -> int:
        """How many lots of the session wait for review (`None`: the lots no dashboard run wrote, "Earlier scans")."""
        if session_id is None:
            return self.conn.execute(f"SELECT COUNT(*) FROM proposals WHERE {WAITING} AND "
                                     "(SELECT session_id FROM runs WHERE runs.id = proposals.run_id) IS NULL").fetchone()[0]
        return self.conn.execute(f"SELECT COUNT(*) FROM proposals WHERE {WAITING} AND run_id IN "
                                 "(SELECT id FROM runs WHERE session_id=?)", (session_id,)).fetchone()[0]
