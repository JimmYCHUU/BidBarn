"""`bot run` (bidbarn.cmd): the steps in order, the Finish button, and placement through place_bid.
Under the dashboard the run waits for the finish file instead of serving its own review page."""
import os
import threading
import time
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auction_bot import lock
from auction_bot.pipeline import scan
from auction_bot.review.app import create_app
from auction_bot.nichibo.bid import PlacementResult
from auction_bot.run import place_approved, run, wait_for_review
from auction_bot.store import Store
from tests.test_card_sheet import _BidBrowser
from tests.test_pipeline_offline import FakeBrowser, cfg  # noqa: F401  (fixture)


def test_finish_button_only_when_started_by_run(cfg):
    plain = TestClient(create_app(cfg))
    assert "Finish review" not in plain.get("/review").text
    assert plain.post("/finish").status_code == 404
    finished = threading.Event()
    client = TestClient(create_app(cfg, finish_event=finished))
    assert "Finish review" in client.get("/review").text and "Finish review" in client.get("/list").text
    r = client.post("/finish")
    assert r.status_code == 200 and "Review finished" in r.text and finished.is_set()


def test_run_calls_the_steps_in_order(cfg):
    order = []

    def fake(name, result=None):
        def f(c, echo, **kw):
            order.append((name, kw))
            return result
        return f

    steps = {"ensure_signed_in": fake("login"), "fetch_guides": fake("guides", (7, None)),
             "scan_all": fake("scan", {"guides": 7}), "serve_review": fake("review"),
             "place_approved": fake("place", ["GDH206 x -> 1240K: OK dry run"])}
    lines = []
    out = run(cfg, echo=lines.append, steps=steps, open_browser=False)
    assert [n for n, _ in order] == ["login", "guides", "scan", "review", "place"]
    assert out.guides == 7 and out.scan == {"guides": 7} and out.approved == 1 and len(out.steps) == 5
    assert any("[1/5]" in ln for ln in lines) and any("testing lock" in ln for ln in lines) and lines[-1].startswith("Done.")
    assert dict(order[2][1]) == {"headless": True} and dict(order[3][1]) == {"open_browser": False}
    order.clear()
    out = run(cfg, echo=lines.append, steps=steps, skip_scan=True, no_place=True, headed=True)
    assert [n for n, _ in order] == ["login", "guides", "review"] and out.placed == [] and len(out.steps) == 3


def test_place_approved_uses_place_bid_and_records(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    a, b = [r["id"] for r in store.list_proposals(decision="pending", status="NEEDS_REVIEW")]
    store.decide(a, "approve", None, 1240, False)
    store.decide(b, "reject", "no", None, False)
    store.close()
    lines = []
    out = place_approved(cfg, echo=lines.append, browser_factory=lambda: nullcontext(_BidBrowser(1240)))
    assert len(out) == 1 and "-> 1240K: OK dry run" in out[0]  # the testing lock keeps it a dry run
    assert any("DRY RUN (testing lock" in ln for ln in lines)
    store = Store(cfg.db_path)
    assert not store.already_bid(store.proposal(a)["ref_no"])  # dry run: nothing recorded as placed
    assert "dry run" in store.proposal(a)["placement_json"]
    assert store.proposal(b)["decision"] == "rejected" and store.proposal(b)["placement_json"] is None
    store.close()


def test_place_approved_survives_a_broken_lot(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    pid = store.list_proposals(decision="pending")[0]["id"]
    store.decide(pid, "approve", None, 1240, False)
    store.close()

    class Broken(FakeBrowser):
        def goto(self, url, wait_until="domcontentloaded"):
            raise RuntimeError("Nichibo timed out")

    out = place_approved(cfg, echo=lambda s: None, browser_factory=lambda: nullcontext(Broken()))
    assert len(out) == 1 and "BLOCKED error: Nichibo timed out" in out[0]


def test_learn_watches_then_compares_and_suggests(cfg, tmp_path):
    import json
    from auction_bot.pipeline import scan as real_scan

    N = "https://autosearch.nichibojapan.com"
    # The bot's view: a real (offline) scan of GDH206, then Jim's session refers to one of those lots.
    store = Store(cfg.db_path)
    real_scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    row = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]
    ref, bot_bid = row["ref_no"], row["bid_jpy_k"]
    store.close()
    session = tmp_path / "sessions" / "20260926-100000"
    session.mkdir(parents=True)
    card = {"tag": "button", "text": "cart", "refno": ref, "card_ref": ref, "card": "2020 Toyota Hiace"}
    events = [
        {"t": "2026-09-26T10:00:00.000", "type": "session", "started": "2026-09-26T10:00:00.000"},
        {"t": "2026-09-26T10:00:01.000", "type": "nav", "url": N + "/auction?Chassis=GDH206", "ref": None, "chassis": "GDH206"},
        {"t": "2026-09-26T10:00:05.000", "type": "click", "url": N + "/auction?Chassis=GDH206", "el": card},
        {"t": "2026-09-26T10:00:08.000", "type": "reason", "url": N + "/auction?Chassis=GDH206", "text": "grade 4 and under the cap"},
        {"t": "2026-09-26T10:00:20.000", "type": "nav", "url": N + "/auction/cart", "ref": None, "chassis": None},
        {"t": "2026-09-26T10:00:25.000", "type": "input", "url": N + "/auction/cart", "el": {"tag": "input", "card_ref": ref, "card": "2020 Toyota Hiace"}, "value": str(bot_bid + 50)},
        {"t": "2026-09-26T10:00:30.000", "type": "reason", "url": N + "/auction/cart", "text": "would normally skip, already 122k odo"},
        {"t": "2026-09-26T10:03:00.000", "type": "reason", "url": N + "/auction/cart", "text": "never click the stock cards"},
        {"t": "2026-09-26T10:03:10.000", "type": "session_end", "ended": "2026-09-26T10:03:10.000"},
    ]
    (session / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    calls = []
    steps = {"watch": lambda c, echo: (calls.append("watch"), session)[1],
             "fetch_guides": lambda c, echo: (calls.append("guides"), (1, None))[1],
             "scan_codes": lambda c, codes, echo: (calls.append(("scan", codes)), {})[1]}
    lines = []
    from auction_bot.run import learn
    out = learn(cfg, echo=lines.append, steps=steps)
    assert calls == ["watch", "guides", ("scan", ["GDH206"])] and out == session
    text = (session / "report.txt").read_text(encoding="utf-8")
    assert "2020 Toyota Hiace: Jim typed" in text and f"bot NEEDS_REVIEW {bot_bid:,}K (Jim +50K against the grid)" in text
    assert "why: grade 4 and under the cap" in text and "why: would normally skip, already 122k odo" in text
    assert "candidate rule for GDH206: skip: km >= 122000" in text  # "122k odo" understood, and the lot is at 122,000 km
    assert "note [cart]: never click the stock cards" in text
    store = Store(cfg.db_path)
    rows = store.conn.execute("SELECT action, note FROM manual_actions ORDER BY id").fetchall()
    assert [r["action"] for r in rows] == ["reason", "reason"] and rows[1]["note"].startswith("would normally skip")
    store.close()
    assert any("candidate rule becomes a rule only when you add it" in ln for ln in lines)


# -- Bid mode under the dashboard: the finish file ------------------------------------------------------------

def _events(text: str) -> list[dict]:
    from auction_bot import progress
    parsed = [progress.parse(ln) for ln in text.splitlines()]
    return [p[1] for p in parsed if p and p[0] == "progress"]


def test_wait_for_review_returns_on_the_finish_file(cfg, monkeypatch, tmp_path, capsys):
    flag = tmp_path / "7.finish"
    flag.write_text("finish", encoding="utf-8")  # Finish was clicked before the run got here: it goes straight on
    monkeypatch.setenv("BIDBARN_FINISH_FILE", str(flag))
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    lines = []
    capsys.readouterr()
    wait_for_review(cfg, echo=lines.append, open_browser=True, poll_seconds=0.01)
    assert _events(capsys.readouterr().out) == [{"stage": "review", "pending": 0, "approved": 0, "waiting": True},
                                                {"stage": "review", "pending": 0, "approved": 0, "waiting": False}]
    assert not flag.exists() and lines[-1] == "Review finished." and "Finish review" in lines[0]


def test_wait_for_review_stops_on_cancel(cfg, monkeypatch, tmp_path, capsys):
    from auction_bot.progress import Cancelled
    monkeypatch.setenv("BIDBARN_FINISH_FILE", str(tmp_path / "8.finish"))
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(tmp_path / "8.cancel"))
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    (tmp_path / "8.cancel").write_text("stop", encoding="utf-8")
    capsys.readouterr()
    with pytest.raises(Cancelled, match="stopped from the dashboard"):
        wait_for_review(cfg, echo=lambda s: None, poll_seconds=0.01)
    out = capsys.readouterr().out
    assert _events(out) == [] and _summaries(out) == []  # stopped before the first look: nothing reported, no summary


def test_wait_for_review_reports_count_changes(cfg, monkeypatch, tmp_path, capsys):
    from tests.test_triage import _seed
    store = Store(cfg.db_path)
    pid = _seed(store, "A", "GDH206", "4", 90_000)
    store.close()
    flag = tmp_path / "9.finish"
    monkeypatch.setenv("BIDBARN_FINISH_FILE", str(flag))
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    page = TestClient(create_app(cfg))

    def jim():
        time.sleep(0.3)  # approves the lot on the review page while the run waits
        assert page.post(f"/p/{pid}/decide", data={"action": "approve", "amount": "1000"}, follow_redirects=False).status_code == 303
        time.sleep(0.3)
        flag.write_text("finish", encoding="utf-8")

    t = threading.Thread(target=jim)
    capsys.readouterr()
    t.start()
    wait_for_review(cfg, echo=lambda s: None, poll_seconds=0.02)
    t.join()
    events = _events(capsys.readouterr().out)
    assert events[0] == {"stage": "review", "pending": 1, "approved": 0, "waiting": True}
    assert events[1] == {"stage": "review", "pending": 0, "approved": 1, "waiting": True}  # told once, when it changed
    assert events[-1] == {"stage": "review", "pending": 0, "approved": 1, "waiting": False} and len(events) == 3
    assert not flag.exists()


def test_run_under_the_dashboard_waits_for_the_finish_file_and_reports(cfg, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BIDBARN_FINISH_FILE", str(tmp_path / "3.finish"))
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    order, held = [], []

    def fake(name, result=None):
        def f(c, echo, **kw):
            order.append(name)
            held.append(lock.holder(c)["pid"] == os.getpid())  # the profile is this run's for the whole day
            return result
        return f

    scan_result = {"guides": 7, "cards": 120, "opened": 9, "sheet_only": 6, "pages": 3, "review": 4, "bid": 1, "skip": 30,
                   "reused": 80, "pending": 2, "model_calls": 5, "cache_hits": 40, "errors": [f"E{i}" for i in range(25)]}
    steps = {"ensure_signed_in": fake("login"), "fetch_guides": fake("guides", (7, None)), "scan_all": fake("scan", scan_result),
             "serve_review": fake("served"), "wait_for_review": fake("waited"),
             "place_approved": fake("place", ["GDH206 x -> 1240K: OK dry run", "GDH206 y -> 1190K: OK dry run"])}
    capsys.readouterr()
    out = run(cfg, echo=lambda s: None, steps=steps, open_browser=False)
    assert order == ["login", "guides", "scan", "waited", "place"] and all(held) and lock.holder(cfg) is None
    assert not lock.lock_path(cfg).exists()
    text = capsys.readouterr().out
    assert _events(text) == [{"stage": "signin", "step": 1, "steps": 5}, {"stage": "guides", "step": 2, "steps": 5},
                             {"stage": "scan", "step": 3, "steps": 5}, {"stage": "review", "step": 4, "steps": 5},
                             {"stage": "place", "step": 5, "steps": 5}]
    told = _summaries(text)
    assert len(told) == 1 and told[0] == {
        "steps": out.steps, "nichibo": True, "carbarn": True, "guides": 7, "cards": 120, "opened": 9, "review": 4, "bid": 1,
        "skip": 30, "reused": 80, "pending": 2, "model_calls": 5, "errors": [f"E{i}" for i in range(20)], "errors_total": 25,
        "approved": 2, "dry_run": True, "lines": ["GDH206 x -> 1240K: OK dry run", "GDH206 y -> 1190K: OK dry run"]}
    # Without the variable it is the console's own review page, and no summary line.
    monkeypatch.delenv("BIDBARN_FINISH_FILE")
    monkeypatch.delenv("BIDBARN_PROGRESS")
    order.clear()
    run(cfg, echo=lambda s: None, steps=steps, skip_scan=True, no_place=True)
    assert order == ["login", "guides", "served"] and "SUMMARY" not in capsys.readouterr().out


def test_run_gives_the_profile_back_when_a_step_fails(cfg):
    def broken(c, echo, **kw):
        raise RuntimeError("Nichibo timed out")

    with pytest.raises(RuntimeError, match="Nichibo timed out"):
        run(cfg, echo=lambda s: None, steps={"ensure_signed_in": broken})
    assert lock.holder(cfg) is None and not lock.lock_path(cfg).exists()


def test_serve_review_refuses_a_busy_port(cfg, monkeypatch):
    import socket
    import auction_bot.run as runmod
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    served = []
    monkeypatch.setattr(runmod, "wait_for_review", lambda *a, **k: served.append("waited"))
    try:
        cfg.review = {"host": "127.0.0.1", "port": listener.getsockname()[1]}
        with pytest.raises(RuntimeError, match="dashboard is running on that address"):
            runmod.serve_review(cfg, echo=lambda s: None, open_browser=False)
    finally:
        listener.close()
    assert served == [] and runmod.port_in_use("127.0.0.1", cfg.review["port"]) is False


def test_sign_in_step_reports_progress(cfg, monkeypatch, capsys):
    import auction_bot.run as runmod
    _fake_browser(monkeypatch)
    monkeypatch.setattr(runmod, "nichibo_signed_in", lambda b, c: True)
    monkeypatch.setattr(runmod, "carbarn_signed_in", lambda b, c: True)
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    lines = []
    capsys.readouterr()
    runmod.ensure_signed_in(cfg, echo=lines.append)
    assert _events(capsys.readouterr().out) == [{"stage": "signin", "nichibo": True, "carbarn": True, "waiting": False}]
    assert lines == ["Signed in to Nichibo and the Carbarn dashboard."]

    # Signed out of Nichibo: a window opens (waiting), Jim signs in, and the second line says both are in.
    class Signed(_Tab):
        url = "https://autosearch.nichibojapan.com/auction"

        def on(self, event, fn):
            pass

        def wait_for_timeout(self, ms):
            pass

    class Window:
        def __init__(self, c, headless=False):
            self.page, self.context = Signed([]), self

        def new_page(self):
            return Signed([])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    import auction_bot.nichibo.browser as browser_mod
    monkeypatch.setattr(browser_mod, "Browser", Window)
    monkeypatch.setattr(runmod, "nichibo_signed_in", lambda b, c: False)
    lines.clear()
    runmod.ensure_signed_in(cfg, echo=lines.append)
    assert _events(capsys.readouterr().out) == [{"stage": "signin", "nichibo": False, "carbarn": True, "waiting": True},
                                                {"stage": "signin", "nichibo": True, "carbarn": True, "waiting": False}]
    assert lines[0].startswith("Not signed in to Nichibo.") and lines[-1] == "Signed in. Continuing."


def test_guides_step_reports_progress(cfg, monkeypatch, capsys):
    import auction_bot.carbarn.client as client_mod
    import auction_bot.run as runmod
    _fake_browser(monkeypatch)

    class Guides:
        def __init__(self, browser, c):
            pass

        def fetch_team_guides(self, codes, include_inactive):
            return [{"item": {"modelCode": "GDH206", "make": "Toyota", "model": "Hiace", "minGrade": 3.5,
                              "variants": [{"name": None, "active": True}]}, "tree": {}}]

    monkeypatch.setattr(client_mod, "CarbarnClient", Guides)
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    capsys.readouterr()
    n, path = runmod.fetch_guides(cfg, echo=lambda s: None)
    assert n == 1 and path.exists()
    assert _events(capsys.readouterr().out) == [{"stage": "guides", "guides": 1, "snapshot": path.name}]


# -- Learn mode's structured summary ---------------------------------------------------------------------------

def _jims_session(cfg, tmp_path):
    """The bot's own view of one GDH206 lot, and a recorded session in which Jim carts it, types an amount and why."""
    import json
    from auction_bot.pipeline import scan as real_scan

    N = "https://autosearch.nichibojapan.com"
    store = Store(cfg.db_path)
    real_scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    row = store.list_proposals(decision="pending", status="NEEDS_REVIEW")[0]
    ref, bot_bid, pid = row["ref_no"], row["bid_jpy_k"], row["id"]
    store.close()
    session = tmp_path / "sessions" / "20260926-100000"
    session.mkdir(parents=True)
    card = {"tag": "button", "text": "cart", "refno": ref, "card_ref": ref, "card": "2020 Toyota Hiace"}
    events = [
        {"t": "2026-09-26T10:00:00.000", "type": "session", "started": "2026-09-26T10:00:00.000"},
        {"t": "2026-09-26T10:00:01.000", "type": "nav", "url": N + "/auction?Chassis=GDH206", "ref": None, "chassis": "GDH206"},
        {"t": "2026-09-26T10:00:05.000", "type": "click", "url": N + "/auction?Chassis=GDH206", "el": card},
        {"t": "2026-09-26T10:00:08.000", "type": "reason", "url": N + "/auction?Chassis=GDH206", "text": "grade 4 and under the cap"},
        {"t": "2026-09-26T10:00:20.000", "type": "nav", "url": N + "/auction/cart", "ref": None, "chassis": None},
        {"t": "2026-09-26T10:00:25.000", "type": "input", "url": N + "/auction/cart", "el": {"tag": "input", "card_ref": ref, "card": "2020 Toyota Hiace"}, "value": str(bot_bid + 50)},
        {"t": "2026-09-26T10:00:30.000", "type": "reason", "url": N + "/auction/cart", "text": "would normally skip, already 122k odo"},
        {"t": "2026-09-26T10:03:00.000", "type": "reason", "url": N + "/auction/cart", "text": "never click the stock cards"},
        {"t": "2026-09-26T10:03:10.000", "type": "session_end", "ended": "2026-09-26T10:03:10.000"},
    ]
    (session / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return session, ref, bot_bid, pid


def test_learn_summary_is_structured(cfg, tmp_path, monkeypatch, capsys):
    from auction_bot.run import learn
    session, ref, bot_bid, pid = _jims_session(cfg, tmp_path)
    steps = {"watch": lambda c, echo: session, "fetch_guides": lambda c, echo: (1, None), "scan_codes": lambda c, codes, echo: {}}
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    capsys.readouterr()
    learn(cfg, echo=lambda s: None, steps=steps)
    out = capsys.readouterr().out
    assert [(e["stage"], e["phase"], e["searched"]) for e in _events(out)] == [("learn", "guides", ["GDH206"]), ("learn", "scan", ["GDH206"]),
                                                                              ("learn", "compare", ["GDH206"])]
    told = _summaries(out)
    assert len(told) == 1
    told = told[0]
    assert told["session"] == "20260926-100000" and told["report"] == str(session / "report.txt") and told["searched"] == ["GDH206"]
    assert told["counts"] == {"lots": 1, "opened": 0, "carted": 1, "bids": 0, "agree": 0, "disagree": 0, "searches": 1, "notes": 1,
                              "candidates": 1, "actions_saved": 2, "screenshots": 0}
    lot = told["lots"][0]
    assert lot["ref_no"] == ref and lot["title"] == "2020 Toyota Hiace" and lot["code"] == "GDH206" and lot["proposal_id"] == pid
    assert lot["jim"] == {"opened": False, "carted": True, "cart_clicks": 1, "bid_k": None, "typed_k": bot_bid + 50,
                          "reasons": ["grade 4 and under the cap", "would normally skip, already 122k odo"], "seconds": None,
                          "sheet_viewed": False}
    assert lot["bot"]["status"] == "NEEDS_REVIEW" and lot["bot"]["bid_k"] == bot_bid and lot["bot"]["delta_k"] == 50
    assert lot["bot"]["decision"] == "pending" and lot["model"] is None  # sheet_check none: no judgement to agree with
    assert lot["candidates"] == [{"line": "skip: km >= 122000", "from_reason": "would normally skip, already 122k odo"}]
    assert told["candidates"] == [{"code": "GDH206", "ref_no": ref, "line": "skip: km >= 122000",
                                   "from_reason": "would normally skip, already 122k odo"}]
    assert told["notes"] == [{"where": "cart", "text": "never click the stock cards"}]
    assert told["dashboard"] == {"active_seconds": 0, "headings": []}


def test_compare_lines_is_the_report_text(cfg, tmp_path):
    from auction_bot.run import compare_lines, compare_lots, compare_session
    from auction_bot.watch import load_events, timeline
    session, ref, bot_bid, _ = _jims_session(cfg, tmp_path)
    s = timeline(load_events(session), str(session))
    store = Store(cfg.db_path)
    try:
        lots = compare_lots(s, store)
        lines = compare_lines(lots, s.notes)
        assert lines == compare_session(cfg, s, store)
    finally:
        store.close()
    assert lines[0].startswith(f"- 2020 Toyota Hiace: Jim typed {bot_bid + 50}K, left in the cart | bot NEEDS_REVIEW {bot_bid:,}K")
    assert lines[0].endswith(" (Jim +50K against the grid)")
    assert lines[1:] == ["     why: grade 4 and under the cap", "     why: would normally skip, already 122k odo",
                         "     candidate rule for GDH206: skip: km >= 122000", "- note [cart]: never click the stock cards"]
    # A lot the bot never saw, and a judged lot Jim disagreed with, in plain data and in text.
    never = {"ref_no": "Z", "title": "Z", "code": None, "proposal_id": None, "bot": None, "model": None, "candidates": [],
             "jim": {"opened": True, "carted": False, "cart_clicks": 0, "bid_k": 900, "typed_k": None, "reasons": [], "seconds": 4,
                     "sheet_viewed": True}}
    assert compare_lines([never], []) == ["- Z: Jim bid 900K, opened the page | bot never seen (not in the bot's scan)"]
    judged = dict(never, bot={"status": "SKIP", "bid_k": None, "reason": "grade: 3 below 3.5", "decision": "pending", "delta_k": None},
                  model={"recommendation": "skip", "proposed_k": None, "summary": "", "agree": False, "line": "model recommends skip | DISAGREE: you bid, the model would skip"})
    assert compare_lines([judged], []) == ["- Z: Jim bid 900K, opened the page | bot SKIP because grade: 3 below 3.5",
                                           "     model recommends skip | DISAGREE: you bid, the model would skip"]


def test_model_agrees_reads_the_judgement():
    from auction_bot.run import model_agrees
    bid, skip = {"mode": "judge", "recommendation": "bid"}, {"mode": "judge", "recommendation": "skip"}
    assert model_agrees(bid, jim_acted=True, jim_passed=False) is True
    assert model_agrees(skip, jim_acted=True, jim_passed=False) is False
    assert model_agrees(bid, jim_acted=False, jim_passed=True) is False
    assert model_agrees(skip, jim_acted=False, jim_passed=True) is None and model_agrees({}, True, False) is None
    assert model_agrees({"mode": "manual"}, True, False) is None


def test_learn_and_bid_commands_dispatch(cfg, monkeypatch):
    from typer.testing import CliRunner
    import auction_bot.cli as cli
    import auction_bot.run as runmod
    called = []
    monkeypatch.setattr(runmod, "learn", lambda c, echo: called.append("learn"))
    monkeypatch.setattr(runmod, "run", lambda c, echo, **kw: called.append(("bid", kw["skip_scan"])))
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    assert CliRunner().invoke(cli.app, ["learn"]).exit_code == 0 and called == ["learn"]
    assert CliRunner().invoke(cli.app, ["bid", "--skip-scan"]).exit_code == 0 and called == ["learn", ("bid", True)]


# -- the commands the dashboard starts ------------------------------------------------------------------------

class _Tab:
    def __init__(self, log):
        self.log = log

    def goto(self, url, **kw):
        self.log.append(url)


class _Context:
    def __init__(self, log):
        self.log = log

    def new_page(self):
        return _Tab(self.log)


def _fake_browser(monkeypatch):
    """Stands in for the real Browser: records how it was opened, loads nothing."""
    import auction_bot.nichibo.browser as browser_mod
    opened, pages = [], []

    class Opened:
        def __init__(self, c, headless=False):
            opened.append(headless)
            self.page, self.context = _Tab(pages), _Context(pages)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(browser_mod, "Browser", Opened)
    return opened, pages


def _summaries(output: str) -> list[dict]:
    from auction_bot import progress
    parsed = [progress.parse(ln) for ln in output.splitlines()]
    return [p[1] for p in parsed if p and p[0] == "summary"]


def test_check_login_command(cfg, monkeypatch):
    from typer.testing import CliRunner
    import auction_bot.cli as cli
    import auction_bot.run as runmod
    opened, _ = _fake_browser(monkeypatch)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(runmod, "nichibo_signed_in", lambda b, c: True)
    monkeypatch.setattr(runmod, "carbarn_signed_in", lambda b, c: False)
    monkeypatch.delenv("BIDBARN_PROGRESS", raising=False)
    res = CliRunner().invoke(cli.app, ["check-login"])
    assert res.exit_code == 0 and opened == [True]  # headless: no window
    assert res.output.splitlines() == ["Nichibo: signed in", "Carbarn dashboard: NOT signed in"]
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    res = CliRunner().invoke(cli.app, ["check-login"])
    assert res.exit_code == 0 and _summaries(res.output) == [{"nichibo": True, "carbarn": False}]  # signed out is an answer, not a failure
    assert res.output.splitlines()[-1] == 'SUMMARY {"nichibo":true,"carbarn":false}'

    def held(b, c):
        raise RuntimeError("another BidBarn run (scan, pid 1, started 09:00) is using the browser profile")

    monkeypatch.setattr(runmod, "nichibo_signed_in", held)
    res = CliRunner().invoke(cli.app, ["check-login"])
    assert res.exit_code == 1 and "STOPPED: another BidBarn run (scan" in res.output and _summaries(res.output) == []


def test_login_wait_needs_no_keyboard(cfg, monkeypatch):
    from typer.testing import CliRunner
    import auction_bot.cli as cli
    import auction_bot.run as runmod
    opened, pages = _fake_browser(monkeypatch)
    called, typed = [], []
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(runmod, "ensure_signed_in", lambda c, echo: (called.append(c), echo("Signed in. Continuing."))[0])
    monkeypatch.setattr("builtins.input", lambda *a: typed.append("enter") or "")
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    res = CliRunner().invoke(cli.app, ["login", "--wait"])
    assert res.exit_code == 0 and called == [cfg] and typed == [] and opened == []  # ensure_signed_in opens its own browser
    assert "Signed in. Continuing." in res.output and _summaries(res.output) == [{"nichibo": True, "carbarn": True}]

    def gave_up(c, echo):
        raise RuntimeError("Still not signed in after 15 minutes.")

    monkeypatch.setattr(runmod, "ensure_signed_in", gave_up)
    res = CliRunner().invoke(cli.app, ["login", "--wait"])
    assert res.exit_code == 1 and "STOPPED: Still not signed in" in res.output and _summaries(res.output) == []
    # Without the flag it is the login of before: a window, both tabs, Enter.
    called.clear()
    cfg.carbarn = {"dashboard_url": "https://dashboard.carbarn.com.au/?view=bidding"}
    monkeypatch.delenv("BIDBARN_PROGRESS")
    res = CliRunner().invoke(cli.app, ["login"])
    assert res.exit_code == 0 and opened == [False] and typed == ["enter"] and called == []
    assert pages == ["https://autosearch.nichibojapan.com/auction/search", "https://dashboard.carbarn.com.au/?view=bidding"]
    assert res.output.splitlines() == ["Sign in to BOTH tabs (Nichibo and Carbarn), then press Enter here to save the session.",
                                       f"Session saved in {cfg.profile_dir}"]


def test_fetch_rules_headless(cfg, monkeypatch):
    from typer.testing import CliRunner
    import auction_bot.carbarn.client as client_mod
    import auction_bot.cli as cli
    opened, _ = _fake_browser(monkeypatch)
    asked = []

    class Guides:
        def __init__(self, browser, c):
            pass

        def fetch_team_guides(self, codes, include_inactive):
            asked.append((codes, include_inactive))
            return [{"item": {"modelCode": "GDH206", "make": "Toyota", "model": "Hiace", "minGrade": 3.5,
                              "variants": [{"name": None, "active": True}]}, "tree": {}}]

    monkeypatch.setattr(client_mod, "CarbarnClient", Guides)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.delenv("BIDBARN_PROGRESS", raising=False)
    before = set(cfg.snapshot_dir.glob("rules_2*.json"))
    res = CliRunner().invoke(cli.app, ["fetch-rules"])
    assert res.exit_code == 0 and opened == [False] and asked == [(None, False)]  # as before: a window
    assert res.output.startswith("Saved 1 guides to ") and "SUMMARY" not in res.output
    assert res.output.splitlines()[-1].startswith("  GDH206     Toyota Hiace  minGrade=3.5  variants=['Default']")
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    res = CliRunner().invoke(cli.app, ["fetch-rules", "-m", "GDH206", "--headless"])
    assert res.exit_code == 0 and opened == [False, True] and asked[-1] == (["GDH206"], False)
    new = set(cfg.snapshot_dir.glob("rules_2*.json")) - before
    assert new and _summaries(res.output) == [{"guides": 1, "snapshot": sorted(new)[-1].name}]  # the file's name, not its path


def test_dashboard_command(cfg, monkeypatch):
    import socket

    import uvicorn
    from typer.testing import CliRunner

    import auction_bot.cli as cli
    import auction_bot.review.app as app_mod
    served, tabs, timers = [], [], []

    class Timer:
        def __init__(self, seconds, fn, args):
            self.seconds, self.fn, self.args, self.daemon = seconds, fn, args, False
            timers.append(self)

        def start(self):
            self.fn(*self.args)

    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(app_mod, "create_app", lambda c: ("the app", c))  # called with the config and nothing else
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.append((app, kw)))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: tabs.append(url))
    monkeypatch.setattr(cli.threading, "Timer", Timer)
    # A port that is certainly free, and later one that is certainly taken (this PC only, nothing leaves it).
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free = probe.getsockname()[1]
    probe.close()
    cfg.review = {"host": "127.0.0.1", "port": free}
    url = f"http://127.0.0.1:{free}/"

    res = CliRunner().invoke(cli.app, ["dashboard", "--no-browser"])
    assert res.exit_code == 0 and tabs == [] and timers == []
    assert served == [(("the app", cfg), {"host": "127.0.0.1", "port": free, "log_level": "warning"})]
    assert res.output.strip() == f"BidBarn dashboard: {url}"

    res = CliRunner().invoke(cli.app, ["dashboard"])
    assert res.exit_code == 0 and len(served) == 2 and tabs == [url]
    assert len(timers) == 1 and timers[0].seconds == 1.5 and timers[0].daemon is True

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(50)  # nobody accepts here, so every probe of the test waits in this queue
    try:
        taken = listener.getsockname()[1]
        assert cli.port_in_use("127.0.0.1", taken) and not cli.port_in_use("127.0.0.1", free)
        cfg.review = {"host": "127.0.0.1", "port": taken}
        res = CliRunner().invoke(cli.app, ["dashboard"])  # a second double-click: a tab, no second server
        assert res.exit_code == 0 and len(served) == 2 and tabs == [url, f"http://127.0.0.1:{taken}/"]
        assert "already running" in res.output
        res = CliRunner().invoke(cli.app, ["dashboard", "--no-browser"])
        assert res.exit_code == 0 and len(served) == 2 and len(tabs) == 2 and len(timers) == 1
    finally:
        listener.close()


def test_commands_print_a_summary_for_the_dashboard_only(cfg, monkeypatch):
    from typer.testing import CliRunner
    import auction_bot.cli as cli
    import auction_bot.run as runmod
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    store.close()

    def both(args):
        monkeypatch.delenv("BIDBARN_PROGRESS", raising=False)
        plain = CliRunner().invoke(cli.app, args)
        monkeypatch.setenv("BIDBARN_PROGRESS", "1")
        told = CliRunner().invoke(cli.app, args)
        assert plain.exit_code == 0 and told.exit_code == 0, (plain.output, told.output)
        assert "PROGRESS" not in plain.output and "SUMMARY" not in plain.output
        extra = [ln for ln in told.output.splitlines() if ln.startswith(("PROGRESS ", "SUMMARY "))]
        rest = [ln for ln in told.output.splitlines() if ln not in extra]
        assert rest == plain.output.splitlines()  # what a person reads is the same either way
        assert told.output.splitlines()[-1].startswith("SUMMARY ")
        return _summaries(told.output)[-1]

    replayed = both(["replay", "-m", "GDH206"])
    assert replayed["checked"] >= 2
    assert replayed == {"checked": replayed["checked"], "unchanged": replayed["checked"], "changed": 0, "needs_fetch": 0,
                        "applied": 0, "apply": False, "changes": [], "changes_total": 0}
    told = both(["insights"])
    assert told["lines"] >= 1 and told["decisions"] == 0
    # Placement: nothing is approved here, so there is nothing to place, and the summary says dry run.
    real, asked = runmod.place_approved, []
    monkeypatch.setattr(runmod, "place_approved",
                        lambda c, **kw: (asked.append(kw["live"]), real(c, **kw, browser_factory=lambda: None))[1])
    assert both(["place", "--headless"]) == {"dry_run": True, "lines": []} and asked == [False, False]
    # Judge with sheet_check none has nothing to do, and says so.
    assert both(["judge", "--pending"]) == {"skipped": "sheet_check is not 'judge' in config.yaml"}


def test_scan_command_reports_and_stops_when_asked(cfg, monkeypatch, tmp_path):
    from typer.testing import CliRunner
    import auction_bot.cli as cli
    import auction_bot.nichibo.browser as browser_mod
    opened = []

    class Offline(FakeBrowser):
        def __init__(self, c, headless=False):
            super().__init__()
            opened.append(headless)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(browser_mod, "Browser", Offline)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    res = CliRunner().invoke(cli.app, ["scan", "-m", "GDH206", "--headless", "--limit", "2"])
    assert res.exit_code == 0 and opened == [True]
    lines = res.stdout.splitlines()
    assert lines[-1].startswith("SUMMARY ") and lines[-2].startswith("Review at http://127.0.0.1:8765")
    told = _summaries(res.stdout)[-1]
    assert told["guides"] == 1 and told["opened"] == 2 and told["review"] == 2 and told["errors"] == [] and told["errors_total"] == 0
    assert _place_events(res.stdout)[-1]["guide"] == 1

    flag = tmp_path / "5.cancel"
    flag.write_text("stop", encoding="utf-8")
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(flag))
    res = CliRunner().invoke(cli.app, ["scan", "-m", "GDH206", "--headless", "--rescan"])
    assert res.exit_code == 1 and res.stdout.splitlines()[-1] == "STOPPED: stopped from the dashboard"
    assert _summaries(res.stdout) == []  # a run that was stopped has no summary


def test_scan_summary_keeps_at_most_twenty_errors(cfg, monkeypatch):
    from typer.testing import CliRunner
    import auction_bot.cli as cli
    import auction_bot.nichibo.browser as browser_mod
    import auction_bot.pipeline as pl

    class Closed:
        def __init__(self, c, headless=False):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(browser_mod, "Browser", Closed)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(pl, "scan", lambda *a, **k: pl.ScanStats(guides=46, errors=[f"G{i}: search failed" for i in range(45)]))
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    res = CliRunner().invoke(cli.app, ["scan", "--headless"])
    told = _summaries(res.stdout)[-1]
    assert res.exit_code == 0 and told["guides"] == 46 and told["errors_total"] == 45
    assert told["errors"] == [f"G{i}: search failed" for i in range(20)]
    assert res.stdout.count("search failed") == 45 + 20  # the report a person reads still lists them all


def _two_lots_approved_on_the_review_page(cfg) -> list[int]:
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    ids = sorted(r["id"] for r in store.list_proposals(decision="pending", status="NEEDS_REVIEW"))
    store.close()
    page = TestClient(create_app(cfg))  # the approval is the reviewer's click, as everywhere
    for pid in ids:
        assert page.post(f"/p/{pid}/decide", data={"action": "approve", "amount": "1240"}, follow_redirects=False).status_code == 303
    return ids


def _place_events(text: str) -> list[dict]:
    from auction_bot import progress
    parsed = [progress.parse(ln) for ln in text.splitlines()]
    return [p[1] for p in parsed if p and p[0] == "progress"]


def test_place_approved_reports_each_lot(cfg, monkeypatch, capsys):
    first, second = _two_lots_approved_on_the_review_page(cfg)
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    capsys.readouterr()
    lines = []
    out = place_approved(cfg, echo=lines.append, browser_factory=lambda: nullcontext(_BidBrowser(1240)))
    assert len(out) == 2 and all("-> 1240K: OK dry run" in ln for ln in out)  # what it returns and prints is as before
    assert lines == ["DRY RUN (testing lock: config.yaml project_status is not 'The project is finished'): 2 approved bid(s)",
                     "  " + out[0], "  " + out[1]]
    events = _place_events(capsys.readouterr().out)
    assert events[0] == {"stage": "place", "i": 0, "total": 2, "dry_run": True}
    assert [(e["i"], e["total"], e["proposal_id"]) for e in events[1:]] == [(1, 2, first), (2, 2, second)]
    store = Store(cfg.db_path)
    for e in events[1:]:
        assert e["stage"] == "place" and e["ok"] is True and e["dry_run"] is True and e["skipped"] is False
        assert e["message"] == "dry run: 1240K typed, not submitted" and e["amount_jpy_k"] == 1240 and e["model_code"] == "GDH206"
        assert e["ref_no"] == store.proposal(e["proposal_id"])["ref_no"]
        assert Path(e["screenshot"]).name == "bid_before.png" and (cfg.evidence_dir / e["screenshot_rel"]).exists()
        assert e["screenshot_rel"].endswith("/bid_before.png") and "\\" not in e["screenshot_rel"]
        assert not store.already_bid(e["ref_no"])  # a dry run places nothing
    store.close()


def test_place_approved_with_nothing_waiting(cfg, monkeypatch, capsys):
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    lines = []
    assert place_approved(cfg, echo=lines.append, browser_factory=lambda: None) == []
    assert lines == ["No approved bids waiting."]
    assert _place_events(capsys.readouterr().out) == [{"stage": "place", "i": 0, "total": 0, "dry_run": True}]


def test_place_approved_stops_between_lots_never_inside_one(cfg, monkeypatch, tmp_path, capsys):
    from auction_bot.progress import Cancelled
    first, second = _two_lots_approved_on_the_review_page(cfg)
    flag = tmp_path / "9.cancel"
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(flag))
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")

    class StopWhileTheFirstLotIsInHand(_BidBrowser):
        def screenshot(self, path):
            flag.write_text("stop", encoding="utf-8")  # Stop is clicked in the middle of the first lot
            super().screenshot(path)

    browser = StopWhileTheFirstLotIsInHand(1240)
    capsys.readouterr()
    lines = []
    with pytest.raises(Cancelled, match="stopped from the dashboard"):
        place_approved(cfg, echo=lines.append, browser_factory=lambda: nullcontext(browser))
    events = _place_events(capsys.readouterr().out)
    assert [e["i"] for e in events] == [0, 1] and events[1]["ok"] is True and events[1]["proposal_id"] == first
    assert len(browser.visited) == 1 and len(lines) == 2  # the first lot was finished and reported, the second not begun
    store = Store(cfg.db_path)
    assert "dry run" in store.proposal(first)["placement_json"] and store.proposal(second)["placement_json"] is None
    assert store.proposal(second)["decision"] == "approved"  # still waiting for the next placement
    store.close()

    # A stop that arrives just as a lot's page is about to load ends the run there: nothing typed, nothing recorded.
    class StoppedAtThePageLoad(_BidBrowser):
        def goto(self, url, wait_until="domcontentloaded"):
            raise Cancelled("stopped from the dashboard")

    flag.unlink()
    with pytest.raises(Cancelled):
        place_approved(cfg, echo=lines.append, browser_factory=lambda: nullcontext(StoppedAtThePageLoad(1240)))
    store = Store(cfg.db_path)
    assert store.proposal(second)["placement_json"] is None and len(lines) == 3  # only the DRY RUN heading was added
    store.close()


def _two_approved(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=2)
    a, b = [r["id"] for r in store.list_proposals(decision="pending", status="NEEDS_REVIEW")]
    store.decide(a, "approve", None, 1240, False)
    store.decide(b, "approve", None, 1240, False)
    return store, a, b


def test_place_approved_leaves_out_lots_already_placed_as_a_dry_run(cfg, monkeypatch):
    store, a, b = _two_approved(cfg)
    store.record_placement(a, store.proposal(a)["ref_no"], "GDH206", 1240, True, "dry ok", None, dry_run=True)
    store.close()
    seen = []

    def fake_place_bid(browser, cfg_, ref_no, amount, facts, dry_run=True):
        seen.append(ref_no)
        return PlacementResult(True, "dry run", None, dry_run)

    monkeypatch.setattr("auction_bot.nichibo.bid.place_bid", fake_place_bid)
    out = place_approved(cfg, echo=lambda s: None, browser_factory=lambda: nullcontext(object()))
    store = Store(cfg.db_path)
    assert seen == [store.proposal(b)["ref_no"]] and len(out) == 1
    store.close()


def test_place_approved_says_why_nothing_is_typed(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    a = store.list_proposals(decision="pending")[0]["id"]
    store.decide(a, "approve", None, 1240, False)
    store.record_placement(a, store.proposal(a)["ref_no"], "GDH206", 1240, True, "dry ok", None, dry_run=True)
    store.close()
    lines = []
    out = place_approved(cfg, echo=lines.append, browser_factory=lambda: nullcontext(object()))
    assert out == [] and any("already placed as a dry run" in ln and "Reopen" in ln for ln in lines)


def test_place_approved_skips_a_lot_reopened_during_the_run(cfg, monkeypatch):
    store, a, b = _two_approved(cfg)
    last = max(a, b)  # placement takes the older lot first; the newer is reopened while it runs
    store.close()
    seen = []

    def fake_place_bid(browser, cfg_, ref_no, amount, facts, dry_run=True):
        seen.append(ref_no)
        other = Store(cfg.db_path)
        other.reopen(last)
        other.close()
        return PlacementResult(True, "dry run", None, dry_run)

    monkeypatch.setattr("auction_bot.nichibo.bid.place_bid", fake_place_bid)
    out = place_approved(cfg, echo=lambda s: None, browser_factory=lambda: nullcontext(object()))
    store = Store(cfg.db_path)
    assert len(seen) == 1
    assert f"skip {store.proposal(last)['ref_no']}: reopened since the run started" in out
    store.close()
