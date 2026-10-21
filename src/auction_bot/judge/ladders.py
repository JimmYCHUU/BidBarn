"""Evidence ladders for cruise control and the Welcab front seat (Jim, 2026-10-20).

The order is fixed: the auction sheet first, then the photos, then the maker catalogue.
The sheet outranks the photos. A steering-wheel photo that is missing from a complete photo
set means "no". A catalogue entry of "optional" or "none" means "no"; only "standard"
confirms. A Welcab with no seat statement on the sheet, the photos or the catalogue fails.

The model only reports what it saw at each step (`Observation`). It never decides a ladder:
this module is the one place a ladder verdict is computed, from those observations and the
catalogue finding. It is pure; no network, no database. Jim still clicks Approve.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from auction_bot.judge.requirements import Requirement
from auction_bot.judge.topics import SEAT_FAIL, SEAT_PASS, TOPIC_WELCAB

CRUISE_SHEET = ("yes", "no", "not_mentioned")
CRUISE_PHOTO = ("yes", "no", "maybe", "not_visible")
WELCAB_SHEET = SEAT_PASS + SEAT_FAIL + ("welcab_unspecified", "not_welcab", "not_mentioned")
WELCAB_PHOTO = SEAT_PASS + SEAT_FAIL + ("welcab_unspecified", "not_welcab", "not_visible")

_NO_CRUISE_PHOTO = "no steering-wheel photo among the photos; some photos could not be downloaded"
_NO_SEAT_PHOTO = "no seat photo among the photos; some photos could not be downloaded"
_NO_SEATING = "no seating statement on the sheet, the photos or the catalogue"


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
    if not maker or not catalog_grade:
        return None
    wanted = catalog_grade.strip()
    for row in maker.get("grades") or []:
        if (row.get("name") or "").strip() == wanted:
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

def _read_step(obs: dict | None, which: str, allowed: tuple[str, ...], default: str) -> StepObs:
    """The step as the table reads it: an unknown or missing state becomes `default`."""
    raw: StepObs | None = None
    if isinstance(obs, dict) and isinstance(obs.get(which), dict):
        try:
            raw = StepObs.model_validate(obs[which])
        except Exception:
            raw = None
    if raw is None or not raw.state:
        return StepObs(state=default, evidence="no observation")
    if raw.state not in allowed:
        return StepObs(state=default, evidence=f"unknown state '{raw.state}'", photo_no=raw.photo_no)
    return raw


def _step_line(step: dict) -> str:
    if step["step"] == "maker":
        return step["evidence"] if step["evidence"] else f"maker: {step['state']}"
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
        {"step": "maker", "state": "not needed", "evidence": "maker: not needed", "photo_no": None,
         "grade": None, "mark": "", "url": "", "decided": False},
    ]


def _fill_maker(step: dict, maker: dict | None, status: str, grade: str | None, mark: str, row: dict | None,
                note: str = "") -> None:
    urls = (maker or {}).get("urls") or []
    step["state"] = status
    step["grade"] = grade
    step["mark"] = mark
    step["url"] = (row or {}).get("url") or (urls[0] if urls else "")
    line = f"maker: {status}"
    if grade:
        line += f" on {grade}"
    if mark:
        line += f" [{mark}]"
    if note:
        line += f" ({note})"
    step["evidence"] = line


def _finish(req: Requirement, steps: list[dict], verdict: str, decided: int) -> LadderVerdict:
    steps[decided]["decided"] = True
    if req.kind == "must" and req.negated:
        verdict = {"pass": "fail", "fail": "pass"}.get(verdict, verdict)
    return LadderVerdict(req.id, verdict, "; ".join(_step_line(s) for s in steps), steps)


# ---------------------------------------------------------------- the two ladders

def _cruise_state(step: StepObs, adaptive_kind: bool, weak: str) -> str:
    """A `yes` counts for adaptive cruise only when the model says it is adaptive; otherwise `weak`."""
    if adaptive_kind and step.state == "yes" and step.adaptive != "yes":
        return weak
    return step.state


def _resolve_cruise(req: Requirement, obs: dict | None, maker: dict | None, photos_complete: bool) -> LadderVerdict:
    adaptive_kind = req.topic_detail == "adaptive"
    kind = "adaptive" if adaptive_kind else "plain"
    sheet = _read_step(obs, "sheet", CRUISE_SHEET, "not_mentioned")
    photo = _read_step(obs, "photo", CRUISE_PHOTO, "not_visible")
    steps = _make_steps(sheet, photo)

    sheet_state = _cruise_state(sheet, adaptive_kind, "not_mentioned")
    if sheet_state == "yes":
        return _finish(req, steps, "pass", 0)
    if sheet_state == "no":
        return _finish(req, steps, "fail", 0)

    photo_state = _cruise_state(photo, adaptive_kind, "maybe")
    if photo_state == "yes":
        return _finish(req, steps, "pass", 1)
    if photo_state == "no":
        return _finish(req, steps, "fail", 1)
    if photo_state == "not_visible":
        if photos_complete:
            return _finish(req, steps, "fail", 1)
        steps[1]["evidence"] = _NO_CRUISE_PHOTO
        return _finish(req, steps, "unclear", 1)

    catalog_grade = (obs or {}).get("catalog_grade") if isinstance(obs, dict) else None
    status, grade, mark, row = _cruise_block(maker, kind, catalog_grade)
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
    sheet = _read_step(obs, "sheet", WELCAB_SHEET, "not_mentioned")
    photo = _read_step(obs, "photo", WELCAB_PHOTO, "not_visible")
    steps = _make_steps(sheet, photo)

    verdict = _seat_verdict(sheet.state)
    if verdict:
        return _finish(req, steps, verdict, 0)
    verdict = _seat_verdict(photo.state)
    if verdict:
        return _finish(req, steps, verdict, 1)

    catalog_grade = (obs or {}).get("catalog_grade") if isinstance(obs, dict) else None
    status, grade, row = _seat_block(maker, catalog_grade)
    verdict = _seat_verdict(status)
    if verdict:
        _fill_maker(steps[2], maker, status, grade, "", row)
        return _finish(req, steps, verdict, 2)
    if photo.state == "not_visible" and not photos_complete:
        steps[1]["evidence"] = _NO_SEAT_PHOTO
        return _finish(req, steps, "unclear", 1)
    _fill_maker(steps[2], maker, status, grade, "", row, note=_NO_SEATING)
    return _finish(req, steps, "fail", 2)


def resolve(req: Requirement, obs: dict | None, maker: dict | None, photos_complete: bool = True) -> LadderVerdict:
    if req.topic == TOPIC_WELCAB:
        return _resolve_welcab(req, obs, maker, photos_complete)
    return _resolve_cruise(req, obs, maker, photos_complete)


def chip_applies(req: Requirement, verdict: str) -> str:
    """Whether a chip applies, given the verdict on "has it". A negated chip ("no cruise control") inverts."""
    if verdict not in ("pass", "fail"):
        return "unclear"
    has_it = verdict == "pass"
    return "yes" if has_it != req.negated else "no"
