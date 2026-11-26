# BidBarn 🚗

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-834%20passing-brightgreen)
![Playwright](https://img.shields.io/badge/browser-Playwright%20Chromium-45ba4b)
![No paid AI](https://img.shields.io/badge/AI%20APIs-free%20tiers%20only-lightgrey)
![Testing lock](https://img.shields.io/badge/placement-testing%20lock%20on-orange)

An auction bidding assistant for a car importer buying at Japanese auctions. BidBarn reads the
bidding guides the buyers maintain on their dashboard, searches the auction site for every model
code they buy, works out each lot's bid from the guide's grid, reads the auction sheet with a
local OCR engine, lets a free vision model pre-read the sheet and photos where a rule needs eyes,
and puts every candidate on a review page with the evidence beside it. A person approves or
rejects each one. Nothing is bid without that click, and while the testing lock is on nothing is
bid at all.

Built for [Carbarn](https://www.carbarn.com.au), Sydney, buying through Nichibo AutoSearch.

`Python 3.12` · `Playwright` · `RapidOCR` · `FastAPI` · `SQLite` · `Gemini and Groq free tiers`

---

## Table of contents

- [The idea](#the-idea)
- [How a lot is judged](#how-a-lot-is-judged)
- [Non-negotiable rules](#non-negotiable-rules)
- [What is under the hood](#what-is-under-the-hood)
- [The dashboard and the desk](#the-dashboard-and-the-desk)
- [Quick start](#quick-start)
- [Tests](#tests)
- [Project layout](#project-layout)
- [Documentation](#documentation)
- [Status](#status)
- [Licence](#licence)

---

## The idea

Bidding by hand means, for every model code, every day: open the dealership's bidding page, copy
the model code, search the auction site, sort by odometer and grade, open each lot, hover the
photos to find the auction sheet, read the seat count and the 4WD mark, work out which variant it
is, look up the year and odometer band in the grid, remember to add the grade bonus, copy the
number, paste it into the bid form, and check the bid is not below the start price. One slip costs
real money.

BidBarn does the mechanical part of that deterministically, from the same guides the team already
maintains. What it cannot decide from the rules, it hands to the reviewer with the evidence laid
out instead of guessing. Rejections with a reason can be saved as standing instructions for that
model, so the checklist gets stricter over time without any machine learning. Learning and the
vision model only ever add skips, ordering and pre-fills: an approval is always a person's click.

## How a lot is judged

```
 Dealer dashboard ──► bidding-guide snapshot (grids, variants, modifiers, text rules)
                                   │
 Auction search (one per model code, stock excluded)
                                   │
        ┌──────────────────────────▼──────────────────────────┐
        │ 1. CARD          model family · grade · year row ·   │──► SKIP
        │                  odo cap · start price vs ceiling    │
        │                  your "skip:" standing instructions  │
        └──────────────────────────┬──────────────────────────┘
        ┌──────────────────────────▼──────────────────────────┐
        │ 2. SHEET         downloaded from the card's own link │──► WAIT (not published yet)
        │    (local OCR)   header band: seats · model code · km│
        └──────────────────────────┬──────────────────────────┘
        ┌──────────────────────────▼──────────────────────────┐
        │ 3. HARD RULES    variant · grid cell · grade bonus   │──► SKIP
        │                  listing conditions · seats · start  │──► NEEDS REVIEW (ambiguous)
        │                  skip lines · one bid per vehicle    │
        └──────────────────────────┬──────────────────────────┘
        ┌──────────────────────────▼──────────────────────────┐
        │ 3b. JUDGE        free vision model (Gemini, Groq)    │──► recommends bid / review / skip,
        │                  requirements with "where to look"   │    with evidence per requirement
        │                  candidate amounts (never the grid)  │
        └──────────────────────────┬──────────────────────────┘
        ┌──────────────────────────▼──────────────────────────┐
        │ 4. REVIEW PAGE   triage list · sheet · checklist     │──► REJECT (+ standing instruction,
        │                  the model's verdicts · photos       │    + correction into the playbook)
        └──────────────────────────┬──────────────────────────┘
                              APPROVED
        ┌──────────────────────────▼──────────────────────────┐
        │ 5. PLACEMENT     re-open lot · re-verify · type      │──► BLOCKED (anything changed)
        │                  amount · read back · screenshot     │
        └─────────────────────────────────────────────────────┘
```

Every stage writes its result to SQLite with the facts, the rule snapshot and the evidence files,
so any bid can be explained afterwards, and a rule change can be re-applied to every stored lot
offline. The lot's page is opened only when the guide's variants differ by chassis suffix, a
modifier needs the colour, or a skip line needs a page-only fact; everything else is judged from
the search card and the sheet. That took a lot from about 22 seconds to about 6.

## Non-negotiable rules

Enforced in code, not by convention, and each one has tests.

| Rule | What it means |
|---|---|
| **Testing lock** | While `project_status` in `config.yaml` is anything other than the exact phrase `The project is finished`, placement is always a dry run, whatever `dry_run` or `--live` say. |
| **No bid without a human** | Only lots approved on the review page can be placed. Nothing approves automatically. |
| **Never Stock** | The browser layer refuses every auction-site URL outside the search, lot pages, the sheet translator, images and login. Stock pages, offers, carts, bulk "Place All Bids" and paid image requests are unreachable. |
| **Never guess** | Anything the rules cannot decide goes to the reviewer as NEEDS REVIEW. |
| **One bid per vehicle, ever** | Enforced by the `bids` table's primary key. |
| **Never re-scrape** | Pages, translator replies and images are cached; rule changes are re-applied offline with `bot replay --apply`. |
| **Learning only tightens** | Telemetry, `skip:` lines, `bot insights` and `bot learn` can add skips, ordering and pre-fills. Nothing learned can approve a bid. |

## What is under the hood

**A deterministic rules engine with a replayable record.** The guide's grid is parsed into
variants, year rows and odometer bands (upper bound inclusive, a km no band covers takes the
closest band, above the last band is a skip), with grade bonuses and modifiers. Every decision is
stored with the rule snapshot that made it, so `bot replay` shows exactly which lots a rule change
would move before `--apply` commits it.

**Local OCR of auction sheets.** RapidOCR runs on the PC. The sheet's header band is cropped and
read first (seats, model code, odometer), with a full-sheet fallback, so a sheet costs one OCR pass
and no paid API.

**A vision model that is never shown the price list.** The guide's text rules, chips and standing
instructions become requirements, each with a place to look: the card, the sheet or the photos.
The model receives the lot's facts, the OCR, the requirements and a short list of candidate
amounts, never the grid itself, and answers with a verdict and evidence per requirement. The code
turns that into a proposal deterministically. Free tiers are tried in order (Gemini Flash-Lite,
a second Gemini quota, then Groq for lots that need no photos), and a lot no provider could answer
still reaches the reviewer, marked "model unavailable".

**Evidence ladders for what a model gets wrong.** Cruise control and wheelchair-accessible
(Welcab) seats are judged by a fixed ladder: the auction sheet first (a bare "CC" means engine
size, not cruise control), then the steering-wheel or seat photo, then the maker's catalogue for
that grade. The model only reports what it sees at each step; `judge/ladders.py` decides, and a
missing or malformed observation reads review, never yes.

**Import eligibility.** Each lot is checked against the government approval records (MRE and SEV
entries) behind its guide: build window, expiry and chassis code. The verdict is Eligible, Confirm
import or Cannot import, and it never skips a lot by itself.

**Learning that a person controls.** A standing instruction written as `skip: km > 120k and colour
is white` is parsed by a small grammar and applied as early as its facts allow. `bot insights`
proposes such lines from the reviewer's own rejections. Learn mode watches a manual bidding
session in the bot's browser (clicks, hovers, the reasons typed), then compares what the person
did with what the bot would have done, lot by lot. A correction typed on the review page ("the
model was wrong about … because …") goes into that model code's playbook, which the next judgement
reads.

**A local dashboard that cannot be turned against itself.** The FastAPI dashboard is served to the
PC only; a request naming another host, or coming from another website's page, is refused, so no
page in the browser can start a command or approve a lot. The command table is tested so that no
option and no value can produce a live placement. One command runs at a time, guarded by a lock on
the bot's browser profile.

## The dashboard and the desk

BidBarn opens on the **desk**: lots grouped by auction session and by lane (Needs a look,
Card-clear, Waiting, Decided), decided from the keyboard with a 10-second undo. **Scan now** checks
the sign-in, fetches the guides and scans every active guide. A page per guide shows its grid, its
requirements, its standing instructions and the skip lines the reviewer's rejections suggest. The
**Rulebook** holds what is decided and the open questions; the odometer margin, for one, is set
there with a preview of every lot it would change before Apply. **Ctrl+K** finds a page or a lot by number, chassis or model code.

## Quick start

Windows, Python 3.12, and the [Visual C++ 2015-2022 x64 runtime](https://aka.ms/vs/17/release/vc_redist.x64.exe)
for the OCR engine.

```bat
git clone https://github.com/JimmYCHUU/BidBarn.git
cd BidBarn
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m playwright install chromium
copy .env.example .env          :: free Gemini / Groq keys for the judgement layer, optional
login.cmd                       :: sign in once to the auction site and the dealer dashboard
dashboard.cmd                   :: http://127.0.0.1:8765
```

Using it for real needs accounts on both sites. The tests do not: they run on captured pages
with no network. Every command, every page and the daily workflow are in
[the manual](docs/MANUAL.md#install--run).

## Tests

```bat
.venv\Scripts\python -m pytest -q
```

834 tests, offline: the golden cases from a manual walkthrough of a captured guide, variant
resolution, the seat rule, OCR parsing on real sheets and the header-band crop, an end-to-end scan
against captured auction pages, the HAR parser (with a socket guard proving no network), the
odometer bands and the grid ceiling, placement re-verification, cache and replay, the skip-line grammar, the
judgement layer with a stubbed model (provider fallback and quotas, requirements, candidates,
ladders, corrections), the Stock URL guard and the testing lock, and every dashboard page on an
empty database, including that a request from another site can neither start a command nor
approve a lot.

The fixtures keep the shape of real dashboard replies and auction pages, but every bid amount,
modifier and dial in them is invented (the real grid scaled and jittered), and staff names,
contact details and account identifiers are replaced. The expected values follow the invented
grid.

## Project layout

```
bidbarn/
├── dashboard.cmd · bidbarn.cmd · bot.cmd     the dashboard; one-click Learn or Bid mode; the CLI
├── config.yaml                               locks, judgement providers, cache, pacing
├── src/auction_bot/
│   ├── carbarn/       the dealer dashboard's API: guides, variants, grids, modifiers
│   ├── nichibo/       browser session and URL guard · search · parsers · sheet OCR · placement
│   ├── rules/         hard.py (the deterministic rules) · instructions.py (the skip-line grammar)
│   ├── judge/         free providers and quotas · requirements · candidates · playbook · ladders
│   ├── catalog/       the maker's catalogue: allow-listed, paced, cached
│   ├── review/        the local FastAPI app: the only place a lot is decided
│   ├── dashboard/     the command table and job runner · state of the day · a page per guide
│   ├── pipeline.py    scan (card and sheet first, page only when needed) and offline replay
│   ├── run.py         the one-click modes: learn and bid
│   ├── insights.py · learn.py · watch.py     how a person decides; HAR comparison; watch mode
│   └── store.py · cli.py                     SQLite; commands
├── docs/              the manual, the dashboard, the desk, the rulebook, the endpoints
└── tests/             834 tests on captured pages with invented amounts
```

## Documentation

| Document | What it covers |
|---|---|
| [The manual](docs/MANUAL.md) | Running the day, the dashboard, the desk, the review page, the rules as confirmed, the judgement layer, standing instructions, the CLI, troubleshooting |
| [CLAUDE.md](CLAUDE.md) | The safety rules for anyone changing the code |
| [docs/dashboard.md](docs/dashboard.md) | The dashboard's routes, runs and progress lines |
| [docs/desk.md](docs/desk.md) | The desk: lanes, keys, parity with the old pages |
| [docs/rulebook.md](docs/rulebook.md) | The Rulebook: decided rules and open questions |
| [docs/endpoints.md](docs/endpoints.md) | The endpoints the code uses on both sites |
| [DESIGN.md](DESIGN.md) · [PRODUCT.md](PRODUCT.md) | How the pages look, and who they are for |

## Status

Testing phase: the testing lock is on, so placement is a dry run that types the amount, reads it
back and takes a screenshot, and submits nothing. The scan, the judgement layer, the dashboard and
the desk are in and covered by tests; the first complete Bid run from the dashboard on real data is
next. The open questions for the buyers are listed in [the manual](docs/MANUAL.md#status) and on
the Rulebook page.

## Licence

All rights reserved. The code is published to be read and referred to; see [LICENSE](LICENSE).
The Carbarn and Nichibo names belong to the businesses they refer to.
