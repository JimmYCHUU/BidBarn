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
  decision TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
  decision_reason TEXT,
  decided_at TEXT,
  approved_bid_jpy_k INTEGER,
  placed_at TEXT,
  placement_json TEXT
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
"""

# What the reviewer did on the page, summarised per decision (added to `decisions` on first run).
DECISION_COLUMNS = {
    "opened_at": "TEXT", "decide_seconds": "REAL", "sheet_zoomed": "INTEGER DEFAULT 0",
    "photos_clicked": "INTEGER DEFAULT 0", "nichibo_opened": "INTEGER DEFAULT 0", "amount_edited": "INTEGER DEFAULT 0",
    "proposed_jpy_k": "INTEGER", "via": "TEXT",
}
EVENTS = ("open", "sheet_zoom", "photos_click", "nichibo_click", "amount_edit", "decide")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._ensure_columns("proposals", {"guide_id": "INTEGER"})  # databases created before guide_id existed
        self._ensure_columns("decisions", DECISION_COLUMNS)

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
        """actions: (ref_no, action, bid_jpy, bid_jpy_k, at). Re-running the same file adds nothing."""
        cur = self.conn.executemany(
            "INSERT OR IGNORE INTO manual_actions(session, ref_no, action, bid_jpy, bid_jpy_k, at) VALUES(?,?,?,?,?,?)",
            [(session, *a) for a in actions])
        self.conn.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0

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

    def decide(self, pid: int, action: str, reason: str | None, amount_jpy_k: int | None, make_rule: bool,
               via: str = "detail") -> None:
        """Record the human decision. The only caller is the review page (CLAUDE.md, rule 1)."""
        p = self.proposal(pid)
        if p is None:
            raise KeyError(pid)
        decision = "approved" if action == "approve" else "rejected"
        ts = now()
        tele = self.telemetry(pid)
        seconds = None
        if tele.get("opened_at"):
            seconds = round((datetime.fromisoformat(ts) - datetime.fromisoformat(tele["opened_at"])).total_seconds(), 1)
        edited = int(decision == "approved" and amount_jpy_k is not None and amount_jpy_k != p["bid_jpy_k"])
        self.conn.execute(
            "UPDATE proposals SET decision=?, decision_reason=?, decided_at=?, approved_bid_jpy_k=? WHERE id=?",
            (decision, reason, ts, amount_jpy_k if decision == "approved" else None, pid))
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
        return self.conn.execute("SELECT * FROM decisions WHERE proposal_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()

    def decisions_with_facts(self, model_code: str | None = None, since: str | None = None) -> list[dict]:
        """Every human decision joined with the proposal it was made on (facts parsed)."""
        q = ("SELECT d.*, p.summary, p.facts_json, p.hard_json, p.bid_jpy_k AS bid_jpy_k, p.guide_id, p.status "
             "FROM decisions d JOIN proposals p ON p.id=d.proposal_id WHERE 1=1")
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
        """Pending proposals a person still has to decide."""
        q = "SELECT * FROM proposals WHERE decision='pending' AND status IN ('BID','NEEDS_REVIEW')"
        args: list = []
        if model_code:
            q += " AND model_code=?"; args.append(model_code)
        return self.conn.execute(q + " ORDER BY model_code, id DESC", args).fetchall()

    def supersede_pending(self, ref_no: str, guide_id: int | None) -> int:
        """Retire earlier pending proposals for this vehicle+guide before a fresh evaluation is stored."""
        cur = self.conn.execute(
            "UPDATE proposals SET decision='superseded', decided_at=? WHERE ref_no=? AND decision='pending'"
            + (" AND guide_id=?" if guide_id is not None else ""),
            (now(), ref_no, guide_id) if guide_id is not None else (now(), ref_no))
        self.conn.commit()
        return cur.rowcount

    def approved_unplaced(self, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM proposals WHERE decision='approved' AND placed_at IS NULL ORDER BY id LIMIT ?", (limit,)).fetchall()

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
            "WHERE d.model_code=? AND d.reason IS NOT NULL AND d.reason<>'' ORDER BY d.id DESC LIMIT ?",
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
