"""Reading Carbarn's sheet-reader reply into the bot's sheet fields and ladder sheet steps (pure, no network)."""
import copy
import json
from pathlib import Path

import pytest

from auction_bot.judge.sheet_facts import (cruise_sheet_step, fields, parse_km, parse_seats, sheet_usable, welcab_sheet_step,
                                           welfare_usable)

FIX = Path(__file__).parent / "fixtures"


def _reply(**changes):
    r = json.loads((FIX / "carbarn_read_sheet_reply.json").read_text(encoding="utf-8"))
    for path, value in changes.items():
        part, key = path.split("__")
        r[part][key] = value
    return r


def test_fields_from_the_sheet_part():
    f = fields(_reply())
    assert f == {"seats": 5, "model_code": "DAA-ZVW30", "bare_model_code": "ZVW30", "km": 54_000, "grade": "4", "is_4wd": True,
                 "engine_cc": 1800}
    assert fields(_reply(sheet__grade="S ツーリングセレクション", sheet__model_name="プリウス"))["is_4wd"] is None
    assert fields(None) == {} and fields({"sheet": None}) == {}


@pytest.mark.parametrize("raw,km", [("54千km", 54_000), ("54,000km", 54_000), ("5.4万km", 54_000), ("54000", 54_000),
                                    ("54k", 54_000), ("123,456 km", 123_456), ("54", None), ("", None), (None, None), ("不明", None)])
def test_parse_km(raw, km):
    assert parse_km(raw) == km


@pytest.mark.parametrize("raw,seats", [("5名", 5), ("8", 8), (7, 7), ("10人", 10), ("2/5名", 2), ("", None), (None, None), ("x", None),
                                       ("99", None)])
def test_parse_seats(raw, seats):
    assert parse_seats(raw) == seats


def test_cruise_step_reads_equipment_selling_points_grade_line_and_notes():
    s = cruise_sheet_step(_reply())
    assert s["state"] == "yes" and "クルーズコントロール" in s["evidence"] and s["adaptive"] == "unknown"
    radar = cruise_sheet_step(_reply(sheet__selling_points=["レーダークルーズコントロール"]))
    assert radar["state"] == "yes" and radar["adaptive"] == "yes"
    notes = cruise_sheet_step(_reply(sheet__selling_points=[], sheet__inspector_notes=[{"ja": "クルコン付", "en": "with cruise"}]))
    assert notes["state"] == "yes"
    none = cruise_sheet_step(_reply(sheet__selling_points=["スマートキー"]))
    assert none["state"] == "not_mentioned" and none["adaptive"] == "unknown"


def test_a_bare_cc_is_never_cruise_control():
    s = cruise_sheet_step(_reply(sheet__selling_points=[], sheet__equipment=["PS", "PW", "CC"]))
    assert s["state"] == "not_mentioned" and "CC" in s["evidence"] and "not read as cruise control" in s["evidence"]


def test_land_cruiser_is_not_cruise_control():
    s = cruise_sheet_step(_reply(sheet__selling_points=[], sheet__model_name="ランドクルーザー", sheet__grade="Land Cruiser AX"))
    assert s["state"] == "not_mentioned"


@pytest.mark.parametrize("features,state", [
    (["SWIVEL_SEAT"], "front_swivel"),
    (["LIFT_UP_SEAT"], "front_lift_up"),
    (["SIDE_LIFT_UP_SEAT"], "second_row_lift_up"),
    (["WHEELCHAIR_SLOPE"], "slope"),
    (["WHEELCHAIR_LIFT"], "wheelchair_lift"),
    (["WHEELCHAIR_SLOPE", "SIDE_LIFT_UP_SEAT"], "second_row_lift_up"),  # a passing seat wins: the seat is what the rule asks for
    ([], "welcab_unspecified"),
])
def test_welcab_step_maps_the_features(features, state):
    r = _reply(welfare__found=True, welfare__features=features, welfare__sentence="Welcab with a side lift-up seat",
               welfare__evidence=[{"ja": "サイドリフトアップシート", "en": "side lift-up seat"}])
    s = welcab_sheet_step(r)
    assert s["state"] == state and "サイドリフトアップシート" in s["evidence"]


def test_welcab_step_silent_sheet_and_failures():
    assert welcab_sheet_step(_reply())["state"] == "not_mentioned"  # nothing about welfare is a silent sheet, never "not a Welcab"
    assert welcab_sheet_step({**_reply(), "welfareError": "model busy"}) is None
    assert welcab_sheet_step({**_reply(), "welfare": None}) is None
    assert welcab_sheet_step(None) is None


def test_usability():
    assert sheet_usable(_reply()) and welfare_usable(_reply())
    assert not sheet_usable(_reply(sheet__low_confidence=True))
    assert not sheet_usable({**_reply(), "sheetError": "timeout"}) and not sheet_usable({**_reply(), "sheet": None})
    assert not welfare_usable(_reply(welfare__confidence="low"))
    assert not welfare_usable(_reply(welfare__sheetText=False))
    assert not welfare_usable({**_reply(), "welfareError": "x"}) and not sheet_usable(None) and not welfare_usable(None)
    r = _reply()
    assert copy.deepcopy(r) == r  # the functions never change their input
    fields(r), cruise_sheet_step(r), welcab_sheet_step(r)
    assert r == json.loads((FIX / "carbarn_read_sheet_reply.json").read_text(encoding="utf-8"))
