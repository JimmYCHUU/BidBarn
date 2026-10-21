"""Evidence ladders for cruise control and the Welcab front seat (Jim, 2026-10-20).

The order is fixed: the auction sheet first, then the photos, then the maker catalogue.
The sheet outranks the photos. A steering-wheel photo that is missing from a complete photo
set means "no". A catalogue entry of "optional" or "none" means "no"; only "standard"
confirms. A Welcab with no seat statement on the sheet, the photos or the catalogue fails.
The catalogue alone never makes a lot a Welcab: its seat system counts only for a lot the
sheet or the photos call a Welcab, or for the catalogue grade the model names.
When the model gave no observation at all, gave no sheet reading, or gave a state outside the
table, the verdict is unclear, never a silent "no". Only a cruise note is read as negated.

The model only reports what it saw at each step (`Observation`). It never decides a ladder:
this module is the one place a ladder verdict is computed, from those observations and the
catalogue finding. It is pure; no network, no database. Jim still clicks Approve.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from auction_bot.judge.requirements import Requirement
from auction_bot.judge.topics import SEAT_FAIL, SEAT_PASS, TOPIC_CRUISE, TOPIC_WELCAB

CRUISE_SHEET = ("yes", "no", "not_mentioned")
CRUISE_PHOTO = ("yes", "no", "maybe", "not_visible")
WELCAB_SHEET = SEAT_PASS + SEAT_FAIL + ("welcab_unspecified", "not_welcab", "not_mentioned")
WELCAB_PHOTO = SEAT_PASS + SEAT_FAIL + ("welcab_unspecified", "not_welcab", "not_visible")

_NO_CRUISE_PHOTO = "no steering-wheel photo among the photos; some photos could not be downloaded"
_NO_SEAT_PHOTO = "no seat photo among the photos; some photos could not be downloaded"
_NO_SEATING = "no seating statement on the sheet, the photos or the catalogue"
_NO_OBSERVATION = "model gave no observation"
_NO_SHEET = "model gave no sheet reading"


class StepObs(BaseModel):
    state: str = ""
    evidence: str = ""
    photo_no: int | None = None
    adaptive: Literal["yes", "no", "unknown"] = "unknown"

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data):
        if not isinstance(data, dict):
            return {}
        d = dict(data)
        for key in ("state", "evidence"):
            if key in d and not isinstance(d[key], str):
                d[key] = ""
        if "adaptive" in d and d["adaptive"] not in ("yes", "no", "unknown"):
            d["adaptive"] = "unknown"
        p = d.get("photo_no")
        if "photo_no" in d and (not isinstance(p, int) or isinstance(p, bool)):
            d["photo_no"] = None
        return d


class Observation(BaseModel):
    id: str
    sheet: StepObs = Field(default_factory=StepObs)
    photo: StepObs = Field(default_factory=StepObs)
    catalog_grade: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for key in ("sheet", "photo"):
            if key in d and not isinstance(d[key], dict):
                d[key] = {}
        if "catalog_grade" in d and not isinstance(d["catalog_grade"], str):
            d["catalog_grade"] = None
        return d


@dataclass
class LadderVerdict:
    id: str
    verdict: str  # pass | fail | unclear
    evidence: str
    steps: list[dict]

    def to_dict(self) -> dict:
        return {"id": self.id, "verdict": self.verdict, "evidence": self.evidence, "steps": self.steps}


# ---------------------------------------------------------------- the catalogue

def _grade_row(maker: dict | None, catalog_grade: str | None) -> dict | None:
    if not maker or not isinstance(catalog_grade, str) or not catalog_grade.strip():
        return None
    wanted = catalog_grade.strip()
    for row in maker.get("grades") or []:
        name = row.get("name")
        if isinstance(name, str) and name.strip() == wanted:
            return row
    return None


def _cruise_block(maker: dict | None, kind: str, catalog_grade: str | None) -> tuple[str, str | None, str, dict | None]:
    """(status, grade, mark, row) for one kind of cruise."""
    if not maker:
        return "unknown", None, "", None

    def one(which: str) -> tuple[str, str | None, str, dict | None]:
        block = maker.get(which) or {}
        status = block.get("status") or "unknown"
        if status == "mixed":
            row = _grade_row(maker, catalog_grade)
            if row is not None:
                return row.get(which) or "unknown", row.get("name"), row.get(f"{which}_mark") or "", row
        return status, block.get("grade"), block.get("mark") or "", None

    if kind == "adaptive":
        return one("adaptive")
    plain = one("cruise")
    if plain[0] == "standard" or (plain[0] == "mixed" and plain[3] is None):
        return plain  # decided, or the catalogue cannot say which grade this is
    row = plain[3]
    adaptive = ((row.get("adaptive") or "unknown"), row.get("name"), row.get("adaptive_mark") or "", row) if row else one("adaptive")
    if adaptive[0] == "standard":  # adaptive cruise is also cruise control
        return adaptive
    if adaptive[0] == "mixed" and plain[0] in ("none", "optional"):
        return adaptive  # some grades carry adaptive cruise and the lot's grade is not named: the catalogue cannot say
    return plain


def maker_cruise(maker: dict | None, kind: str, catalog_grade: str | None) -> tuple[str, dict | None]:
    status, _grade, _mark, row = _cruise_block(maker, kind, catalog_grade)
    return status, row


def maker_seat(maker: dict | None, catalog_grade: str | None, known_welcab: bool = False) -> tuple[str, dict | None]:
    _verdict, status, _grade, row = _seat_block(maker, catalog_grade, known_welcab)
    return status, row


def _welcab_systems(maker: dict) -> list[str]:
    """The distinct Welcab seat systems the catalogue sells for the lot's model code. A finding stored before
    `welcab_systems` existed is read from its grade rows (or, with none, from its single seat system)."""
    block = maker.get("welcab") or {}
    stored = block.get("welcab_systems")
    if isinstance(stored, list):
        return [s for s in stored if isinstance(s, str) and s != "not_welcab"]
    out: list[str] = []
    for g in maker.get("grades") or []:
        s = g.get("seat_system") if isinstance(g, dict) else None
        if isinstance(s, str) and s != "not_welcab" and s not in out:
            out.append(s)
    if not out and not maker.get("grades") and block.get("seat_system") in SEAT_PASS + SEAT_FAIL + ("welcab_unspecified",):
        out.append(block["seat_system"])
    return out


def _seat_block(maker: dict | None, catalog_grade: str | None, known_welcab: bool
                ) -> tuple[str | None, str, str | None, dict | None]:
    """The Welcab maker step: (verdict, status, grade, row). Verdict None means no seating statement.

    The catalogue alone never makes a lot a Welcab. For a lot the sheet or the photos call a Welcab
    (`known_welcab`), a catalogue that sells one Welcab seat system decides by it. Otherwise (several systems,
    or nothing says this car is a Welcab) only a catalogue grade the model named that is a Welcab row decides,
    by its seat system; a named plain grade or no named grade gives no seating statement.
    """
    if not maker:
        return None, "unknown", None, None
    block = maker.get("welcab") or {}
    status = block.get("seat_system") or "unknown"
    systems = _welcab_systems(maker)
    if known_welcab and len(systems) == 1:
        system = systems[0]
        row = next((g for g in maker.get("grades") or [] if isinstance(g, dict) and g.get("seat_system") == system), None)
        grade = (row or {}).get("name") or (block.get("grade") if status == system else None)
        return _seat_verdict(system), system, grade, row
    row = _grade_row(maker, catalog_grade)
    if row is None:
        return None, status, None, None
    named = row.get("seat_system") or "unknown"
    if named in ("not_welcab", "unknown"):
        return None, named, row.get("name"), row  # a plain grade: the catalogue says nothing about a seat
    return _seat_verdict(named), named, row.get("name"), row


# ---------------------------------------------------------------- one step's reading

def _read_step(obs: dict | None, which: str, allowed: tuple[str, ...], default: str) -> tuple[StepObs, str]:
    """The step as the table reads it, and how it was read: "ok", "missing" (no state at all; it shows as
    `default`) or "unknown" (a state outside the table; it shows as given, with "unknown state 'x'" as its
    evidence). A missing or unknown step reads unclear wherever the ladder consults it.

    Side fields are coerced one by one; a bad one never discards the step.
    """
    raw = obs.get(which) if isinstance(obs, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    state = raw.get("state")
    state = state.strip() if isinstance(state, str) else ""
    evidence = raw.get("evidence")
    evidence = evidence if isinstance(evidence, str) else ""
    photo_no = raw.get("photo_no")
    photo_no = photo_no if isinstance(photo_no, int) and not isinstance(photo_no, bool) else None
    adaptive = raw.get("adaptive")
    adaptive = adaptive if adaptive in ("yes", "no", "unknown") else "unknown"
    if not state:
        return StepObs(state=default, evidence=_NO_OBSERVATION, photo_no=photo_no), "missing"
    if state not in allowed:
        shown = state[:40]
        return StepObs(state=shown, evidence=f"unknown state '{shown}'", photo_no=photo_no), "unknown"
    return StepObs(state=state, evidence=evidence, photo_no=photo_no, adaptive=adaptive), "ok"


def _step_line(step: dict) -> str:
    if step["step"] == "maker":
        return f"maker: {step['evidence']}" if step["evidence"] else f"maker: {step['state']}"
    name = step["step"]
    if name == "photo" and step["photo_no"] is not None:
        name = f"photo {step['photo_no']}"
    line = f"{name}: {step['state']}"
    return f"{line} ({step['evidence']})" if step["evidence"] else line


def _make_steps(sheet: StepObs, photo: StepObs) -> list[dict]:
    return [
        {"step": "sheet", "state": sheet.state, "evidence": sheet.evidence, "photo_no": None,
         "grade": None, "mark": "", "url": "", "decided": False},
        {"step": "photo", "state": photo.state, "evidence": photo.evidence, "photo_no": photo.photo_no,
         "grade": None, "mark": "", "url": "", "decided": False},
        {"step": "maker", "state": "not needed", "evidence": "not needed", "photo_no": None,
         "grade": None, "mark": "", "url": "", "decided": False},
    ]


def _fill_maker(step: dict, maker: dict | None, status: str, grade: str | None, mark: str, row: dict | None,
                note: str = "") -> None:
    urls = (maker or {}).get("urls") or []
    step["state"] = status
    step["grade"] = grade
    step["mark"] = mark
    step["url"] = (row or {}).get("url") or (urls[0] if urls else "")
    text = status
    if grade:
        text += f" on {grade}"
    if mark:
        text += f" [{mark}]"
    if note:
        text += f" ({note})"
    step["evidence"] = text


def _short_set(step: dict, text: str) -> None:
    """The photo step could not be judged because photos are missing."""
    step["evidence"] = text


def _finish(req: Requirement, steps: list[dict], verdict: str, decided: int) -> LadderVerdict:
    steps[decided]["decided"] = True
    if req.kind == "must" and req.negated and req.topic == TOPIC_CRUISE:  # a Welcab verdict is never inverted
        verdict = {"pass": "fail", "fail": "pass"}.get(verdict, verdict)
    return LadderVerdict(req.id, verdict, "; ".join(_step_line(s) for s in steps), steps)


def _unreadable(req: Requirement, steps: list[dict], sheet_how: str, photo_how: str) -> LadderVerdict | None:
    """Unclear when the sheet step cannot be read: the sheet outranks the photo, so a photo never stands in for it."""
    if sheet_how == "missing":
        if photo_how != "missing":
            steps[0]["evidence"] = _NO_SHEET
        return _finish(req, steps, "unclear", 0)
    if sheet_how == "unknown":
        return _finish(req, steps, "unclear", 0)
    return None


def _catalog_grade(obs: dict | None) -> str | None:
    grade = obs.get("catalog_grade") if isinstance(obs, dict) else None
    return grade if isinstance(grade, str) else None


# ---------------------------------------------------------------- the two ladders

def _adaptive_reading(step: StepObs, weak: str) -> StepObs:
    """Adaptive kind: a `yes` counts only when the model says it is adaptive.

    `adaptive == "no"` makes it a plain `no`; `unknown` makes it the weaker reading `weak`.
    The evidence shows the downgrade.
    """
    if step.state != "yes" or step.adaptive == "yes":
        return step
    if step.adaptive == "no":
        state, note = "no", "yes (not adaptive)"
    else:
        state, note = weak, "yes (adaptive unknown)"
    return StepObs(state=state, evidence=f"{note}: {step.evidence}" if step.evidence else note,
                   photo_no=step.photo_no, adaptive=step.adaptive)


def _resolve_cruise(req: Requirement, obs: dict | None, maker: dict | None, photos_complete: bool) -> LadderVerdict:
    adaptive_kind = req.topic_detail == "adaptive"
    kind = "adaptive" if adaptive_kind else "plain"
    sheet, sheet_how = _read_step(obs, "sheet", CRUISE_SHEET, "not_mentioned")
    photo, photo_how = _read_step(obs, "photo", CRUISE_PHOTO, "not_visible")
    if adaptive_kind:
        sheet = _adaptive_reading(sheet, "not_mentioned")
        photo = _adaptive_reading(photo, "maybe")
    steps = _make_steps(sheet, photo)

    unreadable = _unreadable(req, steps, sheet_how, photo_how)
    if unreadable is not None:
        return unreadable
    if sheet.state == "yes":
        return _finish(req, steps, "pass", 0)
    if sheet.state == "no":
        return _finish(req, steps, "fail", 0)

    if photo_how != "ok":
        return _finish(req, steps, "unclear", 1)
    if photo.state == "yes":
        return _finish(req, steps, "pass", 1)
    if photo.state == "no":
        return _finish(req, steps, "fail", 1)
    if photo.state == "not_visible":
        if photos_complete:
            return _finish(req, steps, "fail", 1)
        _short_set(steps[1], _NO_CRUISE_PHOTO)
        return _finish(req, steps, "unclear", 1)

    status, grade, mark, row = _cruise_block(maker, kind, _catalog_grade(obs))
    _fill_maker(steps[2], maker, status, grade, mark, row)
    if status == "standard":
        verdict = "pass"
    elif status in ("optional", "none"):
        verdict = "fail"
    else:
        verdict = "unclear"
    return _finish(req, steps, verdict, 2)


def _seat_verdict(state: str) -> str | None:
    if state in SEAT_PASS:
        return "pass"
    if state in SEAT_FAIL or state == "not_welcab":
        return "fail"
    return None


def _resolve_welcab(req: Requirement, obs: dict | None, maker: dict | None, photos_complete: bool) -> LadderVerdict:
    sheet, sheet_how = _read_step(obs, "sheet", WELCAB_SHEET, "not_mentioned")
    photo, photo_how = _read_step(obs, "photo", WELCAB_PHOTO, "not_visible")
    steps = _make_steps(sheet, photo)

    unreadable = _unreadable(req, steps, sheet_how, photo_how)
    if unreadable is not None:
        return unreadable
    verdict = _seat_verdict(sheet.state)
    if verdict:
        return _finish(req, steps, verdict, 0)
    if photo_how != "ok":
        return _finish(req, steps, "unclear", 1)
    verdict = _seat_verdict(photo.state)
    if verdict:
        return _finish(req, steps, verdict, 1)

    # Only welcab_unspecified / not_mentioned (sheet) and welcab_unspecified / not_visible (photo) reach the catalogue.
    known_welcab = "welcab_unspecified" in (sheet.state, photo.state)
    verdict, status, grade, row = _seat_block(maker, _catalog_grade(obs), known_welcab)
    if verdict:
        _fill_maker(steps[2], maker, status, grade, "", row)
        return _finish(req, steps, verdict, 2)
    if photo.state in ("not_visible", "welcab_unspecified") and not photos_complete:
        _fill_maker(steps[2], maker, status, grade, "", row)
        _short_set(steps[1], _NO_SEAT_PHOTO)
        return _finish(req, steps, "unclear", 1)
    _fill_maker(steps[2], maker, status, grade, "", row, note=_NO_SEATING)
    return _finish(req, steps, "fail", 2)


def resolve(req: Requirement, obs: dict | None, maker: dict | None, photos_complete: bool = True) -> LadderVerdict:
    if req.topic == TOPIC_WELCAB:
        return _resolve_welcab(req, obs, maker, photos_complete)
    if req.topic == TOPIC_CRUISE:
        return _resolve_cruise(req, obs, maker, photos_complete)
    raise ValueError(f"no ladder for topic {req.topic!r} (requirement {req.id})")


def chip_applies(req: Requirement, verdict: str) -> str:
    """Whether a chip applies, given the verdict on "has it". A negated chip ("no cruise control") inverts."""
    if verdict not in ("pass", "fail"):
        return "unclear"
    has_it = verdict == "pass"
    return "yes" if has_it != req.negated else "no"
