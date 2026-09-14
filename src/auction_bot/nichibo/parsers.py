"""HTML parsers for Nichibo AutoSearch pages. Pure functions over HTML strings."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from auction_bot.nichibo.facts import VehicleFacts, parse_int

BASE = "https://autosearch.nichibojapan.com"


def ref_no_from_url(url: str) -> str:
    return urlsplit(url).path.rstrip("/").split("/")[-1]


def parse_search_page(html: str) -> tuple[list[VehicleFacts], int | None]:
    """Return (cards, total_vehicles)."""
    soup = BeautifulSoup(html, "lxml")
    cards: list[VehicleFacts] = []
    for card in soup.select("div.card-item-listings"):
        a = card.select_one("a.preview")
        if not a or not a.get("href"):
            continue
        url = a["href"]
        btn_type = (card.select_one("button.cart-button-list") or {}).get("data-vehicletype", "auction")
        if "/auction/" not in url or str(btn_type).lower() != "auction":
            continue  # Nichibo stock vehicle: never considered
        f = VehicleFacts(ref_no=ref_no_from_url(url), url=url, source="card")
        title = card.select_one(".car-title-h")
        if title:
            m = re.match(r"\s*(\d{4})\s+(\S+)\s+(.*)", title.get_text(" ", strip=True))
            if m:
                f.year, f.make, f.model = int(m.group(1)), m.group(2), m.group(3).strip()
        sub = card.select_one(".title-desc-listing small")
        if sub:
            f.variant_text = sub.get_text(" ", strip=True)
        tr = card.select_one(".transmission-listing small")
        if tr:
            f.transmission = tr.get_text(" ", strip=True)
        text = card.select_one("p.card-text")
        if text:
            t = text.get_text(" ", strip=True)
            km = re.search(r"([\d,]+)\s*km", t, re.I)
            cc = re.search(r"([\d,]+)\s*cc", t, re.I)
            gr = re.search(r"Grade:\s*([^\s]+)", t)
            f.km = parse_int(km.group(1)) if km else None
            f.engine_cc = parse_int(cc.group(1)) if cc else None
            f.grade_raw = gr.group(1) if gr else ""
        price = card.select_one("span[id^=bidPanelPrice]")
        if price:
            f.start_price_jpy = parse_int(price.get_text(" ", strip=True))
        f.sheet_url = a.get("data-auction") or ""
        btn = card.select_one("button.cart-button-list")
        if btn and btn.get("data-refno"):
            f.ref_no = btn["data-refno"]
        cards.append(f)
    total = None
    m = re.search(r"(\d[\d,]*)\s+total vehicles", soup.get_text(" ", strip=True))
    if m:
        total = parse_int(m.group(1))
    return cards, total


_TABLE_KEYS = {
    "year of registration": "year",
    "make": "make",
    "model": "model",
    "variant": "variant_text",
    "auction place": "auction_place",
    "exhibit #": "exhibit_no",
    "km": "km",
    "grade": "grade_raw",
    "colour": "colour",
    "engine": "engine_cc",
    "chassis": "chassis",
    "transmission": "transmission",
    "equipment": "equipment",
}


def parse_detail_page(html: str, url: str) -> VehicleFacts:
    soup = BeautifulSoup(html, "lxml")
    f = VehicleFacts(ref_no=ref_no_from_url(url), url=url, source="detail")

    for row in soup.select("table.table-new tr"):
        cells = row.find_all("td")
        if len(cells) != 2:
            continue
        key = cells[0].get_text(" ", strip=True).lower()
        strong = cells[1].find("strong")
        value = (strong.get_text(" ", strip=True) if strong else cells[1].get_text(" ", strip=True)).strip()
        field = _TABLE_KEYS.get(key)
        if not field:
            continue
        if field in ("year", "km", "engine_cc"):
            setattr(f, field, parse_int(value))
        else:
            setattr(f, field, value)

    m = re.search(r"var\s+refno\s*=\s*'([^']+)'", html)
    if m:
        f.ref_no = m.group(1)
    m = re.search(r"var\s+startPrice\s*=\s*'([\d]+)'", html)
    if m:
        f.start_price_jpy = int(m.group(1))
    m = re.search(r"var\s+dataParams\s*=\s*'(\{.*?\})'", html)
    if m:
        try:
            dp = json.loads(m.group(1))
            f.vid = str(dp.get("vid") or "")
            if not f.chassis:
                f.chassis = dp.get("Chassis") or ""
            if f.km is None:
                f.km = parse_int(dp.get("km"))
            if f.year is None:
                f.year = parse_int(dp.get("Year"))
            if not f.grade_raw:
                f.grade_raw = str(dp.get("Grade") or "")
        except json.JSONDecodeError:
            pass

    # Nichibo marks a guessed chassis suffix with a trailing "?" (e.g. "GDH206K?").
    chassis = (f.chassis or "").strip()
    if chassis.endswith("?"):
        f.chassis = chassis.rstrip("?").strip()
        f.chassis_uncertain = True

    m = re.search(r"AuctionSheet:\s*'([^']+)'.*?AuctionHall:\s*'([^']*)'.*?ExhibitNo:\s*'([^']*)'.*?AuctionDate:\s*'([^']*)'", html, re.S)
    if m:
        f.sheet_url = f.sheet_url or m.group(1)
        f.auction_place = f.auction_place or m.group(2)
        f.exhibit_no = f.exhibit_no or m.group(3)
        f.auction_date = m.group(4)

    sheet = soup.select_one("img#auction-sheet-img")
    if sheet and sheet.get("src"):
        f.sheet_url = sheet["src"]

    seen: set[str] = set()
    for a in soup.select("a.gallery-demo"):
        href = a.get("href")
        if href and href not in seen and href != f.sheet_url:
            seen.add(href)
            f.photo_urls.append(href)

    f.images_missing = soup.select_one("#btn-request-image") is not None and not f.photo_urls

    row = soup.select_one("#currentBidRow")
    if row:
        style = (row.get("style") or "").replace(" ", "").lower()
        amount = soup.select_one("#currentBidAmount")
        amount_val = parse_int(amount.get_text(" ", strip=True)) if amount else None
        if "display:none" not in style and amount_val:
            f.has_current_bid = True
            f.current_bid_jpy = amount_val
    return f


def parse_bid_response(text: str) -> tuple[bool, str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False, text[:200]
    return data.get("type") == "success", str(data.get("message") or data.get("type") or "")
