"""Structured facts about one Nichibo auction vehicle."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


@dataclass
class VehicleFacts:
    ref_no: str
    url: str
    year: int | None = None
    make: str = ""
    model: str = ""
    variant_text: str = ""
    chassis: str = ""
    km: int | None = None
    grade_raw: str = ""
    start_price_jpy: int | None = None
    transmission: str = ""
    colour: str = ""
    engine_cc: int | None = None
    auction_place: str = ""
    exhibit_no: str = ""
    auction_date: str = ""
    equipment: str = ""
    vid: str = ""
    photo_urls: list[str] = field(default_factory=list)
    sheet_url: str = ""
    chassis_uncertain: bool = False  # Nichibo showed the code with a trailing "?" (e.g. GDH206K?)
    has_current_bid: bool = False
    current_bid_jpy: int | None = None
    images_missing: bool = False
    translator: dict | None = None
    seats: int | None = None  # filled from the auction-sheet check (OCR or model)
    sheet_ocr: dict | None = None  # SheetRead.to_dict() when the sheet was OCR-read
    source: str = "detail"  # "card" when only search-card data is known
    # The lot's own page was read for its auction day (Jim, 2026-10-26), even when it showed none. Kept through
    # every rewrite of the stored facts (replay, the pending judge pass) so that page is not read again.
    auction_day_read: bool = False

    @property
    def grade(self) -> float | None:
        return parse_grade(self.grade_raw)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["grade"] = self.grade
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "VehicleFacts":
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**d)


_GRADE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def parse_grade(raw: str | None) -> float | None:
    """Numeric auction grade, or None for 0 / R / RA / S / blank / anything else."""
    if raw is None:
        return None
    m = _GRADE_RE.match(str(raw))
    if not m:
        return None
    value = float(m.group(1))
    if value <= 0:
        return None
    return value


def parse_int(text: str | None) -> int | None:
    if text is None:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None
