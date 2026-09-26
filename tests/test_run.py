"""`bot run` (bidbarn.cmd): the steps in order, the Finish button, and placement through place_bid."""
import threading
from contextlib import nullcontext

from fastapi.testclient import TestClient

from auction_bot.pipeline import scan
from auction_bot.review.app import create_app
from auction_bot.run import place_approved, run
from auction_bot.store import Store
from tests.test_card_sheet import _BidBrowser
from tests.test_pipeline_offline import FakeBrowser, cfg  # noqa: F401  (fixture)


def test_finish_button_only_when_started_by_run(cfg):
    plain = TestClient(create_app(cfg))
    assert "Finish review" not in plain.get("/").text
    assert plain.post("/finish").status_code == 404
    finished = threading.Event()
    client = TestClient(create_app(cfg, finish_event=finished))
    assert "Finish review" in client.get("/").text and "Finish review" in client.get("/list").text
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
