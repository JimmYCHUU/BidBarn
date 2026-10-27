"""The simpler dashboard (2026-09-30): two modes on the overview, the steps behind a fold, a slim
review row, a Bid run that waits for Finish on the page, a Learn run's session on its run page."""
from __future__ import annotations

import re

from auction_bot.store import Store
from tests.test_dashboard_app import FakeJobs, _client, _run  # noqa: F401
from tests.test_pipeline_offline import cfg  # noqa: F401  (fixture)
from tests.test_triage import _seed


def test_the_runs_page_folds_the_steps_with_few_options(cfg):
    client, _ = _client(cfg)
    page = client.get("/runs").text
    assert '<details class="card start-card">' in page and 'start-card" open' not in page  # closed, always
    assert "Start a single step" in page and "Learn and Bid start from the overview." in page
    assert 'data-run-command="scan"' in page and 'name="model"' in page and 'name="limit"' in page
    assert 'name="rescan"' not in page and 'name="fresh"' not in page and 'name="headless"' not in page  # kept in the table, not shown
    assert 'data-run-command="bid"' not in page and 'data-run-command="learn"' not in page and 'data-run-command="insights"' not in page
    assert "Uses the bot's browser" not in page and "Offline. No Nichibo traffic." not in page
    assert ">Learn</a>" in page and ">Bid</a>" in page and ">Steps</a>" in page
    assert client.get("/runs?command=steps").status_code == 200 and "single steps only" in client.get("/runs?command=steps").text
    # The sheet reader trial is a step on this page, with its one option (how many sheets) showing.
    trial = page.split('data-run-command="sheet-trial"')[1].split("</form>")[0]
    assert "Sheet reader trial" in page and 'name="count"' in trial and 'name="model"' not in trial


def test_the_review_page_is_slim(cfg):
    store = Store(cfg.db_path)
    _seed(store, "A", "GDH206", "4", 90_000)
    store.close()
    client, _ = _client(cfg)
    page = client.get("/review").text
    assert 'id="keys"' in page and 'aria-controls="keys"' in page and "<kbd>p</kbd>" not in page  # the keys are a popover; no photos key
    assert 'class="why-line"' in page and 'class="banner' not in page.split("<main>")[1]  # one line of why, no lock banner
    assert ">Photos" not in page and 'href="/guides/GDH206"' in page  # one Nichibo link; the group links to its guide
    assert "1 lot in 1 model" in page and 'placeholder="Filter by model code"' in page and ">Filter</button>" not in page


def test_a_bid_run_waiting_at_the_review_asks_for_it(cfg):
    client, jobs = _client(cfg)
    store = Store(cfg.db_path)
    _seed(store, "A", "GDH206", "4", 90_000)
    _seed(store, "B", "GDH206", "4", 80_000)
    store.close()
    rid = client.post("/api/runs", json={"command": "bid"}).json()["id"]
    jobs.progress_is({"stage": "scan", "step": 3, "steps": 5, "guides": 1, "guide": 1})
    page = client.get("/overview").text
    assert "Running Bid mode" in page and "Watch the run" in page and 'data-stages' in page  # the stages line, only for a Bid run
    assert "Open the review" not in page and "Finish review and place approved bids" not in page
    jobs.progress_is({"stage": "review", "step": 4, "steps": 5, "pending": 2, "approved": 0, "waiting": True})
    page = client.get("/overview").text
    assert "Review 2 lots, then finish" in page and "Open the review" in page
    assert "Finish review and place approved bids" in page  # the header button, on every page while the run waits
    run_page = client.get(f"/runs/{rid}").text
    assert "Open the review" in run_page and "data-review-banner hidden" not in run_page
    assert "Finish review and place approved bids" in client.get("/review").text


def test_a_learn_run_page_shows_the_session(cfg):
    summary = {
        "session": "20260930-101500", "report": "x", "searched": ["ANH20W"],
        "counts": {"lots": 2, "opened": 1, "carted": 1, "bids": 1, "agree": 1, "disagree": 1, "searches": 1, "notes": 1,
                   "candidates": 1, "actions_saved": 3, "screenshots": 4},
        "lots": [
            {"ref_no": "R1", "title": "2012 Toyota Alphard 240S", "code": "ANH20W", "proposal_id": None,
             "jim": {"opened": True, "carted": True, "cart_clicks": 1, "bid_k": 760, "typed_k": 760, "reasons": ["clean sheet"], "seconds": 9, "sheet_viewed": True},
             "bot": {"status": "NEEDS_REVIEW", "bid_k": 710, "reason": "judge the sheet", "decision": "pending", "delta_k": 50},
             "model": {"recommendation": "bid", "proposed_k": 710, "summary": "dark seats", "agree": True, "line": "model recommends bid"},
             "candidates": []},
            {"ref_no": "R2", "title": "2011 Toyota Alphard 240X", "code": "ANH20W", "proposal_id": None,
             "jim": {"opened": False, "carted": False, "cart_clicks": 2, "bid_k": None, "typed_k": None, "reasons": ["skip: repaint"], "seconds": 3, "sheet_viewed": False},
             "bot": None,
             "model": {"recommendation": "bid", "proposed_k": 640, "summary": "x", "agree": False, "line": "model recommends bid"},
             "candidates": [{"line": "skip: grade <= 3.5", "from_reason": "skip: repaint"}]},
        ],
        "notes": [{"where": "cart", "text": "never click the stock cards"}],
        "candidates": [{"code": "ANH20W", "ref_no": "R2", "line": "skip: grade <= 3.5", "from_reason": "skip: repaint"}],
        "dashboard": {"active_seconds": 60, "headings": []},
    }
    rid = _run(cfg, "learn", ["learn"], summary=summary, progress={"stage": "learn", "phase": "compare", "session": "20260930-101500"})
    client, _ = _client(cfg)
    page = client.get(f"/runs/{rid}").text
    assert "Your session" in page and "2012 Toyota Alphard 240S" in page and "never seen" in page
    assert ">agree</span>" in page and ">disagree</span>" in page and "you +50K against the grid" in page
    assert 'action="/rules/ANH20W/skip"' in page and f'value="/runs/{rid}"' in page and "skip: grade &lt;= 3.5" in page
    assert 'href="/guides/ANH20W"' in page and "never click the stock cards" in page
    assert ">Outcome<" not in page  # the session card replaces it
    overview = client.get("/overview").text
    assert "Learn mode" in overview and re.search(r"Lots <b>2</b>", overview) and "Agree <b>1</b>" in overview
