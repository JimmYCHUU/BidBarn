"""What a guide asks for beyond the grid, and where the answer lives.

Every text rule, reviewer chip and free-text standing instruction becomes a Requirement
with a stable id and a `source`: `card` (year, km, grade, colour on the listing), `sheet`
(seats, engine, model code, 4WD on the auction sheet), `photos` (seat colour, leather,
Pro Pilot button, Autech badges, stickers, body) or `any`. Odd-case flags name what makes
a lot unusual. Both are deterministic, so the model is told exactly what to look at and
routine lots never cost a model call.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass

from auction_bot.carbarn.models import ModelRule
from auction_bot.judge.topics import (TOPIC_CRUISE, TOPIC_WELCAB, WELCAB_GUIDE_TEXT, approval_seat_sentences, cruise_kind,
                                      detect_topic, is_negated, welcab_guide)
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.eligibility import Eligibility
from auction_bot.rules.hard import HardResult
from auction_bot.rules.instructions import free_text_lines

_CARD_FIRST = re.compile(r"(colou?r.*grade|grade.*colou?r|minimum grade|min\.? grade)", re.I)
_PHOTOS = re.compile(r"seat colou?r|colou?red seats?|dark colou?r|beige|leather|captain|pro ?pilot|autech|sticker|wrap|decal|"
                     r"\bbody\b|roof|badge|steering|\bwheels?\b|bumper|paint|interior|exterior|photo|picture|dent|rust|scratch|alloy|\bkit\b", re.I)
_SHEET = re.compile(r"\bseat(?:s|er|ers)?\b|engine|\bcc\b|litt?res?|liters?|\b\d(?:\.\d)?\s*l\b|model code|型式|chassis|"
                    r"\b[24]wd\b|\bawd\b|hybrid|e-power|\bgrade\b|\bkm\b|\bodo|registration|sheet|wheelbase|long", re.I)
_CARD = re.compile(r"colou?r|grade|year|\bkm\b|\bodo|start", re.I)
_CHIP = re.compile(r"^(?P<note>.*?)\s*(?P<amount>[+-]\d+)K$")


@dataclass
class Requirement:
    id: str
    text: str
    source: str  # card | sheet | photos | any | ladder
    kind: str  # must | chip | instruction | eligibility
    amount_k: int | None = None  # chips: the dashboard's adjustment
    topic: str | None = None  # cruise_control | welcab_seat
    topic_detail: str | None = None  # cruise: plain | adaptive
    negated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def classify(text: str) -> str:
    t = text or ""
    if _CARD_FIRST.search(t) and not _PHOTOS.search(t):
        return "card"
    if _PHOTOS.search(t):
        return "photos"
    if _SHEET.search(t):
        return "sheet"
    if _CARD.search(t):
        return "card"
    return "any"


def requirements_for(rule: ModelRule, hard: HardResult | None = None,
                     eligibility: Eligibility | None = None) -> list[Requirement]:
    out: list[Requirement] = []
    for i, text in enumerate(rule.text_rules, 1):
        topic = detect_topic(text)
        if topic:
            # Negation applies to cruise notes only: the Welcab seat table already says what passes and fails.
            negated = is_negated(text) if topic == TOPIC_CRUISE else False
            out.append(Requirement(f"r{i}", text, "ladder", "must", topic=topic,
                                   topic_detail=cruise_kind(text) if topic == TOPIC_CRUISE else None, negated=negated))
        else:
            out.append(Requirement(f"r{i}", text, classify(text), "must"))
    if welcab_guide(rule) and not any(r.topic == TOPIC_WELCAB for r in out):
        out.append(Requirement("g:welcab", WELCAB_GUIDE_TEXT, "ladder", "must", topic=TOPIC_WELCAB))
    if any(r.topic == TOPIC_WELCAB for r in out):
        variant = rule.resolve_variant(hard.variant_name or "", "").variant if hard else None
        sentences = approval_seat_sentences(rule.approvals_for(variant))
        wording = (" Approval wording: " + "; ".join(sentences)) if sentences else " No approval text names the seat system; confirm on ROVER by hand."
        for r in out:
            if r.topic == TOPIC_WELCAB:
                r.text += wording
    for t in (hard.toggles if hard else []):
        m = _CHIP.match(t.strip())
        note, amount = (m.group("note").strip(), int(m.group("amount"))) if m else (t.strip(), None)
        topic = detect_topic(note)
        if topic == TOPIC_CRUISE:  # only cruise chips route to the ladder (and only cruise reads negation); seat chips keep the model's answer
            out.append(Requirement(f"chip:{note.lower().replace(' ', '-')}", note, "ladder", "chip", amount, topic=topic,
                                   topic_detail=cruise_kind(note), negated=is_negated(note)))
        else:
            out.append(Requirement(f"chip:{note.lower().replace(' ', '-')}", note, classify(note) if classify(note) != "any" else "photos",
                                   "chip", amount))
    for t in (eligibility.texts if eligibility else []):
        out.append(Requirement(t["id"], f'{t["label"]}: {t["text"]}', "sheet", "eligibility"))
    for i, text in enumerate(free_text_lines(rule), 1):
        out.append(Requirement(f"note:{i}", text, classify(text), "instruction"))
    return out


def needs_photos(reqs: list[Requirement]) -> bool:
    return any(r.source in ("photos", "any", "ladder") for r in reqs)


def has_ladder(reqs: list[Requirement]) -> bool:
    return any(r.source == "ladder" for r in reqs)


def judge_photos(cfg, reqs: list[Requirement] | None = None) -> int:
    """How many photos to keep and send: the ladder cap when an equipment ladder is in play, else the usual cap."""
    judge = getattr(cfg, "judge", None) or {}
    if has_ladder(reqs or []):
        return int(judge.get("max_photos_ladder") or 40)
    return int(judge.get("max_photos") or 8)


def ladder_topics(reqs: list[Requirement]) -> set[str]:
    return {r.topic for r in reqs if r.source == "ladder" and r.topic}


_SEAT_ONLY = re.compile(r"^[\d\s,/&+]*(?:or|and)?[\d\s,/&+]*seat(?:s|er|ers|ing)?\s*(?:only)?$", re.I)


def machine_decided(r: Requirement) -> bool:
    """A plain seat-count rule ("2, 3, 5 or 6 seats") is settled by the OCR and the hard rules."""
    return r.kind == "must" and r.source == "sheet" and bool(_SEAT_ONLY.match(r.text.strip()))


def needs_judge(reqs: list[Requirement], flags: list[str]) -> bool:
    """Ask the model when something must be read from the photos or the sheet image, a chip exists,
    or the lot is odd. Card-decidable and plain seat-count rules never cost a call."""
    if needs_photos(reqs) or any(r.kind == "chip" for r in reqs) or flags:
        return True
    return any(r.source == "sheet" and not machine_decided(r) for r in reqs)


_HINT_ODD = re.compile(r"repair|accident|welcab|wheelchair|dump|ambulance|\bR\b|corrosion|rust|frame|flood|fire", re.I)


def odd_flags(rule: ModelRule, hard: HardResult, facts: VehicleFacts, ocr_hints: list[dict] | None = None,
              hints: list[dict] | None = None, eligibility: Eligibility | None = None) -> list[str]:
    """What makes this lot unusual, in words the model and the reviewer both get."""
    flags: list[str] = []
    if hard.variant_how in ("trim", "single", "chassis-uncertain", "ambiguous"):
        note = next((c.detail for c in hard.checks if c.name == "variant"), "")
        flags.append(f"variant resolved by {hard.variant_how}: {note}"[:200])
    for m in hard.deferred_modifiers:
        flags.append(f"modifier needs judgement: {m}"[:200])
    if facts.chassis_uncertain:
        flags.append("Nichibo guessed the chassis code (shown with '?')")
    match = rule.resolve_variant(facts.chassis, facts.variant_text)
    variant = match.variant
    if variant is not None and facts.year is not None and facts.km is not None:
        cell = variant.cell_for(facts.year, facts.km)
        if cell is not None and facts.km == cell.band.odo_to:
            flags.append(f"odometer exactly on the band boundary ({facts.km:,} km, band {cell.band.label}); the odometer rises before delivery")
        elif variant.max_odo and facts.km >= 0.9 * variant.max_odo:
            flags.append(f"odometer within 10 % of the guide's cap ({facts.km:,} of {variant.max_odo:,} km); the odometer rises before delivery")
    for h in ocr_hints or []:
        if h.get("level") == "warn":
            flags.append(f"sheet disagrees with the listing: {h.get('message')}"[:200])
    for h in hints or []:
        msg = h.get("message") or ""
        if _HINT_ODD.search(msg):
            flags.append(f"listing mentions: {msg}"[:200])
    tr = facts.translator or {}
    for key in ("VehicleCondition", "Comments", "SalesPoint"):
        if tr.get(key):
            flags.append(f"sheet notes ({key}): {str(tr[key])[:120]}")
            break
    for c in (eligibility.checks if eligibility else []):
        if c.state in ("fail", "unclear") and c.name != "source_age":
            flags.append(f"import approval {c.name} {c.state}: {c.detail}"[:200])
    return flags


def requirements_hash(reqs: list[Requirement], flags: list[str]) -> str:
    blob = "|".join(f"{r.id}={r.text}={r.amount_k}={r.topic}={r.topic_detail}={r.negated}" for r in reqs) + "||" + "|".join(flags)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
