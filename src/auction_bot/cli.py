"""Command line: learn | bid | dashboard | login | check-login | fetch-rules | rules | scan | judge | review | place |
replay | insights | playbook | skip-line | watch.

A command started by the dashboard (`BIDBARN_PROGRESS=1`) also prints `PROGRESS` and `SUMMARY` lines
(`progress.py`); from a console the output is unchanged.
"""
from __future__ import annotations

import json
import logging
import threading
import webbrowser
from pathlib import Path

import typer

from auction_bot import progress
from auction_bot.config import ROOT, load_config
from auction_bot.run import port_in_use  # noqa: F401  (the dashboard command and its test use it from here)

app = typer.Typer(add_completion=False, no_args_is_help=True, help="BidBarn: Carbarn's Nichibo auction bidding assistant",
                  pretty_exceptions_enable=False)


def _log() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "openai"):  # the model client logs every request; the judge line says enough
        logging.getLogger(noisy).setLevel(logging.WARNING)


def friendly(fn):
    """Print expected failures as one line instead of a traceback."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except typer.Exit:
            raise  # a deliberate exit code, not a failure (typer.Exit subclasses RuntimeError)
        except (FileNotFoundError, RuntimeError) as exc:
            typer.echo("")
            typer.echo(f"STOPPED: {exc}")
            raise typer.Exit(1)

    return wrapper


@app.command()
@friendly
def login(wait: bool = typer.Option(False, "--wait", help="No keyboard: check the sign-in, open a window only if a site "
                                                          "is signed out, and continue by itself once you are in")) -> None:
    """Open the bot's browser so you can sign in to Nichibo and the Carbarn dashboard once."""
    _log()
    cfg = load_config()
    if wait:
        from auction_bot.run import ensure_signed_in

        ensure_signed_in(cfg, echo=typer.echo)  # returns only when both sites are signed in
        progress.summary({"nichibo": True, "carbarn": True})
        return
    from auction_bot.nichibo.browser import Browser

    with Browser(cfg, headless=False) as b:
        b.page.goto(cfg.nichibo.get("base_url", "https://autosearch.nichibojapan.com") + "/auction/search")
        b.context.new_page().goto(cfg.carbarn.get("dashboard_url"))
        typer.echo("Sign in to BOTH tabs (Nichibo and Carbarn), then press Enter here to save the session.")
        input()
    typer.echo(f"Session saved in {cfg.profile_dir}")


@app.command("check-login")
@friendly
def check_login() -> None:
    """Check, without opening a window, whether the bot's browser is signed in to Nichibo and the Carbarn dashboard."""
    _log()
    cfg = load_config()
    from auction_bot.nichibo.browser import Browser
    from auction_bot.run import carbarn_signed_in, nichibo_signed_in

    with Browser(cfg, headless=True) as b:
        nichibo_ok = bool(nichibo_signed_in(b, cfg))
        carbarn_ok = bool(carbarn_signed_in(b, cfg))
    typer.echo(f"Nichibo: {'signed in' if nichibo_ok else 'NOT signed in'}")
    typer.echo(f"Carbarn dashboard: {'signed in' if carbarn_ok else 'NOT signed in'}")
    progress.summary({"nichibo": nichibo_ok, "carbarn": carbarn_ok})


@app.command("fetch-rules")
@friendly
def fetch_rules(model: list[str] = typer.Option(None, "--model", "-m", help="Only these model codes"),
                include_inactive: bool = typer.Option(False, help="Also fetch inactive guides"),
                headless: bool = typer.Option(False, help="Do not show the browser window")) -> None:
    """Pull Team Bid bidding guides from Carbarn and save a snapshot."""
    _log()
    cfg = load_config()
    from auction_bot.carbarn.client import CarbarnClient, save_snapshot
    from auction_bot.nichibo.browser import Browser

    with Browser(cfg, headless=headless) as b:
        client = CarbarnClient(b, cfg)
        guides = client.fetch_team_guides(model or None, include_inactive)
    path = save_snapshot(cfg, guides)
    typer.echo(f"Saved {len(guides)} guides to {path}")
    for g in guides:
        it = g["item"]
        typer.echo(f"  {it['modelCode']:10} {it['make']} {it['model']}  minGrade={it.get('minGrade')}  variants={[(v.get('name') or 'Default') + ('' if v.get('active') else ' (off)') for v in it.get('variants', [])]}")
    progress.summary({"guides": len(guides), "snapshot": Path(path).name})


@app.command()
@friendly
def rules(model: str = typer.Argument(None), snapshot: Path = typer.Option(None)) -> None:
    """Show the parsed rules from the latest snapshot."""
    cfg = load_config()
    from auction_bot.carbarn.client import load_rules, snapshot_age_hours
    from auction_bot.carbarn.models import rule_to_summary

    from auction_bot.rules.instructions import split_override_lines

    rs, path = load_rules(cfg, snapshot, [model] if model else None)
    typer.echo(f"snapshot {path.name} ({snapshot_age_hours(path):.1f}h old), {len(rs)} guides")
    for r in rs:
        typer.echo(json.dumps(rule_to_summary(r), indent=1, ensure_ascii=False))
        free, instructions, errors = split_override_lines(r)
        for ins in instructions:
            typer.echo(f"   auto-skip: {ins.raw}")
        for err in errors:
            typer.echo(f"   NOT UNDERSTOOD (ignored): {err}")
        if model:
            for v in r.variants:
                typer.echo(f"-- {v.label} ({'on' if v.active else 'off'})")
                for y in v.years:
                    cells = sorted((c for c in v.cells.values() if c.year == y), key=lambda c: c.band.odo_from)
                    typer.echo(f"   {y}: " + "  ".join(f"{c.band.label}={c.bid_jpy_k if not c.no_bid else '-'}" for c in cells))


@app.command()
@friendly
def scan(model: list[str] = typer.Option(None, "--model", "-m", help="Only these model codes"),
         sheet_check: str = typer.Option(None, help="none (you judge the sheet on the review page) or openai; default from config.yaml"),
         rescan: bool = typer.Option(False, help="Re-evaluate vehicles that already have a pending/skip proposal"),
         limit: int = typer.Option(None, help="Open at most N detail pages per model code"),
         fresh: bool = typer.Option(False, help="Ignore cached pages and images (default reuses downloads younger than cache_hours)"),
         headless: bool = typer.Option(False)) -> None:
    """Search Nichibo for every active guide, evaluate, and write proposals for review."""
    _log()
    cfg = load_config()
    from auction_bot.nichibo.browser import Browser
    from auction_bot.pipeline import scan as run_scan
    from auction_bot.store import Store

    mode = (sheet_check or cfg.sheet_check or "none").lower()
    if mode not in ("none", "judge", "openai"):
        typer.echo("sheet_check must be 'none', 'judge' or 'openai'")
        raise typer.Exit(2)
    if mode == "openai" and not cfg.openai_api_key:
        typer.echo("sheet_check is 'openai' but OPENAI_API_KEY is not set in .env. Use --sheet-check none.")
        raise typer.Exit(2)
    cfg.sheet_check = mode
    if mode == "judge":
        from auction_bot.judge.client import providers_for
        ready = [p.name for p in providers_for(cfg) if p.api_key]
        typer.echo("judge: " + (", ".join(ready) + " (free vision model; you confirm on the review page)" if ready
                               else "no provider key in .env (GEMINI_API_KEY / GROQ_API_KEY): lots go to review unjudged"))
    else:
        typer.echo(f"sheet check: {mode}" + ("  (you judge sheets on the review page)" if mode == "none" else f"  (model {cfg.model})"))
    store = Store(cfg.db_path)
    try:
        with Browser(cfg, headless=headless) as b:
            stats = run_scan(cfg, b, store, model or None, no_model=(mode == "none"), rescan=rescan,
                             limit_per_model=limit, fresh=fresh)
    finally:
        store.close()
    typer.echo(json.dumps(stats.__dict__, indent=1))
    typer.echo(f"Review at http://{cfg.review.get('host', '127.0.0.1')}:{cfg.review.get('port', 8765)}  (bot review)")
    progress.summary({**stats.__dict__, "errors": [str(e)[:300] for e in stats.errors[:20]], "errors_total": len(stats.errors)})


@app.command()
@friendly
def judge(pending: bool = typer.Option(True, "--pending/--all", help="Only lots without a judgement (default)"),
          model: list[str] = typer.Option(None, "--model", "-m", help="Only these model codes"),
          limit: int = typer.Option(None, help="Judge at most N lots"),
          no_browser: bool = typer.Option(False, help="Do not open lot pages for photos; such lots stay pending")) -> None:
    """Ask the free vision model about stored lots that are still waiting for a judgement (rate-limited, resumable)."""
    _log()
    cfg = load_config()
    from auction_bot.pipeline import judge_pending
    from auction_bot.store import Store

    if not judge_mode(cfg):
        typer.echo("sheet_check is not 'judge' in config.yaml; nothing to do.")
        progress.summary({"skipped": "sheet_check is not 'judge' in config.yaml"})
        return
    store = Store(cfg.db_path)
    try:
        if no_browser:
            st = judge_pending(cfg, store, model or None, limit=limit)
        else:
            from auction_bot.nichibo.browser import Browser

            with Browser(cfg, headless=True) as b:
                st = judge_pending(cfg, store, model or None, limit=limit, browser=b)
    finally:
        store.close()
    typer.echo(f"checked {st.checked}: judged {st.judged}, pages opened {st.pages}, model unavailable {st.unavailable}"
               + (f", errors {len(st.errors)}" if st.errors else ""))
    for e in st.errors[:10]:
        typer.echo("  " + e)
    progress.summary({"checked": st.checked, "judged": st.judged, "routine": st.routine, "pages": st.pages,
                      "unavailable": st.unavailable, "errors": [str(e)[:300] for e in st.errors[:20]],
                      "errors_total": len(st.errors)})


def judge_mode(cfg) -> bool:
    return (cfg.sheet_check or "none").lower() == "judge"


@app.command()
@friendly
def review() -> None:
    """Serve the local review page."""
    cfg = load_config()
    import uvicorn

    from auction_bot.review.app import create_app

    host, port = cfg.review.get("host", "127.0.0.1"), int(cfg.review.get("port", 8765))
    typer.echo(f"Review page: http://{host}:{port}/review")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


@app.command()
@friendly
def dashboard(no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the page in your browser")) -> None:
    """Serve the dashboard (overview, runs, review) and open it in your browser. One per PC: a second start opens a tab."""
    cfg = load_config()
    host, port = cfg.review.get("host", "127.0.0.1"), int(cfg.review.get("port", 8765))
    url = f"http://{host}:{port}/"
    if port_in_use(host, port):
        typer.echo(f"BidBarn dashboard is already running: {url}")
        if not no_browser:
            webbrowser.open(url)
        return
    import uvicorn

    from auction_bot.review.app import create_app

    typer.echo(f"BidBarn dashboard: {url}")
    if not no_browser:
        timer = threading.Timer(1.5, webbrowser.open, [url])
        timer.daemon = True
        timer.start()
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


@app.command()
@friendly
def place(live: bool = typer.Option(False, help="Actually submit (also requires dry_run: false in config.yaml)"),
          headless: bool = typer.Option(False)) -> None:
    """Place every approved, not-yet-placed bid. Dry run unless config and --live both allow it."""
    _log()
    cfg = load_config()
    from auction_bot.run import place_approved

    lines = place_approved(cfg, echo=typer.echo, headless=headless, live=live)
    progress.summary({"dry_run": cfg.placement_mode(live)[0], "lines": [str(ln)[:300] for ln in (lines or [])]})


@app.command()
@friendly
def bid(skip_scan: bool = typer.Option(False, help="Do not scan; review and place what is already stored"),
        no_place: bool = typer.Option(False, help="Stop after the review"),
        headed: bool = typer.Option(False, help="Show the browser during the scan and the placement"),
        no_browser: bool = typer.Option(False, help="Do not open the review page in your browser")) -> None:
    """Bid mode (bidbarn.cmd, B): sign in if needed, fetch the guides, scan, review page, place what you approve."""
    _log()
    cfg = load_config()
    from auction_bot.run import run as run_all

    run_all(cfg, echo=typer.echo, skip_scan=skip_scan, no_place=no_place, headed=headed, open_browser=not no_browser)


@app.command("scan-now")
@friendly
def scan_now(headed: bool = typer.Option(False, help="Show the browser during the scan")) -> None:
    """Scan now: sign in if needed, fetch the guides and scan. No review wait, nothing placed."""
    _log()
    cfg = load_config()
    from auction_bot.run import scan_now as scan_all_now

    scan_all_now(cfg, echo=typer.echo, headed=headed)


@app.command()
@friendly
def replay(model: list[str] = typer.Option(None, "--model", "-m", help="Only these model codes"),
           apply: bool = typer.Option(False, help="Supersede pending proposals whose outcome changed"),
           answer: list[str] = typer.Option(None, "--answer", help="Preview a Rulebook answer, KEY=VALUE (repeatable)")) -> None:
    """Re-apply the current rules and code to stored pending proposals. No Nichibo traffic.

    Uses the saved facts and the evidence already on disk. Without --apply it only reports.
    --answer previews a Rulebook setting on top of the stored answers and never stores anything.
    """
    _log()
    cfg = load_config()
    from auction_bot import rulebook
    from auction_bot.pipeline import reevaluate
    from auction_bot.store import Store

    hypothetical: dict = {}
    for item in answer or []:
        key, _, raw = item.partition("=")
        q = next((q for q in rulebook.OPEN if q.kind == "setting" and q.setting == key), None)
        try:
            value = int(raw)
        except ValueError:
            value = None
        if q is None or value is None or not rulebook.valid_value(q, value):
            typer.echo(f"{item!r} is not a Rulebook answer: use a setting such as odo_margin_km=2000, "
                       "with one of the values the Rulebook offers.", err=True)
            raise typer.Exit(2)
        hypothetical[key] = value
    if hypothetical and apply:
        typer.echo("--answer only previews; apply it from the Rulebook.", err=True)
        raise typer.Exit(2)
    store = Store(cfg.db_path)
    try:
        version = store.rulebook_version()
        answers = {**store.rulebook_answers(), **hypothetical} if hypothetical else None
        st = reevaluate(cfg, store, model or None, apply=apply, answers=answers)
    finally:
        store.close()
    for line in st.changes[:200]:
        typer.echo("  " + line)
    typer.echo(f"checked {st.checked}: unchanged {st.unchanged}, changed {st.changed}, need a fetch {st.needs_fetch}"
               + (f", errors {len(st.errors)}" if st.errors else "")
               + (f", applied {st.applied}" if apply else "  (dry: add --apply to store the new outcomes)"))
    progress.summary({"checked": st.checked, "unchanged": st.unchanged, "changed": st.changed, "needs_fetch": st.needs_fetch,
                      "applied": st.applied, "apply": bool(apply), "changes": [str(c)[:300] for c in st.changes[:20]],
                      "changes_total": len(st.changes),
                      **({"rulebook_version": version, "answer": hypothetical, "lots": st.lots} if hypothetical else {}),
                      **({"errors": [str(e)[:300] for e in st.errors[:20]], "errors_total": len(st.errors)} if st.errors else {})})


@app.command("sheet-trial")
@friendly
def sheet_trial(count: int = typer.Option(50, help="How many stored sheets to send"),
                model: list[str] = typer.Option(None, "--model", "-m", help="Only these model codes"),
                headless: bool = typer.Option(True, "--headless/--no-headless", help="Hide the bot's browser window")) -> None:
    """Send stored auction sheets to Carbarn's own sheet reader and compare its answers with the bot's reading.

    Only lots whose guide needs the sheet; one paid read per sheet image, never twice. No Nichibo traffic.
    Open /sheet-trial on the dashboard afterwards to mark who was right where they disagree.
    """
    _log()
    cfg = load_config()
    from auction_bot.carbarn.client import load_rules
    from auction_bot.carbarn.sheet_reader import SheetReader
    from auction_bot.nichibo.browser import Browser
    from auction_bot.sheet_trial import run_trial
    from auction_bot.store import Store

    rules, _ = load_rules(cfg)
    store = Store(cfg.db_path)
    try:
        with Browser(cfg, headless=headless) as b:
            reader = SheetReader.for_browser(cfg, b, max_calls=count)
            path, s = run_trial(cfg, store, reader, rules, count, model or None, echo=typer.echo)
    finally:
        store.close()
    typer.echo(f"{s['lots']} sheets: read {s['ok']}, failed {s['failed']}, hard to read {s['low_confidence']}, "
               f"paid reads {s['calls']}, from the cache {s['cached']}, disagreements {s['disagreements']}")
    if s["median_s"] is not None:
        typer.echo(f"time per read: median {s['median_s']} s, slowest {s['max_s']} s")
    typer.echo(f"saved {path}")
    typer.echo(f"decision so far: {s['decision']}")
    host, port = cfg.review.get("host", "127.0.0.1"), int(cfg.review.get("port", 8765))
    typer.echo(f"mark the disagreements: http://{host}:{port}/sheet-trial")
    progress.summary({k: s[k] for k in ("lots", "ok", "failed", "low_confidence", "calls", "cached", "disagreements", "decision")}
                     | {"stamp": s["stamp"]} | ({"errors": [s["signed_out"]]} if s["signed_out"] else {}))


@app.command()
@friendly
def insights(model: list[str] = typer.Option(None, "--model", "-m", help="Only these model codes"),
             days: int = typer.Option(None, help="Only decisions from the last N days"),
             min_support: int = typer.Option(2, help="Rejections a suggested skip rule must explain")) -> None:
    """How you decide, per model: time, what you looked at, rejection themes, and skip rules to consider."""
    cfg = load_config()
    from datetime import datetime, timedelta, timezone

    from auction_bot.insights import propose_skip_lines, render, summarise
    from auction_bot.store import Store

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds") if days else None
    from auction_bot.insights import model_accuracy

    store = Store(cfg.db_path)
    try:
        rows = store.decisions_with_facts(since=since)
        feedback = [dict(r) for r in store.judge_feedback()]
    finally:
        store.close()
    if model:
        wanted = {m.upper() for m in model}
        rows = [r for r in rows if r["model_code"].upper() in wanted]
        feedback = [r for r in feedback if (r["model_code"] or "").upper() in wanted]
    report = render(summarise(rows), propose_skip_lines(rows, min_support=min_support), accuracy=model_accuracy(feedback))
    typer.echo(report)
    progress.summary({"lines": len(report.splitlines()), "decisions": len(rows)})


@app.command()
@friendly
def learn(har: Path = typer.Option(None, "--har", exists=True, dir_okay=False, help="Instead of watching: a HAR saved from your own browser"),
          model: list[str] = typer.Option(None, "--model", "-m", help="With --har: only these model codes"),
          save: bool = typer.Option(False, help="With --har: keep the parsed opens and bids in the store")) -> None:
    """Learn mode (bidbarn.cmd, L): you bid by hand in the bot's browser and type why; the bot watches, then compares."""
    _log()
    cfg = load_config()
    if har is None:
        from auction_bot.run import learn as learn_by_watching

        learn_by_watching(cfg, echo=typer.echo)
        return
    from auction_bot.learn import join_session, parse_har, render_report, save_session
    from auction_bot.store import Store

    session = parse_har(har, cfg.nichibo.get("base_url", "https://autosearch.nichibojapan.com"))
    store = Store(cfg.db_path)
    try:
        rep = join_session(store, session)
        if model:
            wanted = {m.upper() for m in model}
            for name in ("bot_skipped_jim_bid", "agree", "unknown_to_bot", "bot_proposed_not_opened", "opened_no_bid"):
                setattr(rep, name, [it for it in getattr(rep, name) if it["model_code"].upper() in wanted])
            rep.per_model = {k: v for k, v in rep.per_model.items() if k.upper() in wanted}
        typer.echo(render_report(rep))
        if save:
            n = save_session(store, session)
            typer.echo(f"\nSaved {n} new actions to manual_actions (session '{session.name}').")
    finally:
        store.close()


@app.command()
@friendly
def watch(report: Path = typer.Option(None, "--report", exists=True, file_okay=False,
                                      help="Print the timeline of a recorded session folder instead of recording")) -> None:
    """Open the bot's browser for you and record how you work on Nichibo (the bot only watches)."""
    _log()
    cfg = load_config()
    from auction_bot.watch import load_events, render_session, timeline

    if report is not None:
        from auction_bot.store import Store

        store = Store(cfg.db_path)
        try:
            def bot_view(ref: str) -> str | None:
                row = store.latest_proposal(ref)
                if row is None:
                    return "never seen"
                return f"{row['status']}" + (f" {row['bid_jpy_k']:,}K" if row["bid_jpy_k"] else "") + f", {row['decision']}"
            typer.echo(render_session(timeline(load_events(report), str(report)), bot_view))
        finally:
            store.close()
        return
    from auction_bot.watch import watch as run_watch

    out = run_watch(cfg, echo=typer.echo)
    typer.echo(f"Recorded to {out}. Timeline: bot watch --report \"{out}\"")
    typer.echo(render_session(timeline(load_events(out), str(out))))


@app.command()
@friendly
def playbook(code: str = typer.Argument(None, help="Model code, e.g. ZWR90; none lists every code with notes")) -> None:
    """What the judgement model is told about how you decide: your principles, your corrections and comments per model code."""
    cfg = load_config()
    from auction_bot.judge.playbook import code_lines, general_lines, playbook_dir
    from auction_bot.store import Store

    if not code:
        files = sorted(p for p in playbook_dir(cfg).glob("*.md") if p.stem != "general")
        typer.echo(f"General principles: {len(general_lines(cfg))} lines ({playbook_dir(cfg) / 'general.md'})")
        for p in files:
            typer.echo(f"  {p.stem:10} {len(code_lines(cfg, p.stem))} line(s)")
        if not files:
            typer.echo("  no model code has notes yet: they come from learn-mode comments and review-page corrections")
        return
    code = code.upper()
    typer.echo("# Principles (rules/playbook/general.md)")
    for ln in general_lines(cfg):
        typer.echo(f"- {ln}")
    typer.echo(f"\n# Your corrections and comments on {code} (rules/playbook/{code}.md)")
    lines = code_lines(cfg, code)
    for ln in lines or ["(none yet)"]:
        typer.echo(f"- {ln}")
    store = Store(cfg.db_path)
    try:
        notes = store.manual_notes(code)
        rows = store.decisions_with_facts(code)
        feedback = store.judge_feedback(code)
    finally:
        store.close()
    typer.echo(f"\n# Learn-mode comments on {code}: {len(notes)}")
    for n in notes[:10]:
        typer.echo(f"- {n}")
    typer.echo(f"\n# Your review decisions on {code}: {len(rows)} ({sum(1 for r in rows if r['action'] == 'approve')} approved); "
               f"the most similar ones go into every judgement of a {code} lot")
    for r in rows[:8]:
        reason = f": {r['reason']}" if r.get("reason") else ""
        amount = f" at {r['amount_jpy_k']:,}K" if r.get("amount_jpy_k") else ""
        typer.echo(f"- {r['action']}{amount} {(r.get('summary') or '')[:60]}{reason}")
    typer.echo(f"\n# Times you overruled the model on {code}: {len(feedback)}")


@app.command("skip-line")
@friendly
def skip_line(code: str = typer.Argument(..., help="Model code, e.g. GDH206"),
              line: str = typer.Argument(..., help='e.g. "skip: variant contains DUMP" or "km > 120000"')) -> None:
    """Add a structured standing instruction (a skip rule) for a model code. Validated before it is stored."""
    cfg = load_config()
    from auction_bot.carbarn.client import append_override
    from auction_bot.rules.instructions import InstructionError, is_structured, parse_instruction

    text = line.strip() if is_structured(line) else "skip: " + line.strip()
    try:
        ins = parse_instruction(text)
    except InstructionError as exc:
        typer.echo(f"Not stored: {exc}")
        raise typer.Exit(2)
    path = append_override(cfg, code, text, author="cli")
    typer.echo(f"Added to {path}: {ins.raw}  ({', '.join(c.describe() for c in ins.conditions)})")
    typer.echo("Run `bot replay --apply -m " + code.upper() + "` to apply it to stored proposals.")


if __name__ == "__main__":
    app()
