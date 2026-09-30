"""Watch mode: the recorder's file format and the offline timeline built from it (no browser), and the
recording loop against a stand-in browser: it reports to the dashboard and stops at its Stop."""
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from auction_bot import lock, progress
from auction_bot.config import Config
from auction_bot.watch import INIT_SCRIPT, Recorder, load_events, ref_from_url, render_session, search_chassis, session_dir, timeline

N = "https://autosearch.nichibojapan.com"


def test_url_helpers():
    assert ref_from_url(N + "/auction/abc+def%3D") == "abc+def="  # base64 refs keep their plus signs
    assert ref_from_url(N + "/auction/cart/x") is None and ref_from_url(N + "/auction/bid?v=x") is None
    assert ref_from_url(N + "/auction/today") is None  # Nichibo's "today" page is not a lot
    assert ref_from_url(N + "/auction?Chassis=GDH206") is None and ref_from_url("https://other.example/auction/x") is None
    assert search_chassis(N + "/auction?Make=All&Chassis=gdh206&page=1") == "GDH206"
    assert search_chassis(N + "/auction/x") is None


def test_init_script_only_observes():
    for hook in ("addEventListener('click'", "'scroll'", "'mouseover'", "'change'", "bidbarn-reason", "__bidbarn_event", "nichibojapan"):
        assert hook in INIT_SCRIPT
    for forbidden in (".click()", ".submit()", "btnPlaceBid').click", "fetch(", "XMLHttpRequest"):
        assert forbidden not in INIT_SCRIPT


def test_recorder_writes_jsonl_and_queues_screenshots(tmp_path):
    rec = Recorder(tmp_path / "s1")
    rec.record("session", started="x")
    rec.handle_binding(None, json.dumps({"type": "click", "url": N + "/auction/R1", "el": {"tag": "a"}, "t_page": 1}))
    rec.handle_binding(None, "not json")

    class _Frame:
        url = N + "/auction/R1"

    class _Page:
        main_frame = _Frame()
        url = _Frame.url
        shots = []

        def is_closed(self):
            return False

        def screenshot(self, path, full_page=False):
            self.shots.append(path)
            Path(path).write_bytes(b"png")

    page = _Page()
    rec.on_nav(page, page.main_frame)
    rec.flush_screenshots()  # not due yet (1.2 s after the navigation)
    assert page.shots == []
    rec._queue = [(page, "lot", 0.0)]
    rec.flush_screenshots()
    assert len(page.shots) == 1 and page.shots[0].endswith("0001_lot.png")
    rec.close()
    events = load_events(tmp_path / "s1")
    assert [e["type"] for e in events] == ["session", "click", "nav", "screenshot"]
    assert events[2]["ref"] == "R1" and events[3]["file"] == "0001_lot.png"


class _Page:
    """A tab of the stand-in browser: it remembers its handlers and calls back on every wait."""

    def __init__(self, url, on_wait):
        self.url = url
        self.main_frame = SimpleNamespace(url=url)
        self.handlers = {}
        self.closed = False
        self._on_wait = on_wait
        self.visited = []

    def on(self, name, fn):
        self.handlers.setdefault(name, []).append(fn)

    def evaluate(self, script):
        pass

    def goto(self, url):
        self.visited.append(url)

    def bring_to_front(self):
        pass

    def is_closed(self):
        return self.closed

    def wait_for_timeout(self, ms):
        self._on_wait(self)

    def screenshot(self, path, full_page=False):
        Path(path).write_bytes(b"png")


class _Context:
    def __init__(self, first_url, on_wait):
        self.handlers = {}
        self.closed = False
        self.launched = None
        self._on_wait = on_wait
        self.pages = [_Page(first_url, on_wait)]

    def expose_binding(self, name, fn):
        self.binding = (name, fn)

    def add_init_script(self, script):
        self.script = script

    def on(self, name, fn):
        self.handlers.setdefault(name, []).append(fn)

    def new_page(self):
        page = _Page("about:blank", self._on_wait)
        self.pages.append(page)
        for fn in self.handlers.get("page", []):
            fn(page)
        return page

    def close(self):
        self.closed = True
        for fn in self.handlers.get("close", []):
            fn()


def _stand_in_browser(monkeypatch, ctx):
    """`sync_playwright()` as `watch._record` imports it, opening `ctx` instead of Chromium."""
    pw_mod = pytest.importorskip("playwright.sync_api")

    class PW:
        def __init__(self):
            self.chromium = SimpleNamespace(launch_persistent_context=self._launch)

        def _launch(self, **kw):
            ctx.launched = kw
            return ctx

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(pw_mod, "sync_playwright", lambda: PW())


def _cfg(tmp_path) -> Config:
    cfg = Config()
    cfg.db_path = tmp_path / "data" / "bot.sqlite"
    cfg.profile_dir = tmp_path / "data" / "profile"
    cfg.nichibo = {"base_url": N}
    cfg.carbarn = {"dashboard_url": "https://dashboard.carbarn.com.au/?view=bidding"}
    return cfg


def test_record_reports_while_recording_and_ends_when_the_window_closes(tmp_path, monkeypatch, capsys):
    from auction_bot import watch as watch_mod
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    monkeypatch.delenv("BIDBARN_CANCEL_FILE", raising=False)
    monkeypatch.setattr(watch_mod, "PROGRESS_SECONDS", 0.0)
    waits = []

    def on_wait(page):
        waits.append(page)
        if len(waits) == 1:  # Jim opens a lot in a new tab, then closes the window
            tab = ctx.new_page()
            tab.url = tab.main_frame.url = N + "/auction/R1"
            for fn in tab.handlers.get("framenavigated", []):
                fn(tab.main_frame)
        elif len(waits) == 2:
            ctx.close()

    ctx = _Context(N + "/auction?Chassis=GDH206", on_wait)  # the profile's last page: a GDH206 search
    _stand_in_browser(monkeypatch, ctx)
    cfg = _cfg(tmp_path)
    lines = []
    capsys.readouterr()
    out = watch_mod.watch(cfg, echo=lines.append)
    assert out.parent == cfg.db_path.parent / "sessions" and ctx.closed and ctx.launched["headless"] is False
    assert str(ctx.launched["user_data_dir"]) == str(cfg.profile_dir) and ctx.binding[0] == "__bidbarn_event"
    assert ctx.pages[0].visited == [N + "/auction"] and ctx.pages[1].visited == ["https://dashboard.carbarn.com.au/?view=bidding"]
    events = load_events(out)
    assert [e["type"] for e in events] == ["nav", "session", "nav", "session_end"]
    assert events[0]["chassis"] == "GDH206" and events[2]["ref"] == "R1"  # the new tab's own navigation was heard
    assert events[-1]["events"] == 3 and lock.holder(cfg) is None and not lock.lock_path(cfg).exists()
    told = [progress.parse(ln)[1] for ln in capsys.readouterr().out.splitlines() if progress.parse(ln)]
    assert told == [{"stage": "learn", "phase": "recording", "session": out.name, "events": 2, "lots": 0, "bids": 0, "searched": ["GDH206"]},
                    {"stage": "learn", "phase": "recording", "session": out.name, "events": 3, "lots": 1, "bids": 0, "searched": ["GDH206"]}]
    assert any("Watching. Two tabs are open" in ln for ln in lines)


def test_record_stops_on_the_cancel_file_and_keeps_the_recording(tmp_path, monkeypatch, capsys):
    from auction_bot import watch as watch_mod
    flag = tmp_path / "4.cancel"
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(flag))
    monkeypatch.setenv("BIDBARN_PROGRESS", "1")
    monkeypatch.setattr(watch_mod, "PROGRESS_SECONDS", 0.0)
    waits = []

    def on_wait(page):
        waits.append(page)
        flag.write_text("stop", encoding="utf-8")  # Stop is clicked on the dashboard

    ctx = _Context("about:blank", on_wait)
    _stand_in_browser(monkeypatch, ctx)
    cfg = _cfg(tmp_path)
    capsys.readouterr()
    with pytest.raises(progress.Cancelled, match="stopped from the dashboard"):
        watch_mod.watch(cfg, echo=lambda s: None)
    assert len(waits) == 1 and ctx.closed  # one look, then the stop; the window was closed for Jim
    assert lock.holder(cfg) is None and not lock.lock_path(cfg).exists()  # the profile is free again
    session = next((cfg.db_path.parent / "sessions").iterdir())
    events = load_events(session)
    assert [e["type"] for e in events] == ["session", "session_end"]  # what was recorded is kept, closed properly
    told = [progress.parse(ln)[1] for ln in capsys.readouterr().out.splitlines() if progress.parse(ln)]
    assert told == [{"stage": "learn", "phase": "recording", "session": session.name, "events": 1, "lots": 0, "bids": 0, "searched": []}]
    assert not any(k == "summary" for k in [progress.parse(ln)[0] for ln in capsys.readouterr().out.splitlines() if progress.parse(ln)])


def test_a_cancel_file_present_at_the_start_stops_before_the_browser_opens(tmp_path, monkeypatch):
    from auction_bot import watch as watch_mod
    flag = tmp_path / "5.cancel"
    flag.write_text("stop", encoding="utf-8")
    monkeypatch.setenv("BIDBARN_CANCEL_FILE", str(flag))
    ctx = _Context("about:blank", lambda page: None)
    _stand_in_browser(monkeypatch, ctx)
    cfg = _cfg(tmp_path)
    with pytest.raises(progress.Cancelled):
        watch_mod.watch(cfg, echo=lambda s: None)
    assert ctx.closed and lock.holder(cfg) is None  # the window opened and was closed again at the first look


def test_session_dir_under_data():
    cfg = Config()
    cfg.db_path = Path("D:/x/data/bot.sqlite")
    assert session_dir(cfg, datetime(2026, 9, 15, 10, 30, 0)) == Path("D:/x/data/sessions/20260915-103000")


def _ev(t, type, **d):
    return {"t": f"2026-09-15T10:{t}", "type": type, **d}


def test_timeline_and_report():
    events = [
        _ev("00:00.000", "session", started="2026-09-15T10:00:00.000"),
        _ev("00:01.000", "nav", url=N + "/auction?Chassis=GDH206", ref=None, chassis="GDH206"),
        _ev("00:05.000", "hover", url=N + "/auction?Chassis=GDH206", el={"tag": "div", "cls": "card-item-listings", "refno": "R9"}),
        _ev("00:06.000", "hover", url=N + "/auction?Chassis=GDH206", el={"tag": "div", "cls": "card-item-listings", "refno": "R1"}),
        _ev("00:10.000", "nav", url=N + "/auction/R1", ref="R1", chassis=None),
        _ev("00:11.000", "screenshot", url=N + "/auction/R1", file="0001_lot.png", tag="lot"),
        _ev("00:12.000", "hover", url=N + "/auction/R1", el={"tag": "img", "id": "auction-sheet-img"}),
        _ev("00:14.000", "hover", url=N + "/auction/R1", el={"tag": "a", "cls": "gallery-demo", "href": "x1"}),
        _ev("00:15.000", "hover", url=N + "/auction/R1", el={"tag": "a", "cls": "gallery-demo", "href": "x2"}),
        _ev("00:16.000", "scroll", url=N + "/auction/R1", y=900, h=3000, vh=900),
        _ev("00:20.000", "click", url=N + "/auction/R1", el={"tag": "input", "id": "bidAmountDisplay"}),
        _ev("00:21.000", "input", url=N + "/auction/R1", el={"tag": "input", "id": "bidAmountDisplay"}, value="1240"),
        _ev("00:22.000", "reason", url=N + "/auction/R1", text="bid: clean sheet, 5 seats"),
        _ev("00:25.000", "bid", url=N + "/auction/bid?v=R1&b=1240000&ba=1240", ref="R1", yen="1240000", thousands="1240",
            status=200, response='{"type":"success"}'),
        _ev("00:40.000", "nav", url=N + "/auction?Chassis=GDH206", ref=None, chassis="GDH206"),
        _ev("00:50.000", "nav", url=N + "/auction/R2", ref="R2", chassis=None),
        _ev("00:53.000", "reason", url=N + "/auction/R2", text="skip: dump body"),
        _ev("00:55.000", "nav", url=N + "/auction?Chassis=GDH206", ref=None, chassis="GDH206"),
        _ev("01:00.000", "session_end", ended="2026-09-15T10:01:00.000"),
    ]
    s = timeline(events, "sess")
    assert [v.ref for v in s.visits] == ["R1", "R2"]
    r1, r2 = s.visits
    assert r1.seconds == 30 and r1.sheet_viewed and r1.gallery_hovers == 2 and r1.scroll_depth == 0.6
    assert r1.inputs == ["input #bidAmountDisplay = 1240"] and r1.reasons == ["bid: clean sheet, 5 seats"]
    assert r1.bid_k == "1240" and r1.bid_status == "ok" and r1.screenshots == ["0001_lot.png"]
    assert r2.seconds == 5 and r2.reasons == ["skip: dump body"] and r2.bid_k is None and not r2.sheet_viewed
    assert s.searches == ["GDH206", "GDH206", "GDH206"] and s.list_seconds == 9 + 10 + 5
    assert dict(s.considered_not_opened) == {"R9": 1}  # R1 was hovered then opened
    text = render_session(s, bot_view=lambda ref: "NEEDS_REVIEW 1,240K, pending" if ref == "R1" else "never seen")
    assert "2 lots opened, 0 cards handled on the list or in the cart, 1 bids" in text and "R1 30 s BID 1240K (ok)" in text and "sheet, 2 photo hovers, scrolled 60%" in text
    assert "why: skip: dump body" in text and "bot: never seen" in text and "nothing else: R9" in text
    assert "median 17.5 s, max 30 s" in text


def test_watch_plumbing_in_a_headless_browser(tmp_path):
    """The injected script, the binding, the reason box and the screenshot queue, against a local copy of a lot page."""
    pw_mod = __import__("pytest").importorskip("playwright.sync_api")
    html = (Path(__file__).parent / "fixtures" / "detail_gdh206v.html").read_text(encoding="utf-8")
    rec = Recorder(tmp_path / "s")
    with pw_mod.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except pw_mod.Error:
            __import__("pytest").skip("chromium not installed")
        ctx = browser.new_context(viewport={"width": 1200, "height": 800})
        ctx.expose_binding("__bidbarn_event", rec.handle_binding)
        ctx.add_init_script(INIT_SCRIPT)

        def serve(route):
            if "nichibojapan.com" in route.request.url and route.request.resource_type == "document":
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
            else:
                route.abort()

        ctx.route("**/*", serve)
        def attach(p):
            p.on("framenavigated", lambda f, p=p: rec.on_nav(p, f))
            rec.on_new_page(p)

        ctx.on("page", attach)  # fires for every new tab, including the first one
        page = ctx.new_page()
        page.goto(N + "/auction/TESTREF")
        # Lots opened in a new tab (target=_blank or window.open) must be recorded too.
        page.evaluate("window.open('" + N + "/auction/NEWTAB1', '_blank')")
        page.evaluate("const a=document.createElement('a');a.href='" + N + "/auction/NEWTAB2';a.target='_blank';document.body.appendChild(a);a.click()")
        page.wait_for_timeout(800)
        page.bring_to_front()
        assert page.locator("#bidbarn-box").count() == 1
        page.locator("#bidbarn-reason").fill("skip: test reason")
        page.locator("#bidbarn-reason").press("Enter")
        assert "saved: skip: test reason" in page.locator("#bidbarn-last").inner_text()
        page.locator("table.table-new td").first.click()
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(300)
        rec._queue = [(page, "lot", 0.0)]
        rec.flush_screenshots()
        ctx.close()
        browser.close()
    rec.close()
    events = load_events(tmp_path / "s")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "nav" and {"reason", "click", "screenshot"} <= set(kinds)
    s = timeline(events, "s")
    trail = [(e["type"], e.get("ref") or e.get("url", "")[-12:], e.get("opened", "")) for e in events]
    assert s.visits[0].ref == "TESTREF" and s.visits[0].reasons == ["skip: test reason"] and s.visits[0].clicks, trail
    assert [v.ref for v in s.visits] == ["TESTREF", "NEWTAB1", "NEWTAB2"]
    assert [e.get("opened") for e in events if e["type"] == "nav"].count("new tab") == 2
    assert (tmp_path / "s" / "0001_lot.png").stat().st_size > 1000


def test_dashboard_events_in_timeline():
    D = "https://dashboard.carbarn.com.au/?view=bidding"
    events = [
        _ev("00:00.000", "session", started="2026-09-15T10:00:00.000"),
        _ev("00:01.000", "nav", url=D, ref=None, chassis=None),
        _ev("00:02.000", "pointer", site="dashboard", url=D, x=10, y=10, el={"tag": "td", "text": "1,412", "row": "10/2021 1,412 1,370", "heading": "Nissan / Serena GC27"}),
        _ev("00:02.500", "hover", site="dashboard", url=D, el={"tag": "td", "text": "1,370", "row": "10/2021 1,412 1,370", "heading": "Nissan / Serena GC27"}),
        _ev("00:04.000", "select", site="dashboard", url=D, text="Dark Color Seats Only"),
        _ev("00:05.000", "click", site="dashboard", url=D, el={"tag": "button", "text": "Default", "heading": "Nissan / Serena GC27"}),
        _ev("00:06.000", "reason", site="dashboard", url=D, text="checking seat rule"),
        _ev("00:10.000", "nav", url=N + "/auction/R1", ref="R1", chassis=None),
        _ev("00:12.000", "pointer", site="nichibo", url=N + "/auction/R1", x=1, y=1, el={"tag": "div"}),
        _ev("00:20.000", "session_end", ended="2026-09-15T10:00:20.000"),
    ]
    s = timeline(events, "sess")
    d = s.dashboard
    assert d.active_seconds == 3 and d.headings["Nissan / Serena GC27"] == 2  # seconds 02, 04, 05; a typed reason is not activity
    assert d.cells["10/2021 1,412 1,370 > 1,412"] == 1 and d.selections == ["Dark Color Seats Only"]
    assert d.clicks == ["button 'Default' [Nissan / Serena GC27]"] and d.reasons == ["checking seat rule"]
    assert [v.ref for v in s.visits] == ["R1"]  # the dashboard is not a lot
    text = render_session(s)
    assert "Carbarn dashboard: about 3 s" in text and "Nissan / Serena GC27 (2)" in text and "highlighted: Dark Color Seats Only" in text


def test_dashboard_page_is_recorded_in_a_headless_browser(tmp_path):
    pw_mod = __import__("pytest").importorskip("playwright.sync_api")
    html = ("<html><body><h2>Nissan / Serena GC27</h2><table><tr><td id='c1'>10/2021</td><td id='c2'>1,412</td></tr></table>"
            "<p id='rule'>Dark Color Seats Only</p></body></html>")
    rec = Recorder(tmp_path / "s")
    with pw_mod.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except pw_mod.Error:
            __import__("pytest").skip("chromium not installed")
        ctx = browser.new_context(viewport={"width": 800, "height": 600})
        ctx.expose_binding("__bidbarn_event", rec.handle_binding)
        ctx.add_init_script(INIT_SCRIPT)
        ctx.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html) if r.request.resource_type == "document" else r.abort())
        page = ctx.new_page()
        page.goto("https://dashboard.carbarn.com.au/?view=bidding")
        assert page.locator("#bidbarn-box").count() == 1 and "dashboard" in page.locator("#bidbarn-box").inner_text()
        box = page.locator("#c2").bounding_box()
        page.mouse.move(box["x"] + 5, box["y"] + 5)
        page.wait_for_timeout(200)
        page.mouse.move(box["x"] + 8, box["y"] + 6)
        page.evaluate("const r=document.createRange(); r.selectNodeContents(document.getElementById('rule')); const s=getSelection(); s.removeAllRanges(); s.addRange(r);")
        page.wait_for_timeout(900)
        ctx.close()
        browser.close()
    rec.close()
    events = load_events(tmp_path / "s")
    kinds = {e["type"] for e in events}
    assert {"pointer", "hover", "select"} <= kinds, kinds
    hov = next(e for e in events if e["type"] == "hover")
    assert hov["site"] == "dashboard" and hov["el"]["text"] == "1,412" and hov["el"]["heading"] == "Nissan / Serena GC27" and "10/2021" in hov["el"]["row"]
    assert next(e for e in events if e["type"] == "select")["text"] == "Dark Color Seats Only"


def test_cart_flow_attributes_amounts_and_comments_to_cards():
    L = N + "/auction?Chassis=GP7"
    C = N + "/auction/cart"
    card = {"tag": "button", "text": "cart", "refno": "S1", "card_ref": "S1", "card": "2020 Honda Shuttle"}
    other = {"tag": "button", "text": "cart", "refno": "S2", "card_ref": "S2", "card": "2021 Honda Shuttle"}
    events = [
        _ev("00:00.000", "session", started="2026-09-15T10:00:00.000"),
        _ev("00:01.000", "nav", url=L, ref=None, chassis="GP7"),
        _ev("00:05.000", "reason", url=L, text="only the grade 4 ones"),  # nothing touched yet: a general note
        _ev("00:10.000", "hover", url=L, el=other),
        _ev("00:12.000", "click", url=L, el=card),  # into the cart
        _ev("00:15.000", "reason", url=L, text="grade 4, 54,000 km"),  # belongs to S1
        _ev("00:20.000", "nav", url=C, ref=None, chassis=None),
        _ev("00:25.000", "input", url=C, el={"tag": "input", "cls": "form-control text-right", "card_ref": "S1", "card": "2020 Honda Shuttle"}, value="640"),
        _ev("00:30.000", "reason", url=C, text="entered 640 because 2020 and 50-60k"),
        _ev("00:40.000", "click", url=C, el=card),  # taken out again
        _ev("00:41.000", "reason", url=C, text="uncarted, just teaching"),
        _ev("03:00.000", "reason", url=C, text="late remark"),  # more than 90 s after the last card touch
        _ev("03:05.000", "bid", url=N + "/auction/bid?v=S2&b=610000&ba=610", ref="S2", yen="610000", thousands="610", status=200, response='{"type":"success"}'),
        _ev("03:10.000", "session_end", ended="2026-09-15T10:03:10.000"),
    ]
    s = timeline(events, "sess")
    assert s.visits == [] and set(s.lots) == {"S1", "S2"}
    s1, s2 = s.lots["S1"], s.lots["S2"]
    assert s1.title == "2020 Honda Shuttle" and s1.cart_clicks == 2 and not s1.carted and s1.amounts == ["640"]
    assert s1.reasons == ["grade 4, 54,000 km", "entered 640 because 2020 and 50-60k", "uncarted, just teaching"]
    assert s2.bid_k == "610" and s2.bid_status == "ok" and not s2.opened
    assert [(n.where, n.text) for n in s.notes] == [("list GP7", "only the grade 4 ones"), ("cart", "late remark")]
    assert dict(s.considered_not_opened) == {}  # S2 was hovered, then bid on
    text = render_session(s, bot_view=lambda ref: "bot says")
    assert "2 cards handled on the list or in the cart, 1 bids" in text and "2020 Honda Shuttle: carted then taken out, typed 640 | bot: bot says" in text
    assert "2021 Honda Shuttle: priced, BID 610K (ok)" in text and "[list GP7] only the grade 4 ones" in text
