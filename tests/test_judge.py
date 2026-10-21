"""The judgement of one lot with a stubbed model: prompt content, the deterministic decision, amounts."""
import json
from pathlib import Path

from auction_bot.carbarn.models import Modifier, parse_guide_tree
from auction_bot.config import Config
from auction_bot.judge.candidates import candidates_for
from auction_bot.judge.client import JudgeClient, Provider, StoreLedger
from auction_bot.judge.judge import Judgement, build_messages, decide, judge_lot
from auction_bot.judge.requirements import odd_flags, requirements_for
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.rules.hard import evaluate_hard
from auction_bot.store import Store

FIX = Path(__file__).parent / "fixtures"


def _gdh206():
    tree = json.loads((FIX / "guide_223_tree.json").read_text(encoding="utf-8"))
    item = next(i for i in json.loads((FIX / "guide_list_subset.json").read_text(encoding="utf-8"))["content"] if i["id"] == 223)
    rule = parse_guide_tree(tree, item)
    rule.variants[0].modifiers.append(Modifier(id=99, variant_id=None, year_from=None, year_to=None, odo_from=None, odo_to=None,
                                               conditions=[], amount_jpy_k=-220, percent=None, no_bid=False, strict=False,
                                               note="No Pro Pilot", active=True, stale=False))
    return rule


def _facts(**kw) -> VehicleFacts:
    base = dict(ref_no="r", url="u", year=2020, make="Toyota", model="Hiace", variant_text="Van DX GL Package 4WD",
                chassis="GDH206V", km=120_000, grade_raw="4", start_price_jpy=1_110_000, colour="White",
                translator={"VehicleCondition": "light scratches", "sheet_url": "x"})
    base.update(kw)
    return VehicleFacts(**base)


def _cfg(tmp_path) -> Config:
    cfg = Config()
    cfg.playbook_dir = tmp_path / "playbook"
    cfg.override_dir = tmp_path / "overrides"
    cfg.db_path = tmp_path / "bot.sqlite"
    cfg.judge = {"max_photos": 2}
    return cfg


def _images(tmp_path):
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"\xff\xd8\xff" + bytes(50))
    photos = []
    for i in range(3):
        p = tmp_path / f"photo_{i:02d}.jpg"
        p.write_bytes(b"\xff\xd8\xff" + bytes(10 + i))
        photos.append(p)
    return sheet, photos


def test_build_messages_carries_requirements_playbook_and_images_but_no_grid(tmp_path):
    cfg = _cfg(tmp_path)
    rule = _gdh206()
    f = _facts()
    hard = evaluate_hard(f, rule)
    reqs = requirements_for(rule, hard)
    flags = odd_flags(rule, hard, f)
    cands = candidates_for(rule, f, hard)
    sheet, photos = _images(tmp_path)
    messages, images = build_messages(f, rule, hard, reqs, flags, cands, "# How the buyer decides (his words)\n- dark means black",
                                      sheet, photos, {"available": True, "seats": 5, "model_code": "GDH206V"}, max_photos=2)
    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    parts = messages[1]["content"]
    text = "\n".join(p["text"] for p in parts if p["type"] == "text")
    assert "r1 [must, look at: photos]: No sticker paint" in text and "chip:no-pro-pilot [chip, look at: photos]: No Pro Pilot (chip -220K)" in text
    assert "exactly on the band boundary" in text and "dark means black" in text and "seats=5" in text and "light scratches" in text
    assert f"{hard.bid_jpy_k:,}K: grid" in text and "next odometer band" in text
    # Only the candidate numbers appear, never other grid cells (the 2018 row's values, for instance).
    for cell in rule.variants[0].cells.values():
        if cell.bid_jpy_k and cell.bid_jpy_k not in {c.amount_k for c in cands}:
            assert f"{cell.bid_jpy_k:,}K" not in text
    assert images == ["sheet.jpg", "photo_00.jpg", "photo_01.jpg"]  # max_photos 2 plus the sheet
    assert sum(1 for p in parts if p["type"] == "image_url") == 3
    assert "Photo 1:" in text and "Photo 3:" not in text


def _judgement(**kw) -> dict:
    base = {"requirements": [{"id": "r1", "verdict": "pass", "evidence": "photo 1: painted white", "confidence": "high"},
                             {"id": "r2", "verdict": "pass", "evidence": "sheet: 5 seats", "confidence": "high"},
                             {"id": "r3", "verdict": "pass", "evidence": "sheet: 4WD", "confidence": "medium"}],
            "chips": [{"id": "chip:no-pro-pilot", "applies": "no", "evidence": "photo 2 shows the button"}],
            "odd_cases": [], "amount_k": None, "amount_reason": "", "recommendation": "bid", "confidence": "high",
            "summary": "clean van", "sheet_notes": ""}
    base.update(kw)
    return Judgement.model_validate(base).model_dump()


def test_decide_mapping_and_amounts():
    rule = _gdh206()
    f = _facts()
    hard = evaluate_hard(f, rule)
    reqs = requirements_for(rule, hard)
    cands = candidates_for(rule, f, hard)
    grid = hard.bid_jpy_k
    rec, amount, notes = decide(_judgement(), reqs, cands, hard)
    assert (rec, amount) == ("bid", grid)
    # A confident fail on a must requirement: skip.
    j = _judgement(requirements=[{"id": "r1", "verdict": "fail", "evidence": "photo 1: sticker wrap", "confidence": "high"},
                                 {"id": "r2", "verdict": "pass"}, {"id": "r3", "verdict": "pass"}])
    rec, _, notes = decide(j, reqs, cands, hard)
    assert rec == "skip" and "fails: No sticker paint (photo 1: sticker wrap)" in notes[0]
    # Unclear or missing verdicts: review.
    j = _judgement(requirements=[{"id": "r1", "verdict": "unclear", "evidence": "not visible"}])
    rec, _, notes = decide(j, reqs, cands, hard)
    assert rec == "review" and any("no verdict for: r2, r3" in n for n in notes) and any("unclear: No sticker paint" in n for n in notes)
    # A chip that applies changes the amount deterministically.
    j = _judgement(chips=[{"id": "chip:no-pro-pilot", "applies": "yes", "evidence": "no button on the wheel"}])
    rec, amount, notes = decide(j, reqs, cands, hard)
    assert amount == grid - 220 and "chips applied: No Pro Pilot -220K" in notes
    # The model may pick a candidate (the next band) but nothing outside the list.
    nxt = next(c.amount_k for c in cands if c.label == "next odometer band")
    j = _judgement(amount_k=nxt, amount_reason="odometer will rise before delivery")
    assert decide(j, reqs, cands, hard)[1] == nxt
    j = _judgement(amount_k=999_999)
    rec, amount, notes = decide(j, reqs, cands, hard)
    assert amount == grid and any("not among the candidates" in n for n in notes)
    # Low overall confidence or an odd case without a recommendation: review.
    assert decide(_judgement(confidence="low"), reqs, cands, hard)[0] == "review"
    assert decide(_judgement(odd_cases=[{"what": "dump body?", "recommendation": "", "confidence": "low"}]), reqs, cands, hard)[0] == "review"
    assert decide(_judgement(odd_cases=[{"what": "km near cap", "recommendation": "use the next band", "confidence": "high"}]), reqs, cands, hard)[0] == "bid"


def test_judge_lot_with_a_stubbed_model(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    store = Store(cfg.db_path)
    rule = _gdh206()
    f = _facts()
    hard = evaluate_hard(f, rule)
    sheet, photos = _images(tmp_path)
    evidence = {"sheet": str(sheet), "photos": [str(p) for p in photos]}
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    seen = {}

    def transport(p, messages, response_format, max_tokens):
        seen["provider"] = p.name
        seen["images"] = sum(1 for part in messages[1]["content"] if part["type"] == "image_url")
        return json.dumps(_judgement(chips=[{"id": "chip:no-pro-pilot", "applies": "yes", "evidence": "no button"}],
                                     summary="dark cloth seats, no Pro Pilot")), {"input": 5000, "output": 300}

    client = JudgeClient([Provider("gemini", "http://x", "m", "GEMINI_API_KEY")], ledger=StoreLedger(store), transport=transport, sleep=lambda s: None)
    res = judge_lot(client, cfg, store, f, rule, hard, evidence, ocr={"available": True, "seats": 5})
    assert res.status == "NEEDS_REVIEW" and res.recommendation == "bid" and res.proposed_bid_jpy_k == hard.bid_jpy_k - 220
    assert res.provider == "gemini" and res.usage == {"input": 5000, "output": 300} and seen["images"] == 3
    assert res.judgement["summary"] == "dark cloth seats, no Pro Pilot" and res.images_used == ["sheet.jpg", "photo_00.jpg", "photo_01.jpg"]
    assert [r["id"] for r in res.requirements][:3] == ["r1", "r2", "r3"] and res.requirements_hash and res.candidates
    assert store.model_calls_today("gemini") == 1
    # No provider available: the lot still goes to review with the reason, nothing raised.
    monkeypatch.delenv("GEMINI_API_KEY")
    res = judge_lot(client, cfg, store, f, rule, hard, evidence)
    assert res.judgement is None and res.recommendation == "review" and "no GEMINI_API_KEY" in res.unavailable
    assert res.proposed_bid_jpy_k == hard.bid_jpy_k and res.notes[0].startswith("model unavailable")
    store.close()


def test_prompt_has_the_approval_section_and_decide_treats_it_as_must(tmp_path):
    from datetime import date
    from auction_bot.rules.eligibility import check_lot
    rule = _gdh206()
    f = _facts()
    hard = evaluate_hard(f, rule)
    elig = check_lot(rule, rule.variants[0], f, None, date(2026, 10, 20))
    reqs = requirements_for(rule, hard, eligibility=elig)
    messages, _ = build_messages(f, rule, hard, reqs, [], candidates_for(rule, f, hard), "", None, [], None)
    text = "\n".join(p["text"] for p in messages[1]["content"] if p["type"] == "text")
    assert "# Import approval conditions (ROVER SEV / Model Report)" in text and "elig:sev [eligibility, look at: sheet]" in text
    assert "workshop obligation" in messages[0]["content"]
    j = _judgement(requirements=[{"id": "r1", "verdict": "pass"}, {"id": "r2", "verdict": "pass"}, {"id": "r3", "verdict": "pass"},
                                 *({"id": r.id, "verdict": "pass"} for r in reqs if r.kind == "eligibility" and r.id != "elig:sev"),
                                 {"id": "elig:sev", "verdict": "fail", "evidence": "sheet: 2WD", "confidence": "high"}])
    rec, _, notes = decide(j, reqs, candidates_for(rule, f, hard), hard)
    assert rec == "skip" and "fails:" in notes[0] and "4x4 Campervan only" in notes[0]


def _alt_reqs():
    from auction_bot.judge.requirements import Requirement
    base = [Requirement(f"r{i}", f"rule {i}", "sheet", "must") for i in (1, 2, 3)]
    return base + [Requirement("elig:sev:1", "SEV-1: 2WD only", "sheet", "eligibility"),
                   Requirement("elig:sev:2", "SEV-2: 4x4 Campervan only", "sheet", "eligibility")]


def _alt_decide(v1, v2):
    rule = _gdh206()
    f = _facts()
    hard = evaluate_hard(f, rule)
    j = _judgement(requirements=[{"id": "r1", "verdict": "pass"}, {"id": "r2", "verdict": "pass"}, {"id": "r3", "verdict": "pass"},
                                 {"id": "elig:sev:1", "verdict": v1, "evidence": "sheet: 2WD", "confidence": "high"},
                                 {"id": "elig:sev:2", "verdict": v2, "evidence": "sheet: 2WD", "confidence": "high"}])
    return decide(j, _alt_reqs(), candidates_for(rule, f, hard), hard)


def test_numbered_approval_pairs_are_alternatives():
    assert _alt_decide("pass", "fail")[0] == "bid"
    rec, _, notes = _alt_decide("unclear", "fail")
    assert rec == "review" and any("confirm which approval pair applies" in n for n in notes)
    rec, _, notes = _alt_decide("fail", "fail")
    assert rec == "skip" and "no approval pair fits" in notes[0] and "4x4 Campervan only" in notes[0]


def test_bare_approval_fail_still_skips_and_alternatives_line_is_conditional():
    from auction_bot.judge.requirements import Requirement
    rule = _gdh206()
    f = _facts()
    hard = evaluate_hard(f, rule)
    reqs = [Requirement("r1", "rule 1", "sheet", "must"), Requirement("elig:sev", "SEV-1: 4x4 only", "sheet", "eligibility")]
    j = _judgement(requirements=[{"id": "r1", "verdict": "pass"},
                                 {"id": "elig:sev", "verdict": "fail", "evidence": "sheet: 2WD", "confidence": "high"}])
    assert decide(j, reqs, candidates_for(rule, f, hard), hard)[0] == "skip"
    line = "alternative approvals"
    for rs, expected in ((reqs, False), (_alt_reqs(), True)):
        messages, _ = build_messages(f, rule, hard, rs, [], candidates_for(rule, f, hard), "", None, [], None)
        text = "\n".join(p["text"] for p in messages[1]["content"] if p["type"] == "text")
        assert (line in text) is expected
