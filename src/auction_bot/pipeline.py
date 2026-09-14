"""Scan: search Nichibo per bidding guide, evaluate, write proposals.

`evaluate_vehicle` is a pure function over the facts and the saved evidence, so the
same logic serves the live scan and `reevaluate` (offline replay after rule changes).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from auction_bot.carbarn.client import load_rules, snapshot_age_hours
from auction_bot.carbarn.models import ModelRule, rule_to_summary
from auction_bot.config import Config, cache_dir
from auction_bot.nichibo.browser import Browser
from auction_bot.nichibo.cache import NoCache, PageCache
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.nichibo.search import search_rule
from auction_bot.nichibo.sheet_ocr import read_sheet
from auction_bot.nichibo.vehicle import _safe, download_evidence, fetch_facts, load_saved_evidence
from auction_bot.rules.hard import NEEDS_REVIEW, PASS, SKIP, Check, allowed_seats, card_prefilter, evaluate_hard, override_lines
from auction_bot.rules.hints import keyword_hints
from auction_bot.store import Store

log = logging.getLogger("auction_bot.scan")


@dataclass
class ScanStats:
    guides: int = 0
    cards: int = 0
    opened: int = 0
    model_calls: int = 0
    bid: int = 0
    skip: int = 0
    review: int = 0
    reused: int = 0
    cache_hits: int = 0
    errors: list[str] = field(default_factory=list)


def summary_line(f: VehicleFacts) -> str:
    return f"{f.year} {f.make} {f.model} {f.variant_text} | {f.chassis} | {f.km:,} km | grade {f.grade_raw} | start {f.start_price_jpy}"


def _name_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def model_matches(rule: ModelRule, facts: VehicleFacts) -> bool:
    """Same make and same model family as the guide (Alphard vs Vellfire both use ANH20W).

    Compares the first word of each name so 'Hijet Campervan' matches 'Hijet Truck'
    and 'LS460' matches 'LS'. Unknown names never block.
    """
    gm, nm = _name_tokens(rule.make), _name_tokens(facts.make)
    if gm and nm and gm[0] != nm[0] and not (gm[0].startswith(nm[0]) or nm[0].startswith(gm[0])):
        return False
    g, n = _name_tokens(rule.model), _name_tokens(facts.model)
    if not g or not n:
        return True
    return g[0] == n[0] or g[0].startswith(n[0]) or n[0].startswith(g[0])


def card_check(rule: ModelRule, card: VehicleFacts, cfg: Config) -> Check | None:
    """Cheap rejection from the search card alone (model family + grade/year/odo)."""
    if not model_matches(rule, card):
        return Check("model", False, f"Nichibo lists it as {card.make} {card.model}, guide is {rule.make} {rule.model}")
    return card_prefilter(card, rule) if cfg.open_details_only_for_candidates else None


def _should_skip_existing(store: Store, ref_no: str, rule: ModelRule, rescan: bool) -> bool:
    if store.already_bid(ref_no):
        return True
    prev = store.latest_proposal(ref_no, rule.guide_id)
    if prev is None or rescan:
        return False
    if prev["decision"] != "pending":
        return True  # a human already decided on this vehicle
    if prev["status"] in ("BID", "NEEDS_REVIEW"):
        try:
            pending_images = bool(json.loads(prev["soft_json"] or "{}").get("images_pending"))
        except json.JSONDecodeError:
            pending_images = False
        return not pending_images  # waiting for review, unless we are waiting for Nichibo's images
    return prev["rule_updated_at"] == rule.updated_at  # SKIP under identical rules


def scan(cfg: Config, browser: Browser, store: Store, model_codes: list[str] | None = None,
         snapshot: Path | None = None, no_model: bool | None = None, rescan: bool = False,
         limit_per_model: int | None = None, fresh: bool = False) -> ScanStats:
    if no_model is None:
        no_model = (cfg.sheet_check or "none").lower() == "none"
    rules, snap_path = load_rules(cfg, snapshot, model_codes)
    age = snapshot_age_hours(snap_path)
    if age > cfg.max_rules_age_hours:
        raise RuntimeError(f"Rules snapshot {snap_path.name} is {age:.1f}h old (limit {cfg.max_rules_age_hours}h). Run `bot fetch-rules`.")
    cache = NoCache() if fresh else PageCache(cache_dir(cfg), cfg.cache_hours)
    stats = ScanStats()
    for rule in rules:
        if not rule.active or not rule.active_variants:
            continue
        stats.guides += 1
        log.info("== %s %s %s: years %s, odo<=%s, min grade %s", rule.model_code, rule.make, rule.model,
                 rule.search_years, rule.search_max_odo, rule.min_grade)
        try:
            cards = search_rule(browser, cfg, rule, cache)
        except Exception as exc:
            stats.errors.append(f"{rule.model_code}: search failed: {exc}")
            log.exception("search failed for %s", rule.model_code)
            continue
        stats.cards += len(cards)
        log.info("   %d cards", len(cards))
        opened = 0
        for card in cards:
            if _should_skip_existing(store, card.ref_no, rule, rescan):
                stats.reused += 1
                continue
            store.upsert_vehicle(card.ref_no, rule.model_code, card.to_dict())
            pre = card_check(rule, card, cfg)
            if pre is not None:
                store.supersede_pending(card.ref_no, rule.guide_id)
                store.add_proposal(ref_no=card.ref_no, guide_id=rule.guide_id, model_code=rule.model_code,
                                   snapshot=snap_path.name, rule_updated_at=rule.updated_at, status=SKIP, stage="card",
                                   summary=summary_line(card), facts_json=card.to_dict(),
                                   hard_json={"checks": [pre.__dict__]})
                stats.skip += 1
                continue
            if limit_per_model is not None and opened >= limit_per_model:
                break
            opened += 1
            stats.opened += 1
            try:
                facts = fetch_facts(browser, cfg, card.ref_no, cache)
                facts.ref_no = card.ref_no  # the card's reference is the vehicle's identity everywhere
                store.upsert_vehicle(card.ref_no, rule.model_code, facts.to_dict())
                proposal = evaluate_vehicle(
                    cfg, rule, facts, no_model,
                    evidence_loader=lambda f=facts: download_evidence(browser, cfg, f, cfg.max_photos_to_model, cache),
                    past_decisions=store.past_decisions(rule.model_code) if not no_model else [],
                    stats=stats,
                )
                store.supersede_pending(card.ref_no, rule.guide_id)
                store.add_proposal(snapshot=snap_path.name, rule_updated_at=rule.updated_at, **proposal)
                _count(stats, proposal["status"])
            except Exception as exc:
                stats.errors.append(f"{rule.model_code} {card.ref_no}: {exc}")
                log.exception("candidate failed %s", card.ref_no)
    stats.cache_hits = cache.hits
    return stats


def _ocr_hints(ocr, facts: VehicleFacts) -> list[dict]:
    hints: list[dict] = []
    if ocr.model_code and facts.chassis and facts.chassis.upper() not in ocr.model_code.upper():
        hints.append({"level": "warn", "message": f"sheet model code reads {ocr.model_code}, listing says {facts.chassis}", "where": "sheet OCR"})
    if ocr.km is not None and facts.km and abs(ocr.km - facts.km) > max(1_500, int(facts.km * 0.03)):
        hints.append({"level": "warn", "message": f"sheet odometer reads {ocr.km:,} km, listing says {facts.km:,} km", "where": "sheet OCR"})
    if ocr.grade and facts.grade_raw and ocr.grade != facts.grade_raw:
        hints.append({"level": "warn", "message": f"sheet grade reads {ocr.grade}, listing says {facts.grade_raw}", "where": "sheet OCR"})
    return hints


def evaluate_vehicle(cfg: Config, rule: ModelRule, facts: VehicleFacts, no_model: bool,
                     evidence_loader: Callable[[], dict], past_decisions: list[dict] | None = None,
                     stats: ScanStats | None = None) -> dict:
    """Decide one vehicle. Returns the proposal row fields (without snapshot/rule_updated_at)."""
    base = dict(ref_no=facts.ref_no, guide_id=rule.guide_id, model_code=rule.model_code, summary=summary_line(facts))
    hard = evaluate_hard(facts, rule)
    log.info("   %s -> hard %s %s", base["summary"], hard.status, "; ".join(hard.reasons)[:160])
    if hard.status != PASS:
        status = NEEDS_REVIEW if hard.status == NEEDS_REVIEW else SKIP
        return dict(base, status=status, stage="hard", bid_jpy_k=hard.bid_jpy_k, base_jpy_k=hard.base_jpy_k,
                    facts_json=facts.to_dict(), hard_json=hard.to_dict())

    evidence = evidence_loader()
    out_dir = Path(evidence["dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rule.json").write_text(json.dumps(rule_to_summary(rule), indent=1, ensure_ascii=False), encoding="utf-8")

    # Read the printed fields of the sheet locally (seats, model code, km, grade, 4WD).
    ocr = None
    ocr_hints: list[dict] = []
    if evidence.get("sheet"):
        ocr = read_sheet(Path(evidence["sheet"]))
        facts.sheet_ocr = ocr.to_dict()
        (out_dir / "ocr.json").write_text(json.dumps(ocr.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
        if ocr.available:
            if ocr.seats is not None:
                facts.seats = ocr.seats
            ocr_hints = _ocr_hints(ocr, facts)
            hard = evaluate_hard(facts, rule)  # seat limits and seat modifiers can now be decided
            log.info("   sheet OCR: seats=%s model=%s km=%s grade=%s -> %s", ocr.seats, ocr.model_code, ocr.km, ocr.grade, hard.status)
            if hard.status == SKIP:
                return dict(base, status=SKIP, stage="sheet", bid_jpy_k=hard.bid_jpy_k, base_jpy_k=hard.base_jpy_k,
                            facts_json=facts.to_dict(), hard_json=hard.to_dict(), evidence_json=evidence,
                            soft_json={"mode": "manual", "ocr": ocr.to_dict(), "hints": ocr_hints,
                                       "reasons": ["skipped from the auction sheet: " + "; ".join(hard.reasons)]})

    if no_model:
        # Route 1: the reviewer judges the sheet. Give them the checklist and keyword hints.
        checklist = list(rule.text_rules) + override_lines(rule)
        hints = ocr_hints + keyword_hints(facts, rule.text_rules)
        seat_rule = allowed_seats(rule)
        if seat_rule is not None:
            allowed_txt = ", ".join(map(str, sorted(seat_rule)))
            if facts.seats is not None:
                checklist.insert(0, f"Seats: the sheet reads {facts.seats} (rule allows {allowed_txt}); confirm on the sheet")
            else:
                checklist.insert(0, f"Seats: read 乗車定員 on the sheet (rule allows {allowed_txt})")
        if hard.variant_how in ("single", "trim", "chassis-uncertain"):
            variant_check = next((c.detail for c in hard.checks if c.name == "variant"), "")
            checklist.insert(0, f"Confirm the variant: {variant_check}")
        for t in hard.toggles:
            checklist.append(f"Dashboard chip, apply by changing the amount if it fits: {t}")
        reasons = ["sheet and photos to be judged by the reviewer (sheet_check: none)"]
        if hard.deferred_modifiers:
            reasons.append("modifier needs your judgement: " + "; ".join(hard.deferred_modifiers))
        images_pending = bool(evidence.get("images_pending"))
        if images_pending:
            reasons.append("Nichibo has not published the sheet and photos yet (placeholder images); "
                           "the next scan re-checks this vehicle automatically")
        elif not evidence.get("sheet"):
            reasons.append("auction sheet image could not be downloaded; open it on Nichibo")
        soft = {"status": NEEDS_REVIEW, "reasons": reasons, "checklist": checklist, "hints": hints, "mode": "manual",
                "images_pending": images_pending, "ocr": ocr.to_dict() if ocr else None}
        (out_dir / "hard.json").write_text(json.dumps(hard.to_dict(), indent=1), encoding="utf-8")
        return dict(base, status=NEEDS_REVIEW, stage="manual", bid_jpy_k=hard.bid_jpy_k, base_jpy_k=hard.base_jpy_k,
                    facts_json=facts.to_dict(), hard_json=hard.to_dict(), evidence_json=evidence, soft_json=soft)

    from auction_bot.rules.soft import evaluate_soft  # optional path (route 2, needs an API key)
    if stats is not None:
        stats.model_calls += 1
    soft = evaluate_soft(cfg, facts, rule, evidence, past_decisions or [])
    # Re-run hard rules with facts learned from the sheet (seat-count modifiers etc.).
    final_hard = evaluate_hard(facts, rule, final=True)
    if final_hard.status == PASS and soft.status == PASS:
        status = "BID"
    elif SKIP in (final_hard.status, soft.status):
        status = SKIP
    else:
        status = NEEDS_REVIEW
    log.info("   soft %s %s -> %s bid %s", soft.status, "; ".join(soft.reasons)[:160], status, final_hard.bid_jpy_k)
    (out_dir / "hard.json").write_text(json.dumps(final_hard.to_dict(), indent=1), encoding="utf-8")
    (out_dir / "soft.json").write_text(json.dumps(soft.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
    return dict(base, status=status, stage="soft", bid_jpy_k=final_hard.bid_jpy_k, base_jpy_k=final_hard.base_jpy_k,
                facts_json=facts.to_dict(), hard_json=final_hard.to_dict(), soft_json=soft.to_dict(), evidence_json=evidence)


def _count(stats: ScanStats, status: str) -> None:
    if status == "BID":
        stats.bid += 1
    elif status == SKIP:
        stats.skip += 1
    else:
        stats.review += 1


@dataclass
class ReplayStats:
    checked: int = 0
    unchanged: int = 0
    changed: int = 0
    needs_fetch: int = 0
    applied: int = 0
    changes: list[str] = field(default_factory=list)


def reevaluate(cfg: Config, store: Store, model_codes: list[str] | None = None, apply: bool = False,
               snapshot: Path | None = None) -> ReplayStats:
    """Re-run the current rules over stored pending proposals without touching Nichibo.

    Uses the facts saved with each proposal and the evidence already on disk (sheet OCR is
    re-run from the saved image). A proposal whose outcome changes is superseded by a new
    one when `apply` is True. A vehicle that now needs pages or images we never downloaded
    is marked so the next scan fetches it.
    """
    rules, snap_path = load_rules(cfg, snapshot, model_codes)
    by_guide = {r.guide_id: r for r in rules}
    by_code = {r.model_code: r for r in rules}
    no_model = (cfg.sheet_check or "none").lower() == "none"
    wanted = {c.upper() for c in model_codes} if model_codes else None
    st = ReplayStats()
    rows = store.list_proposals(decision="pending", model_code=None, limit=100_000)
    for row in rows:
        rule = by_guide.get(row["guide_id"]) or by_code.get(row["model_code"])
        if rule is None or (wanted and rule.model_code.upper() not in wanted):
            continue
        st.checked += 1
        facts = VehicleFacts.from_dict(json.loads(row["facts_json"] or "{}"))
        old = (row["status"], row["bid_jpy_k"])
        label = f"#{row['id']} {row['model_code']} {(row['summary'] or '')[:60]}"
        if row["stage"] == "card":
            if card_check(rule, facts, cfg) is not None:
                st.unchanged += 1
                continue
            # Passes the card checks now, but the detail page was never opened.
            st.needs_fetch += 1
            st.changes.append(f"{label}: card SKIP -> needs the detail page (next scan opens it)")
            if apply:
                store.supersede_pending(row["ref_no"], rule.guide_id)
                store.add_proposal(ref_no=row["ref_no"], guide_id=rule.guide_id, model_code=rule.model_code,
                                   snapshot=snap_path.name, rule_updated_at=rule.updated_at, status=NEEDS_REVIEW,
                                   stage="card", summary=row["summary"], facts_json=facts.to_dict(),
                                   hard_json={"checks": []},
                                   soft_json={"mode": "manual", "images_pending": True, "checklist": [], "hints": [],
                                              "reasons": ["passes the card checks under the current rules; the next scan opens the page"]})
                st.applied += 1
            continue
        saved = load_saved_evidence(cfg, row["ref_no"])
        out_dir = cfg.evidence_dir / _safe(row["ref_no"])
        missing = {"dir": str(out_dir), "sheet": None, "photos": [], "failed": [], "placeholders": 1, "images_pending": True}
        proposal = evaluate_vehicle(cfg, rule, facts, no_model, evidence_loader=lambda s=saved, m=missing: s or m)
        new = (proposal["status"], proposal["bid_jpy_k"])
        if new == old:
            st.unchanged += 1
            continue
        st.changed += 1
        st.changes.append(f"{label}: {old[0]} {old[1]} -> {new[0]} {new[1]}")
        if apply:
            store.supersede_pending(row["ref_no"], rule.guide_id)
            store.add_proposal(snapshot=snap_path.name, rule_updated_at=rule.updated_at, **proposal)
            st.applied += 1
    return st
