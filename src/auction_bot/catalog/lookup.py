"""Look a lot up in the maker's catalogue: is cruise control standard on its grade, which Welcab seat system does it carry.

Which pages: the lot's model code. A code with an emissions prefix read off the sheet ("3BA-AGH30W") points
at that exact page; otherwise the bare chassis code (Nichibo's, then the guide's) points at every catalogue
page for it, or at the family of codes that start with it. Which grades: a model-code page groups its grades
by "YYYY年M月モデル"; the ones inside the lot's registration year count, plus the newest one before it
(the model that was on sale when the year began). A grade page is read only when cruise control is asked.

What each status means (cruise and adaptive cruise):
  standard / optional / none   every readable candidate grade says so
  mixed                        the candidate grades disagree
  unknown                      nothing readable (no page, catalogue unreachable, topic not asked, blank cells)
Welcab `seat_system` is the system the candidate grades carry; "none" means grades were found but none is a
Welcab, "mixed" means several systems, "unknown" means no grade could be read at all.
A failure is always a finding with status "unknown" and a note, never an exception.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from auction_bot.carbarn.models import ModelRule
from auction_bot.catalog.client import CatalogClient
from auction_bot.catalog.kurumaerabi import Grade, bare_code, mark_state, parse_grade_page, parse_type_page
from auction_bot.judge.topics import TOPIC_CRUISE
from auction_bot.nichibo.facts import VehicleFacts

_NOTE_MAX = 400
MAX_PAGES = 6


def _status(status: str = "unknown", grade: str | None = None, mark: str = "") -> dict:
    return {"status": status, "grade": grade, "mark": mark}


@dataclass
class CatalogFinding:
    model_codes: list[str]
    urls: list[str]
    grades: list[dict]
    cruise: dict
    adaptive: dict
    welcab: dict
    note: str
    fetched_at: str
    source: str = "kurumaerabi.com"
    key: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def unknown(note: str, codes: list[str] | None = None, key: str = "") -> CatalogFinding:
    return CatalogFinding(
        model_codes=list(codes or []), urls=[], grades=[], cruise=_status(), adaptive=_status(),
        welcab={"seat_system": "unknown", "grade": None}, note=note[:_NOTE_MAX], fetched_at=_now(), key=key)


def finding_key(codes: list[str], year: int | None, topics: set[str]) -> str:
    raw = "|".join(sorted(codes)) + "|" + str(year) + "|" + ",".join(sorted(topics))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def candidate_codes(facts: VehicleFacts, rule: ModelRule) -> list[str]:
    ocr = str((facts.sheet_ocr or {}).get("model_code") or "").strip().upper()
    if "-" in ocr and _usable(ocr):
        return [ocr]
    out: list[str] = []
    for code in (str(facts.chassis or "").strip().rstrip("?").strip().upper(), str(rule.model_code or "").strip().upper()):
        if code and code not in out:
            out.append(code)
    return out


def _usable(code: str) -> bool:
    """A model code worth a catalogue search: its bare part has 4 or more characters and a digit."""
    bare = bare_code(code)
    return len(bare) >= 4 and any(ch.isdigit() for ch in bare)


def candidate_sections(sections: list[int], year: int | None) -> list[int]:
    """Sections inside the registration year plus the newest one before it, newest first. No year keeps them all."""
    uniq = sorted(set(sections), reverse=True)
    if year is None:
        return uniq
    start = year * 100 + 1
    inside = [s for s in uniq if start <= s <= year * 100 + 12]
    before = [s for s in uniq if s < start]
    return inside + before[:1]


def _urls_for(code: str, sitemap: dict[str, list[str]]) -> list[str]:
    if not _usable(code):
        return []
    if "-" in code:
        urls = [u for urls in sitemap.values() for u in urls if u.rstrip("/").rsplit("/", 1)[-1].upper() == code]
        return urls or _urls_for(bare_code(code), sitemap)
    if sitemap.get(code):
        return list(sitemap[code])
    return [u for k, urls in sitemap.items() if k.startswith(code) for u in urls]


def _fold(grades: list[Grade], field_name: str, mark_name: str) -> dict:
    """One status over the grades whose page was read (a blank cell counts as the status "unknown"):
    the shared status, else "mixed"; no grade read gives "unknown"."""
    read = [g for g in grades if getattr(g, field_name) is not None]
    states = {getattr(g, field_name) for g in read}
    if not states:
        return _status()
    if len(states) > 1:
        return _status("mixed")
    return _status(states.pop(), read[0].name, getattr(read[0], mark_name))


def _welcab(grades: list[Grade], parsed: int) -> dict:
    systems = {g.seat_system for g in grades if g.seat_system != "not_welcab"}
    if len(systems) == 1:
        system = systems.pop()
        return {"seat_system": system, "grade": next(g.name for g in grades if g.seat_system == system)}
    if systems:
        return {"seat_system": "mixed", "grade": None}
    return {"seat_system": "none" if parsed else "unknown", "grade": None}


def lookup(client: CatalogClient, facts: VehicleFacts, rule: ModelRule, topics: set[str]) -> CatalogFinding:
    codes: list[str] = []
    key = ""
    try:
        codes = candidate_codes(facts, rule)
        key = finding_key(codes, facts.year, topics)
        return _lookup(client, facts, codes, key, topics)
    except Exception as e:  # a surprise in a page must not stop the scan
        return unknown(f"catalogue lookup failed: {e}", codes, key)


def _price_free(mark: str) -> str:
    return "" if "円" in (mark or "") else mark


def _lookup(client: CatalogClient, facts: VehicleFacts, codes: list[str], key: str, topics: set[str]) -> CatalogFinding:
    sitemap = client.sitemap()
    if sitemap is None:
        return unknown("catalogue sitemap unavailable: " + client.errors.get(client.sitemap_url(), "no answer"), codes, key)
    urls: list[str] = []
    for code in codes:
        for u in _urls_for(code, sitemap):
            if u not in urls:
                urls.append(u)
    if not urls:
        return unknown("no catalogue page for " + ", ".join(codes or ["(no model code)"]), codes, key)
    capped = len(urls) > MAX_PAGES
    urls = urls[:MAX_PAGES]

    year = facts.year
    page_grades: list[Grade] = []
    used: list[str] = []
    for url in urls:
        html = client.get(url)
        if html is None:
            continue
        used.append(url)
        page_grades += parse_type_page(html, client.base_url)
    wanted = set(candidate_sections([g.section_ym for g in page_grades if g.section_ym], year))
    grades = [g for g in page_grades if g.section_ym in wanted]

    n_failed = 0
    if TOPIC_CRUISE in topics:
        kept: list[Grade] = []
        for g in grades:
            html = client.get(g.url)
            if html is None:
                n_failed += 1
                kept.append(g)
                continue
            info = parse_grade_page(html)
            g.cruise, g.adaptive = info["cruise"], info["adaptive"]
            g.cruise_mark, g.adaptive_mark = _price_free(info["cruise_mark"]), _price_free(info["adaptive_mark"])
            if "円" in info["cruise_mark"]:
                g.cruise = mark_state("")
            if "円" in info["adaptive_mark"]:
                g.adaptive = mark_state("")
            g.welfare, g.sale_from_ym, g.sale_to_ym = info["welfare"], info["sale_from_ym"], info["sale_to_ym"]
            if year is not None and g.sale_to_ym is not None and g.sale_to_ym < year * 100 + 1:
                continue    # off sale before the registration year began
            kept.append(g)
        grades = kept
        cruise, adaptive = _fold(grades, "cruise", "cruise_mark"), _fold(grades, "adaptive", "adaptive_mark")
    else:
        cruise, adaptive = _status(), _status()

    note = f"{len(used)} page(s), {len(grades)} grade(s), {n_failed} grade pages failed"
    if capped:
        note += f"; capped at {MAX_PAGES} pages"
    if year is None:
        note += "; registration year unknown"
    if TOPIC_CRUISE not in topics:
        note += "; cruise not needed"
    errs: list[str] = []
    for u in urls + [g.url for g in grades]:
        if u in client.errors:
            e = f"{u}: {client.errors[u]}"
            if e not in errs:
                errs.append(e)
    note += "".join("; " + e for e in errs)
    return CatalogFinding(
        model_codes=codes, urls=used, grades=[g.to_dict() for g in grades], cruise=cruise, adaptive=adaptive,
        welcab=_welcab(grades, len(grades)), note=note[:_NOTE_MAX], fetched_at=_now(), key=key)
