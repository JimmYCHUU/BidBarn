"""Structured output schema for the auction-sheet / photo check."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["pass", "fail", "unknown"]
Tri = Literal["yes", "no", "unknown"]


class RuleVerdict(BaseModel):
    rule: str = Field(description="The rule text exactly as given")
    verdict: Verdict
    evidence: str = Field(description="What in the sheet or photos supports the verdict; say 'not visible' when unknown")


class SheetCheck(BaseModel):
    sheet_readable: bool = Field(description="False if the auction sheet image is missing, blank or unreadable")
    sheet_grade: str | None = Field(description="Overall grade printed on the sheet, e.g. '4', '3.5', 'R'")
    sheet_km: int | None = Field(description="Odometer reading printed on the sheet, in km")
    first_registration_year: int | None = Field(description="Western year of first registration from the sheet (convert R3 -> 2021, H30 -> 2018)")
    model_code_on_sheet: str | None = Field(description="Model code printed on the sheet, e.g. QDF-GDH206V")
    seats: int | None = Field(description="Seating capacity printed on the sheet (乗車定員)")
    drivetrain: Literal["4WD", "2WD", "unknown"]
    roof: Literal["high", "low", "unknown"] = Field(description="Roof height judged from photos")
    wheelbase: Literal["standard", "long", "super_long", "unknown"]
    sticker_paint: Tri = Field(description="Is the body sticker-wrapped / vinyl-wrapped rather than painted")
    seat_material: str | None = Field(description="e.g. 'cloth', 'leather', 'synthetic leather', or null if not visible")
    seat_colour: str | None
    captain_seats: Tri
    hybrid_or_ev: Tri
    notable_issues: list[str] = Field(description="Accident/repair marks, corrosion, warning notes, odometer doubts")
    rule_verdicts: list[RuleVerdict]
    confidence: Literal["high", "medium", "low"]
    notes: str
