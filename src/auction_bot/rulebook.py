"""The Rulebook's catalogue: what Jim decided, what is still open, and what is locked.

Plain words, written from the README ("Rules as Carbarn confirmed them", "Open questions for Carbarn")
and CLAUDE.md. Only the open questions can be answered, and only with the values listed here; the
locked rules have no question at all. An answer is kept as a version in the store and never edits the
rules by itself. Nothing here approves or places a bid.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Decided:
    id: str
    text: str
    date: str       # as written in the source; "" when the source gives none
    source: str


@dataclass(frozen=True)
class Choice:
    value: object
    label: str


@dataclass(frozen=True)
class Question:
    id: str
    title: str
    text: str
    source: str
    kind: str                       # "setting" (pick a value) or "confirm" (keep what the bot does today)
    setting: str | None = None      # the answers key for a setting
    choices: tuple[Choice, ...] = ()
    today: object = None            # a setting's value today, or the sentence for what the bot does today
    change_hint: str = ""


@dataclass(frozen=True)
class Locked:
    text: str
    source: str


_README = "README, Rules as Carbarn confirmed them"

DECIDED: tuple[Decided, ...] = (
    Decided("odo_bands",
            "Odometer bands include their upper bound: 40K-50K covers 40,001 to 50,000 km. 0 km is the lowest band; a km "
            "no band covers (below the first band, or in a gap) takes the closest band; above the last band is a skip.",
            "2026-09-30", _README + "; CLAUDE.md section 2"),
    Decided("grade", "Grade is Nichibo's listing grade; 0, R, RA or blank never bid.", "2026-09-14",
            _README + "; CLAUDE.md section 2"),
    Decided("grade_bonus",
            "A positive bonus stored as \"grade 4\" applies to 4 and above (highest threshold only); negative "
            "adjustments apply to that exact grade.", "2026-09-14", _README + "; CLAUDE.md section 2"),
    Decided("dials", "Percentage dials (for example x92%) are already inside the grid values and are never applied again, "
            "including the E-Class Wagon's dial.", "2026-09-14", _README + "; CLAUDE.md section 2"),
    Decided("variants", "Every variant on a guide is biddable; the on/off dot on the dashboard is only the selected tab.",
            "", _README),
    Decided("chips", "Chips (No Pro Pilot, Autech, Normal Pack) are applied by the reviewer after looking at the car, "
            "never by the bot.", "", _README),
    Decided("seats", "Seats are parsed from the text rules (\"2, 3, 5 or 6 seats\"); a sheet reading outside the set "
            "skips the lot.", "", _README),
    Decided("chassis_question_mark", "For a chassis with \"?\", Nichibo's guessed suffix is used, with \"confirm on the "
            "sheet\" on the checklist.", "", _README),
    Decided("same_code_two_guides", "When one code is in two guides (Serena / Serena Campervan), both rows are shown "
            "and cross-linked.", "", _README),
    Decided("year", "Year is Nichibo's \"Year of Registration\".", "", _README),
    Decided("detail_page",
            "A lot's page is opened only when the guide's variants differ by chassis suffix (GDH206V / GDH206K), a "
            "modifier depends on the colour, a skip line needs a page-only fact, or the search card lacks a fact. "
            "Placement re-opens the page and re-verifies it anyway.", "", _README),
    Decided("card_ceiling", "A lot whose start price is above the highest bid any variant could reach (cell plus every "
            "positive modifier) is skipped before anything is downloaded.", "", _README),
    Decided("cruise_ladder",
            "Cruise control is judged in this order: the sheet (yes is yes; a bare \"CC\" is not cruise control), then "
            "the steering-wheel photo, then the maker's catalogue. Adaptive cruise counts; a note that says radar or "
            "adaptive needs adaptive.", "2026-10-20", _README + "; CLAUDE.md section 2"),
    Decided("welcab_seats",
            "A Welcab must have a front swivel/rotating, front lift-up or second-row side lift-up seat. A slope/ramp or "
            "wheelchair-lift Welcab, a plain car, or a Welcab with no seat statement is not eligible. Same ladder order "
            "as cruise control.", "2026-10-20", _README + "; CLAUDE.md section 2"),
    Decided("images_not_published",
            "A lot whose images are not published yet is parked until the next scan, without opening its page. It keeps "
            "its grid amount from the card, so it can be approved; the sheet and photos, when they arrive, can only "
            "skip it or change the modifiers.", "2026-09-30", _README + "; CLAUDE.md section 2"),
)


def _confirm(qid: str, title: str, text: str, today: str, source: str = "README, Open questions for Carbarn",
             change_hint: str = "") -> Question:
    return Question(qid, title, text, source, "confirm", today=today, change_hint=change_hint)


OPEN: tuple[Question, ...] = (
    Question(
        "odo_margin", "Odometer margin",
        "The reviewer skips a lot already at the last band's cap and, for a lot exactly on a band boundary, uses the "
        "next band's value, because the odometer rises between the auction and the warehouse. What margin should the "
        "bot apply, in km?",
        "Teaching session 2026-09-15; README, Open questions for Carbarn", "setting", setting="odo_margin_km",
        choices=(Choice(0, "No margin (today)"), Choice(1000, "1,000 km"), Choice(2000, "2,000 km"),
                 Choice(3000, "3,000 km"), Choice(5000, "5,000 km")),
        today=0,
        change_hint="Does it apply to every model? Say so in a change request."),
    _confirm("interior_years", "Interior years of a build window",
             "Interior years of a build window count as inside it (registered 2020, window 07/2019 to 06/2023).",
             "Interior years count as inside the window."),
    _confirm("selected_pair", "The selected pair decides",
             "The selected pair decides the verdict even when another active pair would cover the lot.",
             "The selected pair decides the verdict, even when another active pair would cover the lot."),
    _confirm("no_selected_pair", "No selected pair",
             "With no selected pair the active pairs are alternatives, and the bot may only recommend against a lot "
             "when every pair fails it.",
             "The active pairs are alternatives; the bot recommends against a lot only when every pair fails it."),
    _confirm("model_code_unmatched", "Model code and unknown year",
             "A model code that does not match the SEV text reads \"confirm import\"; one that cannot be parsed is not "
             "checked; neither reads \"cannot import\"; an unknown registration year reads confirm.",
             "An unmatched model code reads \"confirm import\"; one that cannot be parsed is not checked (\"eligible "
             "(model code not checked)\"); neither reads \"cannot import\"; an unknown year reads confirm."),
    _confirm("welcab_chips", "Chips with Welcab or seat words",
             "Chips with Welcab or seat words (for example \"Slope type -180K\") are not judged by the ladder. How "
             "should they read?",
             "They are still judged by the model, not by the ladder.",
             change_hint="Say in a change request how they should read."),
    _confirm("welcab_refinements", "Two Welcab refinements",
             "Two refinements made during the build on 2026-10-21: the maker's catalogue alone never makes a lot a "
             "Welcab; and a negated note that names a failing seat system (\"No ramp / slope type\", \"No wheelchair "
             "lift\") is the seat requirement itself.",
             "The catalogue's single seat system decides only for a lot the sheet or photos call a Welcab; otherwise "
             "only a catalogue grade the model names that is a Welcab row decides. \"No ramp / slope type\", \"No "
             "wheelchair lift\" and \"Without wheelchair spec\" are the seat requirement, so a plain car fails it; "
             "other negated Welcab notes stay text the model judges.",
             source="README, Open questions for Carbarn; CLAUDE.md section 2 (refined 2026-10-21)",
             change_hint="Should \"Without wheelchair spec\" instead mean \"no conversion\"?"),
    _confirm("guide_213271", "Guide 213271",
             "Guide 213271 has two selected pairs whose cells overwrite each other.",
             "Not changed: the two selected pairs' cells overwrite each other."),
    _confirm("gdh206k", "The GDH206K variant",
             "The GDH206K variant is inactive on the guide although the walkthrough bids on it.",
             "The bot treats every variant as biddable; the dot is only the selected tab."),
)

_CG = "CLAUDE.md"
LOCKED: tuple[Locked, ...] = (
    Locked("Testing lock: while project_status is not exactly \"The project is finished\", placement is always a dry "
           "run, even with dry_run: false and --live. Only Jim changes that line, by hand.", _CG + " section 0"),
    Locked("A bid is placed only for a proposal Jim approved by clicking Approve on the review page. Nothing approves "
           "automatically, on a heuristic, a model verdict or a rule match.", _CG + " section 1"),
    Locked("Live placement needs both dry_run: false in config and bot place --live.", _CG + " section 1"),
    Locked("Every vehicle can be bid on at most once ever.", _CG + " section 1"),
    Locked("The bot never opens the Stock side of Nichibo, Vehicle Offers, any cart, bulk \"Place All Bids\", or the "
           "paid \"Request images from Live Support\" button.", _CG + " section 0b"),
    Locked("Scraped data is never thrown away: data/bot.sqlite, data/evidence and data/cache stay. After a rule change "
           "the stored proposals are re-judged with bot replay, with no Nichibo traffic.", _CG + " section 0c"),
    Locked("No paid model APIs beyond the ones Jim allowed; the free judge only pre-fills, and every judged lot stays "
           "for review.", _CG + " section 3"),
    Locked("Import eligibility is a review note, never a skip.", _CG + " section 3b"),
)

_BY_ID = {q.id: q for q in OPEN}


def question(qid: str) -> Question:
    """The open question with this id; KeyError when there is none (the locked rules are not questions)."""
    return _BY_ID[qid]


def valid_value(q: Question, value) -> bool:
    """A setting accepts only its own choice values (an int is not a bool); a confirm question only "keep"."""
    if q.kind == "setting":
        return not isinstance(value, bool) and any(
            type(c.value) is type(value) and c.value == value for c in q.choices)
    return value == "keep" and isinstance(value, str)


def current_answers(cfg) -> dict:
    """The newest version's answers, {} when there are none or the database does not exist yet."""
    if not cfg.db_path.exists():
        return {}
    from .store import Store
    store = Store(cfg.db_path)
    try:
        return store.rulebook_answers()
    finally:
        store.close()


# -- which waiting lots an open question touches today ---------------------------------------------------

_ELIGIBILITY_QUESTIONS = frozenset({"interior_years", "selected_pair", "no_selected_pair", "model_code_unmatched"})
_WELCAB_WORDS = re.compile(r"welcab|wheelchair|slope|ramp|swivel|rotating|lift-?up|\bseat", re.I)
_MARGIN_REACH_KM = 5000   # the largest margin the Rulebook offers


def _waiting(row: dict) -> bool:
    return row.get("decision", "pending") == "pending" and row.get("status") in ("BID", "NEEDS_REVIEW")


def _near_a_bound(row: dict, rules) -> bool:
    """The lot's listed km is within the largest margin below a band's upper bound or its variant's cap."""
    facts = row.get("facts") or {}
    km = facts.get("km")
    if not isinstance(km, int) or isinstance(km, bool):
        return False
    rule = next((r for r in rules if r.guide_id == row.get("guide_id")), None) or next(
        (r for r in rules if r.model_code == row.get("model_code")), None)
    if rule is None:
        return False
    try:
        variants = list(rule.resolve_variant(facts.get("chassis") or "", facts.get("variant_text") or "").candidates)
    except Exception:  # a lot whose variant cannot be named is judged against every variant of the rule
        variants = []
    for variant in variants or list(rule.variants):
        bounds = {cell.band.odo_to for cell in variant.cells.values()}
        bounds.add(variant.max_odo)
        if any(bound - _MARGIN_REACH_KM < km <= bound for bound in bounds if bound):
            return True
    return False


def _welcab(row: dict) -> bool:
    soft = row.get("soft") or {}
    for r in soft.get("requirements") or []:
        if r.get("topic") == "welcab_seat" or (r.get("kind") == "chip" and _WELCAB_WORDS.search(r.get("text") or "")):
            return True
    return any("welcab" in str(l.get("id") or l.get("topic") or "").lower() for l in soft.get("ladders") or [])


def _elig_verdict(row: dict) -> str:
    view = row.get("eligibility") or (row.get("soft") or {}).get("eligibility") or {}
    return view.get("verdict") or "eligible"


def affected(q: Question, rows: list[dict], rules) -> list[int]:
    """The ids of the waiting lots (BID or NEEDS_REVIEW, pending) this open question touches today, from the
    rows the desk loads (facts, soft and hard parsed) and the current rules. Not a promise of a change: the
    preview says what would change. A question with no rule of its own touches nothing."""
    waiting = [r for r in rows if _waiting(r)]
    if q.id == "odo_margin":
        picked = [r for r in waiting if _near_a_bound(r, rules)]
    elif q.id == "guide_213271":
        picked = [r for r in waiting if r.get("guide_id") == 213271]
    elif q.id == "gdh206k":
        picked = [r for r in waiting if str(r.get("model_code") or "").upper() == "GDH206K"
                  or str((r.get("facts") or {}).get("chassis") or "").upper().rstrip("?") == "GDH206K"]
    elif q.id in ("welcab_chips", "welcab_refinements"):
        picked = [r for r in waiting if _welcab(r)]
    elif q.id in _ELIGIBILITY_QUESTIONS:
        picked = [r for r in waiting if _elig_verdict(r) != "eligible"]
    else:
        picked = []
    return [r["id"] for r in picked]
