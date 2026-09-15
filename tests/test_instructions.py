"""Structured standing instructions: grammar, evaluation, and where the scan applies them."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.nichibo.parsers import parse_detail_page
from auction_bot.pipeline import needs_detail, reevaluate, scan
from auction_bot.review.app import create_app
from auction_bot.rules.hard import SKIP, allowed_seats, evaluate_hard
from auction_bot.rules.instructions import (InstructionError, evaluate_condition, fires, instruction_check, is_structured,
                                            parse_instruction, split_override_lines, suggest_from_reason)
from auction_bot.store import Store
from tests.test_pipeline_offline import FIX, FakeBrowser, cfg  # noqa: F401  (fixture)


def _gdh206(override: str = ""):
    tree = json.loads((FIX / "guide_223_tree.json").read_text(encoding="utf-8"))
    item = next(i for i in json.loads((FIX / "guide_list_subset.json").read_text(encoding="utf-8"))["content"] if i["id"] == 223)
    rule = parse_guide_tree(tree, item)
    rule.override_text = override
    return rule


def _facts(**kw) -> VehicleFacts:
    base = dict(ref_no="r", url="u", year=2020, make="Toyota", model="Hiace", variant_text="Van 5d 4wd DX",
                km=122_000, grade_raw="4", start_price_jpy=1_110_000, source="card")
    base.update(kw)
    return VehicleFacts(**base)


# -- grammar --------------------------------------------------------------------------

def test_parse_text_and_number_conditions():
    i = parse_instruction("skip: variant contains DUMP")
    assert [c.describe() for c in i.conditions] == ["variant contains DUMP"]
    i = parse_instruction("skip: hall USS Kyushu")  # no operator: contains for text
    assert i.conditions[0].op == "contains" and i.conditions[0].value == "USS Kyushu"
    assert parse_instruction("skip: km > 120k").conditions[0].value == 120_000
    assert parse_instruction("skip: km > 120,000").conditions[0].value == 120_000
    assert parse_instruction("skip: seats 10").conditions[0].op == "="  # no operator: equals for numbers
    i = parse_instruction("skip: start >= bid")
    assert i.conditions[0].ref_attr == "bid" and i.conditions[0].value is None
    i = parse_instruction("Skip: colour is white AND year < 2019")
    assert len(i.conditions) == 2 and i.conditions[0].op == "is" and i.conditions[1].value == 2019
    assert parse_instruction('skip: variant is "Van DX"').conditions[0].value == "Van DX"
    assert parse_instruction("skip: variant not contains 4WD").conditions[0].op == "not contains"
    assert parse_instruction("skip: chassis starts with GDH206K").needs_detail
    assert not parse_instruction("skip: variant contains DX").needs_detail


@pytest.mark.parametrize("line", [
    "bid: 100", "approve: grade 4", "skip:", "skip: foo > 1", "skip: km contains x", "skip: variant > 3",
    "skip: km > abc", "skip: km > variant", "just words", "skip: > 3",
])
def test_bad_lines_are_refused(line):
    with pytest.raises(InstructionError):
        parse_instruction(line)


def test_split_override_lines_keeps_free_text_and_reports_errors():
    rule = _gdh206("# notes\n\n- (2026-09-14, jim) Skip ambulance conversions\n- (2026-09-15, review) skip: variant contains DUMP\n"
                   "- (2026-09-15, review) skip: nonsense field\n")
    free, ins, errors = split_override_lines(rule)
    assert free == ["Skip ambulance conversions"]
    assert [i.raw for i in ins] == ["skip: variant contains DUMP"]
    assert len(errors) == 1 and "unknown field" in errors[0]
    assert is_structured("  SKIP: x") and not is_structured("skipping is fine")
    # Structured seat lines are not read as seat allow-lists.
    assert allowed_seats(_gdh206("- (2026-09-15, r) skip: seats > 6\n")) == {2, 3, 5, 6}


# -- evaluation -------------------------------------------------------------------------

def test_conditions_on_unknown_facts_are_undecided():
    card = _facts()  # chassis, colour, seats unknown on a card
    assert evaluate_condition(parse_instruction("skip: chassis contains V").conditions[0], card) is None
    assert evaluate_condition(parse_instruction("skip: colour is white").conditions[0], card) is None
    assert evaluate_condition(parse_instruction("skip: seats > 6").conditions[0], card) is None
    assert evaluate_condition(parse_instruction("skip: start >= bid").conditions[0], card) is None  # no bid yet
    assert not fires(parse_instruction("skip: variant contains DX and colour is white"), card)


def test_conditions_decidable_from_the_card():
    card = _facts()
    assert fires(parse_instruction("skip: variant contains dx"), card)  # case-insensitive
    assert not fires(parse_instruction("skip: variant contains DUMP"), card)
    assert fires(parse_instruction("skip: variant not contains DUMP"), card)
    assert fires(parse_instruction("skip: km > 120000"), card) and not fires(parse_instruction("skip: km > 122000"), card)
    assert fires(parse_instruction("skip: km >= 122k"), card)
    assert fires(parse_instruction("skip: year < 2021 and grade >= 4"), card)
    assert fires(parse_instruction("skip: start > 1,000,000"), card)
    assert fires(parse_instruction("skip: make is toyota"), card) and fires(parse_instruction("skip: model = Hiace"), card)


def test_conditions_with_detail_facts_and_bid():
    f = _facts(chassis="GDH206V", colour="White", auction_place="CAA Gifu", seats=10, source="detail")
    assert fires(parse_instruction("skip: colour is white"), f) and not fires(parse_instruction("skip: colour is not white"), f)
    assert fires(parse_instruction("skip: hall CAA"), f) and not fires(parse_instruction("skip: hall USS Kyushu"), f)
    assert fires(parse_instruction("skip: seats > 6"), f) and fires(parse_instruction("skip: seats 10"), f)
    assert fires(parse_instruction("skip: chassis starts with gdh206"), f)
    # start (yen) against bid (K JPY): 1,110,000 vs 1,060K fires, vs 1,240K does not.
    ins = parse_instruction("skip: start >= bid")
    assert fires(ins, f, bid_jpy_k=1_060) and not fires(ins, f, bid_jpy_k=1_240)
    hit = instruction_check(_gdh206("- (2026-09-15, r) skip: seats > 6\n"), f)
    assert hit is not None and hit.name == "instruction" and "skip: seats > 6" in hit.detail
    assert instruction_check(_gdh206("- (2026-09-15, r) skip: seats > 12\n"), f) is None


def test_hard_rules_apply_instructions_before_and_after_the_bid():
    html = (FIX / "detail_gdh206v.html").read_text(encoding="utf-8")
    f = parse_detail_page(html, "https://autosearch.nichibojapan.com/auction/x")
    ok = evaluate_hard(f, _gdh206())
    assert ok.bid_jpy_k
    r = evaluate_hard(f, _gdh206("- (2026-09-15, r) skip: colour is white\n"))
    assert r.status == SKIP and r.checks[0].name == "instruction" and r.bid_jpy_k is None
    r = evaluate_hard(f, _gdh206(f"- (2026-09-15, r) skip: start >= bid\n"))
    assert r.status != SKIP  # start 1,110,000 is below the 1,240K bid
    f.start_price_jpy = 2_000_000
    r = evaluate_hard(f, _gdh206("- (2026-09-15, r) skip: start >= bid\n"))
    assert r.status == SKIP and r.checks[-1].name == "instruction" and r.bid_jpy_k is not None


def test_needs_detail_for_instructions_on_page_facts():
    plain = _gdh206()
    plain.variants = [plain.variants[0]]
    plain.variants[0].name = ""
    card = _facts(sheet_url="s")
    assert not needs_detail(plain, card)[0]
    plain.override_text = "- (2026-09-15, r) skip: variant contains DUMP\n"
    assert not needs_detail(plain, card)[0]
    plain.override_text = "- (2026-09-15, r) skip: colour is white\n"
    need, why = needs_detail(plain, card)
    assert need and "standing instruction" in why


# -- suggestions ---------------------------------------------------------------------------

def test_suggest_from_reason():
    f = _facts(variant_text="Van DUMP 4WD", colour="White", auction_place="USS Tokyo", equipment="ps pw")
    assert suggest_from_reason("dump body, not a van", f) == "skip: variant contains DUMP"
    assert suggest_from_reason("start price above our bid", f) == "skip: start >= bid"
    assert suggest_from_reason("122,000 km is too many", f) == "skip: km >= 122000"
    assert suggest_from_reason("150000 km", f) is None  # the lot has fewer km than the reason names
    assert suggest_from_reason("10 seats", _facts(seats=10)) == "skip: seats = 10"
    assert suggest_from_reason("uss tokyo lots are always rough", f) == "skip: hall contains uss"
    assert suggest_from_reason("white cars do not sell", _facts(colour="White")) == "skip: colour is White"
    assert suggest_from_reason("", f) is None and suggest_from_reason("looks tired", f) is None


# -- in the scan ---------------------------------------------------------------------------

def _add_line(cfg, text):
    with (cfg.override_dir / "GDH206.md").open("a", encoding="utf-8") as fh:
        fh.write(f"- (2026-09-15, review) {text}\n")


def test_card_stage_instruction_opens_nothing(cfg):
    _add_line(cfg, "skip: variant contains DARK PRIME2")
    _add_line(cfg, "skip: nonsense field 3")  # ignored with a warning, never stops the scan
    browser = FakeBrowser()
    store = Store(cfg.db_path)
    stats = scan(cfg, browser, store, ["GDH206"])  # no limit: every card is checked
    assert stats.errors == []
    rows = store.list_proposals(decision="pending", status="SKIP", limit=1000)
    hits = [r for r in rows if any(c["name"] == "instruction" for c in json.loads(r["hard_json"])["checks"])]
    assert len(hits) >= 5 and all(r["stage"] == "card" for r in hits)  # the DARK PRIME2 cards in the fixture
    assert all("DARK PRIME2" in json.loads(r["facts_json"])["variant_text"] for r in hits)
    assert len([u for u in browser.visited if "/auction/" in u]) == stats.pages > 0
    soft = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])
    assert any(c.startswith("Auto-skip rule in force") and "DARK PRIME2" in c for c in soft["checklist"])
    assert any(c.startswith("Skip line NOT understood") for c in soft["checklist"])
    store.close()


def test_seat_instruction_fires_at_the_sheet_stage(cfg, monkeypatch):
    import auction_bot.pipeline as pl
    from auction_bot.nichibo.sheet_ocr import SheetRead
    _add_line(cfg, "skip: seats > 6")
    monkeypatch.setattr(pl, "read_sheet", lambda path: SheetRead(available=True, seats=10, model_code="GDH206V"))
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    assert stats.review == 0 and stats.skip >= 1
    row = next(r for r in store.list_proposals(decision="pending", status="SKIP", limit=1000) if r["stage"] == "sheet")
    assert any(c["name"] in ("instruction", "seats") for c in json.loads(row["hard_json"])["checks"])
    store.close()


def test_replay_applies_a_new_instruction_offline(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    assert len(store.list_proposals(decision="pending", status="NEEDS_REVIEW")) == 2
    _add_line(cfg, "skip: colour is white")  # the detail fixture is white
    dry = reevaluate(cfg, store, ["GDH206"], apply=False)
    assert dry.changed == 2 and dry.applied == 0
    st = reevaluate(cfg, store, ["GDH206"], apply=True)
    assert st.applied == 2 and store.list_proposals(decision="pending", status="NEEDS_REVIEW") == []
    skipped = [r for r in store.list_proposals(decision="pending", status="SKIP", limit=1000) if r["stage"] == "hard"]
    assert len(skipped) == 2 and all("standing instruction" in r["hard_json"] for r in skipped)
    store.close()


def test_review_page_stores_validated_skip_lines(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    pid = store.list_proposals(decision="pending")[0]["id"]
    store.close()
    client = TestClient(create_app(cfg))
    r = client.post("/rules/gdh206/skip", data={"line": "variant contains DUMP", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    text = (cfg.override_dir / "GDH206.md").read_text(encoding="utf-8")
    assert "review) skip: variant contains DUMP" in text
    r = client.post("/rules/GDH206/skip", data={"line": "skip: wheels > 3"}, follow_redirects=False)
    assert r.status_code == 400 and "wheels" in text or "wheels" not in (cfg.override_dir / "GDH206.md").read_text(encoding="utf-8")
    # The suggestion endpoint reads the lot's facts.
    r = client.get(f"/p/{pid}/suggest", params={"reason": "start above bid"})
    assert r.status_code == 200 and r.json()["line"] == "skip: start >= bid"
    # A malformed structured instruction on the decide form is refused before anything is written.
    r = client.post(f"/p/{pid}/decide", data={"action": "reject", "reason": "x", "make_rule": "1", "rule_text": "skip: foo 1"},
                    follow_redirects=False)
    assert r.status_code == 400
    r = client.post(f"/p/{pid}/decide", data={"action": "reject", "reason": "dump body", "make_rule": "1",
                                              "rule_text": "skip: variant contains DUMP"}, follow_redirects=False)
    assert r.status_code == 303
    assert (cfg.override_dir / "GDH206.md").read_text(encoding="utf-8").count("skip: variant contains DUMP") == 2


def test_cli_skip_line(cfg, monkeypatch):
    from typer.testing import CliRunner
    import auction_bot.cli as cli
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    runner = CliRunner()
    res = runner.invoke(cli.app, ["skip-line", "gdh206", "km > 120k"])
    assert res.exit_code == 0 and "skip: km > 120k" in res.output
    assert "cli) skip: km > 120k" in (cfg.override_dir / "GDH206.md").read_text(encoding="utf-8")
    res = runner.invoke(cli.app, ["skip-line", "gdh206", "approve: everything"])
    assert res.exit_code == 2 and "Not stored" in res.output
    res = runner.invoke(cli.app, ["rules", "GDH206"])
    assert res.exit_code == 0 and "auto-skip: skip: km > 120k" in res.output
