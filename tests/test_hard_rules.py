"""Golden cases from Jim's walkthrough, evaluated against the captured GDH206 guide tree (its amounts are invented)."""
import json
from pathlib import Path

import pytest

from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.nichibo.facts import VehicleFacts, parse_grade
from auction_bot.rules.hard import NEEDS_REVIEW, PASS, SKIP, card_prefilter, evaluate_hard

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def gdh206():
    tree = json.loads((FIX / "guide_223_tree.json").read_text(encoding="utf-8"))
    items = json.loads((FIX / "guide_list_subset.json").read_text(encoding="utf-8"))["content"]
    item = next(i for i in items if i["id"] == 223)
    rule = parse_guide_tree(tree, item)
    # The live guide has GDH206K switched off; the walkthrough bids on K, so
    # switch it on for the tests that exercise the K grid.
    return rule


@pytest.fixture(scope="module")
def gdh206_k_on(gdh206):
    import copy
    rule = copy.deepcopy(gdh206)
    for v in rule.variants:
        v.active = True
    return rule


def facts(**kw) -> VehicleFacts:
    base = dict(ref_no="x", url="u", make="Toyota", model="Hiace", start_price_jpy=0)
    base.update(kw)
    return VehicleFacts(**base)


def test_parse_tree_basics(gdh206):
    assert gdh206.model_code == "GDH206"
    assert gdh206.min_grade == 3.5
    assert gdh206.text_rules == ["No sticker paint", "2, 3, 5 or 6 seats",
                                 "4WD, long or super long wheelbase with 2, 3, 5 or 6 seats"]
    v = gdh206.variant_by_chassis("GDH206V")
    assert v is not None and v.active
    assert v.years == [2018, 2019, 2020, 2021, 2022, 2023, 2024]
    assert v.max_odo == 130_000
    k = gdh206.variant_by_chassis("GDH206K")
    assert k is not None and not k.active
    assert k.max_odo == 160_000
    assert gdh206.search_years == (2018, 2024)
    assert gdh206.search_max_odo == 160_000  # every variant is biddable, so the search covers the K cap
    assert not gdh206.boss_bid


def test_grade_parsing():
    assert parse_grade("4") == 4.0
    assert parse_grade("3.5") == 3.5
    assert parse_grade("0") is None
    assert parse_grade("R") is None
    assert parse_grade("RA") is None
    assert parse_grade("") is None
    assert parse_grade(None) is None


def test_2019_k_42000_grade4_uses_k_grid(gdh206_k_on):
    f = facts(chassis="GDH206K", year=2019, km=42_000, grade_raw="4", start_price_jpy=0)
    r = evaluate_hard(f, gdh206_k_on)
    assert r.status == PASS
    assert r.variant_name == "GDH206K"
    assert r.band_label == "40K-50K"
    assert r.base_jpy_k == 1_560  # K grid 2019 40K-50K
    # The +80 grade-4 modifier is defined on the V variant only.
    assert r.bid_jpy_k == 1_560 and r.modifiers_applied == []


def test_2019_v_42000_grade4_matches_dashboard(gdh206):
    # The guide's 2019 / 40K-50K cell: 1,520 base and 1,600 with the +80 grade bonus.
    f = facts(chassis="GDH206V", year=2019, km=42_000, grade_raw="4")
    r = evaluate_hard(f, gdh206)
    assert r.status == PASS
    assert r.base_jpy_k == 1_520
    assert r.bid_jpy_k == 1_600


def test_2021_v_134054_over_cap_skips(gdh206):
    f = facts(chassis="GDH206V", year=2021, km=134_054, grade_raw="3.5")
    r = evaluate_hard(f, gdh206)
    assert r.status == SKIP
    assert any("exceeds" in c.detail for c in r.checks if not c.ok)


def test_2021_v_126000_grade4(gdh206):
    f = facts(chassis="GDH206V", year=2021, km=126_000, grade_raw="4", start_price_jpy=1_300_000)
    r = evaluate_hard(f, gdh206)
    assert r.status == PASS
    assert r.band_label == "120K-130K"
    assert r.base_jpy_k == 1_330
    # Open question 1 in the plan: the walkthrough bid the base value; the guide's +80 rule says 1,410.
    assert r.bid_jpy_k == 1_410


def test_grade_0_skips(gdh206):
    f = facts(chassis="GDH206V", year=2018, km=76_000, grade_raw="0")
    r = evaluate_hard(f, gdh206)
    assert r.status == SKIP
    assert r.checks[-1].name == "grade"


def test_grade_below_min_skips(gdh206):
    f = facts(chassis="GDH206V", year=2019, km=126_000, grade_raw="3")
    assert evaluate_hard(f, gdh206).status == SKIP


def test_grade_35_no_bonus(gdh206):
    f = facts(chassis="GDH206V", year=2019, km=126_000, grade_raw="3.5")
    r = evaluate_hard(f, gdh206)
    assert r.status == PASS
    assert r.bid_jpy_k == r.base_jpy_k == 1_060


def test_grade_45_and_5_get_bonus(gdh206):
    # Jim, 2026-09-14: the +80 stored as "GRADE eq 4" applies to grade 4 and above.
    for grade in ("4", "4.5", "5"):
        f = facts(chassis="GDH206V", year=2019, km=126_000, grade_raw=grade)
        r = evaluate_hard(f, gdh206)
        assert r.status == PASS
        assert r.bid_jpy_k == 1_140, grade
        assert r.modifiers_applied == ["+80K for grade >= 4 (years 2018-2021)"]


def _add_grade_modifier(rule, variant_name, grade, amount, years=(2018, 2024)):
    from auction_bot.carbarn.models import Condition, Modifier
    v = rule.variant_by_chassis(variant_name)
    v.modifiers.append(Modifier(id=9000 + len(v.modifiers), variant_id=v.id, year_from=years[0], year_to=years[1],
                                odo_from=None, odo_to=None, conditions=[Condition("GRADE", "eq", str(grade))],
                                amount_jpy_k=amount, percent=None, no_bid=False, strict=False, note="",
                                active=True, stale=False))


def test_grade_bonus_ladder_uses_highest_threshold_only(gdh206):
    import copy
    rule = copy.deepcopy(gdh206)
    _add_grade_modifier(rule, "GDH206V", 4.5, 200, years=(2018, 2021))
    r45 = evaluate_hard(facts(chassis="GDH206V", year=2019, km=126_000, grade_raw="4.5"), rule)
    assert r45.bid_jpy_k == 1_060 + 200
    r4 = evaluate_hard(facts(chassis="GDH206V", year=2019, km=126_000, grade_raw="4"), rule)
    assert r4.bid_jpy_k == 1_060 + 80


def test_negative_grade_adjustment_stays_exact(gdh206):
    # Negative bands: grade 3.5 -> -90 applies to 3.5 only, never to 4 or above.
    import copy
    rule = copy.deepcopy(gdh206)
    _add_grade_modifier(rule, "GDH206V", 3.5, -90)
    r35 = evaluate_hard(facts(chassis="GDH206V", year=2019, km=126_000, grade_raw="3.5"), rule)
    assert r35.bid_jpy_k == 1_060 - 90
    r4 = evaluate_hard(facts(chassis="GDH206V", year=2019, km=126_000, grade_raw="4"), rule)
    assert r4.bid_jpy_k == 1_060 + 80
    r45 = evaluate_hard(facts(chassis="GDH206V", year=2019, km=126_000, grade_raw="4.5"), rule)
    assert r45.bid_jpy_k == 1_060 + 80


def test_bonus_only_2018_to_2021(gdh206):
    f = facts(chassis="GDH206V", year=2022, km=5_000, grade_raw="4")
    r = evaluate_hard(f, gdh206)
    assert r.status == PASS
    assert r.bid_jpy_k == r.base_jpy_k == 2_060


def test_bid_below_start_skips(gdh206):
    f = facts(chassis="GDH206V", year=2020, km=59_000, grade_raw="4", start_price_jpy=1_690_000)
    r = evaluate_hard(f, gdh206)
    assert r.status == SKIP
    assert r.checks[-1].name == "start"


def test_bid_equal_start_passes(gdh206):
    f = facts(chassis="GDH206V", year=2020, km=59_000, grade_raw="4", start_price_jpy=1_640_000)
    r = evaluate_hard(f, gdh206)
    assert r.status == PASS and r.bid_jpy_k == 1_640


def test_secondary_tab_variant_is_biddable(gdh206):
    # The dashboard's on/off dot is the selected tab, not a bidding switch (Jim, 2026-09-14).
    f = facts(chassis="GDH206K", year=2019, km=42_000, grade_raw="4")
    r = evaluate_hard(f, gdh206)
    assert r.status == PASS and r.variant_name == "GDH206K" and r.bid_jpy_k == 1_560
    assert "not the tab selected" in next(c.detail for c in r.checks if c.name == "variant")


def test_unknown_chassis_skips(gdh206):
    f = facts(chassis="GDH206Z", year=2019, km=42_000, grade_raw="4")
    assert evaluate_hard(f, gdh206).status == SKIP


def test_missing_chassis_needs_review(gdh206):
    f = facts(chassis="", year=2019, km=42_000, grade_raw="4")
    assert evaluate_hard(f, gdh206).status == NEEDS_REVIEW


def test_year_without_row_skips(gdh206):
    f = facts(chassis="GDH206V", year=2017, km=42_000, grade_raw="4")
    r = evaluate_hard(f, gdh206)
    assert r.status == SKIP and r.checks[-1].name == "year"


def test_band_upper_bound_inclusive(gdh206):
    f50 = facts(chassis="GDH206V", year=2019, km=50_000, grade_raw="3.5")
    f50001 = facts(chassis="GDH206V", year=2019, km=50_001, grade_raw="3.5")
    assert evaluate_hard(f50, gdh206).band_label == "40K-50K"
    assert evaluate_hard(f50001, gdh206).band_label == "50K-60K"


def test_existing_bid_skips(gdh206):
    f = facts(chassis="GDH206V", year=2019, km=42_000, grade_raw="4", has_current_bid=True, current_bid_jpy=1_600_000)
    r = evaluate_hard(f, gdh206)
    assert r.status == SKIP and r.checks[-1].name == "duplicate"


def test_card_prefilter(gdh206):
    assert card_prefilter(facts(year=2018, km=76_000, grade_raw="0"), gdh206).name == "grade"
    assert card_prefilter(facts(year=2017, km=10_000, grade_raw="4"), gdh206).name == "year"
    assert card_prefilter(facts(year=2019, km=155_000, grade_raw="4"), gdh206) is None  # K cap is 160K
    assert card_prefilter(facts(year=2019, km=165_000, grade_raw="4"), gdh206).name == "odo"
    assert card_prefilter(facts(year=2019, km=42_000, grade_raw="4"), gdh206) is None


def test_uncertain_chassis_is_flagged_not_rejected(gdh206):
    from auction_bot.nichibo.parsers import parse_detail_page
    html = (FIX / "detail_gdh206v.html").read_text(encoding="utf-8").replace("<strong>GDH206V</strong>", "<strong>GDH206V?</strong>")
    f = parse_detail_page(html, "https://autosearch.nichibojapan.com/auction/REF")
    assert f.chassis == "GDH206V" and f.chassis_uncertain
    r = evaluate_hard(f, gdh206)
    assert r.status == PASS and r.variant_how == "chassis-uncertain"
    assert "confirm" in next(c.detail for c in r.checks if c.name == "variant")
    f.chassis, f.chassis_uncertain = "GDH206K", True
    r = evaluate_hard(f, gdh206)
    assert r.status == PASS and r.variant_name == "GDH206K"


def test_allowed_seats_parsing(gdh206):
    import copy
    from auction_bot.rules.hard import allowed_seats
    assert allowed_seats(gdh206) == {2, 3, 5, 6}
    r = copy.deepcopy(gdh206)
    r.text_rules = ["Captain Seat, Leather, Black Color Seat"]
    assert allowed_seats(r) is None
    r.text_rules = ["8 seater only"]
    assert allowed_seats(r) == {8}
    r.text_rules = ["5/7 seats", "White Minimum 4"]
    assert allowed_seats(r) == {5, 7}
    r.text_rules = []
    r.override_text = "- (2026-09-14, jim) 5 or 7 seats only"
    assert allowed_seats(r) == {5, 7}


def test_seats_from_sheet_decide(gdh206):
    # Jim's walkthrough: the 2019 K vans read 10 seats on the sheet and must be skipped for that reason.
    ten = facts(chassis="GDH206K", year=2019, km=42_000, grade_raw="4", seats=10)
    r = evaluate_hard(ten, gdh206)
    assert r.status == SKIP and r.checks[-1].name == "seats" and "10 seats" in r.checks[-1].detail
    six = facts(chassis="GDH206K", year=2019, km=42_000, grade_raw="4", seats=6)
    r = evaluate_hard(six, gdh206)
    assert r.status == PASS and any(c.name == "seats" and c.ok for c in r.checks)
    unknown = facts(chassis="GDH206K", year=2019, km=42_000, grade_raw="4")
    assert evaluate_hard(unknown, gdh206).status == PASS  # the reviewer reads the sheet


def test_variant_text_and_colour_conditions_and_toggles(gdh206):
    import copy
    from auction_bot.carbarn.models import Condition, Modifier
    rule = copy.deepcopy(gdh206)
    v = rule.variant_by_chassis("GDH206V")

    def mod(mid, conds, amount, note):
        return Modifier(id=mid, variant_id=v.id, year_from=None, year_to=None, odo_from=None, odo_to=None,
                        conditions=conds, amount_jpy_k=amount, percent=None, no_bid=False, strict=False,
                        note=note, active=True, stale=False)
    v.modifiers.append(mod(9101, [Condition("MODEL_VARIANT", "in", "", ["HYBRID Z HONDA SENSING", "Z HONDA SENSING"])], 90, "Z HONDA SENSING"))
    v.modifiers.append(mod(9102, [Condition("COLOR", "in", "", ["Red", "Yellow"])], -60, ""))
    v.modifiers.append(mod(9103, [], -220, "No Pro Pilot"))  # dashboard chip: reviewer applies it by hand

    plain = facts(chassis="GDH206V", year=2022, km=5_000, grade_raw="3.5", variant_text="Van DX", colour="White")
    r = evaluate_hard(plain, rule)
    assert r.status == PASS and r.bid_jpy_k == 2_060  # chip never applied, conditions not met
    assert r.toggles == ["No Pro Pilot -220K"]
    assert any(c.name == "toggle" and "not applied" in c.detail for c in r.checks)

    z = facts(chassis="GDH206V", year=2022, km=5_000, grade_raw="3.5", variant_text="Hybrid Z Honda Sensing", colour="Red")
    assert evaluate_hard(z, rule).bid_jpy_k == 2_060 + 90 - 60

    no_colour = facts(chassis="GDH206V", year=2022, km=5_000, grade_raw="3.5", variant_text="Van DX", colour="")
    r = evaluate_hard(no_colour, rule)
    assert r.status == PASS and r.deferred_modifiers and r.bid_jpy_k == 2_060  # unknown colour: deferred to the reviewer
