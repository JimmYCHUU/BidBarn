"""The rulebook's catalogue and the answers kept as versions."""
from types import SimpleNamespace

import pytest

from auction_bot import rulebook
from auction_bot.store import Store

NINE = ["odo_margin", "interior_years", "selected_pair", "no_selected_pair", "model_code_unmatched",
        "welcab_chips", "welcab_refinements", "guide_213271", "gdh206k"]


def test_the_catalogue_has_every_open_question_once():
    ids = [q.id for q in rulebook.OPEN]
    assert ids == NINE
    assert len(set(ids)) == len(ids)
    assert [q.id for q in rulebook.OPEN if q.kind == "setting"] == ["odo_margin"]
    assert all(q.today for q in rulebook.OPEN if q.kind == "confirm")
    margin = rulebook.question("odo_margin")
    assert margin.setting == "odo_margin_km" and margin.today == 0
    assert [c.value for c in margin.choices] == [0, 1000, 2000, 3000, 5000]
    assert len({d.id for d in rulebook.DECIDED}) == len(rulebook.DECIDED) > 10


def test_values_are_limited_to_the_catalogue():
    margin = rulebook.question("odo_margin")
    assert rulebook.valid_value(margin, 2000)
    assert not rulebook.valid_value(margin, 1500)
    assert not rulebook.valid_value(margin, "2000")
    assert not rulebook.valid_value(margin, True)
    gdh = rulebook.question("gdh206k")
    assert rulebook.valid_value(gdh, "keep")
    assert not rulebook.valid_value(gdh, "change")
    assert not rulebook.valid_value(gdh, 0)


def test_answers_are_versions_that_never_change(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    assert s.rulebook_version() == 0 and s.rulebook_answers() == {} and s.rulebook_history() == []
    assert s.add_rulebook_answer("odo_margin", 2000, note="try it", preview_run=7) == 1
    assert s.rulebook_answers() == {"odo_margin_km": 2000}
    assert s.add_rulebook_answer("gdh206k", "keep") == 2
    assert s.rulebook_version() == 2
    assert s.rulebook_answers() == {"odo_margin_km": 2000, "gdh206k": "keep"}
    hist = s.rulebook_history()
    assert [r["version"] for r in hist] == [2, 1]
    assert hist[1]["question_id"] == "odo_margin" and hist[1]["note"] == "try it" and hist[1]["preview_run"] == 7
    assert '"odo_margin_km"' in hist[1]["answers_json"] and "gdh206k" not in hist[1]["answers_json"]
    s.add_rulebook_answer("odo_margin", 0)
    assert [r["answers_json"] for r in s.rulebook_history()][-1] == hist[1]["answers_json"]
    assert s.rulebook_answers()["odo_margin_km"] == 0
    s.close()


def test_change_requests_are_kept_and_empty_ones_refused(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    with pytest.raises(ValueError):
        s.add_rule_request("gdh206k", "   ")
    first = s.add_rule_request("gdh206k", "Treat it as skip")
    second = s.add_rule_request("odo_margin", "Per model please")
    assert second > first
    rows = s.rule_requests()
    assert [r["text"] for r in rows] == ["Treat it as skip", "Per model please"]
    assert rows[0]["question_id"] == "gdh206k" and rows[0]["created_at"]
    s.close()


def test_current_answers_without_a_database_is_empty(tmp_path):
    db = tmp_path / "none.sqlite"
    assert rulebook.current_answers(SimpleNamespace(db_path=db)) == {}
    assert not db.exists()
    s = Store(db)
    s.add_rulebook_answer("odo_margin", 1000)
    s.close()
    assert rulebook.current_answers(SimpleNamespace(db_path=db)) == {"odo_margin_km": 1000}


def test_nothing_locked_can_be_answered():
    ids = {q.id for q in rulebook.OPEN}
    titles = {q.title for q in rulebook.OPEN}
    for item in rulebook.LOCKED:
        assert item.text not in ids and item.text not in titles and item.source
    assert len(rulebook.LOCKED) >= 7
    with pytest.raises(KeyError):
        rulebook.question("testing_lock")


# -- preview and apply (routes) -------------------------------------------------------------------

from tests.test_dashboard_app import FakeJobs, _client, _run   # noqa: E402
from tests.test_pipeline_offline import cfg   # noqa: E402,F401  (fixture)

JSON = {"accept": "application/json"}


def _preview(cfg, value=2000, version=0, status="ok", command="rulebook-preview", answer=None):
    return _run(cfg, command, status=status, summary={
        "rulebook_version": version, "answer": answer if answer is not None else {"odo_margin_km": value},
        "lots": [], "checked": 0, "changed": 0})


def _versions(cfg):
    s = Store(cfg.db_path)
    try:
        return s.rulebook_version()
    finally:
        s.close()


def test_the_store_refuses_a_value_outside_the_catalogue(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    for q, v in (("odo_margin", 1500), ("odo_margin", True), ("odo_margin", "2000"), ("gdh206k", "change"),
                 ("gdh206k", 1000)):
        with pytest.raises(ValueError):
            s.add_rulebook_answer(q, v)
    with pytest.raises(KeyError):
        s.add_rulebook_answer("testing_lock", "keep")
    assert s.rulebook_version() == 0
    s.close()


def test_apply_needs_a_matching_fresh_preview(cfg):
    client, jobs = _client(cfg)
    url = "/rulebook/odo_margin/apply"
    cases = [
        ("none", {"value": "2000", "preview_run": "99"}),
        ("other value", {"value": "2000", "preview_run": str(_preview(cfg, 1000))}),
        ("failed run", {"value": "2000", "preview_run": str(_preview(cfg, 2000, status="failed"))}),
        ("other command", {"value": "2000", "preview_run": str(_preview(cfg, 2000, command="replay"))}),
        ("other key", {"value": "2000", "preview_run": str(_preview(cfg, answer={"gdh206k": 2000}))}),
        ("older version", {"value": "2000", "preview_run": str(_preview(cfg, 2000, version=3))}),
    ]
    for name, form in cases:
        r = client.post(url, data=form, headers=JSON)
        assert r.status_code == 409 and r.json()["detail"].endswith("."), name
    assert _versions(cfg) == 0 and jobs.live is None
    assert client.post(url, data={"value": "1500", "preview_run": str(_preview(cfg, 1500))}, headers=JSON).status_code == 400
    assert client.post(url, data={"value": "2000"}, headers=JSON).status_code == 400
    assert client.post("/rulebook/gdh206k/apply", data={"value": "1", "preview_run": "1"}, headers=JSON).status_code == 409
    assert client.post("/rulebook/nonsense/apply", data={"value": "1", "preview_run": "1"}, headers=JSON).status_code == 404
    assert _versions(cfg) == 0 and jobs.live is None


def test_apply_writes_a_version_and_starts_the_replay(cfg):
    client, jobs = _client(cfg)
    run = _preview(cfg, 2000, version=0)
    r = client.post("/rulebook/odo_margin/apply", data={"value": "2000", "preview_run": str(run)}, headers=JSON)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["version"] == 1
    assert jobs.get(r.json()["run_id"])["args"] == ["replay", "--apply"]
    s = Store(cfg.db_path)
    row = s.rulebook_history()[0]
    assert row["question_id"] == "odo_margin" and row["preview_run"] == run and s.rulebook_answers() == {"odo_margin_km": 2000}
    s.close()
    again = client.post("/rulebook/odo_margin/apply", data={"value": "2000", "preview_run": str(run)}, headers=JSON)
    assert again.status_code == 409 and _versions(cfg) == 1   # the same preview cannot be used twice


def test_apply_keeps_the_version_when_a_job_is_running(cfg):
    client, jobs = _client(cfg)
    jobs.start("replay", {})
    r = client.post("/rulebook/odo_margin/apply", data={"value": "3000", "preview_run": str(_preview(cfg, 3000))},
                    headers=JSON)
    assert r.status_code == 200 and r.json()["version"] == 1 and "running" in r.json()["busy"] and "run_id" not in r.json()
    assert _versions(cfg) == 1


def test_a_browser_post_goes_back_to_the_rulebook(cfg):
    client, _ = _client(cfg)
    r = client.post("/rulebook/gdh206k/keep", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/rulebook"


def test_keep_and_request(cfg):
    client, _ = _client(cfg)
    r = client.post("/rulebook/gdh206k/keep", headers=JSON)
    assert r.status_code == 200 and r.json()["version"] == 1
    assert client.post("/rulebook/odo_margin/keep", headers=JSON).status_code == 409
    assert client.post("/rulebook/nonsense/keep", headers=JSON).status_code == 404
    assert _versions(cfg) == 1
    r = client.post("/rulebook/gdh206k/request", data={"text": "Treat it as skip"}, headers=JSON)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.post("/rulebook/gdh206k/request", data={"text": "  "}, headers=JSON).status_code == 400
    assert client.post("/rulebook/nonsense/request", data={"text": "x"}, headers=JSON).status_code == 404
    s = Store(cfg.db_path)
    assert [r["text"] for r in s.rule_requests()] == ["Treat it as skip"] and s.rulebook_version() == 1
    s.close()
