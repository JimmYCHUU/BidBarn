"""The Rulebook page: what is decided, the open questions with their lots, and what is locked."""
import html as h
import json
import re
from pathlib import Path

import pytest

from auction_bot import rulebook
from auction_bot.store import Store
from tests.test_dashboard_app import _client, _run   # noqa: F401
from tests.test_pipeline_offline import FakeBrowser, cfg, scan   # noqa: F401  (fixture)

JSON = {"accept": "application/json"}


def _preview(cfg, value=2000, version=0):
    return _run(cfg, "rulebook-preview", summary={"rulebook_version": version, "answer": {"odo_margin_km": value},
                                                 "lots": [], "checked": 0, "changed": 0})


# -- the three additions carried from Task 3 -----------------------------------------------------------

def test_apply_refuses_a_preview_with_no_rulebook_version(cfg):
    client, jobs = _client(cfg)
    for version in (None, "0", 1.5, True):
        run = _run(cfg, "rulebook-preview", summary={"rulebook_version": version, "answer": {"odo_margin_km": 2000},
                                                    "lots": []})
        r = client.post("/rulebook/odo_margin/apply", data={"value": "2000", "preview_run": str(run)}, headers=JSON)
        assert r.status_code == 409 and r.json()["detail"] == (
            "This preview cannot be checked against the Rulebook; run the preview again."), version
    s = Store(cfg.db_path)
    assert s.rulebook_version() == 0
    s.close()
    assert jobs.live is None


def test_a_preview_with_no_summary_shows_no_lots_pair():
    from auction_bot.dashboard.routes import summary_pairs
    label = "Lots that would change"
    assert not any(k == label for k, _v in summary_pairs({"command": "rulebook-preview", "summary": None}))
    assert not any(k == label for k, _v in summary_pairs({"command": "rulebook-preview", "summary": {"checked": 3}}))
    assert (label, "0") in summary_pairs({"command": "rulebook-preview", "summary": {"lots": []}})
    assert (label, "4") in summary_pairs({"command": "rulebook-preview", "summary": {"lots_total": 4}})


def test_a_rulebook_preview_never_calls_a_model(cfg, monkeypatch):
    import auction_bot.judge.client as client_mod
    import auction_bot.pipeline as pl
    import auction_bot.rules.soft as soft_mod

    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    cfg.sheet_check = "openai"          # opt-in and paid: a preview must still not use it

    def boom(*a, **k):
        raise AssertionError("a Rulebook preview asked a model")
    monkeypatch.setattr(soft_mod, "evaluate_soft", boom)
    monkeypatch.setattr(client_mod, "make_client", boom)
    monkeypatch.setattr(client_mod, "openai_transport", boom)
    st = pl.reevaluate(cfg, store, ["GDH206"], answers={"odo_margin_km": 1000})
    assert st.errors == [] and st.checked > 0
    store.close()


# -- affected ----------------------------------------------------------------------------------------

def _lot(**kw):
    base = {"id": 1, "status": "NEEDS_REVIEW", "decision": "pending", "guide_id": 223, "model_code": "GDH206",
            "facts": {"km": 20_000, "chassis": "GDH206V"}, "soft": {}, "hard": {}}
    return {**base, **kw}


def test_affected_picks_the_lots_each_question_touches(cfg):
    from auction_bot.pipeline import load_rules
    rules, _ = load_rules(cfg)
    f = lambda km, ch="GDH206V": {"km": km, "chassis": ch}   # noqa: E731
    rows = [_lot(id=1, facts=f(49_000)), _lot(id=2, facts=f(122_000)), _lot(id=3, facts=f(50_000)),
            _lot(id=4, facts=f(45_000)),                                  # 5,000 below: a 5,000 margin only reaches the bound
            _lot(id=5, decision="approved", facts=f(49_000)), _lot(id=6, status="SKIP", facts=f(49_000)),
            _lot(id=7, facts={"km": None})]
    assert rulebook.affected(rulebook.question("odo_margin"), rows, rules) == [1, 3]
    g = [_lot(id=1, guide_id=213271), _lot(id=2), _lot(id=3, model_code="GDH206K"), _lot(id=4, facts=f(1, "GDH206K"))]
    assert rulebook.affected(rulebook.question("guide_213271"), g, rules) == [1]
    assert rulebook.affected(rulebook.question("gdh206k"), g, rules) == [3, 4]
    welcab = _lot(id=1, soft={"requirements": [{"id": "r0", "text": "Welcab front seat", "source": "ladder",
                                                "topic": "welcab_seat"}]})
    chip = _lot(id=2, soft={"requirements": [{"id": "c", "text": "Slope type -180K", "source": "text", "kind": "chip"}]})
    plain = _lot(id=3, soft={"requirements": [{"id": "c", "text": "No Pro Pilot", "source": "text", "kind": "chip"}]})
    for key in ("welcab_chips", "welcab_refinements"):
        assert rulebook.affected(rulebook.question(key), [welcab, chip, plain], rules) == [1, 2]
    elig = [_lot(id=1, soft={"eligibility": {"verdict": "confirm"}}), _lot(id=2, soft={"eligibility": {"verdict": "eligible"}}),
            _lot(id=3), _lot(id=4, soft={"eligibility": {"verdict": "cannot_import"}})]
    for key in ("interior_years", "selected_pair", "no_selected_pair", "model_code_unmatched"):
        assert rulebook.affected(rulebook.question(key), elig, rules) == [1, 4]


# -- the page ----------------------------------------------------------------------------------------

def _section(html, key):
    start = html.index(f'id="{key}"')
    ends = [html.find(f'id="{o}"', start + 1) for o in ("decided", "open", "locked") if o != key]
    ends = [e for e in ends if e > start]
    return html[start:min(ends)] if ends else html[start:]


def _question(html, qid):
    start = html.index(f'id="q-{qid}"')
    nxt = html.find('id="q-', start + 1)
    return html[start:nxt] if nxt > 0 else html[start:]


def test_the_rulebook_shows_decided_open_and_locked(cfg):
    client, _ = _client(cfg)
    r = client.get("/rulebook")
    assert r.status_code == 200
    page = r.text
    for heading in ("Decided", "Open questions", "Locked"):
        assert f">{heading}<" in page
    text = h.unescape(page)
    for q in rulebook.OPEN:
        assert q.title in text
    for d in rulebook.DECIDED:
        assert d.text in text
    for item in rulebook.LOCKED:
        assert item.text in text
    locked = _section(page, "locked")
    for tag in ("<form", "<button", "<input", "<textarea", "<select"):
        assert tag not in locked
    assert "<svg" in locked
    assert "/static/rulebook.js" in page


def test_an_open_question_says_how_many_lots_it_affects(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    first = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]
    facts = json.loads(first["facts_json"])
    facts["km"] = 49_000
    store.conn.execute("UPDATE proposals SET facts_json=? WHERE id=?", (json.dumps(facts), first["id"]))
    store.conn.commit()
    store.close()
    client, _ = _client(cfg)
    page = client.get("/rulebook").text
    odo = _question(page, "odo_margin")
    assert "Affects 1 waiting lot" in odo and 'href="/desk"' in odo and "Open the desk" in odo
    assert "Affects 0 waiting lots" in _question(page, "interior_years")
    assert odo.count('type="radio"') == 5 and "checked" in odo
    assert 'data-act="preview"' in odo and 'data-act="apply"' in odo
    assert "disabled" in odo.split('data-act="apply"')[1][:40]
    assert 'data-act="preview"' not in _question(page, "gdh206k")


def test_a_kept_question_moves_to_decided(cfg):
    client, _ = _client(cfg)
    before = client.get("/rulebook").text
    assert 'id="q-gdh206k"' in _section(before, "open")
    assert client.post("/rulebook/gdh206k/keep", headers=JSON).status_code == 200
    after = client.get("/rulebook").text
    assert 'id="q-gdh206k"' not in after
    decided = _section(after, "decided")
    assert "The GDH206K variant" in decided and "kept as it is" in decided.lower() and "Version 1" in decided
    # a setting question stays open, showing its value, and is also listed as decided
    assert client.post("/rulebook/odo_margin/apply", data={"value": "2000", "preview_run": str(_preview(cfg, 2000, version=1))},
                       headers=JSON).status_code == 200
    page = client.get("/rulebook").text
    odo = " ".join(_question(page, "odo_margin").split())
    assert re.search(r'value="2000"[^>]*checked', odo) and "Today: 2,000 km" in odo
    assert "2,000 km" in _section(page, "decided")


def test_a_request_is_kept_and_the_question_stays_open(cfg):
    client, _ = _client(cfg)
    assert client.post("/rulebook/gdh206k/request", data={"text": "Treat it as skip"}, headers=JSON).status_code == 200
    page = client.get("/rulebook").text
    assert 'id="q-gdh206k"' in page and "Treat it as skip" in page


def test_the_page_offers_nothing_outside_the_catalogue(cfg):
    page = _client(cfg)[0].get("/rulebook").text
    actions = set(re.findall(r'<form[^>]*action="([^"]+)"', page))
    assert actions and all(re.fullmatch(r"/rulebook/[a-z0-9_]+/(keep|request)", a) for a in actions)
    controls = page.replace(_section(page, "locked"), "")   # the locked rules only describe the live flag in words
    assert "--live" not in controls and not re.search(r'type="(number|text)"', page)
    values = set(re.findall(r'type="radio"[^>]*value="([^"]*)"', page))
    assert values == {"0", "1000", "2000", "3000", "5000"}


def test_rulebook_js_talks_only_to_its_routes():
    src = (Path(rulebook.__file__).parent / "review" / "static" / "rulebook.js").read_text(encoding="utf-8")
    fetched = [f.strip() for f in re.findall(r"fetch\(\s*([^,)]+)", src)]
    assert fetched
    assert all(f.startswith(('"/api/runs"', '"/api/runs/" +', '"/rulebook/" +')) for f in fetched), fetched
    assert src.count('"rulebook-preview"') == 1
    for word in ("--live", "place", "scan", "decide", "approve", "/auc/", "/p/"):
        assert word not in src, word
    assert set(re.findall(r'"/rulebook/" \+ [^;]*?\+ "/(\w+)"', src)) <= {"apply", "keep", "request"}


def test_the_rulebook_is_in_the_nav(cfg):
    client, _ = _client(cfg)
    page = client.get("/").text
    assert 'href="/rulebook"' in page
    assert page.index('href="/bids"') < page.index('href="/rulebook"') < page.index('href="/guides"')
    assert 'href="/rulebook" aria-current="page"' in client.get("/rulebook").text


# -- a preview shows only what the answer changes ------------------------------------------------------

def test_a_preview_under_the_judge_setting_changes_nothing_for_todays_value(cfg, monkeypatch):
    from auction_bot.pipeline import reevaluate
    from tests.test_judge_pipeline import _stub
    cfg.sheet_check = "judge"
    cfg.judge = {"max_photos": 3, "inline": True}
    calls = []
    _stub(monkeypatch, calls)
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    asked = len(calls)
    assert asked == 2
    assert reevaluate(cfg, store, ["GDH206"]).changed == 0
    st = reevaluate(cfg, store, ["GDH206"], answers={"odo_margin_km": 0})
    assert st.checked >= 2 and st.changed == 0 and st.lots == [] and st.drift == 0 and st.errors == []
    assert len(calls) == asked
    store.close()


def test_a_preview_lists_only_the_answers_own_effect_and_counts_drift(cfg):
    from auction_bot.pipeline import reevaluate
    from tests.test_pipeline_offline import _settled_at_50k
    store = _settled_at_50k(cfg)
    first, boundary = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[:2]
    facts = json.loads(first["facts_json"])
    facts["km"] = 15_000
    # a lot already out of date with today's rules (an old amount), far from any band bound
    store.conn.execute("UPDATE proposals SET facts_json=?, bid_jpy_k=1 WHERE id=?", (json.dumps(facts), first["id"]))
    store.conn.commit()
    assert reevaluate(cfg, store, ["GDH206"]).changed == 1
    st = reevaluate(cfg, store, ["GDH206"], answers={"odo_margin_km": 1000})
    assert [lot["pid"] for lot in st.lots] == [boundary["id"]] and st.drift == 1 and st.lots_total == 1
    assert st.lots[0]["before"]["bid_jpy_k"] == 1710 and st.lots[0]["after"]["bid_jpy_k"] == 1640
    today = reevaluate(cfg, store, ["GDH206"], answers={"odo_margin_km": 0})
    assert today.lots == [] and today.changed == 0 and today.drift == 1
    store.close()


def test_the_unknown_count_without_a_snapshot(cfg):
    for f in cfg.snapshot_dir.glob("*.json"):
        f.unlink()
    page = _client(cfg)[0].get("/rulebook").text
    assert "Affects an unknown number of waiting lots (no guide snapshot)." in _question(page, "odo_margin")
    assert "Affects 0 waiting lots" in _question(page, "interior_years")


def test_decided_lists_the_rules_before_the_answers(cfg):
    client, _ = _client(cfg)
    client.post("/rulebook/gdh206k/keep", headers=JSON)
    decided = _section(client.get("/rulebook").text, "decided")
    assert decided.index("rb-decided") < decided.index("Your answers")
    src = (Path(rulebook.__file__).parent / "review" / "static" / "rulebook.js").read_text(encoding="utf-8")
    assert "odo_margin_km" not in src
