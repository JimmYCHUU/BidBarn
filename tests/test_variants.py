"""Variant resolution and model matching against real guides from the 2026-09-14 snapshot."""
import json
from pathlib import Path

import pytest

from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.pipeline import model_matches
from auction_bot.rules.hard import NEEDS_REVIEW, PASS, SKIP, evaluate_hard

FIX = Path(__file__).parent / "fixtures"


def load(code, model):
    tree = json.loads((FIX / f"guide_{code}_{model}_tree.json").read_text(encoding="utf-8"))
    item = json.loads((FIX / f"guide_{code}_{model}_item.json").read_text(encoding="utf-8"))
    return parse_guide_tree(tree, item)


@pytest.fixture(scope="module")
def alphard_anh20w():
    return load("ANH20W", "Alphard")


@pytest.fixture(scope="module")
def alphard_ayh30w():
    return load("AYH30W", "Alphard")


@pytest.fixture(scope="module")
def eclass():
    return load("213271", "E-Class")


def facts(**kw):
    base = dict(ref_no="x", url="u", make="Toyota", model="Alphard", start_price_jpy=100_000)
    base.update(kw)
    return VehicleFacts(**base)


def test_default_variant_covers_any_chassis(alphard_anh20w):
    rule = alphard_anh20w
    assert [v.label for v in rule.variants] == ["Default"]
    m = rule.resolve_variant("ANH20W", "240S")
    assert m.how == "default" and m.variant is rule.variants[0]
    # The Vellfire/Alphard grid: 2012 row, 70K-80K = 524 (the dial is already in the value).
    r = evaluate_hard(facts(chassis="ANH20W", year=2012, km=73_000, grade_raw="4", variant_text="240S"), rule)
    assert r.status == PASS
    assert r.variant_name == "Default" and r.variant_how == "default"
    assert r.band_label == "70K-80K" and r.bid_jpy_k == 524


def test_trim_variants_resolution(alphard_ayh30w):
    rule = alphard_ayh30w
    names = [v.name for v in rule.variants]
    assert names == ["Basic/Hybrid X/ G/ S/SR", "SR-C/V/V-L/ZR/ZR-G/Gold", "Executive Z, Executive lounge"]
    expected = {
        "2.5 S": "Basic", "Hybrid X": "Basic", "2.5 SR": "Basic", "2.5 G": "Basic",
        "2.5 V": "SR-C", "3.5 V L Edition": "SR-C", "Hybrid ZR": "SR-C", "3.5 SR C Package": "SR-C", "3.5 ZR G Edition": "SR-C",
        "3.5 Executive Lounge": "Executive", "Hybrid Executive Lounge": "Executive",
    }
    for text, prefix in expected.items():
        m = rule.resolve_variant("AYH30W", text)
        assert m.how == "trim" and m.variant.name.startswith(prefix), (text, m.how, m.variant and m.variant.name)
    # No exact trim phrase ("X" alone is not listed, only "Hybrid X"): the reviewer picks between the three grids.
    for text in ("3.5 GF", "2.5 X"):
        m = rule.resolve_variant("AYH30W", text)
        assert m.how == "ambiguous" and len(m.candidates) == 3, text
    r = evaluate_hard(facts(chassis="AYH30W", year=2021, km=30_000, grade_raw="4.5", variant_text="Hybrid ZR"), rule)
    assert r.status == PASS and r.variant_how == "trim" and r.variant_name.startswith("SR-C")


def test_body_style_variants(eclass):
    m = eclass.resolve_variant("213271", "E400 Avantgarde")
    assert m.how == "ambiguous" and [v.name for v in m.candidates] == ["Sedan", "Wagon"]
    m = eclass.resolve_variant("213271", "E400 Wagon")
    assert m.how == "trim" and m.variant.name == "Wagon"
    m = eclass.resolve_variant("213271", "E400 Sedan Avantgarde")
    assert m.how == "trim" and m.variant.name == "Sedan"


def test_ambiguous_variants_need_review(alphard_ayh30w):
    rule = alphard_ayh30w
    r = evaluate_hard(facts(chassis="AYH30W", year=2021, km=30_000, grade_raw="4.5", variant_text="3.5 GF"), rule)
    assert r.status == NEEDS_REVIEW and r.variant_how == "ambiguous"
    assert set(r.candidate_bids) == {v.name for v in rule.variants}
    assert all(isinstance(b, int) for b in r.candidate_bids.values())


def test_model_matches(alphard_anh20w):
    rule = alphard_anh20w  # Toyota Alphard
    assert model_matches(rule, facts(make="Toyota", model="Alphard"))
    assert not model_matches(rule, facts(make="Toyota", model="Vellfire"))
    assert not model_matches(rule, facts(make="Lexus", model="Alphard"))
    rule.model, rule.make = "Hijet Campervan", "Daihatsu"
    assert model_matches(rule, facts(make="Daihatsu", model="Hijet Truck"))
    rule.model, rule.make = "LS460", "Lexus"
    assert model_matches(rule, facts(make="Lexus", model="LS"))
    rule.model, rule.make = "E-Class E400", "Mercedes-Benz"
    assert model_matches(rule, facts(make="Mercedes Benz", model="E-Class"))
    assert model_matches(rule, facts(make="Mercedes-Benz", model=""))  # unknown never blocks
