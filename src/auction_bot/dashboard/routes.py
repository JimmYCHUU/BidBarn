"""The dashboard's routes: the overview, the runs and their live console, insights, settings,
and the small JSON API the pages use. Registered by `review.app.create_app`.

The page can start only the commands in `jobs.COMMANDS`, as dry runs where bids are concerned.
It never decides a proposal: that stays with the review page's own route (CLAUDE.md, rule 1).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from auction_bot.dashboard import guides as guide_pages  # the module: this file's own function is `register` too
from auction_bot.dashboard import status as st
from auction_bot.dashboard.jobs import COMMANDS, BadRequest, Busy, NotRunning
from auction_bot.insights import model_accuracy, propose_skip_lines, summarise
from auction_bot.judge.client import providers_for

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
MODES = ("learn", "bid")  # the two runs the overview starts; everything else is a single step
# The single steps the Runs page offers, in order, and the options its form shows for each. The
# `COMMANDS` table keeps every option: what is not shown falls to its default through `build_argv`.
STEP_COMMANDS = ["login", "check-login", "fetch-rules", "scan", "judge", "replay", "sheet-trial", "place"]
SHOWN_OPTIONS = {"scan": ["model", "limit"], "replay": ["apply"], "sheet-trial": ["count"]}
# What a log entry shows of a run's closing numbers: at most three, by the labels `summary_pairs` gives them.
ENTRY_LABELS = {"scan": ["For review", "Skipped", "Errors"], "scan-now": ["For review", "Skipped", "Errors"], "fetch-rules": ["Guides"], "judge": ["Judged", "Model unavailable"],
                "replay": ["Changed", "Stored"], "place": ["Bids", "Dry run"], "login": ["Nichibo", "Carbarn dashboard"],
                "check-login": ["Nichibo", "Carbarn dashboard"], "learn": ["Lots", "Agree", "Disagree"], "bid": ["For review", "Bids"],
                "sheet-trial": ["Sheets", "Failed", "Disagreements"]}


def local_only(request: Request, allowed: set[str]) -> str | None:
    """Why this request is refused, or None. The page is for this PC only: a request that names
    another host, or that another site's page made, is not served. `allowed` is the host names
    that mean this PC."""
    name = (request.headers.get("host") or "").rsplit(":", 1)[0].strip().lower()
    if name and name not in allowed:
        return "this page is served to this PC only"
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if (request.headers.get("sec-fetch-site") or "same-origin").lower() not in ("same-origin", "none"):
            return "requests from other sites are refused"
        origin = request.headers.get("origin")
        if origin and origin != "null":
            origin_host = origin.split("://", 1)[-1].rsplit(":", 1)[0].strip().lower()
            if origin_host not in allowed:
                return "requests from other sites are refused"
    return None


def command_label(name: str) -> str:
    return COMMANDS[name].label if name in COMMANDS else name


def options_text(run: dict) -> str:
    """The run's arguments after the command word, as typed on a console."""
    args = list(run.get("args") or [])
    return " ".join(args[1:]) if len(args) > 1 else ""


def options_words(run: dict) -> str:
    """The run's options in the page's words, what the page always passes left out
    ("GDH206, ANH20W, at most 3 lots per model"). The command line itself is `options_text`."""
    args = list(run.get("args") or [])[1:]
    command = run.get("command")
    models, words, i = [], [], 0
    while i < len(args):
        a, value = args[i], (args[i + 1] if i + 1 < len(args) else None)
        if a in ("-m", "--model") and value is not None:
            models.append(value)
            i += 2
        elif a == "--limit" and value is not None:
            words.append(f"at most {value} lot{'' if value == '1' else 's'}" + (" per model" if command in ("scan", "scan-now") else ""))
            i += 2
        elif a == "--days" and value is not None:
            words.append(f"the last {value} day{'' if value == '1' else 's'}")
            i += 2
        elif a == "--min-support" and value is not None:
            words.append(f"{value} rejection{'' if value == '1' else 's'} per rule")
            i += 2
        else:
            word = {"--no-headless": "browser window shown", "--headed": "browser window shown", "--rescan": "known lots again",
                    "--fresh": "cache ignored", "--apply": "new outcomes stored"}.get(a)
            if word:
                words.append(word)
            i += 1
    if command == "replay" and "--apply" not in args:
        words.append("report only")
    return ", ".join(([", ".join(models)] if models else []) + words)


LEARN_COUNTS = [("lots", "Lots"), ("opened", "Opened"), ("bids", "Your bids"), ("agree", "Agree"), ("disagree", "Disagree"),
                ("candidates", "Candidate rules"), ("notes", "Notes"), ("actions_saved", "Kept in the store")]


def summary_pairs(run: dict) -> list[tuple[str, str]]:
    """The closing numbers of a run as label and value, in a reading order, without long lists."""
    s = run.get("summary") or {}
    if run.get("command") == "learn":
        counts = s.get("counts") if isinstance(s.get("counts"), dict) else {}
        return [(label, f"{int(counts[key]):,}") for key, label in LEARN_COUNTS if isinstance(counts.get(key), int)]
    order = ["guides", "cards", "opened", "day_pages", "sheet_only", "pages", "review", "bid", "skip", "reused", "pending", "model_calls",
             "checked", "changed", "applied", "unchanged", "needs_fetch", "judged", "unavailable", "decisions",
             "approved", "snapshot", "nichibo", "carbarn", "signed_in_again", "dry_run",
             "lots", "ok", "failed", "low_confidence", "calls", "cached", "disagreements", "decision"]
    names = {"guides": "Guides", "cards": "Cards read", "opened": "Lots opened", "day_pages": "Lot pages read for the auction day", "sheet_only": "Judged from the sheet",
             "pages": "Pages opened", "review": "For review", "bid": "Bid", "skip": "Skipped", "reused": "Reused",
             "pending": "Images pending", "model_calls": "Model calls", "checked": "Checked", "changed": "Changed",
             "applied": "Stored", "unchanged": "Unchanged", "needs_fetch": "Need a page", "judged": "Judged",
             "unavailable": "Model unavailable", "decisions": "Decisions", "approved": "Approved", "snapshot": "Snapshot",
             "nichibo": "Nichibo", "carbarn": "Carbarn dashboard", "signed_in_again": "Signed in again during the scan",
             "dry_run": "Dry run",
             "lots": "Sheets", "ok": "Read", "failed": "Failed", "low_confidence": "Hard to read", "calls": "Paid reads",
             "cached": "From the cache", "disagreements": "Disagreements", "decision": "Decision"}
    out = []
    if run.get("command") in ("place", "bid") and isinstance(s.get("lines"), list):
        out.append(("Bids", str(len(s["lines"]))))
    for key in order:
        if key not in s:
            continue
        v = s[key]
        if isinstance(v, bool):
            v = ("signed in" if v else "signed out") if key in ("nichibo", "carbarn") else ("yes" if v else "no")
        elif isinstance(v, (list, dict)):
            v = len(v)
        elif isinstance(v, int):
            v = f"{v:,}"
        out.append((names[key], str(v)))
    errors = s.get("errors")
    if errors:
        out.append(("Errors", str(len(errors) if isinstance(errors, list) else errors)))
    return out


def entry_pairs(run: dict) -> list[tuple[str, str]]:
    """The few figures a log entry shows of a run: `summary_pairs` kept to the command's allow-list, at most three."""
    wanted = ENTRY_LABELS.get(run.get("command") or "", [])
    pairs = dict(summary_pairs(run))
    return [(label, pairs[label]) for label in wanted if label in pairs][:3]


def filter_label(command: str) -> str:
    """The Runs page's filter in words: a command's label, "Single steps" for every run that is not a mode, or nothing."""
    if command == "steps":
        return "Single steps"
    return command_label(command) if command else ""


def register(app, templates, cfg, jobs, store, page) -> None:
    """`store()` opens a Store; `page(request, template, nav, **context)` renders inside the shell."""
    # The address in config.yaml, and the names a test asks for (`review.allow_hosts`; config.yaml has none).
    allowed = (LOCAL_HOSTS | {str(cfg.review.get("host", "127.0.0.1")).lower()}
               | {str(h).lower() for h in cfg.review.get("allow_hosts", [])})
    templates.env.globals.update(
        ago=st.ago, local_time=st.local_time, duration=st.duration, command_label=command_label, options_text=options_text,
        summary_pairs=summary_pairs, entry_pairs=entry_pairs, options_words=options_words,
        command_icon=lambda name: st.COMMAND_ICONS.get(name, "terminal"),
        status_icon=lambda status: st.STATUS_ICONS.get(status, "circle"),
        status_word=lambda status: st.STATUS_WORDS.get(status, status),
        command_short=lambda name: st.COMMAND_SHORT.get(name, name),
        STEP_COMMANDS=STEP_COMMANDS, SHOWN_OPTIONS=SHOWN_OPTIONS)

    @app.middleware("http")
    async def this_pc_only(request: Request, call_next):
        why = local_only(request, allowed)
        if why:
            return JSONResponse({"detail": why}, status_code=403)
        return await call_next(request)

    def status() -> dict:
        s = store()
        try:
            return st.gather_status(cfg, s, jobs)
        finally:
            s.close()

    def paused_run_id() -> int | None:
        """The newest run of the paused session, for the run page's Resume (None: nothing is paused)."""
        s = store()
        try:
            row = s.paused_session()
        finally:
            s.close()
        return row["id"] if row else None

    templates.env.globals["paused_run_id"] = paused_run_id

    def run_or_404(run_id: int) -> dict:
        run = jobs.get(run_id)
        if run is None:
            raise HTTPException(404, f"no run {run_id}")
        return run

    # -- pages ---------------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        s = store()
        try:
            state = st.gather_status(cfg, s, jobs)
            log = st.day_log(s, jobs.list(limit=200))
        finally:
            s.close()
        return page(request, "overview.html", "overview", state=state, log=log, commands=jobs.describe(),
                    today=datetime.now().astimezone().strftime("%A %d %B"),
                    counts={k: state["counts"][k] for k in ("bid", "review", "approved", "placed")})

    @app.get("/runs", response_class=HTMLResponse)
    def runs_page(request: Request, command: str | None = None):
        """`?command=` narrows to one command; `steps` is every run that is not Learn or Bid."""
        if command and command != "steps" and command not in COMMANDS:
            raise HTTPException(404, "no such command")
        if command == "steps":
            runs = [r for r in jobs.list(limit=300) if r["command"] not in MODES][:100]
        else:
            runs = jobs.list(limit=100, command=command or None)
        return page(request, "runs.html", "runs", runs=runs, command=command or "", filter_label=filter_label(command or ""),
                    commands=jobs.describe(), current=jobs.current())

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: int):
        run = run_or_404(run_id)
        placements = []
        if run["command"] in ("place", "bid"):
            s = store()
            try:
                placements = st.placements_between(s, run["started_at"], run["finished_at"])
            finally:
                s.close()
        return page(request, "run.html", "runs", run=run, placements=placements)

    @app.get("/runs/{run_id}/log.txt")
    def run_log_file(run_id: int):
        run = run_or_404(run_id)
        path = Path(run.get("log_path") or "")
        if not run.get("log_path") or not path.is_file() or cfg.runs_dir.resolve() not in path.resolve().parents:
            raise HTTPException(404, "this run has no log file")
        return FileResponse(path, media_type="text/plain; charset=utf-8", filename=f"bidbarn-run-{run_id}.log")

    @app.get("/insights", response_class=HTMLResponse)
    def insights_page(request: Request, days: int | None = None, min_support: int = 2):
        days = days if days and 0 < days <= 3650 else None
        min_support = min(max(1, min_support), 50)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds") if days else None
        s = store()
        try:
            rows = s.decisions_with_facts(since=since)
            feedback = [dict(r) for r in s.judge_feedback()]
        finally:
            s.close()
        proposals: dict[str, list] = {}
        for p in propose_skip_lines(rows, min_support=min_support):
            proposals.setdefault(p.model_code, []).append(p)
        return page(request, "insights.html", "insights", insights=summarise(rows), proposals=proposals,
                    accuracy=model_accuracy(feedback), days=days, min_support=min_support, decisions=len(rows))

    @app.get("/config", response_class=HTMLResponse)
    def config_page(request: Request):
        dry, reason = cfg.placement_mode(False)
        providers = []
        if (cfg.sheet_check or "none") != "none":
            s = store()
            try:
                rows = st.judge_state(cfg, s)["providers"]  # name, model, rpm, rpd, calls today, share, level, has_key, vision
            finally:
                s.close()
            envs = {p.name: p.api_key_env for p in providers_for(cfg)}
            providers = [dict(row, key_env=envs.get(row["name"], "")) for row in rows]
        return page(request, "config.html", "config", cfg=cfg, providers=providers, placement_reason=reason,
                    paths=[("Browser profile", cfg.profile_dir), ("Database", cfg.db_path), ("Evidence", cfg.evidence_dir),
                           ("Run logs", cfg.runs_dir), ("Guide snapshots", cfg.snapshot_dir),
                           ("Standing instructions", cfg.override_dir), ("Playbook", cfg.playbook_dir)])

    # -- the API the pages use -------------------------------------------------------------------
    @app.get("/api/status")
    def api_status():
        return JSONResponse(status())

    @app.get("/api/commands")
    def api_commands():
        return JSONResponse(jobs.describe())

    @app.get("/api/runs")
    def api_runs(limit: int = 50, command: str | None = None):
        return JSONResponse(jobs.list(limit=min(max(1, limit), 500), command=command or None))

    @app.post("/api/runs", status_code=201)
    def api_start(body: dict = Body(...)):
        try:
            run = jobs.start(body.get("command"), body.get("options") or {})
        except BadRequest as exc:
            raise HTTPException(400, str(exc))
        except Busy as exc:
            return JSONResponse({"detail": str(exc), "run_id": getattr(exc, "run_id", None)}, status_code=409)
        return JSONResponse(run, status_code=201)

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: int):
        return JSONResponse(run_or_404(run_id))

    @app.get("/api/runs/{run_id}/log")
    def api_log(run_id: int, after: int = 0, limit: int = 2000):
        chunk = jobs.lines_after(run_id, after=after, wait=0.0, limit=limit)
        if chunk is None:
            raise HTTPException(404, f"no run {run_id}")
        return JSONResponse(chunk)

    @app.get("/api/runs/{run_id}/events")
    def api_events(run_id: int, request: Request, after: int = 0):
        """Server-sent events: `log` (one line; its id is the index of the next line), `progress`,
        `done`. A reconnect continues after the last line the page received."""
        run_or_404(run_id)
        last = request.headers.get("last-event-id") or ""
        try:  # str.isdigit() accepts "²", which int() refuses: a bad header is no header
            start = int(last) if last.isdigit() else max(0, after)
        except ValueError:
            start = max(0, after)

        def stream():
            index, told = start, object()
            while True:
                chunk = jobs.lines_after(run_id, after=index, wait=15.0, limit=500)
                if chunk is None:
                    return
                first = chunk["next"] - len(chunk["lines"])
                for offset, line in enumerate(chunk["lines"]):
                    yield f"id: {first + offset + 1}\nevent: log\ndata: {json.dumps(line)}\n\n"
                index = chunk["next"]
                if chunk["progress"] is not None and chunk["progress"] != told:
                    told = chunk["progress"]
                    yield f"event: progress\ndata: {json.dumps(told, default=str)}\n\n"
                if chunk["done"]:
                    end = {k: chunk.get(k) for k in ("status", "exit_code", "summary", "error")}
                    yield f"event: done\ndata: {json.dumps(end, default=str)}\n\n"
                    return
                if not chunk["lines"]:
                    yield ": keepalive\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    def api_cancel(run_id: int):
        run_or_404(run_id)
        try:
            jobs.cancel(run_id)
        except NotRunning as exc:
            raise HTTPException(409, str(exc))
        return JSONResponse({"status": "stopping"}, status_code=202)

    @app.post("/api/runs/{run_id}/pause", status_code=202)
    def api_pause(run_id: int):
        run_or_404(run_id)
        try:
            jobs.pause(run_id)
        except NotRunning as exc:
            raise HTTPException(409, str(exc))
        return JSONResponse({"status": "pausing"}, status_code=202)

    @app.post("/api/sessions/{session_id}/resume", status_code=201)
    def api_resume(session_id: int):
        """Resume: Bid mode again, in the paused session (the job manager joins it)."""
        s = store()
        try:
            paused = s.paused_session()
        finally:
            s.close()
        if paused is None or paused["session_id"] != session_id:
            raise HTTPException(409, "this session is not paused")
        try:
            run = jobs.start("bid", {})
        except Busy as exc:
            return JSONResponse({"detail": str(exc), "run_id": getattr(exc, "run_id", None)}, status_code=409)
        return JSONResponse(run, status_code=201)

    guide_pages.register(app, templates, cfg, store, page)  # /guides and /guides/{code}, behind the same middleware
