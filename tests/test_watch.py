"""Watch mode: the recorder's file format and the offline timeline built from it (no browser)."""
import json
from datetime import datetime
from pathlib import Path

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
    assert "2 lots opened, 1 bids" in text and "R1 30 s BID 1240K (ok)" in text and "sheet, 2 photo hovers, scrolled 60%" in text
    assert "why: skip: dump body" in text and "bot: never seen" in text and "never opened: R9" in text
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
