# BidBarn 🚗

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-104%20passing-brightgreen)
![Playwright](https://img.shields.io/badge/browser-Playwright%20Chromium-45ba4b)
![No paid AI](https://img.shields.io/badge/AI%20APIs-none%20required-lightgrey)
![Testing lock](https://img.shields.io/badge/placement-testing%20lock%20on-orange)

Carbarn's auction bidding assistant for Nichibo. BidBarn reads the bidding guides from the
Carbarn dashboard, searches Nichibo AutoSearch for every model code we buy, works out each
lot's bid from the grid, reads the auction sheet with a local OCR engine, and puts every
candidate on a review page with the sheet, a link to the photos and every rule check beside
it. A person approves or rejects each one. Nothing is placed on Nichibo without that click, and
while the testing lock is on nothing is placed at all. The scan judges a lot from the search
result and the sheet alone; it opens the lot's page only when a rule needs something more.

`Python 3.12` · `Playwright` · `RapidOCR` · `FastAPI` · `SQLite`

---

## Table of contents

- [The idea](#the-idea)
- [How a lot is judged](#how-a-lot-is-judged)
- [Non-negotiable rules](#non-negotiable-rules)
- [Install & run](#install--run)
- [The daily workflow](#the-daily-workflow)
- [The review page](#the-review-page)
- [Rules as Carbarn confirmed them](#rules-as-carbarn-confirmed-them)
- [CLI](#cli)
- [Where your files live](#where-your-files-live)
- [Troubleshooting](#troubleshooting)
- [Tests](#tests)
- [Project layout](#project-layout)
- [Status](#status)

---

## The idea

Bidding by hand means, for every model code, every day: open the Carbarn bidding page, copy
the model code, search Nichibo, sort by odometer and grade, open each lot, hover the photos to
find the auction sheet, read the seat count and the 4WD mark, work out which variant it is, look
up the year and odometer band in the grid, remember to add the grade bonus, copy the number,
paste it into Nichibo, and check the bid is not below the start price. One slip costs real money.

BidBarn does the mechanical part of that, deterministically, from the same guides the team
already maintains on the dashboard. What it cannot decide from the rules it hands to the
reviewer with the evidence laid out, instead of guessing. Rejections with a reason can be saved
as standing instructions for that model, so the checklist gets stricter over time without any
machine learning. A standing instruction written as `skip: ...` is applied by the bot before a
lot is even opened; `bot insights` proposes such lines from your own rejections, and
`bot learn --har` compares one of your manual sessions with what the bot would have done.
Learning only ever adds skips, ordering and pre-fills: an approval is always your click.

## How a lot is judged

```
 Carbarn dashboard ──► bidding-guide snapshot (grids, variants, modifiers, text rules)
                                   │
 Nichibo auction search (one per model code, stock excluded)
                                   │
        ┌──────────────────────────▼──────────────────────────┐
        │ 1. CARD          model family · grade · year row · odo cap │──► SKIP
        │                  start price vs the grid's ceiling         │
        │                  your "skip:" standing instructions        │
        └──────────────────────────┬──────────────────────────┘
        ┌──────────────────────────▼──────────────────────────┐
        │ 2. SHEET         downloaded from the card's own link │──► WAIT (placeholder: not published yet)
        │    (local OCR)   header band: seats · model code · km│
        └──────────────────────────┬──────────────────────────┘
        ┌──────────────────────────▼──────────────────────────┐
        │ 3. HARD RULES    variant · grid cell · grade bonus   │──► SKIP
        │                  listing conditions · seats · start  │──► NEEDS REVIEW (ambiguous)
        │                  skip lines · one bid per vehicle    │
        │   the lot's page is opened only when the guide's     │
        │   variants differ by chassis suffix, a modifier      │
        │   needs the colour, or a skip line needs the page    │
        └──────────────────────────┬──────────────────────────┘
        ┌──────────────────────────▼──────────────────────────┐
        │ 4. REVIEW PAGE   triage list · sheet · checklist     │──► REJECT (+ standing instruction)
        │                  photos on Nichibo · chips · ×2 rows │
        └──────────────────────────┬──────────────────────────┘
                              APPROVED
        ┌──────────────────────────▼──────────────────────────┐
        │ 5. PLACEMENT     re-open lot · re-verify · type      │──► BLOCKED (anything changed)
        │                  amount · read back · screenshot     │
        └─────────────────────────────────────────────────────┘
```

Every stage writes its result to SQLite with the facts, the rule snapshot and the evidence
files, so any bid can be explained afterwards. Per lot the scan now spends about 6 seconds
(one sheet download and one OCR pass) instead of 22; only lots whose guide needs the page
cost the extra page load.

## Non-negotiable rules

Enforced in code, not by convention. The full list for anyone touching the code is in
[CLAUDE.md](CLAUDE.md).

- **RULE-0 Testing lock.** While `project_status` in `config.yaml` is anything other than the
  exact phrase `The project is finished`, placement is always a dry run, whatever `dry_run` or
  `--live` say.
- **RULE-1 No bid without a human.** Only proposals approved on the review page can be placed.
  Nothing approves automatically.
- **RULE-2 Never Stock.** The browser layer refuses every Nichibo URL outside the auction
  search, auction detail pages, the sheet translator, images and login. Stock pages, Vehicle
  Offers, carts, bulk "Place All Bids" and paid image requests are unreachable.
- **RULE-3 Never guess.** Anything the rules cannot decide goes to the reviewer as NEEDS REVIEW.
- **RULE-4 One bid per vehicle, ever.** Enforced by the `bids` table primary key.
- **RULE-5 Never re-scrape.** Pages, translator replies and images are cached; rule changes are
  re-applied offline with `bot replay --apply`. The data folders are never deleted.
- **RULE-6 Learning only tightens.** Telemetry, `skip:` lines, `bot insights` and
  `bot learn` can add skips, ordering and pre-fills. Nothing learned can approve a bid.

---

## Install & run

### Route 0 — Windows double-click (the normal way)

The project ships with `.cmd` launchers. After the one-time setup below, the daily routine is
double-clicking `scan.cmd`, then `review.cmd`, then `place.cmd`. `bot.cmd` on its own shows a menu.

### What you need first

| | |
|---|---|
| Python | 3.12 |
| Browser | Chromium, installed by Playwright below (about 150 MB) |
| OCR runtime | [Microsoft Visual C++ 2015-2022 x64](https://aka.ms/vs/17/release/vc_redist.x64.exe), once |
| Accounts | Nichibo AutoSearch and the Carbarn dashboard, signed in once through `login.cmd` |

### One-time setup

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m playwright install chromium
login.cmd
```

`login.cmd` opens a browser with two tabs, Nichibo and the Carbarn dashboard. Sign in to both,
then press Enter in the black window. The session lives in `data\profile` and is reused by every
command; no password is ever stored by BidBarn.

The generated `.venv\Scripts\bot.exe` is blocked by Windows App Control on the office PC, so the
CLI is always run through `bot.cmd` (or `.venv\Scripts\python -m auction_bot.cli`).

### Check it worked

```bat
bot.cmd fetch-rules
bot.cmd scan -m GDH206 --limit 3
review.cmd
```

You should see the parsed guides listed, three Hiace lots evaluated, and the review page open
at http://127.0.0.1:8765 with the sheet and photos of each candidate.

---

## The daily workflow

1. **`scan.cmd`** fetches a fresh guide snapshot and scans every active guide. Lots already
   evaluated are reused; only new listings are judged. The sheet comes straight from the search
   result; lots whose sheet Nichibo has not published yet are flagged *images pending* and
   re-checked automatically next time, without opening their page.
2. **`review.cmd`** opens the triage view: pending lots grouped by model, best grade first, then
   fewest km, the sheet beside each. Approve, change the amount, or reject with a reason, with
   the mouse or the keyboard. Photos are one click away on Nichibo in your own browser. Tick
   "save as standing instruction" to make a reason part of that model's checklist from then on;
   write it as `skip: ...` and the bot applies it itself.
3. **`place.cmd`** places the approved bids. Dry run by default (the amount is typed on Nichibo
   and screenshotted, nothing is submitted). Live placement needs the testing lock lifted,
   `dry_run: false` in `config.yaml` **and** `bot.cmd place --live`.
4. Now and then, **`bot.cmd insights`** shows how you decide per model (time, what you looked
   at, rejection themes) and the skip lines your rejections justify. **`bot.cmd learn --har`**
   compares a manual session you recorded in your browser with the bot's proposals.
5. **`bot.cmd watch`** opens the bot's browser for you and records a normal manual session:
   every page, click, hover over the photos and the sheet, what you type into the bid form,
   the bids you place, and the reasons you type into the small box at the bottom right (Enter
   saves). Close the window when done. `bot.cmd watch --report data\sessions\<stamp>` prints
   the per-lot timeline with the bot's own verdict beside each lot. The bot only watches: it
   makes no request and never touches the bid form.

## The review page

| Panel | What it shows |
|---|---|
| Triage (`/`) | Every pending lot, grouped by model and sorted the way the manual pass goes: best grade first, then fewest km. Sheet thumbnail, bid, the top reasons, Approve / Reject, links to the lot and its photos on Nichibo. Keys: `j`/`k` move, `a` approve (asks first), `r` reject with a reason, `e` change the amount, `z` sheet full size, `o` Nichibo, `p` photos, `Enter` detail, `?` help. Skip rules your rejections justify are offered at the top of each model with an *add* button. |
| Header | Status, guide, decision state, link to the lot on Nichibo, `×2` when the same vehicle is proposed under another guide (both rows are shown, approve once) |
| Listing facts | Chassis and resolved variant (or "judged from the search card" when the page was not opened), year, km, grade, start price, hall and date when known, Nichibo's own sheet translation when it has one |
| Grid lookup and hard rules | The bid in K JPY, the grid cell used, the bonus applied, every check with a green, amber or red dot, dashboard chips that are *not* applied |
| Your checklist | The guide's text rules and your standing instructions (skip lines are listed as "auto-skip rule in force"), the seat count read from the sheet, "confirm the variant" when it was matched from trim words, keyword hints |
| Auction sheet and photos | The downloaded sheet, full size on click; photos as a link to the lot on Nichibo (the scan no longer downloads them) |
| Your decision | Amount, reason, "save as standing instruction" with a suggested `skip:` line filled in from the reason, Approve / Reject; the testing-lock banner. Decided lots show how long the decision took and what was looked at. |
| All proposals (`/list`) | The plain table with filters by decision, status and model |

## Rules as Carbarn confirmed them

Recorded here so the code and the team agree. Change the code only when this list changes.

| Rule | Confirmed |
|---|---|
| Odometer bands | Upper bound inclusive: 40K–50K covers 40,001 to 50,000 km |
| Grade | Nichibo's listing grade; 0, R, RA or blank never bid |
| Grade bonus | A positive bonus stored as "grade 4" applies to 4 and above (highest threshold only); negative adjustments apply to that exact grade |
| Dials (a percentage such as ×92 %) | Already inside the grid values; never applied again, including the E-Class Wagon's dial |
| Variants | Every variant on a guide is biddable; the on/off dot on the dashboard is only the selected tab |
| Chips (No Pro Pilot, Autech, Normal Pack) | Applied by the reviewer after looking at the car, never by the bot |
| Seats | Parsed from the text rules ("2, 3, 5 or 6 seats"); a sheet reading outside the set skips the lot |
| Chassis with "?" | Nichibo's guessed suffix is used, with "confirm on the sheet" on the checklist |
| Same code in two guides | Serena / Serena Campervan: both rows are shown and cross-linked |
| Year | Nichibo's "Year of Registration" |
| Detail page | Opened only when the guide's variants differ by chassis suffix (GDH206V / GDH206K), a modifier depends on the colour, a skip line needs a page-only fact, or the search card lacks a fact. Otherwise the lot is judged from the card and the sheet; placement re-opens the page and re-verifies it anyway |
| Card-stage ceiling | A lot whose start price is above the highest bid any variant could reach (cell plus every positive modifier) is skipped before anything is downloaded |

## Standing instructions

`rules/overrides/<CODE>.md` holds one line per instruction, stamped with date and author.
Free-text lines are shown on the checklist. A line starting with `skip:` is also applied by
the bot, as early as its facts allow (card, sheet or page stage), and re-applied to stored
lots with `bot replay --apply`. `skip` is the only verb; nothing in this file can approve.

```
skip: <condition> [and <condition>]...
condition := <field> <op> <value>   or   <field> <value>   (no op: "contains" for text, "=" for numbers)
field     := year | make | model | variant | transmission | km | cc | grade | start | seats
             | chassis | colour | equipment | hall | date | bid
op        := > | >= | < | <= | = | != | contains | not contains | is | is not | starts with
value     := number (commas allowed, 120k = 120000) | text, quoted if it matters | another field
```

Examples: `skip: variant contains DUMP`, `skip: hall USS Kyushu`, `skip: km > 120k`,
`skip: start >= bid` (start in yen, bid in K JPY), `skip: seats > 6`,
`skip: colour is white and year < 2019`. A condition on a fact the bot does not have yet is
undecided and never fires. Lines that cannot be read are ignored with a warning and shown on
the checklist as "NOT understood". Add lines on the review page, with
`bot.cmd skip-line CODE "..."`, or by editing the file.

## CLI

| Command | Purpose |
|---|---|
| `bot.cmd login` | One-time sign-in to Nichibo and the Carbarn dashboard |
| `bot.cmd fetch-rules [-m CODE]` | Snapshot the Team Bid guides |
| `bot.cmd rules [CODE]` | Print the parsed grid of a model |
| `bot.cmd scan [-m CODE] [--limit N] [--fresh] [--rescan] [--headless]` | Search, evaluate, write proposals |
| `bot.cmd review` | Serve the review page (triage at `/`, table at `/list`) |
| `bot.cmd replay [--apply]` | Re-apply current rules, skip lines and code to stored proposals, offline |
| `bot.cmd place [--live]` | Place approved bids (dry run unless every lock is off) |
| `bot.cmd insights [-m CODE] [--days N] [--min-support N]` | How you decide per model, and the skip lines your rejections justify |
| `bot.cmd skip-line CODE "skip: ..."` | Add a structured standing instruction (validated first) |
| `bot.cmd learn --har FILE [-m CODE] [--save]` | Compare a manual Nichibo session (HAR from your browser) with the bot's proposals; offline |
| `bot.cmd watch [--report DIR]` | Record a manual session in the bot's browser (clicks, hovers, reasons, bids, screenshots); `--report` prints a recorded session's timeline |

## Where your files live

Everything the bot downloads stays on the office PC. Git never sees any of it.

| Folder | Content |
|---|---|
| `data\profile` | Browser session (cookies) for both sites |
| `data\bot.sqlite` | Every vehicle seen, proposal, decision (with what you looked at and how long it took), placement, and manual sessions saved with `learn --save` |
| `data\evidence\<ref>\` | Sheet, OCR output, facts, rule snapshot, screenshots per lot |
| `data\sessions\<stamp>\` | A watched manual session: `events.jsonl` and screenshots |
| `data\cache\` | Downloaded pages and translator replies, reused for `cache_hours` (12) |
| `rules\snapshots\` | Timestamped copies of the bidding guides |
| `rules\overrides\` | Standing instructions per model code |
| `har\` | Network captures used to learn the endpoints (`docs/endpoints.md` is the distilled version) |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "STOPPED: … not logged in" | The saved session expired. Double-click `login.cmd`, sign in to both tabs, press Enter. |
| "STOPPED: No rules snapshot found" | Run `bot.cmd fetch-rules` (or `scan.cmd`, which does it first). |
| "Rules snapshot is N h old" | Snapshots older than `max_rules_age_hours` are refused. Fetch again. |
| OCR "unavailable" on the review page | Install the Visual C++ runtime linked above and restart. |
| "Images not published yet" banner | Nichibo still serves placeholder images for that lot. Leave it; the next scan re-checks it. |
| A page load times out | Nichibo stalls now and then; loads retry once. Re-run the scan, it continues where it stopped. |
| `bot.exe` "Permission denied" | Windows App Control blocks the launcher. Use `bot.cmd`. |
| A DLL "could not be found" or "blocked by policy" | Same policy. `jiter` is pinned to a version it allows; do not upgrade it blindly. |

## Tests

```bat
.venv\Scripts\python -m pytest -q
```

104 tests: the golden cases from the manual walkthrough against the real GDH206 guide, variant
resolution on captured Alphard and E-Class guides, the seat rule, OCR parsing on three real sheets
plus the header-band crop and its fallback, an offline end-to-end scan against captured Nichibo
pages, the card-and-sheet path that opens no page, the placeholder path that parks a lot
without a page, the grid ceiling, placement re-verification of card-judged lots, the cache and
replay paths, the skip-line grammar and where the scan applies it, review telemetry and
insights, the triage view, the HAR parser and join (with a socket guard proving no network),
the Stock URL guard and the testing lock.

## Project layout

```
bidbarn/
├── bot.cmd · login.cmd · scan.cmd · review.cmd · place.cmd   Windows launchers
├── config.yaml                                              locks, cache, pacing, review address
├── CLAUDE.md                                                rules for anyone working on the code
├── docs/endpoints.md                                        both sites' endpoints, from the HAR captures
├── rules/overrides/                                         standing instructions (git-ignored)
├── src/auction_bot/
│   ├── carbarn/      client.py (dashboard API) · models.py (guides, variants, grids, modifiers)
│   ├── nichibo/      browser.py (session + URL guard) · search.py · parsers.py · vehicle.py
│   │                 sheet_ocr.py (local OCR) · cache.py · bid.py (placement) · facts.py
│   ├── rules/        hard.py (deterministic rules) · instructions.py (skip lines) · hints.py
│   │                 soft.py + schema.py (optional model mode)
│   ├── review/       FastAPI review page, templates (triage, detail, list) and static/review.js
│   ├── pipeline.py   scan (card + sheet first, page only when needed) and offline replay
│   ├── insights.py   how you decide, per model; suggested skip lines
│   ├── learn.py      HAR of a manual session vs the bot's proposals, offline
│   ├── watch.py      watch mode: records Jim's own session (clicks, hovers, reasons, bids)
│   ├── store.py      SQLite
│   └── cli.py        commands
└── tests/            fixtures are real guides and captured pages
```

## Status

Testing phase. The testing lock is on and every approval is test data. The speed rework is in:
lots are judged from the search card and the sheet, the page is opened only when a rule needs
it, photos are a link, and the OCR reads the sheet header first. Next: resume the scan from
guide 22, the first review pass on the triage view, and the first `bot insights` run once
decisions exist.

### Open questions for Carbarn

- The GDH206K variant is inactive on the guide although the walkthrough bids on it; the bot
  treats every variant as biddable (the dot is the selected tab). Confirm.
