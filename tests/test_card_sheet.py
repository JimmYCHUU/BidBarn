"""Judging a lot from the search card and the auction sheet, without its detail page."""
import copy
import json
from pathlib import Path

import pytest

from auction_bot.carbarn.models import parse_guide_tree
from auction_bot.config import Config
from auction_bot.nichibo import sheet_ocr
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.nichibo.parsers import parse_detail_page
from auction_bot.pipeline import card_facts, needs_detail, scan
from auction_bot.rules.hard import card_grid_check
from auction_bot.store import Store
from tests.test_pipeline_offline import FAKE_JPEG, PLACEHOLDER_GIF, FIX, FakeBrowser, cfg  # noqa: F401  (fixture)


def _gdh206():
    tree = json.loads((FIX / "guide_223_tree.json").read_text(encoding="utf-8"))
    item = next(i for i in json.loads((FIX / "guide_list_subset.json").read_text(encoding="utf-8"))["content"] if i["id"] == 223)
    return parse_guide_tree(tree, item)


def _default_variant_snapshot(cfg: Config) -> None:
    """Turn the fixture guide into one with a single unnamed variant (the common case)."""
    snap = cfg.snapshot_dir / "rules_test.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    variants = data["guides"][0]["tree"]["variants"]
    keep = next(v for v in variants if (v.get("name") or "").strip().upper() == "GDH206V")
    keep["name"] = ""
    data["guides"][0]["tree"]["variants"] = [keep]
    snap.write_text(json.dumps(data), encoding="utf-8")


# -- needs_detail -----------------------------------------------------------------

def test_needs_detail_only_for_chassis_variants_or_colour():
    rule = _gdh206()  # GDH206V / GDH206K: the suffix decides the grid
    card = VehicleFacts(ref_no="r", url="u", year=2020, km=100_000, grade_raw="4", start_price_jpy=1_000_000,
                        sheet_url="https://autosearch.nichibojapan.com/image.php?x", source="card")
    need, why = needs_detail(rule, card)
    assert need and "suffix" in why

    plain = copy.deepcopy(rule)
    plain.variants = [plain.variants[0]]
    plain.variants[0].name = ""
    need, why = needs_detail(plain, card)
    assert not need

    # A colour condition anywhere in the guide brings the page back.
    from auction_bot.carbarn.models import Condition, Modifier
    coloured = copy.deepcopy(plain)
    coloured.variants[0].modifiers.append(Modifier(
        id=1, variant_id=None, year_from=None, year_to=None, odo_from=None, odo_to=None,
        conditions=[Condition("COLOUR", "in", "White")], amount_jpy_k=-50, percent=None,
        no_bid=False, strict=False, note="", active=True, stale=False))
    need, why = needs_detail(coloured, card)
    assert need and "colour" in why

    # Missing card facts or no sheet link: open the page.
    assert needs_detail(plain, VehicleFacts(ref_no="r", url="u", year=2020, km=1, grade_raw="4", sheet_url="s"))[0]
    assert needs_detail(plain, VehicleFacts(ref_no="r", url="u", year=2020, km=1, grade_raw="4", start_price_jpy=1))[0]

    facts = card_facts(card, plain)
    assert facts.chassis == "GDH206" and facts.source == "card+sheet" and card.chassis == ""


# -- the scan ---------------------------------------------------------------------

def test_card_sheet_scan_opens_no_detail_pages(cfg, monkeypatch):
    import auction_bot.pipeline as pl
    from auction_bot.nichibo.sheet_ocr import SheetRead
    _default_variant_snapshot(cfg)
    monkeypatch.setattr(pl, "read_sheet", lambda path: SheetRead(available=True, seats=5, model_code="GDH206K", km=None, grade="4"))
    browser = FakeBrowser()
    store = Store(cfg.db_path)
    stats = scan(cfg, browser, store, ["GDH206"], limit_per_model=3)
    assert stats.errors == []
    assert stats.opened == 3 and stats.sheet_only == 3 and stats.pages == 0 and stats.pending == 0
    assert len([u for u in browser.visited if "/auction/" in u and "/auction?" not in u]) == 3  # Jim, 2026-10-26: a review lot opens its page once for the auction day
    assert stats.day_pages == 3
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 3
    for r in rows:
        facts = json.loads(r["facts_json"])
        assert facts["source"] == "card+sheet" and facts["chassis"] == "GDH206" and facts["seats"] == 5
        assert r["bid_jpy_k"]  # the grid was applied from the card's year and km
        soft = json.loads(r["soft_json"])
        assert soft["checklist"][0].startswith("Judged from the search card")
        assert "Seats: the sheet reads 5" in soft["checklist"][1]
        ev = json.loads(r["evidence_json"])
        assert ev["sheet"] and Path(ev["sheet"]).exists() and ev["photos"] == []
        assert (Path(ev["dir"]) / "facts.json").exists() and (Path(ev["dir"]) / "ocr.json").exists()
    # A rescan does not touch the network for lots already pending.
    before = len(browser.visited)
    stats2 = scan(cfg, browser, store, ["GDH206"], limit_per_model=3)
    assert stats2.reused >= 3 and stats2.opened == 3 and len(browser.visited) == before + 3  # Jim, 2026-10-26: only the 3 new review lots open their page (the day); the pending ones cost nothing
    store.close()


def test_suffix_variants_still_open_the_page(cfg):
    browser = FakeBrowser()
    store = Store(cfg.db_path)
    stats = scan(cfg, browser, store, ["GDH206"], limit_per_model=2)
    assert stats.pages == 2 and stats.sheet_only == 0
    assert len([u for u in browser.visited if "/auction/" in u]) == 2
    facts = json.loads(store.list_proposals(decision="pending")[0]["facts_json"])
    assert facts["source"] == "detail" and facts["chassis"] == "GDH206V"
    store.close()


def test_placeholder_sheet_parks_the_lot_without_a_page(cfg):
    browser = FakeBrowser(PLACEHOLDER_GIF)
    store = Store(cfg.db_path)
    stats = scan(cfg, browser, store, ["GDH206"], limit_per_model=2)
    assert stats.pending == 2 and stats.pages == 0 and stats.review == 2
    assert len([u for u in browser.visited if "/auction/" in u and "/auction?" not in u]) == 2  # Jim, 2026-10-26: a review lot opens its page once for the auction day
    rows = store.list_proposals(decision="pending", status="NEEDS_REVIEW")
    assert len(rows) == 2 and all(json.loads(r["soft_json"])["images_pending"] for r in rows)
    # Replay leaves parked lots alone (nothing to re-read yet).
    from auction_bot.pipeline import reevaluate
    st = reevaluate(cfg, store, ["GDH206"], apply=True)
    assert st.changed == 0 and st.applied == 0
    # Images arrive: the next scan opens them (suffix variants need the page) and supersedes the parked rows.
    stats2 = scan(cfg, FakeBrowser(FAKE_JPEG), store, ["GDH206"], limit_per_model=2)
    assert stats2.pages == 2 and stats2.pending == 0
    assert store.conn.execute("SELECT COUNT(*) FROM proposals WHERE decision='superseded'").fetchone()[0] == 2
    store.close()


def test_scan_skips_on_grid_ceiling_before_any_download(cfg):
    """A start price above every possible bid is a card-stage SKIP: no sheet, no page."""
    from auction_bot.nichibo import parsers
    real = parsers.parse_search_page

    def pricey(html):
        cards, total = real(html)
        for c in cards:
            c.start_price_jpy = 99_000_000
        return cards, total

    import auction_bot.nichibo.search as search
    orig = search.parse_search_page
    search.parse_search_page = pricey
    try:
        browser = FakeBrowser()
        store = Store(cfg.db_path)
        stats = scan(cfg, browser, store, ["GDH206"])
        assert stats.opened == 0 and stats.skip == stats.cards
        assert [u for u in browser.visited if "/auction/" in u] == []
        rows = store.list_proposals(decision="pending", status="SKIP", limit=1000)
        assert all(r["stage"] == "card" for r in rows)
        details = [c["detail"] for r in rows for c in json.loads(r["hard_json"])["checks"]]
        assert any("highest possible bid" in d for d in details)
        store.close()
    finally:
        search.parse_search_page = orig


# -- card_grid_check ------------------------------------------------------------------

def test_card_grid_check():
    rule = _gdh206()
    ok = VehicleFacts(ref_no="r", url="u", year=2020, km=125_000, grade_raw="4", start_price_jpy=1_000_000)
    assert card_grid_check(ok, rule) is None
    # 2020 / 120K-130K is 1,160K on V plus the +80 grade bonus: 1,240K is the ceiling for V; K may be higher.
    ceiling = max(v.cell_for(2020, 125_000).bid_jpy_k for v in rule.variants if v.cell_for(2020, 125_000))
    high = VehicleFacts(ref_no="r", url="u", year=2020, km=125_000, grade_raw="4", start_price_jpy=(ceiling + 500) * 1000)
    c = card_grid_check(high, rule)
    assert c is not None and c.name == "start" and "highest possible bid" in c.detail
    # Exactly at the ceiling: not above it, so the lot goes on.
    at = VehicleFacts(ref_no="r", url="u", year=2020, km=125_000, grade_raw="4", start_price_jpy=ceiling * 1000)
    assert card_grid_check(at, rule) is None
    # No cell in any variant for that year.
    none = VehicleFacts(ref_no="r", url="u", year=2010, km=50_000, grade_raw="4", start_price_jpy=1)
    c = card_grid_check(none, rule)
    assert c is not None and c.name == "cell"
    # Unknown year or km: nothing to say.
    assert card_grid_check(VehicleFacts(ref_no="r", url="u", grade_raw="4"), rule) is None


# -- OCR: header band first, whole sheet as fallback --------------------------------------

def _box(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


@pytest.fixture
def sheet_image(tmp_path):
    from PIL import Image
    p = tmp_path / "sheet.jpg"
    Image.new("RGB", (800, 800), "white").save(p)
    return p


def test_read_sheet_crops_first_and_falls_back(sheet_image, monkeypatch):
    calls = []

    def fake_engine(source):
        calls.append(source)
        if isinstance(source, bytes):
            return [(_box(0, 0, 60, 20), "QDF-GDH206K", 0.9)]  # header band: no seat cell read
        return [(_box(0, 0, 60, 20), "乗車定員", 0.9), (_box(0, 22, 30, 20), "10人", 0.9), (_box(0, 60, 60, 20), "42,192km", 0.9)]

    monkeypatch.setattr(sheet_ocr, "engine_status", lambda: (True, "stub"))
    monkeypatch.setattr(sheet_ocr, "_run_engine", fake_engine)
    r = sheet_ocr.read_sheet(sheet_image)
    assert r.available and r.seats == 10 and r.model_code == "GDH206K" and r.km == 42_192
    assert r.region == "top35+full" and len(calls) == 2 and isinstance(calls[0], bytes) and isinstance(calls[1], Path)
    from PIL import Image
    import io
    w, h = Image.open(io.BytesIO(calls[0])).size
    assert w == 800 and h == 280  # top 35 % only


def test_read_sheet_stops_after_the_header_when_seats_are_read(sheet_image, monkeypatch):
    calls = []

    def fake_engine(source):
        calls.append(source)
        return [(_box(0, 0, 60, 20), "乗車定員", 0.9), (_box(0, 22, 30, 20), "6人", 0.9)]

    monkeypatch.setattr(sheet_ocr, "engine_status", lambda: (True, "stub"))
    monkeypatch.setattr(sheet_ocr, "_run_engine", fake_engine)
    r = sheet_ocr.read_sheet(sheet_image)
    assert r.seats == 6 and r.region == "top35" and len(calls) == 1 and r.seconds >= 0


def test_read_sheet_unreadable_image_uses_full_pass(tmp_path, monkeypatch):
    bad = tmp_path / "sheet.jpg"
    bad.write_bytes(b"not an image")
    monkeypatch.setattr(sheet_ocr, "engine_status", lambda: (True, "stub"))
    monkeypatch.setattr(sheet_ocr, "_run_engine", lambda source: [(_box(0, 0, 10, 10), "5人", 0.9)])
    r = sheet_ocr.read_sheet(bad)
    assert r.seats == 5 and r.region == "full"


# -- placement re-verification with a card-judged lot ----------------------------------

class _Loc:
    def __init__(self, count=1, value=""):
        self.n, self.value = count, value

    def count(self):
        return self.n

    def click(self):
        pass

    def fill(self, v):
        self.value = v

    def type(self, v, delay=0):
        self.value += v

    def input_value(self):
        return self.value

    def is_checked(self):
        return False

    def is_visible(self):
        return True


class _BidPage:
    def __init__(self, url, bid):
        self.url = url
        self._locs = {"#bidAmount": _Loc(1, str(bid)), "#bidCallRequest": _Loc(0)}

    def locator(self, sel):
        return self._locs.setdefault(sel, _Loc())

    def wait_for_timeout(self, ms):
        pass


class _BidBrowser(FakeBrowser):
    def __init__(self, bid):
        super().__init__()
        self._bid = bid

    def goto(self, url, wait_until="domcontentloaded"):
        self.visited.append(url)
        self.page = _BidPage(url, self._bid)
        return self.detail_html


def _expected(**kw) -> VehicleFacts:
    html = (FIX / "detail_gdh206v.html").read_text(encoding="utf-8")
    f = parse_detail_page(html, "https://autosearch.nichibojapan.com/auction/x")
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def test_place_bid_accepts_model_code_prefix_for_card_judged_lots(cfg):
    from auction_bot.nichibo.bid import place_bid
    live = _expected()
    bid = max(live.start_price_jpy // 1000, 1)
    # Judged from the card: the stored chassis is the bare model code, the page shows GDH206V.
    res = place_bid(_BidBrowser(bid), cfg, "x", bid, _expected(chassis="GDH206", source="card+sheet"), dry_run=True)
    assert res.ok and res.dry_run and res.message.startswith("dry run")
    # A different model code on the page is still blocked.
    res = place_bid(_BidBrowser(bid), cfg, "x", bid, _expected(chassis="TRH200", source="card+sheet"), dry_run=True)
    assert not res.ok and "does not belong" in res.message
    # Detail-judged lots keep the exact comparison.
    res = place_bid(_BidBrowser(bid), cfg, "x", bid, _expected(chassis="GDH206"), dry_run=True)
    assert not res.ok and "chassis changed" in res.message
