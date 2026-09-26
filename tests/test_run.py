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
