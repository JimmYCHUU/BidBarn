"""The Ctrl+K palette: the read-only find endpoint and the script that uses it."""
import re
from pathlib import Path

from auction_bot.store import Store
from tests.test_dashboard_app import _client
from tests.test_pipeline_offline import cfg  # noqa: F401  (fixture)

STATIC = Path(__file__).resolve().parents[1] / "src" / "auction_bot" / "review" / "static"


def _lot(store, ref, code, chassis=None, summary=None, guide=1):
    facts = {"ref_no": ref}
    if chassis:
        facts["chassis"] = chassis
    return store.add_proposal(ref_no=ref, guide_id=guide, model_code=code, snapshot="s", rule_updated_at="u",
                              status="NEEDS_REVIEW", stage="manual", bid_jpy_k=1000,
                              summary=summary or f"2020 Toyota {ref}", facts_json=facts, hard_json={"checks": []},
                              soft_json={"mode": "manual"}, evidence_json={})


def _pids(client, q):
    r = client.get("/api/find", params={"q": q})
    assert r.status_code == 200
    return [lot["pid"] for lot in r.json()["lots"]]


def test_find_matches_lot_number_chassis_and_model(cfg):
    s = Store(cfg.db_path)
    a = _lot(s, "LOT-1001", "GDH206", chassis="GDH206-1234567")
    b = _lot(s, "LOT-2002", "ANH20W", chassis="ANH20-7654321")
    c = _lot(s, "LOT-3003", "ZZT231")
    s.close()
    client, _ = _client(cfg)
    assert _pids(client, "2002") == [b]
    assert _pids(client, "1234567") == [a]
    assert _pids(client, "anh20w") == [b]
    assert _pids(client, "zzt") == [c]
    lot = client.get("/api/find", params={"q": "2002"}).json()["lots"][0]
    assert lot["url"] == f"/p/{b}" and lot["ref_no"] == "LOT-2002" and set(lot) >= {"summary", "decision", "status"}


def test_find_treats_the_text_as_text(cfg):
    s = Store(cfg.db_path)
    _lot(s, "A1", "GDH206")
    pct = _lot(s, "B%2", "GDH206", summary="50% off")
    s.close()
    client, _ = _client(cfg)
    assert _pids(client, "%%") == []
    assert _pids(client, "__") == []
    assert _pids(client, "B%2") == [pct]
    assert _pids(client, "' OR 1=1 --") == []


def test_find_has_limits(cfg):
    s = Store(cfg.db_path)
    for i in range(30):
        _lot(s, f"MANY-{i:02d}", "GDH206")
    _lot(s, "x" * 40 + "tail", "ZZZ999")
    s.close()
    client, _ = _client(cfg)
    assert len(_pids(client, "many")) == 20
    assert client.get("/api/find", params={"q": "m"}).json() == {"lots": []}
    assert len(_pids(client, "x" * 200)) == 1  # cut to 40 characters, which the 44-character ref still contains


def test_find_survives_malformed_facts(cfg):
    s = Store(cfg.db_path)
    pid = _lot(s, "BAD1", "GDH206")
    s.conn.execute("UPDATE proposals SET facts_json='not json' WHERE id=?", (pid,))
    s.conn.commit()
    s.close()
    client, _ = _client(cfg)
    assert _pids(client, "bad1") == [pid]


def test_find_is_local_only(cfg):
    client, _ = _client(cfg)
    assert client.get("/api/find?q=abc", headers={"Host": "evil.example"}).status_code == 403


def test_palette_js_only_reads():
    src = (STATIC / "palette.js").read_text(encoding="utf-8")
    assert "POST" not in src.upper() and "method:" not in src
    assert re.findall(r'fetch\(\s*"([^"?]*)', src) == ["/api/find"]
    assert "ctrlKey" in src and "metaKey" in src and '"k"' in src


def test_every_page_loads_the_palette(cfg):
    client, _ = _client(cfg)
    for path in ("/", "/bids", "/rulebook", "/overview"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "/static/palette.js" in r.text and 'id="palette"' in r.text, path


def test_only_the_palette_calls_find():
    for js in STATIC.glob("*.js"):
        if js.name != "palette.js":
            assert "/api/find" not in js.read_text(encoding="utf-8"), js.name
