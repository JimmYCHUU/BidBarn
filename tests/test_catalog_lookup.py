import json
from pathlib import Path

from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.catalog import client as client_mod
from auction_bot.catalog.client import CatalogClient, catalog_enabled
from auction_bot.catalog.lookup import CatalogFinding, candidate_codes, candidate_sections, finding_key, lookup, unknown
from auction_bot.config import Config
from auction_bot.judge.topics import TOPIC_CRUISE, TOPIC_WELCAB
from auction_bot.nichibo.cache import PageCache
from auction_bot.nichibo.facts import VehicleFacts

FIX = Path(__file__).parent / "fixtures"
CAT = FIX / "catalog"
BASE = "https://www.kurumaerabi.com"
PAGES = {
    f"{BASE}/catalog/xml-sitemap/type.xml": CAT / "type_sitemap_slice.xml",
    f"{BASE}/catalog/toyota/3491/type/3BA-AGH30W/": CAT / "type_3BA-AGH30W.html",
    f"{BASE}/catalog/toyota/3491/fmc003/62479/": CAT / "grade_alphard_25x.html",
    f"{BASE}/catalog/toyota/3491/fmc003/67465/": CAT / "grade_alphard_25s_sidelift.html",
}


def _cfg(tmp_path, **catalog):
    cfg = Config()
    cfg.db_path = tmp_path / "bot.sqlite"
    cfg.catalog = {"enabled": True, "base_url": BASE, "cache_days": 90, "pace_seconds": [0, 0], **catalog}
    return cfg


def _client(tmp_path, pages=PAGES, fail=()):
    calls = []

    def fetch(url):
        calls.append(url)
        if url in fail or url not in pages:
            raise RuntimeError("404")
        return pages[url].read_text(encoding="utf-8")
    return CatalogClient(_cfg(tmp_path), PageCache(tmp_path / "cache", 24.0), fetch=fetch, sleep=lambda s: None), calls


def _tree():
    return json.loads((FIX / "guide_AYH30W_Alphard_tree.json").read_text(encoding="utf-8"))


def _rule():
    return parse_guide_tree(_tree())


def _facts(year=2021, chassis="AGH30W", variant="2.5 X", ocr=None):
    f = VehicleFacts(ref_no="r", url="u", year=year, make="Toyota", model="Alphard", variant_text=variant, chassis=chassis, km=50_000, grade_raw="4")
    f.sheet_ocr = ocr
    return f


def test_client_allowlist_cache_and_errors(tmp_path):
    client, calls = _client(tmp_path)
    assert not client.allowed("https://example.com/catalog/x") and client.get("https://example.com/catalog/x") is None
    assert client.get(f"{BASE}/kaitori/") is None and "outside" in client.errors[f"{BASE}/kaitori/"]
    url = f"{BASE}/catalog/toyota/3491/type/3BA-AGH30W/"
    assert client.get(url) and client.get(url) and calls.count(url) == 1      # the second read is the cache
    assert client.fetched == [url]
    assert client.get(f"{BASE}/catalog/toyota/3491/fmc003/99999/") is None and "404" in client.errors[f"{BASE}/catalog/toyota/3491/fmc003/99999/"]
    assert not catalog_enabled(Config()) and catalog_enabled(_cfg(tmp_path))


def test_client_uses_module_default_fetch_and_paces_network_only(tmp_path, monkeypatch):
    seen, slept = [], []
    monkeypatch.setattr(client_mod, "default_fetch", lambda url: seen.append(url) or "<html>x</html>")
    client = CatalogClient(_cfg(tmp_path, pace_seconds=[2.0, 5.0]), PageCache(tmp_path / "cache", 24.0), sleep=slept.append)
    a, b = f"{BASE}/catalog/a/", f"{BASE}/catalog/b/"
    assert client.get(a) and client.get(a) and client.get(b)
    assert seen == [a, b] and len(slept) == 1 and 0 < slept[0] <= 5.0     # no wait before the first call, none for a cache hit


def test_candidate_codes_and_sections():
    rule = _rule()
    assert candidate_codes(_facts(chassis="AGH30W?"), rule) == ["AGH30W", "AYH30W"]
    assert candidate_codes(_facts(chassis="", ocr={"model_code": "3BA-AGH30W"}), rule) == ["3BA-AGH30W"]
    assert candidate_codes(_facts(chassis=""), rule) == ["AYH30W"]
    secs = [202105, 202001, 201910, 201801]
    assert candidate_sections(secs, 2020) == [202001, 201910] and candidate_sections(secs, 2021) == [202105, 202001]
    assert candidate_sections(secs, 2019) == [201910, 201801] and candidate_sections(secs, 2023) == [202105] and candidate_sections(secs, 2017) == []


def test_lookup_cruise_and_welcab_from_fixture_pages(tmp_path):
    client, calls = _client(tmp_path)
    f = lookup(client, _facts(ocr={"model_code": "3BA-AGH30W"}), _rule(), {TOPIC_CRUISE, TOPIC_WELCAB})
    assert isinstance(f, CatalogFinding) and f.model_codes == ["3BA-AGH30W"] and f.urls == [f"{BASE}/catalog/toyota/3491/type/3BA-AGH30W/"]
    assert f.cruise["status"] == "standard" and f.cruise["mark"] == "標準装備" and f.adaptive["status"] == "standard"
    assert f.welcab["seat_system"] == "second_row_lift_up" and "サイドリフトアップ" in f.welcab["grade"]
    assert len(f.grades) == 5 and any(g["cruise"] == "standard" and g["name"] == "2.5 X" for g in f.grades)
    assert "grade pages failed" in f.note and f.key == finding_key(["3BA-AGH30W"], 2021, {TOPIC_CRUISE, TOPIC_WELCAB})
    d = f.to_dict()
    assert set(d) >= {"model_codes", "urls", "grades", "cruise", "adaptive", "welcab", "note", "fetched_at", "source", "key"}
    assert "万円" not in json.dumps(d, ensure_ascii=False)    # no prices anywhere in the finding
    # Welcab only: no grade page is fetched.
    client2, calls2 = _client(tmp_path / "b")
    w = lookup(client2, _facts(ocr={"model_code": "3BA-AGH30W"}), _rule(), {TOPIC_WELCAB})
    assert w.cruise["status"] == "unknown" and w.welcab["seat_system"] == "second_row_lift_up" and not any("/fmc003/" in u for u in calls2)


def test_lookup_unknown_paths(tmp_path):
    client, _ = _client(tmp_path, pages={})
    u = lookup(client, _facts(), _rule(), {TOPIC_CRUISE})
    assert u.cruise["status"] == "unknown" and "sitemap" in u.note
    client, _ = _client(tmp_path / "b")
    u = lookup(client, _facts(chassis="ZZZ99"), parse_guide_tree({**_tree(), "modelCode": "ZZZ99"}), {TOPIC_CRUISE})
    assert u.cruise["status"] == "unknown" and "no catalogue page" in u.note
    client, _ = _client(tmp_path / "c")
    old = lookup(client, _facts(year=2015, ocr={"model_code": "3BA-AGH30W"}), _rule(), {TOPIC_CRUISE})
    assert old.cruise["status"] == "unknown" and old.grades == []
    assert unknown("x").welcab["seat_system"] == "unknown"


def test_client_refuses_lookalike_hosts_and_traversal(tmp_path):
    client, calls = _client(tmp_path)
    for bad in ("https://www.kurumaerabi.com.evil.example/catalog/", "https://www.kurumaerabi.com@evil.example/catalog/",
                f"{BASE}/catalog/../kaitori/", f"{BASE}/catalog/%2e%2e/kaitori/", f"{BASE}/catalog/%252e%252e/kaitori/",
                "http://www.kurumaerabi.com/catalog/", "https://www.kurumaerabi.com:8443/catalog/"):
        assert not client.allowed(bad) and client.get(bad) is None
    assert calls == [] and client.allowed(f"{BASE}/catalog/toyota/3491/type/3BA-AGH30W/")


def test_client_redirect_is_a_failure_and_a_later_success_clears_it(tmp_path):
    import urllib.error
    state = {"fail": True}

    def fetch(url):
        if state["fail"]:
            raise urllib.error.HTTPError(url, 302, "Found", {}, None)
        return "<html>ok</html>"
    client = CatalogClient(_cfg(tmp_path), PageCache(tmp_path / "cache", 24.0), fetch=fetch, sleep=lambda s: None)
    url = f"{BASE}/catalog/x/"
    assert client.get(url) is None and "302" in client.errors[url]
    state["fail"] = False
    assert client.get(url) and url not in client.errors


def test_default_fetch_never_follows_a_redirect():
    import urllib.request
    h = client_mod._NoRedirect()
    assert h.redirect_request(urllib.request.Request("https://www.kurumaerabi.com/catalog/"), None, 302, "Found", {}, "https://evil.example/") is None


def test_lookup_ignores_useless_codes_and_does_not_pool_short_prefixes(tmp_path):
    client, calls = _client(tmp_path)
    rule = parse_guide_tree({**_tree(), "modelCode": "-"})
    u = lookup(client, _facts(chassis="-", ocr={"model_code": "-"}), rule, {TOPIC_CRUISE})
    assert u.cruise["status"] == "unknown" and "no catalogue page" in u.note and u.urls == []
    u = lookup(client, _facts(chassis="AGH"), parse_guide_tree({**_tree(), "modelCode": "AGH"}), {TOPIC_CRUISE})
    assert "no catalogue page" in u.note and not any("/fmc003/" in c for c in calls)


def test_lookup_caps_pages(tmp_path):
    urls = [f"{BASE}/catalog/toyota/{i}/type/3BA-AGH30W/" for i in range(1, 9)]
    sm = tmp_path / "sm.xml"
    sm.write_text("<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>", encoding="utf-8")
    pages = {f"{BASE}/catalog/xml-sitemap/type.xml": sm}
    client, calls = _client(tmp_path, pages=pages)
    f = lookup(client, _facts(chassis="AGH30W"), _rule(), {TOPIC_WELCAB})
    assert "capped at 6 pages" in f.note and len([c for c in calls if "/type/3BA" in c]) == 6


def test_welcab_unknown_when_no_grade_survives_the_year(tmp_path):
    client, _ = _client(tmp_path)
    old = lookup(client, _facts(year=2015, ocr={"model_code": "3BA-AGH30W"}), _rule(), {TOPIC_CRUISE})
    assert old.welcab["seat_system"] == "unknown"


def test_pooled_bare_code_path_matches_the_prefixed_one(tmp_path):
    client, _ = _client(tmp_path)
    f = lookup(client, _facts(chassis="AGH30W"), _rule(), {TOPIC_CRUISE, TOPIC_WELCAB})
    assert f.urls == [f"{BASE}/catalog/toyota/3491/type/3BA-AGH30W/"]
    assert f.cruise["status"] == "standard" and f.welcab["seat_system"] == "second_row_lift_up" and len(f.grades) == 5
    assert "404" in f.note and f.cruise is not f.adaptive


def test_lookup_surprise_year_none_empty_topics_and_mixed(tmp_path, monkeypatch):
    from auction_bot.catalog import lookup as lookup_mod
    client, calls = _client(tmp_path)
    f = lookup(client, _facts(year=None, chassis="AGH30W"), _rule(), set())
    assert len(f.grades) == 5 and "registration year unknown" in f.note and not any("/fmc003/" in c for c in calls)
    assert f.cruise["status"] == "unknown"
    monkeypatch.setattr(lookup_mod, "parse_type_page", lambda *a: (_ for _ in ()).throw(ValueError("boom")))
    u = lookup(client, _facts(chassis="AGH30W"), _rule(), {TOPIC_CRUISE})
    assert u.cruise["status"] == "unknown" and u.note.startswith("catalogue lookup failed: boom")
    monkeypatch.undo()
    pages = dict(PAGES)
    none_page = tmp_path / "none.html"
    none_page.write_text(PAGES[f"{BASE}/catalog/toyota/3491/fmc003/62479/"].read_text(encoding="utf-8").replace("標準装備", "なし"), encoding="utf-8")
    pages[f"{BASE}/catalog/toyota/3491/fmc003/62479/"] = none_page
    client2, _ = _client(tmp_path / "m", pages=pages)
    m = lookup(client2, _facts(chassis="AGH30W"), _rule(), {TOPIC_CRUISE})
    assert m.cruise["status"] == "mixed" and m.cruise["grade"] is None


def test_price_mark_reads_as_blank(tmp_path):
    pages = dict(PAGES)
    p = tmp_path / "price.html"
    p.write_text(PAGES[f"{BASE}/catalog/toyota/3491/fmc003/62479/"].read_text(encoding="utf-8").replace("標準装備", "55,000円"), encoding="utf-8")
    pages[f"{BASE}/catalog/toyota/3491/fmc003/62479/"] = p
    pages.pop(f"{BASE}/catalog/toyota/3491/fmc003/67465/")
    client, _ = _client(tmp_path / "p", pages=pages)
    f = lookup(client, _facts(chassis="AGH30W"), _rule(), {TOPIC_CRUISE})
    assert "円" not in json.dumps(f.to_dict(), ensure_ascii=False) and f.cruise["status"] == "unknown"
