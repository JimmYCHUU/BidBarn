"""Place one approved bid on the Nichibo detail page.

Every safety check is repeated here, seconds before the click:
the vehicle is re-fetched, start price and existing bid are re-verified,
the typed amount is read back from the page, and with dry_run nothing is clicked.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from auction_bot.config import Config
from auction_bot.nichibo.browser import Browser
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.nichibo.parsers import parse_detail_page
from auction_bot.nichibo.vehicle import detail_url

log = logging.getLogger("auction_bot.bid")


@dataclass
class PlacementResult:
    ok: bool
    message: str
    screenshot: str | None = None
    dry_run: bool = True


def _screenshot(browser: Browser, cfg: Config, ref_no: str, tag: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in ref_no)[:120]
    path = cfg.evidence_dir / safe / f"bid_{tag}.png"
    browser.screenshot(path)
    return str(path)


def place_bid(browser: Browser, cfg: Config, ref_no: str, bid_jpy_k: int, expected: VehicleFacts,
              comment: str = "", dry_run: bool = True) -> PlacementResult:
    # Defence in depth: whatever the caller asked for, the testing lock and dry_run win.
    locked, _why = cfg.placement_mode(live_flag=True)
    if locked:
        dry_run = True
    url = detail_url(cfg, ref_no)
    html = browser.goto(url)
    page = browser.page
    if "/login" in page.url:
        return PlacementResult(False, "not logged in to Nichibo", dry_run=dry_run)
    live = parse_detail_page(html, url)

    # Re-verify the vehicle is the one that was approved.
    problems = []
    if live.chassis != expected.chassis:
        problems.append(f"chassis changed {expected.chassis} -> {live.chassis}")
    if live.km != expected.km:
        problems.append(f"km changed {expected.km} -> {live.km}")
    if live.year != expected.year:
        problems.append(f"year changed {expected.year} -> {live.year}")
    if live.grade_raw != expected.grade_raw:
        problems.append(f"grade changed {expected.grade_raw} -> {live.grade_raw}")
    if live.start_price_jpy is None:
        problems.append("start price not found on page")
    elif bid_jpy_k * 1000 < live.start_price_jpy:
        problems.append(f"bid {bid_jpy_k}K below current start {live.start_price_jpy}")
    if live.has_current_bid:
        problems.append(f"page already shows a current bid of {live.current_bid_jpy}")
    if page.locator("#btnPlaceBid").count() == 0:
        problems.append("Place Bid button not present (auction closed?)")
    if "/auction/" not in page.url or "/stock" in page.url.lower():
        problems.append(f"not an auction detail page: {page.url}")
    if problems:
        shot = _screenshot(browser, cfg, ref_no, "blocked")
        return PlacementResult(False, "; ".join(problems), shot, dry_run)

    # Type the amount in thousands and let the page's keyup handler update #bidAmount.
    display = page.locator("#bidAmountDisplay")
    display.click()
    display.fill("")
    display.type(str(bid_jpy_k), delay=60)
    page.wait_for_timeout(300)
    hidden = page.locator("#bidAmount").input_value()
    if hidden.strip() != str(bid_jpy_k):
        shot = _screenshot(browser, cfg, ref_no, "mismatch")
        return PlacementResult(False, f"page bid field reads '{hidden}', expected '{bid_jpy_k}'", shot, dry_run)
    if comment:
        page.locator("#bidComment").fill(comment[:200])
    call = page.locator("#bidCallRequest")
    if call.count() and call.is_checked():
        call.uncheck()
    before = _screenshot(browser, cfg, ref_no, "before")

    if dry_run:
        log.info("DRY RUN: would place %sK on %s (%s)", bid_jpy_k, ref_no, expected.chassis)
        return PlacementResult(True, f"dry run: {bid_jpy_k}K typed, not submitted", before, True)

    with page.expect_response(lambda r: "/auction/bid" in r.url, timeout=30_000) as info:
        page.locator("#btnPlaceBid").click()
    resp = info.value
    text = resp.text()
    try:
        data = json.loads(text)
        ok = data.get("type") == "success"
        message = str(data.get("message") or data.get("type") or "")
    except json.JSONDecodeError:
        ok, message = False, text[:200]
    page.wait_for_timeout(1500)
    after = _screenshot(browser, cfg, ref_no, "after")
    if ok:
        # Confirm the page itself now shows the bid.
        row = page.locator("#currentBidRow")
        if row.count() and not row.is_visible():
            message += " (warning: current bid row not visible after success)"
    log.info("bid %sK on %s -> %s %s", bid_jpy_k, ref_no, "OK" if ok else "FAILED", message)
    return PlacementResult(ok, message, after, False)
