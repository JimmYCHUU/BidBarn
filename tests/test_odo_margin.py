"""Rulebook, odometer margin (Jim's teaching session, README "Odometer margin"): with a margin m the lot is
priced as if it had m more km, for the band choice and the cap only. Margin 0 is exactly today."""
import copy
import json
from pathlib import Path

import pytest

from auction_bot.carbarn.client import load_rules
from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.hard import NEEDS_REVIEW, PASS, SKIP, _bid_for_variant, card_grid_check, card_prefilter, evaluate_hard
from tests.test_pipeline_offline import cfg  # noqa: F401  (fixture)

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def base_rule():
    tree = json.loads((FIX / "guide_223_tree.json").read_text(encoding="utf-8"))
    item = next(i for i in json.loads((FIX / "guide_list_subset.json").read_text(encoding="utf-8"))["content"] if i["id"] == 223)
    return parse_guide_tree(tree, item)


def rule_with(base_rule, margin=0, drop_to=None, cap=None):
    r = copy.deepcopy(base_rule)
    r.variants = [v for v in r.variants if v.name == "GDH206V"]
    v = r.variants[0]
    if drop_to is not None:
        v.cells = {k: c for k, c in v.cells.items() if c.band.odo_to != drop_to}
        v.bands = [b for b in v.bands if b.odo_to != drop_to]
    if cap is not None:
        v.cells = {k: c for k, c in v.cells.items() if c.band.odo_to <= cap}
        v.bands = [b for b in v.bands if b.odo_to <= cap]
    r.odo_margin_km = margin
    return r


def facts(**kw) -> VehicleFacts:
    base = dict(ref_no="x", url="u", make="Toyota", model="Hiace", start_price_jpy=0, chassis="GDH206V", year=2020, grade_raw="4")
    base.update(kw)
    return VehicleFacts(**base)


def test_no_margin_is_today(base_rule):
    plain = copy.deepcopy(base_rule)
    plain.variants = [v for v in plain.variants if v.name == "GDH206V"]
    zero = rule_with(base_rule, 0)
    for km in (0, 5_000, 10_000, 10_001, 49_999, 50_000, 129_999, 130_000, 130_001, 200_000):
        assert evaluate_hard(facts(km=km), zero).to_dict() == evaluate_hard(facts(km=km), plain).to_dict()


def test_on_the_mark_uses_the_next_band(base_rule):
    r0 = evaluate_hard(facts(km=50_000), rule_with(base_rule, 0))
    r1 = evaluate_hard(facts(km=50_000), rule_with(base_rule, 1_000))
    assert r0.band_label == "40K-50K" and r1.band_label == "50K-60K"
    assert r1.status == PASS and r1.base_jpy_k < r0.base_jpy_k
    cell = [c for c in r1.checks if c.name == "cell"][0]
    assert "50,000 + 1,000 km margin" in cell.detail and "50K-60K" in cell.detail
    # 48,000 km + 1,000 stays in 40-50K
    assert evaluate_hard(facts(km=48_000), rule_with(base_rule, 1_000)).band_label == "40K-50K"


def test_at_the_cap_is_a_skip(base_rule):
    r = rule_with(base_rule, 1_000, cap=80_000)
    assert r.variants[0].max_odo == 80_000
    res = evaluate_hard(facts(km=80_000), r)
    assert res.status == SKIP
    odo = [c for c in res.checks if c.name == "odo" and not c.ok][0]
    assert "80,000 km + 1,000 km margin exceeds" in odo.detail and "cap of 80,000 km" in odo.detail
    assert evaluate_hard(facts(km=80_000), rule_with(base_rule, 0, cap=80_000)).status == PASS


def test_below_the_cap_by_more_than_the_margin_stays(base_rule):
    r = rule_with(base_rule, 2_000, cap=80_000)
    res = evaluate_hard(facts(km=77_000), r)
    assert res.status == PASS and res.band_label == "70K-80K"


def test_a_gap_takes_the_closest_band_after_the_margin(base_rule):
    r = rule_with(base_rule, 2_000, drop_to=40_000)       # no 30K-40K band
    res = evaluate_hard(facts(km=28_500), r)               # 30,500 effective: in the gap
    assert res.status == PASS and res.band_label == "20K-30K"
    note = [c for c in res.checks if c.name == "odo" and c.ok]
    assert note and note[0].detail == "no band covers 28,500 km + 2,000 km margin (30,500 km); the closest band 20K-30K is used"
    # margin 0 keeps today's wording
    r0 = evaluate_hard(facts(km=30_500), rule_with(base_rule, 0, drop_to=40_000))
    assert [c.detail for c in r0.checks if c.name == "odo" and c.ok] == ["no band covers 30,500 km; the closest band 20K-30K is used"]


@pytest.mark.parametrize("margin", [0, 2_000])
def test_unknown_km_is_review_with_or_without_margin(base_rule, margin):
    res = evaluate_hard(facts(km=None), rule_with(base_rule, margin))
    assert res.status == NEEDS_REVIEW


@pytest.mark.parametrize("km", [49_000, 50_000, 79_500, 80_000, 129_500])
def test_the_card_stage_agrees_with_the_hard_rules(base_rule, km):
    r = rule_with(base_rule, 1_000)
    v = r.variants[0]
    f = facts(km=km)
    res = evaluate_hard(f, r)
    if res.status == SKIP and any(c.name == "odo" for c in res.checks):
        assert card_prefilter(f, r) is None or card_prefilter(f, r).name == "odo"
        return
    assert card_grid_check(f, r) is None
    assert card_prefilter(f, r) is None
    # same band, same amount as the hard rules (no modifier for grade 4 is added by _bid_for_variant's own call)
    assert _bid_for_variant(v, f, 1_000) == _bid_for_variant(v, facts(km=km + 1_000))
    assert res.band_label == v.cell_for(2020, km + 1_000).band.label


def test_card_prefilter_applies_the_margin_to_the_cap(base_rule):
    r = rule_with(base_rule, 1_000, cap=80_000)
    assert card_prefilter(facts(km=80_000), r).name == "odo"
    assert card_prefilter(facts(km=79_000), r) is None
    assert card_prefilter(facts(km=80_000), rule_with(base_rule, 0, cap=80_000)) is None


def test_load_rules_takes_the_given_answers(cfg):  # noqa: F811
    assert {r.odo_margin_km for r in load_rules(cfg, answers={"odo_margin_km": 2000})[0]} == {2000}
    assert {r.odo_margin_km for r in load_rules(cfg, answers={})[0]} == {0}
    assert {r.odo_margin_km for r in load_rules(cfg)[0]} == {0}   # nothing stored


def test_cell_and_card_texts_name_the_margin(base_rule):
    r = rule_with(base_rule, 1_000)
    v = r.variants[0]
    key = next(k for k, c in v.cells.items() if c.year == 2020 and c.band.odo_to == 60_000)
    v.cells[key].no_bid = True
    res = evaluate_hard(facts(km=50_000), r)
    assert res.status == SKIP
    assert [c.detail for c in res.checks if c.name == "cell"][0].startswith(
        "grid cell 2020 / 50K-60K (50,000 km + 1,000 km margin) is empty or switched off")
    r0 = rule_with(base_rule, 0)
    r0.variants[0].cells[key].no_bid = True
    res0 = evaluate_hard(facts(km=55_000), r0)
    assert [c.detail for c in res0.checks if c.name == "cell"][0].startswith("grid cell 2020 / 50K-60K is empty or switched off")
    # card_grid_check: no live cell anywhere
    r2 = rule_with(base_rule, 1_000)
    for c in r2.variants[0].cells.values():
        if c.year == 2020:
            c.no_bid = True
    assert card_grid_check(facts(km=50_000), r2).detail == "no variant has a bid cell for 2020 / 50,000 km + 1,000 km margin"
    r3 = rule_with(base_rule, 0)
    for c in r3.variants[0].cells.values():
        if c.year == 2020:
            c.no_bid = True
    assert card_grid_check(facts(km=50_000), r3).detail == "no variant has a bid cell for 2020 / 50,000 km"


def test_the_judge_flags_and_candidates_read_the_effective_km(base_rule):
    from auction_bot.judge.candidates import candidates_for
    from auction_bot.judge.requirements import odd_flags

    def flags(km, margin):
        r = rule_with(base_rule, margin)
        f = facts(km=km)
        return odd_flags(r, evaluate_hard(f, r), f), candidates_for(r, f, evaluate_hard(f, r))

    # margin 0: unchanged
    fl, cands = flags(50_000, 0)
    assert any("exactly on the band boundary (50,000 km, band 40K-50K)" in x for x in fl)
    assert any(c.label == "next odometer band" for c in cands)
    # margin 1,000 on 50,000: the lot is already in 50-60K; no "exactly on the boundary" flag, next band is not offered
    fl, cands = flags(50_000, 1_000)
    assert not any("exactly on the band boundary" in x for x in fl)
    assert not any(c.label == "next odometer band" for c in cands)
    # margin 1,000 on 49,000: effective 50,000 is the boundary of 40-50K
    fl, cands = flags(49_000, 1_000)
    assert any("exactly on the band boundary (49,000 km + 1,000 km margin, band 40K-50K)" in x for x in fl)
    nxt = [c for c in cands if c.label == "next odometer band"]
    assert nxt and nxt[0].amount_k == evaluate_hard(facts(km=50_001), rule_with(base_rule, 0)).bid_jpy_k
    # cap proximity
    r = rule_with(base_rule, 5_000)
    f = facts(km=116_000)
    fl = odd_flags(r, evaluate_hard(f, r), f)
    assert any("within 10 % of the guide's cap (116,000 km + 5,000 km margin of 130,000 km)" in x for x in fl)


def test_load_rules_reads_the_stored_answer_by_itself(cfg):  # noqa: F811
    from auction_bot.store import Store
    Store(cfg.db_path).add_rulebook_answer("odo_margin", 2000)
    assert {r.odo_margin_km for r in load_rules(cfg)[0]} == {2000}
