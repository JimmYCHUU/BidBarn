"""The triage view: sorted like the manual pass, sheet inline, photos as a Nichibo link."""
import json
import re

from fastapi.testclient import TestClient

from auction_bot.pipeline import scan
from auction_bot.review.app import create_app, triage_sort_key
from auction_bot.store import Store
from tests.test_pipeline_offline import FakeBrowser, cfg  # noqa: F401  (fixture)


def _seed(store: Store, ref: str, code: str, grade: str, km: int, bid: int | None = 1000, status="NEEDS_REVIEW", guide=1):
    facts = {"ref_no": ref, "url": f"https://autosearch.nichibojapan.com/auction/{ref}", "year": 2020, "make": "Toyota",
             "model": "Hiace", "variant_text": "Van", "km": km, "grade_raw": grade, "grade": float(grade),
             "start_price_jpy": 500_000, "sheet_url": f"https://img.example/{ref}.jpg", "source": "card+sheet"}
    return store.add_proposal(ref_no=ref, guide_id=guide, model_code=code, snapshot="s", rule_updated_at="u", status=status,
                              stage="manual", bid_jpy_k=bid, summary=f"2020 Toyota Hiace {ref}", facts_json=facts,
                              hard_json={"checks": []}, soft_json={"mode": "manual", "checklist": ["No sticker paint"], "hints": [],
                                                                    "reasons": ["judge the sheet"]}, evidence_json={"dir": "", "sheet": None, "photos": []})


def test_triage_order_and_content(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "A", "GDH206", "4", 90_000)
    b = _seed(store, "B", "GDH206", "4.5", 120_000)
    c = _seed(store, "C", "GDH206", "4", 60_000)
    d = _seed(store, "D", "ANH20W", "3.5", 10_000, guide=2)
    _seed(store, "E", "GDH206", "4", 10_000, status="SKIP")  # not for triage
    store.close()
    client = TestClient(create_app(cfg))
    r = client.get("/review")
    assert r.status_code == 200
    order = [int(x) for x in re.findall(r'data-pid="(\d+)"', r.text)]
    assert order == [d, b, c, a]  # models alphabetically; within GDH206: grade 4.5 first, then fewest km
    assert "2020 Toyota Hiace" in r.text and "https://img.example/A.jpg" in r.text  # sheet straight from Nichibo when not downloaded
    assert 'data-ev="nichibo_click"' in r.text and 'data-ev="photos_click"' not in r.text  # one link to Nichibo, not two
    assert "/evidence/" not in r.text.split("data-pid")[1]
    assert 'data-bid="1000"' in r.text and "review.js" in r.text
    assert client.get("/static/review.js").status_code == 200 and "triageKeys" in client.get("/static/review.js").text
    assert client.get("/list").status_code == 200  # the old table still exists
    # Filter by model.
    r = client.get("/review?model=ANH20W")
    assert [int(x) for x in re.findall(r'data-pid="(\d+)"', r.text)] == [d]


def test_triage_sort_key_handles_missing_facts():
    assert triage_sort_key({"model_code": "X", "id": 3, "facts": {}}) == ("X", 1, 0.0, 10**9, -3)  # no recommendation: middle
    assert triage_sort_key({"model_code": "X", "id": 3, "facts": {"grade": 4.5, "km": 0}, "recommendation": "bid"}) == ("X", 0, -4.5, 0, -3)
    assert triage_sort_key({"model_code": "X", "id": 3, "facts": {}, "recommendation": "skip"})[1] == 2


def test_json_decide_from_triage_logs_open_and_via(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    pids = [r["id"] for r in store.list_proposals(decision="pending")]
    store.close()
    client = TestClient(create_app(cfg))
    assert client.post(f"/p/{pids[0]}/event", json={"event": "open", "value": "triage"}).status_code == 204
    r = client.post(f"/p/{pids[0]}/decide", data={"action": "approve", "via": "triage"}, headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["amount"] == 1240 and r.json()["next_id"] == pids[1]
    # Errors come back as JSON too, and nothing is decided twice.
    r = client.post(f"/p/{pids[0]}/decide", data={"action": "approve", "via": "triage"}, headers={"Accept": "application/json"})
    assert r.status_code == 409 and "already decided" in r.json()["detail"]
    r = client.post(f"/p/{pids[1]}/decide", data={"action": "reject", "reason": "", "via": "triage"}, headers={"Accept": "application/json"})
    assert r.status_code == 400
    store = Store(cfg.db_path)
    d = store.decision_for(pids[0])
    assert d["via"] == "triage" and d["decide_seconds"] is not None and d["amount_edited"] == 0
    assert store.proposal(pids[1])["decision"] == "pending"
    store.close()
    # Without the JSON accept header the form flow redirects to the triage list.
    r = client.post(f"/p/{pids[1]}/decide", data={"action": "reject", "reason": "no"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/review"


def test_triage_shows_suggested_skip_rules(cfg):
    store = Store(cfg.db_path)
    for i in range(3):
        pid = _seed(store, f"R{i}", "GDH206", "4", 50_000 + i)
        store.conn.execute("UPDATE proposals SET facts_json=json_set(facts_json, '$.variant_text', 'Van DUMP') WHERE id=?", (pid,))
        store.conn.commit()
        store.decide(pid, "reject", "dump body", None, False)
    keep = _seed(store, "K", "GDH206", "4", 70_000)
    store.close()
    client = TestClient(create_app(cfg))
    r = client.get("/review")
    assert "1 skip rule suggested" in r.text and 'href="/guides/GDH206#suggested"' in r.text  # the review page only points at it
    r = client.get("/guides/GDH206")
    assert r.status_code == 200 and "skip: variant contains DUMP" in r.text and "3 rejections" in r.text  # the guide page holds it
    r = client.post("/rules/GDH206/skip", data={"line": "skip: variant contains DUMP", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    assert "skip: variant contains DUMP" in (cfg.override_dir / "GDH206.md").read_text(encoding="utf-8")
