"""Learn from a HAR of Jim's own manual Nichibo session (`bot learn --har file.har`).

The HAR is a network capture from his browser. Only three things are read from it: which
auction lots he opened, in what order and when; which bids he placed (the `/auction/bid`
calls) and for how much; and which model codes he searched. Nothing else is kept: no
headers, cookies, bodies or images. Everything is then compared with what the bot has in
its store, entirely offline. This module never imports the browser layer and never
makes a request.
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from auction_bot.store import Store

# /auction/<segment> paths that are not a lot's detail page
NOT_A_LOT = {"search", "cart", "bid", "login", "jsonAuctionSheetData", "jsonSuggested", "jsonMaketPrice"}
RELOAD_SECONDS = 5.0


@dataclass
class Opened:
    ref_no: str
    at: datetime
    status: int | None = None


@dataclass
class ManualBid:
    ref_no: str
    yen: int
    thousands: int
    at: datetime
    status: int | None = None


@dataclass
class Search:
    at: datetime
    chassis: str | None


@dataclass
class HarSession:
    source: str
    started: datetime | None = None
    ended: datetime | None = None
    opened: list[Opened] = field(default_factory=list)
    bids: list[ManualBid] = field(default_factory=list)
    searches: list[Search] = field(default_factory=list)
    entries: int = 0
    ignored: int = 0

    @property
    def name(self) -> str:
        stamp = self.started.strftime("%Y-%m-%d %H:%M") if self.started else "?"
        return f"{self.source} @ {stamp}"


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def parse_har(path: Path, base_url: str = "https://autosearch.nichibojapan.com") -> HarSession:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    host = urlsplit(base_url).netloc.lower()
    session = HarSession(source=Path(path).stem)
    for entry in data.get("log", {}).get("entries", []):
        session.entries += 1
        req = entry.get("request") or {}
        url = str(req.get("url") or "")
        parts = urlsplit(url)
        if parts.netloc.lower() != host or str(req.get("method") or "GET").upper() != "GET" or not entry.get("startedDateTime"):
            session.ignored += 1
            continue
        at = _time(entry["startedDateTime"])
        status = (entry.get("response") or {}).get("status")
        p = parts.path.rstrip("/")
        q = parse_qs(parts.query)
        if p == "/auction/bid":
            ref = unquote(q.get("v", [""])[0])
            try:
                yen = int(q.get("b", ["0"])[0] or 0)
                ba = int(q.get("ba", ["0"])[0] or 0)
            except ValueError:
                yen, ba = 0, 0
            if ref:
                session.bids.append(ManualBid(ref, yen, ba, at, status))
                continue
        elif p in ("/auction", "/auction/search"):
            chassis = (q.get("Chassis", [""])[0] or "").strip().upper() or None
            session.searches.append(Search(at, chassis))
            continue
        else:
            m = re.fullmatch(r"/auction/([^/]+)", p)
            if m and m.group(1) not in NOT_A_LOT:
                session.opened.append(Opened(unquote(m.group(1)), at, status))
                continue
        session.ignored += 1
    session.opened.sort(key=lambda o: o.at)
    session.bids.sort(key=lambda b: b.at)
    session.searches.sort(key=lambda s: s.at)
    collapsed: list[Opened] = []
    for o in session.opened:  # a reload within a few seconds is the same visit
        if collapsed and collapsed[-1].ref_no == o.ref_no and (o.at - collapsed[-1].at).total_seconds() <= RELOAD_SECONDS:
            continue
        collapsed.append(o)
    session.opened = collapsed
    times = [o.at for o in session.opened] + [b.at for b in session.bids] + [s.at for s in session.searches]
    if times:
        session.started, session.ended = min(times), max(times)
    return session


# -- joining against the store ---------------------------------------------------------------

@dataclass
class LearnReport:
    session: HarSession
    bot_skipped_jim_bid: list[dict] = field(default_factory=list)
    agree: list[dict] = field(default_factory=list)
    unknown_to_bot: list[dict] = field(default_factory=list)
    bot_proposed_not_opened: list[dict] = field(default_factory=list)
    opened_no_bid: list[dict] = field(default_factory=list)
    per_model: dict[str, dict] = field(default_factory=dict)


def _search_before(session: HarSession, at: datetime) -> str | None:
    chassis = None
    for s in session.searches:
        if s.at <= at:
            chassis = s.chassis or chassis
    return chassis


def _bot_view(store: Store, ref_no: str, vid_map: dict[str, str]) -> tuple[dict | None, dict]:
    """(latest proposal row as dict, vehicle facts) for a ref; falls back on Nichibo's vid."""
    row = store.latest_proposal(ref_no)
    facts = store.vehicle_facts(ref_no)
    if row is None and not facts and ref_no in vid_map:
        ref_no = vid_map[ref_no]
        row = store.latest_proposal(ref_no)
        facts = store.vehicle_facts(ref_no)
    return (dict(row) if row is not None else None), facts or {}


def _reasons(row: dict | None) -> list[str]:
    if not row:
        return []
    try:
        checks = json.loads(row.get("hard_json") or "{}").get("checks") or []
    except json.JSONDecodeError:
        checks = []
    return [f"{c.get('name')}: {c.get('detail')}" for c in checks if not c.get("ok")][:3]


def join_session(store: Store, session: HarSession, guide_names: dict[int, str] | None = None) -> LearnReport:
    rep = LearnReport(session=session)
    vid_map = store.vehicles_by_vid()
    opened_refs = {o.ref_no for o in session.opened}
    bid_by_ref: dict[str, ManualBid] = {}
    for b in session.bids:
        bid_by_ref.setdefault(b.ref_no, b)
    model_of: dict[str, str] = {}
    facts_of: dict[str, dict] = {}

    for ref in sorted(opened_refs | set(bid_by_ref)):
        row, facts = _bot_view(store, ref, vid_map)
        at = bid_by_ref[ref].at if ref in bid_by_ref else next(o.at for o in session.opened if o.ref_no == ref)
        model = (row or {}).get("model_code") or facts.get("model_code") or _search_before(session, at) or "?"
        model_of[ref] = model
        facts_of[ref] = facts
        jim_bid = bid_by_ref.get(ref)
        entry = {"ref_no": ref, "model_code": model, "summary": (row or {}).get("summary") or "",
                 "jim_k": jim_bid.thousands if jim_bid else None, "bot_status": (row or {}).get("status"),
                 "bot_bid_k": (row or {}).get("bid_jpy_k"), "bot_decision": (row or {}).get("decision"),
                 "reasons": _reasons(row), "opened": ref in opened_refs}
        if row is None and not facts:
            rep.unknown_to_bot.append(entry)
        elif jim_bid is not None:
            if row and row["status"] in ("BID", "NEEDS_REVIEW"):
                entry["delta_k"] = (jim_bid.thousands - row["bid_jpy_k"]) if row.get("bid_jpy_k") else None
                rep.agree.append(entry)
            else:
                rep.bot_skipped_jim_bid.append(entry)
        else:
            rep.opened_no_bid.append(entry)

    if session.ended is not None:
        cutoff = session.ended.isoformat(timespec="seconds")
        for row in store.list_proposals(decision=None, limit=100_000):
            if row["decision"] not in ("pending", "approved") or row["status"] not in ("BID", "NEEDS_REVIEW"):
                continue
            if row["ref_no"] in opened_refs or row["ref_no"] in bid_by_ref:
                continue
            created = (row["created_at"] or "")[:19]
            if created and created > cutoff[:19]:
                continue  # proposed after the session
            rep.bot_proposed_not_opened.append({"ref_no": row["ref_no"], "model_code": row["model_code"],
                                                "summary": row["summary"] or "", "bot_status": row["status"],
                                                "bot_bid_k": row["bid_jpy_k"], "created_at": row["created_at"]})

    # Per model: counts, how the opening order compares with grade desc / km asc, pace.
    models = set(model_of.values()) | {p["model_code"] for p in rep.bot_proposed_not_opened}
    for model in sorted(models):
        opened = [o for o in session.opened if model_of.get(o.ref_no) == model]
        bids = [b for b in session.bids if model_of.get(b.ref_no) == model]
        proposed = [p for p in rep.bot_proposed_not_opened if p["model_code"] == model]
        proposed_and_opened = [r for r, m in model_of.items() if m == model and r in opened_refs
                               and (rep_row := store.latest_proposal(r)) is not None and rep_row["status"] in ("BID", "NEEDS_REVIEW")]
        gaps = [(b.at - a.at).total_seconds() for a, b in zip(opened, opened[1:])]
        pairs = ok = 0
        for a, b in zip(opened, opened[1:]):
            fa, fb = facts_of.get(a.ref_no, {}), facts_of.get(b.ref_no, {})
            if fa.get("grade") is None or fb.get("grade") is None:
                continue
            pairs += 1
            if fa["grade"] > fb["grade"] or (fa["grade"] == fb["grade"] and (fa.get("km") or 0) <= (fb.get("km") or 0)):
                ok += 1
        rep.per_model[model] = {
            "opened": len(opened), "bid": len(bids),
            "bot_proposed": len(proposed) + len(proposed_and_opened), "overlap": len(proposed_and_opened),
            "median_gap_s": round(statistics.median(gaps), 1) if gaps else None,
            "order_consistency": round(ok / pairs, 2) if pairs else None,
        }
    return rep


def render_report(rep: LearnReport) -> str:
    s = rep.session
    lines = [f"Session {s.name}: {len(s.opened)} lots opened, {len(s.bids)} bids placed, "
             f"{len(s.searches)} searches, {s.ignored} of {s.entries} requests ignored"]
    if s.started and s.ended:
        lines.append(f"   from {s.started:%H:%M:%S} to {s.ended:%H:%M:%S}")

    def block(title: str, items: list[dict], fmt) -> None:
        lines.append("")
        lines.append(f"{title} ({len(items)})")
        for it in items:
            lines.append("   " + fmt(it))

    block("Jim bid, the bot had skipped or never proposed", rep.bot_skipped_jim_bid,
          lambda it: f"{it['model_code']} {it['ref_no']} Jim {it['jim_k']:,}K | bot {it['bot_status'] or '-'}"
                     + (f" {it['bot_bid_k']:,}K" if it.get('bot_bid_k') else "") + (" | " + "; ".join(it["reasons"]) if it["reasons"] else ""))
    block("Both would bid", rep.agree,
          lambda it: f"{it['model_code']} {it['ref_no']} Jim {it['jim_k']:,}K | bot {it['bot_status']}"
                     + (f" {it['bot_bid_k']:,}K ({it['delta_k']:+,}K)" if it.get('delta_k') is not None else ""))
    block("Jim opened or bid on lots the bot has never seen", rep.unknown_to_bot,
          lambda it: f"{it['model_code']} {it['ref_no']}" + (f" Jim {it['jim_k']:,}K" if it.get('jim_k') else " (opened only)"))
    block("Bot proposed, Jim never opened", rep.bot_proposed_not_opened,
          lambda it: f"{it['model_code']} {it['ref_no']} {it['bot_status']}" + (f" {it['bot_bid_k']:,}K" if it.get('bot_bid_k') else "") + f" | {it['summary'][:60]}")
    block("Jim opened without bidding", rep.opened_no_bid,
          lambda it: f"{it['model_code']} {it['ref_no']} | bot {it['bot_status'] or 'not proposed'}"
                     + (f" {it['bot_bid_k']:,}K" if it.get('bot_bid_k') else ""))
    lines.append("")
    lines.append("Per model: opened / bid / bot proposed (overlap) / median seconds between lots / order matches grade-then-km")
    for model, st in rep.per_model.items():
        gap = f"{st['median_gap_s']:g}s" if st["median_gap_s"] is not None else "-"
        oc = f"{st['order_consistency']:.0%}" if st["order_consistency"] is not None else "-"
        lines.append(f"   {model}: {st['opened']} / {st['bid']} / {st['bot_proposed']} ({st['overlap']}) / {gap} / {oc}")
    return "\n".join(lines)


def save_session(store: Store, session: HarSession) -> int:
    """Store the parsed opens and bids (refs, times, amounts only) in data/bot.sqlite."""
    actions = [(o.ref_no, "open", None, None, o.at.isoformat(timespec="seconds")) for o in session.opened]
    actions += [(b.ref_no, "bid", b.yen, b.thousands, b.at.isoformat(timespec="seconds")) for b in session.bids]
    return store.add_manual_actions(session.name, actions)
