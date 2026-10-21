"""Parsers for the kurumaerabi.com new-car catalogue (public pages, read by the equipment ladders).

URL shapes:
  /catalog/xml-sitemap/type.xml                     every model-code page (and per-model index pages ending in /type/)
  /catalog/<maker>/<id>/type/<model code>/          the grades sold under one model code, grouped by "YYYY年M月モデル"
  /catalog/<maker>/<id>/fmc<nnn>/<grade id>/        one grade: specifications and equipment

Equipment marks come in two vocabularies. Older pages write 標準装備 / オプション / ◯ / blank;
newer pages write ◯ / なし / メーカーオプション / blank. A blank cell means the catalogue says
nothing, which is "unknown", never "none".

Plain regexes over the HTML; no parser dependency.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from html import unescape

from auction_bot.judge.topics import seat_system

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_STRIP_BLOCKS = re.compile(r"<(style|script)\b.*?</\1>", re.S)
_SECTION_SPLIT = re.compile(r'(?=<section id="section_)')
_LINKBOX_SPLIT = re.compile(r'(?=<section class="chakra-linkbox)')
_GRADE_LINK = re.compile(r'<h3\b[^>]*>\s*<a\b[^>]*href="([^"]+)"[^>]*>\s*<span\b[^>]*>(.*?)</span>', re.S)
_YM = re.compile(r"(\d{4})年\s*(\d{1,2})月")
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_GRADE_TITLE = re.compile(r"(\d{4})年(\d{1,2})月〜(?:(\d{4})年(\d{1,2})月販売終了|販売中)モデル (.+?)の新車カタログ")
_SEATS = re.compile(r"(\d+)\s*名")
_DRIVER_ASSIST = re.compile(r"<h4\b[^>]*>運転支援</h4>")

_STANDARD = {"標準装備", "◯", "○"}
_OPTIONAL = {"オプション", "メーカーオプション", "ディーラーオプション"}
_NONE = {"なし", "ー", "-", "×"}


@dataclass
class Grade:
    name: str
    url: str
    model_code: str
    seats: int | None
    section_ym: int | None
    seat_system: str
    welfare: bool | None = None
    cruise: str | None = None
    adaptive: str | None = None
    cruise_mark: str = ""
    adaptive_mark: str = ""
    sale_from_ym: int | None = None
    sale_to_ym: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def bare_code(code_segment: str) -> str:
    """The distinguishing part of a model code: "3BA-AGH30W" -> "AGH30W", "DBA-ANH10W-PRASK" -> "ANH10W"."""
    parts = [p for p in code_segment.split("-") if p]
    if not parts:
        return ""
    return (parts[1] if len(parts) > 1 else parts[0]).upper()


def parse_type_sitemap(xml: str) -> dict[str, list[str]]:
    """Bare model code (upper case) -> absolute model-code page URLs; the ".../type/" index pages are skipped."""
    out: dict[str, list[str]] = {}
    for url in _LOC.findall(xml):
        m = re.search(r"/type/([^/]+)/$", url)
        if not m:
            continue
        code = bare_code(m.group(1))
        if code:
            out.setdefault(code, []).append(url)
    return out


def ym(text: str) -> int | None:
    """"2021年5月" -> 202105."""
    m = _YM.search(text or "")
    return int(m.group(1)) * 100 + int(m.group(2)) if m else None


def _clean(fragment: str) -> str:
    return " ".join(unescape(_TAG.sub("", fragment)).split())


def dd(html: str, label: str) -> str | None:
    """The <dd> that follows <dt>label</dt>, tags stripped and whitespace collapsed; None when the label is absent."""
    m = re.search(r"<dt[^>]*>\s*" + re.escape(label) + r"\s*</dt>\s*<dd[^>]*>(.*?)</dd>", html, re.S)
    return _clean(m.group(1)) if m else None


def mark_state(mark: str) -> str:
    m = (mark or "").strip()
    if m in _STANDARD:
        return "standard"
    if m in _OPTIONAL:
        return "optional"
    if m in _NONE:
        return "none"
    return "unknown"


def _seats(text: str | None) -> int | None:
    m = _SEATS.search(text or "")
    return int(m.group(1)) if m else None


def parse_type_page(html: str, base_url: str) -> list[Grade]:
    """Every grade listed on a model-code page, with the model's month heading as `section_ym`."""
    html = _STRIP_BLOCKS.sub("", html)
    title = _TITLE.search(html)
    cm = re.search(r"型式：([^（\s]+)", title.group(1)) if title else None
    model_code = cm.group(1) if cm else ""
    grades: list[Grade] = []
    for section in _SECTION_SPLIT.split(html):
        if not section.startswith('<section id="section_'):
            continue
        boxes = _LINKBOX_SPLIT.split(section)
        section_ym = ym(_clean(boxes[0]))
        for box in boxes[1:]:
            box = box.split("</dl>", 1)[0]
            link = _GRADE_LINK.search(box)
            if not link:
                continue
            name = _clean(link.group(2))
            grades.append(Grade(
                name=name, url=base_url.rstrip("/") + link.group(1), model_code=model_code,
                seats=_seats(dd(box, "定員")), section_ym=section_ym, seat_system=seat_system(name)))
    return grades


def parse_grade_page(html: str) -> dict:
    """Facts from one grade page: name and sale period from the title, welfare flag, seats, cruise marks."""
    html = _STRIP_BLOCKS.sub("", html)
    t = _TITLE.search(html)
    tm = _GRADE_TITLE.search(unescape(t.group(1))) if t else None
    welfare_raw = dd(html, "福祉車両")
    welfare = None if welfare_raw is None else (True if mark_state(welfare_raw) == "standard" else False)
    block = html
    da = _DRIVER_ASSIST.search(html)
    if da:
        block = html[da.end():]
    cruise_mark = (dd(block, "クルーズコントロール") or "") if da else ""
    adaptive_mark = (dd(block, "アダプティブクルーズコントロール") or "") if da else ""
    return {
        "name": tm.group(5).strip() if tm else None,
        "model_code": dd(html, "型式"),
        "seats": _seats(dd(html, "乗車定員")),
        "welfare": welfare,
        "cruise_mark": cruise_mark,
        "adaptive_mark": adaptive_mark,
        "cruise": mark_state(cruise_mark),
        "adaptive": mark_state(adaptive_mark),
        "sale_from_ym": int(tm.group(1)) * 100 + int(tm.group(2)) if tm else None,
        "sale_to_ym": int(tm.group(3)) * 100 + int(tm.group(4)) if tm and tm.group(3) else None,
    }
