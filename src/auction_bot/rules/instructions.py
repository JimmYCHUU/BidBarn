"""Standing instructions per model code (rules/overrides/<CODE>.md), free text and structured.

Every line is shown to the reviewer as before. A line whose text starts with `skip:` is
also machine-checked and, when every condition holds, skips the lot before any page is
opened. `skip` is the only verb: a standing instruction can never approve anything.

    skip: <cond> [and <cond>]...
    cond  := <field> <op> <value> | <field> <value>   (no op: "contains" for text, "=" for numbers)
    field := year | make | model | variant | transmission | km | cc | grade | start | seats
             | chassis | colour | equipment | hall | date | bid
    op    := > | >= | < | <= | = | != | contains | not contains | is | is not | starts with
    value := number (commas allowed, k suffix: 120k) | bare or "quoted" text | another field

`start` is in yen, `bid` in thousands of yen; comparing the two converts the bid to yen.
A condition on a fact that is not known yet is undecided and never fires: uncertainty
never skips and never bids.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from auction_bot.carbarn.models import ModelRule
from auction_bot.nichibo.facts import VehicleFacts

# field name -> (VehicleFacts attribute, kind)
FIELDS: dict[str, tuple[str, str]] = {
    "year": ("year", "num"), "make": ("make", "text"), "model": ("model", "text"),
    "variant": ("variant_text", "text"), "transmission": ("transmission", "text"),
    "km": ("km", "num"), "odo": ("km", "num"), "cc": ("engine_cc", "num"), "engine": ("engine_cc", "num"),
    "grade": ("grade", "num"), "start": ("start_price_jpy", "num"), "seats": ("seats", "num"),
    "chassis": ("chassis", "text"), "colour": ("colour", "text"), "color": ("colour", "text"),
    "equipment": ("equipment", "text"), "hall": ("auction_place", "text"), "date": ("auction_date", "text"),
    "bid": ("bid", "num"),
}
# Facts that only the detail page carries; a line using one of these needs the page.
DETAIL_ATTRS = {"chassis", "colour", "equipment", "auction_place", "auction_date"}
YEN_ATTRS = {"start_price_jpy"}  # bid (K JPY) is converted when compared with these

NUM_OPS = {">", ">=", "<", "<=", "=", "!="}
TEXT_OPS = {"contains", "not contains", "is", "is not", "starts with", "=", "!="}
_OP_RE = r">=|<=|!=|=|>|<|not contains|contains|is not|is|starts with"
_COND_RE = re.compile(rf"^(?P<field>[a-z]+)\s*(?:(?P<op>{_OP_RE})\s+)?(?P<value>.+?)\s*$", re.I)
_NUM_RE = re.compile(r"^(\d[\d,]*)(k)?$", re.I)
_OVERRIDE_STAMP = re.compile(r"^\(\d{4}-\d{2}-\d{2}[^)]*\)\s*")
_STRUCTURED = re.compile(r"^\s*skip\s*:", re.I)
_VERB = re.compile(r"^\s*([a-z]+)\s*:", re.I)


class InstructionError(ValueError):
    pass


@dataclass
class Condition:
    field: str  # canonical field name as written (lower case)
    attr: str  # VehicleFacts attribute (or "bid")
    kind: str  # num | text
    op: str
    value: str | int | float | None = None
    ref_attr: str | None = None  # compare with another fact instead of a literal

    def describe(self) -> str:
        val = self.ref_attr and next(k for k, (a, _) in FIELDS.items() if a == self.ref_attr) or self.value
        return f"{self.field} {self.op} {val}"


@dataclass
class Instruction:
    raw: str
    conditions: list[Condition] = field(default_factory=list)

    @property
    def needs_detail(self) -> bool:
        return any(c.attr in DETAIL_ATTRS or c.ref_attr in DETAIL_ATTRS for c in self.conditions)


# -- override file lines --------------------------------------------------------------

def override_lines(rule: ModelRule) -> list[str]:
    """Standing instructions without their '(date, author)' stamp."""
    out = []
    for ln in rule.override_text.splitlines():
        if ln.strip().startswith("-"):
            text = _OVERRIDE_STAMP.sub("", ln.strip().lstrip("- ").strip())
            if text:
                out.append(text)
    return out


def is_structured(text: str) -> bool:
    return bool(_STRUCTURED.match(text or ""))


def split_override_lines(rule: ModelRule) -> tuple[list[str], list[Instruction], list[str]]:
    """(free-text lines, parsed skip lines, error messages for lines that could not be parsed)."""
    free: list[str] = []
    instructions: list[Instruction] = []
    errors: list[str] = []
    for ln in override_lines(rule):
        if not is_structured(ln):
            free.append(ln)
            continue
        try:
            instructions.append(parse_instruction(ln))
        except InstructionError as exc:
            errors.append(f"{ln} ({exc})")
    return free, instructions, errors


def free_text_lines(rule: ModelRule) -> list[str]:
    return split_override_lines(rule)[0]


# -- grammar ------------------------------------------------------------------------

def _parse_value(kind: str, raw: str, field_name: str) -> tuple[str | int | None, str | None]:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1], None
    key = raw.lower()
    if key in FIELDS and key != field_name:
        attr, ref_kind = FIELDS[key]
        if ref_kind != kind:
            raise InstructionError(f"cannot compare {field_name} with {key}")
        return None, attr
    if kind == "num":
        m = _NUM_RE.match(raw)
        if not m:
            raise InstructionError(f"'{raw}' is not a number for {field_name}")
        n = int(m.group(1).replace(",", ""))
        return n * 1000 if m.group(2) else n, None
    return raw, None


def parse_condition(text: str) -> Condition:
    m = _COND_RE.match(text.strip())
    if not m:
        raise InstructionError(f"cannot read condition '{text.strip()}'")
    name = m.group("field").lower()
    if name not in FIELDS:
        raise InstructionError(f"unknown field '{name}' (use {', '.join(FIELDS)})")
    attr, kind = FIELDS[name]
    op = (m.group("op") or "").lower().strip()
    if not op:
        op = "=" if kind == "num" else "contains"
    op = " ".join(op.split())
    if kind == "num" and op not in NUM_OPS:
        raise InstructionError(f"'{op}' does not apply to the number field {name}")
    if kind == "text" and op not in TEXT_OPS:
        raise InstructionError(f"'{op}' does not apply to the text field {name}")
    value, ref = _parse_value(kind, m.group("value"), name)
    return Condition(field=name, attr=attr, kind=kind, op=op, value=value, ref_attr=ref)


def parse_instruction(text: str) -> Instruction:
    """Parse one structured line. Anything but `skip:` is refused: no line can approve."""
    verb = _VERB.match(text or "")
    if not verb:
        raise InstructionError("a structured line starts with 'skip:'")
    if verb.group(1).lower() != "skip":
        raise InstructionError(f"'{verb.group(1)}:' is not allowed; the only verb is 'skip:'")
    body = text[verb.end():].strip()
    if not body:
        raise InstructionError("nothing after 'skip:'")
    parts = [p for p in re.split(r"\s+and\s+", body, flags=re.I) if p.strip()]
    return Instruction(raw=text.strip(), conditions=[parse_condition(p) for p in parts])


# -- evaluation ---------------------------------------------------------------------

def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _fact(facts: VehicleFacts, attr: str, bid_jpy_k: int | None):
    if attr == "bid":
        return None if bid_jpy_k is None else bid_jpy_k
    return getattr(facts, attr, None)


def _as_yen(attr: str, value, other_attr: str) -> float | None:
    if value is None:
        return None
    if attr == "bid" and other_attr in YEN_ATTRS:
        return float(value) * 1000
    return float(value)


def evaluate_condition(c: Condition, facts: VehicleFacts, bid_jpy_k: int | None = None) -> bool | None:
    """True/False when decidable from the facts, None when a needed fact is unknown."""
    actual = _fact(facts, c.attr, bid_jpy_k)
    if actual is None or actual == "":
        return None
    if c.kind == "num":
        if c.ref_attr:
            other = _fact(facts, c.ref_attr, bid_jpy_k)
            if other is None:
                return None
            a = _as_yen(c.attr, actual, c.ref_attr)
            b = _as_yen(c.ref_attr, other, c.attr)
        else:
            a, b = float(actual), float(c.value)
        return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b, "=": a == b, "!=": a != b}[c.op]
    hay = " ".join(str(actual).split()).lower()
    if c.ref_attr:
        other = _fact(facts, c.ref_attr, bid_jpy_k)
        if other is None or other == "":
            return None
        needle = " ".join(str(other).split()).lower()
    else:
        needle = " ".join(str(c.value).split()).lower()
    if c.op == "contains":
        return needle in hay
    if c.op == "not contains":
        return needle not in hay
    if c.op in ("is", "="):
        return _squash(hay) == _squash(needle)
    if c.op in ("is not", "!="):
        return _squash(hay) != _squash(needle)
    if c.op == "starts with":
        return hay.startswith(needle)
    return None


def fires(instruction: Instruction, facts: VehicleFacts, bid_jpy_k: int | None = None) -> bool:
    return all(evaluate_condition(c, facts, bid_jpy_k) is True for c in instruction.conditions)


def instruction_check(rule: ModelRule, facts: VehicleFacts, bid_jpy_k: int | None = None):
    """The failing Check for the first standing instruction that fires, else None."""
    from auction_bot.rules.hard import Check  # local import: hard.py imports this module

    _free, instructions, _errors = split_override_lines(rule)
    for ins in instructions:
        if fires(ins, facts, bid_jpy_k):
            return Check("instruction", False, f"standing instruction: {ins.raw}")
    return None


def detail_fields_used(instructions: list[Instruction]) -> bool:
    return any(i.needs_detail for i in instructions)


# -- suggestions from a reject reason ------------------------------------------------------

_STOP = {"the", "and", "has", "have", "with", "not", "this", "that", "was", "are", "its", "too", "for", "from",
         "seat", "seats", "seater", "car", "van", "lot", "bid", "skip", "rule", "only", "but", "one", "all"}


def suggest_from_reason(reason: str, facts: VehicleFacts) -> str | None:
    """A deterministic `skip:` line that would have skipped this lot for the reason given, or None."""
    text = (reason or "").strip()
    if not text:
        return None
    low = text.lower()
    m = re.search(r"(\d{2,3}(?:,\d{3})+|\d{4,7})\s*km", low)
    if m:
        n = int(m.group(1).replace(",", ""))
        if facts.km is None or facts.km >= n:
            return f"skip: km >= {n}"
    if "start" in low and ("bid" in low or "price" in low):
        return "skip: start >= bid"
    m = re.search(r"(\d{1,2})\s*seat", low)
    if m and (facts.seats is None or facts.seats == int(m.group(1))):
        return f"skip: seats = {int(m.group(1))}"
    tokens = [t for t in re.findall(r"[a-z][a-z0-9\-]{2,}", low) if t not in _STOP]
    for name, attr in (("variant", "variant_text"), ("equipment", "equipment"), ("hall", "auction_place")):
        hay = " ".join(str(getattr(facts, attr, "") or "").split()).lower()
        if not hay:
            continue
        for t in tokens:
            if t in hay:
                return f"skip: {name} contains {t.upper() if name != 'hall' else t}"
    colour = (facts.colour or "").strip()
    if colour and colour.lower() in low:
        return f"skip: colour is {colour}"
    return None
