"""One Nichibo job at a time: a lock file beside the database names the run using the browser profile.

`data/profile.lock` holds `{"pid", "command", "started"}`. It is taken when the persistent
Chromium profile is opened (`Browser.__enter__`, `watch.watch`) and removed when it is closed.
A lock whose process is gone is stale and is replaced; a lock held by a live process makes a
second run stop with one line instead of fighting over the profile.

Standard library only: the dashboard imports this module and must never load Playwright.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_QUERY_LIMITED_INFORMATION = 0x1000
_TERMINATE = 0x0001
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
_REUSE_SLACK_SECONDS = 5.0  # a process created this long after the lock was written is not its owner
_WRITE_SECONDS = 2.0  # an unreadable lock younger than this is being written by its owner right now

_guard = threading.Lock()
_depth: dict[str, int] = {}  # nested acquires in this process, per lock file
_k32 = None


def _kernel32():
    """kernel32 with the prototypes set (handles are pointer-sized; the defaults would truncate them)."""
    global _k32
    if _k32 is None:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.GetProcessTimes.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        _k32 = k
    return _k32


def _as_pid(pid) -> int | None:
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def pid_alive(pid: int) -> bool:
    """Is a process with this id running? Never signals it: on Windows `os.kill(pid, 0)` terminates the process."""
    n = _as_pid(pid)
    if n is None:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k = _kernel32()
        handle = k.OpenProcess(_QUERY_LIMITED_INFORMATION, False, n)
        if not handle:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED  # it exists, we may not look at it
        try:
            code = wintypes.DWORD()
            if not k.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            k.CloseHandle(handle)
    try:
        os.kill(n, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_started(pid: int) -> float | None:
    """When the process was created (epoch seconds), to tell a reused process id from the original.
    None when it cannot be read (other platforms, no access, process gone)."""
    n = _as_pid(pid)
    if n is None or sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    k = _kernel32()
    handle = k.OpenProcess(_QUERY_LIMITED_INFORMATION, False, n)
    if not handle:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ticks / 10_000_000 - 11_644_473_600  # 100 ns ticks since 1601 -> seconds since 1970
    finally:
        k.CloseHandle(handle)


def kill_pid(pid: int) -> bool:
    """Terminate a process by id. Never this process itself."""
    n = _as_pid(pid)
    if n is None or n == os.getpid():
        return False
    if sys.platform == "win32":
        k = _kernel32()
        handle = k.OpenProcess(_TERMINATE, False, n)
        if not handle:
            return False
        try:
            return bool(k.TerminateProcess(handle, 1))
        finally:
            k.CloseHandle(handle)
    try:
        os.kill(n, signal.SIGTERM)
    except OSError:
        return False
    return True


def command_name() -> str:
    """What this process is running, for the lock and its message."""
    name = (os.environ.get("BIDBARN_COMMAND") or "").strip()
    if name:
        return name[:40]
    for arg in sys.argv[1:]:
        if arg and not arg.startswith("-"):
            return arg[:40]
    return "bot"


def lock_path(cfg) -> Path:
    return Path(cfg.db_path).parent / "profile.lock"


def _epoch(stamp: str) -> float | None:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None


def _clock(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        return "?"


def _read(path: Path) -> dict | None:
    """The lock's content, None when the file is missing, corrupt or half written."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = _as_pid(data.get("pid"))
    if pid is None:
        return None
    return {"pid": pid, "command": str(data.get("command") or "bot"), "started": str(data.get("started") or "")}


def holder(cfg) -> dict | None:
    """`{"pid", "command", "started"}` of the live run holding the profile, None when nobody does."""
    data = _read(lock_path(cfg))
    if data is None or not pid_alive(data["pid"]):
        return None
    written, created = _epoch(data["started"]), process_started(data["pid"])
    if written is not None and created is not None and created > written + _REUSE_SLACK_SECONDS:
        return None  # the id belongs to a newer process now (after a restart of the PC, say)
    return data


def busy_message(data: dict) -> str:
    return (f"another BidBarn run ({data['command']}, pid {data['pid']}, started {_clock(data['started'])}) "
            "is using the browser profile; wait for it to finish or stop it from the dashboard")


def _being_written(path: Path) -> bool:
    try:
        return _read(path) is None and (time.time() - path.stat().st_mtime) < _WRITE_SECONDS
    except OSError:
        return False


def acquire(cfg, command: str | None = None) -> None:
    """Take the profile lock or raise RuntimeError naming the run that holds it. Re-entrant per process."""
    path = lock_path(cfg)
    key = str(path)
    me = os.getpid()
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(60):
        try:
            fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            data = holder(cfg)
            if data is not None:
                if data["pid"] == me:
                    with _guard:
                        _depth[key] = _depth.get(key, 0) + 1
                    return
                raise RuntimeError(busy_message(data))
            if _being_written(path):
                time.sleep(0.1)
                continue
            try:
                path.unlink()  # stale: its process is gone
            except FileNotFoundError:
                pass
            except OSError:
                time.sleep(0.1)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": me, "command": str(command or command_name()),
                       "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}, f)
        with _guard:
            _depth[key] = 1
        return
    raise RuntimeError(f"could not take the browser profile lock {path}; delete the file if no BidBarn run is going")


def release(cfg) -> None:
    """Give the lock back. Removes the file only when this process owns it; never raises."""
    try:
        path = lock_path(cfg)
        key = str(path)
        with _guard:
            depth = _depth.get(key, 0)
            if depth > 1:
                _depth[key] = depth - 1
                return
            _depth.pop(key, None)
        data = _read(path)
        if data is not None and data["pid"] == os.getpid():
            path.unlink()
    except Exception:
        pass
