import json
from pathlib import Path

from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.judge.topics import (TOPIC_CRUISE, TOPIC_WELCAB, WELCAB_GUIDE_TEXT, approval_seat_sentences, cruise_kind,
                                      detect_topic, is_negated, seat_system, welcab_guide)
from auction_bot.judge.requirements import Requirement, has_ladder, ladder_topics, needs_photos, requirements_for, requirements_hash
from auction_bot.rules.hints import keyword_hints
from auction_bot.nichibo.facts import VehicleFacts

FIX = Path(__file__).parent / "fixtures"


def test_detect_topic_and_kind():
    assert detect_topic("Cruise control") == TOPIC_CRUISE and detect_topic("Must have radar cruise") == TOPIC_CRUISE
    assert detect_topic("No cruise control") == TOPIC_CRUISE and detect_topic("クルコン付き") == TOPIC_CRUISE
    assert detect_topic("CC") is None and detect_topic("2000cc engine") is None
    assert detect_topic("Welcab, swivel passenger seat") == TOPIC_WELCAB and detect_topic("Side lift up seat only") == TOPIC_WELCAB
    assert detect_topic("No ramp / slope type") == TOPIC_WELCAB and detect_topic("Only dark coloured seats") is None
    assert detect_topic("Land Cruiser 70 only") is None and detect_topic("No rotating captain seats") is None
    assert detect_topic("tire rotation") is None and seat_system("rotating passenger seat") == "front_swivel"
    assert cruise_kind("Cruise control") == "plain" and cruise_kind("Adaptive cruise control") == "adaptive" and cruise_kind("追従クルコン") == "adaptive"
    assert is_negated("No cruise control") and is_negated("Without Welcab") and not is_negated("Cruise control, no sticker")


def test_a_bare_lift_up_is_a_lifted_4wd_not_a_welcab():
    assert detect_topic("No lift up kit") is None and detect_topic("4WD リフトアップ") is None
    assert detect_topic("Side lift up seat only") == TOPIC_WELCAB and detect_topic("Lift-up seat") == TOPIC_WELCAB
    assert detect_topic("助手席リフトアップシート") == TOPIC_WELCAB and detect_topic("サイドリフトアップ車") == TOPIC_WELCAB
    msgs = [h["message"] for h in keyword_hints(VehicleFacts(ref_no="r", url="u", variant_text="XC LIFT UP 4WD"))]
    assert "wheelchair / Welcab conversion mentioned" not in msgs
    assert seat_system("助手席リフトアップシート") == "front_lift_up" and seat_system("LIFT UP") == "front_lift_up"   # grade names keep the bare form


def test_a_welcab_note_is_never_negated():
    reqs = {r.id: r for r in requirements_for(_rule(notes="No ramp / slope type|No cruise control"))}
    assert reqs["r1"].topic == TOPIC_WELCAB and reqs["r1"].negated is False
    assert reqs["r2"].topic == TOPIC_CRUISE and reqs["r2"].negated is True


def test_seat_system_words():
    assert seat_system("2.5 X サイドリフトアップチルトシート") == "second_row_lift_up"
    assert seat_system("X SIDE LIFT UP SEAT 4WD") == "second_row_lift_up"
    assert seat_system("助手席リフトアップシート") == "front_lift_up" and seat_system("LIFT UP SEAT") == "front_lift_up"
    assert seat_system("助手席回転シート") == "front_swivel" and seat_system("swivel seat") == "front_swivel"
    assert seat_system("車いす仕様車 スロープタイプ") == "slope" and seat_system("wheelchair ramp") == "slope"
    assert seat_system("車いすリフト付") == "wheelchair_lift"
    assert seat_system("ZR WELL CAB 4WD") == "welcab_unspecified" and seat_system("2.5 X") == "not_welcab"


def _rule(name="guide_223_tree.json", notes=None, model=None):
    tree = json.loads((FIX / name).read_text(encoding="utf-8"))
    if notes is not None:
        tree["notes"] = notes
    if model is not None:
        tree["model"] = model
    return parse_guide_tree(tree)


def test_requirements_carry_topics_and_the_welcab_guide_rule():
    rule = _rule(notes="No sticker paint|Cruise control|Welcab with swivel or lift up seat")
    reqs = requirements_for(rule)
    by = {r.id: r for r in reqs}
    assert by["r1"].topic is None and by["r1"].source == "photos"
    assert by["r2"].topic == TOPIC_CRUISE and by["r2"].topic_detail == "plain" and by["r2"].source == "ladder" and not by["r2"].negated
    assert by["r3"].topic == TOPIC_WELCAB and by["r3"].text.startswith("Welcab with swivel or lift up seat")
    assert by["r3"].text.endswith(" No approval text names the seat system; confirm on ROVER by hand.")
    assert has_ladder(reqs) and ladder_topics(reqs) == {TOPIC_CRUISE, TOPIC_WELCAB} and needs_photos(reqs)
    assert requirements_hash(reqs, []) != requirements_hash(requirements_for(_rule(notes="No sticker paint|Cruise control|Welcab")), [])
    plain = requirements_for(_rule(notes="No sticker paint"))
    assert not has_ladder(plain)
    welcab = requirements_for(_rule(notes=None, model="Hiace Welcab"))
    g = next(r for r in welcab if r.id == "g:welcab")
    assert welcab_guide(_rule(model="Hiace Welcab")) and g.topic == TOPIC_WELCAB and g.text.startswith(WELCAB_GUIDE_TEXT) and g.kind == "must"
    assert not any(r.id == "g:welcab" for r in requirements_for(_rule(notes="Welcab swivel seat", model="Hiace Welcab")))


def test_approval_seat_sentences_and_hints():
    rule = _rule("guide_AYH30W_Alphard_tree.json")
    assert approval_seat_sentences(rule.variants[0].approvals) == []   # no seat words in these pairs
    for a in rule.variants[0].approvals:
        if a.selected:
            a.sev_variant_details = "Hybrid with swivel passenger seat. Engine 2AR-FXE."
    sents = approval_seat_sentences(rule.variants[0].approvals)
    assert sents[0].startswith("SEV-000743 / MRE-000028: ") and "swivel passenger seat" in sents[0] and "2AR-FXE" not in sents[0]
    f = VehicleFacts(ref_no="r", url="u", variant_text="X SIDE LIFT UP SEAT 4WD", equipment="ps pw cc", translator={"SalesPoint": "クルコン ナビ"})
    msgs = [h["message"] for h in keyword_hints(f)]
    assert "wheelchair / Welcab conversion mentioned" in msgs and "cruise control mentioned" in msgs
    assert any(m.startswith("the text says CC") for m in msgs)
    assert not any(m.startswith("the text says CC") for m in [h["message"] for h in keyword_hints(VehicleFacts(ref_no="r", url="u", variant_text="2.8 cc test"))])
    num = VehicleFacts(ref_no="r", url="u", translator={"Comments": "2.7 GL 2700 cc"}, equipment="2000cc")
    assert not any(h["message"].startswith("the text says CC") for h in keyword_hints(num))


def test_hash_sees_negation_and_cruise_kind():
    def mk(**kw):
        return [Requirement("r1", "Cruise control", "ladder", "must", topic=TOPIC_CRUISE, **kw)]
    base = requirements_hash(mk(topic_detail="plain"), [])
    assert base != requirements_hash(mk(topic_detail="adaptive"), [])
    assert base != requirements_hash(mk(topic_detail="plain", negated=True), [])


def test_odd_flags_read_the_sales_point():
    from auction_bot.judge.requirements import odd_flags
    from auction_bot.rules.hard import evaluate_hard
    rule = _rule()
    f = VehicleFacts(ref_no="r", url="u", year=2020, make="Toyota", model="Hiace", variant_text="Van DX", chassis="GDH206V", km=65_000,
                     grade_raw="4", start_price_jpy=1_000_000, translator={"SalesPoint": "クルコン ナビ ETC"})
    assert any(x.startswith("sheet notes (SalesPoint): クルコン") for x in odd_flags(rule, evaluate_hard(f, rule), f))
