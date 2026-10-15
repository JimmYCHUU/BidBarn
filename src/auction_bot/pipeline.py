"""Scan: search Nichibo per bidding guide, evaluate, write proposals.

`evaluate_vehicle` is a pure function over the facts and the saved evidence, so the
same logic serves the live scan and `reevaluate` (offline replay after rule changes).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from auction_bot import progress
from auction_bot.carbarn.client import load_rules, snapshot_age_hours
from auction_bot.carbarn.models import ModelRule, rule_to_summary
from auction_bot.config import Config, cache_dir
from auction_bot.nichibo.browser import Browser, NotLoggedIn
from auction_bot.nichibo.cache import NoCache, PageCache
from auction_bot.progress import Cancelled
from auction_bot.nichibo.facts import VehicleFacts
from auction_bot.nichibo.search import search_rule
from auction_bot.nichibo.sheet_ocr import read_sheet
from auction_bot.nichibo.vehicle import _safe, download_evidence, fetch_facts, load_saved_evidence
from auction_bot.rules.hard import (NEEDS_REVIEW, PASS, SKIP, Check, allowed_seats, card_grid_check, card_prefilter,
                                    evaluate_hard)
from auction_bot.rules.hints import keyword_hints
from auction_bot.rules.instructions import detail_fields_used, instruction_check, split_override_lines
from auction_bot.judge.requirements import needs_judge, needs_photos, odd_flags, requirements_for, requirements_hash
from auction_bot.store import Store

log = logging.getLogger("auction_bot.scan")


@dataclass
class ScanStats:
    guides: int = 0
    cards: int = 0
    opened: int = 0  # candidates taken past the card stage
    pages: int = 0  # detail pages actually loaded
    sheet_only: int = 0  # judged from the search card and the sheet, no page
    pending: int = 0  # sheet not published yet, no page
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
    """Cheap rejection from the search card alone (model family, grade/year/odo, grid ceiling vs start)."""
    if not model_matches(rule, card):
        return Check("model", False, f"Nichibo lists it as {card.make} {card.model}, guide is {rule.make} {rule.model}")
    hit = instruction_check(rule, card)  # "skip: ..." standing instructions decidable from the card
    if hit is not None:
        return hit
    if not cfg.open_details_only_for_candidates:
        return None
    return card_prefilter(card, rule) or card_grid_check(card, rule)


def needs_detail(rule: ModelRule, card: VehicleFacts) -> tuple[bool, str]:
    """Must the lot's detail page be opened, or do the search card and the sheet suffice?

    The card carries year, km, grade, start price, variant text and the sheet link. The
    page is needed only for the chassis suffix (guides whose variants are chassis codes),
    for colour conditions, or when the card is missing a fact the rules need.
    """
    code = rule.model_code.upper()
    if any(not v.is_default and v.name.strip().upper().startswith(code) for v in rule.variants):
        return True, "the guide's variants differ by chassis suffix"
    for v in rule.variants:
        for m in v.modifiers:
            if m.active and not m.stale and any(c.attr.upper() in ("COLOR", "COLOUR") for c in m.conditions):
                return True, "a modifier depends on the colour"
    if detail_fields_used(split_override_lines(rule)[1]):
        return True, "a standing instruction needs the listing page"
    if card.year is None or card.km is None or card.grade is None or card.start_price_jpy is None:
        return True, "the search card lacks year, km, grade or start price"
    if not card.sheet_url:
        return True, "the search card has no auction sheet link"
    return False, "default variant; every fact the rules need is on the search card"


def card_facts(card: VehicleFacts, rule: ModelRule) -> VehicleFacts:
    """Facts for a lot judged without its detail page: the chassis is the bare model code."""
    return replace(card, chassis=rule.model_code, chassis_uncertain=False, source="card+sheet",
                   photo_urls=list(card.photo_urls))


def pending_images_proposal(rule: ModelRule, card: VehicleFacts, evidence: dict) -> dict:
    """Nichibo still serves placeholder images: park the lot until the next scan, no page opened.

    The card already carries year, odometer and grade, so the grid amount is worked out and stored
    with the parked lot (Jim, 2026-09-30: such a lot must be approvable at its grid value); the
    sheet and photos, when they arrive, can only move it to SKIP or change the modifiers."""
    hard = evaluate_hard(card_facts(card, rule), rule)
    return dict(ref_no=card.ref_no, guide_id=rule.guide_id, model_code=rule.model_code, summary=summary_line(card),
                status=NEEDS_REVIEW, stage="manual", facts_json=card.to_dict(), hard_json=hard.to_dict(),
                bid_jpy_k=hard.bid_jpy_k, base_jpy_k=hard.base_jpy_k,
                evidence_json=evidence,
                soft_json={"mode": "manual", "images_pending": True, "checklist": [], "hints": [],
                           "reasons": ["Nichibo has not published the sheet and photos yet (placeholder images); "
                                       "the next scan re-checks this vehicle automatically"]})


def _should_skip_existing(store: Store, ref_no: str, rule: ModelRule, rescan: bool) -> bool:
    if store.already_bid(ref_no):
        return True
    prev = store.latest_proposal(ref_no, rule.guide_id)
    if prev is None or rescan:
        return False
    if prev["decision"] in ("deleted", "superseded"):
        return False  # out of the review without a decision, or a stop forced between two writes: check it again
    if prev["decision"] != "pending":
        return True  # a human already decided on this vehicle
    if prev["status"] in ("BID", "NEEDS_REVIEW"):
        try:
            pending_images = bool(json.loads(prev["soft_json"] or "{}").get("images_pending"))
        except json.JSONDecodeError:
            pending_images = False
        return not pending_images  # waiting for review, unless we are waiting for Nichibo's images
    return prev["rule_updated_at"] == rule.updated_at  # SKIP under identical rules


def _deleted_soft(store: Store, ref_no: str, guide_id: int | None) -> dict | None:
    """The judgement of a lot Jim deleted from the review, so it is reused when nothing changed (no new model call)."""
    prev = store.latest_proposal(ref_no, guide_id)
    if prev is None or prev["decision"] != "deleted":
        return None
    try:
        soft = json.loads(prev["soft_json"] or "{}")
    except json.JSONDecodeError:
        return None
    return soft if isinstance(soft, dict) else None


def scan(cfg: Config, browser: Browser, store: Store, model_codes: list[str] | None = None,
         snapshot: Path | None = None, no_model: bool | None = None, rescan: bool = False,
         limit_per_model: int | None = None, fresh: bool = False) -> ScanStats:
    if no_model is None:
        no_model = (cfg.sheet_check or "none").lower() == "none"
    judge_client = None
    if not no_model and judge_mode(cfg):
        from auction_bot.judge.client import make_client
        judge_client = make_client(cfg, store)
    rules, snap_path = load_rules(cfg, snapshot, model_codes)
    age = snapshot_age_hours(snap_path)
    if age > cfg.max_rules_age_hours:
        raise RuntimeError(f"Rules snapshot {snap_path.name} is {age:.1f}h old (limit {cfg.max_rules_age_hours}h). Run `bot fetch-rules`.")
    cache = NoCache() if fresh else PageCache(cache_dir(cfg), cfg.cache_hours)
    stats = ScanStats()
    guides = [r for r in rules if r.active and r.active_variants]  # the ones that are actually searched
    codes = [r.model_code for r in guides]
    marks: list[str] = []  # one letter per finished guide: f lots for review, e nothing new, x an error
    progress.emit(stage="scan", guides=len(guides), guide=0, codes=codes, marks="")
    told = [0.0]
    run = progress.run_id()  # the dashboard run, so the review can show and delete the lots by session
    for n, rule in enumerate(guides, 1):
        progress.check_cancel()
        stats.guides += 1

        def report(quiet: bool = False, n=n, rule=rule) -> None:
            """The running totals for the dashboard. `quiet`: at most twice a second (cards decided without a download)."""
            if quiet and time.monotonic() - told[0] < 0.5:
                return
            told[0] = time.monotonic()
            progress.emit(stage="scan", guides=len(guides), guide=n, code=rule.model_code, codes=codes,
                          marks="".join(marks), **_scan_counters(stats))

        before = (stats.review + stats.bid, len(stats.errors))

        def close_guide(before=before) -> None:
            marks.append("x" if len(stats.errors) > before[1] else ("f" if stats.review + stats.bid > before[0] else "e"))

        report()
        log.info("== %s %s %s: years %s, odo<=%s, min grade %s", rule.model_code, rule.make, rule.model,
                 rule.search_years, rule.search_max_odo, rule.min_grade)
        for err in split_override_lines(rule)[2]:
            log.warning("   %s: standing instruction not understood, ignored: %s", rule.model_code, err)
        try:
            cards = search_rule(browser, cfg, rule, cache)
        except (NotLoggedIn, Cancelled):
            raise  # signed out or stopped: the run ends here, not once per guide
        except Exception as exc:
            stats.errors.append(f"{rule.model_code}: search failed: {exc}")
            log.exception("search failed for %s", rule.model_code)
            close_guide()
            report()
            continue
        stats.cards += len(cards)
        log.info("   %d cards", len(cards))
        report()
        opened = 0
        for card in cards:
            progress.check_cancel()
            if _should_skip_existing(store, card.ref_no, rule, rescan):
                stats.reused += 1
                report(quiet=True)
                continue
            store.upsert_vehicle(card.ref_no, rule.model_code, card.to_dict())
            pre = card_check(rule, card, cfg)
            if pre is not None:
                store.supersede_pending(card.ref_no, rule.guide_id)
                store.add_proposal(ref_no=card.ref_no, guide_id=rule.guide_id, model_code=rule.model_code,
                                   snapshot=snap_path.name, rule_updated_at=rule.updated_at, status=SKIP, stage="card",
                                   summary=summary_line(card), facts_json=card.to_dict(),
                                   hard_json={"checks": [pre.__dict__]}, run_id=run)
                stats.skip += 1
                report(quiet=True)
                continue
            if limit_per_model is not None and opened >= limit_per_model:
                break
            opened += 1
            stats.opened += 1
            t0 = time.time()
            try:
                # 1. The sheet, straight from the card's own link: no page load. A placeholder
                #    means Nichibo has not published the images yet, so the lot is parked.
                photos = 0 if no_model else cfg.max_photos_to_model
                evidence = download_evidence(browser, cfg, card, photos, cache)
                t_sheet = time.time() - t0
                if evidence.get("images_pending"):
                    stats.pending += 1
                    store.supersede_pending(card.ref_no, rule.guide_id)
                    store.add_proposal(snapshot=snap_path.name, rule_updated_at=rule.updated_at, run_id=run,
                                       **pending_images_proposal(rule, card, evidence))
                    stats.review += 1
                    log.info("   %s -> images not published yet | sheet %.1fs, no page opened", summary_line(card), t_sheet)
                    report()
                    continue
                # 2. The detail page only when a rule needs something the card does not carry.
                need, why = needs_detail(rule, card)
                t1 = time.time()
                if need:
                    facts = fetch_facts(browser, cfg, card.ref_no, cache)
                    facts.ref_no = card.ref_no  # the card's reference is the vehicle's identity everywhere
                    stats.pages += 1
                else:
                    facts = card_facts(card, rule)
                    stats.sheet_only += 1
                t_page = time.time() - t1
                store.upsert_vehicle(card.ref_no, rule.model_code, facts.to_dict())

                def load_evidence(f=facts, ev=evidence, c=card):
                    if f.sheet_url and f.sheet_url != c.sheet_url:  # the page named a different sheet
                        return download_evidence(browser, cfg, f, photos, cache)
                    return ev

                def load_photos(f, c=card):
                    """Judge mode: a lot whose requirements need the photos gets its page opened now."""
                    full = f if f.source == "detail" else fetch_facts(browser, cfg, c.ref_no, cache)
                    if full is not f:
                        full.ref_no = c.ref_no
                        full.seats, full.sheet_ocr = f.seats, f.sheet_ocr
                        stats.pages += 1
                    ev = download_evidence(browser, cfg, full, judge_photos(cfg), cache)
                    return full, ev

                proposal = evaluate_vehicle(
                    cfg, rule, facts, no_model, evidence_loader=load_evidence,
                    past_decisions=store.past_decisions(rule.model_code) if not no_model else [],
                    stats=stats, judge_client=judge_client, store=store, photo_loader=load_photos,
                    previous_soft=_deleted_soft(store, card.ref_no, rule.guide_id),
                )
                store.supersede_pending(card.ref_no, rule.guide_id)
                store.add_proposal(snapshot=snap_path.name, rule_updated_at=rule.updated_at, run_id=run, **proposal)
                _count(stats, proposal["status"])
                ocr_s = float((facts.sheet_ocr or {}).get("seconds") or 0)
                bid_txt = f"{proposal['bid_jpy_k']:,}K" if proposal.get("bid_jpy_k") else ""
                log.info("   %s %s | sheet %.1fs - ocr %.1fs - page %.1fs (%s) - total %.1fs",
                         proposal["status"], bid_txt, t_sheet, ocr_s, t_page,
                         "opened: " + why if need else "not opened", time.time() - t0)
            except (NotLoggedIn, Cancelled):
                raise
            except Exception as exc:
                stats.errors.append(f"{rule.model_code} {card.ref_no}: {exc}")
                log.exception("candidate failed %s", card.ref_no)
            report()
        close_guide()
        report()  # the cards skipped or already known since the last lot, and this guide's result
    stats.cache_hits = cache.hits
    return stats


def _scan_counters(stats: ScanStats) -> dict:
    """The running totals the dashboard shows while a scan is going (errors as a count)."""
    return {"cards": stats.cards, "opened": stats.opened, "review": stats.review, "skip": stats.skip, "bid": stats.bid,
            "reused": stats.reused, "pending": stats.pending, "model_calls": stats.model_calls, "errors": len(stats.errors)}


def _ocr_hints(ocr, facts: VehicleFacts) -> list[dict]:
    hints: list[dict] = []
    if ocr.model_code and facts.chassis and facts.chassis.upper() not in ocr.model_code.upper():
        hints.append({"level": "warn", "message": f"sheet model code reads {ocr.model_code}, listing says {facts.chassis}", "where": "sheet OCR"})
    if ocr.km is not None and facts.km and abs(ocr.km - facts.km) > max(1_500, int(facts.km * 0.03)):
        hints.append({"level": "warn", "message": f"sheet odometer reads {ocr.km:,} km, listing says {facts.km:,} km", "where": "sheet OCR"})
    if ocr.grade and facts.grade_raw and ocr.grade != facts.grade_raw:
        hints.append({"level": "warn", "message": f"sheet grade reads {ocr.grade}, listing says {facts.grade_raw}", "where": "sheet OCR"})
    return hints


def judge_mode(cfg: Config) -> bool:
    return (cfg.sheet_check or "none").lower() == "judge"


def judge_photos(cfg: Config) -> int:
    return int((getattr(cfg, "judge", None) or {}).get("max_photos") or 8)


def judge_inline(cfg: Config) -> bool:
    return bool((getattr(cfg, "judge", None) or {}).get("inline", True))


def manual_soft(rule: ModelRule, hard, facts: VehicleFacts, ocr, ocr_hints: list[dict], evidence: dict, mode: str = "manual") -> dict:
    """The reviewer's checklist and hints for one lot (route 1, and the base of the judge's row)."""
    free_lines, instructions, bad_lines = split_override_lines(rule)
    checklist = list(rule.text_rules) + free_lines
    checklist += [f"Auto-skip rule in force (did not fire): {i.raw}" for i in instructions]
    checklist += [f"Skip line NOT understood, ignored: {e}" for e in bad_lines]
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
    if facts.source == "card+sheet":
        checklist.insert(0, f"Judged from the search card and the sheet, without the listing page: the guide's "
                            f"{hard.variant_name or 'default'} variant covers every {rule.model_code} chassis. "
                            f"Check the model code on the sheet if in doubt; placement re-reads the page anyway.")
    for t in hard.toggles:
        checklist.append(f"Dashboard chip, apply by changing the amount if it fits: {t}")
    reasons = ["sheet and photos to be judged by the reviewer (sheet_check: none)"] if mode == "manual" else []
    if hard.deferred_modifiers:
        reasons.append("modifier needs your judgement: " + "; ".join(hard.deferred_modifiers))
    images_pending = bool(evidence.get("images_pending"))
    if images_pending:
        reasons.append("Nichibo has not published the sheet and photos yet (placeholder images); "
                       "the next scan re-checks this vehicle automatically")
    elif not evidence.get("sheet"):
        reasons.append("auction sheet image could not be downloaded; open it on Nichibo")
    return {"status": NEEDS_REVIEW, "reasons": reasons, "checklist": checklist, "hints": hints, "mode": mode,
            "images_pending": images_pending, "ocr": ocr.to_dict() if ocr else None}


def evaluate_vehicle(cfg: Config, rule: ModelRule, facts: VehicleFacts, no_model: bool,
                     evidence_loader: Callable[[], dict], past_decisions: list[dict] | None = None,
                     stats: ScanStats | None = None, judge_client=None, store=None, photo_loader=None,
                     previous_soft: dict | None = None) -> dict:
    """Decide one vehicle. Returns the proposal row fields (without snapshot/rule_updated_at).

    Judge mode (`sheet_check: judge`): after the hard rules and the OCR, the guide's requirements
    and the odd-case flags decide whether the photos are needed (`photo_loader` opens the page)
    and whether the model is asked at all. The lot always stays NEEDS_REVIEW with the model's
    recommendation and proposed amount beside the checklist; Jim confirms.
    """
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
    (out_dir / "facts.json").write_text(json.dumps(facts.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")

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
        soft = manual_soft(rule, hard, facts, ocr, ocr_hints, evidence)
        (out_dir / "hard.json").write_text(json.dumps(hard.to_dict(), indent=1), encoding="utf-8")
        return dict(base, status=NEEDS_REVIEW, stage="manual", bid_jpy_k=hard.bid_jpy_k, base_jpy_k=hard.base_jpy_k,
                    facts_json=facts.to_dict(), hard_json=hard.to_dict(), evidence_json=evidence, soft_json=soft)

    if judge_mode(cfg):
        # The judgement layer: what to look at, whether the photos are needed, whether to ask the model.
        reqs = requirements_for(rule, hard)
        soft = manual_soft(rule, hard, facts, ocr, ocr_hints, evidence, mode="judge")
        flags = odd_flags(rule, hard, facts, ocr_hints, soft["hints"])
        if needs_photos(reqs) and not evidence.get("photos") and photo_loader is not None and not evidence.get("images_pending"):
            facts, evidence = photo_loader(facts)
            base["summary"] = summary_line(facts)
            hard = evaluate_hard(facts, rule)  # the page may name a different chassis
            if hard.status != PASS:
                status = NEEDS_REVIEW if hard.status == NEEDS_REVIEW else SKIP
                return dict(base, status=status, stage="hard", bid_jpy_k=hard.bid_jpy_k, base_jpy_k=hard.base_jpy_k,
                            facts_json=facts.to_dict(), hard_json=hard.to_dict(), evidence_json=evidence)
            reqs = requirements_for(rule, hard)
            soft = manual_soft(rule, hard, facts, ocr, ocr_hints, evidence, mode="judge")
            flags = odd_flags(rule, hard, facts, ocr_hints, soft["hints"])
            (out_dir / "facts.json").write_text(json.dumps(facts.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
        rhash = requirements_hash(reqs, flags)
        soft.update(requirements=[r.to_dict() for r in reqs], flags=flags, requirements_hash=rhash,
                    proposed_bid_jpy_k=hard.bid_jpy_k, judgement=None, recommendation="review", judge_notes=[],
                    judge_pending=False, candidates=[], provider=None, usage=None, unavailable=None, images_used=[])
        wanted = needs_judge(reqs, flags)
        prev = previous_soft or {}
        if not wanted:
            soft.update(recommendation="bid", judge_notes=["routine: nothing to look at beyond the grid; confirm"])
        elif prev.get("judgement") and prev.get("requirements_hash") == rhash:
            for k in ("judgement", "recommendation", "proposed_bid_jpy_k", "judge_notes", "candidates", "provider",
                      "usage", "images_used"):
                soft[k] = prev.get(k, soft.get(k))
        elif judge_client is not None and judge_inline(cfg):
            from auction_bot.judge.judge import judge_lot
            res = judge_lot(judge_client, cfg, store, facts, rule, hard, evidence, ocr.to_dict() if ocr else None,
                            ocr_hints, soft["hints"], reqs, flags)
            if stats is not None and res.provider:
                stats.model_calls += 1
            soft.update(judgement=res.judgement, recommendation=res.recommendation, proposed_bid_jpy_k=res.proposed_bid_jpy_k,
                        judge_notes=res.notes, candidates=res.candidates, provider=res.provider, usage=res.usage,
                        unavailable=res.unavailable, images_used=res.images_used, judge_pending=res.judgement is None)
            (out_dir / "judge.json").write_text(json.dumps(res.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
            log.info("   judge %s -> %s, proposed %s (%s)", res.provider or "unavailable", res.recommendation,
                     res.proposed_bid_jpy_k, "; ".join(res.notes)[:120])
        else:
            soft.update(judge_pending=True, judge_notes=["judge pending: run `bot judge --pending`"])
        (out_dir / "hard.json").write_text(json.dumps(hard.to_dict(), indent=1), encoding="utf-8")
        return dict(base, status=NEEDS_REVIEW, stage="judge", bid_jpy_k=hard.bid_jpy_k, base_jpy_k=hard.base_jpy_k,
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
    todo = []  # the rows a current guide covers, in the stored order, so the total is known up front
    for row in rows:
        rule = by_guide.get(row["guide_id"]) or by_code.get(row["model_code"])
        if rule is None or (wanted and rule.model_code.upper() not in wanted):
            continue
        todo.append((row, rule))

    told = [0.0]

    def report(quiet: bool = False) -> None:
        """The running totals for the dashboard. `quiet`: at most twice a second (card rows take no time each)."""
        if quiet and st.checked and time.monotonic() - told[0] < 0.5:
            return
        told[0] = time.monotonic()
        progress.emit(stage="replay", checked=st.checked, total=len(todo), changed=st.changed, applied=st.applied,
                      unchanged=st.unchanged, needs_fetch=st.needs_fetch)

    for row, rule in todo:
        report(quiet=True)  # the totals after the rows before this one
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
                                   hard_json={"checks": []}, run_id=row["run_id"],
                                   soft_json={"mode": "manual", "images_pending": True, "checklist": [], "hints": [],
                                              "reasons": ["passes the card checks under the current rules; the next scan opens the page"]})
                st.applied += 1
            continue
        saved = load_saved_evidence(cfg, row["ref_no"])
        try:
            parked = bool(json.loads(row["soft_json"] or "{}").get("images_pending"))
        except json.JSONDecodeError:
            parked = False
        if parked and not (saved and saved.get("sheet")):
            st.unchanged += 1  # still waiting for Nichibo's images; the next scan re-checks it
            continue
        out_dir = cfg.evidence_dir / _safe(row["ref_no"])
        missing = {"dir": str(out_dir), "sheet": None, "photos": [], "failed": [], "placeholders": 1, "images_pending": True}
        try:
            prev_soft = json.loads(row["soft_json"] or "{}")
        except json.JSONDecodeError:
            prev_soft = {}
        proposal = evaluate_vehicle(cfg, rule, facts, no_model, evidence_loader=lambda s=saved, m=missing: s or m,
                                    previous_soft=prev_soft)
        # A judge row whose requirements changed needs a fresh judgement: that counts as a change too.
        old = (*old, prev_soft.get("requirements_hash"))
        new = (proposal["status"], proposal["bid_jpy_k"], (proposal.get("soft_json") or {}).get("requirements_hash"))
        if new == old:
            st.unchanged += 1
            continue
        st.changed += 1
        st.changes.append(f"{label}: {old[0]} {old[1]} -> {new[0]} {new[1]}")
        if apply:
            store.supersede_pending(row["ref_no"], rule.guide_id)
            store.add_proposal(snapshot=snap_path.name, rule_updated_at=rule.updated_at, run_id=row["run_id"], **proposal)
            st.applied += 1
    report()
    return st


@dataclass
class JudgeStats:
    checked: int = 0
    judged: int = 0
    routine: int = 0
    pages: int = 0
    unavailable: int = 0
    errors: list[str] = field(default_factory=list)


def judge_pending(cfg: Config, store: Store, model_codes: list[str] | None = None, limit: int | None = None,
                  browser: Browser | None = None, client=None, snapshot: Path | None = None) -> JudgeStats:
    """Judge stored pending lots that have no judgement yet (or a stale one), rate-limited and resumable.

    Lots whose requirements need the photos get their page opened through `browser` (headless);
    without a browser they are left pending with a note. The proposal row is updated in place.
    """
    from auction_bot.judge.client import make_client
    from auction_bot.judge.judge import judge_lot
    from auction_bot.judge.requirements import Requirement

    rules, _ = load_rules(cfg, snapshot, model_codes)
    by_guide = {r.guide_id: r for r in rules}
    by_code = {r.model_code: r for r in rules}
    client = client or make_client(cfg, store)
    cache = PageCache(cache_dir(cfg), cfg.cache_hours)
    st = JudgeStats()
    wanted = {c.upper() for c in model_codes} if model_codes else None
    todo = []  # the lots waiting for a judgement, in the stored order, so the total is known up front
    for row in store.list_proposals(decision="pending", status=NEEDS_REVIEW, limit=100_000):
        try:
            soft = json.loads(row["soft_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if soft.get("mode") != "judge" or not soft.get("judge_pending"):
            continue
        rule = by_guide.get(row["guide_id"]) or by_code.get(row["model_code"])
        if rule is None or (wanted and rule.model_code.upper() not in wanted):
            continue
        todo.append((row, soft, rule))

    def report(i: int, **extra) -> None:
        progress.emit(stage="judge", i=i, total=len(todo), judged=st.judged, unavailable=st.unavailable,
                      checked=st.checked, pages=st.pages, errors=len(st.errors), limit=limit, **extra)

    seen = 0
    for i, (row, soft, rule) in enumerate(todo, 1):
        progress.check_cancel()
        if limit is not None and st.judged >= limit:
            break
        seen = i
        report(i, ref_no=row["ref_no"], code=rule.model_code)  # the totals after the lots before this one
        st.checked += 1
        facts = VehicleFacts.from_dict(json.loads(row["facts_json"] or "{}"))
        hard = evaluate_hard(facts, rule)
        if hard.status != PASS:
            continue
        evidence = load_saved_evidence(cfg, row["ref_no"]) or {"dir": str(cfg.evidence_dir / _safe(row["ref_no"])), "sheet": None, "photos": []}
        reqs = [Requirement(**r) for r in soft.get("requirements", [])] or requirements_for(rule, hard)
        flags = soft.get("flags") or []
        if needs_photos(reqs) and not evidence.get("photos"):
            if browser is None:
                soft["judge_notes"] = ["judge pending: the photos are needed; run `bot judge --pending` with a browser"]
                store.update_soft(row["id"], soft)
                continue
            try:
                full = fetch_facts(browser, cfg, row["ref_no"], cache)
                full.ref_no = row["ref_no"]
                full.seats, full.sheet_ocr = facts.seats, facts.sheet_ocr
                evidence = download_evidence(browser, cfg, full, judge_photos(cfg), cache)
                facts = full
                st.pages += 1
            except (NotLoggedIn, Cancelled):
                raise  # signed out or stopped: the pass ends here, not once per lot
            except Exception as exc:
                st.errors.append(f"{row['ref_no']}: {exc}")
                continue
        ocr = facts.sheet_ocr
        res = judge_lot(client, cfg, store, facts, rule, hard, evidence, ocr, [], soft.get("hints") or [], reqs, flags)
        soft.update(judgement=res.judgement, recommendation=res.recommendation, proposed_bid_jpy_k=res.proposed_bid_jpy_k,
                    judge_notes=res.notes, candidates=res.candidates, provider=res.provider, usage=res.usage,
                    unavailable=res.unavailable, images_used=res.images_used, judge_pending=res.judgement is None)
        out_dir = Path(evidence["dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "judge.json").write_text(json.dumps(res.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
        store.update_soft(row["id"], soft, facts=facts.to_dict(), evidence=evidence)
        if res.judgement is None:
            st.unavailable += 1
            if res.unavailable and "quota" in res.unavailable and "no " not in res.unavailable:
                break  # every provider is out for today; the rest stays pending
        else:
            st.judged += 1
    report(seen, done=True)
    return st
