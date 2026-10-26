"""Local review page: approve or reject each proposal before anything is placed.

The triage view (`/`) lists pending lots the way Jim sorts them by hand; the detail page
(`/p/{id}`) shows everything about one lot. Both post to the same `decide` endpoint,
which is the only place a proposal can be approved (CLAUDE.md, rule 1). What the
reviewer looks at and how long a decision takes is logged for `bot insights`. Delete
(`/p/{id}/delete`, `/sessions/{key}/delete`) takes waiting lots out of the review without a
decision: nothing is recorded for the learning, and the next session's scan checks them again.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from auction_bot.carbarn.client import append_override
from auction_bot.config import Config
from auction_bot.dashboard import status as dashboard_status
from auction_bot.insights import propose_skip_lines
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.instructions import InstructionError, is_structured, parse_instruction, suggest_from_reason
from auction_bot.store import EVENTS, Refused, Store, VehicleClaimed

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
    soft = d.get("soft") or {}
    d["judge"] = soft if soft.get("mode") == "judge" else None
    d["recommendation"] = soft.get("recommendation") if d["judge"] else None
    d["proposed_bid_jpy_k"] = (soft.get("proposed_bid_jpy_k") if d["judge"] else None) or d.get("bid_jpy_k")
    d["badges"] = judge_badges(soft) if d["judge"] else []
    d["eligibility"] = eligibility_view(soft)
    d["ladders"] = {l["id"]: _safe_ladder(l) for l in (soft.get("ladders") or [])}
    for r in soft.get("requirements") or []:
        if r.get("kind") == "chip" and r["id"] in d["ladders"]:
            d["ladders"][r["id"]] = {**d["ladders"][r["id"]], "applies": _ladder_applies(r, d["ladders"][r["id"]])}
    d["catalog"] = {**soft["catalog"], "urls": [u for u in soft["catalog"].get("urls") or [] if _is_http(u)]} if soft.get("catalog") else None
    lines = check_lines(d.get("hard"), soft)
    d["verdict_counts"] = verdict_counts(d["badges"])
    d["verdict_title"] = verdict_title(d["badges"])
    d["why_line"] = why_line(d, lines)
    d["more_lines"] = max(0, len(lines) - 1) if not d["judge"] else 0
    return d


REC_RANK = {"bid": 0, "review": 1, "skip": 2}
VERDICT_WORDS = {"✓": "pass", "✗": "fail", "?": "unclear", "·": "unclear"}


def check_lines(hard: dict | None, soft: dict | None) -> list[str]:
    """What the reviewer has to check on a lot: the failed hard checks, then the checklist without the
    lines that only say how the lot was judged (the same list the review row used to print)."""
    out = [f"{c.get('name')}: {c.get('detail')}" for c in (hard or {}).get("checks") or [] if not c.get("ok")]
    out += [c for c in (soft or {}).get("checklist") or [] if not str(c).startswith(("Auto-skip", "Judged from"))]
    return out


def verdict_counts(badges: list[dict]) -> dict:
    """How many requirements the model passed, failed and left unclear, from `judge_badges`."""
    counts = {"ok": 0, "bad": 0, "unk": 0}
    for b in badges:
        counts[b["cls"] if b["cls"] in counts else "unk"] += 1
    return counts


def verdict_title(badges: list[dict]) -> str:
    """Every requirement on its own line ("pass  No sticker paint: photo 2: painted body (high)"), for a title attribute."""
    lines = []
    for b in badges:
        word = VERDICT_WORDS.get(b["mark"], b["mark"])
        lines.append(f"{word}  {b['text']}" + (f": {b['evidence']}" if b.get("evidence") else ""))
    return "\n".join(lines)


def why_line(p: dict, lines: list[str] | None = None) -> str:
    """The one line the review row shows: a judged lot's first failed requirement, else the model's summary,
    else the first thing the reviewer has to check."""
    if lines is None:
        lines = check_lines(p.get("hard"), p.get("soft"))
    view = p.get("eligibility")
    if view and view["verdict"] == "cannot_import":
        return f"cannot import: {_eligibility_reason(view)}"
    if p.get("judge"):
        failed = next((b for b in p.get("badges") or [] if b["cls"] == "bad"), None)
        if failed:
            return f"{failed['text']}: {failed['evidence']}" if failed.get("evidence") else failed["text"]
        summary = ((p["judge"].get("judgement") or {}).get("summary") or "").strip()
        if summary:
            return summary
    return lines[0] if lines else ""


_ELIG_NUMBERED = re.compile(r"^elig:[a-z-]+:(\d+)$")
ELIG_WORDS = {"cannot_import": "cannot import", "confirm": "confirm import", "eligible": "eligible"}


def eligibility_view(soft: dict | None) -> dict | None:
    """The import-eligibility result for the review page: the stored checks plus what the model said about each
    pair text (`elig:*` ids). A failed deterministic check or a model fail reads cannot import, anything unclear
    reads confirm. When no pair is selected the numbered pairs are alternatives: the lot only needs to fit one."""
    e = (soft or {}).get("eligibility")
    if not e:
        return None
    said = {v.get("id"): v for v in ((soft.get("judgement") or {}).get("requirements") or [])}
    texts: list[dict] = []
    groups: dict[int, list[dict]] = {}
    for t in e.get("texts") or []:
        v = said.get(t.get("id")) or {}
        row = {**t, "verdict": v.get("verdict"), "evidence": v.get("evidence")}
        texts.append(row)
        m = _ELIG_NUMBERED.match(t.get("id") or "")
        if m:
            groups.setdefault(int(m.group(1)), []).append(row)
    alts = e.get("alternatives") or []
    for n, rows in groups.items():
        for row in rows:
            row["group"] = alts[n - 1]["label"] if n <= len(alts) else f"Pair {n}"
    plain = [t for t in texts if "group" not in t]
    model = "eligible"
    if any(t["verdict"] == "fail" for t in plain):
        model = "cannot_import"
    elif any(t["verdict"] == "unclear" for t in plain):
        model = "confirm"
    if groups and any(t["verdict"] for rows in groups.values() for t in rows):
        if not any(all(t["verdict"] == "pass" for t in rows) for rows in groups.values()):
            every_fail = all(any(t["verdict"] == "fail" for t in rows) for rows in groups.values())
            if every_fail:
                model = "cannot_import"
            elif model == "eligible":
                model = "confirm"
    stored = e.get("verdict") or "eligible"
    verdict = next((w for w in ("cannot_import", "confirm") if w in (stored, model)), "eligible")
    label = ELIG_WORDS.get(verdict, verdict)
    if verdict == "eligible" and any(c.get("name") == "model_code" and c.get("state") == "info" for c in e.get("checks") or []):
        label = "eligible (model code not checked)"
    better = None
    if e.get("pair") and verdict != "eligible":
        better = next((a["label"] for a in alts if a.get("verdict") == "eligible"), None)
    return {**e, "verdict": verdict, "texts": texts, "label": label, "grouped": bool(groups), "better_pair": better}


def _eligibility_reason(view: dict) -> str:
    """Why a lot cannot be imported: the first failed check, else the first failed pair text with the model's evidence."""
    bad = next((c for c in view.get("checks") or [] if c.get("state") == "fail"), None)
    if bad:
        return bad.get("detail") or bad.get("name") or ""
    t = next((t for t in view.get("texts") or [] if t.get("verdict") == "fail"), None)
    if t:
        return f"{t['label']}: {t['evidence']}" if t.get("evidence") else t["label"]
    return "no approval pair fits"


def _is_http(url) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def _safe_ladder(ladder: dict) -> dict:
    """The stored ladder with every step url that is not http(s) blanked, so a stored value can never become a javascript: href."""
    steps = [{**s, "url": s.get("url") if _is_http(s.get("url")) else ""} for s in ladder.get("steps") or []]
    return {**ladder, "steps": steps}


def _ladder_applies(req: dict, ladder: dict) -> str:
    """Whether a ladder chip applies: the ladder's verdict says the car has it, the chip's own wording decides what that means."""
    from auction_bot.judge.ladders import chip_applies
    from auction_bot.judge.requirements import Requirement

    return chip_applies(Requirement(**req), ladder.get("verdict"))


def judge_badges(soft: dict) -> list[dict]:
    """One badge per requirement and chip: mark, short text, evidence (for the title attribute)."""
    j = soft.get("judgement") or {}
    verdicts = {v.get("id"): v for v in j.get("requirements", [])}
    chips = {c.get("id"): c for c in j.get("chips", [])}
    ladders = {l.get("id"): l for l in (soft.get("ladders") or [])}
    out = []
    for r in soft.get("requirements", []):
        lad = ladders.get(r["id"])
        if lad and r.get("kind") == "chip":
            applies = _ladder_applies(r, lad)
            out.append({"mark": {"yes": "chip", "no": "no chip"}.get(applies, "?"), "cls": {"yes": "unk", "no": "ok"}.get(applies, "unk"),
                        "text": f"{r['text']} {r['amount_k']:+d}K" if r.get("amount_k") else r["text"], "evidence": lad.get("evidence", "")})
        elif lad:
            verdict = lad.get("verdict")
            out.append({"mark": {"pass": "✓", "fail": "✗", "unclear": "?"}.get(verdict, "·"), "cls": {"pass": "ok", "fail": "bad"}.get(verdict, "unk"),
                        "text": r["text"][:40], "evidence": lad.get("evidence", "")})
        elif r.get("kind") == "chip":
            c = chips.get(r["id"], {})
            mark = {"yes": "chip", "no": "no chip", "unclear": "?"}.get(c.get("applies"), "?")
            out.append({"mark": mark, "cls": {"yes": "unk", "no": "ok"}.get(c.get("applies"), "unk"),
                        "text": f"{r['text']} {r['amount_k']:+d}K" if r.get("amount_k") else r["text"], "evidence": c.get("evidence", "")})
        else:
            v = verdicts.get(r["id"], {})
            verdict = v.get("verdict", "unclear" if j else "")
            mark = {"pass": "✓", "fail": "✗", "unclear": "?"}.get(verdict, "·")
            out.append({"mark": mark, "cls": {"pass": "ok", "fail": "bad"}.get(verdict, "unk"), "text": r["text"][:40],
                        "evidence": v.get("evidence", "") + (f" ({v['confidence']})" if v.get("confidence") else "")})
    return out


def triage_sort_key(p: dict) -> tuple:
    """Within a model: the model's recommendation (bid, review, skip), then best grade, fewest km, newest."""
    f = p.get("facts") or {}
    km = f.get("km")
    return (p["model_code"], REC_RANK.get(p.get("recommendation"), 1), -float(f.get("grade") or 0),
            km if km is not None else 10**9, -p["id"])


def model_groups(s: Store, rows: list[dict], names: dict[int, str], multi: set[str]) -> list[dict]:
    """Rows already in triage order, grouped by model code as the review page shows them."""
    groups: list[dict] = []
    for p in rows:
        p["multi_guide"] = p["ref_no"] in multi
        if not groups or groups[-1]["model_code"] != p["model_code"]:
            groups.append({"model_code": p["model_code"], "guide_name": names.get(p["guide_id"], p["model_code"]),
                           "rows": [], "suggestions": propose_skip_lines(s.decisions_with_facts(p["model_code"]))})
        groups[-1]["rows"].append(p)
    return groups


def session_state(status: str, p: dict) -> str:
    """Where a session stands, in the words of its section head on the review page."""
    where = f" at guide {p['guide']} of {p['guides']}" if p.get("guide") and p.get("guides") else ""
    if status == "running":
        stage = p.get("stage")
        if stage == "review":
            return "waiting for your review"
        if stage == "place":
            return "placing the approved bids"
        return "scanning" + where if stage in ("scan", "judge") else "starting"
    return {"paused": "paused" + where, "stopped": "stopped", "ok": "finished", "failed": "failed"}.get(status, status)


def session_view(key: int | None, info: dict | None, live: dict | None, groups: list[dict],
                 total: int | None = None) -> dict:
    """One section of the review page: the session's name, where it stands, its lots by model, and whether
    Delete session is offered (not while it places bids). `total` is every waiting lot of the session, which
    is what Delete session takes; it is more than the rows shown when the page is filtered by model."""
    from auction_bot.dashboard.jobs import COMMANDS

    count = sum(len(g["rows"]) for g in groups)
    total = max(total or 0, count)
    if key is None or info is None:
        return {"key": "earlier", "title": "Earlier scans", "state": "", "count": count, "total": total, "live": False,
                "can_delete": True, "groups": groups}
    running = bool(live and live.get("session_id") == key)
    p = (live.get("progress") if running else info.get("progress")) or {}
    label = COMMANDS[info["command"]].label if info["command"] in COMMANDS else info["command"]
    return {"key": str(key), "title": f"{label} · {dashboard_status.local_time(info['started_at'])}",
            "state": session_state("running" if running else info["status"], p), "count": count, "total": total,
            "live": running, "can_delete": not (running and p.get("stage") == "place"), "groups": groups}


def _wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "")


STATUS_LABELS = {"BID": "Bid", "NEEDS_REVIEW": "Needs review", "SKIP": "Skip"}
_ICON_NAME = re.compile(r"^[a-z0-9-]{1,40}$")
_ICON_CLASS = re.compile(r"^[A-Za-z0-9_ -]{0,60}$")


def status_label(status: str | None) -> str:
    """The proposal status as the page words it; the stored values stay BID, SKIP, NEEDS_REVIEW."""
    return STATUS_LABELS.get(status or "", status or "")


_asset_v = ""


def asset_version() -> str:
    """Changes whenever a file under static/ changes, so a browser never shows an old stylesheet, script or icon."""
    newest = max((p.stat().st_mtime for p in STATIC.rglob("*") if p.is_file()), default=0)
    return format(int(newest), "x")


def lot_title(summary: str | None) -> str:
    """The lot's name from its stored one-line summary ("2020 Toyota Hiace Van | GDH206V | ..."): the first part."""
    return (summary or "").split("|", 1)[0].strip()


def icon(name: str, cls: str = "") -> Markup:
    """One icon from the Lucide sprite (`static/icons.svg`), sized and coloured by CSS."""
    if not _ICON_NAME.match(name) or not _ICON_CLASS.match(cls):
        raise ValueError(f"bad icon {name!r} {cls!r}")
    classes = "icon" + (f" {cls}" if cls else "")
    return Markup(f'<svg class="{classes}" aria-hidden="true" focusable="false">'
                  f'<use href="/static/icons.svg?v={_asset_v}#i-{name}"></use></svg>')


def feedback_lines(loaded: dict, action: str, amount: int | None, reason: str, req_id: str, note: str) -> list[tuple[str, str, str, str]]:
    """(requirement id, what the model said, what Jim said, playbook line) for a judged lot Jim overruled or corrected."""
    judge = loaded.get("judge") or {}
    j = judge.get("judgement") or {}
    if not j:
        return []
    out = []
    lot = (loaded.get("summary") or "")[:70]
    reqs = {r["id"]: r for r in judge.get("requirements", [])}
    if req_id and note:
        v = next((x for x in j.get("requirements", []) if x.get("id") == req_id), None) or \
            next((x for x in j.get("chips", []) if x.get("id") == req_id), None) or {}
        lad = next((x for x in judge.get("ladders") or [] if x.get("id") == req_id), None)
        if lad and reqs.get(req_id, {}).get("kind") == "chip":
            said = f"chip applies {_ladder_applies(reqs[req_id], lad)}: {lad.get('evidence', '')}"
        elif lad:
            said = f"{lad.get('verdict') or '?'}: {lad.get('evidence', '')}"
        else:
            said = f"{v.get('verdict') or v.get('applies') or '?'}: {v.get('evidence', '')}"
        text = reqs.get(req_id, {}).get("text", req_id)
        out.append((req_id, said, note, f"Model was wrong about '{text}' (it said {said}); Jim: {note} [{lot}]"))
    rec = judge.get("recommendation")
    if action == "reject" and rec == "bid":
        out.append(("overall", f"recommended bid {judge.get('proposed_bid_jpy_k')}K", reason,
                    f"Model recommended a bid but Jim rejected: {reason} [{lot}]"))
    elif action == "approve" and rec == "skip":
        out.append(("overall", "recommended skip", f"approved at {amount}K",
                    f"Model recommended skip but Jim bid {amount}K [{lot}]"))
    elif action == "approve" and amount and judge.get("proposed_bid_jpy_k") and amount != judge["proposed_bid_jpy_k"]:
        out.append(("amount", f"proposed {judge['proposed_bid_jpy_k']}K", f"{amount}K",
                    f"Model proposed {judge['proposed_bid_jpy_k']}K, Jim bid {amount}K" + (f": {reason}" if reason else "") + f" [{lot}]"))
    return out


def record_feedback(s: Store, loaded: dict, action: str, amount: int | None, reason: str, req_id: str, note: str) -> None:
    from auction_bot.judge.playbook import append_playbook

    for rid, said, jim, line in feedback_lines(loaded, action, amount, reason, req_id, note):
        s.add_judge_feedback(loaded["id"], rid, said, jim, note or reason or None)
        append_playbook(s.cfg, loaded["model_code"], line, author="review") if getattr(s, "cfg", None) else None


def create_app(cfg: Config, finish_event=None, jobs=None) -> FastAPI:
    """The whole local page: the overview and runs (`dashboard/routes.py`) and the review pages here.

    `finish_event` is set by `bot bid`: the page then shows "Finish review", which hands control
    back to the console so the approved bids can be placed. `jobs` runs the commands started from
    the page; `bot bid` passes a manager that starts nothing, tests pass a fake one."""
    from contextlib import asynccontextmanager

    from auction_bot.dashboard import routes as dashboard_routes
    from auction_bot.review import desk_routes
    from auction_bot.dashboard.jobs import JobManager, NotRunning

    jobs = jobs if jobs is not None else JobManager(cfg)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        jobs.close()  # stops watching; a run that is going finishes by itself

    app = FastAPI(title="BidBarn", lifespan=lifespan)
    app.state.jobs = jobs
    templates = Jinja2Templates(directory=str(TEMPLATES))

    class CanFinish:
        """Whether the header shows Finish: the console's `bot bid` is waiting, or a dashboard Bid run is.
        Callable (`can_finish()`), and true or false on its own for a template that tests it directly."""

        def __call__(self) -> bool:
            return finish_event is not None or jobs.review_waiting() is not None

        def __bool__(self) -> bool:
            return self()

    templates.env.globals["can_finish"] = CanFinish()
    templates.env.globals["icon"] = icon
    templates.env.globals["status_label"] = status_label
    templates.env.globals["lot_title"] = lot_title
    global _asset_v
    _asset_v = templates.env.globals["asset_v"] = asset_version()
    app.mount("/evidence", StaticFiles(directory=str(cfg.evidence_dir)), name="evidence")
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    def store() -> Store:
        s = Store(cfg.db_path)
        s.cfg = cfg  # so feedback can reach the playbook files
        return s

    def guide_names() -> dict[int, str]:
        """Guide id to "Make Model CODE", from the newest snapshot each time (a guides fetch changes it)."""
        try:
            codes = dashboard_status.rules_state(cfg)["codes"]
        except Exception:  # no snapshot yet, or one that cannot be read: the pages fall back to model codes
            return {}
        return {g["guide_id"]: f"{g['name']} {g['code']}".strip() for g in codes}

    def lock_label() -> str:
        if not cfg.project_finished:
            return "TESTING: no bids are placed"
        return "DRY RUN" if cfg.dry_run else "LIVE PLACEMENT"

    def lock_state() -> str:
        """testing | dry | live, for the page's body attribute."""
        if not cfg.project_finished:
            return "testing"
        return "dry" if cfg.placement_mode(False)[0] else "live"

    def next_pending(s: Store, exclude: int | None = None) -> int | None:
        row = s.conn.execute(
            "SELECT id FROM proposals WHERE decision='pending' AND status IN ('BID','NEEDS_REVIEW') AND id<>? "
            "ORDER BY CASE status WHEN 'BID' THEN 0 ELSE 1 END, id DESC LIMIT 1", (exclude or -1,)).fetchone()
        return row["id"] if row else None

    def page(request: Request, name: str, nav: str, **context):
        """A template inside the shell: the counts for the nav and the lock for the header."""
        if "counts" not in context:
            s = store()
            try:
                context["counts"] = s.counts()
            finally:
                s.close()
        context.update(nav=nav, lock_label=lock_label(), dry_run=cfg.dry_run, lock_state=lock_state())
        return templates.TemplateResponse(request, name, context)

    dashboard_routes.register(app, templates, cfg, jobs, store, page)
    desk_routes.register(app, templates, cfg, store, page, load=_load, sort_key=triage_sort_key)

    @app.get("/review", response_class=HTMLResponse)
    def triage(request: Request, model: str | None = None):
        s = store()
        try:
            rows = [_load(r) for r in s.list_triage(model or None)]
            counts = s.counts()
            multi = s.multi_guide_refs()
            rows.sort(key=triage_sort_key)
            names = guide_names()
            live = jobs.current()
            keys = sorted({p["session_id"] for p in rows}, key=lambda k: (k is None, -(k or 0)))  # newest first, earlier last
            sessions = [session_view(k, s.session_info(k) if k is not None else None, live,
                                     model_groups(s, [p for p in rows if p["session_id"] == k], names, multi),
                                     total=s.session_waiting(k))
                        for k in keys]
        finally:
            s.close()
        return templates.TemplateResponse(request, "triage.html", {
            "sessions": sessions, "total": len(rows), "models": len({p["model_code"] for p in rows}), "counts": counts,
            "model": model or "", "lock_label": lock_label(), "dry_run": cfg.dry_run, "lock_state": lock_state(),
            "nav": "review",
        })

    @app.get("/list", response_class=HTMLResponse)
    def index(request: Request, decision: str = "pending", status: str | None = None, model: str | None = None,
              placed: str | None = None):
        s = store()
        try:
            rows = [_load(r) for r in s.list_proposals(decision or None, status or None, model or None)]
            if placed in ("0", "1"):  # approved lots: the ones a placement has been recorded for, or the others
                rows = [r for r in rows if bool(r.get("placed_at")) == (placed == "1")]
            counts = s.counts()
            multi = s.multi_guide_refs()
        finally:
            s.close()
        for row in rows:
            row["multi_guide"] = row["ref_no"] in multi
        return templates.TemplateResponse(request, "index.html", {
            "rows": rows, "counts": counts, "decision": decision, "status": status or "", "model": model or "",
            "placed": placed if placed in ("0", "1") else "", "dry_run": cfg.dry_run, "lock_label": lock_label(),
            "lock_state": lock_state(), "nav": "list",
        })

    @app.get("/p/{pid}", response_class=HTMLResponse)
    def detail(request: Request, pid: int):
        s = store()
        try:
            row = s.proposal(pid)
            if row is None:
                raise HTTPException(404)
            p = _load(row)
            names = guide_names()
            p["siblings"] = [dict(x, guide_name=names.get(x["guide_id"], x["model_code"])) for x in s.siblings(p["ref_no"], pid)]
            p["guide_name"] = names.get(p["guide_id"], p["model_code"])
            dec = s.decision_for(pid)
            p["telemetry"] = dict(dec) if dec else None
            counts = s.counts()
            nxt = next_pending(s, pid)
            if row["decision"] == "pending":
                s.log_event(pid, "open", "detail")
        finally:
            s.close()
        return templates.TemplateResponse(request, "detail.html", {"p": p, "next_id": nxt, "counts": counts, "nav": "review",
                                                                    "dry_run": cfg.dry_run, "lock_label": lock_label(),
                                                                    "lock_state": lock_state()})

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
               make_rule: str | None = Form(None), rule_text: str = Form(""), via: str = Form("detail"),
               feedback_req: str = Form(""), feedback_note: str = Form(""), rev: str = Form("")):
        if action not in ("approve", "reject"):
            raise HTTPException(400, "bad action")
        if rev.strip() and not rev.strip().isdigit():
            raise HTTPException(400, "rev must be a number")
        s = store()
        try:
            row = s.proposal(pid)
            if row is None:
                raise HTTPException(404)
            loaded = _load(row)
            amt = None
            if action == "approve":
                amt = int(amount) if amount.strip() else (loaded.get("proposed_bid_jpy_k") or row["bid_jpy_k"])
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
            try:
                s.decide(pid, action, reason.strip() or None, amt, bool(make_rule),
                         via=via if via in ("detail", "triage", "desk") else "detail",
                         seen_rev=int(rev) if rev.strip() else None)
            except VehicleClaimed as exc:
                holder = s.proposal(exc.holder)
                name = guide_names().get(holder["guide_id"]) or holder["model_code"] if holder else "another guide"
                raise HTTPException(409, f"This vehicle is already approved under {name} (lot {exc.holder}). "
                                         "One bid per vehicle, so it is not approved again.")
            except Refused as exc:
                raise HTTPException(409, str(exc))
            if make_rule and line:
                append_override(cfg, row["model_code"], line)
            record_feedback(s, loaded, action, amt, reason.strip(), feedback_req.strip(), feedback_note.strip())
            nxt = next_pending(s, pid)
            decision = s.proposal(pid)["decision"]
        finally:
            s.close()
        if _wants_json(request):
            return JSONResponse({"ok": True, "decision": decision, "amount": amt, "next_id": nxt})
        return RedirectResponse(f"/p/{nxt}" if nxt else "/review", status_code=303)

    @app.post("/p/{pid}/reopen")
    def reopen(request: Request, pid: int, next: str = Form("")):
        """Return an approved or rejected lot that was not placed live to waiting. Not a decision: it approves
        nothing, and Jim decides the lot again himself. Held back while a run is placing the approved bids."""
        cur = jobs.current()
        if cur and (cur.get("progress") or {}).get("stage") == "place":
            raise HTTPException(409, "The approved bids are being placed: reopen this lot when the run has finished.")
        s = store()
        try:
            try:
                s.reopen(pid)
            except KeyError:
                raise HTTPException(404)
            except Refused as exc:
                raise HTTPException(409, str(exc))
        finally:
            s.close()
        if _wants_json(request):
            return JSONResponse({"ok": True, "decision": "pending"})
        return RedirectResponse(next if next.startswith("/") and not next.startswith("//") else f"/p/{pid}", status_code=303)

    @app.post("/p/{pid}/delete")
    def delete_lot(request: Request, pid: int):
        """Take one waiting lot out of the review. Not a decision: nothing is learned from it, no amount is
        recorded, and the next session's scan checks the vehicle again (CLAUDE.md, rules 0c and 1)."""
        s = store()
        try:
            if s.proposal(pid) is None:
                raise HTTPException(404)
            if not s.delete_lot(pid):
                raise HTTPException(409, "only a lot that waits for review can be deleted")
            nxt = next_pending(s, pid)
        finally:
            s.close()
        if _wants_json(request):
            return JSONResponse({"ok": True, "decision": "deleted", "next_id": nxt})
        return RedirectResponse(f"/p/{nxt}" if nxt else "/review", status_code=303)

    @app.post("/sessions/{key}/delete")
    def delete_session(request: Request, key: str, next: str = Form("/review")):
        """Every waiting lot of a session out of the review (`key`: a session id, or `earlier` for the lots no
        dashboard run wrote). A session that is running is stopped; one placing bids is left alone."""
        sid = None if key == "earlier" else (int(key) if key.isdigit() else -1)
        cur = jobs.current()
        live = cur if cur and sid is not None and cur.get("session_id") == sid else None
        if live and (live.get("progress") or {}).get("stage") == "place":
            raise HTTPException(409, "the approved bids are being placed: delete the session when the run has finished")
        s = store()
        try:
            if sid == -1 or (sid is not None and s.session_info(sid) is None):
                raise HTTPException(404, "no such session")
            count = s.delete_session(sid)
        finally:
            s.close()
        if live:
            try:
                jobs.cancel(live["id"], delete=True)  # stops it; a lot it writes before it ends goes too
            except NotRunning:
                pass  # it ended a moment ago
        if _wants_json(request):
            return JSONResponse({"ok": True, "deleted": count})
        return RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/review", status_code=303)

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

    @app.post("/finish", response_class=HTMLResponse)
    def finish(request: Request):
        """End the review: the console's `bot bid` (its event) or the dashboard's Bid run waiting at the
        review (its finish file) then places the approved bids. Nothing is decided here."""
        run_id = None
        if finish_event is None:
            cur = jobs.review_waiting()
            if cur is None:
                raise HTTPException(404, "no review is waiting: start Bid mode from the overview")
            try:
                jobs.finish_review(cur["id"])
            except NotRunning as exc:
                raise HTTPException(409, str(exc))
            run_id = cur["id"]
        s = store()
        try:
            counts = s.counts()
        finally:
            s.close()
        if finish_event is not None:
            finish_event.set()
        return templates.TemplateResponse(request, "finished.html", {
            "waiting": counts["approved"], "counts": counts, "lock_label": lock_label(), "lock_state": lock_state(),
            "nav": "review", "run_id": run_id,
        })

    @app.post("/rules/{code}/skip")
    def add_skip_line(code: str, line: str = Form(...), next: str = Form("/review")):
        """Store a structured standing instruction for a model code (validated first)."""
        text = line.strip()
        if not is_structured(text):
            text = "skip: " + text
        try:
            parse_instruction(text)
        except InstructionError as exc:
            raise HTTPException(400, f"standing instruction not understood: {exc}")
        append_override(cfg, code.upper(), text)
        return RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/review", status_code=303)

    @app.get("/sheet-trial", response_class=HTMLResponse)
    def sheet_trial_page(request: Request, stamp: str | None = None):
        """The trial of Carbarn's sheet reader: per lot what each reader said, and Jim's marks where they disagree."""
        from auction_bot import sheet_trial

        if stamp is not None and not sheet_trial.valid_stamp(stamp):
            raise HTTPException(404)
        trial = sheet_trial.load_trial(cfg, stamp)
        if stamp is not None and trial is None:
            raise HTTPException(404)
        marks = sheet_trial.load_marks(cfg, trial["stamp"]) if trial else {}
        return page(request, "sheet_trial.html", "runs", trial=trial, marks=marks, stamps=sheet_trial.list_trials(cfg),
                    summary=sheet_trial.summarize(trial, cfg, marks) if trial else None, choices=sheet_trial.MARKS)

    @app.post("/sheet-trial/{stamp}/marks")
    async def sheet_trial_marks(request: Request, stamp: str):
        """Keep Jim's marks: only for a row the trial flagged, only the three words; the earlier marks stay."""
        from auction_bot import sheet_trial

        trial = sheet_trial.load_trial(cfg, stamp) if sheet_trial.valid_stamp(stamp) else None
        if trial is None:
            raise HTTPException(404)
        flagged = {(str(lot["pid"]), r["field"]) for lot in trial["lots"] for r in lot.get("rows") or [] if r["disagree"]}
        marks = sheet_trial.load_marks(cfg, stamp)
        form = await request.form()
        for key, value in form.items():
            parts = key.split(":", 2)
            if len(parts) == 3 and parts[0] == "mark" and (parts[1], parts[2]) in flagged and value in sheet_trial.MARKS:
                marks.setdefault(parts[1], {})[parts[2]] = value
        sheet_trial.save_marks(cfg, stamp, marks)
        return RedirectResponse(f"/sheet-trial?stamp={stamp}", status_code=303)

    return app
