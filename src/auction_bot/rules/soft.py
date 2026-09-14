"""Auction-sheet and photo checks with the OpenAI API, then a Python decision.

The model only extracts facts and gives per-rule verdicts with evidence; the
pass/skip/review decision is made here so it is auditable and deterministic.
"""
from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import openai

from auction_bot.carbarn.models import ModelRule
from auction_bot.config import Config
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.nichibo.vehicle import sniff_image
from auction_bot.rules.hard import NEEDS_REVIEW, PASS, SKIP
from auction_bot.rules.schema import SheetCheck

SYSTEM_PROMPT = """You are the auction-sheet reader for an Australian vehicle importer that buys at Japanese car auctions through Nichibo.
You receive one vehicle: the listing facts, the English translation Nichibo produced for the auction sheet (may be empty), the auction sheet image, and the listing photos.

Your job is to extract what the sheet and photos actually show and to judge each buying rule against that evidence. Money is at stake, so be conservative:
- Report a value only when you can see it. If a field is not visible, illegible or absent, return null / "unknown" and say "not visible" in the evidence.
- Never infer seats, drivetrain, roof height, wheelbase, seat material or colour from the model name alone. Use the sheet fields (乗車定員 = seating capacity, 4WD marking next to the grade name, 型式 = model code) and the photos.
- Japanese era years: R = Reiwa (R1 = 2019, R3 = 2021), H = Heisei (H30 = 2018).
- A rule verdict is "pass" only when the evidence clearly satisfies it, "fail" when the evidence clearly violates it, otherwise "unknown".
- Quote the specific evidence (which photo, which sheet field, what it says) for every verdict.
"""


@dataclass
class SoftResult:
    status: str
    parsed: dict | None
    reasons: list[str] = field(default_factory=list)
    model: str = ""
    usage: dict | None = None
    error: str | None = None
    prompt_chars: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _image_block(path: Path) -> dict | None:
    data = path.read_bytes()
    kind = sniff_image(data)
    if kind is None:
        return None
    b64 = base64.standard_b64encode(data).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{kind[0]};base64,{b64}", "detail": "high"}


def build_user_content(facts: VehicleFacts, rule: ModelRule, sheet_path: Path | None,
                       photo_paths: list[Path], past_decisions: list[dict]) -> list[dict]:
    rules_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(rule.text_rules)) or "(none)"
    override = rule.override_text or "(none)"
    past = "\n".join(
        f"- {d.get('decision')}: {d.get('reason')} (vehicle {d.get('summary')})" for d in past_decisions
    ) or "(none)"
    translator = json.dumps(facts.translator, ensure_ascii=False, indent=1) if facts.translator else "(not available)"
    header = f"""# Vehicle
Nichibo listing: {facts.year} {facts.make} {facts.model} / variant "{facts.variant_text}"
Chassis: {facts.chassis}   Odometer: {facts.km} km   Listing grade: {facts.grade_raw}   Colour: {facts.colour}
Transmission: {facts.transmission}   Engine: {facts.engine_cc} cc   Equipment: {facts.equipment}
Auction: {facts.auction_place} exhibit {facts.exhibit_no} on {facts.auction_date}

# Buying rules for model code {rule.model_code} ({rule.make} {rule.model})
Minimum grade: {rule.min_grade}
Text rules (judge each one):
{rules_text}

# Standing instructions from the buyer for this model (treat as rules too)
{override}

# Past human decisions on similar {rule.model_code} vehicles
{past}

# Nichibo auction sheet translation (machine translated, may be partial)
{translator}
"""
    content: list[dict] = [{"type": "input_text", "text": header}]
    if sheet_path is not None:
        block = _image_block(sheet_path)
        if block:
            content.append({"type": "input_text", "text": "Auction sheet image:"})
            content.append(block)
    else:
        content.append({"type": "input_text", "text": "Auction sheet image: MISSING"})
    for i, p in enumerate(photo_paths):
        block = _image_block(p)
        if block:
            content.append({"type": "input_text", "text": f"Photo {i + 1} ({p.name}):"})
            content.append(block)
    content.append({"type": "input_text", "text": (
        "Fill in every field of the schema. In rule_verdicts include one entry per text rule above "
        "and one per standing instruction, using the rule text verbatim.")})
    return content


def call_model(cfg: Config, content: list[dict]) -> tuple[SheetCheck | None, dict]:
    """One OpenAI Responses API call with a strict JSON schema (SheetCheck)."""
    client = openai.OpenAI()
    kwargs: dict = {}
    effort = getattr(cfg, "model_reasoning_effort", None)
    if effort:
        kwargs["reasoning"] = {"effort": effort}
    response = client.responses.parse(
        model=cfg.model,
        instructions=SYSTEM_PROMPT,
        input=[{"role": "user", "content": content}],
        text_format=SheetCheck,
        max_output_tokens=8000,
        **kwargs,
    )
    usage = getattr(response, "usage", None)
    meta = {
        "model": getattr(response, "model", cfg.model),
        "status": getattr(response, "status", None),
        "usage": {"input": getattr(usage, "input_tokens", None), "output": getattr(usage, "output_tokens", None)} if usage else None,
    }
    refusal = None
    for item in getattr(response, "output", []) or []:
        for part in getattr(item, "content", []) or []:
            if getattr(part, "type", "") == "refusal":
                refusal = getattr(part, "refusal", "refused")
    if response.output_parsed is None:
        meta["refusal"] = refusal or "no parsed output"
        return None, meta
    return response.output_parsed, meta


def decide(parsed: SheetCheck, facts: VehicleFacts, rule: ModelRule) -> tuple[str, list[str]]:
    reasons: list[str] = []
    status = PASS
    if not parsed.sheet_readable:
        return NEEDS_REVIEW, ["auction sheet missing or unreadable"]
    for rv in parsed.rule_verdicts:
        if rv.verdict == "fail":
            reasons.append(f"rule failed: {rv.rule} ({rv.evidence})")
            status = SKIP
        elif rv.verdict == "unknown":
            reasons.append(f"rule undecided: {rv.rule} ({rv.evidence})")
            if status != SKIP:
                status = NEEDS_REVIEW
    # Sheet vs listing consistency.
    if parsed.sheet_km is not None and facts.km is not None:
        if abs(parsed.sheet_km - facts.km) > max(1_500, int(facts.km * 0.03)):
            reasons.append(f"sheet odometer {parsed.sheet_km:,} km disagrees with listing {facts.km:,} km")
            if status != SKIP:
                status = NEEDS_REVIEW
    if parsed.sheet_grade and facts.grade_raw and parsed.sheet_grade.strip() != facts.grade_raw.strip():
        reasons.append(f"sheet grade '{parsed.sheet_grade}' disagrees with listing grade '{facts.grade_raw}'")
        if status != SKIP:
            status = NEEDS_REVIEW
    if parsed.model_code_on_sheet and facts.chassis and facts.chassis.upper() not in parsed.model_code_on_sheet.upper():
        reasons.append(f"sheet model code '{parsed.model_code_on_sheet}' does not contain listing chassis '{facts.chassis}'")
        if status != SKIP:
            status = NEEDS_REVIEW
    if parsed.confidence == "low" and status == PASS:
        reasons.append("model confidence low")
        status = NEEDS_REVIEW
    if parsed.notable_issues and status == PASS:
        reasons.append("notable issues: " + "; ".join(parsed.notable_issues))
    return status, reasons


def evaluate_soft(cfg: Config, facts: VehicleFacts, rule: ModelRule, evidence: dict,
                  past_decisions: list[dict]) -> SoftResult:
    sheet = Path(evidence["sheet"]) if evidence.get("sheet") else None
    photos = [Path(p) for p in evidence.get("photos", [])]
    if sheet is None and not photos:
        return SoftResult(NEEDS_REVIEW, None, ["no auction sheet and no photos could be downloaded"])
    content = build_user_content(facts, rule, sheet, photos, past_decisions)
    prompt_chars = sum(len(c.get("text", "")) for c in content)
    try:
        parsed, meta = call_model(cfg, content)
    except openai.OpenAIError as exc:
        return SoftResult(NEEDS_REVIEW, None, [f"model call failed: {exc}"], error=str(exc), prompt_chars=prompt_chars)
    if parsed is None:
        return SoftResult(NEEDS_REVIEW, None, ["model declined the request"], model=meta.get("model", ""),
                          usage=meta.get("usage"), error="refusal", prompt_chars=prompt_chars)
    status, reasons = decide(parsed, facts, rule)
    if parsed.seats is not None:
        facts.seats = parsed.seats
    if sheet is None:
        reasons.append("auction sheet image was not downloadable")
        if status == PASS:
            status = NEEDS_REVIEW
    return SoftResult(status, parsed.model_dump(), reasons, model=meta.get("model", ""),
                      usage=meta.get("usage"), prompt_chars=prompt_chars)
