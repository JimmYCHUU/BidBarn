"""Topics that need an evidence ladder: cruise control and the Welcab front-seat system.

A guide text rule that names one of these is routed to the ladder (`source="ladder"`) instead of
the generic photo check. The words below only route and label; nothing here decides a bid.
Jim reads the evidence and clicks Approve.
"""
from __future__ import annotations

import re

from auction_bot.carbarn.models import Approval, ModelRule

TOPIC_CRUISE = "cruise_control"
TOPIC_WELCAB = "welcab_seat"
WELCAB_GUIDE_TEXT = "Welcab guide: the front passenger seat must be a swivel/rotating or lift-up seat (Jim, 2026-10-20)"
SEAT_PASS = ("front_swivel", "front_lift_up", "second_row_lift_up")
SEAT_FAIL = ("slope", "wheelchair_lift")

CRUISE_WORDS = r"cruise(?!r)|クルーズ|クルコン|追従"
# A bare "lift up" / リフトアップ is a lifted 4WD, not a seat: topic detection and the Welcab hint need a seat word.
# `seat_system` below keeps the bare forms, for catalogue grade names and approval sentences.
WELCAB_WORDS = (r"welcab|well ?cab|wel+fare|wheel ?chair|swivel|rotating (?:passenger |front )?seat|"
                r"lift[- ]?up ?(?:seat|シート)|side lift|slope|ramp|"
                r"ウェルキャブ|福祉|車いす|車椅子|クルマイス|回転シート|助手席回転|助手席リフトアップ|サイドリフトアップ|リフトアップシート|スロープ")
_CRUISE = re.compile(CRUISE_WORDS, re.I)
_WELCAB = re.compile(WELCAB_WORDS, re.I)
_ADAPTIVE = re.compile(r"(?i:radar|adaptive)|\bACC\b|追従")
_NEGATED = re.compile(r"^\s*(?:no|not|without)\s", re.I)

_SIDE_LIFT = re.compile(r"side lift|サイドリフト", re.I)
_SWIVEL = re.compile(r"助手席回転|回転シート|swivel|rotating (?:passenger |front )?seat", re.I)
_LIFT_UP = re.compile(r"リフトアップ|lift[- ]?up", re.I)
_WHEELCHAIR_LIFT = re.compile(r"wheelchair lift|車いすリフト|rear lift|リヤリフト|リアリフト", re.I)
_SLOPE = re.compile(r"slope|ramp|スロープ|車いす仕様|車椅子仕様|wheelchair (?:spec|type)|クルマイス", re.I)


def detect_topic(text: str) -> str | None:
    t = text or ""
    if _CRUISE.search(t):
        return TOPIC_CRUISE
    if _WELCAB.search(t):
        return TOPIC_WELCAB
    return None


def is_negated(text: str) -> bool:
    """Whether a note starts with no/not/without. Only cruise notes are read as negated; a negated Welcab note is
    either the seat requirement itself or no ladder topic at all (`judge.requirements.ladder_topic`)."""
    return bool(_NEGATED.match(text or ""))


def cruise_kind(text: str) -> str:
    return "adaptive" if _ADAPTIVE.search(text or "") else "plain"


def welcab_guide(rule: ModelRule) -> bool:
    return "welcab" in (rule.model or "").lower()


def seat_system(text: str) -> str:
    t = text or ""
    if _SIDE_LIFT.search(t):
        return "second_row_lift_up"
    if _SWIVEL.search(t):
        return "front_swivel"
    if _LIFT_UP.search(t):
        return "front_lift_up"
    if _WHEELCHAIR_LIFT.search(t):
        return "wheelchair_lift"
    if _SLOPE.search(t):
        return "slope"
    if _WELCAB.search(t):
        return "welcab_unspecified"
    return "not_welcab"


def approval_seat_sentences(approvals: list[Approval]) -> list[str]:
    """Seat-related sentences from each active pair's text, the selected pair first."""
    active = [a for a in approvals if a.active]
    ordered = [a for a in active if a.selected] + [a for a in active if not a.selected]
    out: list[str] = []
    for a in ordered:
        for _kind, text in a.texts():
            for part in text.split(". "):
                for sentence in part.split("。"):
                    s = sentence.strip()
                    if s and seat_system(s) != "not_welcab":
                        line = f"{a.label}: {s}"
                        if line not in out:
                            out.append(line)
    return out
