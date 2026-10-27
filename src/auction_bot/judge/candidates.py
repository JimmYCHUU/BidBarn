"""The few amounts a lot could be bid at, computed from the grid so the model only ever
picks one of them (or none). The grid itself is never sent to a provider."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from auction_bot.carbarn.models import ModelRule
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.hard import HardResult, _bid_for_variant


@dataclass
class Candidate:
    label: str
    amount_k: int
    why: str

    def to_dict(self) -> dict:
        return asdict(self)


def candidates_for(rule: ModelRule, facts: VehicleFacts, hard: HardResult) -> list[Candidate]:
    out: list[Candidate] = []

    def add(label: str, amount: int | None, why: str) -> None:
        if amount is None or amount <= 0 or any(c.amount_k == amount for c in out):
            return
        out.append(Candidate(label, int(amount), why))

    base = hard.bid_jpy_k
    if base:
        mods = ", ".join(hard.modifiers_applied) if hard.modifiers_applied else "no modifier"
        add("grid", base, f"grid cell {hard.year} / {hard.band_label} ({mods})")
    match = rule.resolve_variant(facts.chassis, facts.variant_text)
    variant = match.variant
    if variant is not None and facts.year is not None and facts.km is not None:
        if base:
            for m in variant.modifiers:
                if m.active and not m.stale and m.is_toggle and m.applies_to_year_odo(facts.year, facts.km) and m.amount_jpy_k:
                    add(f"grid {m.amount_jpy_k:+d}K ({m.note or 'chip'})", base + m.amount_jpy_k,
                        f"the dashboard chip '{m.note or 'chip'}' applies")
        km = facts.km + rule.odo_margin_km   # the km the hard rules priced (Rulebook margin)
        cell = variant.cell_for(facts.year, km)
        if cell is not None and (km == cell.band.odo_to or (variant.max_odo and km >= 0.9 * variant.max_odo)):
            nxt = _bid_for_variant(variant, _with_km(facts, cell.band.odo_to + 1))
            if nxt:
                add("next odometer band", nxt, f"the odometer rises before delivery; the band after {cell.band.label}")
    if hard.variant_how == "ambiguous":
        for label, amount in (hard.candidate_bids or {}).items():
            add(f"variant {label}", amount, f"if the lot is the {label} variant")
    return out


def _with_km(facts: VehicleFacts, km: int) -> VehicleFacts:
    from dataclasses import replace
    return replace(facts, km=km, photo_urls=list(facts.photo_urls))
