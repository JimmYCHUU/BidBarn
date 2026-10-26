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
