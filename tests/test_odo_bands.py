"""Jim, 2026-09-30: 0 km is the lowest band; a km that no band covers takes the closest band; a lot
parked for unpublished images still carries its grid amount so it can be approved."""
import copy
import json
from pathlib import Path

import pytest

from auction_bot.carbarn.models import OdoBand, parse_guide_tree
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.pipeline import pending_images_proposal
from auction_bot.rules.hard import NEEDS_REVIEW, PASS, SKIP, card_grid_check, evaluate_hard

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def gdh206():
    tree = json.loads((FIX / "guide_223_tree.json").read_text(encoding="utf-8"))
    item = next(i for i in json.loads((FIX / "guide_list_subset.json").read_text(encoding="utf-8"))["content"] if i["id"] == 223)
    return parse_guide_tree(tree, item)


@pytest.fixture
def grid_from_10k(gdh206):
    """The same guide with the 0K-10K band removed from GDH206V: the grid then starts at 10K."""
    rule = copy.deepcopy(gdh206)
    v = next(x for x in rule.variants if x.name == "GDH206V")
    v.cells = {k: c for k, c in v.cells.items() if c.band.odo_to != 10_000}
    v.bands = [b for b in v.bands if b.odo_to != 10_000]
    return rule


def facts(**kw) -> VehicleFacts:
    base = dict(ref_no="x", url="u", make="Toyota", model="Hiace", start_price_jpy=0)
    base.update(kw)
    return VehicleFacts(**base)


def test_zero_km_is_the_lowest_band(gdh206):
    assert OdoBand(0, 10_000).contains(0) and not OdoBand(10_000, 20_000).contains(0)
    r = evaluate_hard(facts(chassis="GDH206V", year=2020, km=0, grade_raw="4"), gdh206)
    assert r.status == PASS and r.band_label == "0K-10K" and r.base_jpy_k == 1_840


def test_a_km_no_band_covers_takes_the_closest_band(grid_from_10k):
    for km in (0, 5_000):
        r = evaluate_hard(facts(chassis="GDH206V", year=2020, km=km, grade_raw="4"), grid_from_10k)
        assert r.status == PASS and r.band_label == "10K-20K" and r.base_jpy_k == 1_810
        note = [c for c in r.checks if c.name == "odo" and c.ok]
        assert note and "closest band 10K-20K" in note[0].detail
    # a km inside a band is untouched
    r = evaluate_hard(facts(chassis="GDH206V", year=2020, km=15_000, grade_raw="4"), grid_from_10k)
    assert r.band_label == "10K-20K" and not any("closest" in c.detail for c in r.checks)


def test_above_the_last_band_stays_a_skip(grid_from_10k):
    r = evaluate_hard(facts(chassis="GDH206V", year=2020, km=200_000, grade_raw="4"), grid_from_10k)
    assert r.status == SKIP and any("exceeds" in c.detail for c in r.checks if not c.ok)


def test_the_card_check_uses_the_closest_band_too(grid_from_10k):
    assert card_grid_check(facts(chassis="GDH206", year=2020, km=5_000, grade_raw="4"), grid_from_10k) is None


def test_a_parked_lot_carries_its_grid_amount(gdh206):
    # A guide with one unnamed default variant, as most guides have: the bare model code on the card resolves to it.
    rule = copy.deepcopy(gdh206)
    rule.variants = [v for v in rule.variants if v.name == "GDH206V"]
    rule.variants[0].name = ""
    card = facts(ref_no="P1", chassis="GDH206", year=2020, km=0, grade_raw="4", start_price_jpy=500_000)
    p = pending_images_proposal(rule, card, {"dir": "d", "sheet": None, "photos": [], "images_pending": True})
    assert p["status"] == NEEDS_REVIEW and p["soft_json"]["images_pending"] is True
    assert p["base_jpy_k"] == 1_840 and p["bid_jpy_k"] == 1_920  # 0K-10K for 2020, plus the grade-4 bonus
    assert any(c["name"] == "cell" and c["ok"] for c in p["hard_json"]["checks"])
