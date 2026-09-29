"""The profile lock: one run at a time on the bot's browser profile."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from auction_bot import lock

_KEPT = []  # finished processes whose objects stay alive, so that their ids are not handed out again


@pytest.fixture
def cfg(tmp_path):
    return SimpleNamespace(db_path=tmp_path / "data" / "bot.sqlite")  # the lock only needs to know where the database is


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    _KEPT.append(proc)
    return proc.pid


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


def _end(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=30)
    _KEPT.append(proc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def test_pid_alive_leaves_the_process_alone():
    assert lock.pid_alive(os.getpid())
    assert lock.pid_alive(os.getpid())  # asking did not end this process
    proc = _sleeper()
    try:
        assert lock.pid_alive(proc.pid)
        assert proc.poll() is None  # and not that one either
    finally:
        _end(proc)
    assert not lock.pid_alive(proc.pid)
    assert not lock.pid_alive(_dead_pid())
    for nonsense in (0, -1, None, "abc"):
        assert not lock.pid_alive(nonsense)


def test_acquire_and_release(cfg):
    path = lock.lock_path(cfg)
    assert path == cfg.db_path.parent / "profile.lock" and lock.holder(cfg) is None
    lock.acquire(cfg, "scan")
    held = lock.holder(cfg)
    assert held["pid"] == os.getpid() and held["command"] == "scan" and held["started"]
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"pid", "command", "started"}
    lock.release(cfg)
    assert not path.exists() and lock.holder(cfg) is None
    lock.release(cfg)  # nothing to release: no error


def test_reentrant_for_the_same_process(cfg):
    lock.acquire(cfg, "bid")
    lock.acquire(cfg, "bid")  # a second browser in the same run
    lock.release(cfg)
    assert lock.holder(cfg)["pid"] == os.getpid()  # the outer one still holds it
    lock.release(cfg)
    assert lock.holder(cfg) is None and not lock.lock_path(cfg).exists()


def test_stale_lock_is_replaced(cfg):
    path = lock.lock_path(cfg)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": _dead_pid(), "command": "scan", "started": "2026-09-28T09:00:00+00:00"}), encoding="utf-8")
    assert lock.holder(cfg) is None
    lock.acquire(cfg, "place")
    held = lock.holder(cfg)
    assert held["pid"] == os.getpid() and held["command"] == "place"
    lock.release(cfg)
    assert not path.exists()


def test_live_foreign_holder_refuses(cfg):
    path = lock.lock_path(cfg)
    path.parent.mkdir(parents=True)
    proc = _sleeper()
    try:
        started = _now()
        path.write_text(json.dumps({"pid": proc.pid, "command": "scan", "started": started}), encoding="utf-8")
        assert lock.holder(cfg)["pid"] == proc.pid
        with pytest.raises(RuntimeError) as err:
            lock.acquire(cfg, "place")
        text = str(err.value)
        assert text.startswith(f"another BidBarn run (scan, pid {proc.pid}, started ")
        assert text.endswith("is using the browser profile; wait for it to finish or stop it from the dashboard")
        assert datetime.fromisoformat(started).astimezone().strftime("%H:%M") in text
        lock.release(cfg)  # not ours: it stays
        assert path.exists() and lock.holder(cfg)["pid"] == proc.pid
        assert proc.poll() is None  # refusing did not touch the other run
    finally:
        _end(proc)
    assert lock.holder(cfg) is None  # its process is gone: the lock is stale now
    lock.acquire(cfg, "place")
    assert lock.holder(cfg)["pid"] == os.getpid()
    lock.release(cfg)


def test_a_process_id_handed_out_again_is_not_the_holder(cfg, monkeypatch):
    """After a restart of the PC the id in an old lock belongs to some other program."""
    path = lock.lock_path(cfg)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": os.getpid(), "command": "scan", "started": "2026-09-28T09:00:00+00:00"}), encoding="utf-8")
    monkeypatch.setattr(lock, "process_started", lambda pid: 4_000_000_000.0)  # created long after the lock was written
    assert lock.holder(cfg) is None
    monkeypatch.setattr(lock, "process_started", lambda pid: 1_000_000_000.0)  # created before it: the same process
    assert lock.holder(cfg)["command"] == "scan"
    monkeypatch.setattr(lock, "process_started", lambda pid: None)  # cannot tell: believe the lock
    assert lock.holder(cfg)["command"] == "scan"


def test_process_started():
    created = lock.process_started(os.getpid())
    if sys.platform == "win32":
        assert created is not None and time.time() - 86_400 < created <= time.time() + 1
    else:
        assert created is None
    assert lock.process_started(0) is None and lock.process_started("abc") is None


@pytest.mark.parametrize("content", ["", "{", "not json", "[]", '{"pid": "abc"}', '{"command": "scan"}', "\x00\x00"])
def test_corrupt_lock_is_tolerated(cfg, content):
    path = lock.lock_path(cfg)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    os.utime(path, (1_700_000_000, 1_700_000_000))  # old: nobody is in the middle of writing it
    assert lock.holder(cfg) is None
    lock.acquire(cfg, "scan")
    assert lock.holder(cfg)["pid"] == os.getpid()
    lock.release(cfg)
    assert not path.exists()


def test_a_lock_being_written_is_waited_for(cfg, monkeypatch):
    """Between creating the file and writing it the owner's lock is empty: that is not a stale lock."""
    path = lock.lock_path(cfg)
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    proc = _sleeper()
    try:
        naps = []

        def owner_finishes_writing(seconds):
            naps.append(seconds)
            path.write_text(json.dumps({"pid": proc.pid, "command": "scan", "started": _now()}), encoding="utf-8")

        monkeypatch.setattr(lock, "time", SimpleNamespace(time=time.time, sleep=owner_finishes_writing))
        with pytest.raises(RuntimeError, match=r"another BidBarn run \(scan"):
            lock.acquire(cfg, "place")
        assert naps  # it waited instead of taking the half-written lock away
    finally:
        _end(proc)


def test_command_name(monkeypatch):
    monkeypatch.setenv("BIDBARN_COMMAND", "check-login")
    assert lock.command_name() == "check-login"
    monkeypatch.delenv("BIDBARN_COMMAND")
    monkeypatch.setattr(sys, "argv", ["cli.py", "--verbose", "scan", "-m", "GDH206"])
    assert lock.command_name() == "scan"
    monkeypatch.setattr(sys, "argv", ["cli.py"])
    assert lock.command_name() == "bot"


def test_kill_pid_ends_another_process_never_this_one():
    assert lock.kill_pid(os.getpid()) is False
    assert lock.kill_pid(_dead_pid()) is False
    proc = _sleeper()
    try:
        assert lock.kill_pid(proc.pid) is True
        proc.wait(timeout=30)
        assert not lock.pid_alive(proc.pid)
    finally:
        _end(proc)


# -- where the lock is taken: the bot's browser and watch mode (Playwright replaced, no browser is started) ----------

def _no_real_browser(monkeypatch, cfg, log, fail=False):
    import auction_bot.nichibo.browser as browser_mod

    class Context:
        def route(self, *a, **k):
            log.append("guard installed")

        def close(self):
            log.append("context closed")

    class Chromium:
        def launch_persistent_context(self, **kw):
            log.append(("launch", (lock.holder(cfg) or {}).get("command"), kw["headless"]))
            if fail:
                raise RuntimeError("Chromium could not start")
            return Context()

    class Playwright:
        chromium = Chromium()

        def stop(self):
            log.append("playwright stopped")

    class Starter:
        def start(self):
            log.append("playwright started")
            return Playwright()

    monkeypatch.setattr(browser_mod, "sync_playwright", lambda: Starter())
    cfg.profile_dir = cfg.db_path.parent / "profile"
    cfg.pace_seconds = (0, 0)
    return browser_mod.Browser


def test_browser_holds_the_lock_while_it_is_open(cfg, monkeypatch):
    log = []
    Browser = _no_real_browser(monkeypatch, cfg, log)
    monkeypatch.setenv("BIDBARN_COMMAND", "scan")
    with Browser(cfg, headless=True):
        assert lock.holder(cfg) == {"pid": os.getpid(), "command": "scan", "started": lock.holder(cfg)["started"]}
    assert lock.holder(cfg) is None and not lock.lock_path(cfg).exists()
    # The lock was there before Chromium was started, and went after everything was closed.
    assert log == ["playwright started", ("launch", "scan", True), "guard installed", "context closed", "playwright stopped"]
    with pytest.raises(ValueError):
        with Browser(cfg, headless=True):
            raise ValueError("the run failed")
    assert lock.holder(cfg) is None  # given back when the run fails, too


def test_browser_gives_the_lock_back_when_chromium_cannot_start(cfg, monkeypatch):
    log = []
    Browser = _no_real_browser(monkeypatch, cfg, log, fail=True)
    monkeypatch.setenv("BIDBARN_COMMAND", "fetch-rules")
    with pytest.raises(RuntimeError, match="Chromium could not start"):
        with Browser(cfg, headless=False):
            raise AssertionError("never entered")
    assert lock.holder(cfg) is None and not lock.lock_path(cfg).exists()
    assert log == ["playwright started", ("launch", "fetch-rules", False), "playwright stopped"]


def test_a_second_browser_stops_before_chromium_is_started(cfg, monkeypatch):
    log = []
    Browser = _no_real_browser(monkeypatch, cfg, log)
    path = lock.lock_path(cfg)
    path.parent.mkdir(parents=True)
    proc = _sleeper()
    try:
        path.write_text(json.dumps({"pid": proc.pid, "command": "scan", "started": _now()}), encoding="utf-8")
        with pytest.raises(RuntimeError, match=r"another BidBarn run \(scan, pid \d+, started \d\d:\d\d\) is using the browser profile"):
            with Browser(cfg, headless=True):
                raise AssertionError("never entered")
        assert log == [] and lock.holder(cfg)["pid"] == proc.pid  # nothing was started, the other run keeps its lock
    finally:
        _end(proc)


def test_pace_ends_a_run_that_was_stopped(cfg, monkeypatch, tmp_path):
    from auction_bot.progress import Cancelled
    Browser = _no_real_browser(monkeypatch, cfg, [])
    flag = tmp_path / "4.cancel"
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(flag))
    b = Browser(cfg, headless=True)
    b.pace()  # not stopped: paces as always
    flag.write_text("stop", encoding="utf-8")
    with pytest.raises(Cancelled):
        b.pace()
    with pytest.raises(Cancelled):
        b.goto("https://autosearch.nichibojapan.com/auction")  # so no page is loaded after Stop


def test_watch_holds_the_lock_while_it_records(cfg, monkeypatch, tmp_path):
    import auction_bot.watch as watch_mod
    monkeypatch.setenv("BIDBARN_COMMAND", "learn")
    during = []

    def record(c, echo, headless, max_seconds):
        during.append(lock.holder(c))
        return tmp_path / "session"

    monkeypatch.setattr(watch_mod, "_record", record)
    assert watch_mod.watch(cfg, echo=lambda s: None) == tmp_path / "session"
    assert during[0]["pid"] == os.getpid() and during[0]["command"] == "learn" and lock.holder(cfg) is None

    def broken(c, echo, headless, max_seconds):
        raise RuntimeError("Chromium could not start")

    monkeypatch.setattr(watch_mod, "_record", broken)
    with pytest.raises(RuntimeError, match="could not start"):
        watch_mod.watch(cfg)
    assert lock.holder(cfg) is None and not lock.lock_path(cfg).exists()
