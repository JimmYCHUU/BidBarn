"""One click (`bidbarn.cmd`): Learn mode (`bot learn`) watches Jim bid; Bid mode (`bot bid`) does the day.

Every step is the same code the separate commands use; this module only puts them in
order and stops in the middle for the human review. From a console the review page is served
here and its "Finish review" button hands control back; under the dashboard (`BIDBARN_FINISH_FILE`
set) the dashboard already serves the page and this run waits for the finish file its header
button writes. Then the approved bids are placed. Placement is a dry run while the testing lock
is on (CLAUDE.md, rule 0).
"""
from __future__ import annotations

import json
import socket
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from auction_bot import lock, progress
from auction_bot.config import Config
from auction_bot.progress import Cancelled

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
    except Cancelled:
        raise  # stopped from the dashboard: that is not "signed out"
    except Exception:
        return False


def ensure_signed_in(cfg: Config, echo: Callable[[str], None] = print) -> None:
    """Check both sessions headlessly; if one is missing, open a window and wait for the sign-in."""
    from auction_bot.nichibo.browser import Browser

    with Browser(cfg, headless=True) as b:
        nichibo_ok = nichibo_signed_in(b, cfg)
        carbarn_ok = carbarn_signed_in(b, cfg)
    progress.emit(stage="signin", nichibo=nichibo_ok, carbarn=carbarn_ok, waiting=not (nichibo_ok and carbarn_ok))
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
            progress.check_cancel()
            nichibo_page.wait_for_timeout(2000)
            n_ok = nichibo_ok or ("/login" not in nichibo_page.url and "nichibojapan" in nichibo_page.url)
            if n_ok and guides_seen.is_set():
                echo("Signed in. Continuing.")
                progress.emit(stage="signin", nichibo=True, carbarn=True, waiting=False)
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
    progress.emit(stage="guides", guides=len(guides), snapshot=path.name)
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
            if mode == "judge":
                from auction_bot.pipeline import judge_pending

                js = judge_pending(cfg, store, browser=b)
                echo(f"Judge: {js.judged} lots judged by the model, {js.unavailable} left unjudged (model unavailable), "
                     f"{js.pages} pages opened for photos")
    finally:
        store.close()
    d = dict(stats.__dict__)
    echo(f"Scan: {d['guides']} guides, {d['cards']} cards, {d['opened']} judged ({d['sheet_only']} from the card and the sheet, "
         f"{d['pages']} with the page, {d['day_pages']} pages read for the auction day, {d['pending']} waiting for images), {d['review']} to review, {d['skip']} skipped, "
         f"{d['reused']} already known" + (f", {len(d['errors'])} errors" if d["errors"] else ""))
    for err in d["errors"][:10]:
        echo(f"   error: {err}")
    return d


# -- 4. review --------------------------------------------------------------------------------

DASHBOARD_HAS_THE_PORT = ("The dashboard is running on that address. Start Bid mode from the dashboard, "
                          "or close the dashboard window and run bidbarn.cmd again.")


def port_in_use(host: str, port: int, timeout: float = 0.5) -> bool:
    """Does something already accept connections there (a dashboard that is running)?"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def serve_review(cfg: Config, echo: Callable[[str], None] = print, open_browser: bool = True) -> None:
    """Serve the review page until "Finish review" is clicked on it (or Enter is pressed here).

    The console path only: with the dashboard on the same address this stops with one line,
    because that page is already there and Bid mode can be started from it."""
    import uvicorn

    from auction_bot.dashboard.jobs import DisabledJobManager
    from auction_bot.review.app import create_app

    host, port = cfg.review.get("host", "127.0.0.1"), int(cfg.review.get("port", 8765))
    if port_in_use(host, port):
        raise RuntimeError(DASHBOARD_HAS_THE_PORT)
    finished = threading.Event()
    app = create_app(cfg, finish_event=finished, jobs=DisabledJobManager(cfg))  # this window runs the commands itself
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    url = f"http://{host}:{port}/review"
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


def wait_for_review(cfg: Config, echo: Callable[[str], None] = print, open_browser: bool = True,
                    poll_seconds: float = 1.0) -> None:
    """Under the dashboard: the review page is its own, so wait until its Finish button writes the
    finish file. Never opens a browser (`open_browser` keeps the call shape of `serve_review`).
    The counts are reported whenever they change, so the run page can show the review moving."""
    from auction_bot.store import Store

    echo("Review the lots on the dashboard's review page. When you are done click \"Finish review\" at the top")
    echo("of the page. The approved bids are placed after that.")
    last: tuple[int, int] | None = None
    while True:
        progress.check_cancel()
        store = Store(cfg.db_path)
        try:
            c = store.counts()
        finally:
            store.close()
        now = (int(c["bid"]) + int(c["review"]), int(c["approved"]))
        if now != last:
            last = now
            progress.emit(stage="review", pending=now[0], approved=now[1], waiting=True)
        if progress.finish_requested():
            break
        time.sleep(poll_seconds)
    progress.emit(stage="review", pending=last[0], approved=last[1], waiting=False)
    progress.clear_finish()
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
        rows = store.approved_unplaced(cfg.max_bids_per_run, dry_run=dry)
        progress.emit(stage="place", i=0, total=len(rows), dry_run=dry)
        if not rows:
            done = store.dry_done_count() if dry else 0
            echo(f"No approved bids waiting: {done} already placed as a dry run. Reopen one to type it again."
                 if done else "No approved bids waiting.")
            return lines
        echo(f"{'DRY RUN' if dry else 'LIVE'} ({why}): {len(rows)} approved bid(s)")
        factory = browser_factory or (lambda: Browser(cfg, headless=headless))
        with factory() as b:
            for i, row in enumerate(rows, 1):
                progress.check_cancel()  # between lots only: a lot that was started is finished and recorded
                if store.already_bid(row["ref_no"]):
                    lines.append(f"skip {row['ref_no']}: already bid")
                    echo("  " + lines[-1])
                    progress.emit(stage="place", i=i, total=len(rows), ref_no=row["ref_no"], ok=False, skipped=True,
                                  message="already bid", screenshot=None, dry_run=dry)
                    continue
                if not store.still_approved(row["id"]):  # reopened (or replaced) since the run started
                    lines.append(f"skip {row['ref_no']}: reopened since the run started")
                    echo("  " + lines[-1])
                    progress.emit(stage="place", i=i, total=len(rows), ref_no=row["ref_no"], ok=False, skipped=True,
                                  message="reopened", screenshot=None, dry_run=dry)
                    continue
                facts = VehicleFacts.from_dict(json.loads(row["facts_json"]))
                amount = int(row["approved_bid_jpy_k"] or row["bid_jpy_k"])
                try:
                    res = place_bid(b, cfg, row["ref_no"], amount, facts, dry_run=dry)
                    ok, message, shot = res.ok, res.message, res.screenshot
                except Cancelled:
                    raise  # raised before the lot's page was loaded: nothing was typed, nothing is recorded
                except Exception as exc:  # one broken lot must not stop the others
                    ok, message, shot = False, f"error: {str(exc)[:160]}", None
                store.record_placement(row["id"], row["ref_no"], row["model_code"], amount, ok, message, shot, dry)
                lines.append(f"{row['model_code']} {(row['summary'] or '')[:70]} -> {amount}K: {'OK' if ok else 'BLOCKED'} {message}")
                echo("  " + lines[-1])
                progress.emit(stage="place", i=i, total=len(rows), ref_no=row["ref_no"], ok=ok, skipped=False,
                              message=message, screenshot=shot, screenshot_rel=_evidence_rel(cfg, shot), dry_run=dry,
                              proposal_id=row["id"], model_code=row["model_code"], amount_jpy_k=amount)
    finally:
        store.close()
    return lines


def _evidence_rel(cfg: Config, path: str | None) -> str | None:
    """A screenshot's path below the evidence folder ("<lot>/bid_before.png"), for the page's /evidence mount."""
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(Path(cfg.evidence_dir).resolve()).as_posix()
    except (OSError, ValueError):
        return None


# -- the whole day -----------------------------------------------------------------------------

SCAN_KEYS = ("cards", "opened", "review", "bid", "skip", "reused", "pending", "model_calls")


def bid_summary(cfg: Config, out: RunSummary) -> dict:
    """The closing numbers of a Bid run, flat so the run page orders them like a scan's or a placement's.
    `nichibo` and `carbarn` are true because the sign-in step only returns once both are in."""
    scan = out.scan or {}
    errors = [str(e)[:300] for e in (scan.get("errors") or [])]
    d: dict = {"steps": list(out.steps), "nichibo": True, "carbarn": True, "guides": out.guides}
    for key in SCAN_KEYS:
        if key in scan:
            d[key] = scan[key]
    d.update(errors=errors[:20], errors_total=len(errors), approved=out.approved,
             dry_run=cfg.placement_mode(False)[0], lines=[str(ln)[:300] for ln in out.placed])
    return d


def run(cfg: Config, echo: Callable[[str], None] = print, skip_scan: bool = False, no_place: bool = False,
        headed: bool = False, open_browser: bool = True, steps: dict | None = None) -> RunSummary:
    """The daily routine in one go. `steps` lets tests swap the individual step functions.

    The browser profile is held for the whole run, so a scan started from a console during the
    review wait stops with one line instead of taking the profile. Each step boundary is
    reported with its stage, so the dashboard's stepper moves with the run."""
    s = steps or {}
    sign_in = s.get("ensure_signed_in", ensure_signed_in)
    guides = s.get("fetch_guides", fetch_guides)
    scan = s.get("scan_all", scan_all)
    review = s.get("wait_for_review", wait_for_review) if progress.finish_file() else s.get("serve_review", serve_review)
    place = s.get("place_approved", place_approved)
    out = RunSummary()
    total = 5 - int(skip_scan) - int(no_place)
    n = 0

    def step(title: str, key: str) -> None:
        nonlocal n
        n += 1
        echo(f"\n[{n}/{total}] {title}")
        out.steps.append(title)
        progress.emit(stage=key, step=n, steps=total)

    mode = "testing lock on: placement is a dry run" if not cfg.project_finished else ("dry run" if cfg.dry_run else "LIVE placement")
    echo(f"BidBarn ({mode})")
    lock.acquire(cfg, lock.command_name())  # re-entrant: every Browser below takes and gives back the same lock
    try:
        step("Checking the sign-in", "signin")
        sign_in(cfg, echo)
        step("Fetching the bidding guides", "guides")
        out.guides, _ = guides(cfg, echo)
        if not skip_scan:
            step("Scanning Nichibo", "scan")
            out.scan = scan(cfg, echo, headless=not headed)
        step("Your review", "review")
        review(cfg, echo, open_browser=open_browser)
        if not no_place:
            step("Placing the approved bids", "place")
            out.placed = place(cfg, echo, headless=not headed)
            out.approved = len(out.placed)
    finally:
        lock.release(cfg)
    echo("")
    echo("Done. " + (f"{out.approved} approved bid(s) handled." if out.approved else "Nothing was placed."))
    progress.summary(bid_summary(cfg, out))
    return out


# -- teaching: Jim bids by hand, the bot watches, then compares and suggests -------------------------

def scan_codes(cfg: Config, codes: list[str], echo: Callable[[str], None] = print, headless: bool = True) -> dict:
    """Scan only the model codes Jim searched, so the bot has its own view of the same lots."""
    from auction_bot.nichibo.browser import Browser
    from auction_bot.pipeline import scan
    from auction_bot.store import Store

    judge = (cfg.sheet_check or "none").lower() == "judge"
    store = Store(cfg.db_path)
    try:
        with Browser(cfg, headless=headless) as b:
            stats = scan(cfg, b, store, codes, no_model=None if judge else True)
            if judge:
                from auction_bot.pipeline import judge_pending

                judge_pending(cfg, store, codes, browser=b)
    finally:
        store.close()
    d = dict(stats.__dict__)
    echo(f"Bot's own scan of {', '.join(codes)}: {d['guides']} guides, {d['cards']} cards, {d['opened']} judged, "
         f"{d['review']} to review, {d['skip']} skipped, {d['reused']} already known"
         + (f", {d['model_calls']} asked of the model" if judge else ""))
    return d


def model_view(soft: dict, jim_acted: bool, jim_passed: bool) -> str:
    """What the judgement layer said about a lot, and whether it agrees with what Jim did."""
    if not soft or soft.get("mode") != "judge":
        return ""
    rec = soft.get("recommendation") or "review"
    j = soft.get("judgement") or {}
    bits = [f"model recommends {rec}"]
    if soft.get("proposed_bid_jpy_k"):
        bits.append(f"proposes {soft['proposed_bid_jpy_k']:,}K")
    marks = {"pass": "✓", "fail": "✗", "unclear": "?"}
    verdicts = {v.get("id"): v for v in j.get("requirements", [])}
    reqs = [f"{marks.get(verdicts.get(r['id'], {}).get('verdict'), '·')} {r['text'][:30]}" for r in soft.get("requirements", [])
            if r.get("kind") != "chip"]
    if reqs:
        bits.append(", ".join(reqs))
    if j.get("summary"):
        bits.append(j["summary"])
    verdict = ""
    if jim_acted and rec == "skip":
        verdict = " | DISAGREE: you bid, the model would skip"
    elif jim_passed and rec == "bid":
        verdict = " | DISAGREE: you passed, the model would bid"
    elif jim_acted and rec == "bid":
        verdict = " | agree"
    return "; ".join(bits) + verdict


def _k(value) -> int | str | None:
    """An amount in thousands as recorded: a whole number when it is one, else the text as typed."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def model_agrees(soft: dict, jim_acted: bool, jim_passed: bool) -> bool | None:
    """True when Jim and the model both bid, False when one bid and the other would skip, else None."""
    if not soft or soft.get("mode") != "judge":
        return None
    rec = soft.get("recommendation") or "review"
    if jim_acted and rec == "skip":
        return False
    if jim_passed and rec == "bid":
        return False
    if jim_acted and rec == "bid":
        return True
    return None


def compare_lots(session, store) -> list[dict]:
    """Per lot Jim touched: what he did, what the bot proposed, what the model said, and the rules
    his comments suggest, as plain data. `compare_lines` turns it into the report's text.

    `bot` is None when the bot never saw the lot; `model` is None without a judgement."""
    from auction_bot.learn import _reasons
    from auction_bot.nichibo.facts import VehicleFacts
    from auction_bot.rules.instructions import suggest_from_reason

    lots: list[dict] = []
    for v in session.lots.values():
        if not v.touched or v.ref in ("today",):
            continue
        row = store.latest_proposal(v.ref)
        row = dict(row) if row is not None else None
        facts = store.vehicle_facts(v.ref)
        title = v.title or (f"{facts.get('year', '')} {facts.get('make', '')} {facts.get('model', '')}".strip() if facts else v.ref)
        typed = v.bid_k or (v.amounts[-1] if v.amounts else None)
        jim = {"opened": v.opened, "carted": v.carted, "cart_clicks": v.cart_clicks, "bid_k": _k(v.bid_k),
               "typed_k": _k(v.amounts[-1]) if v.amounts else None, "reasons": list(v.reasons), "seconds": v.seconds,
               "sheet_viewed": v.sheet_viewed}
        bot = None
        if row is not None:
            why = _reasons(row)
            delta = None
            if typed and row.get("bid_jpy_k"):
                try:
                    delta = int(typed) - int(row["bid_jpy_k"])
                except ValueError:
                    pass
            bot = {"status": row["status"], "bid_k": row.get("bid_jpy_k"), "reason": why[0] if why else None,
                   "decision": row.get("decision"), "delta_k": delta}
        model = None
        if row and row.get("soft_json"):
            try:
                soft = json.loads(row["soft_json"])
            except (TypeError, ValueError):
                soft = {}
            acted = bool(v.bid_k or v.amounts or v.carted)
            passed = bool(v.cart_clicks and not v.carted and not v.bid_k and not v.amounts)
            line = model_view(soft, jim_acted=acted, jim_passed=passed)
            if line:
                j = soft.get("judgement") or {}
                model = {"recommendation": soft.get("recommendation") or "review", "proposed_k": soft.get("proposed_bid_jpy_k"),
                         "summary": j.get("summary") or "", "agree": model_agrees(soft, acted, passed), "line": line}
        candidates = []
        for r in v.reasons:
            cand = suggest_from_reason(r, VehicleFacts.from_dict(facts)) if facts else None
            if cand:
                candidates.append({"line": cand, "from_reason": r})
        lots.append({"ref_no": v.ref, "title": title, "code": row["model_code"] if row else None,
                     "proposal_id": row["id"] if row else None, "jim": jim, "bot": bot, "model": model, "candidates": candidates})
    return lots


def compare_lines(lots: list[dict], notes) -> list[str]:
    """The comparison as the report prints it, from `compare_lots` and the session's notes."""
    lines: list[str] = []
    for lot in lots:
        jim, bot, model = lot["jim"], lot["bot"], lot["model"]
        did = []
        if jim["bid_k"] is not None:
            did.append(f"bid {jim['bid_k']}K")
        elif jim["typed_k"] is not None:
            did.append(f"typed {jim['typed_k']}K")
        if jim["cart_clicks"]:
            did.append("left in the cart" if jim["carted"] else "carted, then taken out")
        if jim["opened"]:
            did.append("opened the page")
        if bot is None:
            said = "never seen (not in the bot's scan)"
        else:
            said = bot["status"] + (f" {bot['bid_k']:,}K" if bot["bid_k"] else "")
            if bot["reason"]:
                said += " because " + bot["reason"]
        delta = f" (Jim {bot['delta_k']:+,}K against the grid)" if bot and bot["delta_k"] is not None else ""
        lines.append(f"- {lot['title']}: Jim {', '.join(did) or 'looked'} | bot {said}{delta}")
        if model:
            lines.append(f"     {model['line']}")
        for r in jim["reasons"]:
            lines.append(f"     why: {r}")
            for cand in lot["candidates"]:
                if cand["from_reason"] == r:
                    lines.append(f"     candidate rule for {lot['code'] or 'this model'}: {cand['line']}")
    for n in notes:
        lines.append(f"- note [{n.where}]: {n.text}")
    return lines


def compare_session(cfg: Config, session, store) -> list[str]:
    """Per lot Jim touched: what he did, what the bot proposed, and the rule his comment suggests."""
    return compare_lines(compare_lots(session, store), session.notes)


def learn(cfg: Config, echo: Callable[[str], None] = print, steps: dict | None = None) -> Path:
    """Learn mode: watch Jim bid by hand, then scan what he searched, compare, and suggest rules."""
    from auction_bot.store import Store
    from auction_bot.watch import load_events, render_session, timeline
    from auction_bot.watch import watch as watch_fn

    s_ = steps or {}
    watch_ = s_.get("watch", watch_fn)
    guides_ = s_.get("fetch_guides", fetch_guides)
    scan_ = s_.get("scan_codes", scan_codes)
    echo("BidBarn is learning. Nichibo and the Carbarn dashboard open in two tabs; put the dashboard on your other monitor.")
    echo("Bid exactly as you normally do; type why in the blue box (Enter saves). Hovering and highlighting are recorded too.")
    echo("Close the browser window when you are done.")
    out = watch_(cfg, echo)
    session = timeline(load_events(out), str(out))
    codes = list(dict.fromkeys(session.searches))
    if codes:
        echo("")
        echo(f"You searched {', '.join(codes)}. The bot now scans the same codes for comparison (nothing is placed).")
        try:
            progress.emit(stage="learn", phase="guides", session=out.name, searched=codes)
            guides_(cfg, echo)
            progress.emit(stage="learn", phase="scan", session=out.name, searched=codes)
            scan_(cfg, codes, echo)
        except Exception as exc:  # the comparison is optional; the session is already saved
            echo(f"Could not scan for comparison: {str(exc)[:200]}")
    progress.emit(stage="learn", phase="compare", session=out.name, searched=codes)
    store = Store(cfg.db_path)
    try:
        def bot_view(ref: str) -> str:
            row = store.latest_proposal(ref)
            if row is None:
                return "never seen"
            return row["status"] + (f" {row['bid_jpy_k']:,}K" if row["bid_jpy_k"] else "") + f", {row['decision']}"

        report = render_session(session, bot_view)
        lots = compare_lots(session, store)
        comparison = compare_lines(lots, session.notes)
        actions = [(v.ref, "open", None, None, v.opened_at, None) for v in session.visits]
        actions += [(v.ref, "bid", int(v.bid_k) * 1000 if str(v.bid_k).isdigit() else None,
                     int(v.bid_k) if str(v.bid_k).isdigit() else None, session.ended or session.started or "", None)
                    for v in session.lots.values() if v.bid_k]
        actions += [(v.ref, "reason", None, None, (v.reason_times[i] if i < len(v.reason_times) else session.ended or ""), r)
                    for v in session.lots.values() for i, r in enumerate(v.reasons)]
        saved = store.add_manual_actions(f"{out.name} (watch)", actions) if actions else 0
    finally:
        store.close()
    text = report + "\n\nWhat the bot would have done, and the rules your comments suggest:\n" + "\n".join(comparison or ["   (nothing to compare)"])
    (out / "report.txt").write_text(text, encoding="utf-8")
    echo("")
    echo(text)
    echo("")
    echo(f"Report saved to {out / 'report.txt'} ({saved} actions kept in the store).")
    echo("A candidate rule becomes a rule only when you add it: review page, or  bot skip-line CODE \"skip: ...\".")
    progress.summary(learn_summary(out, session, codes, lots, saved))
    return out


def learn_summary(out: Path, session, codes: list[str], lots: list[dict], saved: int) -> dict:
    """The session as plain data for the run page: the counts, every lot compared, the notes and the
    candidate rules (which become rules only when Jim adds them)."""
    agree = sum(1 for lot in lots if lot["model"] and lot["model"]["agree"] is True)
    disagree = sum(1 for lot in lots if lot["model"] and lot["model"]["agree"] is False)
    candidates = [{"code": lot["code"], "ref_no": lot["ref_no"], **c} for lot in lots for c in lot["candidates"]]
    d = session.dashboard
    return {"session": out.name, "report": str(out / "report.txt"), "searched": list(codes),
            "counts": {"lots": len(lots), "opened": sum(1 for lot in lots if lot["jim"]["opened"]),
                       "carted": sum(1 for lot in lots if lot["jim"]["carted"]),
                       "bids": sum(1 for lot in lots if lot["jim"]["bid_k"] is not None), "agree": agree, "disagree": disagree,
                       "searches": len(session.searches), "notes": len(session.notes), "candidates": len(candidates),
                       "actions_saved": saved, "screenshots": int(session.counts.get("screenshot", 0))},
            "lots": lots, "notes": [{"where": n.where, "text": n.text} for n in session.notes], "candidates": candidates,
            "dashboard": {"active_seconds": d.active_seconds, "headings": [[h, n] for h, n in d.headings.most_common(8)]}}
