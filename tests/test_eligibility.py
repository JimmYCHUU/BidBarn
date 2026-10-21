import json
import re
from datetime import date
from pathlib import Path

from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.eligibility import check_lot, split_model_codes, window_state

FIX = Path(__file__).parent / "fixtures"


def _rule(name):
    return parse_guide_tree(json.loads((FIX / name).read_text(encoding="utf-8")))


def test_parse_keeps_every_approval_pair():
    rule = _rule("guide_AYH30W_Alphard_tree.json")
    v = rule.variants[0]
    assert len(v.approvals) == 10
    sel = [a for a in v.approvals if a.selected]
    assert len(sel) == 1 and sel[0].label == "SEV-000743 / MRE-000028"
    a = sel[0]
    assert (a.build_from_ym, a.build_to_ym) == (201907, 202306) and a.expiry_date == date(2027, 12, 16)
    assert a.sev_model_codes == "AYH30" and a.sev_criterion == "Environmental Criterion" and a.active
    assert a.last_scraped_at.startswith("2026-08-21") and 2021 in a.eligible_years
    camper = next(x for x in v.approvals if x.label == "SEV-000924 / MRE-000886")
    assert (camper.build_from_ym, camper.build_to_ym) == (201501, 202110)   # the overlap of 201501-202306 and 201501-202110
    assert "Campervan" in camper.sev_variant_details and camper.texts()[0][0] == "sev"
    assert sum(1 for x in v.approvals if not x.active) == 4
    open_end = next(x for x in v.approvals if x.sev_number == "SEV-000460")
    assert open_end.build_to_ym == 202502   # SEV end is null, MRE end 202502
    # Holder and contact fields are never parsed.
    assert not any(hasattr(a, f) for f in ("mre_holder_name", "mre_phone", "mre_contact_name", "mre_website"))


def test_parse_gdh206_selected_pair_and_cells_unchanged():
    rule = _rule("guide_223_tree.json")
    sel = next(a for a in rule.variants[0].approvals if a.selected)
    assert sel.label == "SEV-000813 / MRE-000233" and sel.sev_model_codes == "200 series" and sel.sev_variant_details == "4x4 Campervan only"
    assert rule.approvals_for(None) == rule.variants[0].approvals


TODAY = date(2026, 10, 20)


def _facts(year=2021, chassis="AYH30W"):
    return VehicleFacts(ref_no="r", url="u", year=year, make="Toyota", model="Alphard", chassis=chassis, km=50_000, grade_raw="4")


def test_window_states_on_the_selected_alphard_pair():
    rule = _rule("guide_AYH30W_Alphard_tree.json")
    v = rule.variants[0]
    by_year = {y: check_lot(rule, v, _facts(y), None, TODAY) for y in (2018, 2019, 2021, 2023, 2024)}
    assert by_year[2018].verdict == "cannot_import" and "07/2019" in next(c.detail for c in by_year[2018].checks if c.name == "window")
    assert by_year[2019].verdict == "confirm" and any(t["id"] == "elig:build" and "07/2019" in t["text"] for t in by_year[2019].texts)
    assert by_year[2021].verdict == "eligible" and not any(t["id"] == "elig:build" for t in by_year[2021].texts)
    assert by_year[2023].verdict == "confirm" and by_year[2024].verdict == "confirm"
    e = by_year[2021]
    assert e.pair["label"] == "SEV-000743 / MRE-000028" and e.pair["window"] == "07/2019 to 06/2023"
    assert {c.name: c.state for c in e.checks} == {"window": "pass", "expiry": "pass", "model_code": "pass"}
    assert len(e.alternatives) == 5 and all(a["label"] != e.pair["label"] for a in e.alternatives)


def test_expiry_model_code_and_hash():
    rule = _rule("guide_AYH30W_Alphard_tree.json")
    v = rule.variants[0]
    assert check_lot(rule, v, _facts(2021), None, date(2028, 1, 1)).verdict == "cannot_import"
    e = check_lot(rule, v, _facts(2021, chassis="AGH30W"), None, TODAY)
    assert e.verdict == "confirm" and next(c for c in e.checks if c.name == "model_code").state == "unclear"
    ocr = check_lot(rule, v, _facts(2021, chassis=""), {"model_code": "DAA-AYH30W"}, TODAY)
    assert next(c for c in ocr.checks if c.name == "model_code").state == "pass"
    assert check_lot(rule, v, _facts(2021), None, TODAY).hash == check_lot(rule, v, _facts(2021), None, date(2027, 3, 1)).hash
    assert check_lot(rule, v, _facts(2021), None, TODAY).hash != check_lot(rule, v, _facts(2018), None, TODAY).hash
    assert check_lot(rule, v, _facts(None), None, TODAY).checks[0].state == "info"


def test_model_code_parsing():
    assert split_model_codes("TRH2XX, KDH2XX, GDH2XX") == ["TRH2", "KDH2", "GDH2"]
    assert split_model_codes("AYH30/AGH30/AGH35/GGH30/GGH35") == ["AYH30", "AGH30", "AGH35", "GGH30", "GGH35"]
    assert split_model_codes("MXPH1#") == ["MXPH1"] and split_model_codes("200 series") is None
    assert window_state(None, 201907, 202306) == ("info", "registration year unknown")
    assert window_state(2020, None, None)[0] == "pass"


def test_gdh206_free_text_codes_and_no_selected_pair():
    rule = _rule("guide_223_tree.json")
    v = rule.variants[0]
    e = check_lot(rule, v, VehicleFacts(ref_no="r", url="u", year=2020, chassis="GDH206V", km=100_000, grade_raw="4"), None, TODAY)
    assert next(c for c in e.checks if c.name == "model_code").state == "info"
    assert any(t["id"] == "elig:sev" and "4x4 Campervan only" in t["text"] for t in e.texts)
    for a in v.approvals:
        a.selected = False
    e2 = check_lot(rule, v, VehicleFacts(ref_no="r", url="u", year=2020, chassis="GDH206V", km=100_000, grade_raw="4"), None, TODAY)
    assert e2.pair is None and e2.verdict == "confirm" and e2.alternatives
    ids = [t["id"] for t in e2.texts if t["id"] != "elig:build"]
    assert ids and all(re.fullmatch(r"elig:(sev|mre|mre-conditions):\d+", i) for i in ids) and "elig:sev:1" in ids
    for a in v.approvals:
        a.expiry_date = None
    e3 = check_lot(rule, v, _facts(2020, "GDH206V"), None, TODAY)
    assert all(c["state"] == "info" for c in e3.alternatives[0]["checks"] if c["name"] == "expiry")   # alternatives carry plain dicts
    v.approvals = []
    assert check_lot(rule, v, _facts(2020, "GDH206V"), None, TODAY) is None


def test_hash_sees_outcome_changes_without_a_selected_pair():
    rule = _rule("guide_223_tree.json")
    v = rule.variants[0]
    for a in v.approvals:
        a.selected = False
    f = _facts(2020, "GDH206V")
    early, late = check_lot(rule, v, f, None, TODAY), check_lot(rule, v, f, None, date(2029, 1, 1))
    assert early.verdict == "confirm" and late.verdict == "cannot_import" and early.hash != late.hash
    alphard = _rule("guide_AYH30W_Alphard_tree.json")
    av = alphard.variants[0]
    assert check_lot(alphard, av, _facts(2021), None, TODAY).hash == check_lot(alphard, av, _facts(2021), None, date(2027, 3, 1)).hash


def test_selected_but_inactive_pair_still_decides():
    rule = _rule("guide_AYH30W_Alphard_tree.json")
    v = rule.variants[0]
    sel = next(a for a in v.approvals if a.selected)
    sel.active = False
    e = check_lot(rule, v, _facts(2021), None, TODAY)
    assert e.pair["label"] == sel.label and e.verdict == "eligible"
    assert next(c for c in e.checks if c.name == "pair_inactive").state == "info"


def test_no_selected_and_no_active_pair_is_none():
    rule = _rule("guide_223_tree.json")
    v = rule.variants[0]
    for a in v.approvals:
        a.selected = False
        a.active = False
    assert check_lot(rule, v, _facts(2020, "GDH206V"), None, TODAY) is None


def test_model_code_pass_names_the_matching_value():
    rule = _rule("guide_AYH30W_Alphard_tree.json")
    v = rule.variants[0]
    by_chassis = check_lot(rule, v, _facts(2021), None, TODAY)
    assert next(c for c in by_chassis.checks if c.name == "model_code").detail == "chassis AYH30W matches AYH30"
    ocr = check_lot(rule, v, _facts(2021, chassis="ZZZ99"), {"model_code": "DAA-AYH30W"}, TODAY)
    assert next(c for c in ocr.checks if c.name == "model_code").detail == \
        "sheet model code AYH30W matches AYH30 (listing chassis ZZZ99 differs)"
