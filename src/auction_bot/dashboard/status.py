"""What the dashboard shows about the state of the day.

Pure reads: the config, the newest rules snapshot, the store and the job manager. Nothing here
opens a browser or talks to a site, and nothing here changes a proposal.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from auction_bot import lock
from auction_bot.carbarn.client import latest_snapshot, load_rules
from auction_bot.dashboard.jobs import COMMANDS, run_dict
from auction_bot.judge.client import providers_for

SIGNIN_FRESH_HOURS = 12.0  # a sign-in check older than this is asked for again
_cache: dict = {}


def lock_label(cfg) -> str:
    if not cfg.project_finished:
        return "TESTING: no bids are placed"
    return "DRY RUN" if cfg.dry_run else "LIVE PLACEMENT"


def parse_time(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        t = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def age_hours(stamp: str | None) -> float | None:
    t = parse_time(stamp)
    return None if t is None else max(0.0, (datetime.now(timezone.utc) - t).total_seconds() / 3600.0)


def ago(stamp: str | None) -> str:
    """'just now', '12 min ago', '3 h ago', '2 days ago': the age of a stored time, in words."""
    h = age_hours(stamp)
    if h is None:
        return "never"
    minutes = h * 60
    if minutes < 1.5:
        return "just now"
    if minutes < 90:
        return f"{round(minutes)} min ago"
    if h < 36:
        return f"{round(h)} h ago"
    return f"{round(h / 24)} days ago"


def local_time(stamp: str | None, with_date: bool = True) -> str:
    t = parse_time(stamp)
    if t is None:
        return ""
    t = t.astimezone()
    return t.strftime("%d %b %H:%M" if with_date else "%H:%M:%S")


def duration(started: str | None, finished: str | None) -> str:
    a, b = parse_time(started), parse_time(finished) or datetime.now(timezone.utc)
    if a is None:
        return ""
    seconds = max(0, int((b - a).total_seconds()))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


def _today_start_utc() -> str:
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).isoformat(timespec="seconds")


# -- the parts --------------------------------------------------------------------------------

def rules_state(cfg) -> dict:
    """The newest guide snapshot: its age and how many guides it holds. Parsed once per file."""
    path = latest_snapshot(cfg)
    base = {"snapshot": None, "fetched_at": None, "age_hours": None, "stale": True, "error": None,
            "max_age_hours": cfg.max_rules_age_hours, "guides": 0, "active_guides": 0, "codes": []}
    if path is None:
        return base
    key = (str(path), path.stat().st_mtime_ns)
    hit = _cache.get("rules")
    if hit is None or hit["key"] != key:
        hit = {"key": key, "fetched_at": None, "guides": 0, "active_guides": 0, "codes": [], "error": None}
        try:
            hit["fetched_at"] = json.loads(path.read_text(encoding="utf-8")).get("fetched_at")
            rules, _ = load_rules(cfg, path)
            hit["guides"] = len(rules)
            for r in rules:
                active = bool(r.active and r.active_variants)
                hit["active_guides"] += active
                hit["codes"].append({"guide_id": r.guide_id, "code": r.model_code, "name": f"{r.make} {r.model}".strip(),
                                     "active": active})
        except Exception as exc:  # a snapshot that cannot be read is reported, never fatal to the page
            hit["error"] = f"{type(exc).__name__}: {exc}"[:200]
        _cache["rules"] = hit
    age = age_hours(hit["fetched_at"])
    return dict(base, snapshot=path.name, fetched_at=hit["fetched_at"], age_hours=age, error=hit["error"],
                stale=age is None or age > float(cfg.max_rules_age_hours), guides=hit["guides"],
                active_guides=hit["active_guides"], codes=hit["codes"])


def signin_state(store) -> dict:
    """The result of the newest sign-in check (`check-login`, `login --wait`, or a Bid run, whose first
    step is that check), and how old it is."""
    newest = None
    for command in ("check-login", "login", "bid"):
        for row in store.list_runs(limit=5, command=command):
            d = run_dict(row)
            if d["status"] == "ok" and d["summary"] and "nichibo" in d["summary"]:
                if newest is None or (d["finished_at"] or "") > (newest["finished_at"] or ""):
                    newest = d
                break
    out = {"known": False, "ok": False, "nichibo": None, "carbarn": None, "checked_at": None, "age_hours": None,
           "fresh": False, "run_id": None, "doubt": None}
    if newest is None:
        return out
    age = age_hours(newest["finished_at"])
    nichibo, carbarn = bool(newest["summary"].get("nichibo")), bool(newest["summary"].get("carbarn"))
    out.update(known=True, nichibo=nichibo, carbarn=carbarn, ok=nichibo and carbarn, checked_at=newest["finished_at"],
               age_hours=age, fresh=age is not None and age <= SIGNIN_FRESH_HOURS, run_id=newest["id"])
    # A later run that stopped on the login page outranks an older check that said "signed in".
    for row in store.list_runs(limit=10):
        d = run_dict(row)
        if d["id"] <= newest["id"]:
            break
        if d["status"] == "failed" and "log" in (d["error"] or "").lower() and "in" in (d["error"] or "").lower():
            out.update(ok=False, fresh=False, doubt=f"run {d['id']} ({d['command']}) stopped: {d['error']}")
            break
    return out


def judge_state(cfg, store) -> dict:
    mode = (cfg.sheet_check or "none").lower()
    out = {"mode": mode, "inline": bool((cfg.judge or {}).get("inline", True)), "providers": []}
    if mode not in ("judge", "openai"):
        return out
    for p in providers_for(cfg):
        calls = store.model_calls_today(p.name)
        share = min(1.0, calls / p.rpd) if p.rpd else 0.0
        out["providers"].append({"name": p.name, "model": p.model, "calls": calls, "rpd": p.rpd, "rpm": p.rpm,
                                 "vision": p.vision, "has_key": bool(p.api_key), "share": round(share, 4),
                                 "level": "bad" if share >= 0.95 else ("warn" if share >= 0.75 else "ok")})
    return out


def counts_state(store) -> dict:
    c = dict(store.counts())
    row = store.conn.execute(
        "SELECT SUM(status='SKIP') AS skipped, COUNT(*) AS seen FROM proposals WHERE created_at >= ?",
        (_today_start_utc(),)).fetchone()
    c["to_review"] = c["bid"] + c["review"]
    c["skipped_today"] = row["skipped"] or 0
    c["seen_today"] = row["seen"] or 0
    return c


def model_rows(cfg, store, rules: dict | None = None) -> list[dict]:
    """One row per model code: the guide, and what is stored under it."""
    rules = rules or rules_state(cfg)
    found = {r["model_code"]: dict(r) for r in store.conn.execute(
        "SELECT model_code, "
        "SUM(decision='pending' AND status IN ('BID','NEEDS_REVIEW')) AS to_review, "
        "SUM(decision='approved') AS approved, SUM(decision='rejected') AS rejected, "
        "SUM(decision='pending' AND status='SKIP') AS skipped, "
        "COUNT(*) AS lots, MAX(created_at) AS last_seen "
        "FROM proposals WHERE decision <> 'superseded' GROUP BY model_code").fetchall()}
    rows, seen = [], set()
    for g in rules["codes"]:
        code = g["code"]
        if code in seen:  # the same code under two guides (Serena, Serena Campervan): one row, both names
            for r in rows:
                if r["code"] == code and g["name"] not in r["name"]:
                    r["name"] += f", {g['name']}"
            continue
        seen.add(code)
        f = found.get(code, {})
        rows.append({"code": code, "name": g["name"], "active": g["active"], "to_review": f.get("to_review") or 0,
                     "approved": f.get("approved") or 0, "rejected": f.get("rejected") or 0, "skipped": f.get("skipped") or 0,
                     "lots": f.get("lots") or 0, "last_seen": f.get("last_seen")})
    for code, f in found.items():  # lots stored under a code the newest snapshot no longer has
        if code not in seen:
            rows.append({"code": code, "name": "not in the newest guides", "active": False, "to_review": f["to_review"] or 0,
                         "approved": f["approved"] or 0, "rejected": f["rejected"] or 0, "skipped": f["skipped"] or 0,
                         "lots": f["lots"] or 0, "last_seen": f["last_seen"]})
    rows.sort(key=lambda r: (-r["to_review"], not r["active"], r["code"]))
    return rows


def placements_between(store, started: str | None, finished: str | None) -> list[dict]:
    """The placement records written while a run was going (dry runs included), oldest first."""
    a, b = parse_time(started), parse_time(finished) or datetime.now(timezone.utc)
    if a is None:
        return []
    out = []
    for row in store.conn.execute(
            "SELECT id, ref_no, model_code, summary, approved_bid_jpy_k, bid_jpy_k, placement_json, evidence_json "
            "FROM proposals WHERE placement_json IS NOT NULL ORDER BY id").fetchall():
        try:
            p = json.loads(row["placement_json"])
        except (TypeError, ValueError):
            continue
        at = parse_time(p.get("at"))
        if at is None or not (a <= at <= b):
            continue
        shot = p.get("screenshot")
        rel = None
        if shot:
            try:
                ev = json.loads(row["evidence_json"] or "{}")
                from pathlib import Path
                folder = Path(ev["dir"]).name if ev.get("dir") else Path(shot).parent.name
                rel = f"/evidence/{folder}/{Path(shot).name}"
            except (TypeError, ValueError, KeyError):
                rel = None
        out.append({"proposal_id": row["id"], "ref_no": row["ref_no"], "model_code": row["model_code"],
                    "summary": row["summary"], "amount": row["approved_bid_jpy_k"] or row["bid_jpy_k"],
                    "ok": bool(p.get("ok")), "dry_run": bool(p.get("dry_run")), "message": p.get("message") or "",
                    "screenshot": rel, "at": p.get("at")})
    return out


COMMAND_ICONS = {"bid": "route", "learn": "graduation-cap", "scan": "scan-search", "fetch-rules": "book-open", "judge": "eye",
                 "replay": "rotate-ccw", "place": "gavel", "insights": "chart-column", "login": "log-in", "check-login": "shield-check"}
COMMAND_SHORT = {"bid": "Bid", "learn": "Learn", "scan": "Scan", "fetch-rules": "Guides", "judge": "Judge", "replay": "Replay",
                 "place": "Place", "insights": "Insights", "login": "Sign in", "check-login": "Sign-in check"}
DOING = {"bid": "Running Bid mode", "learn": "Recording your session", "scan": "Scanning Nichibo",
         "fetch-rules": "Fetching the bidding guides", "judge": "Judging pending lots", "replay": "Replaying the rules",
         "place": "Placing the approved bids as a dry run", "insights": "Working out the insights",
         "login": "Waiting for the sign-in", "check-login": "Checking the sign-in"}
# A Bid run's progress stage, as the step of the day it is at (the scan step includes the judge).
BID_STAGE_STEP = {"signin": "signin", "guides": "guides", "scan": "scan", "judge": "scan", "review": "review", "place": "place"}
STATUS_ICONS = {"running": "loader-circle", "ok": "circle-check", "failed": "circle-x", "stopped": "square"}
STATUS_WORDS = {"running": "Running", "ok": "Finished", "failed": "Failed", "stopped": "Stopped"}
SESSION_GAP_MINUTES = 30  # decisions closer together than this are one review session


def day_log(store, runs: list[dict], days: int = 7, limit: int = 80) -> dict:
    """The day book: runs and review sessions, newest first, today apart from the days before.

    A run is one entry. Decisions made one after another are one entry, a review session; a run
    started in between, or a pause of half an hour, starts a new one."""
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    runs = [r for r in runs if (r.get("started_at") or "") >= since]
    rows = [dict(r) for r in store.conn.execute(
        "SELECT d.id, d.action, d.amount_jpy_k AS amount, d.reason, d.ref_no, d.model_code, d.at, d.proposal_id, p.summary "
        "FROM decisions d LEFT JOIN proposals p ON p.id = d.proposal_id WHERE d.at >= ? ORDER BY d.id", (since,)).fetchall()]
    starts = sorted(t for t in (parse_time(r["started_at"]) for r in runs) if t)
    sessions: list[dict] = []
    for d in rows:
        at = parse_time(d["at"])
        if at is None:
            continue
        last = sessions[-1] if sessions else None
        split = (last is None or (at - last["_end"]).total_seconds() > SESSION_GAP_MINUTES * 60
                 or any(last["_end"] < s <= at for s in starts))
        if split:
            last = {"kind": "review", "started_at": d["at"], "at": d["at"], "_end": at, "approved": 0, "rejected": 0, "items": []}
            sessions.append(last)
        last["at"], last["_end"] = d["at"], at
        last["approved" if d["action"] == "approve" else "rejected"] += 1
        last["items"].insert(0, d)
    for s in sessions:
        s.pop("_end")
        s["count"] = s["approved"] + s["rejected"]
    entries = [{"kind": "run", "at": r["started_at"], "run": r} for r in runs] + sessions
    entries.sort(key=lambda e: (parse_time(e["at"]) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    entries = entries[:limit]
    today = _today_start_utc()
    live = [e for e in entries if e["kind"] == "run" and e["run"]["status"] == "running"]
    rest = [e for e in entries if e not in live]
    return {"live": live, "today": [e for e in rest if (e["at"] or "") >= today],
            "earlier": [e for e in rest if (e["at"] or "") < today]}


# -- the day as steps ---------------------------------------------------------------------------

def day_steps(s: dict, cache_hours: float) -> list[dict]:
    """Sign in, guides, scan, review, place: where each stands, and the one that is next.

    A single step runs as its own command; a Bid run walks all five, so its progress stage says
    which step is going. While it waits at the review, that step says so and links to the page."""
    current = s["current"] or {}
    running = current.get("command")
    bid_step = BID_STAGE_STEP.get(current.get("stage") or "") if running == "bid" else None
    waiting = bool(current.get("review_waiting"))
    last = s["last_runs"]

    def step(key, label, state, detail, command=None, href=None, options=None):
        going = bool(command and running == command) or key == bid_step  # what is true of a step at rest is not true while it runs
        if going:
            detail = "Waiting for your review." if key == "review" and waiting else "Running now."
        return {"key": key, "label": label, "state": "running" if going else state,
                "detail": detail, "command": command, "options": options or {}, "href": href}

    sign = s["signin"]
    if not sign["known"]:
        a = step("signin", "Check the sign-in", "due", "Not checked yet on this PC.", command="check-login")
    elif not sign["ok"]:
        out = [n for n, ok in (("Nichibo", sign["nichibo"]), ("the Carbarn dashboard", sign["carbarn"])) if not ok]
        a = step("signin", "Sign in", "due", sign["doubt"] or f"Signed out of {' and '.join(out)}.", command="login")
    elif not sign["fresh"]:
        a = step("signin", "Check the sign-in", "due", f"Last checked {ago(sign['checked_at'])}.", command="check-login")
    else:
        a = step("signin", "Signed in", "done", f"Both sites, checked {ago(sign['checked_at'])}.", command="check-login")

    rules = s["rules"]
    if rules["snapshot"] is None:
        b = step("guides", "Fetch the bidding guides", "due", "No snapshot on this PC yet.", command="fetch-rules")
    elif rules["stale"]:
        b = step("guides", "Fetch the bidding guides", "due",
                 f"The snapshot is {ago(rules['fetched_at']).replace(' ago', '')} old; scans need one under {rules['max_age_hours']:g} h.",
                 command="fetch-rules")
    else:
        b = step("guides", "Guides are fresh", "done", f"{rules['active_guides']} active guides, fetched {ago(rules['fetched_at'])}.",
                 command="fetch-rules")

    scan = last.get("scan")
    scan_age = age_hours(scan["finished_at"]) if scan and scan["status"] == "ok" else None
    if scan_age is not None and scan_age <= cache_hours:
        c = step("scan", "Scanned", "done", f"Finished {ago(scan['finished_at'])}.", command="scan")
    elif scan and scan["status"] in ("failed", "stopped") and (age_hours(scan["finished_at"]) or 99) <= cache_hours:
        c = step("scan", "Scan Nichibo", "due", f"The last scan {scan['status']} {ago(scan['finished_at'])}.", command="scan")
    else:
        c = step("scan", "Scan Nichibo", "due", "No scan yet today." if scan is None or scan_age is None else f"Last scan {ago(scan['finished_at'])}.",
                 command="scan")

    n = s["counts"]["to_review"]
    d = step("review", f"Review {n} lot{'s' if n != 1 else ''}" if n else "Nothing to review", "due" if n else "idle",
             "Approve, change the amount, or reject with a reason." if n else "New lots appear after a scan.", href="/review")

    m = s["counts"]["approved"]
    e = step("place", f"Place {m} approved bid{'s' if m != 1 else ''}" if m else "Nothing to place", "due" if m else "idle",
             (s["lock"]["label"] + ". " if m else "") + ("The amounts are typed and screenshotted, nothing is submitted." if m and s["lock"]["dry"] else
                                                          "Approved bids wait here until they are placed." if not m else ""),
             command="place" if m else None)

    steps = [a, b, c, d, e]
    nxt = next((x for x in steps if x["state"] == "running"), None) or next((x for x in steps if x["state"] == "due"), None)
    for x in steps:
        x["next"] = x is nxt
    return steps


def gather_status(cfg, store, jobs) -> dict:
    dry, reason = cfg.placement_mode(False)
    current = jobs.current()
    if current:
        waiting = getattr(jobs, "review_waiting", lambda: None)() is not None
        current = dict(current, label=COMMANDS[current["command"]].label if current["command"] in COMMANDS else current["command"],
                       doing=DOING.get(current["command"], current["command"]), started=ago(current["started_at"]),
                       stage=(current.get("progress") or {}).get("stage"), review_waiting=waiting)
    s = {
        "lock": {"testing": not cfg.project_finished, "dry_run": cfg.dry_run, "dry": dry, "label": lock_label(cfg), "reason": reason},
        "signin": signin_state(store),
        "rules": {k: v for k, v in rules_state(cfg).items() if k != "codes"},
        "judge": judge_state(cfg, store),
        "counts": counts_state(store),
        "last_runs": {command: {k: d[k] for k in ("id", "command", "status", "started_at", "finished_at", "summary", "error")}
                      for command, d in ((c, run_dict(r)) for c, r in store.last_runs_by_command().items())},
        "current": current,
        "profile_lock": lock.holder(cfg),
    }
    s["steps"] = day_steps(s, float(cfg.cache_hours))
    return s
