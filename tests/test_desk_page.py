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
    # the board, the lot fragment, the routes the lot view names itself (q.url is its data-decide, then reopen)
    allowed = {"boardUrl()", "lotUrl(pid)", "v.dataset.event", 'v.dataset["delete"]', "q.url", "v.dataset.reopen"}
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
    # A refused decision records nothing; the lot is in the Decided lane (decided elsewhere) or replaced (a replay)
    assert 'ref + ": This lot changed while you decided (decided elsewhere, or replaced by a newer scan). Nothing was recorded from the desk."' in src
    assert "It is back on the desk" not in src
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
    assert "Testing lock is on. Placement is a dry run: each lot's page is opened, re-checked, the amount typed and read back, and a screenshot saved. Nothing is submitted. Live placement cannot be started from this page." in html


def test_the_bid_sheet_shows_a_dry_run_and_a_block(cfg):
    client, ids = _approve_two(cfg)
    store = Store(cfg.db_path)
    store.record_placement(ids[0], "A1", "GDH206", 1000, ok=True, message="typed and read back", screenshot=None, dry_run=True)
    store.record_placement(ids[1], "B2", "GDH206", 1000, ok=False, message="start price changed", screenshot=None, dry_run=True)
    store.close()
    html = client.get("/bids").text
    assert "Dry run done" in html and "start price changed" in html


def test_the_bid_sheet_cannot_ask_for_a_live_placement(cfg):
    client, _ = _approve_two(cfg)
    html = client.get("/bids").text
    assert "--" + "live" not in html
    assert 'data-run-command="place"' in html
    assert 'data-run-command="scan"' not in html


def test_the_bid_sheet_lock_band_in_the_dry_state(cfg):
    cfg.project_status = "The project is finished"
    cfg.dry_run = True
    client, _ = _approve_two(cfg)
    html = client.get("/bids").text
    assert "Placement is a dry run (dry_run in config.yaml)" in html
    assert "Testing lock is on" not in html and "--" + "live" not in html


def test_the_bid_sheet_lock_band_in_the_live_state(cfg):
    cfg.project_status = "The project is finished"
    cfg.dry_run = False
    client, _ = _approve_two(cfg)
    html = client.get("/bids").text
    assert "Placement from this page is still a dry run. Live placement is started only by hand, outside this page." in html
    assert "Testing lock is on" not in html and "--" + "live" not in html
    assert 'data-run-command="place"' in html


# -- Final review fixes -----------------------------------------------------------------------------------

def _js_fn(src: str, name: str) -> str:
    body = src[src.index(f"function {name}("):]
    return body[:body.index("\n  }")]


def test_desk_js_a_double_click_decides_one_lot():
    """C1: the second click of a double click lands on the next lot's Approve (the bar is sticky); it must not
    decide that lot. Decision clicks with e.detail > 1 are ignored, and nothing decides for SETTLE_MS after a
    lot view is swapped in. The second D of a clear must come CLEAR_GAP_MS after the first."""
    src = DESK_JS.read_text(encoding="utf-8")
    assert "var SETTLE_MS = 400;" in src and "var CLEAR_GAP_MS = 300;" in src
    assert "e.detail > 1" in _js_fn(src, "onLotClick")
    assert "shownAt = Date.now();" in _js_fn(src, "loadLot")
    for fn in ("approve", "approveChosen", "rejectWithReason", "clearLot"):
        assert "if (settling()) return;" in _js_fn(src, fn), fn
    assert "now - clearArmed.at < CLEAR_GAP_MS" in _js_fn(src, "clearLot")


def _rules_review(cfg, ref, hard, bid):
    """A lot stored the way the pipeline stores a hard NEEDS_REVIEW: stage hard, the HardResult, no soft_json."""
    import json
    store = Store(cfg.db_path)
    pid = _seed(store, ref, "GDH206", "4", 50_000, bid=bid)
    store.conn.execute("UPDATE proposals SET hard_json=?, soft_json=NULL, stage='hard' WHERE id=?", (json.dumps(hard), pid))
    store.conn.commit()
    store.close()
    return pid


HARD_START = {"status": "NEEDS_REVIEW", "checks": [{"name": "cell", "ok": True, "detail": "2020 / 40K-60K -> 1,000K", "review": False},
                                                    {"name": "start", "ok": False, "detail": "start price unknown", "review": True}],
              "variant_name": "Van", "year": 2020, "band_label": "40K-60K", "base_jpy_k": 1000, "bid_jpy_k": 1000,
              "modifiers_applied": [], "deferred_modifiers": [], "toggles": [], "variant_how": "default", "candidate_bids": {}}
HARD_AMBIGUOUS = {"status": "NEEDS_REVIEW", "checks": [{"name": "variant", "ok": False, "detail": "two variants fit", "review": True}],
                  "variant_name": None, "year": None, "band_label": None, "base_jpy_k": None, "bid_jpy_k": None,
                  "modifiers_applied": [], "deferred_modifiers": [], "toggles": [], "variant_how": "ambiguous",
                  "candidate_bids": {"Van DX": 1200, "Van GL": None}}


def test_a_lot_the_rules_sent_to_review_is_in_needs_a_look(cfg):
    pid = _rules_review(cfg, "HS1", HARD_START, 1000)
    client, _ = _client(cfg)
    board = client.get("/desk/board").text
    assert re.search(rf'data-pid="{pid}"[^>]*data-lane="look"', board)
    t = client.get(f"/desk/lot/{pid}").text
    assert "Card-clear" not in t and "start: start price unknown" in t


def test_the_lot_view_shows_the_rules_reasons_and_the_per_variant_amounts(cfg):
    pid = _rules_review(cfg, "HV1", HARD_AMBIGUOUS, None)
    client, _ = _client(cfg)
    t = client.get(f"/desk/lot/{pid}").text
    amount = t.split('aria-label="Amount"')[1].split("</section>")[0]
    assert "two variants fit" in amount
    assert "Van DX" in amount and "1,200K" in amount and "Van GL" in amount and "no bid" in amount


def test_a_lot_covered_by_its_other_guides_approval_cannot_be_approved_again(cfg):
    """I2: the same vehicle under a second guide, once the first is approved, offers no Approve and no amount."""
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000, guide=1)
    b = _seed(store, "TW1", "GDH206", "4", 50_000, guide=2)
    store.close()
    client, _ = _client(cfg)
    before = client.get(f"/desk/lot/{b}").text
    assert 'data-act="approve"' in before and "Covered by" not in before
    assert client.post(f"/p/{a}/decide", data={"action": "approve", "amount": "1000", "via": "desk"}, headers=JSON).status_code == 200
    t = client.get(f"/desk/lot/{b}").text
    assert re.search(rf"Covered by the approval of #{a} under [^<]+\.", t)
    for gone in ('data-act="approve"', 'data-act="amount"', 'data-act="amount-submit"', 'name="amount"'):
        assert gone not in t, gone
    assert 'data-act="reject"' in t and 'data-act="clear"' in t
    assert re.search(rf'<article[^>]*data-covered="Covered by the approval of #{a} under', t)


def test_desk_js_does_not_queue_an_approve_for_a_covered_lot():
    src = DESK_JS.read_text(encoding="utf-8")
    assert "dataset.covered" in _js_fn(src, "approve")
    assert 'action === "approve" && v.dataset.covered' in _js_fn(src, "queueDecision")
    key_e = src[src.index('else if (k === "e")'):]
    assert "dataset.covered" in key_e[:key_e.index("\n")]


def _approve(client, pid, amount=None):
    data = {"action": "approve", "via": "desk"} | ({"amount": str(amount)} if amount else {})
    r = client.post(f"/p/{pid}/decide", data=data, headers=JSON)
    assert r.status_code == 200, r.text


def test_the_bid_sheet_follows_the_placement_order_and_marks_the_next_dry_run(cfg):
    """I3: the sheet lists what `bot place` will take, in its order, and marks the first max_bids_per_run."""
    cfg.max_bids_per_run = 2
    ids = _three(cfg)
    client, _ = _client(cfg)
    for pid in reversed(ids):  # approved newest first: placement still takes the oldest proposal first
        _approve(client, pid)
    html = client.get("/bids").text
    listed = [int(x) for x in re.findall(r'<tr[^>]*data-queue="\d+"[^>]*>\s*<td[^>]*>[^<]*</td>\s*<td><a href="/p/(\d+)"', html)]
    assert listed == ids
    marked = [int(x) for x in re.findall(r'<tr[^>]*data-next-run[^>]*>\s*<td[^>]*>[^<]*</td>\s*<td><a href="/p/(\d+)"', html)]
    store = Store(cfg.db_path)
    try:
        assert marked == [r["id"] for r in store.approved_unplaced(cfg.max_bids_per_run)] == ids[:2]
    finally:
        store.close()
    assert "taken by the next dry run" in html and "1 more waits for a later run" in html


def test_the_bid_sheet_uses_the_amount_placement_types(cfg):
    ids = _three(cfg)
    client, _ = _client(cfg)
    _approve(client, ids[0])
    store = Store(cfg.db_path)
    store.conn.execute("UPDATE proposals SET approved_bid_jpy_k=NULL WHERE id=?", (ids[0],))  # an older approval
    store.conn.commit()
    store.close()
    html = client.get("/bids").text
    assert "<b>1,000K</b>" in html and "Exposure 1,000K" in html and "<b>0K</b>" not in html


def test_the_bid_sheet_keeps_the_last_day_of_placed_lots_apart(cfg):
    from datetime import datetime, timedelta, timezone
    ids = _three(cfg)
    client, _ = _client(cfg)
    for pid in ids:
        _approve(client, pid)
    now = datetime.now(timezone.utc)
    store = Store(cfg.db_path)
    store.conn.execute("UPDATE proposals SET placed_at=? WHERE id=?", ((now - timedelta(hours=1)).isoformat(timespec="seconds"), ids[0]))
    store.conn.execute("UPDATE proposals SET placed_at=? WHERE id=?", ((now - timedelta(hours=30)).isoformat(timespec="seconds"), ids[1]))
    store.conn.commit()
    store.close()
    html = client.get("/bids").text
    waiting, placed = html.split('aria-label="Placed in the last 24 hours"')
    assert "A1" not in waiting and "B2" not in waiting and "C3" in waiting
    assert "A1" in placed and "B2" not in placed and "C3" not in placed


# -- phase 2: refusals in plain words, Reopen, the dry-run state ---------------------------------------------------

REVIEW_JS = DESK_JS.with_name("review.js")


def _post(client, pid, **data):
    return client.post(f"/p/{pid}/decide", data={"action": "approve", "via": "desk", **data}, headers=JSON)


def test_a_second_approval_is_refused_with_the_guide_named(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000, guide=223)
    b = _seed(store, "TW1", "GDH206", "4", 50_000, guide=224)
    store.close()
    client, _ = _client(cfg)
    assert _post(client, a, amount="1000").status_code == 200
    r = _post(client, b, amount="900")
    assert r.status_code == 409
    assert "already approved under" in r.json()["detail"] and f"lot {a}" in r.json()["detail"]
    assert "One bid per vehicle, so it is not approved again." in r.json()["detail"]


def test_an_approval_of_a_changed_lot_is_refused(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.update_soft(a, {"mode": "judge"})
    store.close()
    client, _ = _client(cfg)
    r = _post(client, a, amount="1000", rev="0")
    assert r.status_code == 409 and "changed since you opened it" in r.json()["detail"]
    assert _post(client, a, amount="1000", rev="1").status_code == 200
    assert _post(client, a, amount="1000", rev="x").status_code == 400


def test_a_decision_on_a_replaced_lot_names_the_new_one(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.supersede_pending("TW1", 1)
    n = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    r = _post(client, a, amount="1000")
    assert r.status_code == 409 and f"lot {n}" in r.json()["detail"]


def test_every_decision_form_sends_the_revision(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    assert 'data-rev="0"' in client.get(f"/desk/lot/{a}").text
    assert 'name="rev"' in client.get(f"/p/{a}").text
    assert 'data-rev="0"' in client.get("/review").text
    assert 'append("rev"' in DESK_JS.read_text(encoding="utf-8")
    assert '"rev"' in REVIEW_JS.read_text(encoding="utf-8")


def test_reopen_returns_a_decision_to_waiting(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    _approve(client, a)
    r = client.post(f"/p/{a}/reopen", headers=JSON)
    assert r.status_code == 200 and r.json() == {"ok": True, "decision": "pending"}
    assert client.post(f"/p/{a}/reopen", headers=JSON).status_code == 409
    assert client.post("/p/99999/reopen", headers=JSON).status_code == 404
    r = client.post(f"/p/{a}/reopen", data={"next": "//evil.example"}, follow_redirects=False)
    assert r.status_code == 409  # still waiting: nothing to reopen


def test_reopen_redirects_only_to_a_local_page(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    b = _seed(store, "TW2", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    _approve(client, a); _approve(client, b)
    r = client.post(f"/p/{a}/reopen", data={"next": "/bids"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/bids"
    r = client.post(f"/p/{b}/reopen", data={"next": "//evil.example"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/p/{b}"


def test_reopen_of_a_lot_a_newer_scan_replaced_points_at_the_new_one(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    b = _seed(store, "TW2", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    _approve(client, a); _approve(client, b)
    store = Store(cfg.db_path)  # bot scan --rescan stored a newer lot of each vehicle under the same guide
    n = _seed(store, "TW1", "GDH206", "4", 50_000)
    m = _seed(store, "TW2", "GDH206", "4", 50_000)
    store.close()
    r = client.post(f"/p/{a}/reopen", headers=JSON)
    assert r.status_code == 200 and r.json() == {"ok": True, "decision": "superseded", "newer": n}
    _approve(client, n)
    r = client.post(f"/p/{b}/reopen", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/p/{m}"


def test_no_redirect_leaves_the_site(cfg):
    from auction_bot.review.app import _local_path
    assert _local_path("/bids") and _local_path("/review?model=GDH206")
    for bad in ("", "bids", "//evil.example", "/\\evil.example", "/x\\evil", "https://evil.example"):
        assert not _local_path(bad), bad
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    _approve(client, a)
    r = client.post(f"/p/{a}/reopen", data={"next": "/\\evil.example"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/p/{a}"
    r = client.post("/sessions/earlier/delete", data={"next": "/\\evil.example"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/review"


def test_a_revision_that_is_not_a_number_is_a_bad_request(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    for bad in ("²", "-1", "1.5"):
        r = _post(client, a, amount="1000", rev=bad)
        assert r.status_code == 400 and r.json()["detail"] == "rev must be a number", bad
    assert _post(client, a, amount="1000", rev=" 0 ").status_code == 200


def test_desk_js_reopen_waits_for_the_lot_and_says_what_happened():
    src = DESK_JS.read_text(encoding="utf-8")
    body = src.split("function reopen() {")[1].split("\n  }\n")[0]
    assert 'if (e.detail > 1 && (DECIDING[act] || act === "reopen")) return;' in src  # a double click reopens once
    assert "return loadLot(pid);" in body                     # the in-flight guard holds until the lot has reloaded
    assert '"The lot could not be reopened (error " + r.status + "). Reload the page and look again."' in body
    assert "Error " not in body
    assert '"Reopened. A newer scan replaced this lot: decide the new one (lot " + newer + ")."' in body
    assert "loadLot(newer, newer)" not in body
    # item 3: the new lot is found on the refreshed board and opened on the session and lane that hold it
    assert "var nr = rowFor(newer);" in body
    assert "state.session = nr.dataset.session; state.lane = nr.dataset.lane;" in body
    assert "return select(newer);" in body
    assert "which is not on the board now." in body and "if (state.lot === pid) return loadLot(pid);" in body
    # item 4: any refusal reloads the lot, not only a 409
    assert "r.status === 409 && state.lot" not in body
    assert "if (state.lot === pid) return loadLot(pid);   // any refusal" in body


def test_reopen_waits_while_bids_are_placed(cfg):
    from tests.test_dashboard_app import FakeJobs

    class Placing(FakeJobs):
        def current(self):
            return {"id": 1, "progress": {"stage": "place"}}

    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    _approve(client, a)
    client = TestClient(create_app(cfg, jobs=Placing(cfg)))
    r = client.post(f"/p/{a}/reopen", headers=JSON)
    assert r.status_code == 409 and "being placed" in r.json()["detail"]
    store = Store(cfg.db_path)
    assert store.proposal(a)["decision"] == "approved"
    store.close()


def test_a_decided_lot_offers_reopen_until_placed_live(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    _approve(client, a)
    t = client.get(f"/desk/lot/{a}").text
    assert f'data-reopen="/p/{a}/reopen"' in t and 'data-act="reopen"' in t
    assert f'action="/p/{a}/reopen"' in client.get(f"/p/{a}").text
    store = Store(cfg.db_path)
    store.record_placement(a, "TW1", "GDH206", 1000, True, "placed", None, dry_run=False)
    store.close()
    assert "/reopen" not in client.get(f"/desk/lot/{a}").text
    assert "/reopen" not in client.get(f"/p/{a}").text


def test_the_bid_sheet_shows_a_dry_run_done_and_skips_it_for_the_next_run(cfg):
    cfg.max_bids_per_run = 1
    ids = _three(cfg)[:2]
    client, _ = _client(cfg)
    for pid in ids:
        _approve(client, pid)
    store = Store(cfg.db_path)
    store.record_placement(ids[0], "A1", "GDH206", 1000, True, "dry run", None, dry_run=True)
    store.close()
    html = client.get("/bids").text
    rows = re.findall(r"<tr[^>]*data-queue[^>]*>.*?</tr>", html, re.S)
    first = next(r for r in rows if f'/p/{ids[0]}"' in r)
    second = next(r for r in rows if f'/p/{ids[1]}"' in r)
    assert "Dry run done" in first and "data-next-run" not in first
    assert "data-next-run" in second
    for pid in ids:
        assert re.search(rf'<form[^>]*action="/p/{pid}/reopen"[^>]*>.*?name="next" value="/bids"', html, re.S)


def test_the_bid_sheet_reads_a_failed_live_attempt_as_failed(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    _approve(client, a)
    store = Store(cfg.db_path)
    store.record_placement(a, "TW1", "GDH206", 1000, False, "error: x", None, dry_run=False)
    store.close()
    html = client.get("/bids").text
    assert "Live attempt failed" in html and "error: x" in html


def _lane_of(html, pid):
    m = re.search(r'<[^>]*class="[^"]*desk-row[^"]*"[^>]*data-pid="%d"[^>]*>' % pid, html)
    assert m, "no row"
    return re.search(r'data-lane="(\w+)"', m.group(0)).group(1)


def test_a_covered_lot_stays_covered_however_old_the_approval(cfg):
    from datetime import datetime, timedelta, timezone
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000, guide=223)
    b = _seed(store, "TW1", "GDH206", "4", 50_000, guide=224)
    c = _seed(store, "TW2", "GDH206", "4", 50_000, guide=223)
    d = _seed(store, "TW2", "GDH206", "4", 50_000, guide=224)
    store.close()
    client, _ = _client(cfg)
    _approve(client, a); _approve(client, c)
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    store = Store(cfg.db_path)
    store.record_placement(c, "TW2", "GDH206", 1000, True, "placed", None, dry_run=False)  # c: placed live
    store.conn.execute("UPDATE proposals SET decided_at=?, placed_at=COALESCE(placed_at, ?) WHERE id IN (?,?)", (old, old, a, c))
    store.conn.commit(); store.close()
    html = client.get("/desk/board").text
    assert _lane_of(html, b) == "done" and _lane_of(html, d) == "done"
    # covered lots are not work: the sessions' undecided counts see none of them
    from auction_bot.review.desk_model import build_desk
    from auction_bot.review.app import _load
    store = Store(cfg.db_path)
    pending = [_load(r) for r in store.list_triage()]
    covering = [_load(r) for r in store.covering_proposals()]
    store.close()
    sessions = build_desk(pending, [], lambda p: (p["id"],), datetime.now(timezone.utc), covering=covering)
    assert sum(s.undecided() for s in sessions) == 0


def test_a_replaced_lot_reopened_keeps_its_time_on_its_page(cfg):
    store = Store(cfg.db_path)
    a = _seed(store, "TW1", "GDH206", "4", 50_000)
    store.close()
    client, _ = _client(cfg)
    _approve(client, a)
    store = Store(cfg.db_path)
    _seed(store, "TW1", "GDH206", "4", 50_000)
    store.close()
    assert client.post(f"/p/{a}/reopen", headers=JSON).json()["decision"] == "superseded"
    store = Store(cfg.db_path)
    assert store.proposal(a)["decided_at"] is not None
    store.close()
    page = client.get(f"/p/{a}").text
    assert "None" not in page


def test_a_bad_session_key_is_no_session(cfg):
    client, _ = _client(cfg)
    assert client.post("/sessions/²/delete", follow_redirects=False).status_code == 404
    assert client.post("/sessions/abc/delete", follow_redirects=False).status_code == 404


def test_review_js_a_refused_decision_says_to_reload():
    src = (DESK_JS.parent / "review.js").read_text(encoding="utf-8")
    assert '" Reload the page to see the lot as it is now."' in src and "x.status === 409" in src


def test_the_desk_has_a_scan_bar(cfg):
    client, _ = _client(cfg)
    r = client.get("/desk")
    assert "data-desk-scan" in r.text and "Scan now" in r.text and "/static/desk_scan.js" in r.text


def test_the_scan_fragment_is_its_own_route(cfg):
    client, _ = _client(cfg)
    r = client.get("/desk/scan")
    assert r.status_code == 200 and "data-desk-scan" in r.text and "<html" not in r.text
    assert 'data-desk-scan-act="start"' in r.text
    assert client.get("/desk/scan", headers={"Host": "evil.example"}).status_code == 403


def test_a_running_scan_offers_stop(cfg):
    from tests.test_dashboard_app import FakeJobs

    class Scanning(FakeJobs):
        def current(self):
            return {"id": 9, "command": "scan-now", "args": [], "started_at": "2026-10-26T01:00:00+00:00",
                    "progress": {"stage": "scan", "guide": 3, "guides": 12, "review": 1, "bid": 0},
                    "cancel_requested": False, "pause_requested": False, "session_id": None}

    client, _ = _client(cfg, Scanning(cfg))
    t = client.get("/desk/scan").text
    assert 'data-desk-scan-act="stop"' in t and 'data-desk-scan-act="start"' not in t
    assert 'data-run="9"' in t and 'data-kind="running"' in t and 'data-poll="5"' in t


def test_desk_scan_js_starts_only_a_scan():
    from pathlib import Path
    import auction_bot.review as rv
    body = (Path(rv.__file__).parent / "static" / "desk_scan.js").read_text(encoding="utf-8")
    assert re.findall(r'command:\s*"([^"]+)"', body) == ["scan-now"]
    # fetch goes to the poll address or to the one `send` helper, which is called with two addresses only
    assert sorted(t.strip() for t in re.findall(r'fetch\(([^,)]+)', body)) == ['"/desk/scan"', "url"]
    assert re.findall(r'send\(("[^"]+"(?: \+ run \+ "/cancel")?)', body) == ['"/api/runs"', '"/api/runs/" + run + "/cancel"']
    for banned in ("--live", "place", "bid", "learn", "/finish", "/auction/bid", "nichibojapan"):
        assert banned not in body.lower(), banned
