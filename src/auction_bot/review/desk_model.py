"""The desk's read model: lots grouped into auction sessions and lanes, with the "look at" items that keep a
lot out of Card-clear. Pure functions over the rows `review.app._load` returns; no store, no I/O. The routes
pass the sort key in, so this module never imports `review.app`."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone

from auction_bot.judge.requirements import Requirement, machine_decided

LANES = ("look", "clear", "wait", "done")
DECIDED_WINDOW = timedelta(hours=24)
UNKNOWN_KEY = "unknown"
_MIN = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class LookItem:
    text: str
    kind: str  # fail | unclear | check | wait
    where: str  # sheet | photo | grid | pair | page
    photo_no: int | None = None


@dataclass
class DeskLot:
    p: dict
    lane: str
    look: list[LookItem]
    siblings: list[int] = field(default_factory=list)
    covered_by: int | None = None
    cleared: bool = False


@dataclass
class Session:
    key: str
    label: str
    date: str
    hall: str
    lanes: dict[str, list[DeskLot]]

    def counts(self) -> dict[str, int]:
        return {lane: len(self.lanes.get(lane, [])) for lane in LANES}

    def undecided(self) -> int:
        c = self.counts()
        return c["look"] + c["clear"] + c["wait"]


_REQ_FIELDS = {f.name for f in fields(Requirement)}


def _requirement(r: dict) -> Requirement:
    return Requirement(**{k: v for k, v in r.items() if k in _REQ_FIELDS})


def _photo_no(p: dict, req_id: str) -> int | None:
    ladder = (p.get("ladders") or {}).get(req_id) or next(
        (l for l in (p.get("soft") or {}).get("ladders") or [] if l.get("id") == req_id), None)
    for s in (ladder or {}).get("steps") or []:
        if s.get("decided") and s.get("photo_no"):
            return s["photo_no"]
    return None


SEATS_NOT_READ = "Seats: the sheet's seat count was not read"


def hard_reasons(hard: dict | None) -> list[str]:
    """The hard checks that failed or could not be decided, as `HardResult.reasons` words them ("start: start
    price unknown"). Read from the stored `HardResult.to_dict()`; anything unreadable is skipped."""
    out = []
    for c in (hard or {}).get("checks") or []:
        if isinstance(c, dict) and not c.get("ok"):
            name, detail = c.get("name") or "rule", c.get("detail") or ""
            out.append(f"{name}: {detail}" if detail else name)
    return out


def look_items(p: dict) -> list[LookItem]:
    """What Jim should look at before this lot can be approved. Strict card-clear: anything the model
    observed, anything unclear or failing (the hard rules' own review reasons and deferred modifiers too),
    an odd-case flag, an eligibility verdict other than eligible, an unread seat count or a missing amount
    is an item. A lot the rules sent to review with no judgement stored is never card-clear."""
    soft = p.get("soft") or {}
    hard = p.get("hard") if isinstance(p.get("hard"), dict) else {}
    items: list[LookItem] = []
    if soft.get("images_pending"):
        items.append(LookItem("The sheet and photos are not published yet. The amount comes from the card.", "wait", "page"))
    if soft.get("judge_pending"):
        items.append(LookItem("Waiting for the model (free quota used up).", "wait", "page"))
    items += [LookItem(r, "unclear", "grid") for r in hard_reasons(hard)]
    judged = soft.get("mode") == "judge"
    badges = p.get("badges") or []
    seats_read = (p.get("facts") or {}).get("seats") is not None
    for i, r in enumerate(soft.get("requirements") or []):
        try:
            req = _requirement(r)
            seat_rule = machine_decided(req)
            skip = r.get("source") == "card" or r.get("kind") == "eligibility" or (seat_rule and seats_read)
        except (TypeError, KeyError, AttributeError):
            # A stored requirement we cannot read is uncertainty, never a reason to read card-clear or to fail the page.
            said = r.get("text") if isinstance(r, dict) else None
            items.append(LookItem(said if isinstance(said, str) and said else "A requirement could not be read", "unclear", "page"))
            continue
        if skip:
            continue
        if seat_rule:
            # The hard seat check is skipped while the count is unknown, so the rule is answered by nobody yet.
            items.append(LookItem(SEATS_NOT_READ, "unclear", "sheet"))
            continue
        photo_no = _photo_no(p, r["id"])
        where = "photo" if photo_no else {"photos": "photo", "any": "photo", "sheet": "sheet"}.get(r.get("source"), "page")
        if i >= len(badges):
            items.append(LookItem(r["text"], "unclear", where, photo_no))
            continue
        b = badges[i]
        text = f"{b.get('text', '')}: {b['evidence']}" if b.get("evidence") else b.get("text", "")
        unjudged = judged and not soft.get("judgement") and not soft.get("judge_pending")
        kind = "fail" if b.get("cls") == "bad" else "unclear" if b.get("mark") == "?" or unjudged else "check"
        items.append(LookItem(text, kind, where, photo_no))
    if not judged:
        # pipeline.manual_soft writes "Auto-skip rule in force (did not fire): ..."; the match ignores case.
        items += [LookItem(c, "check", "page") for c in soft.get("checklist") or []
                  if "auto-skip rule in force" not in str(c).lower()]
    flags = [f for f in soft.get("flags") or [] if isinstance(f, str)]
    items += [LookItem(f, "check", "grid") for f in flags]
    # A modifier the rules could not decide (judge mode already names it in a flag; manual mode does not).
    items += [LookItem(f"Modifier you must judge yourself: {m}", "unclear", "grid")
              for m in hard.get("deferred_modifiers") or [] if not any(str(m) in f for f in flags)]
    items += [LookItem(h.get("message", ""), "check", "sheet") for h in soft.get("hints") or [] if h.get("level") != "info"]
    elig = p.get("eligibility") or {}
    if elig.get("verdict") == "cannot_import":
        reason = (p.get("why_line") or "").removeprefix("cannot import: ")
        items.append(LookItem("Cannot import: " + reason, "fail", "pair"))
    elif elig.get("verdict") == "confirm":
        first = next((c for c in elig.get("checks") or [] if c.get("state") not in ("pass", "info")), None)
        items.append(LookItem("Confirm import: " + ((first or {}).get("detail") or elig.get("label") or ""), "unclear", "pair"))
    if not p.get("proposed_bid_jpy_k"):
        items.append(LookItem("No amount from the grid. Type one with E.", "check", "grid"))
    if p.get("recommendation") == "skip" and not any(i.kind == "fail" for i in items):
        items.append(LookItem("The model recommends skip.", "check", "page"))
    if not items and p.get("status") == "NEEDS_REVIEW" and not p.get("soft"):
        # The pipeline stores a hard NEEDS_REVIEW without soft_json: nothing judged it, so it is never card-clear.
        first = next(iter(hard_reasons(hard) + [r for r in hard.get("reasons") or [] if isinstance(r, str)]), "no reason was stored")
        items.append(LookItem(f"The rules sent this lot to review: {first}", "unclear", "grid"))
    return items


def lane_of(p: dict, look: list[LookItem]) -> str:
    if p.get("decision") != "pending":
        return "done"
    if any(i.kind == "wait" for i in look):
        return "wait"
    return "look" if look else "clear"


def session_of(p: dict) -> tuple[str, str, str, str]:
    """(key, label, date, hall) from the auction day and hall the lot's own page gave; card-only lots have neither."""
    f = p.get("facts") or {}
    date, hall = f.get("auction_date") or "", f.get("auction_place") or ""
    if not date and not hall:
        return (UNKNOWN_KEY, "Auction day not known", "", "")
    try:
        day = datetime.strptime(date, "%Y%m%d").strftime("%a %d %b")
    except ValueError:
        day = date
    label = " · ".join(x for x in (day, hall) if x)
    return (f"{date}|{hall}", label, date, hall)


def session_order(s) -> tuple:
    """Sessions with no auction day first, then by date, then by hall (any object with key, date, hall)."""
    return (s.key != UNKNOWN_KEY, s.date, s.hall)


def _utc(d: datetime) -> datetime:
    """Aware UTC; a naive value is taken to be UTC already."""
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def _when(p: dict) -> datetime | None:
    try:
        return _utc(datetime.fromisoformat(p.get("decided_at") or ""))
    except ValueError:
        return None


def build_desk(pending: list[dict], decided: list[dict], sort_key: Callable[[dict], tuple], now: datetime,
               covering: list[dict] | None = None) -> list[Session]:
    """Sessions from the pending rows (one lot per vehicle, even when two guides propose it) and the last
    24 hours of decisions. A vehicle approved under one guide shows the other guide's proposal as done
    (covered), never as work and never decided for Jim.

    The store writes `decided_at` as UTC ISO text. Both it and `now` are compared as aware UTC; a naive
    value (a test's fixed `now`, say) is taken to be UTC.

    `covering` is every approval that still holds its vehicle or was placed (no time window); a pending lot of
    such a vehicle is covered however old the approval is. It adds to the approvals found in `decided`."""
    now = _utc(now)
    approved: dict[str, int] = {}
    for d in sorted(list(covering or []) + decided, key=lambda r: _when(r) or _MIN):
        if d.get("decision") == "approved":
            approved[d["ref_no"]] = d["id"]
    lots: list[DeskLot] = []
    by_ref: dict[str, list[dict]] = {}
    for p in pending:
        by_ref.setdefault(p["ref_no"], []).append(p)
    for ref, rows in by_ref.items():
        rows.sort(key=sort_key)
        head = rows[0]
        sibs = [r["id"] for r in rows[1:]]
        look = look_items(head)
        covered = approved.get(ref)
        lots.append(DeskLot(head, "done" if covered else lane_of(head, look), look, sibs, covered_by=covered))
    for d in decided:
        at = _when(d)
        if at is None or now - at > DECIDED_WINDOW:
            continue
        lots.append(DeskLot(d, "done", look_items(d), cleared=d.get("decision") == "deleted"))
    sessions: dict[str, Session] = {}
    for lot in lots:
        key, label, date, hall = session_of(lot.p)
        s = sessions.setdefault(key, Session(key, label, date, hall, {lane: [] for lane in LANES}))
        s.lanes[lot.lane].append(lot)
    for s in sessions.values():
        for lane in ("look", "clear", "wait"):
            s.lanes[lane].sort(key=lambda lot: sort_key(lot.p))
        s.lanes["done"].sort(key=lambda lot: _when(lot.p) or _MIN, reverse=True)
    return sorted(sessions.values(), key=session_order)


@dataclass
class BidRow:
    p: dict
    amount_k: int
    state: str  # approved | dry_run | blocked | placed | live_failed
    message: str
    screenshot: str | None
    queue: int = 0  # 1-based place in the order `bot place` takes the waiting approvals; 0 for a placed lot
    next_run: bool = False  # among the first `max_bids_per_run`: taken by the next dry run


@dataclass
class BidGroup:
    key: str
    label: str
    rows: list[BidRow]
    exposure_k: int
    date: str = ""
    hall: str = ""


def _screenshot_url(p: dict, shot: str | None) -> str | None:
    """`/evidence/<lot folder>/<file>` from the stored screenshot path, built from path names only so it
    cannot leave the evidence folder (same rule as `status.placements_between`)."""
    if not shot:
        return None
    from pathlib import PurePath
    name = PurePath(str(shot).replace("\\", "/")).name
    ev_dir = (p.get("evidence") or {}).get("dir")
    folder = PurePath(str(ev_dir).replace("\\", "/")).name if ev_dir else PurePath(str(shot).replace("\\", "/")).parent.name
    if not name or not folder or name in (".", "..") or folder in (".", ".."):
        return None
    return f"/evidence/{folder}/{name}"


def bid_amount_k(p: dict) -> int:
    """The amount placement types: `run.place_approved` uses `approved_bid_jpy_k or bid_jpy_k`."""
    return int(p.get("approved_bid_jpy_k") or p.get("bid_jpy_k") or 0)


def bid_row(p: dict) -> BidRow:
    pl = p.get("placement") or {}
    if p.get("placed_at"):
        state = "live_failed" if pl and not pl.get("ok") and not pl.get("dry_run") else "placed"
    elif pl and pl.get("ok") and pl.get("dry_run"):
        state = "dry_run"
    elif pl and not pl.get("ok"):
        state = "blocked"
    else:
        state = "approved"
    return BidRow(p, bid_amount_k(p), state, pl.get("message") or "", _screenshot_url(p, pl.get("screenshot")))


def bid_sheet(approved: list[dict], next_run: int = 0) -> list[BidGroup]:
    """Approved lots per auction session, in the desk's session order, with each session's exposure
    (the sum of its amounts; shown, never capped).

    `approved` comes in the order placement takes it (`Store.approved_unplaced`: oldest proposal first).
    Each row keeps that place (`queue`), groups keep it inside, and the first `next_run` rows
    (`cfg.max_bids_per_run`) are marked as taken by the next dry run."""
    groups: dict[str, BidGroup] = {}
    marked = 0
    for i, p in enumerate(approved):
        key, label, date, hall = session_of(p)
        g = groups.setdefault(key, BidGroup(key, label, [], 0, date, hall))
        row = bid_row(p)
        row.queue = i + 1
        if row.state != "dry_run" and marked < next_run:
            row.next_run, marked = True, marked + 1
        g.rows.append(row)
        g.exposure_k += row.amount_k
    return sorted(groups.values(), key=session_order)


# -- the scan bar --------------------------------------------------------------------------------

@dataclass
class ScanState:
    """What the desk's scan bar shows and offers. `kind`: idle, running, signin, busy or failed."""
    kind: str
    text: str
    run_id: int | None
    can_start: bool
    can_stop: bool
    poll_seconds: int


def _at(stamp: str | None, now: datetime | None = None) -> str:
    """A stored time in the office's local time, as the dashboard shows times: "07:30" today, "Mon 26 Oct 07:30"
    on another day (the weekday as the desk's session labels write it)."""
    from auction_bot.dashboard.status import parse_time
    t = parse_time(stamp)
    if t is None:
        return ""
    t = t.astimezone()
    today = (now or datetime.now()).astimezone().date()
    return t.strftime("%H:%M" if t.date() == today else "%a %d %b %H:%M")


def _signed_out_site(error: str | None) -> str | None:
    """The site a stored run error says signed out (the `NotLoggedIn` texts), or None for any other error."""
    e = (error or "").lower()
    if "redirected to the login page" in e:
        return "Nichibo"
    if "carbarn" in e and "bot login" in e:
        return "the Carbarn dashboard"
    return None


def _signin_text(prog: dict) -> str:
    """The sign-in wait in words, naming the site the progress says is signed out (Nichibo while it is not known)."""
    out = [name for key, name in (("nichibo", "Nichibo"), ("carbarn", "the Carbarn dashboard")) if prog.get(key) is False]
    sites = " and ".join(out) or "Nichibo"
    if prog.get("expired"):
        return f"{sites[0].upper()}{sites[1:]} signed out during the scan. Sign in in the bot's window; the scan carries on after that."
    return f"Sign in to {sites} in the bot's window. The scan carries on after that."


def scan_state(status: dict, now: datetime | None = None) -> ScanState:
    """The scan bar from `gather_status`'s dict. Pure: it starts and stops nothing. `now` (local time) decides
    whether the last scan's time needs its day; tests pass it, the page uses the clock."""
    current = status.get("current")
    if current and current.get("command") == "scan-now":
        prog = current.get("progress") or {}
        rid = current.get("id")
        if prog.get("stage") == "signin" and prog.get("waiting"):
            return ScanState("signin", _signin_text(prog), rid, False, True, 5)
        if prog.get("stage") == "scan" and prog.get("guides"):
            new = int(prog.get("review") or 0) + int(prog.get("bid") or 0)
            text = f"Scanning: guide {prog.get('guide') or 0} of {prog['guides']}, {new} new for the desk so far."
        else:
            text = "Scanning: checking the sign-in and the guides."
        return ScanState("running", text, rid, False, True, 5)
    if current:
        label = current.get("label") or current.get("command") or "Another run"
        return ScanState("busy", f"{label} is running. Scan now is offered again when it has finished.", current.get("id"),
                         False, False, 15)
    last = status.get("last_runs") or {}
    runs = [r for r in (last.get(c) for c in ("scan-now", "scan", "bid"))
            if r and r.get("status") in ("ok", "failed", "stopped")]
    newest = max(runs, key=lambda r: r.get("finished_at") or r.get("started_at") or "", default=None)
    if newest is None:
        return ScanState("idle", "No scan yet.", None, True, False, 60)
    at = _at(newest.get("finished_at") or newest.get("started_at"), now)
    if newest["status"] != "ok":
        error, site = newest.get("error"), _signed_out_site(newest.get("error"))
        if site:
            text = f"The last scan stopped at {at}: {site} signed you out. Press Scan now to sign in and scan again."
        elif error:
            text = f"The last scan stopped at {at}: {error}"
        elif newest["status"] == "stopped":
            text = f"The last scan was stopped at {at}."
        else:
            text = f"The last scan failed at {at}."
        return ScanState("failed", text, None, True, False, 60)
    summary = newest.get("summary") or {}
    n = int(summary.get("review") or 0) + int(summary.get("bid") or 0)
    return ScanState("idle", f"Last scan {at}, {n} new for the desk.", None, True, False, 60)
