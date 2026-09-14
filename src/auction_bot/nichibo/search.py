"""Run a Nichibo auction search for one bidding guide and return the cards."""
from __future__ import annotations

from urllib.parse import urlencode

from auction_bot.carbarn.models import ModelRule
from auction_bot.config import Config
from auction_bot.nichibo.browser import Browser
from auction_bot.nichibo.cache import NoCache, PageCache
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.nichibo.parsers import parse_search_page


def build_search_url(base: str, chassis: str, year_from: int, year_to: int, km_max: int,
                     per_page: int = 1000, page: int = 1) -> str:
    params = [
        ("Make", "All"), ("Model[]", "All"), ("search-type", "global"),
        ("YearFrom", year_from), ("YearTo", year_to),
        ("KMMin", 0), ("KMMax", km_max),
        ("Grade", ""), ("EngineMin", ""), ("EngineMax", ""), ("Engine", "0 - 99999"), ("Fuel", ""),
        ("Chassis", chassis), ("Colour[]", ""), ("Shift", ""), ("AuctionPlace[]", "All"),
        ("sortGridSize", 6), ("includeStock", "N"),  # never Nichibo stock, only auction lots
        ("sort", "Make, Model, Year"), ("order", "ASC"),
        ("perPage", per_page), ("page", page),
    ]
    return f"{base.rstrip('/')}/auction?{urlencode(params)}"


def search_rule(browser: Browser, cfg: Config, rule: ModelRule, cache: PageCache | None = None) -> list[VehicleFacts]:
    cache = cache or NoCache()
    base = cfg.nichibo.get("base_url", "https://autosearch.nichibojapan.com")
    per_page = int(cfg.nichibo.get("per_page", 1000))
    years = rule.search_years
    km_max = rule.search_max_odo
    if not years or km_max <= 0:
        return []
    cards: list[VehicleFacts] = []
    page = 1
    while True:
        url = build_search_url(base, rule.model_code, years[0], years[1], km_max, per_page, page)
        html = cache.get(url)
        if html is None:
            html = browser.goto(url)
            if "/login" in browser.page.url:
                from auction_bot.nichibo.browser import NotLoggedIn
                raise NotLoggedIn("Nichibo redirected to the login page. Run `bot login`.")
            cache.put(url, html)
        page_cards, total = parse_search_page(html)
        cards.extend(page_cards)
        if not page_cards or total is None or len(cards) >= total or len(page_cards) < per_page:
            break
        page += 1
    # de-duplicate by ref_no, keep first
    seen: set[str] = set()
    unique = []
    for c in cards:
        if c.ref_no in seen:
            continue
        seen.add(c.ref_no)
        unique.append(c)
    return unique
