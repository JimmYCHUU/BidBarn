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
from typing import Callable

from auction_bot.config import cache_dir
from auction_bot.nichibo.cache import PageCache

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BidBarn/1.0"
_DEFAULT_BASE = "https://www.kurumaerabi.com"


def catalog_enabled(cfg) -> bool:
    return bool((getattr(cfg, "catalog", None) or {}).get("enabled", False))


def default_fetch(url: str, timeout: float = 30.0) -> str:
    """One GET; raises on a non-200 answer (urllib.error.HTTPError) or a network problem."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept-Language": "ja,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return resp.read().decode("utf-8", errors="replace")


class CatalogClient:
    def __init__(self, cfg, cache: PageCache | None = None, fetch: Callable[[str], str] | None = None,
                 sleep: Callable = time.sleep):
        c = getattr(cfg, "catalog", None) or {}
        self.base_url = str(c.get("base_url") or _DEFAULT_BASE).rstrip("/")
        pace = c.get("pace_seconds") or [2.0, 5.0]
        self.pace = (float(pace[0]), float(pace[-1]))
        self.cache = cache if cache is not None else PageCache(cache_dir(cfg) / "catalog", float(c.get("cache_days", 90)) * 24)
        self._fetch = fetch
        self._sleep = sleep
        self._last_call: float | None = None
        self.errors: dict[str, str] = {}
        self.fetched: list[str] = []

    def allowed(self, url: str) -> bool:
        return url.startswith(self.base_url + "/catalog/")

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
            return text
        except Exception as e:  # a failed page is a finding "unknown", never an exception out of the scan
            self.errors[url] = (str(e) or type(e).__name__)[:200]
            return None
