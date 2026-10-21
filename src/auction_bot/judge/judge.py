"""The judgement of one lot: prompt, schema, the call, and a deterministic reading of the answer.

The model receives the lot's facts, the OCR fields, Nichibo's translation, the guide's
requirements with where to look, the chips with their amounts, the odd-case flags, the
playbook, the candidate amounts, the sheet and up to `max_photos` photos. It answers with
a verdict and evidence per requirement, whether each chip applies, the odd cases it sees,
one of the candidate amounts (or none), a recommendation and a two-line summary. The
status of the lot stays NEEDS_REVIEW: Jim confirms everything on the review page.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from auction_bot.carbarn.models import ModelRule
from auction_bot.config import Config
from auction_bot.judge.candidates import Candidate, candidates_for
from auction_bot.judge.client import CallResult, JudgeClient, image_part, text_part
from auction_bot.judge.playbook import playbook_for
from auction_bot.judge.requirements import Requirement, odd_flags, requirements_for, requirements_hash
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.hard import NEEDS_REVIEW, HardResult

Confidence = Literal["high", "medium", "low"]


class ReqVerdict(BaseModel):
    id: str = Field(description="The requirement id exactly as given, e.g. r1 or chip:no-pro-pilot")
    verdict: Literal["pass", "fail", "unclear"]
    evidence: str = Field(default="", description="Which photo number or sheet field shows it; 'not visible' when unclear")
    confidence: Confidence = "medium"


class ChipVerdict(BaseModel):
    id: str
    applies: Literal["yes", "no", "unclear"]
    evidence: str = ""


class OddCase(BaseModel):
    what: str
    recommendation: str = Field(default="", description="What the buyer should do about it, in one sentence")
    confidence: Confidence = "medium"


class Judgement(BaseModel):
    requirements: list[ReqVerdict] = Field(default_factory=list)
    chips: list[ChipVerdict] = Field(default_factory=list)
    odd_cases: list[OddCase] = Field(default_factory=list)
    amount_k: int | None = Field(default=None, description="One of the candidate amounts, in thousands of yen, or null for the grid amount")
    amount_reason: str = ""
    recommendation: Literal["bid", "skip", "review"] = "review"
    confidence: Confidence = "medium"
    summary: str = Field(default="", description="Two short lines for the reviewer: what you saw and what you recommend")
    sheet_notes: str = Field(default="", description="Anything on the sheet the buyer should know: repairs, notes, oddities")


SYSTEM_PROMPT = """You are the assistant of a vehicle buyer at Japanese car auctions (Nichibo). You judge ONE lot against the
buyer's requirements for that model, using the auction sheet image, the listing photos, the listing facts and the
buyer's own playbook. Money is at stake, so:
- Judge only from what you can see. If something is not visible in the photos or on the sheet, say "unclear" and
  write "not visible" as evidence. Never infer seat colour, seat material, badges or buttons from the model name.
- For every requirement, cite the evidence: the photo number (Photo 3) or the sheet field (乗車定員, 型式, 排気量).
- Read the requirement's "where to look" hint: photos for seat colour, leather, the Pro Pilot button (a blue button
  on the steering wheel), Autech badges, stickers and body condition; the sheet for seats, engine size, model code,
  4WD marks and the inspector's notes.
- Import approval conditions: judge only properties of this vehicle (body, drive, seats, engine, odometer, registration date). An obligation on the workshop after import (conversion to a campervan, ADR evidence) passes with evidence "workshop obligation".
- Chips are the buyer's own adjustments; say whether each one applies (yes / no / unclear). When unclear, the buyer
  uses the base amount.
- Odd cases: name anything unusual the buyer should know (odometer at the cap, conversions, repairs, notes).
- The amount: pick one of the candidate amounts by its exact number, or null for the grid amount. Never invent a
  number. Follow the playbook when it speaks to the situation and say so in amount_reason.
- recommendation: "bid" only when every must requirement passes and nothing odd argues against it; "skip" when a must
  requirement clearly fails; otherwise "review".
Answer with one JSON object matching the schema. Be brief: evidence strings are one line each.
"""


@dataclass
class JudgeResult:
    status: str = NEEDS_REVIEW
    recommendation: str = "review"
    proposed_bid_jpy_k: int | None = None
    judgement: dict | None = None
    notes: list[str] = field(default_factory=list)
    requirements: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    requirements_hash: str = ""
    provider: str | None = None
    model: str | None = None
    usage: dict | None = None
    unavailable: str | None = None
    images_used: list[str] = field(default_factory=list)
    prompt_chars: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _facts_block(facts: VehicleFacts, ocr: dict | None) -> str:
    lines = [
        "# The lot",
        f"Listing: {facts.year} {facts.make} {facts.model}, variant text \"{facts.variant_text}\"",
        f"Chassis / model code: {facts.chassis or 'not read'}   Odometer: {facts.km:,} km   Listing grade: {facts.grade_raw}"
        if facts.km is not None else f"Chassis / model code: {facts.chassis or 'not read'}   Listing grade: {facts.grade_raw}",
    ]
    extras = [x for x in (facts.colour and f"Colour: {facts.colour}", facts.transmission and f"Transmission: {facts.transmission}",
                          facts.engine_cc and f"Engine: {facts.engine_cc} cc", facts.equipment and f"Equipment: {facts.equipment}",
                          facts.auction_place and f"Hall: {facts.auction_place}") if x]
    if extras:
        lines.append("   ".join(extras))
    if ocr and ocr.get("available"):
        lines.append("Sheet fields read by local OCR: " + ", ".join(
            f"{k}={v}" for k, v in (("seats", ocr.get("seats")), ("model code", ocr.get("model_code")),
                                    ("km", ocr.get("km")), ("grade", ocr.get("grade")), ("4WD", ocr.get("is_4wd"))) if v is not None))
    tr = facts.translator or {}
    words = {k: v for k, v in tr.items() if k != "sheet_url" and v}
    if words:
        lines.append("Nichibo's translation of the sheet notes: " + "; ".join(f"{k}: {v}" for k, v in words.items()))
    return "\n".join(lines)


def build_messages(facts: VehicleFacts, rule: ModelRule, hard: HardResult, reqs: list[Requirement], flags: list[str],
                   candidates: list[Candidate], playbook_text: str, sheet_path: Path | None, photo_paths: list[Path],
                   ocr: dict | None, max_photos: int = 8) -> tuple[list[dict], list[str]]:
    """The chat messages (text plus images) and the names of the images that were attached."""
    req_lines = [f"- {r.id} [{r.kind}, look at: {r.source}]: {r.text}" + (f" (chip {r.amount_k:+d}K)" if r.amount_k else "")
                 for r in reqs if r.kind != "eligibility"]
    elig_lines = [f"- {r.id} [eligibility, look at: {r.source}]: {r.text}" for r in reqs if r.kind == "eligibility"]
    cand_lines = [f"- {c.amount_k:,}K: {c.label}, {c.why}" for c in candidates]
    header = "\n".join([
        _facts_block(facts, ocr),
        "",
        f"# The buyer's requirements for model code {rule.model_code} ({rule.make} {rule.model})",
        f"Minimum grade {rule.min_grade}. Variant used: {hard.variant_name or '?'} (resolved by {hard.variant_how}).",
        *(req_lines or ["- (no text requirements; check the sheet and photos for anything unusual)"]),
        *(["", "# Import approval conditions (ROVER SEV / Model Report)", *elig_lines] if elig_lines else []),
        "",
        "# Odd-case flags raised by the rules",
        *([f"- {f}" for f in flags] or ["- none"]),
        "",
        "# Candidate amounts (thousands of yen); choose one by its number, or null for the grid amount",
        *(cand_lines or ["- none: the grid gives no amount for this lot"]),
        "",
        playbook_text,
    ])
    content: list[dict] = [text_part(header)]
    images: list[str] = []
    if sheet_path is not None and Path(sheet_path).exists():
        block = image_part(Path(sheet_path))
        if block:
            content.append(text_part("Auction sheet:"))
            content.append(block)
            images.append(Path(sheet_path).name)
    else:
        content.append(text_part("Auction sheet: not available"))
    for i, p in enumerate(photo_paths[:max_photos], 1):
        block = image_part(Path(p)) if Path(p).exists() else None
        if block:
            content.append(text_part(f"Photo {i}:"))
            content.append(block)
            images.append(Path(p).name)
    if not photo_paths:
        content.append(text_part("Photos: none available; anything that needs the photos is unclear."))
    content.append(text_part("Now judge every requirement id listed above, every chip, the odd cases, and pick the amount."))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
    return messages, images


def decide(judgement: dict, reqs: list[Requirement], candidates: list[Candidate], hard: HardResult) -> tuple[str, int | None, list[str]]:
    """(recommendation, proposed amount, notes). Deterministic, so the reviewer can see why."""
    notes: list[str] = []
    verdicts = {v.get("id"): v for v in judgement.get("requirements", [])}
    musts = [r for r in reqs if r.kind in ("must", "instruction", "eligibility")]
    fails = [r for r in musts if verdicts.get(r.id, {}).get("verdict") == "fail"
             and verdicts[r.id].get("confidence", "medium") != "low"]
    unclear = [r for r in musts if verdicts.get(r.id, {}).get("verdict", "unclear") == "unclear"]
    low = [r for r in musts if verdicts.get(r.id, {}).get("confidence") == "low"]
    missing = [r.id for r in musts if r.id not in verdicts]
    if missing:
        notes.append("model gave no verdict for: " + ", ".join(missing))
    chip_says = {c.get("id"): c for c in judgement.get("chips", [])}
    if fails:
        rec = "skip"
        notes.append("fails: " + "; ".join(f"{r.text} ({verdicts[r.id].get('evidence', '')})" for r in fails))
    elif unclear or low or missing or judgement.get("confidence") == "low":
        rec = "review"
        if unclear:
            notes.append("unclear: " + "; ".join(r.text for r in unclear))
        if low:
            notes.append("low confidence on: " + "; ".join(r.text for r in low))
    elif any(o.get("confidence") == "low" or not o.get("recommendation") for o in judgement.get("odd_cases", [])):
        rec = "review"
        notes.append("odd case without a clear recommendation")
    else:
        rec = judgement.get("recommendation") or "review"
    amounts = {c.amount_k for c in candidates}
    grid = hard.bid_jpy_k
    proposed = None
    model_amount = judgement.get("amount_k")
    if model_amount is not None and int(model_amount) in amounts:
        proposed = int(model_amount)
        if judgement.get("amount_reason"):
            notes.append(f"amount: {judgement['amount_reason']}")
    elif model_amount is not None:
        notes.append(f"model proposed {model_amount}K, not among the candidates; ignored")
    if proposed is None and grid:
        applied = [r for r in reqs if r.kind == "chip" and r.amount_k and chip_says.get(r.id, {}).get("applies") == "yes"]
        total = grid + sum(r.amount_k for r in applied)
        if applied and (total in amounts or len(applied) > 1):
            proposed = total
            notes.append("chips applied: " + ", ".join(f"{r.text} {r.amount_k:+d}K" for r in applied))
        else:
            proposed = grid
    return rec, proposed, notes


def judge_lot(client: JudgeClient, cfg: Config, store, facts: VehicleFacts, rule: ModelRule, hard: HardResult,
              evidence: dict, ocr: dict | None = None, ocr_hints: list[dict] | None = None, hints: list[dict] | None = None,
              reqs: list[Requirement] | None = None, flags: list[str] | None = None) -> JudgeResult:
    reqs = reqs if reqs is not None else requirements_for(rule, hard)
    flags = flags if flags is not None else odd_flags(rule, hard, facts, ocr_hints, hints)
    cands = candidates_for(rule, facts, hard)
    result = JudgeResult(requirements=[r.to_dict() for r in reqs], flags=flags, candidates=[c.to_dict() for c in cands],
                         requirements_hash=requirements_hash(reqs, flags), proposed_bid_jpy_k=hard.bid_jpy_k)
    judge_cfg = getattr(cfg, "judge", None) or {}
    max_photos = int(judge_cfg.get("max_photos") or 8)
    playbook = playbook_for(store, cfg, rule, facts).to_text()
    sheet = Path(evidence["sheet"]) if evidence.get("sheet") else None
    photos = [Path(p) for p in evidence.get("photos", [])]
    messages, images = build_messages(facts, rule, hard, reqs, flags, cands, playbook, sheet, photos, ocr, max_photos)
    result.images_used = images
    result.prompt_chars = sum(len(part.get("text", "")) for m in messages for part in (m["content"] if isinstance(m["content"], list) else [{"text": m["content"]}]))
    call: CallResult = client.complete(messages, Judgement)
    result.provider, result.model, result.usage = call.provider, call.model, call.usage
    if not call.ok:
        result.unavailable = call.unavailable
        result.notes = [f"model unavailable: {call.unavailable}"]
        return result
    result.judgement = call.parsed
    result.recommendation, result.proposed_bid_jpy_k, result.notes = decide(call.parsed, reqs, cands, hard)
    return result
