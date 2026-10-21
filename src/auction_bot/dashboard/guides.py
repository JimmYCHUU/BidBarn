"""The guide pages: one line per model code, and the whole guide behind each code.

Reads only: the newest snapshot, the code's standing instructions file, its playbook file and
the store. The one form on a guide page posts to the review page's existing `/rules/{code}/skip`
route, behind Jim's click; nothing here writes a rule, decides a lot or talks to a site. The bid
grid is rendered on this PC and sent nowhere.
"""
from __future__ import annotations

import re

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from auction_bot.carbarn.client import latest_snapshot, load_override, load_rules
from auction_bot.carbarn.models import Approval, ModelRule, Modifier, OdoBand, Variant
from auction_bot.dashboard import status as st
from auction_bot.dashboard.jobs import MODEL_CODE
from auction_bot.insights import propose_skip_lines
from auction_bot.judge.playbook import code_lines
from auction_bot.rules.hard import _grade_bonus_threshold  # the one reading of "grade 4" = 4 and above (Jim, 2026-09-14)
from auction_bot.rules.eligibility import _window_text  # the review card's words for a build window
from auction_bot.rules.instructions import InstructionError, is_structured, parse_instruction, split_override_lines

DECISIONS_SHOWN = 15
NOTES_SHOWN = 30
_STAMP = re.compile(r"^\((?P<stamp>\d{4}-\d{2}-\d{2}[^)]*)\)\s*")
_cache: dict = {}


# -- the rules of one code -------------------------------------------------------------------------

def rules_for_code(cfg, code: str) -> list[ModelRule]:
    """The guides of one model code from the newest snapshot: two when two guides share the code.

    The snapshot is parsed once per file (keyed on its path, time and size). The standing
    instructions are read again on every call, so a line added a moment ago shows at once."""
    path = latest_snapshot(cfg)
    if path is None:
        return []
    info = path.stat()
    key = (str(path), info.st_mtime_ns, info.st_size)
    hit = _cache.get("rules")
    if hit is None or hit["key"] != key:
        by_code: dict[str, list[ModelRule]] = {}
        try:
            rules, _ = load_rules(cfg, path)
        except Exception:  # a snapshot that cannot be read: the index reports it, the guide pages are not found
            rules = []
        for r in rules:
            by_code.setdefault(r.model_code.upper(), []).append(r)
        hit = {"key": key, "by_code": by_code}
        _cache["rules"] = hit
    found = hit["by_code"].get(code.upper(), [])
    for r in found:
        r.override_text = load_override(cfg, r.model_code)
    return found


# -- the guide as the page shows it ------------------------------------------------------------------

def _is_dial(m: Modifier) -> bool:
    """The guide's 'Bid at N%' switch: already in the grid values, shown but never applied (hard.py)."""
    return bool(m.percent) and not m.conditions and not m.amount_jpy_k and not m.no_bid


def _is_row_off(m: Modifier) -> bool:
    """A whole year switched off on the dashboard: a no-bid on one year with no other condition."""
    return (m.no_bid and not m.conditions and m.year_from is not None and m.year_from == m.year_to
            and m.odo_from is None and m.odo_to is None)


def _year_ranges(years: list[int]) -> str:
    """[2004, 2005, 2006, 2010] -> '2004-2006, 2010'."""
    out: list[str] = []
    for y in sorted(set(years)):
        if out and out[-1][1] == y - 1:
            out[-1][1] = y
        else:
            out.append([y, y])
    return ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in out)


def modifier_view(m: Modifier) -> dict:
    """One modifier in words, sorted by what the bot does with it: `applied` (the bot adds it when
    its conditions hold), `chip` (an amount with no condition the bot can check: Jim judges it),
    `dial` (already in the grid values), `row_off` (a year switched off), `off` (inactive or stale)."""
    threshold = _grade_bonus_threshold(m)
    years = f"years {m.year_from}-{m.year_to}" if (m.year_from or m.year_to) else ""
    if threshold is not None:
        text = f"{m.amount_jpy_k:+d}K for grade {threshold:g} and up" + (f", {years}" if years else "")
    else:
        text = m.describe()
    if not m.active or m.stale:
        kind = "off"
    elif m.is_toggle:
        kind = "chip"
    elif _is_dial(m):
        kind = "dial"
    elif _is_row_off(m):
        kind = "row_off"
    else:
        kind = "applied"
    return {"id": m.id, "kind": kind, "text": text, "note": m.note, "amount_k": m.amount_jpy_k, "percent": m.percent,
            "no_bid": m.no_bid, "active": m.active, "stale": m.stale, "chip": m.is_toggle,
            "year_from": m.year_from, "year_to": m.year_to}


_TEXT_KINDS = {"sev": "SEV", "mre": "MRE", "mre-conditions": "MRE conditions"}


def approval_view(a: Approval) -> dict:
    """One MRE/SEV approval pair in words: its build window, expiry, model codes and restriction texts."""
    return {"label": a.label, "selected": a.selected, "active": a.active,
            "window": _window_text(a.build_from_ym, a.build_to_ym),
            "expiry": a.expiry_date.isoformat() if a.expiry_date else "",
            "criterion": a.sev_criterion, "category": a.sev_category or a.mre_category,
            "model_codes": a.sev_model_codes,
            "texts": [(_TEXT_KINDS.get(kind, kind), text) for kind, text in a.texts()],
            "scraped": (a.last_scraped_at or "")[:10]}


def variant_view(v: Variant) -> dict:
    """The grid of one variant: years down, odometer bands across, every cell including the no-bid ones."""
    bands = sorted({(c.band.odo_from, c.band.odo_to) for c in v.cells.values()}) or [(b.odo_from, b.odo_to) for b in v.bands]
    labels = [OdoBand(a, b).label for a, b in bands]
    rows = []
    for year in sorted({c.year for c in v.cells.values()}):
        cells = []
        for (a, b), label in zip(bands, labels):
            c = v.cells.get((year, a, b))
            if c is None:
                cells.append({"band": label, "k": None, "no_bid": True, "reason": "no value in the guide"})
            else:
                no_bid = bool(c.no_bid or c.bid_jpy_k is None)
                cells.append({"band": label, "k": c.bid_jpy_k, "no_bid": no_bid,
                              "reason": c.reason or ("no bid" if no_bid else None)})
        rows.append({"year": year, "eligible": year in v.eligible_years, "cells": cells,
                     "biddable": any(not c["no_bid"] for c in cells)})
    mods = [modifier_view(m) for m in v.modifiers]
    return {"id": v.id, "label": v.label, "active": v.active, "bands": labels, "rows": rows, "years": v.years, "max_odo": v.max_odo,
            "applied": [m for m in mods if m["kind"] == "applied"],
            "chips": [m for m in mods if m["kind"] == "chip"],
            "dials": [m for m in mods if m["kind"] == "dial"],
            "off": [m for m in mods if m["kind"] == "off"],
            "approvals": [approval_view(a) for a in v.approvals],
            "rows_off": _year_ranges([m["year_from"] for m in mods if m["kind"] == "row_off"])}


def _stamped(line: str) -> dict:
    """'(2026-09-14, jim) Skip ambulance conversions' -> the stamp and the words apart."""
    m = _STAMP.match(line)
    return {"stamp": m.group("stamp") if m else "", "text": (line[m.end():] if m else line).strip()}


def instruction_lines(rule: ModelRule) -> list[dict]:
    """Every line of the code's standing instructions file with its stamp, read as the bot reads it:
    a free line the reviewer sees, a `skip:` line the bot checks, or a line it could not read."""
    out = []
    for raw in rule.override_text.splitlines():
        s = raw.strip()
        if not s.startswith("-"):
            continue
        d = _stamped(s.lstrip("- ").strip())
        if not d["text"]:
            continue
        d.update(kind="free", conditions=[], needs_detail=False, every_lot=False, error="")
        if is_structured(d["text"]):
            try:
                ins = parse_instruction(d["text"])
                d.update(kind="skip", conditions=[c.describe() for c in ins.conditions], needs_detail=ins.needs_detail,
                         every_lot=not ins.conditions)
            except InstructionError as exc:
                d.update(kind="error", error=str(exc))
        out.append(d)
    return out


def playbook_lines(cfg, code: str) -> list[dict]:
    """The code's playbook file as [{stamp, text}]: Jim's learn-mode comments and review corrections."""
    return [_stamped(ln) for ln in code_lines(cfg, code)]


def guide_view(rule: ModelRule) -> dict:
    """A guide as the page shows it: the head, the grid per variant, the modifiers sorted by what
    the bot does with them, the chips Jim judges, and the code's standing instructions."""
    free, skips, errors = split_override_lines(rule)
    variants = [variant_view(v) for v in rule.variants]
    chips: dict[tuple, dict] = {}
    for v in variants:
        for m in v["chips"]:
            label = m["note"] or "chip"
            entry = chips.setdefault((label, m["amount_k"]), {"label": label, "amount_k": m["amount_k"], "text": m["text"], "variants": []})
            entry["variants"].append(v["label"])
    return {
        "name": f"{rule.make} {rule.model}".strip() or rule.model_code,
        "code": rule.model_code,
        "guide_id": rule.guide_id,
        "active": bool(rule.active),
        "updated_at": rule.updated_at,
        "min_grade": rule.min_grade,
        "boss_bid": rule.boss_bid,
        "eligibility_summary": rule.eligibility_summary,
        "text_rules": list(rule.text_rules),
        "years": rule.search_years,
        "max_odo": rule.search_max_odo,
        "variants": variants,
        "chips": list(chips.values()),
        "instructions": {
            "lines": instruction_lines(rule),
            "free": free,
            "skip": [{"raw": i.raw, "conditions": [c.describe() for c in i.conditions], "needs_detail": i.needs_detail} for i in skips],
            "errors": errors,
        },
    }


# -- the routes ----------------------------------------------------------------------------------------

def register(app, templates, cfg, store, page) -> None:
    """`store()` opens a Store; `page(request, template, nav, **context)` renders inside the shell.
    Called from `dashboard/routes.py::register`, so the same local-only middleware covers these."""
    templates.env.globals.setdefault("ago", st.ago)
    templates.env.globals.setdefault("local_time", st.local_time)

    @app.get("/guides", response_class=HTMLResponse)
    def guides_index(request: Request):
        rules = st.rules_state(cfg)
        s = store()
        try:
            rows = st.model_rows(cfg, s, rules)
        finally:
            s.close()
        known = {c["code"] for c in rules["codes"]}
        for r in rows:
            r["in_snapshot"] = r["code"] in known  # lots stored under a code the snapshot no longer has get no link
        return page(request, "guides.html", "guides", rows=rows, rules=rules)

    @app.get("/guides/{code}", response_class=HTMLResponse)
    def guide_page(request: Request, code: str):
        if not MODEL_CODE.fullmatch(code):
            raise HTTPException(404, "no such guide")
        code = code.upper()
        rules = rules_for_code(cfg, code)
        if not rules:
            raise HTTPException(404, f"no guide for {code} in the newest snapshot")
        zeros = {"code": code, "to_review": 0, "approved": 0, "rejected": 0, "skipped": 0, "lots": 0, "last_seen": None}
        s = store()
        try:
            decisions = s.decisions_with_facts(code)
            notes = s.manual_notes(code, limit=NOTES_SHOWN)
            model_counts = next((r for r in st.model_rows(cfg, s) if r["code"] == code), None) or zeros
        finally:
            s.close()
        return page(request, "guide.html", "guides", code=code, guides=[guide_view(r) for r in rules],
                    playbook=playbook_lines(cfg, code), notes=notes, decisions=decisions[:DECISIONS_SHOWN],
                    decisions_total=len(decisions), suggestions=propose_skip_lines(decisions), model_counts=model_counts)
