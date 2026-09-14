"""Command line: login | fetch-rules | rules | scan | review | place | replay."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from auction_bot.config import ROOT, load_config

app = typer.Typer(add_completion=False, no_args_is_help=True, help="BidBarn: Carbarn's Nichibo auction bidding assistant",
                  pretty_exceptions_enable=False)


def _log() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")


def friendly(fn):
    """Print expected failures as one line instead of a traceback."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (FileNotFoundError, RuntimeError) as exc:
            typer.echo("")
            typer.echo(f"STOPPED: {exc}")
            raise typer.Exit(1)

    return wrapper


@app.command()
@friendly
def login() -> None:
    """Open the bot's browser so you can sign in to Nichibo and the Carbarn dashboard once."""
    _log()
    cfg = load_config()
    from auction_bot.nichibo.browser import Browser

    with Browser(cfg, headless=False) as b:
        b.page.goto(cfg.nichibo.get("base_url", "https://autosearch.nichibojapan.com") + "/auction/search")
        b.context.new_page().goto(cfg.carbarn.get("dashboard_url"))
        typer.echo("Sign in to BOTH tabs (Nichibo and Carbarn), then press Enter here to save the session.")
        input()
    typer.echo(f"Session saved in {cfg.profile_dir}")


@app.command("fetch-rules")
@friendly
def fetch_rules(model: list[str] = typer.Option(None, "--model", "-m", help="Only these model codes"),
                include_inactive: bool = typer.Option(False, help="Also fetch inactive guides")) -> None:
    """Pull Team Bid bidding guides from Carbarn and save a snapshot."""
    _log()
    cfg = load_config()
    from auction_bot.carbarn.client import CarbarnClient, save_snapshot
    from auction_bot.nichibo.browser import Browser

    with Browser(cfg, headless=False) as b:
        client = CarbarnClient(b, cfg)
        guides = client.fetch_team_guides(model or None, include_inactive)
    path = save_snapshot(cfg, guides)
    typer.echo(f"Saved {len(guides)} guides to {path}")
    for g in guides:
        it = g["item"]
        typer.echo(f"  {it['modelCode']:10} {it['make']} {it['model']}  minGrade={it.get('minGrade')}  variants={[(v.get('name') or 'Default') + ('' if v.get('active') else ' (off)') for v in it.get('variants', [])]}")


@app.command()
@friendly
def rules(model: str = typer.Argument(None), snapshot: Path = typer.Option(None)) -> None:
    """Show the parsed rules from the latest snapshot."""
    cfg = load_config()
    from auction_bot.carbarn.client import load_rules, snapshot_age_hours
    from auction_bot.carbarn.models import rule_to_summary

    rs, path = load_rules(cfg, snapshot, [model] if model else None)
    typer.echo(f"snapshot {path.name} ({snapshot_age_hours(path):.1f}h old), {len(rs)} guides")
    for r in rs:
        typer.echo(json.dumps(rule_to_summary(r), indent=1, ensure_ascii=False))
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
    if mode not in ("none", "openai"):
        typer.echo("sheet_check must be 'none' or 'openai'")
        raise typer.Exit(2)
    if mode == "openai" and not cfg.openai_api_key:
        typer.echo("sheet_check is 'openai' but OPENAI_API_KEY is not set in .env. Use --sheet-check none.")
        raise typer.Exit(2)
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


@app.command()
@friendly
def review() -> None:
    """Serve the local review page."""
    cfg = load_config()
    import uvicorn

    from auction_bot.review.app import create_app

    host, port = cfg.review.get("host", "127.0.0.1"), int(cfg.review.get("port", 8765))
    typer.echo(f"Review page: http://{host}:{port}")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


@app.command()
@friendly
def place(live: bool = typer.Option(False, help="Actually submit (also requires dry_run: false in config.yaml)"),
          headless: bool = typer.Option(False)) -> None:
    """Place every approved, not-yet-placed bid. Dry run unless config and --live both allow it."""
    _log()
    cfg = load_config()
    from auction_bot.nichibo.bid import place_bid
    from auction_bot.nichibo.browser import Browser
    from auction_bot.nichibo.facts import VehicleFacts
    from auction_bot.store import Store

    dry, why = cfg.placement_mode(live)
    store = Store(cfg.db_path)
    try:
        rows = store.approved_unplaced(cfg.max_bids_per_run)
        if not rows:
            typer.echo("No approved bids waiting.")
            return
        typer.echo(f"{'DRY RUN' if dry else 'LIVE'} ({why}): {len(rows)} approved bid(s)")
        with Browser(cfg, headless=headless) as b:
            for row in rows:
                if store.already_bid(row["ref_no"]):
                    typer.echo(f"  skip {row['ref_no']}: already bid")
                    continue
                facts = VehicleFacts.from_dict(json.loads(row["facts_json"]))
                amount = int(row["approved_bid_jpy_k"] or row["bid_jpy_k"])
                res = place_bid(b, cfg, row["ref_no"], amount, facts, dry_run=dry)
                store.record_placement(row["id"], row["ref_no"], row["model_code"], amount, res.ok, res.message, res.screenshot, dry)
                typer.echo(f"  {row['model_code']} {row['summary'][:70]} -> {amount}K: {'OK' if res.ok else 'BLOCKED'} {res.message}")
    finally:
        store.close()


@app.command()
@friendly
def replay(model: list[str] = typer.Option(None, "--model", "-m", help="Only these model codes"),
           apply: bool = typer.Option(False, help="Supersede pending proposals whose outcome changed")) -> None:
    """Re-apply the current rules and code to stored pending proposals. No Nichibo traffic.

    Uses the saved facts and the evidence already on disk. Without --apply it only reports.
    """
    _log()
    cfg = load_config()
    from auction_bot.pipeline import reevaluate
    from auction_bot.store import Store

    store = Store(cfg.db_path)
    try:
        st = reevaluate(cfg, store, model or None, apply=apply)
    finally:
        store.close()
    for line in st.changes[:200]:
        typer.echo("  " + line)
    typer.echo(f"checked {st.checked}: unchanged {st.unchanged}, changed {st.changed}, need a fetch {st.needs_fetch}"
               + (f", applied {st.applied}" if apply else "  (dry: add --apply to store the new outcomes)"))


if __name__ == "__main__":
    app()
