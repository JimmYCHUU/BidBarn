"""The Rulebook's actions: apply a previewed answer, keep what the bot does today, ask for a change.

Only the catalogue's open questions and their listed answers are accepted (`rulebook.py`); a locked rule has
no route. Applying an answer needs a finished, ok `rulebook-preview` run of that very answer, made at the
Rulebook version that is current now. It writes a version and starts `replay --apply`, which supersedes
waiting proposals only. Nothing here approves, decides or places anything.
"""
from __future__ import annotations

import json
import threading

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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

    def history_words(row) -> str:
        """One kept version in plain words."""
        q = rulebook.question(row["question_id"])
        value = json.loads(row["value_json"])
        if q.kind == "setting":
            label = next((c.label for c in q.choices if c.value == value), str(value))
            return f"{q.title}: set to {label.replace(' (today)', '')}."
        return f"{q.title}: kept as it is."

    @app.get("/rulebook", response_class=HTMLResponse)
    def rulebook_page(request: Request):
        """The Rulebook: what is decided, the open questions, what is locked. Reads only."""
        from auction_bot.carbarn.client import load_rules
        from auction_bot.review.app import _load
        s = store()
        try:
            answers = s.rulebook_answers()
            history = [{"version": r["version"], "date": (r["created_at"] or "")[:10], "words": history_words(r)}
                       for r in s.rulebook_history() if r["question_id"] in {q.id for q in rulebook.OPEN}]
            requests: dict[str, list] = {}
            for r in s.rule_requests():
                requests.setdefault(r["question_id"], []).append({"text": r["text"], "date": (r["created_at"] or "")[:10]})
            rows = [_load(r) for r in s.list_triage()]
            version = s.rulebook_version()
        finally:
            s.close()
        try:
            rules, _ = load_rules(cfg)
        except Exception:  # no snapshot yet: the counts that need the grid read zero
            rules = []
        open_questions = []
        for q in rulebook.OPEN:
            kept = q.kind == "confirm" and answers.get(q.id) == "keep"
            if kept:
                continue
            lots = rulebook.affected(q, rows, rules)
            unknown = q.id == "odo_margin" and not rules
            current = answers.get(q.setting, q.today) if q.kind == "setting" else None
            open_questions.append({"q": q, "lots": None if unknown else len(lots), "current": current, "requests": requests.get(q.id, [])})
        return page(request, "rulebook.html", "rulebook", decided=rulebook.DECIDED, history=history,
                    open_questions=open_questions, locked=rulebook.LOCKED, version=version)

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
            version_seen = summary.get("rulebook_version")
            if not isinstance(version_seen, int) or isinstance(version_seen, bool):
                _refuse("This preview cannot be checked against the Rulebook; run the preview again.")
            if summary.get("answer") != {q.setting: number}:
                _refuse("That preview was for a different answer. Preview this one first.")
            s = store()
            try:
                try:
                    version = s.add_rulebook_answer(qid, number, preview_run=run_id,
                                                    expect_version=version_seen)
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
