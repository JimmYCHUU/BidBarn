"""Progress protocol between a BidBarn command and the dashboard that started it.

A command started by the dashboard runs with `BIDBARN_PROGRESS=1` and prints, between its
ordinary output, single lines the dashboard reads back from the run's log:

    PROGRESS {"stage":"scan","guides":46,"guide":3,"code":"GDH206",...}
    SUMMARY {"guides":46,"cards":812,...}

Without the variable nothing is printed, so a command run from a console looks exactly as
before. The dashboard stops a run by creating the file named in `BIDBARN_CANCEL_FILE`; the
command notices at its next `check_cancel()` and ends with `Cancelled`. Pause creates the file
named in `BIDBARN_PAUSE_FILE`: the same check points end the run with `Paused`, a kind of
`Cancelled`, and Resume starts it again in the same session. The mirror of both is the file
named in `BIDBARN_FINISH_FILE`: a Bid run waits at the review until the dashboard's Finish
button creates it (`finish_requested()`), then removes it (`clear_finish()`) and goes on.
`BIDBARN_RUN_ID` names the dashboard run (`run_id()`), so the lots a scan writes carry it.

This module imports nothing from the package (the browser module imports it).
"""
from __future__ import annotations

import json
import os
import sys

PROGRESS_PREFIX = "PROGRESS "
SUMMARY_PREFIX = "SUMMARY "
CANCEL_MESSAGE = "stopped from the dashboard"
PAUSE_MESSAGE = "paused from the dashboard"


class Cancelled(RuntimeError):
    """The run was stopped from the dashboard."""


class Paused(Cancelled):
    """The run was paused from the dashboard: it ends like a stop, and Resume starts it again in the same session."""


def enabled() -> bool:
    return bool(os.environ.get("BIDBARN_PROGRESS"))


def _write(prefix: str, data: dict) -> None:
    """One flushed line on stdout. Never raises: progress must not break the command."""
    try:
        line = prefix + json.dumps(data, ensure_ascii=True, separators=(",", ":"), default=str)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def emit(**fields) -> None:
    if enabled():
        _write(PROGRESS_PREFIX, fields)


def summary(data: dict) -> None:
    if enabled():
        _write(SUMMARY_PREFIX, data)


def cancel_requested() -> bool:
    path = os.environ.get("BIDBARN_CANCEL_FILE")
    return bool(path) and os.path.exists(path)


def pause_requested() -> bool:
    path = os.environ.get("BIDBARN_PAUSE_FILE")
    return bool(path) and os.path.exists(path)


def check_cancel() -> None:
    """Stop or Pause from the dashboard ends the run here. Stop wins when both were clicked."""
    if cancel_requested():
        raise Cancelled(CANCEL_MESSAGE)
    if pause_requested():
        raise Paused(PAUSE_MESSAGE)


def run_id() -> int | None:
    """The dashboard run this command is (`BIDBARN_RUN_ID`); None from a console."""
    try:
        return int(os.environ.get("BIDBARN_RUN_ID") or "") or None
    except ValueError:
        return None


def finish_file() -> str | None:
    """The file the dashboard's Finish button writes; None when this run was not started by the dashboard."""
    return os.environ.get("BIDBARN_FINISH_FILE") or None


def finish_requested() -> bool:
    path = finish_file()
    return bool(path) and os.path.exists(path)


def clear_finish() -> None:
    """Remove the finish file once it has been seen. Best effort: a file that is gone already is fine."""
    path = finish_file()
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def parse(line: str) -> tuple[str, dict] | None:
    """("progress" | "summary", data) for a protocol line, None for anything else."""
    if not isinstance(line, str):
        return None
    text = line.rstrip("\r\n")
    for prefix, kind in ((PROGRESS_PREFIX, "progress"), (SUMMARY_PREFIX, "summary")):
        if text.startswith(prefix):
            try:
                data = json.loads(text[len(prefix):])
            except ValueError:
                return None
            return (kind, data) if isinstance(data, dict) else None
    return None
