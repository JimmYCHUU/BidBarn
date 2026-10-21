import pytest
from auction_bot.judge.ladders import LadderVerdict, Observation, chip_applies, maker_cruise, resolve
from auction_bot.judge.requirements import Requirement
from auction_bot.judge.topics import TOPIC_CRUISE, TOPIC_WELCAB

CR = Requirement("r2", "Cruise control", "ladder", "must", topic=TOPIC_CRUISE, topic_detail="plain")
ACC = Requirement("r4", "Radar cruise", "ladder", "must", topic=TOPIC_CRUISE, topic_detail="adaptive")
WC = Requirement("r3", "Welcab swivel seat", "ladder", "must", topic=TOPIC_WELCAB)
CHIP = Requirement("chip:no-cruise-control", "No cruise control", "ladder", "chip", amount_k=-100, topic=TOPIC_CRUISE, topic_detail="plain", negated=True)

def obs(id, sheet="not_mentioned", photo="not_visible", photo_no=None, grade=None, adaptive="unknown", ev=""):
    return Observation(id=id, sheet={"state": sheet, "evidence": ev, "adaptive": adaptive},
                       photo={"state": photo, "photo_no": photo_no, "evidence": ev, "adaptive": adaptive}, catalog_grade=grade).model_dump()

STD = {"cruise": {"status": "standard", "grade": "2.5 X", "mark": "標準装備"}, "adaptive": {"status": "standard", "grade": "2.5 X", "mark": "◯"},
       "welcab": {"seat_system": "second_row_lift_up", "grade": "2.5 X サイドリフトアップチルトシート"}, "grades": [], "urls": ["https://www.kurumaerabi.com/catalog/toyota/3491/type/3BA-AGH30W/"], "note": ""}
OPT = {**STD, "cruise": {"status": "optional", "grade": "DX", "mark": "メーカーオプション"}, "adaptive": {"status": "unknown", "grade": None, "mark": ""}}
NONE = {**STD, "cruise": {"status": "none", "grade": "DX", "mark": "なし"}, "adaptive": {"status": "none", "grade": "DX", "mark": "なし"}}
MIXED = {**STD, "cruise": {"status": "mixed", "grade": None, "mark": ""},
         "grades": [{"name": "2.5 X", "cruise": "none", "adaptive": "none", "cruise_mark": "なし", "adaptive_mark": "", "seat_system": "not_welcab", "welfare": False, "seats": 8, "url": "u1"},
                    {"name": "2.5 S Cパッケージ", "cruise": "standard", "adaptive": "standard", "cruise_mark": "標準装備", "adaptive_mark": "◯", "seat_system": "not_welcab", "welfare": False, "seats": 7, "url": "u2"}]}

@pytest.mark.parametrize("sheet,photo,maker,verdict", [
    ("yes", "no", NONE, "pass"),                 # the sheet wins
    ("no", "yes", STD, "fail"),
    ("not_mentioned", "yes", NONE, "pass"),
    ("not_mentioned", "no", STD, "fail"),
    ("not_mentioned", "not_visible", STD, "fail"),   # Jim: no wheel photo = no
    ("not_mentioned", "maybe", STD, "pass"),
    ("not_mentioned", "maybe", OPT, "fail"),         # Jim: only standard confirms
    ("not_mentioned", "maybe", NONE, "fail"),
    ("not_mentioned", "maybe", MIXED, "unclear"),
    ("not_mentioned", "maybe", None, "unclear"),
])
def test_cruise_ladder_golden_cases(sheet, photo, maker, verdict):
    v = resolve(CR, obs("r2", sheet, photo, photo_no=7), maker)
    assert isinstance(v, LadderVerdict) and v.verdict == verdict and v.id == "r2"
    assert v.steps[0]["step"] == "sheet" and v.steps[1]["step"] == "photo" and v.steps[2]["step"] == "maker"
    assert sum(1 for s in v.steps if s["decided"]) == 1

def test_cruise_evidence_line_and_photo_number():
    v = resolve(CR, obs("r2", "not_mentioned", "no", photo_no=7, ev="bare spokes, no stalk"), STD)
    assert v.evidence.startswith("sheet: not_mentioned") and "photo 7: no (bare spokes, no stalk)" in v.evidence and v.evidence.endswith("maker: not needed")
    assert v.steps[1]["photo_no"] == 7 and v.steps[1]["decided"]
    v = resolve(CR, obs("r2", "not_mentioned", "maybe", photo_no=3), STD)
    assert "maker: standard on 2.5 X [標準装備]" in v.evidence and v.steps[2]["url"] == STD["urls"][0]

def test_incomplete_photo_set_and_unknown_states():
    v = resolve(CR, obs("r2", "not_mentioned", "not_visible"), STD, photos_complete=False)
    assert v.verdict == "unclear" and "could not be downloaded" in v.evidence
    v = resolve(CR, {"id": "r2", "sheet": {"state": "unsure"}, "photo": {"state": "kinda"}}, STD)
    assert v.verdict == "fail" and "unknown state" in v.evidence   # unsure -> not_mentioned, kinda -> not_visible, complete set -> no
    assert resolve(CR, None, STD).verdict == "unclear" and "no observation" in resolve(CR, None, STD).evidence

def test_mixed_maker_resolved_by_the_named_grade_and_adaptive_kind():
    assert resolve(CR, obs("r2", "not_mentioned", "maybe", grade="2.5 S Cパッケージ"), MIXED).verdict == "pass"
    assert resolve(CR, obs("r2", "not_mentioned", "maybe", grade="2.5 X"), MIXED).verdict == "fail"
    assert maker_cruise(MIXED, "plain", "nope") == ("mixed", None)
    assert resolve(ACC, obs("r4", "yes", "no", adaptive="unknown"), STD).verdict == "fail"       # plain cruise on the sheet does not satisfy adaptive; the wheel says no
    assert resolve(ACC, obs("r4", "yes", "no", adaptive="yes"), STD).verdict == "pass"
    assert resolve(ACC, obs("r4", "not_mentioned", "maybe"), OPT).verdict == "unclear"          # adaptive unknown in the catalogue
    assert maker_cruise({**NONE, "adaptive": {"status": "standard", "grade": "G", "mark": "◯"}}, "plain", None)[0] == "standard"

@pytest.mark.parametrize("sheet,photo,maker_seat,verdict", [
    ("front_swivel", "not_visible", "unknown", "pass"),
    ("second_row_lift_up", "slope", "unknown", "pass"),          # the sheet wins (Jim: side lift-up passes)
    ("slope", "front_lift_up", "second_row_lift_up", "fail"),
    ("not_welcab", "not_visible", "second_row_lift_up", "fail"),
    ("welcab_unspecified", "front_lift_up", "unknown", "pass"),
    ("welcab_unspecified", "wheelchair_lift", "unknown", "fail"),
    ("not_mentioned", "not_visible", "second_row_lift_up", "pass"),
    ("welcab_unspecified", "not_visible", "slope", "fail"),
    ("welcab_unspecified", "not_visible", "unknown", "fail"),    # Jim: no seat statement anywhere = not eligible
    ("welcab_unspecified", "not_visible", "mixed", "fail"),
])
def test_welcab_ladder_golden_cases(sheet, photo, maker_seat, verdict):
    maker = {**STD, "welcab": {"seat_system": maker_seat, "grade": "x" if maker_seat not in ("unknown", "mixed", "none") else None}}
    v = resolve(WC, obs("r3", sheet, photo, photo_no=5), maker)
    assert v.verdict == verdict
    if verdict == "fail" and maker_seat in ("unknown", "mixed") and sheet == "welcab_unspecified" and photo == "not_visible":
        assert "no seating statement" in v.evidence

def test_negated_chip_mapping():
    assert chip_applies(CHIP, "pass") == "no" and chip_applies(CHIP, "fail") == "yes" and chip_applies(CHIP, "unclear") == "unclear"
    plain_chip = Requirement("chip:cruise", "Cruise control", "ladder", "chip", amount_k=50, topic=TOPIC_CRUISE)
    assert chip_applies(plain_chip, "pass") == "yes" and chip_applies(plain_chip, "fail") == "no"
    neg_must = Requirement("r9", "No cruise control", "ladder", "must", topic=TOPIC_CRUISE, negated=True)
    assert resolve(neg_must, obs("r9", "yes", "yes"), STD).verdict == "fail"


NEG = Requirement("r9", "No cruise control", "ladder", "must", topic=TOPIC_CRUISE, negated=True)


def test_missing_observation_is_unclear_for_every_topic():
    for req in (CR, ACC, WC, NEG):
        v = resolve(req, None, STD)
        assert v.verdict == "unclear" and "model gave no observation" in v.evidence
        assert resolve(req, {"id": req.id, "sheet": {}, "photo": {}}, STD).verdict == "unclear"
    assert resolve(CR, obs("r2", "not_mentioned", "not_visible"), STD).verdict == "fail"   # explicit not_visible on a complete set


def test_side_fields_are_coerced_one_by_one():
    v = resolve(CR, {"id": "r2", "sheet": {"state": "not_mentioned"}, "photo": {"state": "yes", "adaptive": "Yes", "photo_no": "7"}}, STD)
    assert v.verdict == "pass" and v.steps[1]["photo_no"] is None
    v = resolve(CR, {"id": "r2", "sheet": {"state": "not_mentioned"}, "photo": {"state": "maybe"}, "catalog_grade": 5}, MIXED)
    assert v.verdict == "unclear"


def test_adaptive_no_and_unknown_downgrades_show_in_the_evidence():
    v = resolve(ACC, obs("r4", "yes", "not_visible", adaptive="no"), STD)
    assert v.verdict == "fail" and "yes (not adaptive)" in v.evidence and v.steps[0]["decided"]
    v = resolve(ACC, obs("r4", "not_mentioned", "yes", adaptive="no"), STD)
    assert v.verdict == "fail" and "yes (not adaptive)" in v.evidence and not v.steps[2]["decided"]
    v = resolve(ACC, obs("r4", "yes", "maybe", adaptive="unknown"), STD)
    assert v.verdict == "pass" and "yes (adaptive unknown)" in v.evidence and v.steps[2]["decided"]
    v = resolve(ACC, obs("r4", "not_mentioned", "yes", adaptive="unknown"), OPT)
    assert v.verdict == "unclear" and "yes (adaptive unknown)" in v.evidence


def test_welcab_short_photo_set_and_unspecified_photo():
    maker = {**STD, "welcab": {"seat_system": "unknown", "grade": None}}
    for photo in ("not_visible", "welcab_unspecified"):
        v = resolve(WC, obs("r3", "not_mentioned", photo), maker, photos_complete=False)
        assert v.verdict == "unclear" and "could not be downloaded" in v.evidence
        assert v.steps[2]["state"] == "unknown" and sum(1 for s in v.steps if s["decided"]) == 1


def test_step_evidence_has_no_prefix_and_to_dict_and_dispatch():
    v = resolve(CR, obs("r2", "not_mentioned", "maybe"), STD)
    assert v.steps[2]["evidence"] == "standard on 2.5 X [標準装備]"
    assert set(v.to_dict()) == {"id", "verdict", "evidence", "steps"}
    with pytest.raises(ValueError):
        resolve(Requirement("x", "Seats", "sheet", "must"), None, STD)


def test_mixed_seat_maker_resolved_by_catalog_grade():
    mixed = {**STD, "welcab": {"seat_system": "mixed", "grade": None},
             "grades": [{"name": "W", "seat_system": "front_swivel", "url": "u1"}, {"name": "P", "seat_system": "not_welcab", "url": "u2"}]}
    assert resolve(WC, obs("r3", "not_mentioned", "not_visible", grade="W"), mixed).verdict == "pass"
    assert resolve(WC, obs("r3", "not_mentioned", "not_visible", grade="P"), mixed).verdict == "fail"


@pytest.mark.parametrize("sheet,photo", [("front_swivel", "slope"), ("slope", "front_swivel"), ("not_mentioned", "front_lift_up"), ("welcab_unspecified", "not_visible")])
def test_welcab_exactly_one_decided_step(sheet, photo):
    v = resolve(WC, obs("r3", sheet, photo), STD)
    assert sum(1 for s in v.steps if s["decided"]) == 1
