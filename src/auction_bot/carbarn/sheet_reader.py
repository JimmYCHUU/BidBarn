"""Carbarn's own auction-sheet reader, run by the dashboard's backend on its paid model (Jim, 2026-10-22).

`POST /auc/api/admin/aleado-auction/sheet-discovery/read-sheet` takes one image (multipart field `file`) and
answers `{sheet, welfare, identifiedAs, sheetError, welfareError}`: a structured transcription of the sheet and
a welfare (Welcab) reading with cited wording. The call is stateless on Carbarn's side (Jim).

A sheet image never changes, so a reply is kept for good under `data/cache/sheet_reads/<sha1 of the image>.json`
(nothing is ever deleted, CLAUDE.md 0c): reading the same sheet twice, or re-running the trial, costs no call.
Only complete replies are kept; a failure or a partial answer is asked again next time. The two prices in the
reply are dropped before anything is stored. A failure never raises: the caller gets `ok=False` and the reason.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from auction_bot.config import Config, cache_dir
from auction_bot.nichibo.browser import Browser, NotLoggedIn
from auction_bot.nichibo.vehicle import sniff_image
from auction_bot.progress import Cancelled

_PRICE_KEYS = ("start_price_jpy", "recycling_deposit_jpy")


@dataclass
class SheetReadResult:
    ok: bool
    reply: dict | None = None
    error: str | None = None
    seconds: float = 0.0
    cached: bool = False
    sha1: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def trim(body: dict) -> dict:
    """The reply without its prices (the bot never stores or forwards a price from the reader)."""
    out = dict(body)
    sheet = out.get("sheet")
    if isinstance(sheet, dict):
        out["sheet"] = {k: v for k, v in sheet.items() if k not in _PRICE_KEYS}
    return out


class SheetReader:
    """Reads a sheet image through `call(data, filename, mime) -> (status, body)` (`CarbarnClient.read_sheet` in
    production, a stub in tests), with the cache in front. `max_calls` caps the paid calls of one run."""

    def __init__(self, cfg: Config, call: Callable[[bytes, str, str], tuple[int, object]], max_calls: int | None = None):
        self.cfg = cfg
        self._call = call
        self.max_calls = max_calls
        self.calls = 0
        self.signed_out: str | None = None
        self.cache_dir = cache_dir(cfg) / "sheet_reads"

    @classmethod
    def for_browser(cls, cfg: Config, browser: Browser, max_calls: int | None = None) -> "SheetReader":
        from auction_bot.carbarn.client import CarbarnClient

        return cls(cfg, CarbarnClient(browser, cfg).read_sheet, max_calls=max_calls)

    def cached(self, sha1: str) -> dict | None:
        p = self.cache_dir / f"{sha1}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def read(self, path: Path) -> SheetReadResult:
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            return SheetReadResult(False, error=f"sheet file not readable: {exc}")
        sha1 = hashlib.sha1(data).hexdigest()
        hit = self.cached(sha1)
        if hit and isinstance(hit.get("reply"), dict):
            return SheetReadResult(True, reply=hit["reply"], seconds=float(hit.get("seconds") or 0), cached=True, sha1=sha1)
        kind = sniff_image(data)
        if kind is None:
            return SheetReadResult(False, error="not an image", sha1=sha1)
        if self.signed_out:
            return SheetReadResult(False, error=f"signed out of Carbarn: {self.signed_out}", sha1=sha1)
        if self.max_calls is not None and self.calls >= self.max_calls:
            return SheetReadResult(False, error=f"per-run cap of {self.max_calls} sheet reads reached", sha1=sha1)
        t0 = time.monotonic()
        try:
            status, body = self._call(data, f"sheet-{sha1[:12]}{kind[1]}", kind[0])
        except Cancelled:
            raise
        except NotLoggedIn as exc:
            self.signed_out = str(exc)
            return SheetReadResult(False, error=f"signed out of Carbarn: {exc}", seconds=time.monotonic() - t0, sha1=sha1)
        except Exception as exc:  # a timeout, a dropped connection: reported, never raised
            return SheetReadResult(False, error=f"{exc.__class__.__name__}: {str(exc)[:200]}", seconds=time.monotonic() - t0,
                                   sha1=sha1)
        self.calls += 1
        seconds = time.monotonic() - t0
        if status != 200:
            return SheetReadResult(False, error=f"HTTP {status}: {str(body)[:200]}", seconds=seconds, sha1=sha1)
        if not isinstance(body, dict) or not (body.get("sheet") or body.get("welfare")):
            return SheetReadResult(False, error=f"unexpected reply: {str(body)[:200]}", seconds=seconds, sha1=sha1)
        reply = trim(body)
        if not reply.get("sheetError") and not reply.get("welfareError"):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{sha1}.json").write_text(json.dumps(
                {"reply": reply, "seconds": round(seconds, 1), "read_at": datetime.now(timezone.utc).isoformat()},
                ensure_ascii=False, indent=1), encoding="utf-8")
        return SheetReadResult(True, reply=reply, seconds=seconds, sha1=sha1)
