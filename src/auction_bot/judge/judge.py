"""The judgement of one lot: prompt, schema, the call, and a deterministic reading of the answer.

The model receives the lot's facts, the OCR fields, Nichibo's translation, the guide's
requirements with where to look, the chips with their amounts, the odd-case flags, the
playbook, the candidate amounts, the sheet and up to `max_photos` photos. It answers with
a verdict and evidence per requirement, whether each chip applies, the odd cases it sees,
one of the candidate amounts (or none), a recommendation and a two-line summary. The
status of the lot stays NEEDS_REVIEW: Jim confirms everything on the review page.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from auction_bot.carbarn.models import ModelRule
from auction_bot.config import Config
from auction_bot.judge.candidates import Candidate, candidates_for
from auction_bot.judge.client import CallResult, JudgeClient, image_part, text_part
from auction_bot.judge.ladders import LadderVerdict, Observation, chip_applies, resolve
from auction_bot.judge.playbook import playbook_for
from auction_bot.judge.requirements import Requirement, judge_photos, odd_flags, requirements_for, requirements_hash
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.hard import NEEDS_REVIEW, HardResult

Confidence = Literal["high", "medium", "low"]
_ALT_ID = re.compile(r"^elig:[a-z-]+:(\d+)$")  # a numbered approval pair: alternatives when no pair is selected


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
    observations: list[Observation] = Field(default_factory=list)

    @field_validator("observations", mode="before")
    @classmethod
    def _keep_usable_observations(cls, value):
        if not isinstance(value, list):
            return []
        return [o for o in value if (isinstance(o, dict) and isinstance(o.get("id"), str))
                or isinstance(o, Observation)]

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
- Ladder requirements (cruise control, Welcab seat): report what you see at each step in `observations` and let the buyer's
  rules decide; do not fill `requirements` for those ids. Sheet step for cruise control: クルーズコントロール, クルコン, レーダークルーズ,
  追従 or "cruise" written or ticked on the sheet, in Nichibo's translation or in the Equipment field = yes; a bare "CC" is not cruise
  control (say not_mentioned and name the CC in the evidence); otherwise not_mentioned. Photo step: find the steering wheel; a stalk
  below the right spoke (Toyota, Lexus) or buttons on the right spoke marked CRUISE, RES+, SET-, CANCEL, or a car-with-lines icon
  (adaptive) = yes with the photo number; bare spokes and no stalk = no; something that might be a stalk or a button you cannot
  confirm = maybe; no photo of the steering wheel = not_visible. Set adaptive to yes when the sheet or the buttons show radar or
  adaptive cruise. A chip with a ladder is also reported in observations, not in chips.
- Welcab seat states: front_swivel (助手席回転シート: the front passenger seat turns to the door), front_lift_up (助手席リフトアップシート:
  it turns and lowers), second_row_lift_up (サイドリフトアップシート or サイドリフトアップチルトシート: a second-row seat swings out and
  lowers), wheelchair_lift (a rear lift for a wheelchair), slope (a rear ramp or slope, wheelchair tie-downs, 車いす仕様),
  welcab_unspecified (a Welcab with no seat system named), not_welcab (an ordinary car). Sheet: not_mentioned when it says nothing;
  photo: not_visible when no photo shows the seat or the rear.
- The sheet outranks the photos; the photos outrank the catalogue. In catalog_grade name the catalogue grade that matches the
  listing's variant text, or null.
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
    ladders: list[dict] = field(default_factory=list)
    maker: dict | None = None

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


def _req_line(r: Requirement) -> str:
    chip = f" (chip {r.amount_k:+d}K)" if r.amount_k else ""
    if r.topic:
        detail = f"/{r.topic_detail}" if r.topic_detail else ""
        return (f"- {r.id} [{r.kind}, ladder {r.topic}{detail}]: {r.text} "
                f"(report sheet, photo with its number, catalogue grade; do not decide)" + chip)
    return f"- {r.id} [{r.kind}, look at: {r.source}]: {r.text}" + chip


def _maker_block(maker: dict | None, year) -> list[str]:
    """The maker catalogue for the prompt: grades with their marks, never prices."""
    if not maker:
        return []
    grades = maker.get("grades") or []
    if not grades:
        return [f"# Maker catalogue: no data ({maker.get('note') or 'no catalogue page'})"]
    codes = ", ".join(c for c in (maker.get("model_codes") or []) if c)
    lines = [f"# Maker catalogue (kurumaerabi.com) for 型式 {codes}, grades on sale around {year}"]
    for g in grades:
        seats = g.get("seats") if g.get("seats") is not None else "?"
        lines.append(f"- {g.get('name')} ({seats} seats): cruise {g.get('cruise_mark') or '?'}; adaptive {g.get('adaptive_mark') or '?'}; "
                     f"welfare {'yes' if g.get('welfare') is True else 'no'}; seat system {g.get('seat_system') or '?'}")
    lines.append("Name the catalogue grade that matches the listing's variant text in catalog_grade, or null.")
    return lines


def build_messages(facts: VehicleFacts, rule: ModelRule, hard: HardResult, reqs: list[Requirement], flags: list[str],
                   candidates: list[Candidate], playbook_text: str, sheet_path: Path | None, photo_paths: list[Path],
                   ocr: dict | None, max_photos: int = 8, maker: dict | None = None) -> tuple[list[dict], list[str]]:
    """The chat messages (text plus images) and the names of the images that were attached."""
    req_lines = [_req_line(r) for r in reqs if r.kind != "eligibility"]
    elig_lines = [f"- {r.id} [eligibility, look at: {r.source}]: {r.text}" for r in reqs if r.kind == "eligibility"]
    cand_lines = [f"- {c.amount_k:,}K: {c.label}, {c.why}" for c in candidates]
    header = "\n".join([
        _facts_block(facts, ocr),
        "",
        f"# The buyer's requirements for model code {rule.model_code} ({rule.make} {rule.model})",
        f"Minimum grade {rule.min_grade}. Variant used: {hard.variant_name or '?'} (resolved by {hard.variant_how}).",
        *(req_lines or ["- (no text requirements; check the sheet and photos for anything unusual)"]),
        *(["", "# Import approval conditions (ROVER SEV / Model Report)",
           *(["(Pairs numbered 1, 2, ... are alternative approvals: the lot needs to fit any one of them.)"]
             if any(_ALT_ID.match(r.id) for r in reqs) else []),
           *elig_lines] if elig_lines else []),
        "",
        "# Odd-case flags raised by the rules",
        *([f"- {f}" for f in flags] or ["- none"]),
        "",
        "# Candidate amounts (thousands of yen); choose one by its number, or null for the grid amount",
        *(cand_lines or ["- none: the grid gives no amount for this lot"]),
        "",
        *(_maker_block(maker, facts.year) + [""] if maker else []),
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
    content.append(text_part("Now judge every requirement id listed above, every chip, the observations for the ladder requirements, the odd cases, and pick the amount."))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
    return messages, images


def _alternatives(groups: dict[str, list[Requirement]], verdicts: dict) -> tuple[str, bool]:
    """Numbered approval pairs are alternatives: one pair that fits is enough.
    Returns (fail note or "", whether the pairs are unclear)."""
    if not groups:
        return "", False

    def said(r: Requirement) -> dict:
        return verdicts.get(r.id, {})

    if any(all(said(r).get("verdict") == "pass" for r in g) for g in groups.values()):
        return "", False
    lines = []
    for n, g in groups.items():
        bad = next((r for r in g if said(r).get("verdict") == "fail" and said(r).get("confidence", "medium") != "low"), None)
        if bad is None:
            return "", True
        lines.append(f"{bad.text} ({said(bad).get('evidence', '')})")
    return "no approval pair fits: " + "; ".join(lines), False


def resolve_ladders(judgement: dict, reqs: list[Requirement], maker: dict | None, photos_complete: bool) -> list[LadderVerdict]:
    """One verdict per requirement with a topic, from the model's observations; the model never decides these."""
    obs: dict = {}
    clash: set = set()
    for o in judgement.get("observations") or []:
        if not isinstance(o, dict):
            continue
        key = o.get("id")
        if key in obs:
            a, b = obs[key], o
            if any((a.get(s) or {}).get("state") != (b.get(s) or {}).get("state") for s in ("sheet", "photo")):
                clash.add(key)
            continue
        obs[key] = o
    out = []
    for r in reqs:
        if not r.topic:
            continue
        if r.id in clash:
            out.append(LadderVerdict(r.id, "unclear", f"model gave two observations for {r.id}", []))
        else:
            out.append(resolve(r, obs.get(r.id), maker, photos_complete))
    return out


def decide(judgement: dict, reqs: list[Requirement], candidates: list[Candidate], hard: HardResult,
           ladders: list[LadderVerdict] | None = None) -> tuple[str, int | None, list[str]]:
    """(recommendation, proposed amount, notes). Deterministic, so the reviewer can see why."""
    notes: list[str] = []
    verdicts = {v.get("id"): v for v in judgement.get("requirements", [])}
    chip_says = {c.get("id"): c for c in judgement.get("chips", [])}
    by_id = {r.id: r for r in reqs}
    ladder_ids: set[str] = set()
    ladder_chips: list[Requirement] = []
    for lv in ladders or []:
        req = by_id.get(lv.id)
        if req is None:
            continue
        ladder_ids.add(lv.id)
        if req.kind == "chip":
            chip_says[lv.id] = {"id": lv.id, "applies": chip_applies(req, lv.verdict), "evidence": lv.evidence}
            if chip_says[lv.id]["applies"] in ("yes", "no"):
                ladder_chips.append(req)
        else:
            verdicts[lv.id] = {"id": lv.id, "verdict": lv.verdict, "evidence": lv.evidence, "confidence": "high"}
    groups: dict[str, list[Requirement]] = {}
    for r in reqs:
        m = _ALT_ID.match(r.id) if r.kind == "eligibility" else None
        if m:
            groups.setdefault(m.group(1), []).append(r)
    musts = [r for r in reqs if r.kind in ("must", "instruction", "eligibility") and not (r.kind == "eligibility" and _ALT_ID.match(r.id))]
    alt_fail, alt_unclear = _alternatives(groups, verdicts)
    fails = [r for r in musts if verdicts.get(r.id, {}).get("verdict") == "fail"
             and verdicts[r.id].get("confidence", "medium") != "low"]
    unclear = [r for r in musts if verdicts.get(r.id, {}).get("verdict", "unclear") == "unclear"]
    low = [r for r in musts if verdicts.get(r.id, {}).get("confidence") == "low"]
    missing = [r.id for r in musts if r.id not in verdicts]
    if missing:
        notes.append("model gave no verdict for: " + ", ".join(missing))
    if fails or alt_fail:
        rec = "skip"
        parts = [f"{r.text} ({verdicts[r.id].get('evidence', '')})" for r in fails]
        notes.append("fails: " + "; ".join(parts + ([alt_fail] if alt_fail else [])))
    elif unclear or low or missing or alt_unclear or judgement.get("confidence") == "low":
        rec = "review"
        if unclear:
            notes.append("unclear: " + "; ".join(
                f"{r.text} ({verdicts[r.id]['evidence']})" if r.topic and r.id in ladder_ids and verdicts[r.id].get("evidence") else r.text
                for r in unclear))
        if alt_unclear:
            notes.append("unclear: confirm which approval pair applies")
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
    if ladder_chips and grid:
        # The ladder decides these chips; the model's amount does not override it.
        total = grid + sum(r.amount_k or 0 for r in chip_says_applied(reqs, chip_says))
        if model_amount is not None and int(model_amount) != total:
            why = "; ".join(f"chip '{r.text}' {'applies' if chip_says[r.id]['applies'] == 'yes' else 'does not apply'}"
                            for r in ladder_chips)
            notes.append(f"model amount {int(model_amount)}K ignored: ladder says {why}")
        if proposed != total:
            notes[:] = [n for n in notes if not n.startswith("chips applied:")]
        proposed = total
    return rec, proposed, notes


def chip_says_applied(reqs: list[Requirement], chip_says: dict) -> list[Requirement]:
    return [r for r in reqs if r.kind == "chip" and r.amount_k and chip_says.get(r.id, {}).get("applies") == "yes"]


def redecide(judgement: dict, reqs: list[Requirement], candidates: list[Candidate], hard: HardResult, maker: dict | None,
             photos_complete: bool) -> tuple[str, int | None, list[str], list[dict]]:
    """Resolve the ladders, then decide. Also used for stored judgements."""
    ladders = resolve_ladders(judgement, reqs, maker, photos_complete)
    rec, amount, notes = decide(judgement, reqs, candidates, hard, ladders)
    return rec, amount, notes, [lv.to_dict() for lv in ladders]


def judge_lot(client: JudgeClient, cfg: Config, store, facts: VehicleFacts, rule: ModelRule, hard: HardResult,
              evidence: dict, ocr: dict | None = None, ocr_hints: list[dict] | None = None, hints: list[dict] | None = None,
              reqs: list[Requirement] | None = None, flags: list[str] | None = None,
              maker: dict | None = None, photos_complete: bool = True) -> JudgeResult:
    reqs = reqs if reqs is not None else requirements_for(rule, hard)
    flags = flags if flags is not None else odd_flags(rule, hard, facts, ocr_hints, hints)
    cands = candidates_for(rule, facts, hard)
    result = JudgeResult(requirements=[r.to_dict() for r in reqs], flags=flags, candidates=[c.to_dict() for c in cands],
                         requirements_hash=requirements_hash(reqs, flags), proposed_bid_jpy_k=hard.bid_jpy_k, maker=maker)
    judge_cfg = getattr(cfg, "judge", None) or {}
    max_photos = judge_photos(cfg, reqs)
    playbook = playbook_for(store, cfg, rule, facts).to_text()
    sheet = Path(evidence["sheet"]) if evidence.get("sheet") else None
    photos = [Path(p) for p in evidence.get("photos", [])]
    messages, images = build_messages(facts, rule, hard, reqs, flags, cands, playbook, sheet, photos, ocr, max_photos, maker=maker)
    result.images_used = images
    result.prompt_chars = sum(len(part.get("text", "")) for m in messages for part in (m["content"] if isinstance(m["content"], list) else [{"text": m["content"]}]))
    call: CallResult = client.complete(messages, Judgement)
    result.provider, result.model, result.usage = call.provider, call.model, call.usage
    if not call.ok:
        result.unavailable = call.unavailable
        result.notes = [f"model unavailable: {call.unavailable}"]
        return result
    result.judgement = call.parsed
    result.recommendation, result.proposed_bid_jpy_k, result.notes, result.ladders = redecide(
        call.parsed, reqs, cands, hard, maker, photos_complete)
    return result
