"""The desk's read model: lots grouped into auction sessions and lanes, with the "look at" items that keep a
lot out of Card-clear. Pure functions over the rows `review.app._load` returns; no store, no I/O. The routes
pass the sort key in, so this module never imports `review.app`."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta

from auction_bot.judge.requirements import Requirement, machine_decided

LANES = ("look", "clear", "wait", "done")
DECIDED_WINDOW = timedelta(hours=24)
UNKNOWN_KEY = "unknown"


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


def look_items(p: dict) -> list[LookItem]:
    """What Jim should look at before this lot can be approved. Strict card-clear: anything the model
    observed, anything unclear or failing, an odd-case flag, an eligibility verdict other than eligible
    or a missing amount is an item."""
    soft = p.get("soft") or {}
    items: list[LookItem] = []
    if soft.get("images_pending"):
        items.append(LookItem("The sheet and photos are not published yet. The amount comes from the card.", "wait", "page"))
    if soft.get("judge_pending"):
        items.append(LookItem("Waiting for the model (free quota used up).", "wait", "page"))
    judged = soft.get("mode") == "judge"
    badges = p.get("badges") or []
    for i, r in enumerate(soft.get("requirements") or []):
        if r.get("source") == "card" or r.get("kind") == "eligibility" or machine_decided(_requirement(r)) or i >= len(badges):
            continue
        b = badges[i]
        text = f"{b.get('text', '')}: {b['evidence']}" if b.get("evidence") else b.get("text", "")
        unjudged = judged and not soft.get("judgement") and not soft.get("judge_pending")
        kind = "fail" if b.get("cls") == "bad" else "unclear" if b.get("mark") == "?" or unjudged else "check"
        photo_no = _photo_no(p, r["id"])
        if photo_no:
            where = "photo"
        else:
            where = {"photos": "photo", "any": "photo", "sheet": "sheet"}.get(r.get("source"), "page")
        items.append(LookItem(text, kind, where, photo_no))
    if not judged:
        items += [LookItem(c, "check", "page") for c in soft.get("checklist") or [] if "auto-skip rule in force" not in c]
    items += [LookItem(f, "check", "grid") for f in soft.get("flags") or []]
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


def _when(p: dict) -> datetime | None:
    try:
        d = datetime.fromisoformat(p.get("decided_at") or "")
    except ValueError:
        return None
    return d.replace(tzinfo=None)


def build_desk(pending: list[dict], decided: list[dict], sort_key: Callable[[dict], tuple], now: datetime) -> list[Session]:
    """Sessions from the pending rows (one lot per vehicle, even when two guides propose it) and the last
    24 hours of decisions. A vehicle approved under one guide shows the other guide's proposal as done
    (covered), never as work and never decided for Jim."""
    approved: dict[str, int] = {}
    for d in sorted(decided, key=lambda r: _when(r) or datetime.min):
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
            s.lanes[lane].sort(key=lambda l: sort_key(l.p))
        s.lanes["done"].sort(key=lambda l: _when(l.p) or datetime.min, reverse=True)
    return sorted(sessions.values(), key=lambda s: (s.key != UNKNOWN_KEY, s.date, s.hall))
