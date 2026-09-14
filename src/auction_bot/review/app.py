"""Local review page: approve or reject each proposal before anything is placed."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auction_bot.carbarn.client import append_override, load_rules
from auction_bot.config import Config
from auction_bot.store import Store

TEMPLATES = Path(__file__).parent / "templates"


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
    return d


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="Bid review")
    templates = Jinja2Templates(directory=str(TEMPLATES))
    app.mount("/evidence", StaticFiles(directory=str(cfg.evidence_dir)), name="evidence")

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

    @app.get("/", response_class=HTMLResponse)
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
            nxt = s.conn.execute(
                "SELECT id FROM proposals WHERE decision='pending' AND status IN ('BID','NEEDS_REVIEW') AND id<>? "
                "ORDER BY CASE status WHEN 'BID' THEN 0 ELSE 1 END, id DESC LIMIT 1", (pid,)).fetchone()
        finally:
            s.close()
        return templates.TemplateResponse(request, "detail.html", {"p": p, "next_id": nxt["id"] if nxt else None, "dry_run": cfg.dry_run, "lock_label": lock_label()})

    @app.post("/p/{pid}/decide")
    def decide(pid: int, action: str = Form(...), reason: str = Form(""), amount: str = Form(""),
               make_rule: str | None = Form(None), rule_text: str = Form("")):
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
            s.decide(pid, action, reason.strip() or None, amt, bool(make_rule))
            if make_rule and (rule_text.strip() or reason.strip()):
                append_override(cfg, row["model_code"], rule_text.strip() or reason.strip())
            nxt = s.conn.execute(
                "SELECT id FROM proposals WHERE decision='pending' AND status IN ('BID','NEEDS_REVIEW') "
                "ORDER BY CASE status WHEN 'BID' THEN 0 ELSE 1 END, id DESC LIMIT 1").fetchone()
        finally:
            s.close()
        return RedirectResponse(f"/p/{nxt['id']}" if nxt else "/", status_code=303)

    return app
