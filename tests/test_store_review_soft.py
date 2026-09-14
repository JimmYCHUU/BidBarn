import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.config import Config
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.nichibo.parsers import parse_detail_page
from auction_bot.review.app import create_app
from auction_bot.rules.hard import NEEDS_REVIEW, PASS, SKIP, evaluate_hard
from auction_bot.rules.schema import RuleVerdict, SheetCheck
from auction_bot.rules.soft import build_user_content, decide
from auction_bot.store import Store

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = Config()
    c.db_path = tmp_path / "bot.sqlite"
    c.evidence_dir = tmp_path / "evidence"
    c.override_dir = tmp_path / "overrides"
    c.snapshot_dir = tmp_path / "snapshots"
    for d in (c.evidence_dir, c.override_dir, c.snapshot_dir):
        d.mkdir()
    return c


@pytest.fixture
def rule():
    tree = json.loads((FIX / "guide_223_tree.json").read_text(encoding="utf-8"))
    return parse_guide_tree(tree, {"bossBid": False})


@pytest.fixture
def facts() -> VehicleFacts:
    html = (FIX / "detail_gdh206v.html").read_text(encoding="utf-8")
    return parse_detail_page(html, "https://autosearch.nichibojapan.com/auction/REF1")


def sheet(**kw) -> SheetCheck:
    base = dict(sheet_readable=True, sheet_grade="4", sheet_km=122_000, first_registration_year=2020,
                model_code_on_sheet="QDF-GDH206V", seats=6, drivetrain="4WD", roof="low", wheelbase="long",
                sticker_paint="no", seat_material="cloth", seat_colour="grey", captain_seats="no", hybrid_or_ev="no",
                notable_issues=[], rule_verdicts=[RuleVerdict(rule="r", verdict="pass", evidence="sheet")],
                confidence="high", notes="")
    base.update(kw)
    return SheetCheck(**base)


def test_soft_decide_pass(facts, rule):
    assert decide(sheet(), facts, rule)[0] == PASS


def test_soft_decide_fail_on_rule(facts, rule):
    s = sheet(rule_verdicts=[RuleVerdict(rule="2, 3, 5 or 6 seats", verdict="fail", evidence="sheet says 10")])
    status, reasons = decide(s, facts, rule)
    assert status == SKIP and "10" in reasons[0]


def test_soft_decide_unknown_needs_review(facts, rule):
    s = sheet(rule_verdicts=[RuleVerdict(rule="No sticker paint", verdict="unknown", evidence="not visible")])
    assert decide(s, facts, rule)[0] == NEEDS_REVIEW


def test_soft_decide_sheet_disagreement(facts, rule):
    assert decide(sheet(sheet_km=140_000), facts, rule)[0] == NEEDS_REVIEW
    assert decide(sheet(sheet_grade="3.5"), facts, rule)[0] == NEEDS_REVIEW
    assert decide(sheet(model_code_on_sheet="QDF-GDH206K"), facts, rule)[0] == NEEDS_REVIEW
    assert decide(sheet(sheet_readable=False), facts, rule)[0] == NEEDS_REVIEW
    assert decide(sheet(confidence="low"), facts, rule)[0] == NEEDS_REVIEW


def test_seats_modifier_deferred_then_final(rule, facts):
    # Add a synthetic SEATS modifier to the V variant and check the two-pass behaviour.
    from auction_bot.carbarn.models import Condition, Modifier
    v = rule.variant_by_chassis("GDH206V")
    v.modifiers.append(Modifier(id=999, variant_id=v.id, year_from=2018, year_to=2024, odo_from=None, odo_to=None,
                                conditions=[Condition("SEATS", "eq", "6")], amount_jpy_k=50, percent=None,
                                no_bid=False, strict=False, note="six seats", active=True, stale=False))
    first = evaluate_hard(facts, rule)
    # 2020 / 120K-130K = 1,160 base, +80 for grade 4; the seats modifier is deferred.
    assert first.status == PASS and first.deferred_modifiers
    assert first.base_jpy_k == 1_160 and first.bid_jpy_k == 1_240
    final_unknown = evaluate_hard(facts, rule, final=True)
    assert final_unknown.status == NEEDS_REVIEW
    facts.seats = 6
    final = evaluate_hard(facts, rule, final=True)
    assert final.status == PASS and final.bid_jpy_k == 1_290


def test_prompt_content_includes_rules_and_overrides(facts, rule, tmp_path):
    rule.override_text = "- (2026-09-14) Only bid on white or silver vans"
    img = tmp_path / "sheet.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"0" * 100)
    content = build_user_content(facts, rule, img, [], [{"decision": "reject", "reason": "10 seats", "summary": "x"}])
    text = "\n".join(c.get("text", "") for c in content)
    assert "No sticker paint" in text and "Only bid on white" in text and "10 seats" in text
    assert any(c["type"] == "input_image" for c in content)


def test_store_and_review_flow(cfg, rule, facts):
    store = Store(cfg.db_path)
    hard = evaluate_hard(facts, rule)
    pid = store.add_proposal(ref_no=facts.ref_no, model_code="GDH206", snapshot="rules_x.json", rule_updated_at=rule.updated_at,
                             status="BID", stage="soft", bid_jpy_k=hard.bid_jpy_k, base_jpy_k=hard.base_jpy_k,
                             summary="2020 Toyota Hiace", facts_json=facts.to_dict(), hard_json=hard.to_dict(),
                             soft_json={"status": "PASS", "reasons": [], "parsed": sheet().model_dump()},
                             evidence_json={"dir": str(cfg.evidence_dir / "REF1"), "sheet": None, "photos": [], "failed": []})
    assert store.latest_proposal(facts.ref_no)["id"] == pid
    assert store.counts()["bid"] == 1
    store.close()

    client = TestClient(create_app(cfg))
    r = client.get("/")
    assert r.status_code == 200 and "2020 Toyota Hiace" in r.text
    r = client.get(f"/p/{pid}")
    assert r.status_code == 200 and "GDH206V" in r.text and "1,240" in r.text

    # Reject without reason is refused.
    r = client.post(f"/p/{pid}/decide", data={"action": "reject", "reason": ""})
    assert r.status_code == 400
    # Approve below start price is refused.
    r = client.post(f"/p/{pid}/decide", data={"action": "approve", "amount": "100"})
    assert r.status_code == 400
    # Reject with reason + make rule appends an override.
    r = client.post(f"/p/{pid}/decide", data={"action": "reject", "reason": "10 seats", "make_rule": "1"}, follow_redirects=False)
    assert r.status_code == 303
    assert "10 seats" in (cfg.override_dir / "GDH206.md").read_text(encoding="utf-8")

    store = Store(cfg.db_path)
    p = store.proposal(pid)
    assert p["decision"] == "rejected" and p["decision_reason"] == "10 seats"
    assert store.past_decisions("GDH206")[0]["reason"] == "10 seats"
    # Deciding twice is refused.
    assert client.post(f"/p/{pid}/decide", data={"action": "reject", "reason": "again"}).status_code == 409

    # Approve path and placement bookkeeping.
    pid2 = store.add_proposal(ref_no="REF2", model_code="GDH206", snapshot="rules_x.json", rule_updated_at=rule.updated_at,
                              status="BID", stage="soft", bid_jpy_k=1640, base_jpy_k=1560, summary="v2",
                              facts_json=facts.to_dict(), hard_json=hard.to_dict(), soft_json={}, evidence_json={})
    store.close()
    r = client.post(f"/p/{pid2}/decide", data={"action": "approve", "amount": "1640"}, follow_redirects=False)
    assert r.status_code == 303
    store = Store(cfg.db_path)
    rows = store.approved_unplaced(10)
    assert [r["id"] for r in rows] == [pid2]
    store.record_placement(pid2, "REF2", "GDH206", 1640, True, "dry", None, dry_run=True)
    assert not store.already_bid("REF2") and store.approved_unplaced(10)
    store.record_placement(pid2, "REF2", "GDH206", 1640, True, "success", None, dry_run=False)
    assert store.already_bid("REF2") and not store.approved_unplaced(10)
    store.close()


def test_testing_lock_blocks_live_placement(cfg):
    cfg.dry_run = False
    cfg.project_status = "testing"
    dry, why = cfg.placement_mode(live_flag=True)
    assert dry and "testing lock" in why
    cfg.project_status = "The project is finished "  # whitespace tolerated, wording not
    assert cfg.placement_mode(live_flag=False) == (True, "--live flag not given")
    assert cfg.placement_mode(live_flag=True)[0] is False
    cfg.dry_run = True
    assert cfg.placement_mode(live_flag=True) == (True, "dry_run is true in config.yaml")
    cfg.project_status = "the project is finished"
    assert cfg.placement_mode(live_flag=True)[0] is True  # exact phrase only
    client = TestClient(create_app(cfg))
    assert "TESTING: no bids are placed" in client.get("/").text


def test_same_vehicle_under_two_guides_is_flagged(cfg, rule, facts):
    store = Store(cfg.db_path)
    common = dict(ref_no=facts.ref_no, snapshot="rules_x.json", rule_updated_at=rule.updated_at, status="NEEDS_REVIEW",
                  stage="manual", summary="2021 Nissan Serena", facts_json=facts.to_dict(), hard_json={"checks": []},
                  soft_json={"mode": "manual", "checklist": [], "hints": [], "reasons": []}, evidence_json={})
    a = store.add_proposal(guide_id=146, model_code="GFC27", bid_jpy_k=1412, base_jpy_k=1412, **common)
    b = store.add_proposal(guide_id=151, model_code="GFC27", bid_jpy_k=1830, base_jpy_k=1830, **common)
    assert store.multi_guide_refs() == {facts.ref_no}
    assert [r["id"] for r in store.siblings(facts.ref_no, a)] == [b]
    store.close()
    client = TestClient(create_app(cfg))
    page = client.get(f"/p/{a}").text
    assert "Same vehicle is also proposed under" in page and "1,830" in page and f"/p/{b}" in page
    assert "×2" in client.get("/").text
