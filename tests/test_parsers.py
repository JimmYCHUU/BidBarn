from pathlib import Path

from auction_bot.nichibo.parsers import parse_detail_page, parse_search_page

FIX = Path(__file__).parent / "fixtures"


def test_search_page_cards():
    html = (FIX / "search_gdh206.html").read_text(encoding="utf-8")
    cards, total = parse_search_page(html)
    assert total == 30
    assert len(cards) == 30
    first = cards[0]
    assert first.ref_no == "by9NRDJydGQwZ0dYQXpwOHNsTEVSdzdHOGkyQzVFbmVFVjRrQVNscHFzcz0="
    assert first.year == 2018 and first.make == "Toyota" and first.model == "Hiace"
    assert first.variant_text == "Van 5d 4wd DX"
    assert first.km == 76_000 and first.engine_cc == 2_800 and first.grade_raw == "0"
    assert first.start_price_jpy == 130_000
    assert first.transmission == "IA"
    assert first.sheet_url.startswith("https://autosearch.nichibojapan.com/image.php?img_url=")
    grades = {c.grade_raw for c in cards}
    assert "4" in grades and "3.5" in grades


def test_detail_page_fields():
    html = (FIX / "detail_gdh206v.html").read_text(encoding="utf-8")
    url = "https://autosearch.nichibojapan.com/auction/by9NRDJydGQwZ0dYQXpwOHNsTEVSNjE2b0lURnE5cTBCWGpsWFpsRXUxcz0="
    f = parse_detail_page(html, url)
    assert f.ref_no == "by9NRDJydGQwZ0dYQXpwOHNsTEVSNjE2b0lURnE5cTBCWGpsWFpsRXUxcz0="
    assert f.year == 2020
    assert f.make == "Toyota" and f.model == "Hiace"
    assert f.variant_text == "Van DX GL Package 4WD"
    assert f.chassis == "GDH206V"
    assert f.km == 122_000
    assert f.grade_raw == "4" and f.grade == 4.0
    assert f.start_price_jpy == 1_110_000
    assert f.auction_place == "CAA Gifu" and f.exhibit_no == "5012" and f.auction_date == "20260915"
    assert f.transmission == "AT" and f.colour == "White" and f.engine_cc == 2_800
    assert f.vid == "345545655555167549144"
    assert f.sheet_url.endswith("aHR0cHM6Ly9pbWcuanRoaW5nLmNvbS9jYXJzL2ltYWdlLmFzcD9JRD0xMDAxMjA1NTQxMg==")
    assert len(f.photo_urls) >= 8
    assert f.sheet_url not in f.photo_urls
    assert not f.has_current_bid
    assert not f.images_missing


def test_stock_cards_are_dropped_and_search_excludes_stock():
    from auction_bot.nichibo.search import build_search_url
    html = (FIX / "search_gdh206.html").read_text(encoding="utf-8")
    # Turn the first card into a Nichibo stock vehicle: stock detail link + stock cart button.
    first_href = "https://autosearch.nichibojapan.com/auction/by9NRDJydGQwZ0dYQXpwOHNsTEVSdzdHOGkyQzVFbmVFVjRrQVNscHFzcz0="
    stock_html = html.replace(first_href, first_href.replace("/auction/", "/stock/"), 2).replace(
        'data-vehicletype="auction"', 'data-vehicletype="stock"', 1)
    cards, _ = parse_search_page(stock_html)
    assert len(cards) == 29 and all("/auction/" in c.url for c in cards)
    assert "includeStock=N" in build_search_url("https://autosearch.nichibojapan.com", "GDH206", 2018, 2024, 160_000)


def test_nichibo_url_guard():
    from auction_bot.nichibo.browser import nichibo_url_allowed
    allowed = [
        "https://autosearch.nichibojapan.com/auction?Make=All&Chassis=GDH206",
        "https://autosearch.nichibojapan.com/auction/search",
        "https://autosearch.nichibojapan.com/auction/by9NRDJydGQwZ0dYQXpwOHNsTEVSK3BVRWJ1N1krcGIrLzNpSDB1d2ovND0=",
        "https://autosearch.nichibojapan.com/auction/jsonAuctionSheetData?AuctionSheet=x",
        "https://autosearch.nichibojapan.com/image.php?img_url=abc",
        "https://autosearch.nichibojapan.com/login",
        "https://dashboard.carbarn.com.au/?view=bidding",
        "https://www.cbs.s1.carbarn.com.au/core/api/admin/bidding-guides?page=0",
    ]
    forbidden = [
        "https://autosearch.nichibojapan.com/stock",
        "https://autosearch.nichibojapan.com/stock?Make=All",
        "https://autosearch.nichibojapan.com/stock/abc123",
        "https://autosearch.nichibojapan.com/stock/offer",
        "https://autosearch.nichibojapan.com/stock/cart/abc",
        "https://autosearch.nichibojapan.com/auction/cart",
        "https://autosearch.nichibojapan.com/auction/cart/abc",
        "https://autosearch.nichibojapan.com/auction/bid?v=x&b=1",
        "https://autosearch.nichibojapan.com/auction/update-image-credit?NihonId=1",
        "https://autosearch.nichibojapan.com/vehicleOffers",
        "https://autosearch.nichibojapan.com/account/deposits",
        "https://www.nichibojapan.com/stock/anything",
    ]
    for u in allowed:
        assert nichibo_url_allowed(u)[0], u
    for u in forbidden:
        assert not nichibo_url_allowed(u)[0], u
