"""Local review page: approve or reject each proposal before anything is placed.

The triage view (`/`) lists pending lots the way Jim sorts them by hand; the detail page
(`/p/{id}`) shows everything about one lot. Both post to the same `decide` endpoint,
which is the only place a proposal can be approved (CLAUDE.md, rule 1). What the
reviewer looks at and how long a decision takes is logged for `bot insights`.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auction_bot.carbarn.client import append_override, load_rules
from auction_bot.config import Config
from auction_bot.insights import propose_skip_lines
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.instructions import InstructionError, is_structured, parse_instruction, suggest_from_reason
from auction_bot.store import EVENTS, Store

TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"


def _load(row) -> dict:
    d = dict(row)
    for k in ("facts_json", "hard_json", "soft_json", "evidence_json", "placement_json"):
        v = d.get(k)
        try:
            d[k[:-5]] = json.loads(v) if v else None
        except json.JSONDecodeError:
            d[k[:-5]] = None
    ev = d.get("evidence") or {}
    base = None
    if ev.get("dir"):
        base = Path(ev["dir"]).name
    d["evidence_base"] = base
    d["sheet_file"] = Path(ev["sheet"]).name if ev.get("sheet") else None
    d["photo_files"] = [Path(p).name for p in ev.get("photos", [])]
    d["sheet_src"] = f"/evidence/{base}/{d['sheet_file']}" if base and d["sheet_file"] else ((d.get("facts") or {}).get("sheet_url") or "")
    return d


def triage_sort_key(p: dict) -> tuple:
    """Within a model: best grade first, then fewest km, then newest proposal (Jim's manual order)."""
    f = p.get("facts") or {}
    km = f.get("km")
    return (p["model_code"], -float(f.get("grade") or 0), km if km is not None else 10**9, -p["id"])


def _wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "")


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="Bid review")
    templates = Jinja2Templates(directory=str(TEMPLATES))
    app.mount("/evidence", StaticFiles(directory=str(cfg.evidence_dir)), name="evidence")
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    def store() -> Store:
        return Store(cfg.db_path)

    guide_names: dict[int, str] = {}
    try:
        for r, _ in [load_rules(cfg)]:
            guide_names.update({rule.guide_id: f"{rule.make} {rule.model} {rule.model_code}" for rule in r})
    except Exception:  # no snapshot yet: fall back to model codes
        pass

    def lock_label() -> str:
        if not cfg.project_finished:
            return "TESTING: no bids are placed"
        return "DRY RUN" if cfg.dry_run else "LIVE PLACEMENT"

    def next_pending(s: Store, exclude: int | None = None) -> int | None:
        row = s.conn.execute(
            "SELECT id FROM proposals WHERE decision='pending' AND status IN ('BID','NEEDS_REVIEW') AND id<>? "
            "ORDER BY CASE status WHEN 'BID' THEN 0 ELSE 1 END, id DESC LIMIT 1", (exclude or -1,)).fetchone()
        return row["id"] if row else None

    @app.get("/", response_class=HTMLResponse)
    def triage(request: Request, model: str | None = None):
        s = store()
        try:
            rows = [_load(r) for r in s.list_triage(model or None)]
            counts = s.counts()
            multi = s.multi_guide_refs()
            rows.sort(key=triage_sort_key)
            groups = []
            for p in rows:
                p["multi_guide"] = p["ref_no"] in multi
                if not groups or groups[-1]["model_code"] != p["model_code"]:
                    groups.append({"model_code": p["model_code"], "guide_name": guide_names.get(p["guide_id"], p["model_code"]),
                                   "rows": [], "suggestions": propose_skip_lines(s.decisions_with_facts(p["model_code"]))})
                groups[-1]["rows"].append(p)
        finally:
            s.close()
        return templates.TemplateResponse(request, "triage.html", {
            "groups": groups, "counts": counts, "model": model or "", "lock_label": lock_label(), "dry_run": cfg.dry_run,
        })

    @app.get("/list", response_class=HTMLResponse)
    def index(request: Request, decision: str = "pending", status: str | None = None, model: str | None = None):
        s = store()
        try:
            rows = [_load(r) for r in s.list_proposals(decision or None, status or None, model or None)]
            counts = s.counts()
            multi = s.multi_guide_refs()
        finally:
            s.close()
        for row in rows:
            row["multi_guide"] = row["ref_no"] in multi
        return templates.TemplateResponse(request, "index.html", {
            "rows": rows, "counts": counts, "decision": decision, "status": status or "", "model": model or "",
            "dry_run": cfg.dry_run, "lock_label": lock_label(),
        })

    @app.get("/p/{pid}", response_class=HTMLResponse)
    def detail(request: Request, pid: int):
        s = store()
        try:
            row = s.proposal(pid)
            if row is None:
                raise HTTPException(404)
            p = _load(row)
            p["siblings"] = [dict(x, guide_name=guide_names.get(x["guide_id"], x["model_code"])) for x in s.siblings(p["ref_no"], pid)]
            p["guide_name"] = guide_names.get(p["guide_id"], p["model_code"])
            dec = s.decision_for(pid)
            p["telemetry"] = dict(dec) if dec else None
            counts = s.counts()
            nxt = next_pending(s, pid)
            if row["decision"] == "pending":
                s.log_event(pid, "open", "detail")
        finally:
            s.close()
        return templates.TemplateResponse(request, "detail.html", {"p": p, "next_id": nxt, "counts": counts,
                                                                    "dry_run": cfg.dry_run, "lock_label": lock_label()})

    @app.post("/p/{pid}/event")
    async def log_event(pid: int, request: Request):
        """The page reports what the reviewer looked at (sheet zoom, photos, amount edits)."""
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(400, "json body expected")
        event = str(data.get("event") or "")
        if event not in EVENTS or event == "decide":
            raise HTTPException(400, f"unknown event {event!r}")
        value = data.get("value")
        s = store()
        try:
            if s.proposal(pid) is None:
                raise HTTPException(404)
            s.log_event(pid, event, None if value is None else str(value)[:200])
        finally:
            s.close()
        return Response(status_code=204)

    @app.post("/p/{pid}/decide")
    def decide(request: Request, pid: int, action: str = Form(...), reason: str = Form(""), amount: str = Form(""),
               make_rule: str | None = Form(None), rule_text: str = Form(""), via: str = Form("detail")):
        if action not in ("approve", "reject"):
            raise HTTPException(400, "bad action")
        s = store()
        try:
            row = s.proposal(pid)
            if row is None:
                raise HTTPException(404)
            if row["decision"] != "pending":
                raise HTTPException(409, "already decided")
            amt = None
            if action == "approve":
                amt = int(amount) if amount.strip() else row["bid_jpy_k"]
                if not amt or amt <= 0:
                    raise HTTPException(400, "approve needs a bid amount")
                facts = json.loads(row["facts_json"] or "{}")
                start = facts.get("start_price_jpy") or 0
                if amt * 1000 < start:
                    raise HTTPException(400, f"amount {amt}K is below start price {start}")
            else:
                if not reason.strip():
                    raise HTTPException(400, "reject needs a reason")
            line = rule_text.strip() or reason.strip()
            if make_rule and is_structured(line):
                try:
                    parse_instruction(line)  # a skip line that cannot be read is refused, not stored
                except InstructionError as exc:
                    raise HTTPException(400, f"standing instruction not understood: {exc}")
            s.decide(pid, action, reason.strip() or None, amt, bool(make_rule), via=via if via in ("detail", "triage") else "detail")
            if make_rule and line:
                append_override(cfg, row["model_code"], line)
            nxt = next_pending(s, pid)
            decision = s.proposal(pid)["decision"]
        finally:
            s.close()
        if _wants_json(request):
            return JSONResponse({"ok": True, "decision": decision, "amount": amt, "next_id": nxt})
        return RedirectResponse(f"/p/{nxt}" if nxt else "/", status_code=303)

    @app.get("/p/{pid}/suggest")
    def suggest(pid: int, reason: str = ""):
        """A `skip:` line that would have skipped this lot for the reason typed, as a pre-fill."""
        s = store()
        try:
            row = s.proposal(pid)
        finally:
            s.close()
        if row is None:
            raise HTTPException(404)
        facts = VehicleFacts.from_dict(json.loads(row["facts_json"] or "{}"))
        return JSONResponse({"line": suggest_from_reason(reason, facts)})

    @app.post("/rules/{code}/skip")
    def add_skip_line(code: str, line: str = Form(...), next: str = Form("/")):
        """Store a structured standing instruction for a model code (validated first)."""
        text = line.strip()
        if not is_structured(text):
            text = "skip: " + text
        try:
            parse_instruction(text)
        except InstructionError as exc:
            raise HTTPException(400, f"standing instruction not understood: {exc}")
        append_override(cfg, code.upper(), text)
        return RedirectResponse(next if next.startswith("/") else "/", status_code=303)

    return app
