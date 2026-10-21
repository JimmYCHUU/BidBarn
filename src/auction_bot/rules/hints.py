"""Keyword hints for the human reviewer. No model involved.

They scan the listing variant text, equipment string and Nichibo's English
translation of the auction sheet for words that usually decide a text rule
(wheelchair conversions, seat counts, drivetrain, roof, sticker wrap, hybrid).
Hints never decide anything; they are shown next to the checklist.
"""
from __future__ import annotations

import re

from auction_bot.nichibo.facts import VehicleFacts

_PATTERNS: list[tuple] = [
    # (regex, level, message[, source prefixes the pattern applies to; default all])
    (r"welcab|well ?cab|wel+fare|wheel ?chair|swivel|rotat|lift[- ]?up|side lift|slope|ramp|ウェルキャブ|福祉|車いす|車椅子|クルマイス|"
     r"回転シート|リフトアップ|スロープ", "warn", "wheelchair / Welcab conversion mentioned"),
    (r"cruise|クルーズ|クルコン|追従", "info", "cruise control mentioned"),
    (r"\bcc\b", "info", "the text says CC, which may be cruise control or a car stereo; the steering-wheel photo decides",
     ("equipment", "translation:")),
    (r"\b(7|8|9|10|11|12|13|14)\s*(seat|seater|人|名)", "warn", "a seat count of 7 or more is mentioned"),
    (r"\b(2|3|5|6)\s*(seat|seater|人|名)", "info", "a seat count of 2/3/5/6 is mentioned"),
    (r"\b4\s*wd\b|4x4|awd|四駆", "info", "4WD mentioned"),
    (r"\b2\s*wd\b|\bfr\b|\bff\b|2駆", "warn", "2WD mentioned"),
    (r"high ?roof|ハイルーフ|super ?long|スーパーロング", "info", "high roof / super long body mentioned"),
    (r"sticker|wrap|ラッピング|ステッカー|decal", "warn", "sticker / wrap mentioned"),
    (r"hybrid|phev|\bev\b|electric|ハイブリッド", "info", "hybrid / EV mentioned"),
    (r"leather|革|レザー", "info", "leather mentioned"),
    (r"captain|キャプテン", "info", "captain seats mentioned"),
    (r"repair|accident|修復|frame|corros|rust|錆|flood|water", "warn", "repair / accident / corrosion wording present"),
    (r"\bR\b(?!\w)|\bRA\b|grade\s*r", "warn", "an R grade marker appears in the text"),
    (r"km\s*(unknown|\?|doubt)|走行不明|メーター交換|meter (change|replace)", "warn", "odometer doubt mentioned"),
]


def keyword_hints(facts: VehicleFacts, rule_texts: list[str] | None = None) -> list[dict]:
    """Return [{"level": "warn"|"info", "message": str, "where": str}]."""
    sources: list[tuple[str, str]] = [
        ("variant", facts.variant_text or ""),
        ("equipment", facts.equipment or ""),
    ]
    for key, value in (facts.translator or {}).items():
        if key in ("sheet_url", "error") or not isinstance(value, str):
            continue
        sources.append((f"translation:{key}", value))
    hints: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for where, text in sources:
        low = text.lower()
        for pattern, level, message, *only in _PATTERNS:
            if only and not where.startswith(only[0]):
                continue
            if re.search(pattern, low, re.I) and (message, where) not in seen:
                seen.add((message, where))
                hints.append({"level": level, "message": message, "where": where})
    rules_blob = " ".join(rule_texts or []).lower()
    if "4wd" in rules_blob and not any(h["message"].startswith("4WD") for h in hints):
        hints.append({"level": "warn", "message": "rule needs 4WD but nothing mentions it; check the sheet", "where": "-"})
    return hints
