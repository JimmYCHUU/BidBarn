"""The catalogue's HTTP client: the one place the bot reads kurumaerabi.com.

It sends only the catalogue's own page addresses (sitemap, model-code pages, grade pages): never a lot
reference, a price or anything from Carbarn. It refuses every address that does not start with
`base_url + "/catalog/"`. Pages are cached on disk for `catalog.cache_days`; only a real network call
waits (a random pause from `catalog.pace_seconds` since the previous network call), a cache hit never does.
A failure is remembered in `errors` and `get` returns None; it never raises.
"""
from __future__ import annotations

import random
import time
import urllib.request
from urllib.parse import unquote, urlsplit
from typing import Callable

from auction_bot.catalog.kurumaerabi import parse_type_sitemap
from auction_bot.config import cache_dir
from auction_bot.nichibo.cache import PageCache

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BidBarn/1.0"
_DEFAULT_BASE = "https://www.kurumaerabi.com"


def catalog_enabled(cfg) -> bool:
    return bool((getattr(cfg, "catalog", None) or {}).get("enabled", False))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would leave the allow-list unchecked; refuse it (urllib then raises HTTPError)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def default_fetch(url: str, timeout: float = 30.0) -> str:
    """One GET, never following a redirect; raises on a non-200 answer (urllib.error.HTTPError) or a network problem."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept-Language": "ja,en;q=0.8"})
    with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return resp.read().decode("utf-8", errors="replace")


class CatalogClient:
    def __init__(self, cfg, cache: PageCache | None = None, fetch: Callable[[str], str] | None = None,
                 sleep: Callable = time.sleep):
        c = getattr(cfg, "catalog", None) or {}
        self.base_url = str(c.get("base_url") or _DEFAULT_BASE).rstrip("/")
        pace = c.get("pace_seconds") or [2.0, 5.0]
        if isinstance(pace, (int, float)):
            pace = [pace, pace]
        self.pace = (float(pace[0]), float(pace[-1]))
        self._sitemap: dict[str, list[str]] | None = None
        self.cache = cache if cache is not None else PageCache(cache_dir(cfg) / "catalog", float(c.get("cache_days", 90)) * 24)
        self._fetch = fetch
        self._sleep = sleep
        self._last_call: float | None = None
        self.errors: dict[str, str] = {}
        self.fetched: list[str] = []

    def allowed(self, url: str) -> bool:
        """Same scheme and host as base_url (no userinfo, no other port), a path under /catalog/, no ".." segment."""
        try:
            u, b = urlsplit(url), urlsplit(self.base_url)
            if u.scheme != b.scheme or u.netloc != b.netloc or u.username is not None or u.password is not None:
                return False
            path = unquote(unquote(u.path))
        except ValueError:
            return False
        return path.startswith("/catalog/") and ".." not in path.split("/") and "\\" not in path

    def sitemap(self) -> dict[str, list[str]] | None:
        """The parsed model-code sitemap, read once per client; None when it could not be fetched."""
        if self._sitemap is None:
            text = self.get(self.sitemap_url())
            if text is None:
                return None
            self._sitemap = parse_type_sitemap(text)
        return self._sitemap

    def sitemap_url(self) -> str:
        return self.base_url + "/catalog/xml-sitemap/type.xml"

    def _pace(self) -> None:
        if self._last_call is not None:
            wait = random.uniform(*self.pace) - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)

    def get(self, url: str) -> str | None:
        if not self.allowed(url):
            self.errors[url] = "outside the catalogue"
            return None
        key = "catalog:" + url
        cached = self.cache.get(key)
        if cached is not None:
            self.errors.pop(url, None)
            return cached
        try:
            self._pace()
            fetch = self._fetch if self._fetch is not None else default_fetch
            try:
                text = fetch(url)
            finally:
                self._last_call = time.monotonic()
            self.fetched.append(url)
            self.cache.put(key, text)
            self.errors.pop(url, None)
            return text
        except Exception as e:  # a failed page is a finding "unknown", never an exception out of the scan
            self.errors[url] = (str(e) or type(e).__name__)[:200]
            return None
