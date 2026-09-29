# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Jim at Carbarn, alone, on the office Windows 11 PC. He is the only reviewer and the only
person who opens the page; the server listens on 127.0.0.1 (confirmed 2026-09-29). He works in
a session or two a day: check sign-in and guides, scan, review, place, close. During a session
Nichibo AutoSearch and the Carbarn dashboard are open beside it.

## Product Purpose

BidBarn is Carbarn's auction bidding assistant for Nichibo. It reads the Carbarn bidding
guides, searches Nichibo for every model code Carbarn buys, works out each lot's bid from the
guide's grid, reads the auction sheet locally, and puts every candidate in front of Jim with
the evidence beside it. Jim approves or rejects each one.

The dashboard is the front door to all of that: it starts the daily commands, shows them
running, shows the state of the day, and holds the review pages. Success means Jim runs the
whole day from the browser and needs a CMD window only when a saved session has expired.

## Positioning

Deterministic and explainable. Every bid amount comes from the guide's grid, every stage
stores its facts and evidence, anything a rule cannot decide goes to the reviewer, and nothing
is placed without his click. While the testing lock is on, nothing is placed at all.

## Operating Context

- The daily flow: sign-in check, fetch guides, scan, review (approve, change the amount, or
  reject with a reason), place. Rule changes are re-applied offline with replay.
- Neighbours on screen: Nichibo AutoSearch, the Carbarn dashboard, the auction sheet and the
  lot's photos.
- Windows App Control on the office PC blocks unsigned programs. The product is Python, static
  files and `.cmd` launchers: no exe, no Node toolchain, no container.
- Everything the bot downloads and decides stays on the office PC (`data/`), never in git.

## Capabilities and Constraints

- The page may start the daily set only: scan, fetch guides, judge pending, replay (report and
  apply), place as a dry run, insights, sign-in check, sign-in window. Learn, watch and bid
  modes stay on `bidbarn.cmd`. Live placement cannot be expressed from the page.
- The testing lock and the dry-run flag are shown on the page and changed only by hand in
  `config.yaml`.
- One Nichibo job at a time, paced. A run can be stopped; a placement is never interrupted
  mid-lot.
- Only a click on the review page approves a proposal. Model output, rules and learning can
  add skips, ordering and pre-fills, never an approval.
- Vocabulary: lot, guide, grid, band, variant, modifier, chip, sheet, skip line (standing
  instruction), playbook, proposal, decision, placement, dry run, testing lock, run.
  Amounts are in thousands of yen (K JPY).
- States: proposal status BID, SKIP, NEEDS_REVIEW; decision pending, approved, rejected,
  superseded; run status running, ok, failed, stopped.
- The full rule list for anyone changing code is `CLAUDE.md`.

## Brand Commitments

- Name: BidBarn. Owner: Carbarn.
- Jim, 2026-09-29: the dashboard follows Carbarn's branding, and its interface follows the
  style of the Carbarn dashboard, so the two read as one family.
- Voice: plain, factual, short. It says what happened and what to do next. No marketing
  language, no exclamation marks.

## Evidence on Hand

- Real proposals, decisions, sheets and screenshots exist on the office PC under `data/`.
  They are private: never copied into fixtures, documents or commits.
- Test fixtures under `tests/fixtures/` hold public auction pages and guide trees.
- No usage numbers, customers or testimonials exist, and none may be invented.

## Product Principles

1. A person decides. The page informs, orders and pre-fills; only Jim's click approves.
2. Evidence beside the claim. A number is shown with where it came from.
3. State before action. Lock, sign-in and guide freshness are readable before anything can be
   started.
4. Never guess, never hide. Uncertainty is shown as needing review; a failed run shows the
   line that stopped it.
5. One thing at a time. One Nichibo job, paced, stoppable.

## Accessibility & Inclusion

Keyboard-first review (the triage keys are part of the product). State is never carried by
colour alone. Reduced motion is respected. Text contrast meets WCAG AA. The page is light
only, like the Carbarn dashboard.
