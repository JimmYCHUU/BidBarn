"""The dashboard: the overview, the runs and their console, insights, settings, and the API behind them.

A fake job manager stands in for the real one (no process is started); the real one has its own
tests in test_jobs.py. Everything runs on the offline fixture config, in a temporary folder.
"""
import json
import re
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from auction_bot.dashboard import status as st
from auction_bot.dashboard.jobs import COMMANDS, Busy, DisabledJobManager, NotRunning, build_argv
from auction_bot.dashboard.routes import entry_pairs, summary_pairs
from auction_bot.review.app import create_app
from auction_bot.store import Store
from tests.test_pipeline_offline import cfg  # noqa: F401  (fixture)
from tests.test_triage import _seed


class FakeJobs(DisabledJobManager):
    """Reads runs like the real manager; `start` checks the options for real and records the run, no process."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.live: int | None = None
        self.cancelled: list[int] = []
        self.deleted: list[int] = []  # runs stopped by Delete session
        self.paused: list[int] = []
        self.finished: list[int] = []

    def current(self):
        if self.live is None:
            return None
        r = self.get(self.live)
        return {"id": r["id"], "command": r["command"], "args": r["args"], "started_at": r["started_at"],
                "progress": r["progress"], "cancel_requested": False, "pause_requested": False, "session_id": r["session_id"]}

    def start(self, command, options=None):
        argv = build_argv(command, options, self.commands)
        if self.live is not None:
            raise Busy(f"{self.get(self.live)['command']} is running (run {self.live})", run_id=self.live)
        s = Store(self.cfg.db_path)
        try:
            paused = s.paused_session() if command == "bid" else None  # as the real manager: a Bid start resumes
            self.live = s.start_run(command, argv, session_id=paused["session_id"] if paused else None)
        finally:
            s.close()
        return self.get(self.live)

    def cancel(self, run_id, delete=False):
        if run_id != self.live:
            raise NotRunning(f"run {run_id} is not running")
        self.cancelled.append(run_id)
        if delete:
            self.deleted.append(run_id)

    def pause(self, run_id):
        if run_id != self.live:
            raise NotRunning(f"run {run_id} is not running")
        self.paused.append(run_id)

    def end(self, status: str) -> None:
        """The live run ends (the real manager records this when the process exits)."""
        s = Store(self.cfg.db_path)
        try:
            s.finish_run(self.live, status, exit_code=1)
        finally:
            s.close()
        self.live = None

    def finish_review(self, run_id):
        cur = self.review_waiting()
        if cur is None or run_id != cur["id"]:
            raise NotRunning(f"run {run_id} is not waiting at the review")
        path = self.cfg.runs_dir / f"{run_id}.finish"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("finish\n", encoding="utf-8")
        self.finished.append(run_id)

    def progress_is(self, progress: dict) -> None:
        """What the live run last reported (the real manager reads it from the run's log)."""
        s = Store(self.cfg.db_path)
        try:
            s.update_run_progress(self.live, progress)
        finally:
            s.close()


def _client(cfg, jobs=None):
    jobs = jobs or FakeJobs(cfg)
    return TestClient(create_app(cfg, jobs=jobs)), jobs


def _run(cfg, command, args=None, status="ok", summary=None, error=None, progress=None, lines=()):
    """A finished run with its log file, the way the job manager leaves one."""
    s = Store(cfg.db_path)
    try:
        rid = s.start_run(command, args or [command])
        log = cfg.runs_dir / f"{rid}.log"
        log.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        s.set_run_log(rid, log)
        if progress:
            s.update_run_progress(rid, progress)
        s.finish_run(rid, status, exit_code=0 if status == "ok" else 1, summary=summary, error=error)
    finally:
        s.close()
    return rid


def test_every_page_renders_on_an_empty_database(cfg):
    client, _ = _client(cfg)
    r = client.get("/")
    assert r.status_code == 200
    assert "Start Bid mode" in r.text and "Start Learn mode" in r.text  # the two things the page offers when nothing runs
    assert "Sign-in not checked" in r.text and "Start a single step" not in r.text  # the steps live on the Runs page
    assert "Nothing has run today" in r.text and "TESTING: no bids are placed" in r.text
    assert "dashboard.js" in r.text and "gsap.min.js" in r.text
    stamp = re.search(r'/static/bidbarn\.css\?v=([0-9a-f]+)"', r.text).group(1)  # static files carry a version stamp
    assert f"icons.svg?v={stamp}#i-" in r.text and f"dashboard.js?v={stamp}" in r.text
    for path in ("/runs", "/insights", "/config", "/review", "/list"):
        assert client.get(path).status_code == 200, path
    assert "No runs yet" in client.get("/runs").text
    assert "No decisions recorded yet" in client.get("/insights").text
    assert client.get("/runs/1").status_code == 404 and client.get("/runs?command=nope").status_code == 404
    for name in ("bidbarn.css", "dashboard.js", "review.js", "icons.svg", "carbarn-logo.svg", "favicon.svg", "vendor/gsap.min.js",
                 "vendor/fonts/manrope-latin-wght-normal.woff2"):
        assert client.get(f"/static/{name}").status_code == 200, name


def test_status_says_where_the_day_stands(cfg):
    client, _ = _client(cfg)
    s = client.get("/api/status").json()
    assert s["lock"] == {"testing": True, "dry_run": True, "dry": True, "label": "TESTING: no bids are placed",
                         "reason": s["lock"]["reason"]} and "testing lock" in s["lock"]["reason"]
    assert s["signin"]["known"] is False and s["current"] is None and s["profile_lock"] is None
    assert s["rules"]["snapshot"] == "rules_test.json" and s["rules"]["guides"] == 1 and s["rules"]["stale"] is False
    assert "codes" not in s["rules"]
    assert s["counts"] == {"bid": 0, "review": 0, "approved": 0, "placed": 0, "to_review": 0, "skipped_today": 0, "seen_today": 0}
    assert [x["key"] for x in s["steps"]] == ["signin", "guides", "scan", "review", "place"]
    assert [x["key"] for x in s["steps"] if x["next"]] == ["signin"]

    # Signed in, guides fresh: the scan is next. Lots to review do not jump the queue before a scan of today.
    _run(cfg, "check-login", summary={"nichibo": True, "carbarn": True})
    store = Store(cfg.db_path)
    _seed(store, "A", "GDH206", "4", 90_000)
    _seed(store, "B", "GDH206", "4", 60_000, status="SKIP")
    store.close()
    s = client.get("/api/status").json()
    assert s["signin"]["ok"] and s["signin"]["fresh"] and s["counts"]["to_review"] == 1 and s["counts"]["skipped_today"] == 1
    nxt = next(x for x in s["steps"] if x["next"])
    assert nxt["key"] == "scan" and nxt["command"] == "scan" and nxt["detail"] == "No scan yet today."

    # After a scan, the review is next; it is a link, not a command.
    _run(cfg, "scan", ["scan", "--headless"], summary={"guides": 1, "cards": 7, "review": 1})
    nxt = next(x for x in client.get("/api/status").json()["steps"] if x["next"])
    assert nxt["key"] == "review" and nxt["href"] == "/review" and nxt["command"] is None and nxt["label"] == "Review 1 lot"

    # A run that stopped on the login page outranks the older check that said "signed in".
    _run(cfg, "scan", ["scan"], status="failed", error="Nichibo redirected to the login page")
    s = client.get("/api/status").json()
    assert s["signin"]["ok"] is False and "redirected to the login page" in s["signin"]["doubt"]
    nxt = next(x for x in s["steps"] if x["next"])
    assert nxt["key"] == "signin" and nxt["command"] == "login"

    # Signed out of one site: the sign-in window is next.
    _run(cfg, "check-login", summary={"nichibo": True, "carbarn": False})
    nxt = next(x for x in client.get("/api/status").json()["steps"] if x["next"])
    assert nxt["command"] == "login" and "the Carbarn dashboard" in nxt["detail"]


def test_a_stale_snapshot_asks_for_the_guides(cfg):
    snap = next(cfg.snapshot_dir.glob("rules_*.json"))
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["fetched_at"] = "2026-01-01T00:00:00+00:00"
    snap.write_text(json.dumps(data), encoding="utf-8")
    _run(cfg, "check-login", summary={"nichibo": True, "carbarn": True})
    client, _ = _client(cfg)
    s = client.get("/api/status").json()
    assert s["rules"]["stale"] is True
    assert [x["command"] for x in s["steps"] if x["next"]] == ["fetch-rules"]
    assert "Guides stale" in client.get("/").text and "Fetch the guides" in client.get("/").text  # the chip carries its repair


def test_starting_and_stopping_through_the_api(cfg):
    client, jobs = _client(cfg)
    assert [c["name"] for c in client.get("/api/commands").json()] == list(COMMANDS)

    r = client.post("/api/runs", json={"command": "scan", "options": {"model": ["gdh206"], "limit": 3}})
    assert r.status_code == 201 and r.json()["status"] == "running"
    assert r.json()["args"] == ["scan", "-m", "GDH206", "--headless", "--limit", "3"]
    rid = r.json()["id"]

    busy = client.post("/api/runs", json={"command": "replay"})
    assert busy.status_code == 409 and busy.json() == {"detail": f"scan is running (run {rid})", "run_id": rid}

    for body in ({"command": "watch"}, {"command": "place", "options": {"live": True}},
                 {"command": "bid", "options": {"live": True}}, {"command": "bid", "options": {"skip_scan": True}},
                 {"command": "bid", "options": {"no_place": True}}, {"command": "learn", "options": {"har": "x.har"}},
                 {"command": "learn", "options": {"save": True}},
                 {"command": "scan", "options": {"model": ["--live"]}}, {"command": "scan", "options": {"limit": "3"}},
                 {"command": "scan", "options": {"shell": "x"}}, {"options": {}}):
        assert client.post("/api/runs", json=body).status_code == 400, body
    assert client.post("/api/runs", content="command=scan", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 422

    assert client.post("/api/runs/999/cancel").status_code == 404
    assert client.post(f"/api/runs/{rid}/cancel").status_code == 202 and jobs.cancelled == [rid]
    done = _run(cfg, "replay")
    assert client.post(f"/api/runs/{done}/cancel").status_code == 409

    # The overview shows the run that is going, first and open.
    page = client.get("/").text
    assert f'data-run-id="{rid}"' in page and 'data-status="running"' in page and "Watch the run" in page
    assert "Scanning Nichibo" in page and "One run at a time" in page
    assert "No scan yet today" not in page  # what is true of the step at rest is not said while it runs
    assert "Start Bid mode" not in page and "Start a single step" in client.get("/runs").text  # the modes step aside while a run is going


def test_the_page_cannot_ask_for_a_live_placement(cfg):
    client, _ = _client(cfg)
    r = client.post("/api/runs", json={"command": "place", "options": {"headless": True}})
    assert r.status_code == 201 and r.json()["args"] == ["place", "--headless"]
    flag = "--" + "live"
    for page in ("/", "/runs", "/config", f"/runs/{r.json()['id']}"):
        assert flag not in client.get(page).text, page
    assert flag not in json.dumps(client.get("/api/commands").json())
    assert "Live placement cannot be started from the page" in client.get("/config").text


def test_bid_and_learn_start_from_the_page_without_options(cfg):
    client, jobs = _client(cfg)
    r = client.post("/api/runs", json={"command": "bid"})
    assert r.status_code == 201 and r.json()["args"] == ["bid", "--no-browser"] and r.json()["command"] == "bid"
    assert client.post("/api/runs", json={"command": "learn"}).status_code == 409  # one at a time
    s = client.get("/api/status").json()
    assert s["current"]["command"] == "bid" and s["current"]["doing"] == "Running Bid mode" and s["current"]["label"] == "Bid mode"
    assert s["current"]["stage"] is None and s["current"]["review_waiting"] is False
    jobs.live = None
    r = client.post("/api/runs", json={"command": "learn", "options": {}})
    assert r.status_code == 201 and r.json()["args"] == ["learn"]
    assert client.get("/api/status").json()["current"]["doing"] == "Recording your session"
    for path in ("/", "/runs", f"/runs/{r.json()['id']}", "/runs?command=learn", "/runs?command=bid"):
        assert client.get(path).status_code == 200, path


def test_finish_ends_only_a_bid_run_waiting_at_the_review(cfg):
    client, jobs = _client(cfg)
    assert client.post("/finish").status_code == 404  # nothing runs
    assert "Finish review" not in client.get("/review").text

    scan = client.post("/api/runs", json={"command": "scan"}).json()["id"]
    jobs.progress_is({"stage": "review", "pending": 2, "approved": 1, "waiting": True})  # a scan never waits there
    assert client.post("/finish").status_code == 404 and "Finish review" not in client.get("/review").text
    jobs.live = None

    bid = client.post("/api/runs", json={"command": "bid"}).json()["id"]
    jobs.progress_is({"stage": "scan", "guides": 3, "guide": 1})
    assert client.post("/finish").status_code == 404 and "Finish review" not in client.get("/review").text
    assert client.get("/api/status").json()["current"]["review_waiting"] is False

    jobs.progress_is({"stage": "review", "step": 4, "steps": 5})  # the step boundary: waiting until told otherwise
    s = client.get("/api/status").json()
    assert s["current"]["stage"] == "review" and s["current"]["review_waiting"] is True
    for path in ("/review", "/list", "/", "/runs", f"/runs/{bid}"):
        assert "Finish review" in client.get(path).text, path
    r = client.post("/finish")
    assert r.status_code == 200 and f"run {bid}" in r.text and f'href="/runs/{bid}"' in r.text
    assert jobs.finished == [bid] and (cfg.runs_dir / f"{bid}.finish").read_text(encoding="utf-8") == "finish\n"

    jobs.progress_is({"stage": "review", "pending": 2, "approved": 1, "waiting": False})  # the run has moved on
    assert client.post("/finish").status_code == 404 and "Finish review" not in client.get("/review").text
    jobs.progress_is({"stage": "place", "i": 0, "total": 1, "dry_run": True})
    assert client.post("/finish").status_code == 404
    store = Store(cfg.db_path)
    assert [r["command"] for r in store.list_runs()] == ["bid", "scan"]  # Finish started nothing and decided nothing
    store.close()


def test_status_marks_the_step_a_bid_run_is_at(cfg):
    client, jobs = _client(cfg)
    client.post("/api/runs", json={"command": "bid"})

    def running():
        steps = client.get("/api/status").json()["steps"]
        return [(x["key"], x["detail"]) for x in steps if x["state"] == "running"]

    assert running() == []  # nothing reported yet
    for stage, key in (("signin", "signin"), ("guides", "guides"), ("scan", "scan"), ("judge", "scan"), ("place", "place")):
        jobs.progress_is({"stage": stage})
        assert running() == [(key, "Running now.")], stage
    jobs.progress_is({"stage": "review", "pending": 3, "approved": 0, "waiting": True})
    assert running() == [("review", "Waiting for your review.")]
    review = next(x for x in client.get("/api/status").json()["steps"] if x["key"] == "review")
    assert review["href"] == "/review" and review["next"] is True
    jobs.progress_is({"stage": "review", "pending": 3, "approved": 0, "waiting": False})
    assert running() == [("review", "Running now.")]

    # A finished Bid run is a sign-in check too: its first step was one.
    jobs.live = None
    _run(cfg, "bid", ["bid", "--no-browser"], summary={"steps": 5, "nichibo": True, "carbarn": True, "guides": 46, "approved": 0,
                                                       "dry_run": True, "lines": []})
    s = client.get("/api/status").json()
    assert s["signin"]["known"] and s["signin"]["ok"] and s["signin"]["fresh"] and s["current"] is None


def test_the_runs_page_filters_steps_apart_from_the_modes(cfg):
    learn = _run(cfg, "learn", ["learn"], summary={"counts": {"lots": 2, "agree": 1, "disagree": 0}})
    bid = _run(cfg, "bid", ["bid", "--no-browser"], summary={"approved": 1, "dry_run": True, "lines": ["x"]})
    scan = _run(cfg, "scan", ["scan", "--headless"], summary={"guides": 1})
    client, _ = _client(cfg)
    steps = client.get("/runs?command=steps").text
    assert f'href="/runs/{scan}"' in steps and f'href="/runs/{learn}"' not in steps and f'href="/runs/{bid}"' not in steps
    only = client.get("/runs?command=learn").text
    assert f'href="/runs/{learn}"' in only and f'href="/runs/{scan}"' not in only
    assert client.get("/runs?command=bid").status_code == 200 and client.get("/runs?command=step").status_code == 404
    for rid in (learn, bid, scan):
        assert client.get(f"/runs/{rid}").status_code == 200


def test_the_review_inside_bot_bid_starts_nothing(cfg):
    client = TestClient(create_app(cfg, finish_event=threading.Event(), jobs=DisabledJobManager(cfg)))
    r = client.post("/api/runs", json={"command": "replay"})
    assert r.status_code == 409 and "started by bot bid" in r.json()["detail"]
    assert client.get("/").status_code == 200 and "Finish review" in client.get("/review").text


def test_the_log_is_read_without_the_progress_lines(cfg):
    lines = ["10:00:00 INFO == GDH206 Toyota Hiace: years 2014-2020", 'PROGRESS {"stage":"scan","guides":1,"guide":1}',
             "10:00:01 INFO    7 cards", "10:00:02 WARNING    slow page", 'SUMMARY {"guides":1}', "done"]
    rid = _run(cfg, "scan", summary={"guides": 1, "cards": 7, "review": 2, "errors": ["x"]},
               progress={"stage": "scan", "guides": 1, "guide": 1, "codes": ["GDH206"], "marks": "f"}, lines=lines)
    client, _ = _client(cfg)
    text = [ln for ln in lines if not ln.startswith(("PROGRESS", "SUMMARY"))]

    d = client.get(f"/api/runs/{rid}/log").json()
    assert d["lines"] == text and d["next"] == 4 and d["done"] is True and d["status"] == "ok"
    d = client.get(f"/api/runs/{rid}/log?after=2&limit=1").json()
    assert d["lines"] == [text[2]] and d["next"] == 3 and d["done"] is False
    assert client.get("/api/runs/999/log").status_code == 404

    def events(headers=None, query=""):
        with client.stream("GET", f"/api/runs/{rid}/events{query}", headers=headers or {}) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            body = "".join(r.iter_text())
        out = []
        for block in body.strip().split("\n\n"):
            fields = dict(ln.split(": ", 1) for ln in block.splitlines() if ": " in ln and not ln.startswith(":"))
            if fields:
                out.append((fields.get("event"), fields.get("id"), json.loads(fields["data"])))
        return out

    got = events()
    assert [(e, i, d) for e, i, d in got if e == "log"] == [("log", str(n + 1), ln) for n, ln in enumerate(text)]
    assert [e for e, _, _ in got] == ["log"] * 4 + ["progress", "done"]
    assert got[-2][2]["marks"] == "f"
    assert got[-1][2] == {"status": "ok", "exit_code": 0, "summary": {"guides": 1, "cards": 7, "review": 2, "errors": ["x"]}, "error": None}
    # A page that reconnects says which line it got last, and gets the rest.
    assert [d for e, _, d in events(headers={"Last-Event-ID": "3"}) if e == "log"] == [text[3]]
    assert [d for e, _, d in events(query="?after=2") if e == "log"] == text[2:]
    assert client.get("/api/runs/999/events").status_code == 404

    r = client.get(f"/runs/{rid}/log.txt")
    assert r.status_code == 200 and r.text.splitlines() == lines and "bidbarn-run-" in r.headers["content-disposition"]


def test_a_log_outside_the_runs_folder_is_not_served(cfg, tmp_path):
    secret = tmp_path / "elsewhere.txt"
    secret.write_text("not a log", encoding="utf-8")
    rid = _run(cfg, "replay", lines=["ok"])
    s = Store(cfg.db_path)
    s.set_run_log(rid, secret)
    s.close()
    client, _ = _client(cfg)
    assert client.get(f"/runs/{rid}/log.txt").status_code == 404


def test_run_pages_say_how_a_run_ended(cfg):
    ok = _run(cfg, "scan", ["scan", "-m", "GDH206", "--headless"], summary={"guides": 46, "cards": 1200, "review": 3, "bid": 0,
                                                                          "errors": ["GDH206: search failed"], "errors_total": 1},
              progress={"stage": "scan", "guides": 2, "guide": 2, "codes": ["GDH206", "ANH20W"], "marks": "fx"}, lines=["a"])
    failed = _run(cfg, "scan", status="failed", error="Nichibo redirected to the login page", lines=["STOPPED: x"])
    stopped = _run(cfg, "judge", ["judge", "--pending"], status="stopped")
    client, _ = _client(cfg)

    page = client.get(f"/runs/{ok}").text
    assert "Scan Nichibo" in page and "Outcome" in page
    assert '<span class="page-sub">GDH206</span>' in page and "bot scan -m GDH206 --headless" in page  # words first, the command line small
    assert re.search(r"<dt>Cards read</dt><dd>1,200</dd>", page) and re.search(r"<dt>Errors</dt><dd>1</dd>", page)
    assert "GDH206: search failed" in page and "Review the lots" in page
    assert '"marks": "fx"' in page and 'data-status="ok"' in page and "data-stop" not in page

    page = client.get(f"/runs/{failed}").text
    assert "The run stopped with an error." in page and "Nichibo redirected to the login page" in page and "Sign in from the" in page
    assert "The run was stopped." in client.get(f"/runs/{stopped}").text

    listing = client.get("/runs").text
    assert [int(x) for x in re.findall(r'<td class="num"><a href="/runs/(\d+)">', listing)] == [stopped, failed, ok]
    assert "Stopped from the dashboard" in listing
    only = client.get("/runs?command=judge").text
    assert re.findall(r'<td class="num"><a href="/runs/(\d+)">', only) == [str(stopped)] and "Judge pending lots only" in only


def test_the_list_tells_placed_from_not_placed(cfg):
    store = Store(cfg.db_path)
    a, b = _seed(store, "A", "GDH206", "4", 90_000), _seed(store, "B", "GDH206", "4", 60_000)
    store.close()
    client, _ = _client(cfg)
    for pid in (a, b):
        assert client.post(f"/p/{pid}/decide", data={"action": "approve", "amount": "1000", "via": "triage"},
                           headers={"Accept": "application/json"}).status_code == 200
    s = Store(cfg.db_path)
    s.conn.execute("UPDATE proposals SET placed_at='2026-09-29T00:00:00+00:00' WHERE id=?", (a,))
    s.conn.commit()
    s.close()
    ids = lambda url: sorted(int(x) for x in set(re.findall(r'href="/p/(\d+)"', client.get(url).text)))
    assert ids("/list?decision=approved") == [a, b]
    assert ids("/list?decision=approved&placed=1") == [a] and ids("/list?decision=approved&placed=0") == [b]
    assert "placed only" in client.get("/list?decision=approved&placed=1").text
    page = client.get("/list?decision=approved").text
    assert "2020 Toyota Hiace" in page and " | " not in page.split("<tbody>")[1]  # the lot by its facts, not the stored line


def test_options_are_shown_in_words():
    from auction_bot.dashboard.routes import options_words
    assert options_words({"command": "scan", "args": ["scan", "--headless"]}) == ""
    assert options_words({"command": "scan", "args": ["scan", "-m", "GDH206", "-m", "ANH20W", "--headless", "--limit", "3"]}) == \
        "GDH206, ANH20W, at most 3 lots per model"
    assert options_words({"command": "scan", "args": ["scan", "--no-headless", "--rescan", "--fresh", "--limit", "1"]}) == \
        "browser window shown, known lots again, cache ignored, at most 1 lot per model"
    assert options_words({"command": "judge", "args": ["judge", "--pending", "--limit", "5"]}) == "at most 5 lots"
    assert options_words({"command": "replay", "args": ["replay"]}) == "report only"
    assert options_words({"command": "replay", "args": ["replay", "-m", "GC27", "--apply"]}) == "GC27, new outcomes stored"
    assert options_words({"command": "insights", "args": ["insights", "--days", "7", "--min-support", "2"]}) == \
        "the last 7 days, 2 rejections per rule"
    assert options_words({"command": "login", "args": ["login", "--wait"]}) == "" and options_words({"command": "place"}) == ""
    assert options_words({"command": "bid", "args": ["bid", "--no-browser"]}) == ""
    assert options_words({"command": "bid", "args": ["bid", "--headed", "--no-browser"]}) == "browser window shown"
    assert options_words({"command": "learn", "args": ["learn"]}) == ""


def test_summary_pairs_read_in_order_and_leave_lists_out():
    run = {"command": "scan", "summary": {"review": 2, "guides": 46, "cards": 1200, "errors": ["a", "b"], "odd": 1}}
    assert summary_pairs(run) == [("Guides", "46"), ("Cards read", "1,200"), ("For review", "2"), ("Errors", "2")]
    assert summary_pairs({"command": "check-login", "summary": {"nichibo": True, "carbarn": False}}) == \
        [("Nichibo", "signed in"), ("Carbarn dashboard", "signed out")]
    assert summary_pairs({"command": "place", "summary": {"dry_run": True, "lines": ["x", "y"]}}) == [("Bids", "2"), ("Dry run", "yes")]
    assert summary_pairs({"command": "scan", "summary": None}) == []
    bid = {"command": "bid", "summary": {"steps": ["a", "b"], "nichibo": True, "carbarn": True, "guides": 46, "cards": 1200,
                                         "review": 3, "bid": 0, "skip": 40, "reused": 9, "pending": 1, "model_calls": 0,
                                         "errors": ["x"], "errors_total": 1, "approved": 2, "dry_run": True, "lines": ["p", "q"]}}
    assert summary_pairs(bid) == [("Bids", "2"), ("Guides", "46"), ("Cards read", "1,200"), ("For review", "3"), ("Bid", "0"),
                                  ("Skipped", "40"), ("Reused", "9"), ("Images pending", "1"), ("Model calls", "0"),
                                  ("Approved", "2"), ("Nichibo", "signed in"), ("Carbarn dashboard", "signed in"),
                                  ("Dry run", "yes"), ("Errors", "1")]
    learn = {"command": "learn", "summary": {"session": "s", "counts": {"lots": 4, "opened": 3, "carted": 1, "bids": 2, "agree": 1,
                                                                          "disagree": 1, "searches": 2, "notes": 1, "candidates": 2,
                                                                          "actions_saved": 5, "screenshots": 9}, "lots": [{}] * 4}}
    assert summary_pairs(learn) == [("Lots", "4"), ("Opened", "3"), ("Your bids", "2"), ("Agree", "1"), ("Disagree", "1"),
                                    ("Candidate rules", "2"), ("Notes", "1"), ("Kept in the store", "5")]
    assert summary_pairs({"command": "learn", "summary": {"counts": "odd"}}) == [] and summary_pairs({"command": "learn"}) == []


def test_entry_pairs_keep_a_few_figures_per_command():
    scan = {"command": "scan", "summary": {"guides": 46, "cards": 1200, "review": 3, "skip": 40, "errors": ["a"], "reused": 2}}
    assert entry_pairs(scan) == [("For review", "3"), ("Skipped", "40"), ("Errors", "1")]
    assert entry_pairs({"command": "scan", "summary": {"guides": 46, "cards": 1200}}) == []  # nothing on the list: nothing shown
    assert entry_pairs({"command": "fetch-rules", "summary": {"guides": 46, "snapshot": "rules_x.json"}}) == [("Guides", "46")]
    assert entry_pairs({"command": "judge", "summary": {"checked": 5, "judged": 4, "unavailable": 1}}) == [("Judged", "4"), ("Model unavailable", "1")]
    assert entry_pairs({"command": "replay", "summary": {"checked": 9, "changed": 2, "applied": 2}}) == [("Changed", "2"), ("Stored", "2")]
    assert entry_pairs({"command": "place", "summary": {"dry_run": True, "lines": ["x"]}}) == [("Bids", "1"), ("Dry run", "yes")]
    assert entry_pairs({"command": "check-login", "summary": {"nichibo": True, "carbarn": False}}) == \
        [("Nichibo", "signed in"), ("Carbarn dashboard", "signed out")]
    assert entry_pairs({"command": "login", "summary": {"nichibo": True, "carbarn": True}}) == \
        [("Nichibo", "signed in"), ("Carbarn dashboard", "signed in")]
    learn = {"command": "learn", "summary": {"counts": {"lots": 4, "opened": 3, "bids": 2, "agree": 1, "disagree": 0, "candidates": 2}}}
    assert entry_pairs(learn) == [("Lots", "4"), ("Agree", "1"), ("Disagree", "0")]
    bid = {"command": "bid", "summary": {"guides": 46, "review": 3, "approved": 2, "lines": ["p", "q"], "dry_run": True}}
    assert entry_pairs(bid) == [("For review", "3"), ("Bids", "2")]
    assert entry_pairs({"command": "insights", "summary": {"lines": 40, "decisions": 12}}) == [] and entry_pairs({"summary": None}) == []


def test_requests_from_other_sites_are_refused(cfg):
    store = Store(cfg.db_path)
    pid = _seed(store, "A", "GDH206", "4", 90_000)
    store.close()
    client, jobs = _client(cfg)
    decide = {"action": "approve", "amount": "1000", "via": "triage"}

    for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                    {"Origin": "http://127.0.0.1.evil.example:8765"}, {"Host": "evil.example"}):
        assert client.post(f"/p/{pid}/decide", data=decide, headers=headers).status_code == 403, headers
        assert client.post("/api/runs", json={"command": "scan"}, headers=headers).status_code == 403, headers
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/review", headers={"Host": "bidbarn.evil.example:8765"}).status_code == 403
    s = Store(cfg.db_path)
    assert s.proposal(pid)["decision"] == "pending" and s.list_runs() == [] and jobs.live is None
    s.close()

    # The test client's own host name is a name for this PC only because the fixture says so.
    cfg.review = {}
    strict = TestClient(create_app(cfg, jobs=FakeJobs(cfg)))
    assert strict.get("/review").status_code == 403 and strict.get("/api/status").status_code == 403
    assert strict.get("/api/status", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert strict.get("/api/status", headers={"Host": "localhost:8765"}).status_code == 200

    # The page's own requests go through: same origin, or a form posted from the page itself.
    for headers in ({"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin", "Host": "127.0.0.1:8765"},
                    {"Origin": "http://localhost:8765", "Host": "localhost:8765"}, {"Sec-Fetch-Site": "none"}):
        assert client.get("/api/status", headers=headers).status_code == 200, headers
    r = client.post(f"/p/{pid}/decide", data=decide, headers={"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin",
                                                              "Host": "127.0.0.1:8765", "Accept": "application/json"})
    assert r.status_code == 200 and r.json()["decision"] == "approved"


def test_the_dashboard_package_decides_nothing():
    """Only the review page's own route may decide a proposal (CLAUDE.md, rule 1)."""
    folder = Path(st.__file__).parent
    for path in folder.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert ".decide(" not in source and "record_placement" not in source and "place_bid" not in source, path.name
        assert "finish_event" not in source, path.name  # the console's own review is not the dashboard's business
    js = (folder.parent / "review" / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "/decide" not in js and "/finish" not in js  # Finish is a form in the header, posted by the page itself


def test_the_day_log_groups_decisions_into_sessions(cfg):
    store = Store(cfg.db_path)
    pids = [_seed(store, ref, "GDH206", "4", km) for ref, km in (("A", 90_000), ("B", 60_000), ("C", 30_000))]
    store.close()
    client, jobs = _client(cfg)
    json_headers = {"Accept": "application/json"}
    assert client.post(f"/p/{pids[0]}/decide", data={"action": "approve", "amount": "1000", "via": "triage"}, headers=json_headers).status_code == 200
    assert client.post(f"/p/{pids[1]}/decide", data={"action": "reject", "reason": "10 seats on the sheet", "via": "triage"}, headers=json_headers).status_code == 200

    s = Store(cfg.db_path)
    log = st.day_log(s, jobs.list())
    assert [(e["kind"], e["count"], e["approved"], e["rejected"]) for e in log["today"]] == [("review", 2, 1, 1)]
    assert [d["ref_no"] for d in log["today"][0]["items"]] == ["B", "A"] and log["earlier"] == [] and log["live"] == []
    s.close()

    # A run in between starts a new session; the running run is listed apart, first.
    import time
    time.sleep(1.1)  # the store's times are whole seconds
    _run(cfg, "replay", summary={"checked": 3, "changed": 0})
    time.sleep(1.1)
    assert client.post(f"/p/{pids[2]}/decide", data={"action": "reject", "reason": "repaint", "via": "triage"}, headers=json_headers).status_code == 200
    live = client.post("/api/runs", json={"command": "insights"}).json()["id"]
    s = Store(cfg.db_path)
    log = st.day_log(s, jobs.list())
    s.close()
    assert [e["run"]["id"] for e in log["live"]] == [live]
    assert [(e["kind"], e.get("count")) for e in log["today"]] == [("review", 1), ("run", None), ("review", 2)]

    page = client.get("/").text
    assert "Reviewed 2 lots" in page and "Reviewed 1 lot<" in page and "1 approved" in page and "10 seats on the sheet" in page
    assert page.index("Reviewed 1 lot<") < page.index("Replay the rules") < page.index("Reviewed 2 lots")


def test_insights_and_settings_pages(cfg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "not-for-the-page-1234")
    cfg.sheet_check = "judge"
    cfg.judge = {"inline": True, "providers": [
        {"name": "gemini", "base_url": "https://example.invalid/", "model": "flash-lite", "api_key_env": "GEMINI_API_KEY", "rpm": 10, "rpd": 400},
        {"name": "groq", "base_url": "https://example.invalid/", "model": "oss", "api_key_env": "NO_SUCH_KEY_HERE", "rpm": 20, "rpd": 800,
         "vision": False}]}
    store = Store(cfg.db_path)
    pids = [_seed(store, ref, "GDH206", "4", km) for ref, km in (("A", 90_000), ("B", 60_000))]
    store.record_model_call("gemini", tokens=100)
    store.close()
    client, _ = _client(cfg)
    for pid in pids:
        assert client.post(f"/p/{pid}/decide", data={"action": "reject", "reason": "10 seats on the sheet", "via": "detail"},
                           follow_redirects=False).status_code == 303

    page = client.get("/insights").text
    assert "from 2 decisions" in page and "GDH206" in page and "10 seats on the sheet" in page and "Rejections by theme" in page
    assert client.get("/insights?days=7&min_support=1").status_code == 200
    assert client.get("/insights?days=-3&min_support=9999").status_code == 200

    page = client.get("/config").text
    assert "not-for-the-page-1234" not in page and "GEMINI_API_KEY" in page and "Missing" in page and "Text only" in page
    assert "Testing lock (project_status)" in page and str(cfg.runs_dir) in page
    s = client.get("/api/status").json()
    assert "not-for-the-page-1234" not in json.dumps(s)
    assert s["judge"]["providers"][0] == {"name": "gemini", "model": "flash-lite", "calls": 1, "rpd": 400, "rpm": 10, "vision": True,
                                          "has_key": True, "share": 0.0025, "level": "ok"}
    assert s["judge"]["providers"][1]["has_key"] is False
    assert '<td class="num">1</td><td class="num">10</td><td class="num">400</td>' in page  # calls today, per minute, per day
    assert "Gemini:" not in client.get("/").text  # the quota is a chip on the overview only when it runs short
    store = Store(cfg.db_path)
    store.conn.execute("UPDATE model_calls SET calls=380")
    store.conn.commit()
    store.close()
    assert "Gemini: 380 of 400 calls" in client.get("/").text


# -- pause, resume and the paused session ------------------------------------------------------------

def test_pause_and_resume_from_the_overview(cfg):
    from tests.test_sessions import lot
    client, jobs = _client(cfg)
    assert client.post("/api/runs/1/pause").status_code == 404
    run = client.post("/api/runs", json={"command": "bid"}).json()
    jobs.progress_is({"stage": "scan", "guides": 46, "guide": 21, "code": "GDH206"})
    page = client.get("/").text
    assert "data-pause" in page and f'data-delete-session="{run["id"]}"' in page and "data-stop" in page
    assert client.post(f"/api/runs/{run['id']}/pause").status_code == 202 and jobs.paused == [run["id"]]
    jobs.end("paused")  # what the real manager records once the run stopped at its check point
    s = Store(cfg.db_path)
    try:
        lot(s, "A", run["id"])
    finally:
        s.close()
    page = client.get("/").text
    assert "Bid mode is paused" in page and "guide 21 of 46 (GDH206)" in page and "1 lot found so far waits in the review" in page
    assert f'data-resume="{run["id"]}"' in page and "Start Learn mode" in page and "Start Bid mode" not in page
    assert f'data-delete-session="{run["id"]}"' in page and "Paused at guide 21 of 46" in page  # the card and the day log
    assert client.post("/api/sessions/999/resume").status_code == 409
    again = client.post(f"/api/sessions/{run['id']}/resume")
    assert again.status_code == 201 and again.json()["session_id"] == run["id"] and again.json()["id"] != run["id"]
    assert client.post(f"/api/sessions/{run['id']}/resume").status_code == 409  # running again: nothing is paused


def test_resume_waits_for_the_run_in_the_way(cfg):
    paused = _run(cfg, "bid", status="paused", progress={"stage": "scan", "guide": 2, "guides": 46})
    client, _ = _client(cfg)
    other = client.post("/api/runs", json={"command": "learn"}).json()
    r = client.post(f"/api/sessions/{paused}/resume")
    assert r.status_code == 409 and r.json()["run_id"] == other["id"]


def test_the_run_page_of_a_paused_and_of_a_deleted_session(cfg):
    paused = _run(cfg, "bid", status="paused", progress={"stage": "scan", "guide": 3, "guides": 46})
    deleted = _run(cfg, "bid", status="stopped")
    s = Store(cfg.db_path)
    try:
        s.delete_session(deleted)
    finally:
        s.close()
    client, _ = _client(cfg)
    page = client.get("/").text
    assert "Paused at guide 3 of 46" in page and "Session deleted" in page
    run_page = client.get(f"/runs/{paused}").text
    assert "The session is paused" in run_page and f'data-resume="{paused}"' in run_page
    assert "This session was deleted" in client.get(f"/runs/{deleted}").text


def test_run_screenshot_link_still_opens_the_lightbox():
    from pathlib import Path
    import auction_bot.review as review
    base = Path(review.__file__).parent
    html = (base / "templates" / "run.html").read_text(encoding="utf-8")
    js = (base / "static" / "review.js").read_text(encoding="utf-8")
    assert "data-sheet-zoom" in html and '"[data-zoom], [data-sheet-zoom]"' in js


def test_a_finished_scan_now_run_reads_like_a_scan_and_names_the_day_pages(cfg):
    rid = _run(cfg, "scan-now", ["scan-now"], summary={"guides": 3, "cards": 40, "opened": 12, "day_pages": 3, "review": 2, "skip": 8},
               progress={"stage": "scan", "guides": 3, "guide": 3, "codes": ["GDH206"], "marks": "ffe"})
    client, _ = _client(cfg)
    page = client.get(f"/runs/{rid}").text
    assert "Scan now" in page and "Review the lots" in page
    assert re.search(r"<dt>Cards read</dt><dd>40</dd>", page)
    assert re.search(r"<dt>Lot pages read for the auction day</dt><dd>3</dd>", page)
    assert [k for k, _ in summary_pairs({"command": "scan-now", "summary": {"opened": 1, "day_pages": 3, "pages": 2}})] ==         ["Lots opened", "Lot pages read for the auction day", "Pages opened"]
    from auction_bot.dashboard import status as st
    assert (st.COMMAND_ICONS["scan-now"], st.COMMAND_SHORT["scan-now"], st.DOING["scan-now"]) == ("scan-search", "Scan now", "Scanning Nichibo")


def test_a_last_event_id_that_is_not_a_number_starts_from_the_top(cfg):
    rid = _run(cfg, "scan", summary={"guides": 1}, lines=["one", "two"])
    client, _ = _client(cfg)
    with client.stream("GET", f"/api/runs/{rid}/events", headers={"Last-Event-ID": "²".encode("latin-1")}) as r:
        assert r.status_code == 200
        assert "one" in "".join(r.iter_text())


def test_dashboard_js_scan_counters_show_the_day_pages():
    src = (Path(__file__).resolve().parent.parent / "src" / "auction_bot" / "review" / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert '["day_pages", p.day_pages, "Lot pages read for the auction day"]' in src


def test_a_finished_scan_now_run_marks_the_scanned_step_done(cfg):
    from auction_bot.dashboard.status import gather_status
    _run(cfg, "scan-now", summary={"review": 1})
    s = Store(cfg.db_path)
    try:
        st = gather_status(cfg, s, FakeJobs(cfg))
    finally:
        s.close()
    assert next(x for x in st["steps"] if x["key"] == "scan")["state"] == "done"


def test_a_running_scan_now_does_not_unmark_an_earlier_scan(cfg):
    from datetime import datetime, timedelta, timezone
    from auction_bot.dashboard.status import day_steps
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    ok = {"id": 1, "command": "scan", "status": "ok", "started_at": hour_ago, "finished_at": hour_ago, "summary": {}, "error": None}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    running = {"id": 2, "command": "scan-now", "status": "running", "started_at": now, "finished_at": None, "summary": {}, "error": None}
    base = {"signin": {"known": True, "ok": True, "fresh": True, "checked_at": now, "nichibo": True, "carbarn": True, "doubt": ""},
            "rules": {"snapshot": "x", "stale": False, "fetched_at": now, "active_guides": 1, "max_age_hours": 24},
            "counts": {"to_review": 0, "approved": 0}, "lock": {"label": "DRY RUN", "dry": True}}
    idle = day_steps({**base, "current": None, "last_runs": {"scan": ok, "scan-now": running}}, 12)
    assert next(x for x in idle if x["key"] == "scan")["state"] == "done"
    going = day_steps({**base, "current": {"command": "scan-now", "progress": {}}, "last_runs": {"scan": ok, "scan-now": running}}, 12)
    step = next(x for x in going if x["key"] == "scan")
    assert step["state"] == "running" and step["next"]
