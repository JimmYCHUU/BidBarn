"""The desk: today's pending lots grouped into auction sessions and lanes, served beside the old review.

Read-only. It decides nothing; the desk's buttons (Task 3 and 4) post to the review's own routes.
`GET /desk` is the full page, `GET /desk/board` the board fragment alone. Both take `session=<key>` and
`lane=<look|clear|wait|done>`; every session's rows are rendered and the ones outside the chosen session
and lane carry `hidden`, so the page works without JavaScript."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from auction_bot.dashboard import status as dashboard_status
from auction_bot.dashboard.guides import rules_for_code, variant_view
from auction_bot.review.desk_model import LANES, build_desk, look_items

LANE_NAMES = {"look": "Needs a look", "clear": "Card-clear", "wait": "Waiting", "done": "Decided"}
DECIDED = ("approved", "rejected", "deleted")


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
    rule = next((r for r in rules if r.guide_id == p.get("guide_id")), None) or (rules[0] if rules else None)
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
            decided = [load(r) for d in DECIDED for r in s.list_proposals(decision=d)]
        finally:
            s.close()
        sessions = build_desk(pending, decided, sort_key, datetime.now(timezone.utc))
        key, current_lane = _choose(sessions, session, lane)
        return {"sessions": sessions, "current": key, "lane": current_lane, "lanes": LANES, "lane_names": LANE_NAMES}

    @app.get("/desk", response_class=HTMLResponse)
    def desk(request: Request, session: str | None = None, lane: str | None = None):
        ctx = context(request, session, lane)
        s = store()
        try:
            ctx["pulse"] = dashboard_status.gather_status(cfg, s, request.app.state.jobs)
        finally:
            s.close()
        return page(request, "desk.html", "desk", **ctx)

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
        if not amount:
            blocked = "No amount from the grid. Change the amount to type one."
        elif amount * 1000 < (facts.get("start_price_jpy") or 0):
            blocked = f"The amount is below the start price ({start_k:,}K)."
        else:
            blocked = ""
        images_seen = {str(x).replace("\\", "/").rsplit("/", 1)[-1] for x in (p.get("soft") or {}).get("images_used") or []}
        return templates.TemplateResponse(request, "_desk_lot.html", {
            "p": p, "look": look_items(p), "grid": mini_grid(cfg, p), "start_k": start_k, "blocked": blocked, "seen": images_seen})
