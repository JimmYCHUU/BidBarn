"""Judge mode in the scan, the pending pass, and replay, with a stubbed model (no network)."""
import json
from pathlib import Path

import auction_bot.catalog.client as cat_client
import auction_bot.judge.client as client_mod
from auction_bot.config import cache_dir
from auction_bot.nichibo.cache import PageCache
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.nichibo.parsers import parse_detail_page
from auction_bot.nichibo.vehicle import download_evidence, photos_complete
from auction_bot.pipeline import judge_pending, reevaluate, scan
from auction_bot.store import Store
from tests.test_catalog_lookup import PAGES
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
    assert len(ev["photos"]) >= 3 and (Path(ev["dir"]) / f"guide_{rows[0]['guide_id']}" / "judge.json").exists()
    assert soft["images_used"][0] == "sheet_2.jpg" and len(soft["images_used"]) == 4  # sheet + 3 photos
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


def test_replay_sees_an_eligibility_change(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    cfg.eligibility = {"enabled": True, "judge_text": False}
    calls = []
    _stub(monkeypatch, calls)
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    assert reevaluate(cfg, store, ["GDH206"], apply=True).changed == 0
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    for b in data["guides"][0]["tree"]["variants"][0]["blocks"]:
        if b["selected"]:
            b["sevVariantDetails"] = "4x4 Campervan only, 2 seats"
    snap.write_text(json.dumps(data), encoding="utf-8")
    st = reevaluate(cfg, store, ["GDH206"], apply=True)
    assert st.changed == 1 and len(calls) == 1   # no model call for an eligibility text change in manual-text mode
    assert st.changes[0].endswith("(eligibility changed)")
    store.close()


def test_judge_mode_sends_the_pair_text_and_never_skips_for_it(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    cfg.eligibility = {"enabled": True, "judge_text": True}
    calls = []
    reply = json.loads(_answer()); reply["requirements"].append({"id": "elig:sev", "verdict": "fail", "evidence": "sheet: 2WD", "confidence": "high"})
    _stub(monkeypatch, calls, reply=json.dumps(reply))
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    assert "Import approval conditions" in calls[0]["text"] and "4x4 Campervan only" in calls[0]["text"]
    row = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]
    soft = json.loads(row["soft_json"])
    assert row["status"] == "NEEDS_REVIEW" and row["stage"] == "judge" and soft["recommendation"] == "skip"   # a model "skip" is a label, never SKIP
    assert stats.review >= 1 and any(r["id"] == "elig:sev" for r in soft["requirements"]) and soft["eligibility"]["pair"]
    store.close()


class CountingBrowser(FakeBrowser):
    def __init__(self):
        super().__init__(); self.images = 0

    def get_bytes(self, url, headers=None):
        self.images += 1
        return super().get_bytes(url, headers)


def test_ladder_lots_get_every_photo_and_a_short_bundle_is_extended(cfg, monkeypatch):
    from auction_bot.nichibo.vehicle import photos_complete
    cfg.sheet_check = "judge"
    cfg.judge = {"max_photos": 3, "max_photos_ladder": 40, "inline": True}
    cfg.catalog = {"enabled": False}
    calls = []
    _stub(monkeypatch, calls)
    browser = CountingBrowser()
    store = Store(cfg.db_path)
    scan(cfg, browser, store, ["GDH206"], limit_per_model=1)           # plain guide: 3 photos
    ev = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["evidence_json"])
    assert len(ev["photos"]) == 3 and ev["photo_total"] == 10 and not photos_complete(ev, 40) and photos_complete(ev, 3)
    before = browser.images
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["guides"][0]["tree"]["notes"] += "|Cruise control"
    data["guides"][0]["tree"]["updatedAt"] = "2026-10-20T00:00:00"
    snap.write_text(json.dumps(data), encoding="utf-8")
    scan(cfg, browser, store, ["GDH206"], limit_per_model=1, rescan=True)   # now a ladder lot: the bundle is extended to all 10
    ev = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["evidence_json"])
    assert len(ev["photos"]) == 10 and ev["extended"] == 7 and browser.images - before == 7   # only the missing seven were fetched
    assert photos_complete(ev, 40) and calls[-1]["images"] == 11                                   # sheet + 10 photos went to the model
    store.close()


def _ladder_fixture(cfg, note="Cruise control"):
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["guides"][0]["tree"]["notes"] = "No sticker paint|" + note
    snap.write_text(json.dumps(data), encoding="utf-8")


def _catalog_stub(monkeypatch, pages=PAGES, fetched=None):
    def fetch(url):
        if fetched is not None:
            fetched.append(url)
        if url not in pages:
            raise RuntimeError("404")
        return pages[url].read_text(encoding="utf-8")
    monkeypatch.setattr(cat_client, "default_fetch", fetch)


def _ladder_reply(photo_state="no", photo_no=4):
    r = json.loads(_answer())
    r["requirements"] = [x for x in r["requirements"] if x["id"] != "r2"]
    r["observations"] = [{"id": "r2", "sheet": {"state": "not_mentioned"}, "photo": {"state": photo_state, "photo_no": photo_no, "evidence": "bare spokes"}}]
    return json.dumps(r)


def test_scan_looks_the_catalogue_up_before_the_call_and_stores_the_ladder(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    cfg.catalog = {"enabled": True, "base_url": "https://www.kurumaerabi.com", "cache_days": 90, "pace_seconds": [0, 0]}
    _ladder_fixture(cfg)
    _catalog_stub(monkeypatch)
    calls = []
    _stub(monkeypatch, calls, reply=_ladder_reply())
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    assert stats.errors == [] and len(calls) == 1
    assert "ladder cruise_control/plain" in calls[0]["text"] and "# Maker catalogue" in calls[0]["text"]
    row = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]
    soft = json.loads(row["soft_json"])
    assert row["status"] == "NEEDS_REVIEW" and soft["recommendation"] == "skip" and soft["photos_complete"] is True
    assert soft["ladders"][0]["id"] == "r2" and soft["ladders"][0]["verdict"] == "fail" and "photo 4: no (bare spokes)" in soft["ladders"][0]["evidence"]
    assert soft["catalog"]["source"] == "kurumaerabi.com" and soft["catalog"]["key"]   # the sitemap names a GDH206V page, the fixture set has none: unknown, but recorded
    assert soft["catalog"]["cruise"]["status"] == "unknown" and "404" in soft["catalog"]["note"]   # urls holds only the pages actually read
    assert (Path(json.loads(row["evidence_json"])["dir"]) / "catalog.json").exists()
    store.close()


def test_catalogue_failure_never_fails_the_scan(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    cfg.catalog = {"enabled": True, "base_url": "https://www.kurumaerabi.com", "pace_seconds": [0, 0]}
    _ladder_fixture(cfg)
    monkeypatch.setattr(cat_client, "default_fetch", lambda url: (_ for _ in ()).throw(OSError("network down")))
    calls = []
    _stub(monkeypatch, calls, reply=_ladder_reply(photo_state="maybe"))
    store = Store(cfg.db_path)
    stats = scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    soft = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])
    assert stats.errors == [] and len(calls) == 1 and soft["recommendation"] == "review"
    assert soft["catalog"]["cruise"]["status"] == "unknown" and "network down" in soft["catalog"]["note"]
    assert soft["ladders"][0]["verdict"] == "unclear"
    fetched = []
    monkeypatch.setattr(cat_client, "default_fetch", lambda url: fetched.append(url) or "")
    assert reevaluate(cfg, store, ["GDH206"], apply=True).changed == 0 and fetched == [] and len(calls) == 1   # replay never reads the catalogue
    store.close()


def test_replay_reresolves_ladders_from_stored_observations_without_the_model(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    cfg.catalog = {"enabled": False}
    _ladder_fixture(cfg)
    calls = []
    _stub(monkeypatch, calls, reply=_ladder_reply(photo_state="maybe"))
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    assert json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])["recommendation"] == "review"
    assert reevaluate(cfg, store, ["GDH206"], apply=True).changed == 0 and len(calls) == 1
    import auction_bot.judge.judge as judge_mod
    real = judge_mod.resolve
    monkeypatch.setattr(judge_mod, "resolve", lambda req, obs, maker, photos_complete=True: real(req, {**(obs or {"id": req.id}), "photo": {"state": "yes", "photo_no": 1}}, maker, photos_complete))
    st = reevaluate(cfg, store, ["GDH206"], apply=True)       # the ladder now passes: the recommendation changes, no model call
    assert st.changed == 1 and len(calls) == 1 and st.changes[0].endswith("(recommendation changed)")
    soft = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])
    assert soft["recommendation"] == "bid" and soft["ladders"][0]["verdict"] == "pass" and soft["judgement"]
    store.close()


def test_pending_pass_reuses_the_stored_finding(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    cfg.judge = {"max_photos": 2, "inline": False}
    cfg.catalog = {"enabled": True, "base_url": "https://www.kurumaerabi.com", "pace_seconds": [0, 0]}
    _ladder_fixture(cfg)
    fetched = []
    _catalog_stub(monkeypatch, fetched=fetched)   # the sitemap answers, the GDH206V page 404s: a real (unknown) finding
    calls = []
    _stub(monkeypatch, calls, reply=_ladder_reply())
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    n = len(fetched)
    assert n >= 1 and calls == []
    st = judge_pending(cfg, store, browser=FakeBrowser())
    assert st.judged == 1 and len(fetched) == n            # the finding came from soft_json, no second lookup
    soft = json.loads(store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["soft_json"])
    assert soft["ladders"] and soft["catalog"]["key"]
    store.close()


def test_catalog_finding_reuses_only_a_real_result(cfg, monkeypatch):
    import auction_bot.pipeline as pl
    from auction_bot.carbarn.client import load_rules
    from auction_bot.catalog.lookup import candidate_codes, finding_key, unknown
    from auction_bot.judge.requirements import ladder_topics, requirements_for
    from auction_bot.nichibo.facts import VehicleFacts
    _ladder_fixture(cfg)
    rule = load_rules(cfg, None, ["GDH206"])[0][0]
    reqs = requirements_for(rule)
    facts = VehicleFacts(ref_no="r", url="u", year=2020, make="Toyota", model="Hiace", variant_text="", chassis="GDH206V", km=1, grade_raw="4")
    key = finding_key(candidate_codes(facts, rule), facts.year, ladder_topics(reqs))
    looked = []
    monkeypatch.setattr(pl, "lookup", lambda client, f, r, topics: looked.append(client) or unknown("fresh", key=key))
    client = object()

    def prev(note, k=key):
        return {"catalog": unknown(note, key=k).to_dict()}

    plain = [r for r in reqs if r.source != "ladder"]
    assert pl.catalog_finding(cfg, facts, rule, plain, prev("x"), client) is None            # no ladder, no finding
    cfg.catalog = {"enabled": False}
    off = pl.catalog_finding(cfg, facts, rule, reqs, None, client)
    assert off["note"] == "catalogue disabled" and off["key"] == key and looked == []
    assert pl.catalog_finding(cfg, facts, rule, reqs, {"catalog": off}, client) == off       # still disabled: kept
    cfg.catalog = {"enabled": True}
    real = prev("1 page(s), 2 grade(s), 0 grade pages failed")
    assert pl.catalog_finding(cfg, facts, rule, reqs, real, client) == real["catalog"] and looked == []
    for again in (prev("1 page(s)", k="other"), prev("catalogue sitemap unavailable: 503"),
                  prev("catalogue lookup failed: boom"), {"catalog": off}, {}):
        assert pl.catalog_finding(cfg, facts, rule, reqs, again, client)["note"] == "fresh"
    assert looked == [client] * 5


class ShortBrowser(FakeBrowser):
    """Serves only the first three of the lot's ten photos; the others time out."""

    def __init__(self):
        super().__init__()
        self.missing = set(parse_detail_page(self.detail_html, "https://x/lot").photo_urls[3:])

    def get_bytes(self, url, headers=None):
        if url in self.missing:
            raise RuntimeError("timeout")
        return super().get_bytes(url, headers)


def _pending_row(store):
    row = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]
    return row, json.loads(row["soft_json"])


def _short_scan(cfg, monkeypatch):
    """A cruise-ladder lot judged on 3 of its 10 photos, none of them showing the steering wheel."""
    cfg.sheet_check = "judge"
    cfg.catalog = {"enabled": False}
    _ladder_fixture(cfg)
    calls = []
    _stub(monkeypatch, calls, reply=_ladder_reply(photo_state="not_visible", photo_no=None))
    store = Store(cfg.db_path)
    scan(cfg, ShortBrowser(), store, ["GDH206"], limit_per_model=1)
    row, soft = _pending_row(store)
    assert len(calls) == 1 and len(json.loads(row["evidence_json"])["photos"]) == 3
    assert soft["photos_complete"] is False and soft["recommendation"] == "review" and soft["ladders"][0]["verdict"] == "unclear"
    return store, calls


def _fill_bundle(cfg, store, wanted=40):
    """The bundle on disk gets every photo the page offers (a later download)."""
    row, _ = _pending_row(store)
    facts = VehicleFacts.from_dict(json.loads(row["facts_json"]))
    return download_evidence(FakeBrowser(), cfg, facts, wanted, PageCache(cache_dir(cfg), cfg.cache_hours))


def test_replay_keeps_the_completeness_the_model_saw(cfg, monkeypatch):
    store, calls = _short_scan(cfg, monkeypatch)
    assert reevaluate(cfg, store, ["GDH206"], apply=True).changed == 0 and len(calls) == 1
    _, soft = _pending_row(store)
    assert soft["recommendation"] == "review" and soft["ladders"][0]["verdict"] == "unclear"
    store.close()


def test_photos_that_arrive_after_the_judgement_send_the_lot_back_to_the_judge(cfg, monkeypatch):
    store, calls = _short_scan(cfg, monkeypatch)
    assert len(_fill_bundle(cfg, store)["photos"]) == 10
    st = reevaluate(cfg, store, ["GDH206"], apply=True)
    assert st.changed == 1 and st.applied == 1 and len(calls) == 1
    assert st.changes[0].endswith("(photos arrived after the judgement)")
    _, soft = _pending_row(store)
    assert soft["judge_pending"] and soft["judgement"] is None and soft["recommendation"] == "review"
    assert "photos arrived" in soft["judge_notes"][0] and soft["ladders"] == []
    st = judge_pending(cfg, store, browser=FakeBrowser())
    assert st.judged == 1 and st.pages == 0 and len(calls) == 2
    _, soft = _pending_row(store)
    assert soft["photos_complete"] is True and soft["recommendation"] == "skip"   # every photo, no steering wheel: no cruise
    store.close()


def test_replay_with_the_bundle_gone_keeps_the_stored_reading(cfg, monkeypatch):
    store, calls = _short_scan(cfg, monkeypatch)
    row, _ = _pending_row(store)
    (Path(json.loads(row["evidence_json"])["dir"]) / "evidence.json").unlink()   # replay falls back to the placeholder bundle
    assert reevaluate(cfg, store, ["GDH206"], apply=True).changed == 0 and len(calls) == 1
    _, soft = _pending_row(store)
    assert soft["photos_complete"] is False and soft["recommendation"] == "review"
    store.close()


def test_photos_beyond_the_cap_never_send_a_judged_lot_back(cfg, monkeypatch):
    cfg.sheet_check = "judge"
    cfg.judge = {"max_photos": 3, "inline": True}
    calls = []
    _stub(monkeypatch, calls)
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)     # plain guide: the model saw the sheet and 3 photos
    assert len(_fill_bundle(cfg, store)["photos"]) == 10               # say an earlier ladder rule filled the bundle
    assert reevaluate(cfg, store, ["GDH206"], apply=True).changed == 0 and len(calls) == 1
    store.close()


def test_a_bundle_waiting_for_images_is_never_complete():
    assert photos_complete({"images_pending": True, "photos": [], "failed": []}, 40) is False
    assert photos_complete({"photos": [], "failed": []}, 40) is True
