"""Progress protocol between a BidBarn command and the dashboard that started it.

A command started by the dashboard runs with `BIDBARN_PROGRESS=1` and prints, between its
ordinary output, single lines the dashboard reads back from the run's log:

    PROGRESS {"stage":"scan","guides":46,"guide":3,"code":"GDH206",...}
    SUMMARY {"guides":46,"cards":812,...}

Without the variable nothing is printed, so a command run from a console looks exactly as
before. The dashboard stops a run by creating the file named in `BIDBARN_CANCEL_FILE`; the
command notices at its next `check_cancel()` and ends with `Cancelled`.

This module imports nothing from the package (the browser module imports it).
"""
from __future__ import annotations

import json
import os
import sys

PROGRESS_PREFIX = "PROGRESS "
SUMMARY_PREFIX = "SUMMARY "
CANCEL_MESSAGE = "stopped from the dashboard"


class Cancelled(RuntimeError):
    """The run was stopped from the dashboard."""


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


def check_cancel() -> None:
    if cancel_requested():
        raise Cancelled(CANCEL_MESSAGE)


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
