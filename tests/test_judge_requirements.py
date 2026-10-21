"""Requirement classification, odd-case flags, amount candidates and the playbook (no model, no network)."""
import copy
import json
from pathlib import Path

from auction_bot.carbarn.models import Condition, Modifier, parse_guide_tree
from auction_bot.config import Config
from auction_bot.judge.candidates import candidates_for
from auction_bot.judge.playbook import (GENERAL_SEED, append_playbook, code_lines, general_lines, playbook_for,
                                        similar_decisions)
from auction_bot.judge.requirements import (classify, needs_judge, needs_photos, odd_flags, requirements_for,
                                            requirements_hash)
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.hard import evaluate_hard
from auction_bot.store import Store

FIX = Path(__file__).parent / "fixtures"


def _gdh206():
    tree = json.loads((FIX / "guide_223_tree.json").read_text(encoding="utf-8"))
    item = next(i for i in json.loads((FIX / "guide_list_subset.json").read_text(encoding="utf-8"))["content"] if i["id"] == 223)
    return parse_guide_tree(tree, item)


def _facts(**kw) -> VehicleFacts:
    base = dict(ref_no="r", url="u", year=2020, make="Toyota", model="Hiace", variant_text="Van DX GL Package 4WD",
                chassis="GDH206V", km=122_000, grade_raw="4", start_price_jpy=1_110_000)
    base.update(kw)
    return VehicleFacts(**base)


def _chip(note, amount):
    return Modifier(id=99, variant_id=None, year_from=None, year_to=None, odo_from=None, odo_to=None, conditions=[],
                    amount_jpy_k=amount, percent=None, no_bid=False, strict=False, note=note, active=True, stale=False)


def test_classify_golden_cases():
    assert classify("Dark Color Seats Only") == "photos"
    assert classify("Only Dark Coloured Seats (Not Beige)") == "photos"
    assert classify("Captain Seat, Leather, Black Color Seat") == "photos"
    assert classify("No sticker paint") == "photos"
    assert classify("S-Hybrid 2 littre 8 seater") == "sheet"
    assert classify("2, 3, 5 or 6 seats") == "sheet"
    assert classify("Must be 4WD/AWD") == "sheet"
    assert classify("4WD, long or super long wheelbase with 2, 3, 5 or 6 seats") == "sheet"
    assert classify("White Minimum 4 | Other Colours: Minimum Grade 4.5") == "card"
    assert classify("No Pro Pilot") == "photos" and classify("Autech") == "photos"
    assert classify("Internal campervan fit out") == "any"


def test_requirements_for_guide_with_chips():
    rule = _gdh206()
    rule.override_text = "- (2026-09-15, jim) Skip ambulance conversions\n- (2026-09-15, jim) skip: variant contains DUMP\n"
    rule.variants[0].modifiers.append(_chip("No Pro Pilot", -220))
    f = _facts()
    hard = evaluate_hard(f, rule)
    reqs = requirements_for(rule, hard)
    ids = [r.id for r in reqs]
    assert ids[:3] == ["r1", "r2", "r3"] and "chip:no-pro-pilot" in ids and "note:1" in ids and not any("DUMP" in r.text for r in reqs)
    chip = next(r for r in reqs if r.kind == "chip")
    assert chip.amount_k == -220 and chip.source == "photos" and chip.text == "No Pro Pilot"
    assert next(r for r in reqs if r.id == "r1").text == "No sticker paint" and next(r for r in reqs if r.id == "r1").source == "photos"
    assert needs_photos(reqs) and needs_judge(reqs, [])
    h1 = requirements_hash(reqs, [])
    assert h1 == requirements_hash(reqs, []) and h1 != requirements_hash(reqs, ["odd"])


def test_routine_lot_needs_no_model():
    rule = _gdh206()
    rule.text_rules = ["2, 3, 5 or 6 seats"]  # decidable from the sheet's seat count
    hard = evaluate_hard(_facts(), rule)
    reqs = requirements_for(rule, hard)
    assert not needs_photos(reqs) and not needs_judge(reqs, [])
    assert needs_judge(reqs, ["odometer exactly on the band boundary"])


def test_odd_flags():
    rule = _gdh206()
    f = _facts(km=120_000)  # 110K-120K band: exactly on the boundary
    hard = evaluate_hard(f, rule)
    flags = odd_flags(rule, hard, f)
    assert any("exactly on the band boundary" in x and "120,000 km" in x for x in flags)
    f2 = _facts(km=128_000)  # V cap is 130,000: within 10 %
    flags = odd_flags(rule, evaluate_hard(f2, rule), f2)
    assert any("within 10 % of the guide's cap" in x for x in flags)
    f3 = _facts(km=60_000, chassis_uncertain=True, translator={"VehicleCondition": "Interior stains, light scratches"})
    flags = odd_flags(rule, evaluate_hard(f3, rule), f3, ocr_hints=[{"level": "warn", "message": "sheet grade reads 3.5, listing says 4"}],
                      hints=[{"level": "warn", "message": "Welcab / wheelchair conversion mentioned"}, {"level": "info", "message": "4WD mentioned"}])
    joined = " | ".join(flags)
    assert "guessed the chassis" in joined and "sheet disagrees" in joined and "Welcab" in joined and "sheet notes" in joined
    assert "4WD mentioned" not in joined
    assert odd_flags(rule, evaluate_hard(_facts(km=65_000), rule), _facts(km=65_000)) == []  # mid-band, nothing odd


def test_candidates_grid_chips_and_next_band():
    rule = _gdh206()
    rule.variants[0].modifiers.append(_chip("Autech", 170))
    f = _facts(km=120_000)
    hard = evaluate_hard(f, rule)
    cands = candidates_for(rule, f, hard)
    labels = {c.label: c.amount_k for c in cands}
    assert labels["grid"] == hard.bid_jpy_k
    assert labels["grid +170K (Autech)"] == hard.bid_jpy_k + 170
    nxt = labels["next odometer band"]
    assert nxt < hard.bid_jpy_k  # the 120K-130K cell is cheaper than 110K-120K
    assert len({c.amount_k for c in cands}) == len(cands)  # no duplicates
    # At the very cap there is no next band, so no such candidate.
    f2 = _facts(km=130_000)
    cands2 = candidates_for(rule, f2, evaluate_hard(f2, rule))
    assert "next odometer band" not in {c.label for c in cands2} and {c.label for c in cands2} >= {"grid"}
    # Mid-band (bands are upper-inclusive, so 60,000 would be a boundary), no chip applying: just the grid.
    f3 = _facts(km=65_000)
    r3 = copy.deepcopy(_gdh206())
    assert [c.label for c in candidates_for(r3, f3, evaluate_hard(f3, r3))] == ["grid"]


def test_playbook_files_and_context(tmp_path):
    cfg = Config()
    cfg.playbook_dir = tmp_path / "playbook"
    cfg.override_dir = tmp_path / "overrides"
    cfg.db_path = tmp_path / "bot.sqlite"
    lines = general_lines(cfg)
    assert (cfg.playbook_dir / "general.md").read_text(encoding="utf-8") == GENERAL_SEED
    assert any("odometer keeps rising" in ln for ln in lines) and all(not ln.startswith("#") for ln in lines)
    append_playbook(cfg, "gdh206", "  dump bodies are never  wanted ", author="review")
    append_playbook(cfg, "GENERAL", "always check the sheet date")
    assert code_lines(cfg, "GDH206") == ["(" + __import__("datetime").date.today().isoformat() + ", review) dump bodies are never wanted"]
    assert general_lines(cfg)[-1].endswith("always check the sheet date")
    # Similar decisions come first, and learn-mode notes are joined through the vehicle's model code.
    store = Store(cfg.db_path)
    rule = _gdh206()
    def facts(ref, year, km, grade="4"):
        return {"ref_no": ref, "url": "u", "year": year, "make": "Toyota", "model": "Hiace", "variant_text": "Van DX",
                "km": km, "grade_raw": grade, "grade": float(grade), "chassis": "GDH206V"}
    for ref, year, km in (("A", 2020, 121_000), ("B", 2018, 40_000), ("C", 2020, 60_000)):
        store.upsert_vehicle(ref, "GDH206", facts(ref, year, km))
        pid = store.add_proposal(ref_no=ref, guide_id=223, model_code="GDH206", snapshot="s", rule_updated_at="u", status="NEEDS_REVIEW",
                                 stage="manual", bid_jpy_k=1000, summary=f"{year} Toyota Hiace {ref}", facts_json=facts(ref, year, km),
                                 hard_json={"checks": []}, soft_json={})
        store.decide(pid, "reject" if ref != "C" else "approve", f"reason {ref}" if ref != "C" else None, 1000 if ref == "C" else None, False)
    store.add_manual_actions("s1", [("A", "reason", None, None, "2026-09-15T10:00:00", "would skip, already 120k odo")])
    ctx = playbook_for(store, cfg, rule, _facts(km=122_000, year=2020))
    assert ctx.examples[0].startswith("rejected 2020 Toyota Hiace A")  # same year and km band
    assert any("would skip, already 120k odo [2020 Van DX, 121,000 km, grade 4]" in n for n in ctx.code_notes)
    text = ctx.to_text()
    assert "# How the buyer decides" in text and "# His comments and corrections on this model code" in text and "# His decisions on similar lots" in text
    assert len(ctx.to_text(max_chars=300)) <= 300
    store.close()


def test_sheet_requirements_the_ocr_cannot_read_need_the_model():
    from auction_bot.judge.requirements import Requirement, machine_decided
    seats = Requirement("r1", "2, 3, 5 or 6 seats", "sheet", "must")
    hybrid = Requirement("r2", "Hybrid Only", "sheet", "must")
    engine = Requirement("r3", "S-Hybrid 2 littre 8 seater", "sheet", "must")
    assert machine_decided(seats) and not machine_decided(hybrid) and not machine_decided(engine)
    assert machine_decided(Requirement("r4", "8 seater only", "sheet", "must"))
    assert not needs_judge([seats], []) and needs_judge([hybrid], []) and needs_judge([engine], [])
    assert not needs_judge([Requirement("r5", "White Minimum 4", "card", "must")], [])


def test_eligibility_texts_become_requirements_and_flags():
    from datetime import date
    from auction_bot.rules.eligibility import check_lot
    rule = _gdh206()
    f = _facts(year=2020)
    hard = evaluate_hard(f, rule)
    elig = check_lot(rule, rule.variants[0], f, None, date(2026, 10, 20))
    reqs = requirements_for(rule, hard, eligibility=elig)
    sev = next(r for r in reqs if r.id == "elig:sev")
    assert sev.kind == "eligibility" and sev.source == "sheet" and "4x4 Campervan only" in sev.text and sev.text.startswith("SEV-000813")
    assert [r.id for r in reqs].index("elig:sev") < [r.id for r in reqs].index("note:1") if any(r.id == "note:1" for r in reqs) else True
    assert needs_judge(reqs, [])
    flags = odd_flags(rule, hard, f, eligibility=elig)
    assert not any(x.startswith("import approval") for x in flags)   # GDH206: codes unparseable = info, no flag
    alphard = parse_guide_tree(json.loads((FIX / "guide_AYH30W_Alphard_tree.json").read_text(encoding="utf-8")),
                               json.loads((FIX / "guide_AYH30W_Alphard_item.json").read_text(encoding="utf-8")))
    fa = _facts(year=2021, make="Toyota", model="Alphard", chassis="AGH30W", variant_text="2.5 X")
    ea = check_lot(alphard, alphard.variants[0], fa, None, date(2026, 10, 20))
    assert any(x.startswith("import approval model_code unclear")
               for x in odd_flags(alphard, evaluate_hard(fa, alphard), fa, eligibility=ea))
