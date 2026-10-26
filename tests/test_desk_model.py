from datetime import datetime, timedelta, timezone

from auction_bot.judge.requirements import machine_decided, Requirement
from auction_bot.review.app import triage_sort_key
from auction_bot.review.desk_model import LookItem, bid_sheet, build_desk, look_items, lane_of

NOW = datetime(2026, 10, 26, 12, 0, 0)


def _req(rid, text, source, kind="must", topic=None):
    return {"id": rid, "text": text, "source": source, "kind": kind, "amount_k": None, "topic": topic,
            "topic_detail": None, "negated": False}


def _p(pid, ref, **over):
    p = {"id": pid, "ref_no": ref, "guide_id": 1, "model_code": "M1", "decision": "pending", "decided_at": None,
         "status": "NEEDS_REVIEW", "proposed_bid_jpy_k": 1000,
         "facts": {"grade": 4, "km": 50000, "auction_date": "", "auction_place": ""},
         "soft": {"mode": "judge", "requirements": [], "flags": [], "hints": [], "checklist": []},
         "badges": [], "eligibility": None, "judge": {}, "recommendation": "bid", "why_line": ""}
    for k, v in over.items():
        if k in ("soft", "facts"):
            p[k] = {**p[k], **v}
        else:
            p[k] = v
    return p


def test_a_routine_lot_with_a_seat_rule_is_card_clear():
    r = _req("r0", "8 seats", "sheet")
    assert machine_decided(Requirement(**r))
    p = _p(1, "A", soft={"requirements": [r]}, badges=[{"mark": "·", "cls": "unk", "text": "8 seats", "evidence": ""}])
    look = look_items(p)
    assert look == [] and lane_of(p, look) == "clear"


def test_anything_the_model_saw_needs_a_look():
    r = _req("r0", "Cruise control", "ladder", topic="cruise_control")
    ladder = {"id": "r0", "steps": [{"step": "sheet", "decided": False}, {"step": "photo", "decided": True, "photo_no": 6}]}
    p = _p(1, "A", soft={"requirements": [r], "judgement": {"x": 1}, "ladders": [ladder]},
           ladders={"r0": ladder},
           badges=[{"mark": "✓", "cls": "ok", "text": "Cruise control", "evidence": "wheel switch"}])
    look = look_items(p)
    assert lane_of(p, look) == "look"
    assert look[0] == LookItem(text="Cruise control: wheel switch", kind="check", where="photo", photo_no=6)


def test_a_failed_or_unclear_verdict_needs_a_look():
    r = _req("r0", "No smoking", "photos")
    bad = _p(1, "A", soft={"requirements": [r], "judgement": {"x": 1}}, badges=[{"mark": "✗", "cls": "bad", "text": "No smoking", "evidence": ""}])
    assert look_items(bad)[0].kind == "fail" and look_items(bad)[0].text == "No smoking"
    unc = _p(2, "B", soft={"requirements": [r], "judgement": {"x": 1}}, badges=[{"mark": "?", "cls": "unk", "text": "No smoking", "evidence": ""}])
    assert look_items(unc)[0].kind == "unclear"


def test_eligibility_other_than_eligible_needs_a_look():
    p = _p(1, "A", eligibility={"verdict": "cannot_import", "label": "cannot import", "checks": []}, why_line="cannot import: Registered 2017")
    it = look_items(p)[0]
    assert (it.text, it.kind, it.where) == ("Cannot import: Registered 2017", "fail", "pair")
    q = _p(2, "B", eligibility={"verdict": "confirm", "label": "confirm import",
                                "checks": [{"state": "pass", "detail": "ok"}, {"state": "confirm", "detail": "build month"}]})
    it = look_items(q)[0]
    assert it.kind == "unclear" and it.text == "Confirm import: build month"


def test_flags_hints_and_a_missing_amount_need_a_look():
    p = _p(1, "A", soft={"flags": ["49,800 km is on a band edge"], "hints": [{"level": "info", "message": "n"}, {"level": "warn", "message": "repair"}]},
           proposed_bid_jpy_k=None)
    items = look_items(p)
    assert LookItem("49,800 km is on a band edge", "check", "grid") in items
    assert LookItem("repair", "check", "sheet") in items
    assert all(i.text != "n" for i in items)
    assert LookItem("No amount from the grid. Type one with E.", "check", "grid") in items


def test_waiting_lots():
    a = _p(1, "A", soft={"images_pending": True})
    b = _p(2, "B", soft={"judge_pending": True})
    assert lane_of(a, look_items(a)) == "wait" and lane_of(b, look_items(b)) == "wait"


def test_manual_mode_checklist_needs_a_look():
    p = _p(1, "A", soft={"mode": "manual", "checklist": ["Dark Color Seats Only", "auto-skip rule in force: skip: km > 120k"]})
    items = look_items(p)
    assert [i.text for i in items] == ["Dark Color Seats Only"]


def test_lots_without_an_auction_day_are_grouped_first_not_dropped():
    rows = [_p(1, "A", facts={"auction_date": "20261027", "auction_place": "USS Tokyo"}),
            _p(2, "B", facts={"auction_date": "20261026", "auction_place": "JU Gifu"}),
            _p(3, "C"), _p(4, "D")]
    sessions = build_desk(rows, [], triage_sort_key, NOW)
    assert [s.key for s in sessions] == ["unknown", "20261026|JU Gifu", "20261027|USS Tokyo"]
    assert sessions[0].label == "Auction day not known" and sum(len(v) for v in sessions[0].lanes.values()) == 2
    assert sessions[1].label == "Mon 26 Oct · JU Gifu"


def test_two_guides_make_one_row_and_the_other_is_never_decided():
    a, b = _p(1, "X", guide_id=1), _p(2, "X", guide_id=2)
    s = build_desk([a, b], [], triage_sort_key, NOW)[0]
    lots = [l for v in s.lanes.values() for l in v]
    assert len(lots) == 1 and lots[0].siblings == [({1, 2} - {lots[0].p["id"]}).pop()]
    done = _p(9, "X", decision="approved", decided_at=(NOW - timedelta(hours=1)).isoformat())
    s = build_desk([a], [done], triage_sort_key, NOW)[0]
    covered = [l for l in s.lanes["done"] if l.covered_by == 9]
    assert len(covered) == 1 and covered[0].p["id"] == 1
    assert s.undecided() == 0


def test_decided_lane_holds_the_last_24_hours():
    d = [_p(1, "A", decision="approved", decided_at=(NOW - timedelta(hours=1)).isoformat()),
         _p(2, "B", decision="approved", decided_at=(NOW - timedelta(hours=30)).isoformat()),
         _p(3, "C", decision="deleted", decided_at=(NOW - timedelta(hours=2)).isoformat())]
    s = build_desk([], d, triage_sort_key, NOW)[0]
    assert [l.p["id"] for l in s.lanes["done"]] == [1, 3]
    assert [l.cleared for l in s.lanes["done"]] == [False, True]


def test_lane_order_follows_the_sort_key():
    rows = [_p(1, "A", facts={"grade": 4, "km": 30000}), _p(2, "B", facts={"grade": 4.5, "km": 90000}),
            _p(3, "C", facts={"grade": 4, "km": 10000})]
    s = build_desk(rows, [], triage_sort_key, NOW)[0]
    assert [l.p["id"] for l in s.lanes["clear"]] == [2, 3, 1]


def test_decided_window_compares_in_utc():
    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    d = [_p(1, "A", decision="approved", decided_at="2026-10-26T11:00:00+00:00"),
         _p(2, "B", decision="approved", decided_at="2026-10-25T11:59:00+00:00"),
         _p(3, "C", decision="approved", decided_at="2026-10-25T12:30:00+00:00")]
    s = build_desk([], d, triage_sort_key, now)[0]
    assert [l.p["id"] for l in s.lanes["done"]] == [1, 3]
    # a naive now is read as UTC too
    s = build_desk([], d, triage_sort_key, datetime(2026, 10, 26, 12, 0))[0]
    assert [l.p["id"] for l in s.lanes["done"]] == [1, 3]


def test_a_requirement_without_a_badge_needs_a_look():
    r = _req("r0", "Dark seats only", "sheet")
    p = _p(1, "A", soft={"requirements": [r]}, badges=[])
    assert look_items(p) == [LookItem("Dark seats only", "unclear", "sheet")]


def test_an_unreadable_requirement_needs_a_look_and_does_not_raise():
    p = _p(1, "A", soft={"requirements": [{"text": "Odd one", "bogus": 1}, {"nothing": 1}, "junk"]})
    items = look_items(p)
    assert [i.kind for i in items] == ["unclear"] * 3
    assert [i.text for i in items] == ["Odd one", "A requirement could not be read", "A requirement could not be read"]
    assert lane_of(p, items) == "look"


def test_judge_mode_without_a_verdict_is_unclear_but_pending_judge_is_not():
    r = _req("r0", "Dark seats only", "sheet")
    b = [{"mark": "·", "cls": "unk", "text": "Dark seats only", "evidence": ""}]
    assert look_items(_p(1, "A", soft={"requirements": [r]}, badges=b))[0].kind == "unclear"
    items = look_items(_p(2, "B", soft={"requirements": [r], "judge_pending": True}, badges=b))
    assert [i.kind for i in items] == ["check", "wait"] or {i.kind for i in items} == {"check", "wait"}


def test_a_skip_recommendation_is_a_look_unless_something_already_fails():
    assert look_items(_p(1, "A", recommendation="skip")) == [LookItem("The model recommends skip.", "check", "page")]
    p = _p(2, "B", recommendation="skip", eligibility={"verdict": "cannot_import", "label": "x", "checks": []}, why_line="cannot import: r")
    assert [i.kind for i in look_items(p)] == ["fail"]


def test_photo_number_falls_back_to_the_soft_ladders():
    r = _req("r0", "Cruise control", "ladder", topic="cruise_control")
    ladder = {"id": "r0", "steps": [{"step": "photo", "decided": True, "photo_no": 3}]}
    p = _p(1, "A", soft={"requirements": [r], "judgement": {"x": 1}, "ladders": [ladder]},
           badges=[{"mark": "✓", "cls": "ok", "text": "Cruise control", "evidence": ""}])
    assert look_items(p)[0].photo_no == 3


def test_the_sibling_choice_follows_the_sort_key():
    a = _p(1, "X", guide_id=1, facts={"grade": 3, "km": 90000})
    b = _p(2, "X", guide_id=2, facts={"grade": 4.5, "km": 10000})
    lot = build_desk([a, b], [], triage_sort_key, NOW)[0].lanes["clear"][0]
    assert lot.p["id"] == 2 and lot.siblings == [1]


# -- Task 5: the bid sheet ---------------------------------------------------------------------------------

def _a(pid, ref, amount, date="", place="", **over):
    return _p(pid, ref, decision="approved", approved_bid_jpy_k=amount,
              facts={"auction_date": date, "auction_place": place}, **over)


def test_bid_sheet_groups_by_auction_and_sums_exposure():
    groups = bid_sheet([_a(1, "A", 1000, "20261030", "USS Tokyo"), _a(2, "B", 1220, "20261030", "USS Tokyo"), _a(3, "C", 900)])
    assert len(groups) == 2
    assert groups[0].label == "Auction day not known" and groups[0].exposure_k == 900
    assert groups[1].exposure_k == 2220 and [r.amount_k for r in groups[1].rows] == [1000, 1220]


def test_bid_sheet_states():
    rows = bid_sheet([
        _a(1, "A", 1000),
        _a(2, "B", 1000, placement={"ok": True, "dry_run": True, "message": "typed", "screenshot": None}),
        _a(3, "C", 1000, placement={"ok": False, "dry_run": True, "message": "start price changed", "screenshot": None}),
        _a(4, "D", 1000, placed_at="2026-10-26T01:00:00+00:00", placement={"ok": True, "dry_run": False, "message": "", "screenshot": None}),
    ])[0].rows
    assert [r.state for r in rows] == ["approved", "dry_run", "blocked", "placed"]
    assert rows[2].message == "start price changed"


def test_bid_sheet_screenshot_url_stays_inside_evidence():
    shot = "C:/data/evidence/lotdir/shot.png"
    r = bid_sheet([_a(1, "A", 1000, placement={"ok": True, "dry_run": True, "screenshot": shot},
                      evidence={"dir": "C:/x/evidence/lotdir"})])[0].rows[0]
    assert r.screenshot == "/evidence/lotdir/shot.png"
