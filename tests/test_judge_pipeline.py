"""Judge mode in the scan, the pending pass, and replay, with a stubbed model (no network)."""
import json
from pathlib import Path

import auction_bot.judge.client as client_mod
from auction_bot.pipeline import judge_pending, reevaluate, scan
from auction_bot.store import Store
from tests.test_pipeline_offline import FakeBrowser, cfg  # noqa: F401  (fixture)


def _answer(chip_applies="no", summary="dark cloth seats", verdict="pass"):
    return json.dumps({
        "requirements": [{"id": "r1", "verdict": verdict, "evidence": "photo 2: painted body", "confidence": "high"},
                         {"id": "r2", "verdict": "pass", "evidence": "sheet: seats", "confidence": "high"},
                         {"id": "r3", "verdict": "pass", "evidence": "sheet: 4WD mark", "confidence": "medium"},
                         {"id": "note:1", "verdict": "pass", "evidence": "photos: no ambulance fit-out", "confidence": "medium"}],
        "chips": [], "odd_cases": [], "amount_k": None, "amount_reason": "", "recommendation": "bid",
        "confidence": "high", "summary": summary, "sheet_notes": ""})


def _stub(monkeypatch, calls, reply=None):
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    def transport(p, messages, response_format, max_tokens):
        calls.append({"provider": p.name, "images": sum(1 for part in messages[1]["content"] if part["type"] == "image_url"),
                      "text": "\n".join(part["text"] for part in messages[1]["content"] if part["type"] == "text")})
        return (reply or _answer()), {"input": 4000, "output": 200}

    monkeypatch.setattr(client_mod, "openai_transport", transport)


def test_judge_mode_opens_the_page_for_photos_and_stores_the_judgement(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    cfg.judge = {"max_photos": 3, "inline": True}
    calls = []
    _stub(monkeypatch, calls)
    browser = FakeBrowser()
    store = Store(cfg.db_path)
    stats = scan(cfg, browser, store, ["GDH206"], limit_per_model=2)
    assert stats.errors == [] and stats.model_calls == 2 and stats.pages == 2
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 2 and all(r["stage"] == "judge" for r in rows)
    soft = json.loads(rows[0]["soft_json"])
    assert soft["mode"] == "judge" and soft["recommendation"] == "bid" and soft["proposed_bid_jpy_k"] == rows[0]["bid_jpy_k"]
    assert soft["judgement"]["summary"] == "dark cloth seats" and soft["provider"] == "gemini" and not soft["judge_pending"]
    assert [r["id"] for r in soft["requirements"]][:3] == ["r1", "r2", "r3"] and soft["requirements_hash"]
    assert soft["checklist"] and soft["ocr"] is not None  # the reviewer's checklist is still there
    ev = json.loads(rows[0]["evidence_json"])
    assert len(ev["photos"]) >= 3 and (Path(ev["dir"]) / "judge.json").exists()
    assert soft["images_used"][0] == "sheet.jpg" and len(soft["images_used"]) == 4  # sheet + 3 photos
    assert calls[0]["images"] == 4 and "No sticker paint" in calls[0]["text"] and "Skip ambulance conversions" in calls[0]["text"]
    assert store.model_calls_today("gemini") == 2
    store.close()


def test_routine_lot_skips_the_model(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["guides"][0]["tree"]["notes"] = "2, 3, 5 or 6 seats"  # decidable from the sheet's seat count
    snap.write_text(json.dumps(data), encoding="utf-8")
    (cfg.override_dir / "GDH206.md").write_text("# notes\n", encoding="utf-8")
    import auction_bot.pipeline as pl
    monkeypatch.setattr(pl, "odd_flags", lambda *a, **k: [])  # the fixture lot sits near the cap; pretend it is unremarkable
    calls = []
    _stub(monkeypatch, calls)
    browser = FakeBrowser()
    store = Store(cfg.db_path)
    stats = scan(cfg, browser, store, ["GDH206"], limit_per_model=1)
    assert calls == [] and stats.model_calls == 0
    soft = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])
    assert soft["mode"] == "judge" and soft["judgement"] is None and soft["recommendation"] == "bid"
    assert soft["judge_notes"][0].startswith("routine") and not soft["judge_pending"]
    store.close()


def test_pending_pass_fills_lots_judged_later(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    cfg.judge = {"max_photos": 2, "inline": False}
    calls = []
    _stub(monkeypatch, calls)
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    assert stats.model_calls == 0
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    ids = [r["id"] for r in rows]
    assert all(json.loads(r["soft_json"])["judge_pending"] for r in rows)
    # The scan fetched the photos already (only the model call was deferred), so one lot can be judged
    # without a browser. Strip the other lot's photos to exercise the "photos are needed" path.
    ev1 = json.loads(store.proposal(ids[1])["evidence_json"])
    for photo in ev1["photos"]:  # the pending pass reads the bundle on disk
        Path(photo).unlink()
    st = judge_pending(cfg, store)
    assert st.judged == 1 and st.checked == 2 and st.pages == 0 and len(calls) == 1
    assert "photos are needed" in json.loads(store.proposal(ids[1])["soft_json"])["judge_notes"][0]
    # With a browser: the page is opened for the missing photos and the judgement stored on the same row.
    st = judge_pending(cfg, store, browser=FakeBrowser())
    assert st.judged == 1 and st.pages == 1 and len(calls) == 2
    for pid in ids:
        soft = json.loads(store.proposal(pid)["soft_json"])
        assert not soft["judge_pending"] and soft["judgement"] and soft["recommendation"] == "bid"
    assert [r["id"] for r in store.list_proposals(decision="pending", status="NEEDS_REVIEW")] == ids  # not superseded
    # A second pass has nothing to do.
    assert judge_pending(cfg, store, browser=FakeBrowser()).checked == 0
    store.close()


def test_model_unavailable_leaves_the_lot_for_review(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    assert stats.errors == [] and stats.review == 1
    soft = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])
    assert soft["judgement"] is None and soft["judge_pending"] and soft["recommendation"] == "review"
    assert "no GEMINI_API_KEY" in soft["unavailable"] and soft["proposed_bid_jpy_k"] == 1240
    store.close()


def test_replay_keeps_or_refreshes_the_judgement(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    calls = []
    _stub(monkeypatch, calls)
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    assert len(calls) == 1
    # Same requirements: replay keeps the stored judgement and calls nothing.
    st = reevaluate(cfg, store, ["GDH206"], apply=True)
    assert st.changed == 0 and len(calls) == 1
    # A new text rule changes the requirements: the row is superseded and marked for a fresh judgement.
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["guides"][0]["tree"]["notes"] += " | Only dark coloured seats"
    data["guides"][0]["tree"]["updatedAt"] = "2026-09-28T00:00:00"
    snap.write_text(json.dumps(data), encoding="utf-8")
    st = reevaluate(cfg, store, ["GDH206"], apply=True)
    assert st.changed == 1 and st.applied == 1 and len(calls) == 1
    soft = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])
    assert soft["judge_pending"] and soft["judgement"] is None and any(r["text"] == "Only dark coloured seats" for r in soft["requirements"])
    # The pending pass judges it (the photos are already on disk, so no page is opened).
    st = judge_pending(cfg, store, browser=FakeBrowser())
    assert st.judged == 1 and st.pages == 0 and len(calls) == 2
    store.close()
