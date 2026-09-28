"""The playbook: what the model is told about how Jim decides, in Jim's words.

`rules/playbook/general.md` holds the principles (seeded from the teaching sessions, edited
freely by Jim). `rules/playbook/<CODE>.md` gathers, one stamped line each, the comments he
typed in learn mode about that model and the corrections he made on the review page. Both
are git-ignored like the standing instructions. Nothing here is a rule the code applies;
it is context for the model, and the reviewer sees the same words.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from auction_bot.carbarn.models import ModelRule
from auction_bot.config import Config
from auction_bot.nichibo.facts import VehicleFacts

GENERAL_SEED = """# BidBarn playbook: how Jim decides, in his words
# Seeded 2026-09-28 from the teaching sessions of 15 September. Edit freely; every line is
# shown to the judgement model and to the reviewer. One idea per line, starting with "- ".

- Grade: only the guide's minimum grade and above. Grade 0, R and RA are never bid.
- The amount is the grid cell for the year row and the odometer band. Rows without values are not biddable years.
- The odometer keeps rising between the auction and the warehouse: a lot already at the top of the last band is skipped, and a lot exactly on a band boundary is priced with the next band's value.
- Chips (Autech +, No Pro Pilot -): when unsure whether the chip applies, use the base value.
- Pro Pilot is the blue button on the steering wheel. If it is not there, the No Pro Pilot deduction applies.
- Seat colour is judged from the interior photos: "dark" means black, dark grey or dark brown; beige and light grey are not dark.
- Seats, engine size and model code come from the auction sheet. "S-Hybrid 2 littre 8 seater" means a 2-litre engine and 8 seats.
- Stock "Buy Now" cards are never touched; only auction lots.
- Anything not visible in the photos or on the sheet is "unclear", never guessed.
- Sheet glossary: 乗車定員 = seats; 型式 = model code; 排気量 = engine cc; 評価点 = grade; シフト = gearbox, where F5, F6, 5F, 6F or MT mean a manual (a rule that says AT fails) and AT, FA, IAT, CVT mean an automatic; 4WD is marked next to the grade name; 走行 = odometer.
"""

MAX_CHARS = 6000


def playbook_dir(cfg: Config) -> Path:
    d = getattr(cfg, "playbook_dir", None) or (Path(cfg.override_dir).parent / "playbook")
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_general(cfg: Config) -> Path:
    p = playbook_dir(cfg) / "general.md"
    if not p.exists():
        p.write_text(GENERAL_SEED, encoding="utf-8")
    return p


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("- ") and len(s) > 2:
            out.append(s[2:].strip())
    return out


def general_lines(cfg: Config) -> list[str]:
    return _lines(ensure_general(cfg))


def code_lines(cfg: Config, model_code: str) -> list[str]:
    return _lines(playbook_dir(cfg) / f"{model_code.upper()}.md")


def append_playbook(cfg: Config, model_code: str, text: str, author: str = "learn") -> Path:
    """Add one stamped line for a model code (or GENERAL). Mirrors the standing instructions file."""
    name = "general" if model_code.upper() == "GENERAL" else model_code.upper()
    p = playbook_dir(cfg) / f"{name}.md"
    if name == "general":
        ensure_general(cfg)
    elif not p.exists():
        p.write_text(f"# Playbook for {name}: Jim's comments and corrections, in his words\n\n", encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(f"- ({stamp}, {author}) {' '.join(text.split())}\n")
    return p


@dataclass
class PlaybookContext:
    general: list[str] = field(default_factory=list)
    code_notes: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    def to_text(self, max_chars: int = MAX_CHARS) -> str:
        parts = ["# How the buyer decides (his words)"]
        parts += [f"- {ln}" for ln in self.general]
        if self.code_notes:
            parts.append("# His comments and corrections on this model code")
            parts += [f"- {ln}" for ln in self.code_notes]
        if self.examples:
            parts.append("# His decisions on similar lots")
            parts += [f"- {ln}" for ln in self.examples]
        text = "\n".join(parts)
        suffix = "\n- (playbook truncated)"
        return text if len(text) <= max_chars else text[:max_chars - len(suffix)] + suffix


def _band(km: int | None) -> int | None:
    return None if km is None else km // 10_000


def similar_decisions(store, rule: ModelRule, facts: VehicleFacts, limit: int = 6) -> list[str]:
    """Jim's past decisions on this model code, the most similar lots first (year, km band, grade)."""
    rows = store.decisions_with_facts(rule.model_code)
    scored = []
    for r in rows:
        f = r.get("facts") or {}
        score = 0
        if f.get("year") == facts.year:
            score += 2
        if _band(f.get("km")) == _band(facts.km):
            score += 2
        if f.get("grade") == facts.grade:
            score += 1
        action = "approved" if r.get("action") == "approve" else "rejected"
        amount = f" at {r['amount_jpy_k']:,}K" if r.get("amount_jpy_k") else ""
        reason = f": {r['reason']}" if r.get("reason") else ""
        scored.append((score, f"{action}{amount} {r.get('summary') or ''}{reason}"[:200]))
    scored.sort(key=lambda t: -t[0])
    return [s for _, s in scored[:limit]]


def playbook_for(store, cfg: Config, rule: ModelRule, facts: VehicleFacts, code_notes_limit: int = 15) -> PlaybookContext:
    ctx = PlaybookContext(general=general_lines(cfg))
    notes = code_lines(cfg, rule.model_code)
    if store is not None:
        notes += [f"(learn mode) {n}" for n in store.manual_notes(rule.model_code, limit=code_notes_limit)]
        ctx.examples = similar_decisions(store, rule, facts)
    ctx.code_notes = notes[-code_notes_limit:] if len(notes) > code_notes_limit else notes
    return ctx
