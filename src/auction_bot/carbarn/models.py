"""Typed view of a Carbarn bidding guide, built from the /tree API response."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OdoBand:
    odo_from: int
    odo_to: int  # upper bound inclusive

    def contains(self, km: int) -> bool:
        if km == 0 and self.odo_from == 0:
            return True
        return self.odo_from < km <= self.odo_to

    @property
    def label(self) -> str:
        return f"{self.odo_from // 1000}K-{self.odo_to // 1000}K"


@dataclass
class GridCell:
    year: int
    band: OdoBand
    bid_jpy_k: int | None
    no_bid: bool = False
    reason: str | None = None


@dataclass
class Condition:
    attr: str
    op: str
    value: str
    values: list[str] = field(default_factory=list)  # for "in" conditions

    @property
    def all_values(self) -> list[str]:
        if self.values:
            return [str(v) for v in self.values]
        return [v.strip() for v in str(self.value).replace(";", ",").split(",") if v.strip()]


@dataclass
class Modifier:
    id: int
    variant_id: int | None
    year_from: int | None
    year_to: int | None
    odo_from: int | None
    odo_to: int | None
    conditions: list[Condition]
    amount_jpy_k: int | None
    percent: float | None
    no_bid: bool
    strict: bool
    note: str
    active: bool
    stale: bool

    def applies_to_year_odo(self, year: int, km: int) -> bool:
        if self.year_from is not None and year < self.year_from:
            return False
        if self.year_to is not None and year > self.year_to:
            return False
        if self.odo_from is not None and km < self.odo_from:
            return False
        if self.odo_to is not None and km > self.odo_to:
            return False
        return True

    @property
    def is_toggle(self) -> bool:
        """A chip the reviewer applies by hand ('No Pro Pilot -220', 'Autech +170'): an amount
        with no condition the bot could evaluate. Never applied automatically."""
        return bool(self.amount_jpy_k) and not self.conditions and not self.no_bid and not self.percent

    def describe(self) -> str:
        parts = []
        if self.no_bid:
            parts.append("no bid")
        if self.amount_jpy_k:
            parts.append(f"{self.amount_jpy_k:+d}K")
        if self.percent:
            parts.append(f"x{self.percent}%")
        if self.conditions:
            parts.append(" and ".join(f"{c.attr} {c.op} {', '.join(c.all_values)}" for c in self.conditions))
        if self.year_from or self.year_to:
            parts.append(f"years {self.year_from}-{self.year_to}")
        if self.note:
            parts.append(self.note)
        return ", ".join(parts) or f"modifier {self.id}"


@dataclass
class Variant:
    id: int
    name: str  # "" for the unnamed default variant that covers every chassis of the model code
    active: bool  # the dashboard's currently selected tab, NOT a bidding switch (Jim, 2026-09-14)
    bands: list[OdoBand]
    cells: dict[tuple[int, int, int], GridCell] = field(default_factory=dict)
    modifiers: list[Modifier] = field(default_factory=list)
    eligible_years: set[int] = field(default_factory=set)

    @property
    def is_default(self) -> bool:
        return not self.name.strip()

    @property
    def label(self) -> str:
        return self.name.strip() or "Default"

    def _live_cells(self) -> list[GridCell]:
        return [c for c in self.cells.values() if c.bid_jpy_k is not None and not c.no_bid]

    @property
    def years(self) -> list[int]:
        return sorted({c.year for c in self._live_cells()})

    @property
    def max_odo(self) -> int:
        return max((c.band.odo_to for c in self._live_cells()), default=0)

    @property
    def min_bid(self) -> int | None:
        cells = self._live_cells()
        return min((c.bid_jpy_k for c in cells), default=None)

    @property
    def max_bid(self) -> int | None:
        cells = self._live_cells()
        return max((c.bid_jpy_k for c in cells), default=None)

    def cell_for(self, year: int, km: int) -> GridCell | None:
        for cell in self.cells.values():
            if cell.year == year and cell.band.contains(km):
                return cell
        return None

    def nearest_cell(self, year: int, km: int) -> GridCell | None:
        """The cell of that year whose band lies closest to `km`, for a km that no band covers
        (below the first band, or in a gap between bands). Jim, 2026-09-30: a lot with no
        dedicated band takes the closest band in the grid. Above the last band is not this
        case: the cap is checked first and stays a skip."""
        best: GridCell | None = None
        best_distance: int | None = None
        for cell in self.cells.values():
            if cell.year != year:
                continue
            distance = cell.band.odo_from - km if km <= cell.band.odo_from else max(0, km - cell.band.odo_to)
            if best is None or distance < best_distance:
                best, best_distance = cell, distance
        return best


@dataclass
class ModelRule:
    guide_id: int
    make: str
    model: str
    model_code: str
    min_grade: float | None
    text_rules: list[str]
    variants: list[Variant]
    boss_bid: bool
    active: bool
    updated_at: str | None
    eligibility_summary: str | None = None
    override_text: str = ""

    @property
    def active_variants(self) -> list[Variant]:
        """Every variant is biddable; the dashboard's on/off dot only marks the selected tab."""
        return list(self.variants)

    def resolve_variant(self, chassis: str, variant_text: str = "") -> "VariantMatch":
        """Pick the guide variant for a Nichibo vehicle.

        Order: exact chassis-code name (GDH206V) -> unnamed default variant ->
        trim keywords from the listing text (AYH30W 'Executive lounge') ->
        the guide's single named variant -> ambiguous (reviewer picks).
        Every variant is biddable; the dashboard's on/off dot is only the selected tab.
        """
        chassis = (chassis or "").strip().upper()
        named = [v for v in self.variants if not v.is_default]
        for v in named:
            if v.name.strip().upper() == chassis:
                return VariantMatch(v, "chassis", [v], f"chassis {chassis} = variant {v.name}")
        defaults = [v for v in self.variants if v.is_default]
        if defaults:
            return VariantMatch(defaults[0], "default", defaults[:1], "default variant (covers every chassis of this model code)")
        code = self.model_code.upper()
        chassis_like = [v for v in named if v.name.strip().upper().startswith(code)]
        if named and len(chassis_like) == len(named):
            return VariantMatch(None, "none", [], f"chassis {chassis} matches none of {', '.join(v.name for v in named)}")
        matched = _match_trim(variant_text, named)
        if len(matched) == 1:
            v = matched[0]
            return VariantMatch(v, "trim", [v], f"listing '{variant_text}' matches variant {v.name}")
        if len(matched) > 1:
            return VariantMatch(None, "ambiguous", matched,
                                f"listing '{variant_text}' matches several variants: {', '.join(v.name for v in matched)}")
        if len(named) == 1:
            v = named[0]
            return VariantMatch(v, "single", [v], f"the guide has one variant ({v.name}); confirm it fits the listing '{variant_text}'")
        if not named:
            return VariantMatch(None, "none", [], "the guide has no variants")
        return VariantMatch(None, "ambiguous", named,
                            f"cannot tell the variant from '{variant_text}'; candidates: {', '.join(v.name for v in named)}")

    def variant_by_chassis(self, chassis: str) -> Variant | None:
        return self.resolve_variant(chassis).variant

    @property
    def search_years(self) -> tuple[int, int] | None:
        years = sorted({y for v in self.active_variants for y in v.years})
        return (years[0], years[-1]) if years else None

    @property
    def search_max_odo(self) -> int:
        return max((v.max_odo for v in self.active_variants), default=0)


@dataclass
class VariantMatch:
    variant: Variant | None
    how: str  # chassis | default | trim | single | ambiguous | none
    candidates: list[Variant]
    note: str = ""


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _phrases(variant_name: str) -> list[list[str]]:
    """'Basic/Hybrid X/ G/ S/SR' -> [['basic'], ['hybrid','x'], ['g'], ['s'], ['sr']].

    Each slash- or comma-separated part is one trim phrase; a phrase matches only as
    whole consecutive tokens of the listing text, so 'S' never matches inside 'SR'.
    Pure numbers are ignored.
    """
    out = []
    for part in re.split(r"[/,]", variant_name):
        toks = _tokens(part)
        if toks and not all(t.isdigit() for t in toks):
            out.append(toks)
    return out


def _match_trim(variant_text: str, variants: list[Variant]) -> list[Variant]:
    """Variants whose trim phrases appear in the listing text; the longest phrase wins.

    'SR C Package' matches both 'SR' (Basic/.../SR) and 'SR-C' (SR-C/V/...); the
    two-token phrase is more specific, so only the SR-C variant is returned.
    """
    toks = _tokens(variant_text)
    if not toks:
        return []
    best: dict[int, int] = {}  # variant index -> longest matched phrase length
    for idx, v in enumerate(variants):
        for phrase in _phrases(v.name):
            n = len(phrase)
            if any(toks[i:i + n] == phrase for i in range(len(toks) - n + 1)):
                best[idx] = max(best.get(idx, 0), n)
    if not best:
        return []
    top = max(best.values())
    return [variants[idx] for idx, n in best.items() if n == top]


def _int_or_none(v: Any) -> int | None:
    return None if v is None else int(round(float(v)))


def parse_guide_tree(tree: dict, list_item: dict | None = None) -> ModelRule:
    """Build a ModelRule from /bidding-guides/{id}/tree (+ the list item for bossBid)."""
    variants: list[Variant] = []
    modifiers_raw = tree.get("modifiers") or []
    for v in tree.get("variants") or []:
        bands = [OdoBand(int(b["odoFrom"]), int(b["odoTo"])) for b in v.get("bandTemplate") or []]
        variant = Variant(id=int(v["id"]), name=(v.get("name") or "").strip(), active=bool(v.get("active")), bands=bands)
        blocks = v.get("blocks") or []
        selected = [b for b in blocks if b.get("selected")] or blocks[:1]
        for block in selected:
            for y in block.get("years") or []:
                year = int(y["year"])
                if y.get("eligible"):
                    variant.eligible_years.add(year)
                row_no_bid = bool(y.get("rowNoBid")) or not y.get("eligible", True)
                for line in y.get("lines") or []:
                    band = OdoBand(int(line["odoFrom"]), int(line["odoTo"]))
                    value = line.get("effectiveBiddingJpyK")
                    if value is None:
                        value = line.get("biddingJpyK")
                    no_bid = row_no_bid or bool(line.get("effectiveNoBid")) or not line.get("active", True)
                    key = (year, band.odo_from, band.odo_to)
                    variant.cells[key] = GridCell(
                        year=year,
                        band=band,
                        bid_jpy_k=_int_or_none(value),
                        no_bid=no_bid,
                        reason=line.get("effectiveReason") or ("row off" if row_no_bid else None),
                    )
        for m in modifiers_raw:
            if m.get("variantId") not in (None, variant.id):
                continue
            conds = [Condition(str(c.get("attr")), str(c.get("op")), "" if c.get("value") is None else str(c.get("value")),
                               [str(v) for v in (c.get("values") or [])])
                     for c in (m.get("conditions") or [])]
            variant.modifiers.append(Modifier(
                id=int(m["id"]),
                variant_id=m.get("variantId"),
                year_from=_int_or_none(m.get("resolvedYearFrom") or m.get("yearFrom")),
                year_to=_int_or_none(m.get("resolvedYearTo") or m.get("yearTo")),
                odo_from=_int_or_none(m.get("odoFrom")),
                odo_to=_int_or_none(m.get("odoTo")),
                conditions=conds,
                amount_jpy_k=_int_or_none(m.get("amountJpyK")),
                percent=None if m.get("percent") is None else float(m["percent"]),
                no_bid=bool(m.get("noBid")),
                strict=bool(m.get("strict")),
                note=m.get("note") or m.get("label") or "",
                active=bool(m.get("active", True)),
                stale=bool(m.get("stale")),
            ))
        variants.append(variant)

    notes = tree.get("notes") or (list_item or {}).get("notes") or ""
    text_rules = [t.strip() for t in notes.split("|") if t.strip()]
    return ModelRule(
        guide_id=int(tree["id"]),
        make=tree.get("make") or "",
        model=tree.get("model") or "",
        model_code=tree.get("modelCode") or "",
        min_grade=None if tree.get("minGrade") is None else float(tree["minGrade"]),
        text_rules=text_rules,
        variants=variants,
        boss_bid=bool((list_item or {}).get("bossBid", False)),
        active=bool(tree.get("active", True)),
        updated_at=tree.get("updatedAt"),
        eligibility_summary=tree.get("eligibilitySummary"),
    )


def rule_to_summary(rule: ModelRule) -> dict:
    """Compact JSON-able description used for evidence and the review page."""
    return {
        "guide_id": rule.guide_id,
        "model_code": rule.model_code,
        "make": rule.make,
        "model": rule.model,
        "min_grade": rule.min_grade,
        "text_rules": rule.text_rules,
        "updated_at": rule.updated_at,
        "variants": [
            {"name": v.label, "active": v.active, "years": v.years, "max_odo": v.max_odo,
             "modifiers": [m.describe() for m in v.modifiers if m.active and not m.no_bid]}
            for v in rule.variants
        ],
    }
