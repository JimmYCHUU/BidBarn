"""The desk page: sessions, lanes and rows served beside today's review."""
import re

from fastapi.testclient import TestClient

from auction_bot.review.app import create_app
from auction_bot.store import Store
from auction_bot.pipeline import scan
from auction_bot.review.desk_model import look_items
from auction_bot.review.desk_routes import mini_grid
from tests.test_dashboard_app import _client
from tests.test_judge_review import _judged
from tests.test_pipeline_offline import FakeBrowser, cfg  # noqa: F401  (fixture)
from tests.test_triage import _seed

REFS = ("A1", "B2", "C3")


def _three(cfg):
    store = Store(cfg.db_path)
    ids = [_seed(store, ref, "GDH206", "4", 50_000 + i) for i, ref in enumerate(REFS)]
    store.close()
    return ids


def test_the_desk_shows_sessions_lanes_and_rows(cfg):
    _three(cfg)
    client, _ = _client(cfg)
    r = client.get("/desk")
    assert r.status_code == 200
    assert 'data-session="unknown"' in r.text
    for lane in ("look", "clear", "wait", "done"):
        assert f'data-lane="{lane}"' in r.text
    for ref in REFS:
        assert re.search(rf'class="desk-row[^"]*"[^>]*>(?:(?!</button>).)*{ref}', r.text, re.S), ref
    assert 'id="desk-lot"' in r.text and 'id="desk-toast"' in r.text and 'role="status"' in r.text


def test_the_board_fragment_matches_the_page(cfg):
    ids = _three(cfg)
    client, _ = _client(cfg)
    r = client.get("/desk/board")
    assert r.status_code == 200 and "<html" not in r.text
    assert [int(x) for x in re.findall(r'class="desk-row[^"]*"[^>]*data-pid="(\d+)"', r.text)] == [
        int(x) for x in re.findall(r'class="desk-row[^"]*"[^>]*data-pid="(\d+)"', client.get("/desk").text)]
    assert len(re.findall(r'class="desk-row"', r.text)) == len(ids)


def test_the_desk_and_the_bid_sheet_are_in_the_nav(cfg):
    client, _ = _client(cfg)
    r = client.get("/")
    assert 'href="/desk"' in r.text and 'href="/bids"' in r.text


def test_the_desk_is_local_only(cfg):
    client, _ = _client(cfg)
    assert client.get("/desk", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/desk/board", headers={"Host": "evil.example"}).status_code == 403


def test_the_old_review_still_lists_the_same_lots(cfg):
    ids = _three(cfg)
    client, _ = _client(cfg)
    r = client.get("/review")
    assert r.status_code == 200
    for ref in REFS:
        assert ref in r.text
    assert [int(x) for x in re.findall(r'<tr class="lot[^>]*data-pid="(\d+)"', r.text)] and set(ids) <= {
        int(x) for x in re.findall(r'data-pid="(\d+)"', r.text)}


def test_the_desk_page_gives_review_js_nothing_to_bind(cfg):
    _three(cfg)
    client, _ = _client(cfg)
    html = client.get("/desk").text
    assert '<tr class="lot' not in html
    assert "data-pid" not in re.search(r"<body[^>]*>", html).group(0)
    for name in ("data-confirm", "data-ev=", "data-zoom", "data-sheet-zoom", "data-zoom-photo", "data-delete-session"):
        assert name not in html.split("<main>")[1].split("</main>")[0], name


def test_the_desk_says_when_there_is_nothing_to_decide(cfg):
    client, _ = _client(cfg)
    assert "Nothing to decide" in client.get("/desk").text
    ids = _three(cfg)
    store = Store(cfg.db_path)
    for pid in ids:
        store.decide(pid, "reject", "no", None, False)
    store.close()
    r = client.get("/desk")
    assert "This session is decided" in r.text
    assert "Nothing in this lane" in client.get("/desk?lane=look").text


def test_session_and_lane_params_choose_what_shows(cfg):
    ids = _three(cfg)
    client, _ = _client(cfg)
    r = client.get("/desk/board?session=unknown&lane=done")
    assert r.status_code == 200
    rows = re.findall(r'<button[^>]*class="desk-row[^"]*"[^>]*>', r.text)
    assert len(rows) == len(ids) and all(" hidden" in row for row in rows)
    assert 'href="/desk?session=unknown&amp;lane=done"' in client.get("/desk").text or "lane=done" in client.get("/desk").text


# -- the lot view (Task 3) ---------------------------------------------------------------------------

def _scanned(cfg):
    store = Store(cfg.db_path)
    scan(cfg, FakeBrowser(), store, ["GDH206"], limit_per_model=1)
    row = store.list_proposals(decision="pending")[0]
    store.close()
    return row["id"]


def test_the_lot_view_names_what_to_look_at(cfg, monkeypatch):
    pid = _judged(cfg, monkeypatch)
    client, _ = _client(cfg)
    r = client.get(f"/desk/lot/{pid}")
    assert r.status_code == 200 and "<html" not in r.text
    assert "Look at" in r.text
    store = Store(cfg.db_path)
    from auction_bot.review.app import _load
    items = look_items(_load(store.proposal(pid)))
    store.close()
    assert items
    for it in items:
        assert len(re.findall(r"<button[^>]*desk-look[^>]*>", r.text)) == len(items)
        assert it.text.split(":")[0][:30] in r.text.replace("&#39;", "'")
    assert re.search(r'<button[^>]*class="desk-look[^"]*"[^>]*data-where="[a-z]+"[^>]*data-photo="', r.text) or "data-photo=" in r.text


def test_the_lot_view_lights_the_grid_cell(cfg):
    pid = _scanned(cfg)
    store = Store(cfg.db_path)
    from auction_bot.review.app import _load
    p = _load(store.proposal(pid))
    store.close()
    grid = mini_grid(cfg, p)
    assert grid is not None and grid["rows"]
    client, _ = _client(cfg)
    r = client.get(f"/desk/lot/{pid}")
    used = re.findall(r'<td class="used"[^>]*>(.*?)</td>', r.text, re.S)
    assert len(used) == 1
    assert f"{p['hard']['base_jpy_k']:,}" in used[0]


def test_a_lot_without_an_amount_cannot_be_approved(cfg):
    store = Store(cfg.db_path)
    pid = _seed(store, "N1", "GDH206", "4", 50_000, bid=None)
    store.close()
    client, _ = _client(cfg)
    r = client.get(f"/desk/lot/{pid}")
    approve = re.search(r"<button[^>]*data-act=\"approve\"[^>]*>", r.text).group(0)
    assert "disabled" in approve
    assert "No amount from the grid" in r.text


def test_an_amount_below_the_start_price_cannot_be_approved(cfg):
    store = Store(cfg.db_path)
    pid = _seed(store, "L1", "GDH206", "4", 50_000, bid=300)  # start price in the seed is 500,000 yen = 500K
    store.close()
    client, _ = _client(cfg)
    r = client.get(f"/desk/lot/{pid}")
    assert "disabled" in re.search(r"<button[^>]*data-act=\"approve\"[^>]*>", r.text).group(0)
    assert "below the start price" in r.text


def test_the_decision_bar_uses_the_route_fields(cfg, monkeypatch):
    pid = _judged(cfg, monkeypatch)
    client, _ = _client(cfg)
    r = client.get(f"/desk/lot/{pid}")
    assert f'data-decide="/p/{pid}/decide"' in r.text and f'data-delete="/p/{pid}/delete"' in r.text
    assert f'data-event="/p/{pid}/event"' in r.text and f'data-lot="{pid}"' in r.text and "data-start=" in r.text
    for name in ("amount", "reason", "make_rule", "rule_text", "feedback_req", "feedback_note"):
        assert f'name="{name}"' in r.text, name
    for act in ("approve", "amount", "reject", "clear"):
        assert f'data-act="{act}"' in r.text, act
    assert 'type="checkbox"' in r.text and 'value="1"' in r.text
    assert "desk-lot " not in r.text.split("<article", 1)[1].split(">", 1)[0] and 'class="desk-lotview' in r.text


def test_nichibo_opens_in_jims_own_browser(cfg):
    pid = _scanned(cfg)
    client, _ = _client(cfg)
    r = client.get(f"/desk/lot/{pid}")
    link = re.search(r"<a[^>]*data-desk-ev=\"nichibo_click\"[^>]*>", r.text).group(0)
    assert 'target="_blank"' in link and 'rel="noopener"' in link and 'href="https://' in link
    assert "data-ev=" not in r.text


def test_the_lot_view_gives_review_js_nothing_to_bind(cfg, monkeypatch):
    pid = _judged(cfg, monkeypatch)
    client, _ = _client(cfg)
    for url in (f"/desk/lot/{pid}", f"/desk/lot/{_scanned(cfg)}"):
        t = client.get(url).text
        for bad in ("data-ev=", "data-zoom", "data-sheet-zoom", "data-zoom-photo", "data-confirm", "data-delete-session", "data-pid"):
            assert bad not in t.replace("data-desk-zoom", ""), bad


def test_an_unknown_lot_is_404(cfg):
    client, _ = _client(cfg)
    assert client.get("/desk/lot/9999").status_code == 404


BAR_BUTTONS = ('data-act="approve"', 'data-act="amount"', 'data-act="reject"', 'data-act="clear"')


def test_a_cleared_lot_shows_cleared_and_no_decision_bar(cfg):
    pid = _scanned(cfg)
    client, _ = _client(cfg)
    assert client.post(f"/p/{pid}/delete", headers={"Accept": "application/json"}).status_code == 200
    t = client.get(f"/desk/lot/{pid}").text
    assert "Cleared, not a decision" in t
    assert not any(b in t for b in BAR_BUTTONS)


def test_an_approved_lot_shows_its_decision_and_amount(cfg):
    store = Store(cfg.db_path)
    pid = _seed(store, "AP1", "GDH206", "4", 50_000)  # start price 500K
    store.close()
    client, _ = _client(cfg)
    r = client.post(f"/p/{pid}/decide", data={"action": "approve", "amount": "1000"}, headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    t = client.get(f"/desk/lot/{pid}").text
    assert "Approved" in t and "1,000K" in t
    assert not any(b in t for b in BAR_BUTTONS)


def test_a_rejected_lot_shows_its_reason(cfg):
    pid = _scanned(cfg)
    client, _ = _client(cfg)
    r = client.post(f"/p/{pid}/decide", data={"action": "reject", "reason": "ten seats on the sheet"}, headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    t = client.get(f"/desk/lot/{pid}").text
    assert "Rejected" in t and "ten seats on the sheet" in t
    assert not any(b in t for b in BAR_BUTTONS)


def test_a_lot_of_another_guide_gets_no_grid(cfg):
    pid = _scanned(cfg)
    store = Store(cfg.db_path)
    store.conn.execute("UPDATE proposals SET guide_id=987654 WHERE id=?", (pid,))
    store.conn.commit()
    from auction_bot.review.app import _load
    p = _load(store.proposal(pid))
    store.close()
    assert mini_grid(cfg, p) is None
    client, _ = _client(cfg)
    assert 'class="used"' not in client.get(f"/desk/lot/{pid}").text


# -- Task 4: keys, decisions and the undo window ------------------------------------------------------------

from pathlib import Path  # noqa: E402

DESK_JS = Path(__file__).resolve().parent.parent / "src" / "auction_bot" / "review" / "static" / "desk.js"
JSON = {"Accept": "application/json"}


def test_a_desk_decision_is_recorded_as_from_the_desk(cfg):
    pid = _three(cfg)[0]
    client, _ = _client(cfg)
    r = client.post(f"/p/{pid}/decide", data={"action": "approve", "amount": "1000", "via": "desk"}, headers=JSON)
    assert r.status_code == 200 and r.json()["decision"] == "approved"
    store = Store(cfg.db_path)
    try:
        assert store.conn.execute("SELECT via FROM decisions WHERE proposal_id=?", (pid,)).fetchone()[0] == "desk"
    finally:
        store.close()


def test_a_decision_on_a_superseded_lot_is_refused(cfg):
    store = Store(cfg.db_path)
    old = _seed(store, "S1", "GDH206", "4", 50_000)
    store.supersede_pending("S1", 1)
    new = _seed(store, "S1", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    r = client.post(f"/p/{old}/decide", data={"action": "approve", "amount": "1000", "via": "desk"}, headers=JSON)
    assert r.status_code == 409
    store = Store(cfg.db_path)
    try:
        assert store.proposal(new)["decision"] == "pending"
    finally:
        store.close()


def test_desk_js_talks_only_to_existing_routes():
    src = DESK_JS.read_text(encoding="utf-8")
    targets = [t.strip() for t in re.findall(r"fetch\(\s*([\w.\[\]\"]+(?:\(\w*\))?)", src)]
    assert targets
    # the board, the lot fragment, the three routes the lot view names itself (q.url is its data-decide)
    allowed = {"boardUrl()", "lotUrl(pid)", "v.dataset.event", 'v.dataset["delete"]', "q.url"}
    assert set(targets) <= allowed, set(targets) - allowed
    assert re.search(r"url: v\.dataset\.decide", src)
    assert set(re.findall(r"dataset\.(decide|event)|dataset\[\"(delete)\"\]", src)) <= {("decide", ""), ("event", ""), ("", "delete")}
    assert re.findall(r'"(/[a-z][^"]*)"', src) == ["/desk/board?session=", "/desk/lot/"]
    for banned in ("--live", "/finish", "/auction/bid", "nichibojapan", "/api/runs"):
        assert banned not in src, banned
    # attribute names today's review.js binds on every page
    for name in ("data-confirm", "data-ev=", "data-sheet-zoom", "data-zoom-photo", "data-delete-session"):
        assert name not in src, name
    assert "data-zoom" not in src.replace("data-desk-zoom", "")


def test_desk_js_keeps_the_undo_window_and_sends_on_leave():
    src = DESK_JS.read_text(encoding="utf-8")
    assert "UNDO_MS = 10000" in src and "pagehide" in src and "keepalive: true" in src


def test_the_desk_page_loads_desk_js_deferred(cfg):
    _three(cfg)
    client, _ = _client(cfg)
    assert re.search(r'<script[^>]*src="/static/desk\.js[^"]*"[^>]*defer', client.get("/desk").text)
    assert client.get("/static/desk.js").status_code == 200


# -- Task 4 fix round 1 ----------------------------------------------------------------------------------

def test_desk_js_acts_only_on_the_lot_that_is_shown():
    src = DESK_JS.read_text(encoding="utf-8")
    assert "v.dataset.lot === state.lot" in src          # lotView() is null while another lot loads
    assert "if (!lotView()) return;" in src               # clicks in a stale article do nothing
    assert "waitingFor(row) || waitingFor(pid)" in src    # clear refuses a lot with a decision waiting
    assert "if (e.repeat && !moves) return;" in src       # a held key never decides or clears
    assert "undo(q)" in src and "function undo(q)" in src  # a row's Undo cancels its own entry


def test_desk_js_errors_name_the_lot_and_stay():
    src = DESK_JS.read_text(encoding="utf-8")
    assert "function fail(" in src and "Dismiss" in src
    assert 'ref + ": This lot changed while you decided. It is back on the desk."' in src
    assert 'could not be cleared: " + why' in src and "it is no longer waiting." in src
    assert "Type a whole number of thousands of yen." in src


def test_desk_js_narrow_moves_select_only_and_wide_opens_the_first_lot():
    src = DESK_JS.read_text(encoding="utf-8")
    assert "if (narrow.matches) selectRow(to); else select(to);" in src
    assert "if (narrow.matches || state.lot) return;" in src and "autoOpen();" in src


def test_the_lot_fragment_has_a_close_button_and_names_its_ref(cfg):
    pid = _three(cfg)[0]
    client, _ = _client(cfg)
    html = client.get(f"/desk/lot/{pid}").text
    assert re.search(r'<button[^>]*data-act="close"[^>]*aria-label="Close the lot"', html)
    assert 'data-ref="A1"' in html


# -- Task 4 fix round 2 ----------------------------------------------------------------------------------

def test_desk_js_waiting_line_replaces_notes_and_toast_clicks_do_not_close_the_lot():
    src = DESK_JS.read_text(encoding="utf-8")
    body = src[src.index("function showWaiting()"):]
    body = body[:body.index("\n  }")]
    assert 'noteText = ""' in body and "clearTimeout(noteTimer)" in body
    assert "!t.isConnected" in src and "composedPath().indexOf(toast)" in src


def test_desk_js_lot_keys_use_the_lot_shown():
    src = DESK_JS.read_text(encoding="utf-8")
    for fn in ("showSheet", "showPhoto", "zoom", "openNichibo"):
        body = src[src.index(f"function {fn}("):]
        body = body[:body.index("\n  }")]
        assert "lotView()" in body, fn
    assert "lotPane)" not in src[src.index("function inLot"):src.index("function look(")]


# -- Task 5: the bid sheet -------------------------------------------------------------------------------

def _approve_two(cfg):
    ids = _three(cfg)
    client, _ = _client(cfg)
    for pid in ids[:2]:
        r = client.post(f"/p/{pid}/decide", data={"action": "approve", "via": "triage"}, headers={"Accept": "application/json"})
        assert r.status_code == 200, r.text
    return client, ids


def test_the_bid_sheet_page_lists_approved_lots(cfg):
    client, _ = _approve_two(cfg)
    html = client.get("/bids").text
    assert "A1" in html and "B2" in html and "C3" not in html and "Exposure" in html
    assert "Testing lock is on." in html


def test_the_bid_sheet_shows_a_dry_run_and_a_block(cfg):
    client, ids = _approve_two(cfg)
    store = Store(cfg.db_path)
    store.record_placement(ids[0], "A1", "GDH206", 1000, ok=True, message="typed and read back", screenshot=None, dry_run=True)
    store.record_placement(ids[1], "B2", "GDH206", 1000, ok=False, message="start price changed", screenshot=None, dry_run=True)
    store.close()
    html = client.get("/bids").text
    assert "Dry-run placed" in html and "start price changed" in html


def test_the_bid_sheet_cannot_ask_for_a_live_placement(cfg):
    client, _ = _approve_two(cfg)
    html = client.get("/bids").text
    assert "--" + "live" not in html
    assert 'data-run-command="place"' in html
    assert 'data-run-command="scan"' not in html
