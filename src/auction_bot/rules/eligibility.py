"""Import eligibility of a lot against the MRE/SEV approval pair on its variant. Pure functions,
no I/O. It never skips a lot and never decides a bid: the verdict is information for the reviewer.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from auction_bot.carbarn.models import Approval, ModelRule, Variant
from auction_bot.nichibo.facts import VehicleFacts

PASS = "pass"
FAIL = "fail"
UNCLEAR = "unclear"
INFO = "info"

ELIGIBLE = "eligible"
CONFIRM = "confirm"
CANNOT_IMPORT = "cannot_import"

BUILD_TEXT = "Read 初度登録 (first registration) on the sheet; fail if before {start}; otherwise unclear"


@dataclass
class EligCheck:
    name: str  # window | expiry | model_code | source_age
    state: str  # pass | fail | unclear | info
    detail: str


@dataclass
class Eligibility:
    verdict: str  # eligible | confirm | cannot_import
    pair: dict | None
    checks: list[EligCheck] = field(default_factory=list)
    texts: list[dict] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _mmyyyy(ym: int) -> str:
    return f"{ym % 100:02d}/{ym // 100}"


def _window_text(from_ym: int | None, to_ym: int | None) -> str:
    if from_ym and to_ym:
        return f"{_mmyyyy(from_ym)} to {_mmyyyy(to_ym)}"
    if from_ym:
        return f"from {_mmyyyy(from_ym)}"
    if to_ym:
        return f"to {_mmyyyy(to_ym)}"
    return "open"


def window_state(year: int | None, from_ym: int | None, to_ym: int | None) -> tuple[str, str]:
    """(state, detail) for the registration year against the build window."""
    if year is None:
        return INFO, "registration year unknown"
    if from_ym:
        fy, fm = divmod(from_ym, 100)
        if year < fy:
            return FAIL, f"registered {year}, build window opens {_mmyyyy(from_ym)}"
        if year == fy and fm > 1:
            return UNCLEAR, f"confirm the build month (window opens {_mmyyyy(from_ym)})"
    if to_ym:
        ty, tm = divmod(to_ym, 100)
        if year > ty:
            return UNCLEAR, f"registered after the window closed {_mmyyyy(to_ym)}; confirm the build month"
        if year == ty and tm < 12:
            return UNCLEAR, f"confirm the build month (window closes {_mmyyyy(to_ym)})"
    return PASS, f"registered {year}, inside the build window ({_window_text(from_ym, to_ym)})"


def split_model_codes(text: str) -> list[str] | None:
    """Chassis prefixes from a SEV model-code string, e.g. "TRH2XX, KDH2XX" -> ["TRH2", "KDH2"]."""
    tokens = []
    for word in re.split(r"[,;/ ]+", text or ""):
        word = word.strip().upper().rstrip("X#")
        if re.search(r"\d", word) and re.search(r"[A-Z]", word):
            tokens.append(word)
    return tokens or None


def _window_check(a: Approval, year: int | None) -> EligCheck:
    if a.build_from_ym and a.build_to_ym and a.build_from_ym > a.build_to_ym:
        return EligCheck("window", UNCLEAR, "SEV and MRE build windows do not overlap; confirm on ROVER")
    state, detail = window_state(year, a.build_from_ym, a.build_to_ym)
    return EligCheck("window", state, detail)


def _expiry_check(a: Approval, today: date) -> EligCheck:
    exp = a.expiry_date
    if not isinstance(exp, date):
        return EligCheck("expiry", INFO, "expiry date not known")
    if exp < today:
        return EligCheck("expiry", FAIL, f"expired {exp.isoformat()}")
    return EligCheck("expiry", PASS, f"valid until {exp.isoformat()}, {(exp - today).days} days left")


def _model_code_check(a: Approval, facts: VehicleFacts, ocr: dict | None) -> EligCheck:
    codes = split_model_codes(a.sev_model_codes)
    if codes is None:
        return EligCheck("model_code", INFO, f"model codes not machine-readable: {a.sev_model_codes}")
    chassis = (facts.chassis or "").strip().upper()
    ocr_code = str((ocr or {}).get("model_code") or "").strip().upper().rsplit("-", 1)[-1]
    if not chassis and not ocr_code:
        return EligCheck("model_code", INFO, "no chassis code on the lot")
    for code in codes:
        if (chassis and chassis.startswith(code)) or (ocr_code and ocr_code.startswith(code)):
            return EligCheck("model_code", PASS, f"{chassis or ocr_code} matches {code}")
    return EligCheck("model_code", UNCLEAR, f"confirm: {chassis or ocr_code} is not among {', '.join(codes)}")


def _source_age_check(a: Approval, today: date, stale_days: int) -> EligCheck | None:
    try:
        scraped = datetime.fromisoformat(a.last_scraped_at.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None
    age = (today - scraped).days
    if age > stale_days:
        return EligCheck("source_age", INFO, f"guide approval last scraped {age} days ago")
    return None


def _pair_checks(a: Approval, facts: VehicleFacts, ocr: dict | None, today: date, stale_days: int) -> list[EligCheck]:
    checks = [_window_check(a, facts.year), _expiry_check(a, today), _model_code_check(a, facts, ocr)]
    age = _source_age_check(a, today, stale_days)
    if age:
        checks.append(age)
    return checks


def _verdict(checks: list[EligCheck]) -> str:
    states = {c.state for c in checks}
    if FAIL in states:
        return CANNOT_IMPORT
    return CONFIRM if UNCLEAR in states else ELIGIBLE


def _pair_dict(a: Approval) -> dict:
    return {
        "label": a.label,
        "sev_number": a.sev_number,
        "mre_number": a.mre_number,
        "criterion": a.sev_criterion,
        "category": a.sev_category,
        "window": _window_text(a.build_from_ym, a.build_to_ym),
        "expiry": a.expiry_date.isoformat() if isinstance(a.expiry_date, date) else "",
        "scraped": a.last_scraped_at,
    }


def _text_entries(a: Approval, prefixed: bool = False) -> list[dict]:
    """The pair's guide text as reviewer text entries. Without a selected pair the ids and labels
    carry the pair label so several pairs can sit side by side."""
    labels = {"sev": f"{a.sev_number} {a.sev_criterion}".strip(), "mre": a.mre_number,
              "mre-conditions": a.mre_number}
    return [{"id": f"elig:{kind}" + (f":{a.label}" if prefixed else ""),
             "label": (f"{a.label} " if prefixed else "") + labels[kind], "text": text}
            for kind, text in a.texts()]


def _build_text(a: Approval) -> dict:
    start = _mmyyyy(a.build_from_ym) if a.build_from_ym else "the window start"
    return {"id": "elig:build", "label": "Build month", "text": BUILD_TEXT.format(start=start)}


def _hash(label: str, checks: list[EligCheck], texts: list[dict]) -> str:
    parts = [label]
    # source_age depends on today's date, so it never feeds the hash.
    parts += [f"{c.name}={c.state}" for c in checks if c.name != "source_age"]
    parts += [f"{t['id']}={t['text']}" for t in texts]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def check_lot(rule: ModelRule, variant: Variant | None, facts: VehicleFacts, ocr: dict | None,
              today: date, stale_days: int = 90) -> Eligibility | None:
    """Check the lot against the variant's selected approval pair (or every active pair when none
    is selected). None when the guide lists no approvals for it."""
    approvals = rule.approvals_for(variant)
    if not approvals:
        return None
    selected = next((a for a in approvals if a.selected), None)
    others = [a for a in approvals if a.active and a is not selected]
    alt_checks = [_pair_checks(a, facts, ocr, today, stale_days) for a in others]
    alternatives = [{"label": a.label, "window": _window_text(a.build_from_ym, a.build_to_ym),
                     "verdict": _verdict(c), "checks": [asdict(x) for x in c]}
                    for a, c in zip(others, alt_checks)]

    def window_unclear(checks: list[EligCheck]) -> bool:
        return any(c.name == "window" and c.state == UNCLEAR for c in checks)

    if selected is not None:
        checks = _pair_checks(selected, facts, ocr, today, stale_days)
        verdict = _verdict(checks)
        texts = _text_entries(selected)
        pair, label = _pair_dict(selected), selected.label
        build_for = selected if window_unclear(checks) else None
    else:
        checks = []
        verdicts = [alt["verdict"] for alt in alternatives]
        best = min(verdicts, key=[ELIGIBLE, CONFIRM, CANNOT_IMPORT].index) if verdicts else CONFIRM
        verdict = CONFIRM if best == ELIGIBLE else best
        texts = [t for a in others for t in _text_entries(a, prefixed=True)]
        pair, label = None, "none"
        build_for = next((a for a, c in zip(others, alt_checks) if window_unclear(c)), None)
    if build_for is not None:
        texts.append(_build_text(build_for))
    return Eligibility(verdict=verdict, pair=pair, checks=checks, texts=texts,
                       alternatives=alternatives, hash=_hash(label, checks, texts))
