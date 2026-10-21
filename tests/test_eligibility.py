import json
from datetime import date
from pathlib import Path

from auction_bot.carbarn.models import parse_guide_tree

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
