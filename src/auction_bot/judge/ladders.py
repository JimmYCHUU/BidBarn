"""Evidence ladders for cruise control and the Welcab front seat (Jim, 2026-10-20).

The order is fixed: the auction sheet first, then the photos, then the maker catalogue.
The sheet outranks the photos. A steering-wheel photo that is missing from a complete photo
set means "no". A catalogue entry of "optional" or "none" means "no"; only "standard"
confirms. A Welcab with no seat statement on the sheet, the photos or the catalogue fails.
When the model gave no observation at all, the verdict is unclear, never a silent "no".

The model only reports what it saw at each step (`Observation`). It never decides a ladder:
this module is the one place a ladder verdict is computed, from those observations and the
catalogue finding. It is pure; no network, no database. Jim still clicks Approve.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

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


class StepObs(BaseModel):
    state: str = ""
    evidence: str = ""
    photo_no: int | None = None
    adaptive: Literal["yes", "no", "unknown"] = "unknown"


class Observation(BaseModel):
    id: str
    sheet: StepObs = Field(default_factory=StepObs)
    photo: StepObs = Field(default_factory=StepObs)
    catalog_grade: str | None = None


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
    return plain


def maker_cruise(maker: dict | None, kind: str, catalog_grade: str | None) -> tuple[str, dict | None]:
    status, _grade, _mark, row = _cruise_block(maker, kind, catalog_grade)
    return status, row


def maker_seat(maker: dict | None, catalog_grade: str | None) -> tuple[str, dict | None]:
    status, _grade, row = _seat_block(maker, catalog_grade)
    return status, row


def _seat_block(maker: dict | None, catalog_grade: str | None) -> tuple[str, str | None, dict | None]:
    if not maker:
        return "unknown", None, None
    block = maker.get("welcab") or {}
    status = block.get("seat_system") or "unknown"
    if status == "mixed":
        row = _grade_row(maker, catalog_grade)
        if row is not None:
            return row.get("seat_system") or "unknown", row.get("name"), row
    return status, block.get("grade"), None


# ---------------------------------------------------------------- one step's reading

def _read_step(obs: dict | None, which: str, allowed: tuple[str, ...], default: str) -> tuple[StepObs, bool]:
    """The step as the table reads it, and whether the model gave no state at all.

    Side fields are coerced one by one; a bad one never discards the step. An unknown state
    reads as `default` with "unknown state 'x'" as its evidence.
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
        return StepObs(state=default, evidence=_NO_OBSERVATION, photo_no=photo_no), True
    if state not in allowed:
        return StepObs(state=default, evidence=f"unknown state '{state}'", photo_no=photo_no), False
    return StepObs(state=state, evidence=evidence, photo_no=photo_no, adaptive=adaptive), False


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
    """The photo step could not be judged because photos are missing; keep any unknown-state note."""
    old = step["evidence"]
    step["evidence"] = f"{text}; {old}" if old.startswith("unknown state") else text


def _finish(req: Requirement, steps: list[dict], verdict: str, decided: int) -> LadderVerdict:
    steps[decided]["decided"] = True
    if req.kind == "must" and req.negated:
        verdict = {"pass": "fail", "fail": "pass"}.get(verdict, verdict)
    return LadderVerdict(req.id, verdict, "; ".join(_step_line(s) for s in steps), steps)


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
    sheet, _sheet_missing = _read_step(obs, "sheet", CRUISE_SHEET, "not_mentioned")
    photo, photo_missing = _read_step(obs, "photo", CRUISE_PHOTO, "not_visible")
    if adaptive_kind:
        sheet = _adaptive_reading(sheet, "not_mentioned")
        photo = _adaptive_reading(photo, "maybe")
    steps = _make_steps(sheet, photo)

    if sheet.state == "yes":
        return _finish(req, steps, "pass", 0)
    if sheet.state == "no":
        return _finish(req, steps, "fail", 0)

    if photo_missing:
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
    sheet, _sheet_missing = _read_step(obs, "sheet", WELCAB_SHEET, "not_mentioned")
    photo, photo_missing = _read_step(obs, "photo", WELCAB_PHOTO, "not_visible")
    steps = _make_steps(sheet, photo)

    verdict = _seat_verdict(sheet.state)
    if verdict:
        return _finish(req, steps, verdict, 0)
    if photo_missing:
        return _finish(req, steps, "unclear", 1)
    verdict = _seat_verdict(photo.state)
    if verdict:
        return _finish(req, steps, verdict, 1)

    status, grade, row = _seat_block(maker, _catalog_grade(obs))
    verdict = _seat_verdict(status)
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
