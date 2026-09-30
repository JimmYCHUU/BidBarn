"""The dashboard's job runner (with harmless scripts in place of the CLI) and its allow-list of commands."""
import ast
import itertools
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from auction_bot import lock
from auction_bot.config import Config
from auction_bot.dashboard import jobs
from auction_bot.dashboard.jobs import (COMMANDS, BadRequest, Busy, Cmd, DisabledJobManager, JobManager, NotRunning, Opt,
                                        build_argv, describe)
from auction_bot.store import Store

SRC = Path(__file__).resolve().parents[1] / "src"
DASHBOARD = SRC / "auction_bot" / "dashboard"

OK = r'''
import json
print("first line")
print("PROGRESS " + json.dumps({"stage": "scan", "guide": 1, "guides": 2}))
print("second line: 乗車定員 → 8")
print("PROGRESS " + json.dumps({"stage": "scan", "guide": 2, "guides": 2}))
print("")
print("SUMMARY " + json.dumps({"guides": 2, "review": 3}))
print("last line")
'''
WAIT = r'''
import os, sys, time
flag = os.environ["BIDBARN_CANCEL_FILE"]
print("waiting for run " + os.environ["BIDBARN_RUN_ID"] + " of " + os.environ["BIDBARN_COMMAND"], flush=True)
end = time.time() + 30
while not os.path.exists(flag) and time.time() < end:
    time.sleep(0.05)
print("")
print("STOPPED: stopped from the dashboard")
sys.exit(1)
'''
FAIL = 'import sys\nprint("working")\nprint("")\nprint("STOPPED: x")\nsys.exit(1)\n'
CRASH = 'print("working")\nraise RuntimeError("boom")\n'
SLOW = 'import time\nprint("one", flush=True)\ntime.sleep(0.8)\nprint("two", flush=True)\n'
DEAF = 'import time\nprint("not listening", flush=True)\ntime.sleep(60)\n'
LATER = 'import json, time\nprint("working", flush=True)\ntime.sleep(1.5)\nprint("SUMMARY " + json.dumps({"guides": 1}))\n'
REVIEW = r'''
import json, os, sys, time
finish = os.environ["BIDBARN_FINISH_FILE"]
print("stale finish file: " + ("yes" if os.path.exists(finish) else "no"), flush=True)
print("PROGRESS " + json.dumps({"stage": "review", "pending": 2, "approved": 1, "waiting": True}), flush=True)
end = time.time() + 30
while not os.path.exists(finish) and not os.path.exists(os.environ["BIDBARN_CANCEL_FILE"]) and time.time() < end:
    time.sleep(0.05)
if os.path.exists(os.environ["BIDBARN_CANCEL_FILE"]):
    print("STOPPED: stopped from the dashboard")
    sys.exit(1)
print("PROGRESS " + json.dumps({"stage": "review", "pending": 2, "approved": 1, "waiting": False}), flush=True)
os.remove(finish)
print("SUMMARY " + json.dumps({"approved": 1, "lines": ["x"]}))
'''
TOUCH = r'''
import os
path = os.environ["BIDBARN_FINISH_FILE"]
print("finish file: " + path)
print("stale finish file: " + ("yes" if os.path.exists(path) else "no"))
open(path, "w").write("left behind")
'''
SCRIPTS = {"ok": OK, "wait": WAIT, "fail": FAIL, "crash": CRASH, "slow": SLOW, "deaf": DEAF, "later": LATER,
           "bid": REVIEW, "scan": REVIEW, "touch": TOUCH}


def table(needs_profile: bool = False) -> dict[str, Cmd]:
    return {name: Cmd(name, needs_profile, {}, lambda v, s=script: ["-c", s]) for name, script in SCRIPTS.items()}


def python(script: str, log_path: Path, env: dict) -> subprocess.Popen:
    with open(log_path, "ab") as out:
        return subprocess.Popen([sys.executable, "-X", "utf8", "-u", "-c", script], stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env)


class ScriptManager(JobManager):
    """Runs the table's argv with plain python: no CLI, no browser."""

    def _spawn(self, argv, log_path, env):
        with open(log_path, "ab") as out:
            return subprocess.Popen([self.python, "-X", "utf8", "-u", *argv], stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env)


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = Config()
    c.db_path = tmp_path / "bot.sqlite"
    c.evidence_dir = tmp_path / "evidence"
    return c


@pytest.fixture
def make(cfg):
    made, started = [], []

    def factory(cls=ScriptManager, **kw):
        kw.setdefault("commands", table())
        m = cls(cfg, **kw)
        made.append(m)
        return m

    factory.started = started  # processes a test started by hand
    yield factory
    for m in made:
        for job in list(m._jobs.values()):
            if job.proc is not None and job.proc.poll() is None:
                job.proc.kill()
        m.close()
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=30)


def finished(m, run_id: int, seconds: float = 30.0) -> dict:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        run = m.get(run_id)
        if run["status"] != "running" and m.current() is None:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} is still going: {m.get(run_id)}")


def seen(m, run_id: int, text: str, seconds: float = 30.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if any(text in ln for ln in m.lines_after(run_id, wait=0.2)["lines"]):
            return
    raise AssertionError(f"{text!r} never appeared: {m.lines_after(run_id)}")


# -- runs ------------------------------------------------------------------------------------------

def test_ok_run_keeps_text_progress_and_summary_apart(cfg, make):
    m = make()
    assert m.current() is None
    run = m.start("ok")
    assert run["id"] == 1 and run["command"] == "ok" and run["status"] == "running" and run["args"] == ["-c", OK]
    assert isinstance(run["pid"], int) and run["source"] == "dashboard" and run["started_at"]
    assert Path(run["log_path"]) == cfg.runs_dir / "1.log"
    done = finished(m, 1)
    assert done["status"] == "ok" and done["exit_code"] == 0 and done["error"] is None and done["finished_at"]
    assert done["progress"] == {"stage": "scan", "guide": 2, "guides": 2} and done["summary"] == {"guides": 2, "review": 3}
    assert not done["cancel_requested"] and "args_json" not in done and "summary_json" not in done

    raw = (cfg.runs_dir / "1.log").read_text(encoding="utf-8")
    assert raw.count("PROGRESS ") == 2 and raw.count("SUMMARY ") == 1  # the file holds everything the run printed
    page = m.lines_after(1)
    assert page["lines"] == ["first line", "second line: 乗車定員 → 8", "", "last line"]
    assert page == {"lines": page["lines"], "next": 4, "done": True, "status": "ok", "progress": done["progress"],
                    "summary": done["summary"], "exit_code": 0, "error": None}
    assert not (cfg.runs_dir / "1.cancel").exists() and m.current() is None
    store = Store(cfg.db_path)
    try:  # the row itself holds the end state, for the next dashboard
        row = store.run(1)
        assert row["status"] == "ok" and json.loads(row["summary_json"]) == {"guides": 2, "review": 3}
        assert json.loads(row["progress_json"])["guide"] == 2 and json.loads(row["args_json"]) == ["-c", OK]
    finally:
        store.close()
    assert [r["id"] for r in m.list()] == [1] and m.list(command="scan") == []


def test_lines_after_pages_through_a_finished_run(make):
    m = make()
    m.start("ok")
    finished(m, 1)
    assert m.lines_after(1, after=0, limit=1) == {**m.lines_after(1, limit=1), "lines": ["first line"], "next": 1, "done": False}
    page = m.lines_after(1, after=1, limit=2)
    assert page["lines"] == ["second line: 乗車定員 → 8", ""] and page["next"] == 3 and not page["done"]
    page = m.lines_after(1, after=3, limit=2)
    assert page["lines"] == ["last line"] and page["next"] == 4 and page["done"] and page["status"] == "ok"
    page = m.lines_after(1, after=4)
    assert page["lines"] == [] and page["next"] == 4 and page["done"]
    assert m.lines_after(1, after=99)["next"] == 4  # past the end: back to the end
    assert m.lines_after(1, after=-5)["lines"][0] == "first line"
    assert m.lines_after(999) is None and m.get(999) is None


def test_a_missing_log_is_an_empty_one(cfg, make):
    m = make()
    m.start("ok")
    finished(m, 1)
    (cfg.runs_dir / "1.log").unlink()
    assert m.lines_after(1) == {**m.lines_after(1), "lines": [], "next": 0, "done": True, "status": "ok"}


def test_lines_after_waits_for_a_running_job(make):
    m = make()
    m.start("slow")
    got, after, calls, began = [], 0, 0, time.monotonic()
    while True:
        page = m.lines_after(1, after=after, wait=5)
        calls += 1
        got += page["lines"]
        after = page["next"]
        if page["done"]:
            break
        assert time.monotonic() - began < 30
    assert got == ["one", "two"] and page["status"] == "ok" and page["exit_code"] == 0
    assert calls < 100  # it slept until something happened instead of asking again and again
    assert finished(m, 1)["status"] == "ok"


def test_cancel_stops_a_run_that_listens(cfg, make):
    m = make()
    m.start("wait")
    seen(m, 1, "waiting for run 1 of wait")  # the run got its id and its name
    now = m.current()
    assert now == {"id": 1, "command": "wait", "args": ["-c", WAIT], "started_at": now["started_at"], "progress": None,
                   "cancel_requested": False}
    with pytest.raises(NotRunning):
        m.cancel(2)
    m.cancel(1)
    assert (m.current() or {"cancel_requested": True})["cancel_requested"]
    done = finished(m, 1)
    assert done["status"] == "stopped" and done["error"] is None and done["exit_code"] == 1
    assert done["cancel_requested"] and done["cancel_requested_at"]
    assert m.lines_after(1)["lines"][-1] == "STOPPED: stopped from the dashboard"
    assert not (cfg.runs_dir / "1.cancel").exists()  # tidied away
    with pytest.raises(NotRunning):
        m.cancel(1)


def test_a_run_that_does_not_listen_is_ended_after_the_grace(make):
    m = make(grace=0.3)
    m.start("deaf")
    seen(m, 1, "not listening")
    pid = m.get(1)["pid"]
    m.cancel(1)
    done = finished(m, 1)
    assert done["status"] == "stopped" and done["exit_code"] not in (0, None)
    assert not lock.pid_alive(pid)


def test_failed_run_keeps_the_reason(make):
    m = make()
    m.start("fail")
    done = finished(m, 1)
    assert done["status"] == "failed" and done["error"] == "x" and done["exit_code"] == 1 and done["summary"] is None
    m.start("crash")
    done = finished(m, 2)
    assert done["status"] == "failed" and done["error"] == "RuntimeError: boom" and done["exit_code"] == 1
    assert any("Traceback" in ln for ln in m.lines_after(2)["lines"])


def test_one_job_at_a_time(make):
    m = make()
    m.start("wait")
    with pytest.raises(Busy) as err:
        m.start("ok")
    assert str(err.value) == "wait is running (run 1)" and err.value.run_id == 1
    assert [r["id"] for r in m.list()] == [1]  # the refused one left no row
    m.cancel(1)
    finished(m, 1)
    assert m.start("ok")["id"] == 2
    assert finished(m, 2)["status"] == "ok"


def test_a_held_profile_refuses_commands_that_need_it(cfg, make):
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    make.started.append(sleeper)
    lock.lock_path(cfg).write_text(json.dumps({"pid": sleeper.pid, "command": "scan",
                                               "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}),
                                   encoding="utf-8")
    m = make(commands=table(needs_profile=True))
    with pytest.raises(Busy) as err:
        m.start("ok")
    assert str(err.value).startswith(f"another BidBarn run (scan, pid {sleeper.pid}, started ")
    assert err.value.holder["pid"] == sleeper.pid and err.value.run_id is None and m.list() == []
    assert sleeper.poll() is None  # refused, not ended
    offline = make(commands=table(needs_profile=False))  # replay and insights do not need the browser
    assert finished(offline, offline.start("ok")["id"])["status"] == "ok"


def test_unknown_command_and_failed_start(cfg, make):
    m = make()
    with pytest.raises(BadRequest):
        m.start("judge")  # not in this manager's table
    with pytest.raises(BadRequest):
        m.start("ok", {"live": True})

    class NoPython(ScriptManager):
        def _spawn(self, argv, log_path, env):
            raise OSError("python.exe is blocked")

    broken = make(NoPython)
    with pytest.raises(RuntimeError, match="could not start ok: python.exe is blocked"):
        broken.start("ok")
    run = broken.get(1)
    assert run["status"] == "failed" and "python.exe is blocked" in run["error"] and broken.current() is None


def test_the_real_spawn_runs_the_cli(cfg, make):
    """The line the dashboard really starts, with the one argument that does nothing: --help."""
    m = make(JobManager, commands={"help": Cmd("Help", False, {}, lambda v: ["--help"])})
    assert Path(m.python).name.lower().startswith("python")
    run = m.start("help")
    done = finished(m, run["id"], seconds=60)
    text = "\n".join(m.lines_after(run["id"])["lines"])
    assert done["status"] == "ok" and done["exit_code"] == 0, text
    for command in COMMANDS:
        assert command in text, text  # every command the dashboard offers exists in the CLI


# -- runs left over from an earlier dashboard ------------------------------------------------------------

def test_reconcile_closes_runs_whose_process_is_gone(cfg, make):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=30)
    make.started.append(gone)  # kept, so that its id is not handed out again
    store = Store(cfg.db_path)
    try:
        a = store.start_run("scan", ["scan", "--headless"], pid=gone.pid)
        b = store.start_run("replay", ["replay"])  # never got a process
        c = store.start_run("insights", ["insights"], pid=gone.pid)
        store.finish_run(c, "ok", exit_code=0)
    finally:
        store.close()
    (cfg.runs_dir / f"{a}.cancel").write_text("stop", encoding="utf-8")
    (cfg.runs_dir / f"{a}.finish").write_text("finish", encoding="utf-8")
    untouched = make(reconcile=False)
    assert untouched.get(a)["status"] == "running"
    m = make()
    for run_id in (a, b):
        run = m.get(run_id)
        assert run["status"] == "stopped" and run["finished_at"] and run["exit_code"] is None
        assert run["error"] == "the dashboard was closed while this run was going"
    assert m.get(c)["status"] == "ok" and m.current() is None
    assert not (cfg.runs_dir / f"{a}.cancel").exists() and not (cfg.runs_dir / f"{a}.finish").exists()
    assert m.start("ok")["id"] == 4  # nothing is in the way


def _left_running(cfg, make, script: str) -> int:
    """A run as an earlier dashboard left it: a row, a log, a process that is still going."""
    store = Store(cfg.db_path)
    try:
        run_id = store.start_run("scan", ["scan", "--headless"])
        log_path = cfg.runs_dir / f"{run_id}.log"
        env = dict(os.environ, BIDBARN_CANCEL_FILE=str(cfg.runs_dir / f"{run_id}.cancel"), BIDBARN_RUN_ID=str(run_id),
                   BIDBARN_COMMAND="scan", BIDBARN_PROGRESS="1")
        proc = python(script, log_path, env)
        make.started.append(proc)
        store.set_run_log(run_id, log_path)
        store.set_run_pid(run_id, proc.pid)
    finally:
        store.close()
    return run_id


def test_reconcile_adopts_a_run_that_is_still_going(cfg, make):
    run_id = _left_running(cfg, make, LATER)
    proc = make.started[-1]
    m = make()
    now = m.current()
    assert now is not None and now["id"] == run_id and now["command"] == "scan"
    with pytest.raises(Busy, match=f"scan is running \\(run {run_id}\\)"):
        m.start("ok")
    assert proc.poll() is None  # adopting it did not end it
    seen(m, run_id, "working")
    done = finished(m, run_id)
    assert done["status"] == "ok" and done["summary"] == {"guides": 1} and done["exit_code"] is None
    assert proc.wait(timeout=30) == 0


def test_an_adopted_run_can_be_asked_to_stop_but_is_never_ended(cfg, make):
    run_id = _left_running(cfg, make, WAIT)
    m = make(grace=0.1)
    seen(m, run_id, "waiting for run")
    m.cancel(run_id)
    done = finished(m, run_id)
    assert done["status"] == "stopped" and done["cancel_requested_at"]
    assert make.started[-1].wait(timeout=30) == 1  # it left by itself (exit code of the STOPPED path), nobody ended it

    deaf = _left_running(cfg, make, DEAF)
    m2 = make(grace=0.1)
    seen(m2, deaf, "not listening")
    m2.cancel(deaf)
    time.sleep(0.8)  # several times the grace
    assert make.started[-1].poll() is None and m2.current()["id"] == deaf  # still going: adoption never ends a process


def test_a_process_id_handed_out_again_is_not_adopted(cfg, make, monkeypatch):
    store = Store(cfg.db_path)
    try:
        run_id = store.start_run("scan", ["scan"], pid=os.getpid())  # alive, but certainly not that scan
    finally:
        store.close()
    monkeypatch.setattr(lock, "process_started", lambda pid: time.time() - 86_400)  # created a day before the run
    m = make()
    assert m.current() is None and m.get(run_id)["status"] == "stopped"


# -- the finish file: Bid mode waiting at the review ------------------------------------------------------

def _waiting(m, run_id: int, seconds: float = 30.0) -> dict:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        cur = m.review_waiting()
        if cur is not None and cur["id"] == run_id:
            return cur
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never waited at the review: {m.get(run_id)}")


def test_finish_file_is_named_in_the_environment_and_tidied(cfg, make):
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    (cfg.runs_dir / "1.finish").write_text("stale", encoding="utf-8")  # left by an earlier run with this id
    m = make()
    run = m.start("touch")
    done = finished(m, run["id"])
    lines = m.lines_after(run["id"])["lines"]
    assert done["status"] == "ok" and lines[0] == f"finish file: {cfg.runs_dir / '1.finish'}" and lines[1] == "stale finish file: no"
    assert not (cfg.runs_dir / "1.finish").exists()  # the file the script left behind is tidied with the run
    assert not (cfg.runs_dir / "1.cancel").exists()


def test_review_waiting_and_finish_review(cfg, make):
    m = make()
    assert m.review_waiting() is None
    with pytest.raises(NotRunning):
        m.finish_review(1)
    run = m.start("bid")
    cur = _waiting(m, run["id"])
    assert cur["command"] == "bid" and cur["progress"] == {"stage": "review", "pending": 2, "approved": 1, "waiting": True}
    assert m.lines_after(run["id"])["lines"][0] == "stale finish file: no"
    with pytest.raises(NotRunning, match="run 999 is not waiting"):
        m.finish_review(999)
    assert not (cfg.runs_dir / f"{run['id']}.finish").exists()
    m.finish_review(run["id"])
    done = finished(m, run["id"])
    assert done["status"] == "ok" and done["summary"] == {"approved": 1, "lines": ["x"]}
    assert done["progress"]["waiting"] is False and m.review_waiting() is None
    assert not (cfg.runs_dir / f"{run['id']}.finish").exists()  # the run removed it, and the runner would have
    with pytest.raises(NotRunning):
        m.finish_review(run["id"])  # over: nothing waits

    # The same progress line from any other command is not a review that Finish can end.
    scan = m.start("scan")
    seen(m, scan["id"], "stale finish file")
    time.sleep(0.3)
    assert m.current()["id"] == scan["id"] and m.review_waiting() is None
    with pytest.raises(NotRunning):
        m.finish_review(scan["id"])
    m.cancel(scan["id"])
    assert finished(m, scan["id"])["status"] == "stopped"

    # Stop asked while a Bid run waits: it is no longer "waiting", the run ends stopped, approvals are untouched here.
    again = m.start("bid")
    _waiting(m, again["id"])
    m.cancel(again["id"])
    assert m.review_waiting() is None
    with pytest.raises(NotRunning):
        m.finish_review(again["id"])
    assert finished(m, again["id"])["status"] == "stopped"


# -- the review page inside `bot bid` --------------------------------------------------------------------

def test_disabled_manager_shows_history_and_starts_nothing(cfg, make):
    m = make()
    m.start("ok")
    finished(m, 1)
    off = DisabledJobManager(cfg)
    with pytest.raises(Busy) as err:
        off.start("scan")
    assert str(err.value) == "this review was started by bot bid; open dashboard.cmd to run commands"
    with pytest.raises(Busy):
        off.start("nonsense", {"live": True})
    with pytest.raises(NotRunning):
        off.cancel(1)
    with pytest.raises(NotRunning):
        off.finish_review(1)
    assert off.current() is None and off.review_waiting() is None and off.describe() == describe()
    assert off.get(1)["status"] == "ok" and off.get(1)["summary"] == {"guides": 2, "review": 3} and off.get(2) is None
    assert off.lines_after(1, after=3) == m.lines_after(1, after=3)
    assert off.lines_after(2) is None and [r["id"] for r in off.list()] == [1]
    off.close()
    store = Store(cfg.db_path)
    try:
        assert [r["id"] for r in store.list_runs()] == [1]  # it started nothing
    finally:
        store.close()


# -- the allow-list ------------------------------------------------------------------------------------

SAMPLES = {"bool": [None, True, False], "int": [None, 1, 25, 100_000],
           "list": [None, [], ["gdh206"], ["GDH206", "zwr90", "S321V-2", "gdh206"]], "str": [None, "word"]}


def combinations(cmd: Cmd):
    names = list(cmd.options)
    for values in itertools.product(*(SAMPLES[cmd.options[n].kind] for n in names)):
        yield dict(zip(names, values))
    yield None


def test_the_table_is_the_daily_set():
    assert list(COMMANDS) == ["bid", "learn", "scan", "fetch-rules", "judge", "replay", "place", "insights", "login", "check-login"]
    assert {n for n, c in COMMANDS.items() if c.needs_profile} == {"bid", "learn", "scan", "fetch-rules", "judge", "place", "login",
                                                                   "check-login"}
    assert {n: sorted(c.options) for n, c in COMMANDS.items()} == {
        "bid": [], "learn": [],  # the two modes take no options from the page
        "scan": ["fresh", "headless", "limit", "model", "rescan"], "fetch-rules": ["model"], "judge": ["limit", "model"],
        "replay": ["apply", "model"], "place": ["headless"], "insights": ["days", "min_support", "model"],
        "login": [], "check-login": []}
    for cmd in COMMANDS.values():
        assert cmd.label and cmd.help and all(o.kind in jobs.KINDS and o.label for o in cmd.options.values())
        assert "live" not in cmd.label.lower() and "live" not in cmd.help.lower()


def test_no_combination_can_ask_for_live_placement():
    total = 0
    for name, cmd in COMMANDS.items():
        for options in combinations(cmd):
            argv = build_argv(name, options)
            total += 1
            assert argv[0] == name and all(isinstance(a, str) for a in argv)
            assert "--live" not in argv and not any("live" in a.lower() for a in argv), (name, options, argv)
            assert "--all" not in argv and "--sheet-check" not in argv
            if name == "judge":
                assert "--pending" in argv and "--no-browser" not in argv
            if name == "fetch-rules":
                assert "--headless" in argv and "--no-headless" not in argv
            if name == "login":
                assert argv == ["login", "--wait"]
            if name == "check-login":
                assert argv == ["check-login"]
            if name == "bid":
                assert argv == ["bid", "--no-browser"]  # the whole day, no window of its own, never a shortcut
                assert "--skip-scan" not in argv and "--no-place" not in argv and "--headed" not in argv
            if name == "learn":
                assert argv == ["learn"]  # watching Jim, never a HAR, never saving
            if name == "place":
                assert argv in (["place", "--headless"], ["place", "--no-headless"])
            if name in ("scan", "place"):
                assert ("--headless" in argv) != ("--no-headless" in argv)  # always said, never left to a default
    assert total > 400


def test_argv_is_built_from_the_table():
    assert build_argv("scan") == ["scan", "--headless"]
    assert build_argv("scan", {"model": ["gdh206", "ZWR90", "GDH206"], "headless": False, "limit": 5, "rescan": True,
                               "fresh": True}) == ["scan", "-m", "GDH206", "-m", "ZWR90", "--no-headless", "--limit", "5",
                                                   "--rescan", "--fresh"]
    assert build_argv("scan", {"model": None, "limit": None, "rescan": False, "headless": None}) == ["scan", "--headless"]
    assert build_argv("fetch-rules", {"model": ["GDH206"]}) == ["fetch-rules", "-m", "GDH206", "--headless"]
    assert build_argv("judge", {"limit": 10, "model": ["zwr90"]}) == ["judge", "--pending", "-m", "ZWR90", "--limit", "10"]
    assert build_argv("replay", {"apply": True, "model": ["GDH206"]}) == ["replay", "-m", "GDH206", "--apply"]
    assert build_argv("replay", {"apply": False}) == ["replay"]
    assert build_argv("place", {}) == ["place", "--headless"]
    assert build_argv("insights", {"days": 30, "min_support": 3}) == ["insights", "--days", "30", "--min-support", "3"]
    assert build_argv("bid") == ["bid", "--no-browser"] and build_argv("bid", {}) == ["bid", "--no-browser"]
    assert build_argv("learn") == ["learn"] and build_argv("learn", None) == ["learn"]


@pytest.mark.parametrize("command", ["watch", "review", "dashboard", "skip-line", "rules", "playbook", "", "BID", "Learn",
                                     "scan --live", "--live", "SCAN", None, 5, ["scan"]])
def test_unknown_command_is_refused(command):
    with pytest.raises(BadRequest):
        build_argv(command, {})


@pytest.mark.parametrize("command, options", [
    ("place", {"live": True}), ("place", {"--live": True}), ("place", {"live": False}), ("scan", {"live": True}),
    ("scan", {"sheet_check": "openai"}), ("judge", {"pending": False}), ("judge", {"all": True}),
    ("judge", {"no_browser": True}), ("login", {"wait": False}), ("check-login", {"headless": True}),
    ("fetch-rules", {"headless": False}), ("fetch-rules", {"include_inactive": True}), ("replay", {"limit": 5}),
    ("scan", {"": True}), ("scan", {5: True}),
    ("bid", {"skip_scan": True}), ("bid", {"no_place": True}), ("bid", {"live": True}), ("bid", {"headed": True}),
    ("bid", {"no_browser": False}), ("bid", {"model": ["GDH206"]}),
    ("learn", {"har": "x.har"}), ("learn", {"save": True}), ("learn", {"model": ["GDH206"]}), ("learn", {"live": True}),
])
def test_unknown_option_is_refused(command, options):
    with pytest.raises(BadRequest):
        build_argv(command, options)


@pytest.mark.parametrize("command, options", [
    ("scan", {"headless": "yes"}), ("scan", {"headless": 1}), ("scan", {"rescan": "--live"}), ("scan", {"limit": "5"}),
    ("scan", {"limit": 5.0}), ("scan", {"limit": True}), ("scan", {"limit": [5]}), ("scan", {"model": "GDH206"}),
    ("scan", {"model": [5]}), ("scan", {"model": [["GDH206"]]}), ("scan", {"model": {"a": 1}}), ("scan", {"model": [None]}),
    ("scan", ["--live"]), ("scan", "--live"), ("place", [("live", True)]),
])
def test_wrong_type_is_refused(command, options):
    with pytest.raises(BadRequest):
        build_argv(command, options)


@pytest.mark.parametrize("code", ["GDH206; rm", "--live", "-m", "-", "", " ", "GDH 206", "A" * 21, "GDH206\n", "../x",
                                  "GDH206&calc", "GDH206|x", "$(x)", "%PATH%", "乗車", "GDH206 --live", "--limit=1"])
def test_bad_model_code_is_refused(code):
    for command in ("scan", "fetch-rules", "judge", "replay", "insights"):
        with pytest.raises(BadRequest):
            build_argv(command, {"model": ["GDH206", code]})


def test_numbers_and_lists_have_limits():
    for bad in (0, -1, -100, 100_001, 10**12):
        for command, name in (("scan", "limit"), ("judge", "limit"), ("insights", "days"), ("insights", "min_support")):
            with pytest.raises(BadRequest):
                build_argv(command, {name: bad})
    assert build_argv("judge", {"limit": 100_000})[-1] == "100000"
    codes = [f"M{i}" for i in range(51)]
    with pytest.raises(BadRequest):
        build_argv("scan", {"model": codes})
    assert build_argv("scan", {"model": codes[:50]}).count("-m") == 50


def test_a_word_option_is_one_plain_word_from_its_choices():
    cmds = {"x": Cmd("X", False, {"mode": Opt("str", label="Mode", choices=("fast", "slow"))},
                     lambda v: ["x", *(["--mode", v["mode"]] if v.get("mode") else [])])}
    assert build_argv("x", {"mode": "fast"}, cmds) == ["x", "--mode", "fast"] and build_argv("x", {}, cmds) == ["x"]
    for bad in ("other", "--live", "fast slow", "fast;", "", 5, True, ["fast"]):
        with pytest.raises(BadRequest):
            build_argv("x", {"mode": bad}, cmds)
    assert describe(cmds)[0]["options"][0]["choices"] == ["fast", "slow"]


def test_a_table_that_asks_for_live_placement_is_refused():
    """The net under the table: even a wrongly edited table cannot pass the flag on."""
    cmds = {"place": Cmd("Place", True, {}, lambda v: ["place", "--live"])}
    with pytest.raises(BadRequest, match="dry run only"):
        build_argv("place", {}, cmds)


def test_describe_is_plain_data_for_the_page(cfg):
    d = describe()
    assert json.loads(json.dumps(d)) == d
    assert [c["name"] for c in d] == list(COMMANDS)
    bid, learn = d[0], d[1]
    assert bid == {"name": "bid", "label": "Bid mode", "help": bid["help"], "needs_profile": True, "options": []}
    assert learn == {"name": "learn", "label": "Learn mode", "help": learn["help"], "needs_profile": True, "options": []}
    scan = next(c for c in d if c["name"] == "scan")
    assert scan["label"] == "Scan Nichibo" and scan["needs_profile"] is True and scan["help"]
    assert [o["name"] for o in scan["options"]] == ["model", "headless", "limit", "rescan", "fresh"]
    assert scan["options"][1] == {"name": "headless", "kind": "bool", "default": True, "label": "Hide the browser window",
                                  "help": scan["options"][1]["help"]}
    place = next(c for c in d if c["name"] == "place")
    assert [o["name"] for o in place["options"]] == ["headless"] and "live" not in json.dumps(d).lower()
    assert DisabledJobManager(cfg).describe() == d and JobManager(cfg, reconcile=False).describe() == d


def test_every_argv_is_understood_by_the_real_cli():
    """Parsed by the CLI's own parser, without running anything: the flags exist and mean what the table says."""
    import typer.main

    import auction_bot.cli as cli

    group = typer.main.get_command(cli.app)
    for name, cmd in COMMANDS.items():
        for options in combinations(cmd):
            argv = build_argv(name, options)
            params = group.commands[argv[0]].make_context(argv[0], list(argv[1:])).params
            chosen = {k: v for k, v in (options or {}).items() if v is not None}
            if name == "place":
                assert params["live"] is False and params["headless"] is chosen.get("headless", True)
            if name == "scan":
                assert params["headless"] is chosen.get("headless", True) and params["sheet_check"] is None
                assert params["limit"] == chosen.get("limit") and params["rescan"] is chosen.get("rescan", False)
                assert params["fresh"] is chosen.get("fresh", False)
            if name == "judge":
                assert params["pending"] is True and params["no_browser"] is False and params["limit"] == chosen.get("limit")
            if name == "fetch-rules":
                assert params["headless"] is True and params["include_inactive"] is False
            if name == "replay":
                assert params["apply"] is chosen.get("apply", False)
            if name == "insights":
                assert params["days"] == chosen.get("days") and params["min_support"] == chosen.get("min_support", 2)
            if name == "login":
                assert params == {"wait": True}
            if name == "bid":
                assert params == {"skip_scan": False, "no_place": False, "headed": False, "no_browser": True}
            if name == "learn":
                assert params["har"] is None and params["save"] is False and not params["model"]
            if "model" in cmd.options:
                wanted = list(dict.fromkeys(c.upper() for c in chosen.get("model") or []))
                assert list(params["model"] or []) == wanted


# -- what the package may contain ------------------------------------------------------------------------

def test_the_dashboard_never_loads_playwright():
    allowed = {"auction_bot", "auction_bot.config", "auction_bot.store", "auction_bot.progress", "auction_bot.lock"}
    tree = ast.parse((DASHBOARD / "jobs.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
            if node.module == "auction_bot":
                assert {a.name for a in node.names} <= {"lock", "progress"}
        else:
            continue
        for name in names:
            assert not name.startswith("playwright"), name
            assert not name.startswith("auction_bot") or name in allowed, name
    # And not through anything it imports either: a fresh interpreter, this module, then a look at what got loaded.
    code = ("import sys; import auction_bot.dashboard.jobs; "
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('playwright', 'greenlet')); "
            "print(bad); sys.exit(1 if bad else 0)")
    env = dict(os.environ, PYTHONPATH=str(SRC))
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr


def test_the_live_flag_is_written_nowhere_in_the_dashboard_package():
    files = [p for p in DASHBOARD.rglob("*") if p.is_file()]
    assert DASHBOARD / "jobs.py" in files and DASHBOARD / "__init__.py" in files
    for path in files:
        assert b"--live" not in path.read_bytes(), path
