"""One click (`bidbarn.cmd` -> `bot run`): sign in if needed, fetch the guides, scan, review, place.

Every step is the same code the separate commands use; this module only puts them in
order and stops in the middle for the human review. The review page gets a "Finish review"
button that hands control back here, and then the approved bids are placed. Placement is
a dry run while the testing lock is on (CLAUDE.md, rule 0).
"""
from __future__ import annotations

import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from auction_bot.config import Config

LOGIN_WAIT_SECONDS = 15 * 60


@dataclass
class RunSummary:
    steps: list[str] = field(default_factory=list)
    guides: int = 0
    scan: dict | None = None
    approved: int = 0
    placed: list[str] = field(default_factory=list)


# -- 1. sign-in ---------------------------------------------------------------------------

def nichibo_signed_in(browser, cfg: Config) -> bool:
    base = cfg.nichibo.get("base_url", "https://autosearch.nichibojapan.com").rstrip("/")
    browser.goto(base + "/auction")
    return "/login" not in browser.page.url


def carbarn_signed_in(browser, cfg: Config, timeout_ms: int = 20_000) -> bool:
    """The dashboard loads the bidding guides only for a signed-in user."""
    url = cfg.carbarn.get("dashboard_url", "https://dashboard.carbarn.com.au/?view=bidding")
    page = browser.page
    try:
        with page.expect_response(lambda r: "/bidding-guides?" in r.url and r.status == 200, timeout=timeout_ms):
            browser.goto(url)
        return True
    except Exception:
        return False


def ensure_signed_in(cfg: Config, echo: Callable[[str], None] = print) -> None:
    """Check both sessions headlessly; if one is missing, open a window and wait for the sign-in."""
    from auction_bot.nichibo.browser import Browser

    with Browser(cfg, headless=True) as b:
        nichibo_ok = nichibo_signed_in(b, cfg)
        carbarn_ok = carbarn_signed_in(b, cfg)
    if nichibo_ok and carbarn_ok:
        echo("Signed in to Nichibo and the Carbarn dashboard.")
        return
    missing = " and ".join(n for n, ok in (("Nichibo", nichibo_ok), ("the Carbarn dashboard", carbarn_ok)) if not ok)
    echo(f"Not signed in to {missing}. A browser window is opening: sign in there. This continues by itself.")
    base = cfg.nichibo.get("base_url", "https://autosearch.nichibojapan.com").rstrip("/")
    dashboard = cfg.carbarn.get("dashboard_url", "https://dashboard.carbarn.com.au/?view=bidding")
    with Browser(cfg, headless=False) as b:
        nichibo_page = b.page
        carbarn_page = b.context.new_page()
        guides_seen = threading.Event()
        carbarn_page.on("response", lambda r: guides_seen.set() if "/bidding-guides?" in r.url and r.status == 200 else None)
        if not nichibo_ok:
            nichibo_page.goto(base + "/login")
        if not carbarn_ok:
            carbarn_page.goto(dashboard)
        else:
            guides_seen.set()
        deadline = time.time() + LOGIN_WAIT_SECONDS
        while time.time() < deadline:
            nichibo_page.wait_for_timeout(2000)
            n_ok = nichibo_ok or ("/login" not in nichibo_page.url and "nichibojapan" in nichibo_page.url)
            if n_ok and guides_seen.is_set():
                echo("Signed in. Continuing.")
                nichibo_page.wait_for_timeout(1000)  # let the cookies settle in the profile
                return
        raise RuntimeError("Still not signed in after 15 minutes. Double-click bidbarn.cmd again when you are ready.")


# -- 2. guides ------------------------------------------------------------------------------

def fetch_guides(cfg: Config, echo: Callable[[str], None] = print) -> tuple[int, Path]:
    from auction_bot.carbarn.client import CarbarnClient, save_snapshot
    from auction_bot.nichibo.browser import Browser

    with Browser(cfg, headless=True) as b:
        guides = CarbarnClient(b, cfg).fetch_team_guides(None, False)
    path = save_snapshot(cfg, guides)
    echo(f"Fetched {len(guides)} bidding guides ({path.name}).")
    return len(guides), path


# -- 3. scan --------------------------------------------------------------------------------

def scan_all(cfg: Config, echo: Callable[[str], None] = print, headless: bool = True) -> dict:
    from auction_bot.nichibo.browser import Browser
    from auction_bot.pipeline import scan
    from auction_bot.store import Store

    mode = (cfg.sheet_check or "none").lower()
    if mode == "openai" and not cfg.openai_api_key:
        mode = "none"
    store = Store(cfg.db_path)
    try:
        with Browser(cfg, headless=headless) as b:
            stats = scan(cfg, b, store, None, no_model=(mode == "none"))
    finally:
        store.close()
    d = dict(stats.__dict__)
    echo(f"Scan: {d['guides']} guides, {d['cards']} cards, {d['opened']} judged ({d['sheet_only']} from the card and the sheet, "
         f"{d['pages']} with the page, {d['pending']} waiting for images), {d['review']} to review, {d['skip']} skipped, "
         f"{d['reused']} already known" + (f", {len(d['errors'])} errors" if d["errors"] else ""))
    for err in d["errors"][:10]:
        echo(f"   error: {err}")
    return d


# -- 4. review --------------------------------------------------------------------------------

def serve_review(cfg: Config, echo: Callable[[str], None] = print, open_browser: bool = True) -> None:
    """Serve the review page until "Finish review" is clicked on it (or Enter is pressed here)."""
    import uvicorn

    from auction_bot.review.app import create_app

    host, port = cfg.review.get("host", "127.0.0.1"), int(cfg.review.get("port", 8765))
    finished = threading.Event()
    app = create_app(cfg, finish_event=finished)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    url = f"http://{host}:{port}/"
    echo(f"Review page: {url}")
    echo("Approve or reject the lots there. When you are done click \"Finish review\" at the top of the page")
    echo("(or press Enter in this window). The approved bids are placed after that.")
    if open_browser:
        webbrowser.open(url)

    def wait_for_enter() -> None:
        try:
            input()
        except (EOFError, OSError):
            return
        finished.set()

    threading.Thread(target=wait_for_enter, daemon=True).start()
    while not finished.wait(0.5):
        pass
    server.should_exit = True
    thread.join(timeout=10)
    echo("Review finished.")


# -- 5. place --------------------------------------------------------------------------------

def place_approved(cfg: Config, echo: Callable[[str], None] = print, headless: bool = True, live: bool = False,
                   browser_factory=None) -> list[str]:
    """Place every approved, not-yet-placed bid through `place_bid` (dry run unless every lock is off)."""
    import json

    from auction_bot.nichibo.bid import place_bid
    from auction_bot.nichibo.browser import Browser
    from auction_bot.nichibo.facts import VehicleFacts
    from auction_bot.store import Store

    dry, why = cfg.placement_mode(live)
    lines: list[str] = []
    store = Store(cfg.db_path)
    try:
        rows = store.approved_unplaced(cfg.max_bids_per_run)
        if not rows:
            echo("No approved bids waiting.")
            return lines
        echo(f"{'DRY RUN' if dry else 'LIVE'} ({why}): {len(rows)} approved bid(s)")
        factory = browser_factory or (lambda: Browser(cfg, headless=headless))
        with factory() as b:
            for row in rows:
                if store.already_bid(row["ref_no"]):
                    lines.append(f"skip {row['ref_no']}: already bid")
                    echo("  " + lines[-1])
                    continue
                facts = VehicleFacts.from_dict(json.loads(row["facts_json"]))
                amount = int(row["approved_bid_jpy_k"] or row["bid_jpy_k"])
                try:
                    res = place_bid(b, cfg, row["ref_no"], amount, facts, dry_run=dry)
                    ok, message, shot = res.ok, res.message, res.screenshot
                except Exception as exc:  # one broken lot must not stop the others
                    ok, message, shot = False, f"error: {str(exc)[:160]}", None
                store.record_placement(row["id"], row["ref_no"], row["model_code"], amount, ok, message, shot, dry)
                lines.append(f"{row['model_code']} {(row['summary'] or '')[:70]} -> {amount}K: {'OK' if ok else 'BLOCKED'} {message}")
                echo("  " + lines[-1])
    finally:
        store.close()
    return lines


# -- the whole day -----------------------------------------------------------------------------

def run(cfg: Config, echo: Callable[[str], None] = print, skip_scan: bool = False, no_place: bool = False,
        headed: bool = False, open_browser: bool = True, steps: dict | None = None) -> RunSummary:
    """The daily routine in one go. `steps` lets tests swap the individual step functions."""
    s = steps or {}
    sign_in = s.get("ensure_signed_in", ensure_signed_in)
    guides = s.get("fetch_guides", fetch_guides)
    scan = s.get("scan_all", scan_all)
    review = s.get("serve_review", serve_review)
    place = s.get("place_approved", place_approved)
    out = RunSummary()
    total = 5 - int(skip_scan) - int(no_place)
    n = 0

    def step(title: str) -> None:
        nonlocal n
        n += 1
        echo(f"\n[{n}/{total}] {title}")
        out.steps.append(title)

    lock = "testing lock on: placement is a dry run" if not cfg.project_finished else ("dry run" if cfg.dry_run else "LIVE placement")
    echo(f"BidBarn ({lock})")
    step("Checking the sign-in")
    sign_in(cfg, echo)
    step("Fetching the bidding guides")
    out.guides, _ = guides(cfg, echo)
    if not skip_scan:
        step("Scanning Nichibo")
        out.scan = scan(cfg, echo, headless=not headed)
    step("Your review")
    review(cfg, echo, open_browser=open_browser)
    if not no_place:
        step("Placing the approved bids")
        out.placed = place(cfg, echo, headless=not headed)
        out.approved = len(out.placed)
    echo("")
    echo("Done. " + (f"{out.approved} approved bid(s) handled." if out.approved else "Nothing was placed."))
    return out
