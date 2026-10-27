"""The desk: today's pending lots grouped into auction sessions and lanes, served beside the old review.

Read-only. It decides nothing; the desk's buttons post to the review's own routes (decide, delete, reopen).
`GET /desk` is the full page, `GET /desk/board` the board fragment alone. Both take `session=<key>` and
`lane=<look|clear|wait|done>`; every session's rows are rendered and the ones outside the chosen session
and lane carry `hidden`, so the page works without JavaScript."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from auction_bot.dashboard import status as dashboard_status
from auction_bot.dashboard.guides import rules_for_code, variant_view
from auction_bot.review.desk_model import DECIDED_WINDOW, LANES, bid_row, bid_sheet, build_desk, look_items, scan_state

LANE_NAMES = {"look": "Needs a look", "clear": "Card-clear", "wait": "Waiting", "done": "Decided"}
ALL_WAITING = 100_000  # approved_unplaced's limit for the bid sheet: every waiting approval, not one run's worth


def _choose(sessions, session: str | None, lane: str | None) -> tuple[str, str]:
    """The session and lane to show: the ones asked for when they exist, else the first session with
    undecided lots and its first non-empty lane (none: the first session and `look`)."""
    by_key = {s.key: s for s in sessions}
    current = by_key.get(session or "") or next((s for s in sessions if s.undecided()), sessions[0] if sessions else None)
    if current is None:
        return "", "look"
    if lane in LANES:
        return current.key, lane
    counts = current.counts()
    return current.key, next((l for l in LANES if counts[l]), "look")


def mini_grid(cfg, p: dict) -> dict | None:
    """The lot's year and the years either side from its guide's grid, with the cell the bid came from marked
    `used`. None when the guide or the variant cannot be found. The variant is the one whose label is the hard
    result's `variant_name` (the hard result carries no variant id)."""
    hard = p.get("hard") or {}
    name, year = hard.get("variant_name"), hard.get("year")
    if not name or year is None:
        return None
    try:
        rules = rules_for_code(cfg, p.get("model_code") or "")
    except Exception:  # a snapshot that cannot be read reads as "no grid", never an error on the page
        return None
    # Only the lot's own guide: another guide's variant would light a cell that is not where this bid came from.
    rule = next((r for r in rules if r.guide_id == p.get("guide_id")), None)
    variant = next((v for v in (rule.variants if rule else []) if v.label == name), None)
    if variant is None:
        return None
    view = variant_view(variant)
    rows = []
    for row in view["rows"]:
        if abs(row["year"] - year) > 1:
            continue
        cells = [{"band": c["band"], "k": None if c["no_bid"] else c["k"],
                  "used": row["year"] == year and c["band"] == hard.get("band_label")} for c in row["cells"]]
        rows.append({"year": row["year"], "cells": cells})
    return {"bands": view["bands"], "rows": rows} if rows else None


def register(app, templates, cfg, store, page, *, load, sort_key) -> None:
    def context(request: Request, session: str | None, lane: str | None) -> dict:
        s = store()
        try:
            pending = [load(r) for r in s.list_triage()]
            covering = [load(r) for r in s.covering_proposals()]
            decided = [load(r) for r in s.decided_since((datetime.now(timezone.utc) - DECIDED_WINDOW).isoformat(timespec="seconds"))]
        finally:
            s.close()
        sessions = build_desk(pending, decided, sort_key, datetime.now(timezone.utc), covering=covering)
        key, current_lane = _choose(sessions, session, lane)
        return {"sessions": sessions, "current": key, "lane": current_lane, "lanes": LANES, "lane_names": LANE_NAMES}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/desk", response_class=HTMLResponse)
    def desk(request: Request, session: str | None = None, lane: str | None = None):
        """The desk. `/` is home and `/desk` is the old address; both run this one handler."""
        ctx = context(request, session, lane)
        s = store()
        try:
            ctx["pulse"] = dashboard_status.gather_status(cfg, s, request.app.state.jobs)
        finally:
            s.close()
        ctx["scan"] = scan_state(ctx["pulse"])
        return page(request, "desk.html", "desk", **ctx)

    @app.get("/api/find")
    def find(q: str = ""):
        """The Ctrl+K palette's lot search. Reads only; under 2 characters it finds nothing."""
        q = q.strip()[:40]
        if len(q) < 2:
            return JSONResponse({"lots": []})
        s = store()
        try:
            rows = s.find_lots(q, 20)
        finally:
            s.close()
        return JSONResponse({"lots": [{"pid": r["id"], "ref_no": r["ref_no"], "summary": r["summary"] or "",
                                       "decision": r["decision"], "status": r["status"], "url": f"/p/{r['id']}"}
                                      for r in rows]})

    @app.get("/desk/scan", response_class=HTMLResponse)
    def desk_scan(request: Request):
        """The scan bar fragment. Reads the status only; desk_scan.js starts and stops through /api/runs."""
        s = store()
        try:
            status = dashboard_status.gather_status(cfg, s, request.app.state.jobs)
        finally:
            s.close()
        return templates.TemplateResponse(request, "_desk_scan.html", {"scan": scan_state(status)})

    @app.get("/bids", response_class=HTMLResponse)
    def bids(request: Request):
        """The bid sheet: what `bot place` will take, per auction, with exposure and any stored placement result.
        The waiting rows are the ones `run.place_approved` reads (`approved_unplaced`, oldest proposal first), all
        of them, with the first `max_bids_per_run` that a dry run still types marked as the next dry run's; lots placed in the last 24 hours
        are listed apart. Read-only; the only control is the dry-run `place` command, started by dashboard.js."""
        since = (datetime.now(timezone.utc) - DECIDED_WINDOW).isoformat(timespec="seconds")
        s = store()
        try:
            waiting = [load(r) for r in s.approved_unplaced(ALL_WAITING)]
            placed = [bid_row(load(r)) for r in s.placed_since(since)]
        finally:
            s.close()
        per_run = max(0, int(cfg.max_bids_per_run or 0))
        groups = bid_sheet(waiting, next_run=per_run)
        rows = [r for g in groups for r in g.rows]
        typed_n = sum(r.state != "dry_run" for r in rows)  # a dry run does not type a lot it already placed ok
        next_n = sum(r.next_run for r in rows)
        commands = request.app.state.jobs.describe()
        return page(request, "bids.html", "bids", groups=groups, commands=commands, total_k=sum(g.exposure_k for g in groups),
                    waiting_n=len(waiting), per_run=per_run, next_n=next_n, after_n=typed_n - next_n, done_n=len(waiting) - typed_n, placed=placed, placed_k=sum(r.amount_k for r in placed if r.state == "placed"))

    @app.get("/desk/board", response_class=HTMLResponse)
    def desk_board(request: Request, session: str | None = None, lane: str | None = None):
        return templates.TemplateResponse(request, "_desk_board.html", context(request, session, lane))

    @app.get("/desk/lot/{pid}", response_class=HTMLResponse)
    def desk_lot(request: Request, pid: int):
        """The lot view fragment. Reads the store only: no decision, no telemetry."""
        s = store()
        try:
            row = s.proposal(pid)
            if row is None:
                raise HTTPException(404)
            p = load(row)
            try:
                names = {g["guide_id"]: f"{g['name']} {g['code']}".strip() for g in dashboard_status.rules_state(cfg)["codes"]}
            except Exception:  # no snapshot: guides fall back to their model codes
                names = {}
            p["guide_name"] = names.get(p["guide_id"], p["model_code"])
            p["siblings"] = [dict(x, guide_name=names.get(x["guide_id"], x["model_code"])) for x in s.siblings(p["ref_no"], pid)]
        finally:
            s.close()
        facts = p.get("facts") or {}
        start_k = (facts.get("start_price_jpy") or 0) // 1000
        amount = p.get("proposed_bid_jpy_k")
        # The same vehicle approved under another guide: one bid per vehicle, so this one is not approved again.
        cover = next((sb for sb in p["siblings"] if sb["decision"] == "approved"), None)
        covered = f"Covered by the approval of #{cover['id']} under {cover['guide_name']}." if cover else ""
        if not amount:
            blocked = "No amount from the grid. Change the amount to type one."
        elif amount * 1000 < (facts.get("start_price_jpy") or 0):
            blocked = f"The amount is below the start price ({start_k:,}K)."
        else:
            blocked = ""
        images_seen = {str(x).replace("\\", "/").rsplit("/", 1)[-1] for x in (p.get("soft") or {}).get("images_used") or []}
        return templates.TemplateResponse(request, "_desk_lot.html", {
            "p": p, "look": look_items(p), "grid": mini_grid(cfg, p), "start_k": start_k, "blocked": blocked, "seen": images_seen,
            "covered": covered})
