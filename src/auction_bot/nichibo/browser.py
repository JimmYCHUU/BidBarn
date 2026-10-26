"""One persistent Chromium profile shared by Nichibo and Carbarn access.

The profile is logged in once by a human (`bot login`); afterwards every run
reuses its cookies. All page loads go through `goto`, which paces requests.
"""
from __future__ import annotations

import random
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from auction_bot import lock, progress
from auction_bot.config import Config


class NotLoggedIn(RuntimeError):
    pass


class ForbiddenUrl(RuntimeError):
    """Raised before any navigation or request to a Nichibo area the bot must never touch."""


# Jim, 2026-09-14: never click or go to the Stock buy side of Nichibo. The bot only ever
# uses the auction search, auction detail pages, the sheet translator, images and login.
# Everything else on Nichibo is refused here, including carts, offers and paid image requests.
_NICHIBO_HOST = re.compile(r"(^|\.)nichibojapan\.com$", re.I)
# One list feeds both guards, so they cannot drift apart.
_FORBIDDEN_AREAS = (
    r"stock|vehicle[-_]?offers?|offers?|buy|purchase|order|checkout|cart|"
    r"auction/cart|auction/update-image-credit|auction/request-image|auction/images-request"
)
# The bid form is refused for every navigation and direct request; only the click in
# bid.py::place_bid may send it, so the page-request guard below lets it through.
_NICHIBO_DENY = re.compile(rf"^/({_FORBIDDEN_AREAS}|auction/bid)(/|\?|$)", re.I)
_NICHIBO_PAGE_DENY = re.compile(rf"https?://[^/]*nichibojapan\.com/({_FORBIDDEN_AREAS})(/|\?|$)", re.I)
_NICHIBO_ALLOW = re.compile(
    r"^/(auction/?|auction/search|auction/jsonAuctionSheetData|auction/jsonSuggested|auction/jsonMaketPrice/?|"
    r"image\.php|login|logout|updateSides|auction/[A-Za-z0-9+/=%_.-]+)$",
    re.I,
)


def nichibo_url_allowed(url: str) -> tuple[bool, str]:
    """(allowed, reason). Non-Nichibo hosts (the Carbarn dashboard) are always allowed."""
    parts = urlsplit(url)
    if not _NICHIBO_HOST.search(parts.hostname or ""):
        return True, "not nichibo"
    path = parts.path or "/"
    if _NICHIBO_DENY.match(path):
        return False, f"forbidden Nichibo area: {path}"
    if _NICHIBO_ALLOW.match(path):
        return True, "auction area"
    return False, f"not an auction page: {path}"


def _route_guard(route) -> None:
    ok, _ = nichibo_url_allowed(route.request.url)
    if ok:
        route.continue_()
    else:
        route.abort()


class Browser:
    def __init__(self, cfg: Config, headless: bool = False):
        self.cfg = cfg
        self.headless = headless
        self._pw: Playwright | None = None
        self.context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_nav = 0.0

    def __enter__(self) -> "Browser":
        # One run at a time on the profile: a second one stops here, before Chromium is started.
        lock.acquire(self.cfg, lock.command_name())
        try:
            self._pw = sync_playwright().start()
            # Headed: the page fills the real window (opened maximised) so nothing is cut off on
            # Jim's screen. Headless: a fixed page size keeps screenshots comparable.
            size = {"viewport": {"width": 1400, "height": 900}} if self.headless else {"no_viewport": True}
            args = ["--disable-blink-features=AutomationControlled"] + ([] if self.headless else ["--start-maximized"])
            self.context = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(self.cfg.profile_dir),
                headless=self.headless,
                locale="en-AU",
                args=args,
                **size,
            )
            # Second line of defence: even a page-initiated request to a forbidden Nichibo area is aborted.
            self.context.route(_NICHIBO_PAGE_DENY, _route_guard)
        except BaseException:
            self._close()  # the launch failed: nothing is left open and the lock is given back
            raise
        return self

    def __exit__(self, *exc) -> None:
        self._close()

    def _close(self) -> None:
        try:
            try:
                if self.context:
                    self.context.close()
            finally:
                if self._pw:
                    self._pw.stop()
        finally:
            lock.release(self.cfg)

    @property
    def page(self) -> Page:
        assert self.context is not None
        if self._page is None or self._page.is_closed():
            pages = self.context.pages
            self._page = pages[0] if pages else self.context.new_page()
        return self._page

    def pace(self) -> None:
        progress.check_cancel()  # a run stopped from the dashboard ends before its next page load
        lo, hi = self.cfg.pace_seconds
        wait = random.uniform(lo, hi) - (time.time() - self._last_nav)
        if wait > 0:
            time.sleep(wait)
        self._last_nav = time.time()

    def goto(self, url: str, wait_until: str = "domcontentloaded", retries: int = 1) -> str:
        """Load a page; on a timeout, wait a little and try once more (Nichibo stalls now and then)."""
        from playwright.sync_api import TimeoutError as PWTimeout

        ok, why = nichibo_url_allowed(url)
        if not ok:
            raise ForbiddenUrl(f"refusing to open {url}: {why}")
        last: Exception | None = None
        for attempt in range(retries + 1):
            self.pace()
            try:
                self.page.goto(url, wait_until="commit", timeout=45_000)
                self.page.wait_for_load_state(wait_until, timeout=45_000)
                return self.page.content()
            except PWTimeout as exc:
                last = exc
                if attempt < retries:
                    time.sleep(5)
        assert last is not None
        raise last

    def get(self, url: str, headers: dict | None = None, params: dict | None = None):
        assert self.context is not None
        ok, why = nichibo_url_allowed(url)
        if not ok:
            raise ForbiddenUrl(f"refusing to request {url}: {why}")
        return self.context.request.get(url, headers=headers or {}, params=params or {}, timeout=60_000)

    def get_bytes(self, url: str, headers: dict | None = None) -> tuple[bytes, str]:
        r = self.get(url, headers=headers)
        if not r.ok:
            raise RuntimeError(f"GET {url} -> {r.status}")
        return r.body(), r.headers.get("content-type", "")

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=False)
