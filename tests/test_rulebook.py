"""The rulebook's catalogue and the answers kept as versions."""
import json
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


def seen_now(cfg) -> dict:
    """What a preview made now records of the guides and the waiting lots (the freshness key Apply checks)."""
    from auction_bot.carbarn.client import latest_snapshot
    s = Store(cfg.db_path)
    try:
        return {"snapshot": latest_snapshot(cfg).name, "waiting": s.waiting_fingerprint()}
    finally:
        s.close()


def _preview(cfg, value=2000, version=0, status="ok", command="rulebook-preview", answer=None):
    return _run(cfg, command, status=status, summary={
        "rulebook_version": version, "answer": answer if answer is not None else {"odo_margin_km": value},
        "lots": [], "checked": 0, "changed": 0, **seen_now(cfg)})


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


def test_apply_refuses_a_huge_or_bad_preview_run(cfg):
    client, _ = _client(cfg)
    for bad in ("9" * 25, "0", "-3", "abc", ""):
        r = client.post("/rulebook/odo_margin/apply", data={"value": "2000", "preview_run": bad}, headers=JSON)
        assert r.status_code == 400 and r.json()["detail"] == "preview_run must be a run number", bad
    assert _versions(cfg) == 0


def test_apply_refuses_a_preview_that_could_not_check_every_lot(cfg):
    client, jobs = _client(cfg)
    run = _run(cfg, "rulebook-preview", summary={"rulebook_version": 0, "answer": {"odo_margin_km": 2000}, "lots": [],
                                                "errors": ["#4: boom"], "errors_total": 3})
    r = client.post("/rulebook/odo_margin/apply", data={"value": "2000", "preview_run": str(run)}, headers=JSON)
    assert r.status_code == 409 and r.json()["detail"] == (
        "The preview could not check 3 lots; look at its run page before applying.")
    assert _versions(cfg) == 0 and jobs.live is None


CHANGED = "The lots or the guides changed since this preview; run the preview again."


def test_apply_refuses_a_preview_of_other_guides_or_other_lots(cfg):
    client, jobs = _client(cfg)
    url = "/rulebook/odo_margin/apply"

    def refused(run, name):
        r = client.post(url, data={"value": "2000", "preview_run": str(run)}, headers=JSON)
        assert r.status_code == 409 and r.json()["detail"] == CHANGED, name

    # a newer guide snapshot since the preview
    run = _preview(cfg, 2000)
    newer = cfg.snapshot_dir / "rules_zz_newer.json"
    newer.write_text((cfg.snapshot_dir / "rules_test.json").read_text(encoding="utf-8"), encoding="utf-8")
    refused(run, "newer snapshot")
    newer.unlink()
    # a lot stored since the preview
    run = _preview(cfg, 2000)
    s = Store(cfg.db_path)
    pid = s.add_proposal(ref_no="A1", guide_id=223, model_code="GDH206", snapshot="rules_test.json",
                         status="NEEDS_REVIEW", stage="manual")
    s.close()
    refused(run, "new lot")
    # a judgement stored in place since the preview
    run = _preview(cfg, 2000)
    s = Store(cfg.db_path)
    s.update_soft(pid, {"mode": "judge"})
    s.close()
    refused(run, "new judgement")
    # a lot decided since the preview
    run = _preview(cfg, 2000)
    s = Store(cfg.db_path)
    s.conn.execute("UPDATE proposals SET decision='rejected' WHERE id=?", (pid,))
    s.conn.commit()
    s.close()
    refused(run, "decided")
    # a preview that records neither
    for gone in ("snapshot", "waiting"):
        summary = {"rulebook_version": 0, "answer": {"odo_margin_km": 2000}, "lots": [], **seen_now(cfg)}
        del summary[gone]
        refused(_run(cfg, "rulebook-preview", summary=summary), f"no {gone}")
    assert _versions(cfg) == 0 and jobs.live is None
    # unchanged since the preview: applied
    r = client.post(url, data={"value": "2000", "preview_run": str(_preview(cfg, 2000))}, headers=JSON)
    assert r.status_code == 200 and r.json()["version"] == 1


def test_apply_refuses_a_preview_that_is_still_running(cfg):
    client, jobs = _client(cfg)
    s = Store(cfg.db_path)
    run = s.start_run("rulebook-preview", ["replay", "--answer", "odo_margin_km=2000"])
    s.close()
    assert jobs.get(run)["status"] == "running"
    r = client.post("/rulebook/odo_margin/apply", data={"value": "2000", "preview_run": str(run)}, headers=JSON)
    assert r.status_code == 409 and r.json()["detail"] == "That preview did not finish. Preview this answer again."
    assert _versions(cfg) == 0 and jobs.live is None


def test_two_writers_never_lose_an_answer(tmp_path):
    import threading
    path = tmp_path / "t.sqlite"
    Store(path).close()
    gate = threading.Barrier(2)

    def writer(qid, value):
        s = Store(path)
        gate.wait()
        for _ in range(25):
            s.add_rulebook_answer(qid, value)
        s.close()

    threads = [threading.Thread(target=writer, args=("odo_margin", 1000)), threading.Thread(target=writer, args=("gdh206k", "keep"))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    s = Store(path)
    assert s.rulebook_version() == 50 and s.rulebook_answers() == {"odo_margin_km": 1000, "gdh206k": "keep"}
    # every version holds everything answered before it: the history never skips an earlier answer
    seen = set()
    for row in reversed(s.rulebook_history()):
        seen |= set(json.loads(row["answers_json"]))
        assert set(json.loads(row["answers_json"])) >= seen
    with pytest.raises(ValueError):
        s.add_rulebook_answer("odo_margin", 2000, expect_version=3)
    assert s.rulebook_version() == 50
    s.close()


def test_a_preview_run_is_labelled_and_its_lots_stay_off_the_polled_lists(cfg):
    from auction_bot.dashboard import status as st
    from auction_bot.dashboard.routes import summary_pairs
    assert st.DOING["rulebook-preview"] == "Previewing a Rulebook answer"
    assert st.COMMAND_SHORT["rulebook-preview"] == "Rulebook preview" and st.COMMAND_ICONS["rulebook-preview"]
    lots = [{"pid": i} for i in range(7)]
    run = _run(cfg, "rulebook-preview", summary={"rulebook_version": 0, "answer": {"odo_margin_km": 2000}, "lots": lots,
                                                "checked": 9, "changed": 7})
    client, _ = _client(cfg)
    assert ("Lots that would change", "7") in summary_pairs({"command": "rulebook-preview", "summary": {"lots": lots}})
    assert not any(label == "Sheets" for label, _v in summary_pairs({"command": "rulebook-preview", "summary": {"lots": lots}}))
    listed = next(r for r in client.get("/api/runs").json() if r["id"] == run)
    assert "lots" not in listed["summary"] and listed["summary"]["lots_total"] == 7
    assert ("Lots that would change", "7") in summary_pairs(listed)
    assert client.get("/api/status").json()["last_runs"]["rulebook-preview"]["summary"].get("lots") is None
    assert client.get(f"/api/runs/{run}").json()["summary"]["lots"] == lots     # the run's own page keeps them
