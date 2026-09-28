"""What the review pass says about how Jim decides, and which skip rules it suggests.

Pure functions over the decision rows from `Store.decisions_with_facts`. Nothing here
decides anything: the output is a report, and the suggested `skip:` lines are stored
only when Jim adds them (review page button or `bot skip-line`).
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.instructions import InstructionError, fires, parse_instruction, suggest_from_reason

REASON_KEYWORDS: dict[str, str] = {
    "seats": r"\bseat",
    "sticker/wrap": r"sticker|wrap|decal|livery",
    "welcab/conversion": r"welcab|wheelchair|ambulance|camper|conversion",
    "body type": r"\bdump\b|\btruck\b|\bbody\b|\bflat\b",
    "4wd/2wd": r"\b[24]wd\b|four wheel|two wheel",
    "km": r"\bkm\b|odometer|mileage|kilomet",
    "grade": r"\bgrade\b",
    "damage": r"rust|repair|accident|dent|scratch|corrosion|damage|crash|frame",
    "colour": r"colou?r|\bwhite\b|\bblack\b|\bsilver\b|\bpearl\b|\bgold\b",
    "start price": r"start|price|expensive|too high|reserve",
    "variant/trim": r"variant|trim|package|\bgl\b|\bdx\b|\bsuper\b",
    "hall": r"\bhall\b|\buss\b|\bcaa\b|\bjaa\b|\btaa\b|\bhaa\b|auction place",
    "photos": r"photo|image|picture",
    "year": r"\byear\b|\b20[0-3]\d\b",
    "wrong model": r"wrong model|not a|different model|other model",
    "lot gone": r"bid closed|\bclosed\b|\bsold\b|\bended\b|expired lot|no longer listed",
    "model paused": r"no bid on|for a while|\bpause|expires in|expiry|\bsev\b|eligib",
}
_STALE = re.compile(REASON_KEYWORDS["lot gone"], re.I)
_VALUE_LINE = re.compile(r"^skip: (hall|colou?r|transmission) (is|contains) ", re.I)
_PAUSED = re.compile(REASON_KEYWORDS["model paused"], re.I)
_FIELD_WORDS = {"hall": r"\bhall\b|auction place|\buss\b|\bcaa\b|\btaa\b|\bjaa\b|\bhaa\b|\bnaa\b|\bnps\b",
                "colour": r"colou?r|\bwhite\b|\bblack\b|\bsilver\b|\bpearl\b|\bred\b|\bblue\b|\bgold\b|\bgrey\b|\bgray\b",
                "transmission": r"transmission|gearbox|manual|automatic|\bmt\b|\bat\b|\bcvt\b"}


def reason_keywords(reason: str | None) -> set[str]:
    """Buckets a free-text reason falls into; the first two words when nothing matches."""
    text = (reason or "").strip().lower()
    if not text:
        return set()
    out = {k for k, rx in REASON_KEYWORDS.items() if re.search(rx, text)}
    if not out:
        words = re.findall(r"[a-z0-9]+", text)
        out.add(" ".join(words[:2]) or text[:20])
    return out


@dataclass
class ModelInsight:
    model_code: str
    decided: int = 0
    approved: int = 0
    rejected: int = 0
    median_seconds: float | None = None
    p90_seconds: float | None = None
    sheet_zoom_share: float | None = None
    photos_share: float | None = None
    nichibo_share: float | None = None
    amount_edit_share: float | None = None
    median_amount_delta_k: float | None = None
    rejection_keywords: Counter = field(default_factory=Counter)
    top_reasons: list[tuple[str, int]] = field(default_factory=list)

    @property
    def photos_never_consulted(self) -> bool:
        return self.decided >= 3 and self.photos_share == 0 and self.nichibo_share == 0


def _share(values: list[int | None]) -> float | None:
    known = [int(v or 0) for v in values]
    return round(sum(known) / len(known), 2) if known else None


def summarise(rows: list[dict]) -> dict[str, ModelInsight]:
    by_model: dict[str, list[dict]] = {}
    for r in rows:
        by_model.setdefault(r["model_code"], []).append(r)
    out: dict[str, ModelInsight] = {}
    for code, rs in sorted(by_model.items()):
        mi = ModelInsight(model_code=code, decided=len(rs))
        mi.approved = sum(1 for r in rs if r["action"] == "approve")
        mi.rejected = mi.decided - mi.approved
        secs = sorted(float(r["decide_seconds"]) for r in rs if r.get("decide_seconds") is not None)
        if secs:
            mi.median_seconds = round(statistics.median(secs), 1)
            mi.p90_seconds = round(secs[min(len(secs) - 1, int(0.9 * len(secs)))], 1)
        mi.sheet_zoom_share = _share([r.get("sheet_zoomed") for r in rs])
        mi.photos_share = _share([r.get("photos_clicked") for r in rs])
        mi.nichibo_share = _share([r.get("nichibo_opened") for r in rs])
        approved = [r for r in rs if r["action"] == "approve"]
        if approved:
            mi.amount_edit_share = _share([r.get("amount_edited") for r in approved])
            deltas = [float(r["amount_jpy_k"]) - float(r["proposed_jpy_k"]) for r in approved
                      if r.get("amount_edited") and r.get("amount_jpy_k") is not None and r.get("proposed_jpy_k") is not None]
            if deltas:
                mi.median_amount_delta_k = round(statistics.median(deltas), 1)
        reasons = Counter()
        for r in rs:
            if r["action"] != "approve":
                for k in reason_keywords(r.get("reason")):
                    mi.rejection_keywords[k] += 1
                if r.get("reason"):
                    reasons[" ".join(str(r["reason"]).split())[:80]] += 1
        mi.top_reasons = reasons.most_common(5)
        out[code] = mi
    return out


@dataclass
class SkipProposal:
    model_code: str
    line: str
    support: int  # rejected lots the line would have skipped
    conflicts: int  # approved lots it would also have skipped
    examples: list[str] = field(default_factory=list)


def _facts_of(row: dict) -> VehicleFacts:
    return VehicleFacts.from_dict(row.get("facts") or {"ref_no": row.get("ref_no", ""), "url": ""})


def propose_skip_lines(rows: list[dict], min_support: int = 2) -> list[SkipProposal]:
    """Skip lines that would have skipped at least `min_support` rejected lots and no approved one.

    Candidates come from each rejection's reason (`suggest_from_reason`) and from the exact
    hall, colour and transmission values of rejected lots. Every candidate is replayed over
    the model's decided lots, so the counts are what the line would actually have done.
    """
    by_model: dict[str, list[dict]] = {}
    for r in rows:
        by_model.setdefault(r["model_code"], []).append(r)
    out: list[SkipProposal] = []
    for code, rs in sorted(by_model.items()):
        decided = [(r, _facts_of(r)) for r in rs]
        candidates: dict[str, set[int] | None] = {}  # line -> rows whose reason grounds it (None: any rejection counts)
        paused: list[str] = []
        for i, (r, f) in enumerate(decided):
            if r["action"] == "approve":
                continue
            reason = r.get("reason") or ""
            if _STALE.search(reason):
                continue  # "bid closed": the lot is gone, that says nothing about the car
            if _PAUSED.search(reason):
                paused.append(reason)
            line = suggest_from_reason(reason, f)
            if line:
                if _VALUE_LINE.match(line):  # a hall / colour / gearbox line counts only grounded rejections
                    candidates.setdefault(line, set())
                    if candidates[line] is not None:
                        candidates[line].add(i)
                else:
                    candidates.setdefault(line, None)
            for name, value in (("hall", f.auction_place), ("colour", f.colour), ("transmission", f.transmission)):
                value = (value or "").strip()
                if value and (re.search(_FIELD_WORDS[name], reason, re.I) or re.search(r"\b" + re.escape(value) + r"\b", reason, re.I)):
                    key = f"skip: {name} is {value}"  # only when the reason talks about it
                    candidates.setdefault(key, set())
                    if candidates[key] is not None:
                        candidates[key].add(i)
        if len(paused) >= 2 and not any(r["action"] == "approve" for r in rs):
            out.append(SkipProposal(code, "skip: all", len(paused), 0, [p[:60] for p in paused[:3]]))
        for line, grounded in candidates.items():
            try:
                ins = parse_instruction(line)
            except InstructionError:
                continue
            support = conflicts = 0
            examples: list[str] = []
            for i, (r, f) in enumerate(decided):
                if not fires(ins, f, r.get("bid_jpy_k")):
                    continue
                if r["action"] == "approve":
                    conflicts += 1
                elif grounded is None or i in grounded:
                    support += 1
                    if len(examples) < 3:
                        examples.append((r.get("summary") or "")[:60])
            if support >= min_support and conflicts == 0:
                out.append(SkipProposal(code, line, support, conflicts, examples))
        out.sort(key=lambda p: (-p.support, p.line))
    return out


def model_accuracy(feedback_rows: list) -> dict[str, Counter]:
    """Per model code: how often Jim corrected the model, by requirement id (from judge_feedback)."""
    out: dict[str, Counter] = {}
    for r in feedback_rows:
        out.setdefault(r["model_code"] or "?", Counter())[r["requirement_id"] or "?"] += 1
    return out


def render(insights: dict[str, ModelInsight], proposals: list[SkipProposal], conflicts_note: bool = True,
           accuracy: dict[str, Counter] | None = None) -> str:
    if not insights and not accuracy:
        return "No decisions recorded yet. Review some lots first (review.cmd)."
    lines: list[str] = []
    for code, counts in (accuracy or {}).items():
        lines.append(f"{code}: the model was corrected {sum(counts.values())} time(s): "
                     + ", ".join(f"{k} ({n})" for k, n in counts.most_common(6)))
    if accuracy:
        lines.append("")
    props = {}
    for p in proposals:
        props.setdefault(p.model_code, []).append(p)
    for code, mi in insights.items():
        lines.append(f"{code}: {mi.decided} decided, {mi.approved} approved, {mi.rejected} rejected")
        if mi.median_seconds is not None:
            lines.append(f"   time to decide: median {mi.median_seconds:g} s, p90 {mi.p90_seconds:g} s")
        looked = []
        if mi.sheet_zoom_share is not None:
            looked.append(f"sheet zoomed {mi.sheet_zoom_share:.0%}")
        if mi.photos_share is not None:
            looked.append(f"photos opened {mi.photos_share:.0%}")
        if mi.nichibo_share is not None:
            looked.append(f"Nichibo opened {mi.nichibo_share:.0%}")
        if looked:
            lines.append("   looked at: " + ", ".join(looked) + ("  <- photos never consulted" if mi.photos_never_consulted else ""))
        if mi.amount_edit_share is not None:
            delta = f", median change {mi.median_amount_delta_k:+g}K" if mi.median_amount_delta_k is not None else ""
            lines.append(f"   amount changed on {mi.amount_edit_share:.0%} of approvals{delta}")
        if mi.rejection_keywords:
            top = ", ".join(f"{k} {n}" for k, n in mi.rejection_keywords.most_common(6))
            lines.append(f"   rejections by theme: {top}")
        for reason, n in mi.top_reasons:
            lines.append(f"      {n}x \"{reason}\"")
        for p in props.get(code, []):
            lines.append(f"   suggested rule ({p.support} rejections, no approvals): {p.line}")
            lines.append(f"      add with: bot skip-line {code} \"{p.line}\"")
        lines.append("")
    return "\n".join(lines).rstrip()
