"""Deterministic bidding rules. Pure functions, no I/O.

Every check returns a Check; the overall HardResult is SKIP as soon as any
check fails, NEEDS_REVIEW when a rule cannot be evaluated from the facts,
and PASS with a computed bid otherwise.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from auction_bot.carbarn.models import Condition, GridCell, Modifier, ModelRule, Variant
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.instructions import free_text_lines, instruction_check, override_lines  # noqa: F401  (re-export)

PASS = "PASS"
SKIP = "SKIP"
NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    review: bool = False  # True when the check could not be decided


@dataclass
class HardResult:
    status: str
    checks: list[Check] = field(default_factory=list)
    variant_name: str | None = None
    year: int | None = None
    band_label: str | None = None
    base_jpy_k: int | None = None
    bid_jpy_k: int | None = None
    modifiers_applied: list[str] = field(default_factory=list)
    deferred_modifiers: list[str] = field(default_factory=list)
    toggles: list[str] = field(default_factory=list)  # reviewer-applied chips, e.g. "No Pro Pilot -220K"
    variant_how: str | None = None  # chassis | default | trim | single | chassis-uncertain | ambiguous | none
    candidate_bids: dict[str, int | None] = field(default_factory=dict)  # when the variant is ambiguous

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.ok]


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _cond_matches(cond: Condition, facts: VehicleFacts) -> bool | None:
    """True/False when decidable, None when the attribute is unknown."""
    attr = cond.attr.upper()
    if attr in ("MODEL_VARIANT", "VARIANT"):
        # Carbarn stores exact Nichibo variant strings, e.g. "HYBRID Z HONDA SENSING".
        if not facts.variant_text:
            return None
        actual = _squash(facts.variant_text)
        hit = any(_squash(v) == actual for v in cond.all_values)
        return hit if cond.op.lower() in ("in", "eq", "=", "==") else (not hit if cond.op.lower() in ("not_in", "ne", "!=") else None)
    if attr in ("COLOR", "COLOUR"):
        if not facts.colour:
            return None
        actual = _squash(facts.colour)
        hit = any(_squash(v) == actual for v in cond.all_values)
        return hit if cond.op.lower() in ("in", "eq", "=", "==") else (not hit if cond.op.lower() in ("not_in", "ne", "!=") else None)
    if attr == "GRADE":
        actual = facts.grade
        if actual is None:
            return None
        try:
            values = [float(v) for v in cond.all_values]
        except ValueError:
            return None
        target = values[0] if values else None
        op = cond.op.lower()
        if op in ("eq", "=", "=="):
            return target is not None and abs(actual - target) < 1e-9
        if op in ("gte", ">="):
            return target is not None and actual >= target
        if op in ("gt", ">"):
            return target is not None and actual > target
        if op in ("lte", "<="):
            return target is not None and actual <= target
        if op in ("lt", "<"):
            return target is not None and actual < target
        if op == "in":
            return any(abs(actual - v) < 1e-9 for v in values)
        return None
    if attr == "SEATS":
        if facts.seats is None:
            return None
        try:
            values = [int(float(v)) for v in cond.all_values]
        except ValueError:
            return None
        op = cond.op.lower()
        if op == "in":
            return facts.seats in values
        target = values[0] if values else None
        if target is None:
            return None
        return {"eq": facts.seats == target, "gte": facts.seats >= target, "gt": facts.seats > target,
                "lte": facts.seats <= target, "lt": facts.seats < target}.get(op)
    if attr in ("KM", "ODO"):
        if facts.km is None:
            return None
        try:
            target = float(cond.value)
        except ValueError:
            return None
        op = cond.op.lower()
        return {"eq": facts.km == target, "gte": facts.km >= target, "gt": facts.km > target,
                "lte": facts.km <= target, "lt": facts.km < target}.get(op)
    if attr == "YEAR":
        if facts.year is None:
            return None
        try:
            target = int(cond.value)
        except ValueError:
            return None
        op = cond.op.lower()
        return {"eq": facts.year == target, "gte": facts.year >= target, "gt": facts.year > target,
                "lte": facts.year <= target, "lt": facts.year < target}.get(op)
    return None


# Jim, 2026-09-14: a positive grade bonus stored as "GRADE eq X" applies to grade X
# AND ABOVE (e.g. a +80 for grade 4 also applies to 4.5 and 5). When several such
# bonuses could apply, only the one with the highest threshold is used. Negative grade
# adjustments (e.g. grade 3.5 -> -90, grade 3 -> -240) stay exact-match bands.
GRADE_BONUS_AT_LEAST = True


def _grade_bonus_threshold(m: Modifier) -> float | None:
    """Threshold X when m is a single-condition 'GRADE eq X' bonus with a positive amount."""
    if not GRADE_BONUS_AT_LEAST or m.no_bid or m.percent or not m.amount_jpy_k or m.amount_jpy_k <= 0:
        return None
    if len(m.conditions) != 1:
        return None
    c = m.conditions[0]
    if c.attr.upper() != "GRADE" or c.op.lower() not in ("eq", "=", "==", "gte", ">="):
        return None
    try:
        return float(c.value)
    except ValueError:
        return None


def apply_modifiers(variant: Variant, facts: VehicleFacts, base: int, year: int, km: int,
                    final: bool = False) -> tuple[int | None, list[str], list[str], list[Check]]:
    """Return (bid, applied, deferred, checks). bid None means no bid.

    A modifier whose condition needs a fact we do not have yet (e.g. SEATS before
    the auction sheet is read) is deferred. On the final pass (final=True) an
    undecidable modifier sends the vehicle to manual review.
    """
    bid = float(base)
    applied: list[str] = []
    deferred: list[str] = []
    checks: list[Check] = []
    grade_bonus: list[tuple[float, Modifier]] = []
    for m in variant.modifiers:
        if not m.active or m.stale:
            continue
        if not m.applies_to_year_odo(year, km):
            continue
        if m.is_toggle:
            # Hand-applied chip on the dashboard; listed for the reviewer, never applied here.
            checks.append(Check("toggle", True, f"reviewer chip (not applied): {m.note or 'chip'} {m.amount_jpy_k:+d}K"))
            continue
        if m.percent and not m.conditions and not m.amount_jpy_k and not m.no_bid:
            # The guide's dial ("Bid at 92%"). Jim, 2026-09-14: the grid values already
            # include it, so it is shown but never applied a second time.
            checks.append(Check("dial", True, f"'{m.note or 'Bid at ' + format(m.percent, 'g') + '%'}' is already reflected in the grid values"))
            continue
        threshold = _grade_bonus_threshold(m)
        if threshold is not None:
            if facts.grade is not None and facts.grade >= threshold:
                grade_bonus.append((threshold, m))
            continue
        cond_results = [_cond_matches(c, facts) for c in m.conditions]
        if any(r is None for r in cond_results):
            if m.strict or final:
                checks.append(Check("modifier", False, f"cannot evaluate modifier: {m.describe()}", review=True))
            else:
                deferred.append(m.describe())
            continue
        if not all(cond_results):
            continue
        if m.no_bid:
            checks.append(Check("modifier", False, f"no-bid modifier applies: {m.describe()}"))
            return None, applied, deferred, checks
        if m.amount_jpy_k:
            bid += m.amount_jpy_k
            applied.append(m.describe())
        if m.percent:
            bid = bid * m.percent / 100.0
            applied.append(m.describe())
    if grade_bonus:
        threshold, m = max(grade_bonus, key=lambda t: t[0])
        bid += m.amount_jpy_k
        applied.append(f"{m.amount_jpy_k:+d}K for grade >= {threshold:g}"
                       + (f" (years {m.year_from}-{m.year_to})" if m.year_from or m.year_to else ""))
    return int(round(bid)), applied, deferred, checks


_SEAT_WORDS = re.compile(r"\bseat(?:s|er|ers|ing)?\b", re.I)
# Jim, 2026-09-15: "S-Hybrid 2 littre 8 seater" means a 2-litre engine and 8 seats. A number
# glued to an engine size, a door count or a drive type is never a seat count.
_NOT_SEATS = re.compile(r"\b\d+(?:\.\d+)?\s*(?:litres?|liters?|littres?|l|cc|doors?|dr|wd)\b", re.I)


def allowed_seats(rule: ModelRule) -> set[int] | None:
    """Seat counts permitted by the guide's text rules, e.g. '2, 3, 5 or 6 seats' -> {2,3,5,6}.

    Returns None when no text rule talks about seats. Only rules that list plain seat
    numbers are parsed; anything else ('captain seat', 'leather seat') is left to the reviewer.
    """
    allowed: set[int] = set()
    found = False
    for text in list(rule.text_rules) + free_text_lines(rule):  # structured skip lines are not seat lists
        if not _SEAT_WORDS.search(text):
            continue
        # numbers that appear before the word "seat": "2, 3, 5 or 6 seats", "8 seater", "5/7 seats"
        head = _NOT_SEATS.sub(" ", _SEAT_WORDS.split(text, maxsplit=1)[0])
        nums = [int(n) for n in re.findall(r"\b(\d{1,2})\b", head)]
        nums = [n for n in nums if 1 <= n <= 30]
        if nums:
            allowed.update(nums)
            found = True
    return allowed if found else None


def _bid_for_variant(variant: Variant, facts: VehicleFacts, margin: int = 0) -> int | None:
    """Grid bid for one candidate variant (used to show options when the variant is ambiguous).
    `margin` is the Rulebook odometer margin: the band is chosen for km + margin."""
    if facts.year is None or facts.km is None:
        return None
    km = facts.km + margin
    cell = variant.cell_for(facts.year, km) or variant.nearest_cell(facts.year, km)
    if cell is None or cell.no_bid or cell.bid_jpy_k is None:
        return None
    bid, _applied, _deferred, _checks = apply_modifiers(variant, facts, cell.bid_jpy_k, facts.year, facts.km)
    return bid


def evaluate_hard(facts: VehicleFacts, rule: ModelRule, final: bool = False) -> HardResult:
    res = HardResult(status=SKIP)
    checks = res.checks

    def fail(name: str, detail: str, review: bool = False) -> HardResult:
        checks.append(Check(name, False, detail, review=review))
        res.status = NEEDS_REVIEW if review else SKIP
        return res

    if not rule.active:
        return fail("guide", f"bidding guide {rule.model_code} is inactive")

    # Standing instructions (rules/overrides/<CODE>.md, "skip: ..." lines) decidable from the facts so far.
    hit = instruction_check(rule, facts)
    if hit is not None:
        checks.append(hit)
        return res

    # Variant: chassis code (GDH206V vs GDH206K), default variant, or trim keywords.
    if not facts.chassis:
        return fail("variant", "chassis code unknown; open detail page", review=True)
    match = rule.resolve_variant(facts.chassis, facts.variant_text)
    res.variant_how = match.how
    if match.how == "ambiguous":
        res.candidate_bids = {v.label: _bid_for_variant(v, facts, rule.odo_margin_km) for v in match.candidates}
        cands = "; ".join(f"{k}: {v:,}K" if v else f"{k}: no bid" for k, v in res.candidate_bids.items())
        return fail("variant", f"{match.note} ({cands})", review=True)
    variant = match.variant
    if variant is None:
        return fail("variant", match.note)
    res.variant_name = variant.label
    note = match.note
    if not variant.active and not variant.is_default:
        note += " (not the tab selected on the dashboard)"
    if facts.chassis_uncertain and match.how == "chassis":
        note += f" — Nichibo shows the code as '{facts.chassis}?' (guessed); confirm the 型式 on the auction sheet"
        res.variant_how = "chassis-uncertain"
    checks.append(Check("variant", True, note))

    # Grade.
    grade = facts.grade
    if grade is None:
        return fail("grade", f"grade '{facts.grade_raw}' is not a bidable numeric grade")
    if rule.min_grade is not None and grade < rule.min_grade:
        return fail("grade", f"grade {grade:g} below minimum {rule.min_grade:g}")
    checks.append(Check("grade", True, f"grade {grade:g} >= min {rule.min_grade}"))

    # Seats, when the sheet has been read (OCR or model) and the guide limits seat counts.
    allowed = allowed_seats(rule)
    if allowed is not None and facts.seats is not None:
        if facts.seats not in allowed:
            return fail("seats", f"auction sheet shows {facts.seats} seats; rule allows {', '.join(map(str, sorted(allowed)))}")
        checks.append(Check("seats", True, f"{facts.seats} seats on the sheet (rule allows {', '.join(map(str, sorted(allowed)))})"))

    # Year and odo band.
    if facts.year is None:
        return fail("year", "year unknown", review=True)
    if facts.km is None:
        return fail("odo", "odometer unknown", review=True)
    if facts.year not in variant.years:
        return fail("year", f"year {facts.year} has no bid row for {variant.name} (rows {variant.years[:1]}..{variant.years[-1:]})")
    res.year = facts.year
    margin = rule.odo_margin_km
    km = facts.km + margin   # Rulebook odometer margin: band choice and cap only; modifiers keep the listed km
    shown = f"{facts.km:,} km + {margin:,} km margin" if margin else f"{facts.km:,} km"
    if km > variant.max_odo:
        return fail("odo", f"{shown} exceeds {variant.name} cap of {variant.max_odo:,} km")
    cell = variant.cell_for(facts.year, km)
    if cell is None:
        # Jim, 2026-09-30: a km that no band covers (0 km under a grid that starts higher, or a
        # gap between bands) takes the closest band. The cap above was checked first.
        cell = variant.nearest_cell(facts.year, km)
        if cell is None:
            return fail("odo", f"no odo band covers {km:,} km for {facts.year}")
        checks.append(Check("odo", True, f"no band covers {km:,} km; the closest band {cell.band.label} is used"))
    res.band_label = cell.band.label
    if cell.no_bid or cell.bid_jpy_k is None:
        return fail("cell", f"grid cell {facts.year} / {cell.band.label} is empty or switched off ({cell.reason or 'no value'})")
    if margin:
        checks.append(Check("cell", True, f"{facts.km:,} + {margin:,} km margin -> {facts.year} / {cell.band.label} -> {cell.bid_jpy_k:,}K"))
    else:
        checks.append(Check("cell", True, f"{facts.year} / {cell.band.label} -> {cell.bid_jpy_k:,}K"))
    res.base_jpy_k = cell.bid_jpy_k

    # Modifiers (a grade-4 bonus etc.).
    bid, applied, deferred, mod_checks = apply_modifiers(variant, facts, cell.bid_jpy_k, facts.year, facts.km, final=final)
    checks.extend(mod_checks)
    res.modifiers_applied = applied
    res.deferred_modifiers = deferred
    res.toggles = [f"{m.note or 'chip'} {m.amount_jpy_k:+d}K" for m in variant.modifiers
                   if m.active and not m.stale and m.is_toggle and m.applies_to_year_odo(facts.year, facts.km)]
    if any(c.review for c in mod_checks):
        res.status = NEEDS_REVIEW
        return res
    if bid is None:
        res.status = SKIP
        return res
    res.bid_jpy_k = bid
    checks.append(Check("modifiers", True, ", ".join(applied) if applied else "none applied"))

    # Standing instructions that compare the bid (e.g. "skip: start >= bid").
    hit = instruction_check(rule, facts, bid)
    if hit is not None:
        checks.append(hit)
        res.status = SKIP
        return res

    # Sanity: bid within the grid's own range (+ modifier headroom).
    lo, hi = variant.min_bid or 0, (variant.max_bid or 0) + 500
    if not (lo <= bid <= hi):
        return fail("sanity", f"bid {bid:,}K outside grid range {lo:,}K-{hi:,}K")

    # Start price.
    if facts.start_price_jpy is None:
        return fail("start", "start price unknown", review=True)
    if bid * 1000 < facts.start_price_jpy:
        return fail("start", f"bid {bid:,}K is below start price {facts.start_price_jpy // 1000:,}K")
    checks.append(Check("start", True, f"bid {bid:,}K >= start {facts.start_price_jpy // 1000:,}K"))

    # Already bid on Nichibo.
    if facts.has_current_bid:
        return fail("duplicate", f"Nichibo already shows a current bid of {facts.current_bid_jpy}")

    res.status = PASS
    return res


def card_prefilter(facts: VehicleFacts, rule: ModelRule) -> Check | None:
    """Cheap rejection from search-card data only. Returns a failing Check or None."""
    grade = facts.grade
    if grade is None:
        return Check("grade", False, f"grade '{facts.grade_raw}' is not bidable")
    if rule.min_grade is not None and grade < rule.min_grade:
        return Check("grade", False, f"grade {grade:g} below minimum {rule.min_grade:g}")
    years = {y for v in rule.variants for y in v.years}
    if facts.year is not None and facts.year not in years:
        return Check("year", False, f"year {facts.year} has no bid row")
    margin = rule.odo_margin_km
    if facts.km is not None and facts.km + margin > rule.search_max_odo:
        shown = f"{facts.km:,} km + {margin:,} km margin" if margin else f"{facts.km:,} km"
        return Check("odo", False, f"{shown} above every variant cap")
    return None


def card_grid_check(facts: VehicleFacts, rule: ModelRule) -> Check | None:
    """Cheap rejection from the search card and the whole grid, before any page or image is fetched.

    Every variant is tried, so the check holds whichever variant the lot turns out to be:
    SKIP only when no variant has a live cell for this year and odometer, or when the start
    price is above the highest bid any variant could reach (cell plus every positive
    modifier, reviewer chips included). The ceiling over-estimates, so it never over-skips.
    """
    if facts.year is None or facts.km is None:
        return None
    ceiling: int | None = None
    for v in rule.variants:
        eff = facts.km + rule.odo_margin_km
        cell = v.cell_for(facts.year, eff) or v.nearest_cell(facts.year, eff)
        if cell is None or cell.no_bid or cell.bid_jpy_k is None:
            continue
        extra = sum(m.amount_jpy_k for m in v.modifiers
                    if m.active and not m.stale and m.amount_jpy_k and m.amount_jpy_k > 0
                    and m.applies_to_year_odo(facts.year, facts.km))
        ceiling = max(ceiling or 0, cell.bid_jpy_k + extra)
    if ceiling is None:
        return Check("cell", False, f"no variant has a bid cell for {facts.year} / {facts.km + rule.odo_margin_km:,} km")
    if facts.start_price_jpy and ceiling * 1000 < facts.start_price_jpy:
        return Check("start", False, f"start price {facts.start_price_jpy // 1000:,}K is above the highest possible bid {ceiling:,}K")
    return None
