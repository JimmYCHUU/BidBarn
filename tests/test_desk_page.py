"""The desk page: sessions, lanes and rows served beside today's review."""
import re

from fastapi.testclient import TestClient

from auction_bot.review.app import create_app
from auction_bot.store import Store
from tests.test_dashboard_app import _client
from tests.test_pipeline_offline import cfg  # noqa: F401  (fixture)
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
