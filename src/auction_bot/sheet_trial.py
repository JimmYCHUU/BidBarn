"""Trial of Carbarn's sheet reader against the bot's own reading of the same sheets (Jim, 2026-10-22).

Up to `count` stored lots whose guide needs the sheet (`sheet_needs`: Welcab guides first, then cruise-control
guides, then the rest, spread over model codes, newest lot first, one row per lot and one call per image) are
sent to the reader. Nothing goes to Nichibo and the bot's own model is not called. Per lot the trial compares
what each reader says: Carbarn's read, the local OCR, Nichibo's translation, the listing and the free model's
stored verdict. Jim marks who was right where they disagree, and the summary applies the decision rule stated
in the plan before the numbers were in: wire it in when at most 10 % of the reads failed or were hard to read
and, on the marked disagreements, Carbarn's read was right more often than the bot.

Files: `data/sheet_reads/trial-<stamp>.json` and `trial-<stamp>-marks.json` (git-ignored, never deleted).
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from auction_bot import progress
from auction_bot.carbarn.models import ModelRule
from auction_bot.carbarn.sheet_reader import SheetReader, SheetReadResult
from auction_bot.config import Config
from auction_bot.judge.requirements import sheet_needs
from auction_bot.judge.sheet_facts import (cruise_sheet_step, fields, sheet_usable, welcab_sheet_step, welfare_usable)
from auction_bot.judge.topics import CRUISE_WORDS, SEAT_FAIL, SEAT_PASS, TOPIC_CRUISE, TOPIC_WELCAB, detect_topic, seat_system

MARKS = ("carbarn", "bot", "neither")
FAILURE_LINE = 0.10
_STAMP = re.compile(r"^\d{8}-\d{6}(?:-\d{1,3})?$")
_CRUISE = re.compile(CRUISE_WORDS, re.I)


def trial_dir(cfg: Config) -> Path:
    d = cfg.db_path.parent / "sheet_reads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def valid_stamp(stamp: str) -> bool:
    return bool(_STAMP.match(stamp or ""))


# -- which lots ---------------------------------------------------------------------------------------------------

def _tier(needs: set[str]) -> int:
    return 0 if TOPIC_WELCAB in needs else (1 if TOPIC_CRUISE in needs else 2)


def _json(text: str | None) -> dict:
    try:
        v = json.loads(text or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def select_lots(cfg: Config, store, rules: list[ModelRule], count: int, model_codes: list[str] | None = None) -> list[dict]:
    needs_by_guide = {r.guide_id: sheet_needs(r) for r in rules}
    wanted = {c.upper() for c in model_codes} if model_codes else None
    tiers: dict[int, dict[str, list[dict]]] = {0: defaultdict(list), 1: defaultdict(list), 2: defaultdict(list)}
    seen_lots: set[tuple] = set()
    seen_images: set[str] = set()
    rows = store.conn.execute("SELECT id, ref_no, guide_id, model_code, facts_json, soft_json, evidence_json "
                              "FROM proposals ORDER BY id DESC").fetchall()
    for row in rows:
        lot = (row["ref_no"], row["guide_id"])
        if lot in seen_lots:
            continue
        seen_lots.add(lot)
        needs = needs_by_guide.get(row["guide_id"])
        if not needs or (wanted and str(row["model_code"]).upper() not in wanted):
            continue
        ev = _json(row["evidence_json"])
        sheet = ev.get("sheet")
        if not sheet or not Path(sheet).is_file():
            continue
        digest = hashlib.sha1(Path(sheet).read_bytes()).hexdigest()
        if digest in seen_images:
            continue
        seen_images.add(digest)
        tiers[_tier(needs)][row["model_code"]].append({
            "pid": row["id"], "ref_no": row["ref_no"], "guide_id": row["guide_id"], "model_code": row["model_code"],
            "sheet": sheet, "sha1": digest, "needs": sorted(needs), "facts": _json(row["facts_json"]),
            "soft": _json(row["soft_json"])})
    out: list[dict] = []
    for t in (0, 1, 2):
        queues = [q for q in tiers[t].values() if q]
        while queues and len(out) < count:
            nxt = []
            for q in queues:
                if len(out) >= count:
                    break
                out.append(q.pop(0))
                if q:
                    nxt.append(q)
            queues = nxt
    return out[:count]


# -- what each reader says ------------------------------------------------------------------------------------------

def _bare(code) -> str | None:
    s = str(code or "").strip().upper().rstrip("?")
    return s.rsplit("-", 1)[-1] if s else None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _km_differ(a, b) -> bool:
    return abs(a - b) > max(1_500, int(max(a, b) * 0.03))


def _grade_norm(v) -> str | None:
    if v in (None, ""):
        return None
    n = _num(v)
    return f"{n:g}" if n is not None else str(v).strip().upper()


def _yes_no(v) -> str:
    return "yes" if v is True else ("no" if v is False else "")


def _model_verdict(soft: dict, topic: str) -> str:
    """The free model's stored answer for the guide's requirement on this topic, if it judged one."""
    reqs = [r for r in soft.get("requirements") or [] if isinstance(r, dict)
            and (r.get("topic") == topic or detect_topic(str(r.get("text") or "")) == topic)]
    if not reqs:
        return ""
    ladders = {lad.get("id"): lad for lad in soft.get("ladders") or [] if isinstance(lad, dict)}
    verdicts = {v.get("id"): v for v in ((soft.get("judgement") or {}).get("requirements") or []) if isinstance(v, dict)}
    for r in reqs:
        lad = ladders.get(r.get("id"))
        if lad:
            return f"{lad.get('verdict')}: {lad.get('evidence', '')}".strip()
        v = verdicts.get(r.get("id"))
        if v:
            return f"{v.get('verdict')}: {v.get('evidence', '')}".strip()
    return ""


def _seat_class(state: str | None) -> str:
    if state in SEAT_PASS:
        return "pass"
    if state in SEAT_FAIL:
        return "fail"
    if state == "welcab_unspecified":
        return "welcab"
    return "silent"


def _row(field: str, disagree: bool, **values) -> dict:
    return {"field": field, "disagree": bool(disagree), "values": {k: ("" if v is None else v) for k, v in values.items()}}


def compare(cfg: Config, cand: dict, res: SheetReadResult) -> dict:
    """One lot of the trial: who read what, and where the readers disagree."""
    sheet = Path(cand["sheet"])
    base = sheet_file = None
    try:
        rel = sheet.resolve().relative_to(Path(cfg.evidence_dir).resolve())
        base, sheet_file = rel.parts[0], "/".join(rel.parts[1:])
    except ValueError:  # a sheet outside the evidence folder is shown by name only
        sheet_file = sheet.name
    reply = res.reply if res.ok else None
    lot = {"pid": cand["pid"], "ref_no": cand["ref_no"], "model_code": cand["model_code"], "guide_id": cand["guide_id"],
           "needs": cand["needs"], "evidence_base": base, "sheet_file": sheet_file, "status": "ok" if res.ok else "error",
           "error": res.error, "seconds": round(res.seconds, 1), "cached": res.cached, "sha1": res.sha1, "read": reply,
           "low_confidence": False, "rows": []}
    if reply is None:
        return lot
    facts, soft = cand["facts"], cand["soft"]
    ocr = facts.get("sheet_ocr") if isinstance(facts.get("sheet_ocr"), dict) else {}
    tr = {k: str(v) for k, v in (facts.get("translator") or {}).items() if k not in ("sheet_url", "error") and v}
    f = fields(reply)
    welfare = reply.get("welfare") if isinstance(reply.get("welfare"), dict) else None
    lot["low_confidence"] = not sheet_usable(reply) or (welfare is not None and not welfare_usable(reply))
    rows = lot["rows"]

    rows.append(_row("seats", f.get("seats") is not None and ocr.get("seats") is not None and f["seats"] != ocr["seats"],
                     carbarn=f.get("seats"), ocr=ocr.get("seats")))
    cb, ob = _bare(f.get("model_code")), _bare(ocr.get("model_code"))
    rows.append(_row("model code", bool(cb and ob and cb != ob), carbarn=f.get("model_code"), ocr=ocr.get("model_code"),
                     listing=facts.get("chassis")))
    kms = [k for k in (f.get("km"), ocr.get("km"), facts.get("km")) if isinstance(k, (int, float))]
    rows.append(_row("odometer", any(_km_differ(a, b) for i, a in enumerate(kms) for b in kms[i + 1:]),
                     carbarn=f.get("km"), ocr=ocr.get("km"), listing=facts.get("km")))
    grades = [g for g in (_grade_norm(f.get("grade")), _grade_norm(ocr.get("grade")), _grade_norm(facts.get("grade_raw"))) if g]
    rows.append(_row("grade", len(set(grades)) > 1, carbarn=f.get("grade"), ocr=ocr.get("grade"), listing=facts.get("grade_raw")))
    c4, o4 = f.get("is_4wd"), ocr.get("is_4wd")
    rows.append(_row("4WD", (c4 is True and o4 is False) or (c4 is None and o4 is True), carbarn=_yes_no(c4), ocr=_yes_no(o4)))

    cruise = cruise_sheet_step(reply)
    tr_cruise = next((f"yes: {v}" for v in tr.values() if _CRUISE.search(v)), "")
    model_cruise = _model_verdict(soft, TOPIC_CRUISE)
    if TOPIC_CRUISE in cand["needs"] or cruise["state"] == "yes" or tr_cruise:
        carbarn_yes = cruise["state"] == "yes"
        disagree = carbarn_yes != bool(tr_cruise) or (carbarn_yes and model_cruise.startswith("fail"))
        rows.append(_row("cruise control", disagree, carbarn=f"{cruise['state']}: {cruise['evidence']}",
                         translator=tr_cruise or "not mentioned", model=model_cruise))

    welcab = welcab_sheet_step(reply)
    tr_seat = seat_system(" ".join(tr.values())) if tr else "not_welcab"
    tr_seat = "" if tr_seat == "not_welcab" else tr_seat
    if TOPIC_WELCAB in cand["needs"] or (welfare and welfare.get("found")):
        state = welcab["state"] if welcab else None
        rows.append(_row("Welcab seat", _seat_class(state) != _seat_class(tr_seat or None),
                         carbarn=f"{state}: {welcab['evidence']}" if welcab else "no welfare answer",
                         translator=tr_seat or "not mentioned", model=_model_verdict(soft, TOPIC_WELCAB)))

    notes = "; ".join(str(n.get("en") or n.get("ja") or "") for n in ((reply.get("sheet") or {}).get("inspector_notes") or [])
                      if isinstance(n, dict))
    rows.append(_row("inspector notes", False, carbarn=notes,
                     translator="; ".join(tr[k] for k in ("VehicleCondition", "Comments") if tr.get(k))))
    return lot


# -- the run ----------------------------------------------------------------------------------------------------------

def _new_stamp(cfg: Config) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    n, out = 1, stamp
    while (trial_dir(cfg) / f"trial-{out}.json").exists():
        n += 1
        out = f"{stamp}-{n}"
    return out


def run_trial(cfg: Config, store, reader: SheetReader, rules: list[ModelRule], count: int,
              model_codes: list[str] | None = None, echo: Callable[[str], None] | None = None) -> tuple[Path, dict]:
    say = echo or (lambda line: None)
    cands = select_lots(cfg, store, rules, count, model_codes)
    stamp = _new_stamp(cfg)
    trial = {"stamp": stamp, "started_at": datetime.now(timezone.utc).isoformat(), "asked": count, "selected": len(cands),
             "lots": [], "calls": 0, "signed_out": None}
    say(f"{len(cands)} stored sheets selected for the trial")
    for i, cand in enumerate(cands, 1):
        progress.check_cancel()
        progress.emit(stage="sheet-trial", i=i, total=len(cands), code=cand["model_code"], ref_no=cand["ref_no"])
        res = reader.read(Path(cand["sheet"]))
        lot = compare(cfg, cand, res)
        trial["lots"].append(lot)
        flags = sum(1 for r in lot["rows"] if r["disagree"])
        say(f"  {i}/{len(cands)} {cand['model_code']} {cand['ref_no']}: "
            + (f"read in {res.seconds:.0f} s{' (cache)' if res.cached else ''}, {flags} disagreement(s)" if res.ok else f"failed: {res.error}"))
        if reader.signed_out:
            trial["signed_out"] = reader.signed_out
            say("  signed out of the Carbarn dashboard: the trial stops here. Sign in, then start it again (read sheets are kept).")
            break
    trial["calls"] = reader.calls
    trial["finished_at"] = datetime.now(timezone.utc).isoformat()
    path = trial_dir(cfg) / f"trial-{stamp}.json"
    path.write_text(json.dumps(trial, ensure_ascii=False, indent=1), encoding="utf-8")
    return path, summarize(trial, cfg)


# -- files and the summary ---------------------------------------------------------------------------------------------

def list_trials(cfg: Config) -> list[str]:
    stamps = [p.stem.removeprefix("trial-") for p in trial_dir(cfg).glob("trial-*.json") if not p.stem.endswith("-marks")]
    return sorted((s for s in stamps if valid_stamp(s)), reverse=True)


def load_trial(cfg: Config, stamp: str | None = None) -> dict | None:
    if stamp is None:
        stamps = list_trials(cfg)
        if not stamps:
            return None
        stamp = stamps[0]
    if not valid_stamp(stamp):
        return None
    p = trial_dir(cfg) / f"trial-{stamp}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def load_marks(cfg: Config, stamp: str) -> dict:
    p = trial_dir(cfg) / f"trial-{stamp}-marks.json"
    if not valid_stamp(stamp) or not p.exists():
        return {}
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def save_marks(cfg: Config, stamp: str, marks: dict) -> Path:
    if not valid_stamp(stamp):
        raise ValueError(f"not a trial stamp: {stamp!r}")
    p = trial_dir(cfg) / f"trial-{stamp}-marks.json"
    p.write_text(json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def summarize(trial: dict, cfg: Config | None = None, marks: dict | None = None) -> dict:
    lots = trial.get("lots") or []
    n = len(lots)
    ok = [lot for lot in lots if lot.get("status") == "ok"]
    low = sum(1 for lot in ok if lot.get("low_confidence"))
    secs = sorted(lot.get("seconds") or 0 for lot in ok if not lot.get("cached"))
    per_field: dict[str, dict] = {}
    disagreeing: set[tuple[str, str]] = set()
    for lot in ok:
        for r in lot.get("rows") or []:
            d = per_field.setdefault(r["field"], {"rows": 0, "disagree": 0})
            d["rows"] += 1
            if r["disagree"]:
                d["disagree"] += 1
                disagreeing.add((str(lot["pid"]), r["field"]))
    if marks is None:
        marks = load_marks(cfg, trial.get("stamp", "")) if cfg is not None else {}
    tally = {m: 0 for m in MARKS}
    for pid, by_field in (marks or {}).items():
        for field, mark in (by_field or {}).items():
            if (str(pid), field) in disagreeing and mark in tally:
                tally[mark] += 1
    failure_rate = ((n - len(ok)) + low) / n if n else 0.0
    if not n:
        decision = "no sheets read"
    elif failure_rate > FAILURE_LINE:
        decision = f"keep today's reading: {failure_rate:.0%} of the reads failed or were hard to read (the line is {FAILURE_LINE:.0%})"
    elif not any(tally.values()):
        decision = "marks needed: mark who was right where the readers disagree"
    elif tally["carbarn"] > tally["bot"]:
        decision = "wire it in: Carbarn's read was right more often on the marked disagreements"
    else:
        decision = "keep today's reading: on the marked disagreements the bot was right as often or more"
    return {"stamp": trial.get("stamp"), "lots": n, "ok": len(ok), "failed": n - len(ok), "low_confidence": low,
            "calls": int(trial.get("calls") or 0), "cached": sum(1 for lot in lots if lot.get("cached")),
            "median_s": round(statistics.median(secs), 1) if secs else None, "max_s": round(secs[-1], 1) if secs else None,
            "fields": per_field, "disagreements": len(disagreeing), "marks": tally, "marked": sum(tally.values()),
            "failure_rate": round(failure_rate, 3), "decision": decision, "signed_out": trial.get("signed_out")}
