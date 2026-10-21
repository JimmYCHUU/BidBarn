"""The judgement on the review page: triage badges and proposed amount, the detail card, corrections into the playbook."""
import json
import re

from fastapi.testclient import TestClient

from auction_bot.insights import model_accuracy, render
from auction_bot.judge.playbook import code_lines
from auction_bot.pipeline import scan
from auction_bot.review.app import create_app, feedback_lines
from auction_bot.store import Store
from tests.test_judge_pipeline import _stub
from tests.test_pipeline_offline import FakeBrowser, cfg  # noqa: F401  (fixture)


def _judged(cfg, monkeypatch, reply=None):
    cfg.sheet_check = "judge"
    cfg.judge = {"max_photos": 2}
    calls = []
    _stub(monkeypatch, calls, reply=reply)
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    pid = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]["id"]
    store.close()
    return pid


def test_triage_and_detail_show_the_judgement(cfg, monkeypatch):
    pid = _judged(cfg, monkeypatch)
    client = TestClient(create_app(cfg))
    r = client.get("/review")
    assert r.status_code == 200
    assert "model: bid" in r.text and "dark cloth seats" in r.text and "photo 2: painted body (high)" in r.text  # in the verdicts' tooltip
    assert 'class="badge ok"' in r.text and "No sticker paint" in r.text and 'data-bid="1240"' in r.text
    assert 'class="verdicts"' in r.text and "pass," in r.text  # the row carries counts, the lot page the list
    r = client.get(f"/p/{pid}")
    assert "The model's judgement" in r.text and "recommends: bid" in r.text and "proposes <b>1,240K</b>" in r.text
    assert "photo 2: painted body" in r.text and "The model was wrong about" in r.text and 'value="1240"' in r.text
    assert "Your checklist" in r.text  # the reviewer's own checklist stays
    card = r.text.split("Your checklist")[1].split("</section>")[0]
    assert 'type="checkbox"' not in card  # as a list, not as boxes that were never saved


def test_corrections_go_to_feedback_and_the_playbook(cfg, monkeypatch):
    pid = _judged(cfg, monkeypatch)
    client = TestClient(create_app(cfg))
    r = client.post(f"/p/{pid}/decide", data={"action": "reject", "reason": "sticker wrap on the roof, photo 5",
                                              "feedback_req": "r1", "feedback_note": "the roof is wrapped, look at photo 5"},
                    follow_redirects=False)
    assert r.status_code == 303
    store = Store(cfg.db_path)
    rows = [dict(x) for x in store.judge_feedback("GDH206")]
    assert [x["requirement_id"] for x in rows] == ["overall", "r1"]
    assert rows[1]["model_said"].startswith("pass: photo 2: painted body") and rows[1]["jim_said"] == "the roof is wrapped, look at photo 5"
    assert rows[0]["model_said"] == "recommended bid 1240K"
    lines = code_lines(cfg, "GDH206")
    assert any("Model was wrong about 'No sticker paint'" in ln and "the roof is wrapped" in ln for ln in lines)
    assert any("Model recommended a bid but Jim rejected: sticker wrap" in ln for ln in lines)
    acc = model_accuracy(rows)
    assert acc["GDH206"]["r1"] == 1 and acc["GDH206"]["overall"] == 1
    text = render({}, [], accuracy=acc)
    assert "GDH206: the model was corrected 2 time(s)" in text
    store.close()


def test_approving_at_another_amount_is_recorded(cfg, monkeypatch):
    pid = _judged(cfg, monkeypatch)
    client = TestClient(create_app(cfg))
    # No amount typed: the proposed amount is used.
    r = client.post(f"/p/{pid}/decide", data={"action": "approve"}, headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json()["amount"] == 1240
    store = Store(cfg.db_path)
    assert store.judge_feedback("GDH206") == []
    store.close()


def test_feedback_lines_cover_the_three_disagreements():
    judge = {"judgement": {"requirements": [{"id": "r1", "verdict": "pass", "evidence": "photo 1"}], "chips": []},
             "requirements": [{"id": "r1", "text": "No sticker paint", "kind": "must", "source": "photos"}],
             "recommendation": "skip", "proposed_bid_jpy_k": 1000}
    loaded = {"id": 1, "summary": "2020 Toyota Hiace", "model_code": "GDH206", "judge": judge}
    lines = feedback_lines(loaded, "approve", 1100, "", "", "")
    assert [ln[0] for ln in lines] == ["overall"] and "recommended skip but Jim bid 1100K" in lines[0][3]
    judge["recommendation"] = "bid"
    lines = feedback_lines(loaded, "approve", 1100, "chip applies", "", "")
    assert [ln[0] for ln in lines] == ["amount"] and "proposed 1000K, Jim bid 1100K: chip applies" in lines[0][3]
    assert feedback_lines({"id": 1, "judge": None}, "reject", None, "x", "", "") == []


def test_model_view_for_the_learn_report():
    from auction_bot.run import model_view
    soft = {"mode": "judge", "recommendation": "skip", "proposed_bid_jpy_k": 1000,
            "requirements": [{"id": "r1", "text": "No sticker paint", "kind": "must"}, {"id": "chip:x", "text": "Autech", "kind": "chip"}],
            "judgement": {"requirements": [{"id": "r1", "verdict": "fail"}], "summary": "wrapped roof"}}
    assert model_view(soft, jim_acted=True, jim_passed=False) == "model recommends skip; proposes 1,000K; ✗ No sticker paint; wrapped roof | DISAGREE: you bid, the model would skip"
    soft["recommendation"] = "bid"
    assert model_view(soft, jim_acted=False, jim_passed=True).endswith("| DISAGREE: you passed, the model would bid")
    assert model_view(soft, jim_acted=True, jim_passed=False).endswith("| agree")
    assert model_view({"mode": "manual"}, True, False) == "" and model_view({}, True, False) == ""


def test_playbook_command_lists_and_shows(cfg, monkeypatch):
    from typer.testing import CliRunner
    import auction_bot.cli as cli
    from auction_bot.judge.playbook import append_playbook
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    runner = CliRunner()
    res = runner.invoke(cli.app, ["playbook"])
    assert res.exit_code == 0 and "General principles:" in res.output and "no model code has notes yet" in res.output
    append_playbook(cfg, "gdh206", "leather package looks like this", author="review")
    res = runner.invoke(cli.app, ["playbook"])
    assert "GDH206" in res.output and "1 line(s)" in res.output
    res = runner.invoke(cli.app, ["playbook", "gdh206"])
    assert res.exit_code == 0 and "leather package looks like this" in res.output and "# Principles" in res.output
    assert "Your review decisions on GDH206: 0" in res.output


def test_eligibility_card_and_badge(cfg, monkeypatch):
    cfg.eligibility = {"enabled": True, "judge_text": True}
    reply = json.loads(__import__("tests.test_judge_pipeline", fromlist=["_answer"])._answer())
    reply["requirements"].append({"id": "elig:sev", "verdict": "fail", "evidence": "sheet: 2WD", "confidence": "high"})
    pid = _judged(cfg, monkeypatch, reply=json.dumps(reply))
    client = TestClient(create_app(cfg))
    r = client.get(f"/p/{pid}")
    assert "Import eligibility (ROVER)" in r.text and "SEV-000813 / MRE-000233" in r.text and "4x4 Campervan only" in r.text
    assert "cannot import" in r.text and "sheet: 2WD" in r.text
    card = r.text.split("The model's judgement")[1].split("</section>")[0]
    rows = card.split('<ul class="checks">')[1].split("</ul>")[0]
    assert "SEV-000813" not in rows                                        # the requirement list leaves the pair to the eligibility card
    assert '<option value="elig:sev">' in r.text                            # but still correctable
    r = client.get("/review")
    assert "cannot import" in r.text and r.text.index("cannot import") < r.text.index("model: skip")


def test_ladder_steps_numbered_photos_and_corrections(cfg, monkeypatch):
    from tests.test_judge_pipeline import _ladder_fixture, _ladder_reply
    cfg.catalog = {"enabled": False}
    cfg.judge = {"max_photos": 2, "max_photos_ladder": 40}
    _ladder_fixture(cfg)
    pid = _judged(cfg, monkeypatch, reply=_ladder_reply())
    client = TestClient(create_app(cfg))
    r = client.get(f"/p/{pid}")
    assert 'class="steps"' in r.text and "<b>sheet</b>: not_mentioned" in r.text and 'data-zoom-photo="4"' in r.text and "bare spokes" in r.text
    assert 'id="photo-4"' in r.text and 'class="photo-no">4<' in r.text and 'data-zoom data-photo-no="1"' in r.text
    assert "step--decided" in r.text and "maker: not needed" not in r.text.split('class="steps"')[0]
    r = client.get("/review")
    assert "photo 4: no (bare spokes)" in r.text and "model: skip" in r.text   # the why-line and the pill
    r = client.post(f"/p/{pid}/decide", data={"action": "approve", "amount": "1240", "feedback_req": "r2", "feedback_note": "stalk is behind the wheel, photo 6", "via": "detail"})
    assert r.status_code in (200, 303)
    line = next(ln for ln in code_lines(cfg, "GDH206") if "Model was wrong about" in ln)   # the overall "approved a skip" line follows it
    assert "Model was wrong about 'Cruise control'" in line and "photo 4: no (bare spokes)" in line and "photo 6" in line
