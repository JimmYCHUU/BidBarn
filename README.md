# BidBarn 🚗

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-309%20passing-brightgreen)
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

The whole day runs from a dashboard in the browser: it says what is next, starts it, shows it
running, and holds the review pages. It looks like the Carbarn dashboard on purpose.

`Python 3.12` · `Playwright` · `RapidOCR` · `FastAPI` · `SQLite`

---

## Table of contents

- [The idea](#the-idea)
- [How a lot is judged](#how-a-lot-is-judged)
- [Non-negotiable rules](#non-negotiable-rules)
- [Install & run](#install--run)
- [The daily workflow](#the-daily-workflow)
- [The dashboard](#the-dashboard)
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
        │ 3b. JUDGE        free vision model (Gemini, Groq)    │──► recommends bid / review / skip
        │   (sheet_check:  requirements with "where to look"   │    with evidence per requirement,
        │    judge)        photos fetched when needed · chips  │    chips, odd cases, a proposed
        │                  odd-case flags · your playbook      │    amount from the candidates
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

### The dashboard: `dashboard.cmd`

Double-click `dashboard.cmd`. The page opens in your browser at http://127.0.0.1:8765 and the
server runs in a minimised window called "BidBarn dashboard" in the taskbar. Close that window
to stop it. A second double-click just opens the page again.

The first time, run `bidbarn.cmd` once before it (see below): that creates the Python
environment the dashboard needs.

### One click: `bidbarn.cmd`

Install Python 3.12 once (tick "Add python.exe to PATH" in the installer), then double-click
`bidbarn.cmd`. The first run creates the Python environment and installs the packages and the
browser. Every run then asks for one of two modes (or run `bidbarn.cmd learn` /
`bidbarn.cmd bid` to skip the question).

**Learn mode (L).** The bot's browser opens with two tabs, Nichibo and the Carbarn dashboard
(drag the dashboard tab to your other monitor); you bid exactly as you normally do and type
why in the blue box at the top right (Enter saves). Where your mouse rests, what you hover
and what you highlight is recorded on both tabs. The bot only watches. When you close the browser it scans the model codes you searched, compares its
own proposals with what you did, card by card, turns your comments into candidate `skip:`
rules where it can, and saves everything to `data\sessions\<stamp>\report.txt`. Nothing is
placed and no rule is stored until you add it.

**Bid mode (B)**, every run:

1. checks that you are signed in to Nichibo and the Carbarn dashboard, and opens a window for
   you to sign in if a session has expired (it continues by itself once you have);
2. fetches the bidding guides;
3. scans Nichibo;
4. opens the review page in your browser: approve or reject, then click **Finish review** at
   the top of the page;
5. places the approved bids, a dry run while the testing lock is on, and prints the result.

`.\bidbarn.cmd bid --skip-scan` reviews and places what is already stored; `--no-place`
stops after the review; `--headed` shows the browser while it works. No exe and no Docker on
purpose: Windows App Control on the office PC blocks unsigned programs, and a container has
no screen for the sign-in window or for watch mode.

Bid mode serves its own review page on the same address, so close the dashboard window before
starting it. Learn mode and watch mode stay on `bidbarn.cmd` and `bot.cmd`: the dashboard does
not start them.

### The separate steps

The same steps also exist as their own launchers, for when you want just one of them:
`scan.cmd`, `review.cmd`, `place.cmd`, `login.cmd`. `bot.cmd` on its own shows a menu.

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
at http://127.0.0.1:8765/review with the sheet of each candidate.

---

## The daily workflow

From the dashboard the day is five steps, and the page always says which one is next: check the
sign-in, fetch the guides, scan, review, place. Each of them is one button. The same steps as
separate launchers, for when the dashboard is not running:

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
   the bids you place, and the reasons you type into the small box at the top right (Enter
   saves). The Carbarn dashboard tab is recorded too, even when dragged to a second monitor:
   where the mouse rests (the guide and cell under it), text you highlight, and clicks.
   Close the window when done. `bot.cmd watch --report data\sessions\<stamp>` prints
   the per-lot timeline with the bot's own verdict beside each lot. The bot only watches: it
   makes no request and never touches the bid form.

## The dashboard

| Page | What it shows |
|---|---|
| Overview (`/`) | The next step of the day with its one button, and the five steps with where each stands. Below it the day's log, newest first: every run with how it ended and its closing numbers, and every review session with its decisions. A run that is going is the first entry, open, with its guide row, counters and log. Beside the log: the counts, the sign-in state of both sites, the age of the guide snapshot, today's model calls against the free quota, and the other commands. |
| Runs (`/runs`) | Every run started from the page, with its options, duration and outcome, and a panel to start any command with its options. |
| A run (`/runs/<n>`) | Live while it runs: one cell per guide in a single row (tall and blue where lots were found, short where nothing was new, red on an error, the lit cell is the guide being scanned), the running totals, the log as it is written, and **Stop**. Afterwards: the outcome, the placements with their screenshots for a placement run, and the whole log, also as a file. |
| Insights (`/insights`) | What `bot.cmd insights` prints, as a page: per model how many you decided, how long a decision takes, what you looked at, rejection themes, and the skip rules your rejections justify, each with an *Add rule* button. |
| Settings (`/config`) | What `config.yaml` says, read only: the locks, the scan settings, the judgement providers (whether a key is set, never the key), the sites and the folders. |

What the page can start: scan, fetch the guides, judge pending lots, replay (report or store),
place as a dry run, insights, the sign-in check and the sign-in window. Nothing else. It cannot
ask for a live placement, it cannot change the locks, and it decides no lot: approving stays a
click on the review page. One command runs at a time; a second one is refused with a link to
the one that is running. **Stop** asks the run to end at its next check point (before the next
page load) and ends its process after 20 seconds if it has not; a placement is never stopped in
the middle of a lot. If the dashboard window is closed while a command runs, the command
finishes by itself and the next start of the dashboard picks its record up.

The page is served to this PC only. A request that names another host, or that comes from
another website's page, is refused, so no page you visit can start a command or approve a lot.

## The review page

| Panel | What it shows |
|---|---|
| Triage (`/review`) | Every pending lot, grouped by model and sorted the way the manual pass goes: best grade first, then fewest km. Sheet thumbnail, bid, the top reasons, Approve / Reject, links to the lot and its photos on Nichibo. Approve and Reject open a row under the lot with the amount or the reason; nothing is recorded until you confirm there. Keys: `j`/`k` move, `a` approve, `r` reject with a reason, `e` change the amount (each then `Enter` to confirm, `Esc` to cancel), `z` sheet full size, `o` Nichibo, `p` photos, `Enter` detail, `?` the key list. Skip rules your rejections justify are offered at the top of each model with an *Add rule* button. |
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

## The judgement layer

With `sheet_check: judge` in `config.yaml`, a free hosted vision model reads each lot's photos
and sheet against the guide's own requirements and decides what a rule cannot: dark seats or
not, leather or cloth, the Pro Pilot button on the steering wheel, an Autech badge, and the odd
cases (odometer at the cap, conversions, sheet notes). It works like this:

1. The guide's text rules, chips and standing instructions become **requirements**, each with
   a place to look: card, sheet or photos (`judge/requirements.py`). Odd-case flags name what is
   unusual about the lot. A lot with nothing to look at and nothing odd never costs a model call.
2. When the photos are needed the lot's page is opened and up to `judge.max_photos` photos are
   downloaded (only then; the scan stays fast).
3. The model receives the lot's facts, the OCR fields, Nichibo's translation, the requirements,
   the flags, **your playbook** (`rules/playbook/general.md` and `rules/playbook/<CODE>.md`, your
   own words from learn-mode comments and review corrections), the candidate amounts (the grid
   cell, the cell with a chip, the next odometer band when the odometer is on a boundary or near
   the cap; never the grid itself), the sheet and the photos.
4. It answers with a verdict and evidence per requirement, whether each chip applies, the odd
   cases, one of the candidate amounts or none, and a recommendation: bid, review or skip.
   The bot turns that into a proposal deterministically (`judge/judge.py::decide`).
5. **You confirm.** Every judged lot stays on the triage view with the model's recommendation,
   badges per requirement (hover for the evidence), the proposed amount beside the grid's,
   and the photos it looked at. Approve, change the amount, or reject. Telling it what it got
   wrong ("The model was wrong about … because …") writes a line into the playbook for that
   model code, so the next judgement reads your correction.

Providers are free tiers, tried in order: Google Gemini 3.5 Flash-Lite, then Gemini 3.1
Flash-Lite (a separate free quota on the same key), then Groq's text-only model for lots that
need no photos (Groq had no free vision model on 2026-09-28). Limits and model names live in
`config.yaml`, keys in `.env` (see `.env.example`); when a provider retires a model, the next one
takes over and the name is a one-line config change. Google may use free-tier content to
improve its products; Groq states it does not train on it. Neither ever receives the bid grid. When no provider can answer, the lot still
reaches you, marked "model unavailable", and `bot.cmd judge --pending` catches up later.

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
| `bot.cmd learn` | Learn mode (`bidbarn.cmd`, L): watch you bid, then scan the same codes, compare, suggest rules |
| `bot.cmd bid [--skip-scan] [--no-place] [--headed]` | Bid mode (`bidbarn.cmd`, B): sign-in check, guides, scan, review, place |
| `bot.cmd dashboard [--no-browser]` | Serve the dashboard and the review pages (`dashboard.cmd`). If it is already running, only opens the page |
| `bot.cmd login [--wait]` | One-time sign-in to Nichibo and the Carbarn dashboard. `--wait` needs no keyboard: it opens a window only if a site is signed out and continues by itself |
| `bot.cmd check-login` | Says, without a window, whether both sites are signed in |
| `bot.cmd fetch-rules [-m CODE] [--headless]` | Snapshot the Team Bid guides |
| `bot.cmd rules [CODE]` | Print the parsed grid of a model |
| `bot.cmd scan [-m CODE] [--limit N] [--fresh] [--rescan] [--headless]` | Search, evaluate, write proposals |
| `bot.cmd review` | Serve the same pages without opening the browser (triage at `/review`, table at `/list`) |
| `bot.cmd replay [--apply]` | Re-apply current rules, skip lines and code to stored proposals, offline |
| `bot.cmd place [--live]` | Place approved bids (dry run unless every lock is off) |
| `bot.cmd judge [--pending] [-m CODE] [--limit N]` | Ask the free vision model about stored lots still waiting for a judgement (rate-limited, resumable) |
| `bot.cmd insights [-m CODE] [--days N] [--min-support N]` | How you decide per model, how often the model was corrected, and the skip lines your rejections justify |
| `bot.cmd skip-line CODE "skip: ..."` | Add a structured standing instruction (validated first) |
| `bot.cmd learn --har FILE [-m CODE] [--save]` | Learn from a HAR saved in your own browser instead of watching; offline |
| `bot.cmd watch [--report DIR]` | Record a manual session in the bot's browser (clicks, hovers, reasons, bids, screenshots); `--report` prints a recorded session's timeline |

## Where your files live

Everything the bot downloads stays on the office PC. Git never sees any of it.

| Folder | Content |
|---|---|
| `data\profile` | Browser session (cookies) for both sites |
| `data\bot.sqlite` | Every vehicle seen, proposal, decision (with what you looked at and how long it took), placement, run started from the dashboard, and manual sessions saved with `learn --save`. While something has it open there are two files beside it, `bot.sqlite-wal` and `bot.sqlite-shm`; copy the database only when nothing is running |
| `data\runs\<n>.log` | The output of run `<n>`, exactly what the console would have shown |
| `data\profile.lock` | Present while a command uses the bot's browser. It names the command and is removed when the command ends; one left behind by a crash is replaced |
| `data\evidence\<ref>\` | Sheet, OCR output, facts, rule snapshot, screenshots per lot |
| `data\sessions\<stamp>\` | A watched manual session: `events.jsonl` and screenshots |
| `data\cache\` | Downloaded pages and translator replies, reused for `cache_hours` (12) |
| `rules\snapshots\` | Timestamped copies of the bidding guides |
| `rules\overrides\` | Standing instructions per model code |
| `rules\playbook\` | Your playbook for the judgement model: `general.md` (principles) and `<CODE>.md` (your comments and corrections) |
| `har\` | Network captures used to learn the endpoints (`docs/endpoints.md` is the distilled version) |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "STOPPED: … not logged in" or "redirected to the login page" | The saved session expired. Click **Sign in** on the dashboard, or double-click `login.cmd`, sign in to both tabs, press Enter. A scan now stops at the first guide when it is signed out, instead of trying every guide. |
| "STOPPED: another BidBarn run … is using the browser profile" | A command that uses the bot's browser is still running, from the dashboard or from a console. Wait for it, or stop it on its run page. |
| "… is running (run N)" on the dashboard | One command runs at a time. Follow the link to watch it, or stop it. |
| The dashboard says "did not answer" | Its window was closed. Double-click `dashboard.cmd`. |
| The page does not open on `dashboard.cmd` | Something else uses port 8765, usually a `review.cmd` or `bidbarn.cmd` window. Close it and try again. |
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

309 tests: the golden cases from the manual walkthrough against the real GDH206 guide, variant
resolution on captured Alphard and E-Class guides, the seat rule, OCR parsing on three real sheets
plus the header-band crop and its fallback, an offline end-to-end scan against captured Nichibo
pages, the card-and-sheet path that opens no page, the placeholder path that parks a lot
without a page, the grid ceiling, placement re-verification of card-judged lots, the cache and
replay paths, the skip-line grammar and where the scan applies it, review telemetry and
insights, the triage view, the HAR parser and join (with a socket guard proving no network),
the learn and bid modes (card attribution of comments, the Finish hand-off), the judgement layer (provider fallback and quotas, requirement classification, candidates, playbook, the stubbed model in the scan, the pending pass, replay, the review page and corrections), the Stock URL guard and the testing lock,
and the dashboard: the progress lines a run prints and that a signed-out or stopped scan ends at
once, the browser profile lock, the runs table on an old database, the job runner on harmless
stand-in commands (finish, fail, stop, a second start refused, a run left by a closed
dashboard), the command table (no option and no value can produce a live placement), every page
on an empty database, the status and the day's steps, the log and its live stream, that a
request from another site can neither start a command nor approve a lot, and that the dashboard
package decides nothing.

## Project layout

```
bidbarn/
├── dashboard.cmd                                            the dashboard in your browser
├── bidbarn.cmd                                              one click: Learn mode or Bid mode
├── bot.cmd · login.cmd · scan.cmd · review.cmd · place.cmd   the separate steps
├── config.yaml                                              locks, cache, pacing, review address
├── CLAUDE.md                                                rules for anyone working on the code
├── PRODUCT.md · DESIGN.md                                   who the page is for, and how it looks
├── docs/endpoints.md                                        both sites' endpoints, from the HAR captures
├── docs/dashboard.md                                        the dashboard's routes, runs and progress lines
├── rules/overrides/                                         standing instructions (git-ignored)
├── src/auction_bot/
│   ├── carbarn/      client.py (dashboard API) · models.py (guides, variants, grids, modifiers)
│   ├── nichibo/      browser.py (session + URL guard) · search.py · parsers.py · vehicle.py
│   │                 sheet_ocr.py (local OCR) · cache.py · bid.py (placement) · facts.py
│   ├── rules/        hard.py (deterministic rules) · instructions.py (skip lines) · hints.py
│   │                 soft.py + schema.py (the opt-in OpenAI sheet check)
│   ├── judge/        client.py (free providers, fallback, quotas) · requirements.py · candidates.py
│   │                 playbook.py · judge.py (prompt, schema, deterministic decision)
│   ├── review/       the local page (FastAPI): app.py (review routes, the only place a lot is decided),
│   │                 templates, static/ (bidbarn.css, review.js, dashboard.js, icons, vendor/)
│   ├── dashboard/    jobs.py (the command table and the job runner) · status.py (the state of the day)
│   │                 routes.py (overview, runs, insights, settings, the API)
│   ├── progress.py   the PROGRESS and SUMMARY lines a run prints for the dashboard, and Stop
│   ├── lock.py       one command at a time on the bot's browser profile
│   ├── pipeline.py   scan (card + sheet first, page only when needed) and offline replay
│   ├── run.py        the one-click modes: learn (watch, compare, suggest) and bid (sign-in, guides, scan, review, place)
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
it, photos are a link, and the OCR reads the sheet header first. The dashboard is in
(2026-09-29): it has been exercised on stand-in commands and on a throwaway database, not yet
on a real scan. Next: the first real scan started from the dashboard, the first review pass on
the triage view, and the first look at Insights once decisions exist.

### Open questions for Carbarn

- Odometer margin (from the teaching session of 2026-09-15): the reviewer skips a lot that is
  already at the last band's cap ("already 80k") and, for a lot exactly on a band boundary
  ("50k on the mark"), uses the next band's value, because the odometer rises between the
  auction and the warehouse. Bands are upper-bound inclusive today, so the bot would price
  50,000 km in the 40-50K band and would bid on an 80,000 km lot with an 80K cap. What margin
  should the bot apply, in km, and does it apply to every model?
- The GDH206K variant is inactive on the guide although the walkthrough bids on it; the bot
  treats every variant as biddable (the dot is the selected tab). Confirm.
