"""The dashboard's job runner: one BidBarn command at a time, each in its own process.

The page sends `{"command", "options"}`. The arguments are built here from the `COMMANDS`
table, so nothing the page sends reaches the command line as text: a command or option that
is not in the table is refused, model codes are checked letter by letter, and numbers are
numbers. The table has no way to ask for live placement, to approve anything, or to judge
lots that were judged before (CLAUDE.md, rules 0 and 1).

A run is `python -m auction_bot.cli ...` writing its own log file. A supervisor thread reads
the log back, keeps the `PROGRESS` and `SUMMARY` lines (`progress.py`) apart from the text,
and closes the run's row when the process ends. Closing the dashboard does not stop a run;
the next dashboard adopts what is still going and closes what is not.

Two files beside the log carry the page's two buttons to the run: `<id>.cancel` (Stop) and
`<id>.finish` (Finish, while a Bid run waits at the review; `review_waiting`, `finish_review`).

This module imports the store, the config, the progress protocol and the profile lock, and
nothing that loads Playwright: the dashboard can never hold the browser profile itself.
"""
from __future__ import annotations

import codecs
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from auction_bot import lock, progress
from auction_bot.config import ROOT
from auction_bot.store import Store

MAX_LINES = 20_000  # log lines kept in memory per running job; older ones are read from the file
MAX_INT = 100_000
MAX_LIST = 50
MODEL_CODE = re.compile(r"[A-Za-z0-9_-]{1,20}")
WORD = re.compile(r"[A-Za-z0-9_.-]{1,40}")
KINDS = ("bool", "int", "list", "str")
CLOSED_MESSAGE = "the dashboard was closed while this run was going"
# Placement from the dashboard is a dry run. The table below cannot produce the flag that
# asks for anything else; this is the net under it. The flag is put together when the module
# loads, so it is written nowhere in this package, not in the source and not in the bytecode.
_REFUSED = ("".join(("--", "live")),)


class Busy(RuntimeError):
    """A job is running already, or another run holds the browser profile."""

    def __init__(self, message: str, run_id: int | None = None, holder: dict | None = None):
        super().__init__(message)
        self.run_id = run_id  # the dashboard run in the way, when there is one
        self.holder = holder  # the profile lock's {"pid", "command", "started"}, when that is in the way


class BadRequest(ValueError):
    """The command or one of its options is not in the table, or has the wrong shape."""


class NotRunning(RuntimeError):
    """The run to stop is not the one running here."""


@dataclass(frozen=True)
class Opt:
    kind: str  # bool | int | list (of model codes) | str (one plain word)
    default: object = None
    label: str = ""
    help: str = ""
    choices: tuple[str, ...] = ()  # str only: the words allowed


@dataclass(frozen=True)
class Cmd:
    label: str
    needs_profile: bool  # opens the bot's browser, so only one at a time on this PC
    options: dict[str, Opt]
    argv: Callable[[dict], list[str]]
    help: str = ""


def _models(v: dict) -> list[str]:
    return [part for code in (v.get("model") or []) for part in ("-m", code)]


def _window(v: dict) -> list[str]:
    return ["--headless" if v.get("headless") is not False else "--no-headless"]


def _number(v: dict, name: str, flag: str) -> list[str]:
    return [flag, str(v[name])] if v.get(name) else []


def _scan(v: dict) -> list[str]:
    return ["scan", *_models(v), *_window(v), *_number(v, "limit", "--limit"),
            *(["--rescan"] if v.get("rescan") else []), *(["--fresh"] if v.get("fresh") else [])]


def _fetch_rules(v: dict) -> list[str]:
    return ["fetch-rules", *_models(v), "--headless"]


def _judge(v: dict) -> list[str]:
    return ["judge", "--pending", *_models(v), *_number(v, "limit", "--limit")]


def _replay(v: dict) -> list[str]:
    return ["replay", *_models(v), *(["--apply"] if v.get("apply") else [])]


def _place(v: dict) -> list[str]:
    return ["place", *_window(v)]


def _insights(v: dict) -> list[str]:
    return ["insights", *_models(v), *_number(v, "days", "--days"), *_number(v, "min_support", "--min-support")]


_MODEL = Opt("list", label="Model codes", help="Only these model codes, for example GDH206. Empty means every guide.")
_HIDDEN = Opt("bool", default=True, label="Hide the browser window", help="Off shows the bot's browser while it works.")

COMMANDS: dict[str, Cmd] = {
    "bid": Cmd(
        "Bid mode", True, {}, lambda v: ["bid", "--no-browser"],
        help="Checks the sign-in, fetches the guides, scans Nichibo, waits for your review, then places what you "
             "approve as a dry run. One run you can watch."),
    "learn": Cmd(
        "Learn mode", True, {}, lambda v: ["learn"],
        help="Opens the bot's browser for you: bid as usual and type why in the blue box. The bot only watches, "
             "then scans what you searched and reports lot by lot."),
    "scan": Cmd(
        "Scan Nichibo", True,
        {"model": _MODEL, "headless": _HIDDEN,
         "limit": Opt("int", label="Lots per model", help="Open at most this many lots per model code."),
         "rescan": Opt("bool", default=False, label="Re-evaluate known lots",
                       help="Also the lots that already have a pending or skipped proposal."),
         "fresh": Opt("bool", default=False, label="Ignore the cache",
                      help="Download every page again instead of reusing what was downloaded within cache_hours.")},
        _scan, help="Search Nichibo for every active guide, evaluate the lots, and write proposals for review."),
    "fetch-rules": Cmd(
        "Fetch the bidding guides", True, {"model": _MODEL}, _fetch_rules,
        help="Read the Team Bid guides from the Carbarn dashboard and save a snapshot."),
    "judge": Cmd(
        "Judge pending lots", True,
        {"model": _MODEL, "limit": Opt("int", label="Lots", help="Judge at most this many lots.")},
        _judge, help="Ask the free vision model about stored lots that still wait for a judgement. You confirm every lot."),
    "replay": Cmd(
        "Replay the rules", False,
        {"model": _MODEL,
         "apply": Opt("bool", default=False, label="Store the new outcomes",
                      help="Off only reports what would change.")},
        _replay, help="Re-apply the current rules to the stored proposals. No Nichibo traffic."),
    "place": Cmd(
        "Place approved bids (dry run)", True, {"headless": _HIDDEN}, _place,
        help="Types every approved bid into Nichibo's form and takes a screenshot. Nothing is submitted."),
    "insights": Cmd(
        "Insights", False,
        {"model": _MODEL,
         "days": Opt("int", label="Days", help="Only decisions from the last N days."),
         "min_support": Opt("int", label="Rejections per rule", help="How many rejections a suggested skip rule must explain.")},
        _insights, help="How you decide, per model, and the skip rules to consider."),
    "login": Cmd(
        "Sign in", True, {}, lambda v: ["login", "--wait"],
        help="Checks both sites. A browser window opens only if one is signed out, and it continues by itself."),
    "check-login": Cmd(
        "Check the sign-in", True, {}, lambda v: ["check-login"],
        help="Checks, without a window, whether Nichibo and the Carbarn dashboard are signed in."),
}


def _shown(value) -> str:
    return repr(value)[:40]


def _clean(name: str, opt: Opt, value):
    """The value as the command line may carry it, or BadRequest."""
    if opt.kind == "bool":
        if not isinstance(value, bool):
            raise BadRequest(f"{name} must be true or false")
        return value
    if opt.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise BadRequest(f"{name} must be a whole number")
        if not 1 <= value <= MAX_INT:
            raise BadRequest(f"{name} must be between 1 and {MAX_INT}")
        return value
    if opt.kind == "list":
        if not isinstance(value, (list, tuple)):
            raise BadRequest(f"{name} must be a list of model codes")
        if len(value) > MAX_LIST:
            raise BadRequest(f"{name} takes at most {MAX_LIST} model codes")
        codes: list[str] = []
        for item in value:
            if not isinstance(item, str) or item.startswith("-") or not MODEL_CODE.fullmatch(item):
                raise BadRequest(f"{name}: {_shown(item)} is not a model code")
            if item.upper() not in codes:
                codes.append(item.upper())
        return codes
    if opt.kind == "str":
        if not isinstance(value, str) or value.startswith("-") or not WORD.fullmatch(value):
            raise BadRequest(f"{name} must be one plain word")
        if opt.choices and value not in opt.choices:
            raise BadRequest(f"{name} must be one of {', '.join(opt.choices)}")
        return value
    raise BadRequest(f"{name} has an unknown kind")


def build_argv(command: str, options: dict | None = None, commands: dict[str, Cmd] = COMMANDS) -> list[str]:
    """The arguments after `auction_bot.cli` for a command of the table. BadRequest for anything else."""
    if not isinstance(command, str) or command not in commands:
        raise BadRequest(f"unknown command {_shown(command)}")
    cmd = commands[command]
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise BadRequest("options must be an object")
    values = {name: opt.default for name, opt in cmd.options.items()}
    for name, value in options.items():
        if not isinstance(name, str) or name not in cmd.options:
            raise BadRequest(f"{command} has no option {_shown(name)}")
        if value is not None:  # null means "not set": the default applies
            values[name] = _clean(name, cmd.options[name], value)
    argv = [str(part) for part in cmd.argv(values)]
    if any(part in _REFUSED for part in argv):
        raise BadRequest("the dashboard places bids as a dry run only")
    return argv


def describe(commands: dict[str, Cmd] = COMMANDS) -> list[dict]:
    """The table as plain data, for the page's forms."""
    return [{"name": name, "label": cmd.label, "help": cmd.help, "needs_profile": cmd.needs_profile,
             "options": [{"name": key, "kind": opt.kind, "default": opt.default, "label": opt.label or key, "help": opt.help,
                          **({"choices": list(opt.choices)} if opt.choices else {})}
                         for key, opt in cmd.options.items()]}
            for name, cmd in commands.items()]


# -- reading runs back -----------------------------------------------------------------------

def _loads(text, kind: type):
    if not text:
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, kind) else None


def run_dict(row) -> dict:
    """A `runs` row as plain data: `args`, `progress` and `summary` parsed, no JSON text."""
    d = dict(row)
    d["args"] = _loads(d.pop("args_json", None), list) or []
    d["progress"] = _loads(d.pop("progress_json", None), dict)
    d["summary"] = _loads(d.pop("summary_json", None), dict)
    d["cancel_requested"] = bool(d.get("cancel_requested_at"))
    return d


def split_lines(text: str) -> list[str]:
    """Lines as the supervisor counts them: split at the line feed, a final piece without one is a line too."""
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return [p.rstrip("\r") for p in parts]


def read_log(path) -> list[str]:
    """The text lines of a run's log, without the PROGRESS and SUMMARY lines. Empty when the file is gone."""
    if not path:
        return []
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    return [ln for ln in split_lines(data.decode("utf-8", errors="replace")) if progress.parse(ln) is None]


def _window_of(after, limit) -> tuple[int, int]:
    try:
        after, limit = int(after), int(limit)
    except (TypeError, ValueError):
        after, limit = 0, 2000
    return max(0, after), max(1, min(limit, 10_000))


def _page(lines: list[str], base: int, after: int, limit: int) -> tuple[list[str], int, int]:
    """(the lines from `after`, the next index, the index after the last line). `base` is the index of lines[0]."""
    end = base + len(lines)
    after = min(max(after, base), end)
    chunk = lines[after - base: after - base + limit]
    return chunk, after + len(chunk), end


class _Runs:
    """What both managers share: reading runs from the store and their logs from the files."""

    def __init__(self, cfg, commands: dict[str, Cmd] = COMMANDS):
        self.cfg = cfg
        self.commands = commands

    def _store(self) -> Store:
        return Store(self.cfg.db_path)  # one per call: these methods run on any thread

    def _row(self, run_id: int) -> dict | None:
        s = self._store()
        try:
            row = s.run(int(run_id))
        finally:
            s.close()
        return run_dict(row) if row is not None else None

    def describe(self) -> list[dict]:
        return describe(self.commands)

    def current(self) -> dict | None:
        return None

    def review_waiting(self) -> dict | None:
        """The current run while it is Bid mode waiting at the review stage (its Finish button may be shown), else None."""
        cur = self.current()
        if not cur or cur.get("command") != "bid" or cur.get("cancel_requested"):
            return None
        p = cur.get("progress") or {}
        if p.get("stage") != "review" or p.get("waiting") is False:
            return None
        return cur

    def get(self, run_id: int) -> dict | None:
        return self._row(run_id)

    def list(self, limit: int = 50, command: str | None = None) -> list[dict]:
        s = self._store()
        try:
            rows = s.list_runs(limit=limit, command=command)
        finally:
            s.close()
        return [self._overlay(run_dict(r)) for r in rows]

    def _overlay(self, run: dict) -> dict:
        return run

    def lines_after(self, run_id: int, after: int = 0, wait: float = 0.0, limit: int = 2000) -> dict | None:
        return self._lines_from_file(run_id, after, wait, limit)

    def _lines_from_file(self, run_id: int, after: int, wait: float, limit: int) -> dict | None:
        after, limit = _window_of(after, limit)
        deadline = time.monotonic() + max(0.0, min(float(wait or 0), 60.0))
        while True:
            run = self._row(run_id)
            if run is None:
                return None
            chunk, nxt, end = _page(read_log(run.get("log_path")), 0, after, limit)
            running = run["status"] == "running"
            if chunk or not running or time.monotonic() >= deadline:
                return {"lines": chunk, "next": nxt, "done": not running and nxt >= end, "status": run["status"],
                        "progress": run["progress"], "summary": run["summary"], "exit_code": run["exit_code"],
                        "error": run["error"]}
            time.sleep(0.25)  # a run that is going somewhere else: look again until the wait is over


class DisabledJobManager(_Runs):
    """For the review page inside `bot bid`: shows the history, starts nothing."""

    def start(self, command: str, options: dict | None = None) -> dict:
        raise Busy("this review was started by bot bid; open dashboard.cmd to run commands")

    def cancel(self, run_id: int) -> None:
        raise NotRunning("this review was started by bot bid; it runs no commands")

    def finish_review(self, run_id: int) -> None:
        raise NotRunning("this review was started by bot bid; its own Finish button ends it")

    def close(self) -> None:
        pass


# -- running them ------------------------------------------------------------------------------

class _Tail:
    """Reads what was added to a log since the last look and returns the complete lines."""

    def __init__(self, path: Path):
        self.path = path
        self.pos = 0
        self.rest = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def read(self, final: bool = False) -> list[str]:
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read()
        except OSError:
            data = b""
        self.pos += len(data)
        text = self.rest + self.decoder.decode(data, final=final)
        if final:
            self.rest = ""
            return split_lines(text)
        head, sep, self.rest = text.rpartition("\n")
        return split_lines(head + sep) if sep else []


class _Job:
    def __init__(self, run_id: int, command: str, args: list[str], started_at: str, log_path: Path, cancel_path: Path,
                 proc: subprocess.Popen | None = None, pid: int | None = None, adopted: bool = False):
        self.id = run_id
        self.command = command
        self.args = args
        self.started_at = started_at
        self.log_path = log_path
        self.cancel_path = cancel_path
        self.finish_path = cancel_path.with_suffix(".finish")  # written by Finish while a Bid run waits at the review
        self.proc = proc
        self.pid = pid
        self.adopted = adopted  # found running when the dashboard started: watched, never killed
        self.lines: list[str] = []
        self.base = 0  # index of lines[0] among all the text lines of the log
        self.progress: dict | None = None
        self.summary: dict | None = None
        self.cancel_requested = False
        self.cancel_at = 0.0
        self.killed = False
        self.stopped_text: str | None = None  # what followed the last "STOPPED:"
        self.last_text = ""  # the last line that was not empty
        self.done = False
        self.status = "running"
        self.exit_code: int | None = None
        self.error: str | None = None
        self.thread: threading.Thread | None = None


class JobManager(_Runs):
    def __init__(self, cfg, commands: dict[str, Cmd] = COMMANDS, grace: float = 20.0, python: str | None = None,
                 reconcile: bool = True):
        super().__init__(cfg, commands)
        self.grace = float(grace)
        self.python = python or self._default_python()
        self._cond = threading.Condition()
        self._starting = threading.Lock()
        self._closing = threading.Event()
        self._jobs: dict[int, _Job] = {}
        if reconcile:
            self._reconcile()

    @staticmethod
    def _default_python() -> str:
        """python.exe beside the interpreter, so that a dashboard under pythonw starts console programs."""
        if sys.platform == "win32":
            exe = Path(sys.executable).with_name("python.exe")
            if exe.is_file():
                return str(exe)
        return sys.executable

    # -- starting --------------------------------------------------------------
    def _spawn(self, argv: list[str], log_path: Path, env: dict) -> subprocess.Popen:
        """Start the command with its output going to the log. Tests replace this with a harmless script."""
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if sys.platform == "win32" else 0
        with open(log_path, "ab") as out:  # the child keeps its own handle; ours is closed right after
            return subprocess.Popen([self.python, "-X", "utf8", "-u", "-m", "auction_bot.cli", *argv], cwd=str(ROOT),
                                    stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                    creationflags=flags)

    def _env(self, run_id: int, command: str, cancel_path: Path, finish_path: Path | None = None) -> dict:
        env = dict(os.environ)
        env.update({"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "BIDBARN_RUN_ID": str(run_id), "BIDBARN_PROGRESS": "1",
                    "BIDBARN_COMMAND": command, "BIDBARN_CANCEL_FILE": str(cancel_path),
                    "BIDBARN_FINISH_FILE": str(finish_path or cancel_path.with_suffix(".finish"))})
        return env

    def start(self, command: str, options: dict | None = None) -> dict:
        argv = build_argv(command, options, self.commands)
        cmd = self.commands[command]
        with self._starting:
            live = self._live()
            if live is not None:
                raise Busy(f"{live.command} is running (run {live.id})", run_id=live.id)
            if cmd.needs_profile:
                held = lock.holder(self.cfg)
                if held is not None:
                    raise Busy(lock.busy_message(held), holder=held)
            runs_dir = self.cfg.runs_dir
            s = self._store()
            try:
                run_id = s.start_run(command, argv)
                log_path, cancel_path = runs_dir / f"{run_id}.log", runs_dir / f"{run_id}.cancel"
                finish_path = runs_dir / f"{run_id}.finish"
                s.set_run_log(run_id, log_path)
                cancel_path.unlink(missing_ok=True)
                finish_path.unlink(missing_ok=True)
                try:
                    proc = self._spawn(argv, log_path, self._env(run_id, command, cancel_path, finish_path))
                except Exception as exc:
                    s.finish_run(run_id, "failed", error=f"could not start: {exc}"[:500])
                    raise RuntimeError(f"could not start {command}: {exc}") from exc
                s.set_run_pid(run_id, proc.pid)
                started_at = s.run(run_id)["started_at"]
            finally:
                s.close()
            job = _Job(run_id, command, argv, started_at, log_path, cancel_path, proc=proc, pid=proc.pid)
            self._watch(job)
        return self.get(run_id)

    def _watch(self, job: _Job) -> None:
        with self._cond:
            self._jobs[job.id] = job
        job.thread = threading.Thread(target=self._supervise, args=(job,), name=f"bidbarn-run-{job.id}", daemon=True)
        job.thread.start()

    def _live(self) -> _Job | None:
        with self._cond:
            going = [j for j in self._jobs.values() if not j.done]
            return max(going, key=lambda j: j.id) if going else None

    # -- looking ---------------------------------------------------------------
    def current(self) -> dict | None:
        job = self._live()
        if job is None:
            return None
        with self._cond:
            return {"id": job.id, "command": job.command, "args": list(job.args), "started_at": job.started_at,
                    "progress": job.progress, "cancel_requested": job.cancel_requested}

    def _overlay(self, run: dict) -> dict:
        """A running job's progress and summary are newer in memory than in its row."""
        with self._cond:
            job = self._jobs.get(run["id"])
            if job is not None:
                run["progress"] = job.progress if job.progress is not None else run["progress"]
                run["summary"] = job.summary if job.summary is not None else run["summary"]
                run["cancel_requested"] = run["cancel_requested"] or job.cancel_requested
        return run

    def get(self, run_id: int) -> dict | None:
        run = self._row(run_id)
        return self._overlay(run) if run is not None else None

    def lines_after(self, run_id: int, after: int = 0, wait: float = 0.0, limit: int = 2000) -> dict | None:
        try:
            run_id = int(run_id)
        except (TypeError, ValueError):
            return None
        after, limit = _window_of(after, limit)
        with self._cond:
            job = self._jobs.get(run_id)
            if job is not None:
                if after >= job.base + len(job.lines) and not job.done and wait and wait > 0:
                    self._cond.wait(timeout=min(float(wait), 60.0))  # woken by new lines, new progress or the end
                if after >= job.base:
                    chunk, nxt, end = _page(job.lines, job.base, after, limit)
                    return {"lines": list(chunk), "next": nxt, "done": job.done and nxt >= end, "status": job.status,
                            "progress": job.progress, "summary": job.summary, "exit_code": job.exit_code,
                            "error": job.error}
        # Not running here, or so far back that memory no longer has the lines: the file has them all.
        return self._lines_from_file(run_id, after, 0.0 if job is not None else wait, limit)

    # -- stopping --------------------------------------------------------------
    def cancel(self, run_id: int) -> None:
        """Ask the run to stop at its next check point. After `grace` seconds its process is ended."""
        with self._cond:
            job = self._jobs.get(int(run_id))
            if job is None or job.done:
                raise NotRunning(f"run {run_id} is not running")
        job.cancel_path.parent.mkdir(parents=True, exist_ok=True)
        job.cancel_path.write_text("stop\n", encoding="utf-8")
        with self._cond:
            if not job.cancel_requested:
                job.cancel_requested = True
                job.cancel_at = time.monotonic()
            self._cond.notify_all()
        s = self._store()
        try:
            s.request_run_cancel(job.id)
        finally:
            s.close()

    def finish_review(self, run_id: int) -> None:
        """Finish on the review page: tell the Bid run waiting there to go on and place the approved bids.
        Only that run, and only while it waits; anything else is `NotRunning`."""
        cur = self.review_waiting()
        if cur is None or int(run_id) != cur["id"]:
            raise NotRunning(f"run {run_id} is not waiting at the review")
        with self._cond:
            job = self._jobs.get(cur["id"])
        if job is None or job.done:
            raise NotRunning(f"run {run_id} is not running")
        job.finish_path.parent.mkdir(parents=True, exist_ok=True)
        job.finish_path.write_text("finish\n", encoding="utf-8")

    def _kill(self, job: _Job) -> None:
        """The run did not stop by itself: end its process and, on Windows, the ones it started (Chromium)."""
        proc = job.proc
        if proc is None or proc.poll() is not None:
            return
        if sys.platform == "win32":
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            try:
                subprocess.run([str(taskkill), "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            if proc.poll() is None:
                proc.kill()
        except OSError:
            pass

    # -- the supervisor thread -----------------------------------------------------
    def _supervise(self, job: _Job) -> None:
        tail = _Tail(job.log_path)
        saved, saved_at = None, 0.0
        while not self._closing.is_set():
            self._take(job, tail.read())
            now = time.monotonic()
            if job.progress is not None and job.progress is not saved and now - saved_at >= 1.0:
                if self._save_progress(job):
                    saved, saved_at = job.progress, now
            if self._ended(job):
                self._take(job, tail.read(final=True))
                self._finish(job)
                return
            if job.cancel_requested and not job.adopted and not job.killed and now - job.cancel_at >= self.grace:
                job.killed = True
                self._kill(job)
            self._closing.wait(0.25)

    def _ended(self, job: _Job) -> bool:
        if job.proc is not None:
            return job.proc.poll() is not None
        return not lock.pid_alive(job.pid)

    def _take(self, job: _Job, lines: list[str]) -> None:
        if not lines:
            return
        with self._cond:
            for line in lines:
                parsed = progress.parse(line)
                if parsed is not None:
                    if parsed[0] == "progress":
                        job.progress = parsed[1]
                    else:
                        job.summary = parsed[1]
                    continue
                job.lines.append(line)
                text = line.strip()
                if text:
                    job.last_text = text
                    if text.startswith("STOPPED:"):
                        job.stopped_text = text[len("STOPPED:"):].strip()
            extra = len(job.lines) - MAX_LINES
            if extra > 0:
                del job.lines[:extra]
                job.base += extra
            self._cond.notify_all()

    def _save_progress(self, job: _Job) -> bool:
        try:
            s = self._store()
            try:
                s.update_run_progress(job.id, job.progress)
            finally:
                s.close()
            return True
        except sqlite3.Error:
            return False  # the database is busy: the next second tries again

    def _outcome(self, job: _Job, code: int | None) -> tuple[str, str | None]:
        if job.cancel_requested:
            return "stopped", None
        if job.adopted:  # no exit code for a process this dashboard did not start: the log tells
            if job.summary is not None:
                return "ok", None
            if job.stopped_text is not None:
                return "failed", job.stopped_text[:500] or "stopped"
            return "stopped", CLOSED_MESSAGE
        if code == 0:
            return "ok", None
        text = job.stopped_text if job.stopped_text is not None else job.last_text
        return "failed", (text or f"exit code {code}")[:500]

    def _finish(self, job: _Job) -> None:
        code = job.proc.poll() if job.proc is not None else None
        status, error = self._outcome(job, code)
        for attempt in range(5):
            try:
                s = self._store()
                try:
                    if job.progress is not None:
                        s.update_run_progress(job.id, job.progress)
                    s.finish_run(job.id, status, exit_code=code, summary=job.summary, error=error)
                finally:
                    s.close()
                break
            except sqlite3.Error:
                time.sleep(0.5)  # still busy after the timeout: the row stays "running" until the next dashboard start
        for path in (job.cancel_path, job.finish_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        with self._cond:
            job.status, job.exit_code, job.error, job.done = status, code, error, True
            self._jobs.pop(job.id, None)  # from here on the row and the file are the record
            self._cond.notify_all()

    # -- runs left over from an earlier dashboard ---------------------------------------
    def _reconcile(self) -> None:
        going: list[dict] = []
        s = self._store()
        try:
            for run in [run_dict(r) for r in s.running_runs()]:
                if self._still_going(run):
                    going.append(run)
                    continue
                s.finish_run(run["id"], "stopped", error=CLOSED_MESSAGE)
                for suffix in (".cancel", ".finish"):
                    try:
                        (self.cfg.runs_dir / f"{run['id']}{suffix}").unlink(missing_ok=True)
                    except OSError:
                        pass
        finally:
            s.close()
        for run in going:  # watched to its end; adoption never ends a process
            log_path = Path(run["log_path"]) if run.get("log_path") else self.cfg.runs_dir / f"{run['id']}.log"
            job = _Job(run["id"], run["command"], run["args"], run["started_at"], log_path,
                       self.cfg.runs_dir / f"{run['id']}.cancel", pid=run["pid"], adopted=True)
            job.cancel_requested = run["cancel_requested"]
            self._watch(job)

    @staticmethod
    def _still_going(run: dict) -> bool:
        """Is the run's process alive, and is it the same process (ids are handed out again, surely after a restart)?"""
        pid = run.get("pid")
        if not pid or not lock.pid_alive(pid):
            return False
        created = lock.process_started(pid)
        try:
            started = datetime.fromisoformat(run["started_at"]).timestamp()
        except (TypeError, ValueError):
            return True
        return created is None or started - 5 <= created <= started + 120

    def close(self) -> None:
        """Stop watching (tests, shutdown). The runs themselves are left alone."""
        self._closing.set()
        with self._cond:
            jobs = list(self._jobs.values())
            self._cond.notify_all()
        for job in jobs:
            if job.thread is not None and job.thread is not threading.current_thread():
                job.thread.join(timeout=3)
