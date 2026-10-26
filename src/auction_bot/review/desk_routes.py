"""The desk: today's pending lots grouped into auction sessions and lanes, served beside the old review.

Read-only. It decides nothing; the desk's buttons (Task 3 and 4) post to the review's own routes.
`GET /desk` is the full page, `GET /desk/board` the board fragment alone. Both take `session=<key>` and
`lane=<look|clear|wait|done>`; every session's rows are rendered and the ones outside the chosen session
and lane carry `hidden`, so the page works without JavaScript."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse

from auction_bot.dashboard import status as dashboard_status
from auction_bot.review.desk_model import LANES, build_desk

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
