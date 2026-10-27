"""The Rulebook's actions: apply a previewed answer, keep what the bot does today, ask for a change.

Only the catalogue's open questions and their listed answers are accepted (`rulebook.py`); a locked rule has
no route. Applying an answer needs a finished, ok `rulebook-preview` run of that very answer, made at the
Rulebook version that is current now. It writes a version and starts `replay --apply`, which supersedes
waiting proposals only. Nothing here approves, decides or places anything.
"""
from __future__ import annotations

import threading

from fastapi import Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from auction_bot import rulebook

_APPLY = threading.Lock()  # the check of the preview and the write of the version happen as one step


def _refuse(detail: str, status: int = 409):
    raise HTTPException(status_code=status, detail=detail)


def _question(qid: str):
    try:
        return rulebook.question(qid)
    except KeyError:
        _refuse("There is no such Rulebook question.", 404)


def _whole_number(raw, what: str) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        _refuse(f"{what} must be a whole number.", 400)


def register(app, templates, cfg, store, page) -> None:
    from auction_bot.dashboard.jobs import Busy
    from auction_bot.review.app import _wants_json

    def done(request: Request, body: dict):
        if _wants_json(request):
            return JSONResponse(body)
        return RedirectResponse("/rulebook", status_code=303)

    @app.post("/rulebook/{qid}/apply")
    def rulebook_apply(request: Request, qid: str, value: str = Form(""), preview_run: str = Form("")):
        q = _question(qid)
        if q.kind != "setting":
            _refuse("This question has no value to apply. Keep it or ask for a change.")
        number = _whole_number(value, "The answer")
        if not rulebook.valid_value(q, number):
            _refuse("That is not one of the answers the Rulebook offers.", 400)
        try:
            run_id = int(preview_run.strip())
        except ValueError:
            run_id = 0
        if not 0 < run_id < 2**62:
            _refuse("preview_run must be a run number", 400)
        jobs = request.app.state.jobs
        with _APPLY:
            run = jobs.get(run_id)
            if run is None or run.get("command") != "rulebook-preview":
                _refuse("Preview this answer first. That run is not a Rulebook preview.")
            if run.get("status") != "ok":
                _refuse("That preview did not finish. Preview this answer again.")
            summary = run.get("summary") or {}
            bad = summary.get("errors_total") or summary.get("errors")
            if bad:
                n = bad if isinstance(bad, int) else len(bad)
                _refuse(f"The preview could not check {n} lots; look at its run page before applying.")
            if summary.get("answer") != {q.setting: number}:
                _refuse("That preview was for a different answer. Preview this one first.")
            s = store()
            try:
                try:
                    version = s.add_rulebook_answer(qid, number, preview_run=run_id,
                                                    expect_version=summary.get("rulebook_version"))
                except ValueError as exc:
                    _refuse(str(exc))
            finally:
                s.close()
        body: dict = {"ok": True, "version": version}
        try:
            body["run_id"] = jobs.start("replay", {"apply": True})["id"]
        except Busy as exc:
            body["busy"] = (f"The answer is saved as version {version}, but {exc}. "
                            "Run Replay with Store the new outcomes when it is free.")
        return done(request, body)

    @app.post("/rulebook/{qid}/keep")
    def rulebook_keep(request: Request, qid: str):
        q = _question(qid)
        if q.kind != "confirm":
            _refuse("This question has a value to choose. Preview an answer and apply it.")
        s = store()
        try:
            version = s.add_rulebook_answer(qid, "keep")
        finally:
            s.close()
        return done(request, {"ok": True, "version": version})

    @app.post("/rulebook/{qid}/request")
    def rulebook_request(request: Request, qid: str, text: str = Form("")):
        _question(qid)
        s = store()
        try:
            rid = s.add_rule_request(qid, text)
        except ValueError as exc:
            _refuse(str(exc), 400)
        finally:
            s.close()
        return done(request, {"ok": True, "request": rid})
