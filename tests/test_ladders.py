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
    assert v.verdict == "unclear" and "unknown state 'unsure'" in v.evidence and v.steps[0]["decided"]   # never a silent no
    v = resolve(CR, {"id": "r2", "sheet": {"state": "not_mentioned"}, "photo": {"state": "kinda"}}, STD)
    assert v.verdict == "unclear" and "unknown state 'kinda'" in v.evidence and v.steps[1]["decided"]
    v = resolve(WC, {"id": "r3", "sheet": {"state": "not_mentioned"}, "photo": {"state": "swivelish"}}, STD)
    assert v.verdict == "unclear" and "unknown state 'swivelish'" in v.evidence
    assert resolve(CR, {"id": "r2", "sheet": {"state": "yes"}, "photo": {"state": "kinda"}}, STD).verdict == "pass"   # the sheet decides first
    assert resolve(CR, None, STD).verdict == "unclear" and "no observation" in resolve(CR, None, STD).evidence


@pytest.mark.parametrize("req,photo", [(CR, "yes"), (CR, "no"), (WC, "front_swivel"), (WC, "slope")])
def test_missing_sheet_step_is_unclear_even_with_a_photo(req, photo):
    for o in ({"id": req.id, "photo": {"state": photo, "photo_no": 3}}, {"id": req.id, "sheet": {"state": ""}, "photo": {"state": photo}}):
        v = resolve(req, o, STD)
        assert v.verdict == "unclear" and "model gave no sheet reading" in v.evidence and v.steps[0]["decided"]   # the sheet outranks the photo

def test_mixed_maker_resolved_by_the_named_grade_and_adaptive_kind():
    assert resolve(CR, obs("r2", "not_mentioned", "maybe", grade="2.5 S Cパッケージ"), MIXED).verdict == "pass"
    assert resolve(CR, obs("r2", "not_mentioned", "maybe", grade="2.5 X"), MIXED).verdict == "fail"
    assert maker_cruise(MIXED, "plain", "nope") == ("mixed", None)
    assert resolve(ACC, obs("r4", "yes", "no", adaptive="unknown"), STD).verdict == "fail"       # plain cruise on the sheet does not satisfy adaptive; the wheel says no
    assert resolve(ACC, obs("r4", "yes", "no", adaptive="yes"), STD).verdict == "pass"
    assert resolve(ACC, obs("r4", "not_mentioned", "maybe"), OPT).verdict == "unclear"          # adaptive unknown in the catalogue
    assert maker_cruise({**NONE, "adaptive": {"status": "standard", "grade": "G", "mark": "◯"}}, "plain", None)[0] == "standard"


def test_plain_cruise_not_fitted_but_adaptive_mixed_reads_mixed():
    for plain in (NONE, OPT):
        maker = {**plain, "adaptive": {"status": "mixed", "grade": None, "mark": ""}, "grades": MIXED["grades"]}
        assert maker_cruise(maker, "plain", None) == ("mixed", None)
        v = resolve(CR, obs("r2", "not_mentioned", "maybe"), maker)
        assert v.verdict == "unclear" and v.steps[2]["state"] == "mixed"          # some grades carry adaptive: the catalogue cannot say
        assert resolve(CR, obs("r2", "not_mentioned", "maybe", grade="2.5 S Cパッケージ"), maker).verdict == "pass"   # named: adaptive standard
        assert resolve(CR, obs("r2", "not_mentioned", "maybe", grade="2.5 X"), maker).verdict == "fail"

@pytest.mark.parametrize("sheet,photo,maker_seat,verdict", [
    ("front_swivel", "not_visible", "unknown", "pass"),
    ("second_row_lift_up", "slope", "unknown", "pass"),          # the sheet wins (Jim: side lift-up passes)
    ("slope", "front_lift_up", "second_row_lift_up", "fail"),
    ("not_welcab", "not_visible", "second_row_lift_up", "fail"),
    ("welcab_unspecified", "front_lift_up", "unknown", "pass"),
    ("welcab_unspecified", "wheelchair_lift", "unknown", "fail"),
    ("not_mentioned", "not_visible", "second_row_lift_up", "fail"),   # the catalogue alone never makes a lot a Welcab
    ("welcab_unspecified", "not_visible", "second_row_lift_up", "pass"),   # a known Welcab: the one system decides
    ("not_mentioned", "welcab_unspecified", "front_swivel", "pass"),
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

SIDE = "2.5 X サイドリフトアップチルトシート"
# The 3BA-AGH30W page sells plain grades beside one Welcab system (the catalogue fixture's shape).
SELLS_BOTH = {**STD, "welcab": {"seat_system": "mixed", "grade": None, "welcab_systems": ["second_row_lift_up"]},
              "grades": [{"name": "2.5 X", "seat_system": "not_welcab", "url": "u1"}, {"name": SIDE, "seat_system": "second_row_lift_up", "url": "u2"}]}
SEVERAL = {**STD, "welcab": {"seat_system": "mixed", "grade": None, "welcab_systems": ["front_swivel", "slope"]},
           "grades": [{"name": "W", "seat_system": "front_swivel", "url": "u1"}, {"name": "S", "seat_system": "slope", "url": "u2"},
                      {"name": "P", "seat_system": "not_welcab", "url": "u3"}]}


def test_catalogue_alone_never_makes_a_welcab():
    v = resolve(WC, obs("r3", "not_mentioned", "not_visible"), SELLS_BOTH)
    assert v.verdict == "fail" and "no seating statement on the sheet, the photos or the catalogue" in v.evidence and v.steps[2]["decided"]
    v = resolve(WC, obs("r3", "not_mentioned", "not_visible", grade=SIDE), SELLS_BOTH)
    assert v.verdict == "pass" and v.steps[2]["grade"] == SIDE and v.steps[2]["url"] == "u2"
    v = resolve(WC, obs("r3", "not_mentioned", "not_visible", grade="2.5 X"), SELLS_BOTH)
    assert v.verdict == "fail" and "no seating statement" in v.evidence
    # Every grade a Welcab with one system, still nothing on the sheet or the photos and no grade named: fail.
    only = {**STD, "welcab": {"seat_system": "second_row_lift_up", "grade": SIDE, "welcab_systems": ["second_row_lift_up"]},
            "grades": [{"name": SIDE, "seat_system": "second_row_lift_up", "url": "u2"}]}
    assert resolve(WC, obs("r3", "not_mentioned", "not_visible"), only).verdict == "fail"
    assert resolve(WC, obs("r3", "not_mentioned", "not_visible"), only, photos_complete=False).verdict == "unclear"   # short set: review


def test_known_welcab_takes_the_single_system_or_needs_a_named_welcab_grade():
    for sheet, photo in (("welcab_unspecified", "not_visible"), ("not_mentioned", "welcab_unspecified")):
        v = resolve(WC, obs("r3", sheet, photo), SELLS_BOTH)
        assert v.verdict == "pass" and v.steps[2]["state"] == "second_row_lift_up" and v.steps[2]["grade"] == SIDE
        assert resolve(WC, obs("r3", sheet, photo), SEVERAL).verdict == "fail"                 # several systems, no grade named
        assert resolve(WC, obs("r3", sheet, photo, grade="W"), SEVERAL).verdict == "pass"
        assert resolve(WC, obs("r3", sheet, photo, grade="S"), SEVERAL).verdict == "fail"
        v = resolve(WC, obs("r3", sheet, photo, grade="P"), SEVERAL)
        assert v.verdict == "fail" and "no seating statement" in v.evidence                     # a plain grade says nothing about a seat
    assert resolve(WC, obs("r3", "welcab_unspecified", "not_visible"), SEVERAL, photos_complete=False).verdict == "unclear"


def test_a_negated_welcab_note_keeps_the_seat_table():
    neg = Requirement("r5", "No ramp / slope type", "ladder", "must", topic=TOPIC_WELCAB, negated=True)
    assert resolve(neg, obs("r5", "slope", "not_visible"), STD).verdict == "fail"
    assert resolve(neg, obs("r5", "front_swivel", "not_visible"), STD).verdict == "pass"


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
