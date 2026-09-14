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

from auction_bot.config import Config


class NotLoggedIn(RuntimeError):
    pass


class ForbiddenUrl(RuntimeError):
    """Raised before any navigation or request to a Nichibo area the bot must never touch."""


# Jim, 2026-09-14: never click or go to the Stock buy side of Nichibo. The bot only ever
# uses the auction search, auction detail pages, the sheet translator, images and login.
# Everything else on Nichibo is refused here, including carts, offers and paid image requests.
_NICHIBO_HOST = re.compile(r"(^|\.)nichibojapan\.com$", re.I)
_NICHIBO_DENY = re.compile(
    r"^/(stock|vehicleoffers|vehicle-offers|offer|offers|buy|purchase|order|checkout|cart|"
    r"auction/cart|auction/bid|auction/update-image-credit|auction/request-image|auction/images-request)(/|\?|$)",
    re.I,
)
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
        self._pw = sync_playwright().start()
        self.context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.cfg.profile_dir),
            headless=self.headless,
            viewport={"width": 1400, "height": 900},
            locale="en-AU",
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Second line of defence: even a page-initiated request to a forbidden Nichibo area is aborted.
        self.context.route(re.compile(r"https?://[^/]*nichibojapan\.com/(stock|vehicle[-_]?offers?|offers?|buy|purchase|order|checkout|cart|auction/cart|auction/update-image-credit)(/|\?|$)", re.I), _route_guard)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()

    @property
    def page(self) -> Page:
        assert self.context is not None
        if self._page is None or self._page.is_closed():
            pages = self.context.pages
            self._page = pages[0] if pages else self.context.new_page()
        return self._page

    def pace(self) -> None:
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
