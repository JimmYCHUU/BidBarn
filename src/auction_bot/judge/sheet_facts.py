"""What Carbarn's sheet reader says, in the bot's own terms (pure: no I/O, the reply is never changed).

- `fields`: the sheet fields the rules use (seats, model code, odometer, grade, 4WD, engine size).
- `cruise_sheet_step` / `welcab_sheet_step`: the first step of Jim's ladders (2026-10-20), read from the reply:
  the same states `judge/ladders.py` takes from the model's observation, with the reply's wording as evidence.
  A bare "CC" is never cruise control. A sheet that says nothing about welfare is a silent sheet
  (`not_mentioned`), never "not a Welcab". Of several Welcab features a passing seat wins, because the seat is
  what Jim's rule asks for.
- `sheet_usable` / `welfare_usable`: False when the reader flagged the scan as hard to read, gave low
  confidence, failed, or found no text; the bot then keeps its own reading.
"""
from __future__ import annotations

import re

from auction_bot.judge.topics import CRUISE_WORDS

_CRUISE = re.compile(CRUISE_WORDS, re.I)
_ADAPTIVE = re.compile(r"(?i:radar|adaptive)|\bACC\b|レーダー|追従")
_BARE_CC = re.compile(r"^\s*[CＣ][CＣ]\s*$")
_4WD = re.compile(r"4\s*WD|４ＷＤ|\bAWD\b|四駆|E-?Four", re.I)

FEATURE_STATES = {
    "SWIVEL_SEAT": "front_swivel",
    "LIFT_UP_SEAT": "front_lift_up",
    "SIDE_LIFT_UP_SEAT": "second_row_lift_up",
    "WHEELCHAIR_SLOPE": "slope",
    "WHEELCHAIR_LIFT": "wheelchair_lift",
}
_PASSING = ("front_swivel", "front_lift_up", "second_row_lift_up")


def _sheet(reply: dict | None) -> dict:
    s = (reply or {}).get("sheet") if isinstance(reply, dict) else None
    return s if isinstance(s, dict) else {}


def _texts(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            if isinstance(v, dict):
                out += [str(v[k]) for k in ("ja", "en") if v.get(k)]
            elif v is not None and str(v).strip():
                out.append(str(v))
        return out
    return [str(value)] if str(value).strip() else []


def parse_seats(raw) -> int | None:
    """'5名', '8', 7, '10人' -> the first whole number from 1 to 15; anything else None."""
    m = re.search(r"\d+", str(raw)) if raw is not None else None
    n = int(m.group()) if m else None
    return n if n is not None and 1 <= n <= 15 else None


def parse_km(raw) -> int | None:
    """'54千km', '5.4万km', '54,000km', '54k', '54000' -> km. A bare number under 1,000 with no unit is ambiguous
    (thousands or km?) and gives None."""
    if raw is None:
        return None
    text = str(raw).replace(",", "").replace("，", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(万|千|[kK](?![mM]))?", text)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    if unit == "万":
        value *= 10_000
    elif unit in ("千", "k", "K"):
        value *= 1_000
    elif value < 1_000:
        return None
    return int(round(value))


def fields(reply: dict | None) -> dict:
    """The reply's sheet fields the rules use, or {} without a sheet part."""
    s = _sheet(reply)
    if not s:
        return {}
    code = str(s.get("model_code") or "").strip().upper() or None
    grade_line = " ".join(_texts(s.get("grade")) + _texts(s.get("model_name")))
    cc = s.get("displacement_cc")
    try:
        engine_cc = int(cc) if cc not in (None, "") else None
    except (TypeError, ValueError):
        engine_cc = None
    return {
        "seats": parse_seats(s.get("seats")),
        "model_code": code,
        "bare_model_code": code.rsplit("-", 1)[-1] if code else None,
        "km": parse_km(s.get("mileage_raw")),
        "grade": str(s.get("overall_grade")).strip() if s.get("overall_grade") not in (None, "") else None,
        "is_4wd": True if _4WD.search(grade_line) else None,  # absence on the sheet is not proof of 2WD
        "engine_cc": engine_cc,
    }


def cruise_sheet_step(reply: dict | None) -> dict:
    """{state: yes | not_mentioned, evidence, adaptive: yes | unknown} from the equipment, the selling points, the
    grade line and the inspector notes."""
    s = _sheet(reply)
    places = [("equipment", _texts(s.get("equipment"))), ("selling points", _texts(s.get("selling_points"))),
              ("grade line", _texts(s.get("grade"))), ("inspector notes", _texts(s.get("inspector_notes")))]
    for where, items in places:
        for item in items:
            if _CRUISE.search(item):
                adaptive = "yes" if _ADAPTIVE.search(item) else "unknown"
                return {"state": "yes", "evidence": f"Carbarn read, {where}: {item}", "adaptive": adaptive}
    if any(_BARE_CC.match(item) for _, items in places for item in items):
        return {"state": "not_mentioned", "adaptive": "unknown",
                "evidence": "Carbarn read: the sheet says CC, which is not read as cruise control (engine size or car stereo)"}
    return {"state": "not_mentioned", "adaptive": "unknown",
            "evidence": "Carbarn read: no cruise control in the equipment, selling points or notes"}


def welcab_sheet_step(reply: dict | None) -> dict | None:
    """{state, evidence} for the Welcab seat ladder, or None when the reader gave no welfare answer."""
    if not isinstance(reply, dict) or reply.get("welfareError"):
        return None
    w = reply.get("welfare")
    if not isinstance(w, dict):
        return None
    cited = "; ".join(" - ".join(p for p in (str(e.get("ja") or ""), str(e.get("en") or "")) if p)
                      for e in (w.get("evidence") or []) if isinstance(e, dict))
    sentence = str(w.get("sentence") or "").strip()
    evidence = "Carbarn read: " + ("; ".join(p for p in (sentence, cited) if p) or "no welfare wording")
    if not w.get("found"):
        return {"state": "not_mentioned", "evidence": "Carbarn read: no welfare equipment on the sheet"}
    states = [FEATURE_STATES[f] for f in (w.get("features") or []) if f in FEATURE_STATES]
    passing = [st for st in states if st in _PASSING]
    if passing:
        return {"state": passing[0], "evidence": evidence}
    if states:
        return {"state": states[0], "evidence": evidence}
    return {"state": "welcab_unspecified", "evidence": evidence}


def sheet_usable(reply: dict | None) -> bool:
    return isinstance(reply, dict) and not reply.get("sheetError") and bool(_sheet(reply)) and not _sheet(reply).get("low_confidence")


def welfare_usable(reply: dict | None) -> bool:
    if not isinstance(reply, dict) or reply.get("welfareError"):
        return False
    w = reply.get("welfare")
    return isinstance(w, dict) and str(w.get("confidence") or "").lower() != "low" and w.get("sheetText") is not False
