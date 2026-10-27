# BidBarn 🚗

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-830%20passing-brightgreen)
![Playwright](https://img.shields.io/badge/browser-Playwright%20Chromium-45ba4b)
![No paid AI](https://img.shields.io/badge/AI%20APIs-none%20required-lightgrey)
![Testing lock](https://img.shields.io/badge/placement-testing%20lock%20on-orange)

Carbarn's auction bidding assistant for Nichibo. BidBarn reads the bidding guides from the
Carbarn dashboard, searches Nichibo AutoSearch for every model code we buy, works out each
lot's bid from the grid, reads the auction sheet with a local OCR engine, lets a free vision
model pre-read the sheet and the photos where the guide asks for a look, and puts every
candidate on a review page with the sheet, the model's recommendation and every rule check
beside it. A person approves or rejects each one. Nothing is placed on Nichibo without that
click, and while the testing lock is on nothing is placed at all. The scan judges a lot from the
search result and the sheet alone; it opens the lot's page only when a rule or the model needs
something more.

The whole day runs from a dashboard in the browser: it starts Learn mode or Bid mode with one
click, shows the run, holds the review pages, and has a page per bidding guide. It looks like
the Carbarn dashboard on purpose.

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
- [The desk](#the-desk)
- [Rules as Carbarn confirmed them](#rules-as-carbarn-confirmed-them)
- [The judgement layer](#the-judgement-layer)
- [Standing instructions](#standing-instructions)
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
With the judgement layer on, a free vision model reads the sheet and the photos against the
guide's requirements and your playbook, and recommends bid, review or skip with its evidence.
Learning and the model only ever add skips, ordering and pre-fills: an approval is always your
click.

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
cost the extra page load, and only lots with something to look at cost a model call. A lot
that reaches the review costs one more page load, once, for its auction day and hall. A lot
whose sheet is not published yet waits for the next scan, but it keeps its grid amount from the
search card, so it can be approved in the meantime.

**Import eligibility (ROVER).** Next to the grid, every lot is checked against the MRE/SEV
approval pair behind its guide, from the guide tree only (no ROVER visit). The review page shows
an "Import eligibility (ROVER)" card with one of three verdicts. **Eligible**: the registration
year is inside the build window, the approval has not expired and the chassis matches the SEV
model codes. When the lot has no chassis code to compare, the card reads "eligible (model code
not checked)". **Confirm import**: something is unclear, for example a year the window only
partly covers ("confirm the build month"), a registration year the lot does not give (an
unknown year reads confirm), a chassis that is not among the model codes, or a window that
closes before the lot's year. **Cannot import**: the lot was registered before the
window opens, or the approval has expired. The check never skips a lot. A "cannot import" lot
stays in review with the reason and you reject it yourself. A copy of the approval older than
`eligibility.stale_days` (90) is flagged. When a guide has no selected pair, its active pairs
are alternatives: the lot only has to fit any one of them, so the bot's recommendation can
only say skip when every pair fails it, and the verdict is never better than "confirm import".
Holder names, contacts, phones and websites in the blocks are never read.

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
  re-applied offline with `bot replay --apply`. The data folders are never deleted. The first
  `--apply` after the import-eligibility change supersedes every pending lot whose guide has
  approvals, once, because the eligibility hash is new. The hash never includes the day count
  or the staleness flag, so replay does not flip from day to day. Day-one order: `bot replay`
  (dry; read the labelled change list), then `bot replay --apply`, then `bot judge --pending`.
  The first apply also gives every pending judge lot whose pair carries text a new requirements
  hash, so the pending pass redoes its judgement: one model call per such lot, against the
  free-tier daily limits. The equipment ladders add new requirement fields, so the first apply
  after that change supersedes every stored judge lot once more and `bot judge --pending`
  re-judges them (one model call each). Replay never contacts the maker catalogue. In replay and
  in runs without an inline judge, a lot whose photo bundle grew after its judgement goes back to
  the pending pass (the change line says "photos arrived after the judgement") instead of
  re-reading the old observations; a scan with an inline judge re-judges such a lot at once.
- **RULE-6 Learning only tightens.** Telemetry, `skip:` lines, `bot insights` and
  `bot learn` can add skips, ordering and pre-fills. Nothing learned can approve a bid.

---

## Install & run

### The dashboard: `dashboard.cmd`

Double-click `dashboard.cmd`. The page opens in your browser at http://127.0.0.1:8765 and the
server runs in a minimised window called "BidBarn dashboard" in the taskbar. Close that window
to stop it. A second double-click just opens the page again. BidBarn opens on the desk. The
overview, under System in the menu, offers the two modes, **Start Learn mode** and **Start Bid
mode**, one click each; the single steps are in a fold on the Runs page.

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

Both modes also start from the dashboard. From there, Bid mode waits at step 4 on the
dashboard's own review page and continues when you click **Finish review** in the header; a
Learn run ends with the session on its run page, lot by lot, with the candidate rules and an
**Add** button each. From a console, `bidbarn.cmd bid` serves its own review page on the same
address, so it stops with a message while the dashboard is running: start it from the dashboard
instead, or close the dashboard window first. Watch mode on its own stays on `bot.cmd`.

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
bot.cmd scan -m CODE --limit 3
review.cmd
```

`CODE` is any model code from the list the first command prints (the guides change, so pick
one that is there today). You should see the parsed guides listed, up to three lots of that
model evaluated, and the review page open at http://127.0.0.1:8765/review with the sheet of
each candidate.

---

## The daily workflow

The day starts on the desk, which is where BidBarn opens. Press **Scan now** in its scan bar,
decide the lots that come in, then open the bid sheet and place as a dry run. Ctrl+K finds a
lot or a page from anywhere.

Bid mode is still there, under System, then Overview and day log. **Start Bid mode** there passes
through five stages (sign-in, guides, scan, review, place), shows which one it is at, waits at
the review, and continues when you click **Finish review** in the header. The single steps are
in the fold on the Runs page. The same steps as separate launchers, for when the dashboard is
not running:

1. **`scan.cmd`** fetches a fresh guide snapshot and scans every active guide. Lots already
   evaluated are reused; only new listings are judged. The sheet comes straight from the search
   result; lots whose sheet Nichibo has not published yet are flagged *images pending* and
   re-checked automatically next time, without opening their page.
2. **`review.cmd`** opens the triage view: pending lots grouped by model, best grade first, then
   fewest km, the sheet beside each. Approve, change the amount, or reject with a reason, with
   the mouse or the keyboard. The lot on Nichibo is one click away in your own browser. On the
   lot's own page, tick "save as standing instruction" to make a reason part of that model's
   checklist from then on; write it as `skip: ...` and the bot applies it itself.
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
| Overview (`/overview`, System in the menu) | The date, one sentence of state, and the two modes: **Learn** (the bot watches you bid) and **Bid** (the whole day as one run), one button each. While a run is going the band shows it instead, with **Watch the run**, and a Bid run's five stages. Below it the day's log, newest first: every run with how it ended and its closing numbers, and every review session with its decisions. A run that is going is the first entry, open, with its guide row, counters and log. Beside the log one card of state chips: sign-in, guides, lots to review, approved bids not placed, placed, and a model quota only when it is short. |
| Runs (`/runs`) | Every run, filtered as All, Learn, Bid or Steps, with its options, duration and outcome. A closed fold, **Start a single step**, starts one part of the day on its own: sign in, check the sign-in, fetch the guides, scan (model codes, lots per model), judge pending lots, replay (store or report), place as a dry run. |
| Guides (`/guides`, `/guides/<code>`) | Every bidding guide in the snapshot, and one guide as a page: its requirements and the chips you judge, the bid grid per variant, your standing instructions with the form to add one, the skip rules your rejections justify, the playbook and your learn-mode comments, recent decisions, and the lots under the code. Every lot links to its guide. |
| A run (`/runs/<n>`) | Live while it runs: one cell per guide in a single row (tall and blue where lots were found, short where nothing was new, red on an error, the lit cell is the guide being scanned), the running totals, the log as it is written, and **Stop**. A Bid run shows its five stages and, at the review stage, **Open the review**. Afterwards: the outcome, the placements with their screenshots, and the whole log, also as a file. A Learn run shows your session: per lot what you did, what the bot would have done and what the model said, the notes you typed, and the rules your comments suggest with an **Add** button each. |
| Review and All lots (`/review`, `/list`, `/p/<n>`) | The review pages, described in the next section. |
| Insights (`/insights`) | What `bot.cmd insights` prints, as a page: per model how many you decided, how long a decision takes, what you looked at, rejection themes, and how many skip rules your rejections justify, with a link to the guide's page where they are added. |
| Settings (`/config`) | What `config.yaml` says, read only: the locks, the scan settings, the judgement providers (whether a key is set, never the key), the sites and the folders. |

What the page can start: Learn mode, Bid mode, and the single steps (scan, fetch the guides,
judge pending lots, replay, place as a dry run, the sign-in check and the sign-in window).
Nothing else. It cannot ask for a live placement, it cannot change the locks, and it decides no
lot: approving stays a click on the review page, and a suggested skip rule becomes a rule only
when you click **Add**. One command runs at a time; a second one is refused with a link to
the one that is running. **Stop** asks the run to end at its next check point (before the next
page load) and ends its process after 20 seconds if it has not; a placement is never stopped in
the middle of a lot. **Pause** (Bid mode, before the review) ends the run at its next check point
and frees the bot; the lots it found stay in the review, and you can review them during the break.
**Resume** on the overview (`/overview`) starts Bid mode again in the same session: the guides already done go
quickly, from the cache. **Delete session** stops the session if it runs and takes its waiting lots
out of the review without a decision. If the dashboard window is closed while a command runs, the command
finishes by itself and the next start of the dashboard picks its record up.

The page is served to this PC only. A request that names another host, or that comes from
another website's page, is refused, so no page you visit can start a command or approve a lot.

## The review page

| Panel | What it shows |
|---|---|
| Triage (`/review`) | Every pending lot, grouped by model and sorted the way the manual pass goes: best grade first, then fewest km. Per lot: the sheet thumbnail, the bid (the model's proposed amount with the grid's beside it when they differ), one line of why, and for a judged lot the model's recommendation and its verdicts as counts (pass, fail, unclear); Approve / Reject, a link to the lot on Nichibo and one to its detail page. Approve and Reject open a row under the lot with the amount or the reason; nothing is recorded until you confirm there. Keys: `j`/`k` move, `a` approve, `r` reject with a reason, `e` change the amount (each then `Enter` to confirm, `Esc` to cancel), `z` sheet full size, `o` Nichibo, `Enter` detail, `?` the key list. The head of each model links to its guide page and says how many skip rules your rejections suggest; they are added there. Each session (one Bid mode run and the runs that resumed it) is its own section, newest first, with lots from the console under "Earlier scans". **Delete** on a lot, or **Delete session** on a section, takes waiting lots out of the review without a decision: nothing is learned from them, they stay out for the rest of that session, and the next session's scan shows them again if they still match. Key `d` deletes (press it twice). |
| Header (`/p/<n>`) | Status, decision state, link to the guide's page, link to the lot on Nichibo, Next; a note when the same vehicle is proposed under another guide (`×2 guides` on the triage row; both rows are shown, approve once) |
| Listing facts | Chassis and resolved variant (or "judged from the search card" when the page was not opened), year, km, grade, start price, hall and date when known, Nichibo's own sheet translation when it has one |
| Grid lookup and hard rules | The bid in K JPY, the grid cell used, the bonus applied, every check with a green, amber or red dot, dashboard chips that are *not* applied |
| Your checklist | The guide's text rules and your standing instructions (skip lines are listed as "auto-skip rule in force"), the seat count read from the sheet, "confirm the variant" when it was matched from trim words, keyword hints |
| The model's judgement | For a judged lot: the recommendation, the proposed amount beside the grid's, a verdict with evidence per requirement, whether each chip applies, the odd cases, the sheet notes and the candidate amounts; "model unavailable" when no provider could answer |
| Auction sheet and photos | The downloaded sheet, full size on click; the photos the model looked at, when the judgement layer fetched them. Otherwise the scan downloads no photos and the full set is on Nichibo in your own browser |
| Your decision | Amount, reason, "The model was wrong about … because …" for a judged lot, "save as standing instruction" with a suggested `skip:` line filled in from the reason, Approve / Reject. **Delete** takes the lot out of the review without a decision. The lock is stated in the page header. Decided lots show how long the decision took and what was looked at. |
| All lots (`/list`) | The plain table with filters by decision, status and model |

## The desk

BidBarn opens on the desk: `/` is the desk, and `/desk` still reaches it. A bid sheet is at `/bids`.
The old review pages are unchanged and still at their own addresses.

The menu has four places and a fold:

- **Desk** (`/`), **Bid sheet** (`/bids`), **Guides** (`/guides`) and **Rulebook** (`/rulebook`).
- **System**, a fold in the menu: Overview and day log (`/overview`, with Bid mode, Learn mode,
  Pause, Resume and Delete session), Old review (`/review`), All lots (`/list`), Runs, Insights,
  Settings (`/config`) and Sheet trial. Every old address still works.
- **Ctrl+K** (Cmd+K on a Mac) opens "Find a page or a lot". Type a page name to jump to it, or from
  2 characters a lot number, chassis or model code to open that lot's page (`/p/<n>`). Arrows
  move, Enter opens, Esc closes. It only opens pages and changes nothing.
- Each guide page has an "On the desk" block: lots waiting for you, approved and rejected in the
  last 30 days, and the odometer margin in force. The guides page has a **Teach session** button
  that starts today's Learn mode.

- Lots are grouped by auction session (hall and day, in date order) and by lane: Needs a look, Card-clear, Waiting, Decided.
- Card-clear is strict: with `sheet_check: judge`, most lots whose guide has a text requirement are Needs a look, because the model reads those notes.
- Keys move, approve, change the amount, reject and clear. A decision waits 10 seconds for an undo and is sent when the window ends or the page is left. It goes through the same `decide` route as the old pages.
- Clear is never a decision. The bid sheet only starts the dry-run `place` command.
- The scan opens each lot that reaches the review once more, to read its auction day and hall.

Phase 2 made the store safer: an approval claims its vehicle, so a second approval under the other
guide is refused at once; an approval of a lot that changed or was replaced since the page showed it
is refused; an approved or rejected lot can be reopened until it is placed live; and a lot placed as
a dry run is not typed again until reopened.

Phase 3 added a scan bar to the desk: **Scan now** checks the sign-in, fetches the guides and scans
Nichibo for every active guide, then ends. It does not wait for your review and places nothing.
**Stop** pauses it, and Scan now again carries on from the cache and the stored lots. If Nichibo signs
out during the scan, a sign-in window opens once and the scan carries on after you sign in.

Phase 4 added the **Rulebook** (`/rulebook`, nav item Rulebook): what is decided, the open questions
you answer there, and what is locked. The odometer margin is a setting with a preview of every lot it
would change, then Apply; the other questions take Keep it as it is or Ask for a change. Nothing there
approves or places a bid. See [docs/rulebook.md](docs/rulebook.md).

The parity table, the keys, what the desk leaves out, the check list and how to try it before the
merge are in [docs/desk.md](docs/desk.md).

## Rules as Carbarn confirmed them

Recorded here so the code and the team agree. Change the code only when this list changes.

| Rule | Confirmed |
|---|---|
| Odometer bands | Upper bound inclusive: 40K–50K covers 40,001 to 50,000 km. 0 km is the lowest band; a km no band covers (below the first band, or in a gap) takes the closest band; above the last band is a skip (Jim, 2026-09-30) |
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
| Cruise control | Judged by a ladder, in this order (Jim, 2026-10-20): the sheet (yes is yes; a bare "CC" is not cruise control), then the steering-wheel photo (none visible, or no wheel photo on a complete set, is no), then the maker's catalogue (standard on that grade is yes; optional or not fitted is no; no data, or differing grades when the lot's grade cannot be named, is review). Adaptive cruise counts; a note that says radar or adaptive needs adaptive |
| Welcab seats | A Welcab must have a front swivel/rotating, front lift-up or second-row side lift-up seat. A slope/ramp or wheelchair-lift Welcab, a plain car, or a Welcab with no seat statement on the sheet, in the photos or in the catalogue is not eligible. Same ladder order (Jim, 2026-10-20) |
| Images not published yet | The lot is parked until the next scan, without opening its page. It keeps its grid amount from the card, so it can be approved; the sheet and photos, when they arrive, can only skip it or change the modifiers (Jim, 2026-09-30) |

## The judgement layer

With `sheet_check: judge` in `config.yaml`, a free hosted vision model reads each lot's photos
and sheet against the guide's own requirements and decides what a rule cannot: dark seats or
not, leather or cloth, the Pro Pilot button on the steering wheel, cruise control, the Welcab
front seat, an Autech badge, and the odd
cases (odometer at the cap, conversions, sheet notes). It works like this:

1. The guide's text rules, chips and standing instructions become **requirements**, each with
   a place to look: card, sheet or photos (`judge/requirements.py`). Odd-case flags name what is
   unusual about the lot. A lot with nothing to look at and nothing odd never costs a model call.
2. When the photos are needed the lot's page is opened and up to `judge.max_photos` photos are
   downloaded (only then; the scan stays fast). A lot with a cruise or Welcab requirement gets
   every photo instead, up to `judge.max_photos_ladder` (40), because the steering wheel or the
   seat can be anywhere in the set. A cached bundle is extended with the missing photos, with a
   quarter of a random `pace_seconds` draw between two image requests; a file on disk that is not
   an image is left out of the bundle (and on disk), so the set reads short.
3. The model receives the lot's facts, the OCR fields, Nichibo's translation, the requirements,
   the flags, the selected pair's (or, with none selected, each active pair's) SEV/MRE
   restriction text, the maker catalogue's grade table for the lot's model code (grade names,
   seats, cruise and welfare marks; never prices), **your playbook** (`rules/playbook/general.md` and
   `rules/playbook/<CODE>.md`, your own words from learn-mode comments and review corrections),
   the candidate amounts (the grid cell, the cell with a chip, the next odometer band when the
   odometer is on a boundary or near the cap; never the grid itself), the sheet and the photos.
   In judge mode with `eligibility.judge_text` on, a lot whose pair carries restriction text
   costs one model call, like any lot with a requirement the sheet cannot settle, because the
   model reads that text as a pre-fill; a cached judgement with an unchanged requirements hash
   is reused. With `judge_text: false` the text goes on your checklist instead.
4. It answers with a verdict and evidence per requirement, whether each chip applies, the odd
   cases, one of the candidate amounts or none, and a recommendation: bid, review or skip.
   The bot turns that into a proposal deterministically (`judge/judge.py::decide`).
5. **Cruise control and Welcab seats are judged by a ladder** (`judge/topics.py` routes a text
   rule or chip to it by its words; `judge/ladders.py` decides). The order is fixed (Jim,
   2026-10-20):
   - **Sheet.** Yes is yes. A bare "CC" is not cruise control: the JU form uses ＣＣ for engine
     size, inspectors write クルコン or クルーズコントロール, and one Japanese glossary reads CC as
     car stereo.
   - **Steering-wheel photo.** No cruise control visible, or no wheel photo at all on a complete
     photo set, is no. A short photo set (some photos could not be downloaded) reads review.
   - **Maker's catalogue.** Standard on that grade is yes. Optional or not fitted is no. No data,
     or grades that differ and the lot's grade cannot be named, reads review. When the model names
     the lot's catalogue grade, that grade's mark decides; the Welcab seat block works the same way.

   Adaptive cruise counts as cruise control; a note that says radar or adaptive needs adaptive
   specifically. A Welcab must have a front swivel/rotating, front lift-up or second-row side
   lift-up seat. A slope/ramp or wheelchair-lift Welcab, a plain car, and a Welcab with no seat
   statement on the sheet, in the photos or in the catalogue are not eligible. Refined during the
   build on 2026-10-21, to confirm with Jim: the catalogue alone never makes a lot a Welcab: when
   the sheet or the photos call the lot a Welcab without naming the seat, a catalogue that sells
   one Welcab seat system for the model code decides by it; with several systems, or when nothing
   on the sheet or in the photos says Welcab, only a catalogue grade the model names that is a
   Welcab row decides, and a named plain grade or no named grade is no seat statement (for a lot
   the sheet or the photos call a Welcab, a named plain grade conflicts and reads review). Only a
   cruise note is read as negated: a negated Welcab note that names a failing seat system ("No
   ramp / slope type") is the seat requirement itself; any other ("No welcab", "No swivel seat")
   is a plain text requirement the model judges, as before the ladders. A bare "lift up" (a lifted
   4WD) does not make a note a Welcab note; it needs a seat word.
   A guide named
   "... Welcab" with no Welcab note gets the seat requirement from its name; its text quotes the
   approval pair's sentences that name a seat system, or says "No approval text names the seat
   system; confirm on ROVER by hand." The model only reports what it sees at each step;
   `judge/ladders.py` decides, and a missing or malformed observation reads review, never a yes.
   Only cruise chips go through the ladder. When the ladder resolves a cruise chip, the amount is
   the grid plus the applied chips and the model's own amount pick is ignored (the notes say so).
   Chips with Welcab or seat words are still judged by the model (see the open questions).

   **The maker's catalogue** is kurumaerabi.com (`catalog.base_url`), the only third-party site the
   bot reads. It reads the model-code sitemap, the model-code pages and the grade pages,
   allow-listed to `/catalog/` paths on that host with redirects refused, paced by
   `catalog.pace_seconds`, and cached under `data/cache/catalog` for `catalog.cache_days` (90).
   `bot scan --fresh` does not bypass that cache. A lookup reads at most 6 model-code pages and
   ignores codes shorter than 4 characters. It sends only the catalogue's own addresses. A failed
   fetch is "unknown", never a scan error. The catalogue only feeds the third step: it never sets
   an amount or approves.
6. **You confirm.** Every judged lot stays on the triage view with the model's recommendation,
   its verdicts as counts and the proposed amount beside the grid's; the lot's page has the
   verdict and evidence per requirement and the photos it looked at. The photos are numbered and
   open in the lightbox, and a ladder requirement shows its steps (sheet, photo, maker) with the
   photo number and, for the maker step, the catalogue grade and its mark. Approve, change the
   amount, or reject. Telling it what it got wrong ("The model was wrong about … because …")
   writes a line into the playbook for that model code, so the next judgement reads your
   correction. `bot.cmd playbook CODE` prints what the model is told, and the guide's page
   shows it.

Providers are free tiers, tried in order: Google Gemini 3.5 Flash-Lite, then Gemini 3.1
Flash-Lite (a separate free quota on the same key), then Groq's text-only model for lots that
need no photos (Groq had no free vision model on 2026-09-28). Limits and model names live in
`config.yaml`, keys in `.env` (see `.env.example`); when a provider retires a model, the next one
takes over and the name is a one-line config change. Google may use free-tier content to
improve its products; Groq states it does not train on it. Neither ever receives the bid grid.
When no provider can answer, the lot still reaches you, marked "model unavailable", and
`bot.cmd judge --pending` (or **Judge pending lots** on the Runs page) catches up later.
A lot whose sheet notes hold only a SalesPoint now raises a flag ("sheet notes (SalesPoint)") and so
costs a model call against these quotas.

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
the checklist as "NOT understood". Add lines on the guide's page (`/guides/<code>`, which also
lists the rules your rejections suggest), when rejecting a lot on its page, with
`bot.cmd skip-line CODE "..."`, or by editing the file.

## CLI

| Command | Purpose |
|---|---|
| `bot.cmd learn` | Learn mode (`bidbarn.cmd` L, or **Start Learn mode** on the dashboard): watch you bid, then scan the same codes, compare, suggest rules |
| `bot.cmd bid [--skip-scan] [--no-place] [--headed] [--no-browser]` | Bid mode (`bidbarn.cmd` B, or **Start Bid mode** on the dashboard): sign-in check, guides, scan, review, place. Under the dashboard it waits for the header's Finish button instead of serving its own review page |
| `bot.cmd dashboard [--no-browser]` | Serve the dashboard and the review pages (`dashboard.cmd`). If it is already running, only opens the page |
| `bot.cmd login [--wait]` | One-time sign-in to Nichibo and the Carbarn dashboard. `--wait` needs no keyboard: it opens a window only if a site is signed out and continues by itself |
| `bot.cmd check-login` | Says, without a window, whether both sites are signed in |
| `bot.cmd fetch-rules [-m CODE] [--headless]` | Snapshot the Team Bid guides |
| `bot.cmd rules [CODE]` | Print the parsed grid of a model |
| `bot.cmd scan [-m CODE] [--limit N] [--fresh] [--rescan] [--headless]` | Search, evaluate, write proposals |
| `bot.cmd review` | Serve the same pages without opening the browser (triage at `/review`, table at `/list`) |
| `bot.cmd replay [--apply] [--answer KEY=VALUE]` | Re-apply current rules, skip lines and code to stored proposals, offline. `--answer odo_margin_km=2000` only previews a Rulebook answer and stores nothing |
| `bot.cmd place [--live]` | Place approved bids (dry run unless every lock is off) |
| `bot.cmd judge [--pending] [-m CODE] [--limit N] [--no-browser]` | Ask the free vision model about stored lots still waiting for a judgement (rate-limited, resumable). `--no-browser` opens no lot page for photos; such lots stay pending |
| `bot.cmd playbook [CODE]` | What the judgement model is told about how you decide: your principles, and your corrections and comments per model code. Without a code, lists every code with notes |
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
| "The dashboard is running on that address" from `bidbarn.cmd bid` | Start Bid mode from the dashboard instead, or close the dashboard window first. |
| A Learn run started from the page shows no browser window | Check the run's log for "Watching. Two tabs are open"; if it is there and no window came up, see the note on `CREATE_NO_WINDOW` in `docs/dashboard.md` (Not verified yet). Meanwhile `bidbarn.cmd learn` opens it from a console. |
| "STOPPED: No rules snapshot found" | Run `bot.cmd fetch-rules` (or `scan.cmd`, which does it first). |
| "Rules snapshot is N h old" | Snapshots older than `max_rules_age_hours` are refused. Fetch again. |
| OCR "unavailable" on the review page | Install the Visual C++ runtime linked above and restart. |
| "Images not published yet" banner | Nichibo still serves placeholder images for that lot. The next scan re-checks it. It carries its grid amount from the card, so you can leave it or approve it as it is. |
| A page load times out | Nichibo stalls now and then; loads retry once. Re-run the scan, it continues where it stopped. |
| `bot.exe` "Permission denied" | Windows App Control blocks the launcher. Use `bot.cmd`. |
| A DLL "could not be found" or "blocked by policy" | Same policy. `jiter` is pinned to a version it allows; do not upgrade it blindly. |

## Tests

```bat
.venv\Scripts\python -m pytest -q
```

834 tests: the golden cases from the manual walkthrough against a GDH206 guide captured from the dashboard, variant
resolution on captured Alphard and E-Class guides, the seat rule, OCR parsing on three real sheets
plus the header-band crop and its fallback, an offline end-to-end scan against captured Nichibo
pages, the card-and-sheet path that opens no page, the placeholder path that parks a lot
without a page and carries its grid amount, the odometer bands (0 km, a km no band covers,
above the last band), the grid ceiling, placement re-verification of card-judged lots, the
cache and replay paths, the skip-line grammar and where the scan applies it, review telemetry and
insights, the triage view, the HAR parser and join (with a socket guard proving no network),
the learn and bid modes (card attribution of comments, the Finish hand-off), the judgement
layer (provider fallback and quotas, requirement classification, candidates, playbook, the
stubbed model in the scan, the pending pass, replay, the review page and corrections), the
Stock URL guard and the testing lock, the guide pages (grid, chips, standing instructions,
suggestions, a code shared by two guides, hostile codes),
and the dashboard: the progress lines a run prints and that a signed-out or stopped scan ends at
once, the browser profile lock, the runs table on an old database, the job runner on harmless
stand-in commands (finish, fail, stop, a second start refused, a run left by a closed
dashboard), the command table (no option and no value can produce a live placement), every page
on an empty database, the status and the day's steps, the log and its live stream, the folded
single steps, the slim review row, a Bid run waiting at the review, a Learn run's session page,
that a request from another site can neither start a command nor approve a lot, and that the
dashboard package decides nothing.

## Project layout

```
bidbarn/
├── dashboard.cmd                                            the dashboard in your browser
├── bidbarn.cmd                                              one click: Learn mode or Bid mode
├── bot.cmd · login.cmd · scan.cmd · review.cmd · place.cmd   the separate steps
├── config.yaml                                              locks, judgement providers, cache, pacing, review address
├── .env.example                                             the keys the judgement layer reads from .env
├── CLAUDE.md                                                rules for anyone working on the code
├── PRODUCT.md · DESIGN.md                                   who the page is for, and how it looks
├── docs/endpoints.md                                        both sites' endpoints, from the HAR captures
├── docs/dashboard.md                                        the dashboard's routes, runs and progress lines
├── docs/observations.md                                     what watch mode has shown about the manual flow
├── rules/overrides/                                         standing instructions (git-ignored)
├── rules/playbook/                                          the playbook the judgement model reads (git-ignored)
├── src/auction_bot/
│   ├── carbarn/      client.py (dashboard API) · models.py (guides, variants, grids, modifiers)
│   ├── nichibo/      browser.py (session + URL guard) · search.py · parsers.py · vehicle.py
│   │                 sheet_ocr.py (local OCR) · cache.py · bid.py (placement) · facts.py
│   ├── rules/        hard.py (deterministic rules) · instructions.py (skip lines) · hints.py
│   │                 soft.py + schema.py (the opt-in OpenAI sheet check)
│   ├── catalog/      client.py (kurumaerabi.com: allow-list, pacing, cache) · kurumaerabi.py (parsers)
│   │                 lookup.py (model code and year to a finding: cruise marks, Welcab seat system)
│   ├── judge/        client.py (free providers, fallback, quotas) · requirements.py · candidates.py
│   │                 playbook.py · judge.py (prompt, schema, deterministic decision)
│   │                 topics.py (which text is cruise or Welcab) · ladders.py (sheet, photo, maker verdicts)
│   ├── review/       the local page (FastAPI): app.py (review routes, the only place a lot is decided),
│   │                 templates, static/ (bidbarn.css, review.js, dashboard.js, icons, vendor/)
│   ├── dashboard/    jobs.py (the command table and the job runner) · status.py (the state of the day)
│   │                 routes.py (overview, runs, insights, settings, the API) · guides.py (a page per guide)
│   ├── config.py     config.yaml as an object; placement_mode() holds the testing lock
│   ├── progress.py   the PROGRESS and SUMMARY lines a run prints for the dashboard, and Stop
│   ├── lock.py       one command at a time on the bot's browser profile
│   ├── pipeline.py   scan (card + sheet first, page only when needed) and offline replay
│   ├── run.py        the one-click modes: learn (watch, compare, suggest) and bid (sign-in, guides, scan, review, place)
│   ├── insights.py   how you decide, per model; suggested skip lines
│   ├── learn.py      HAR of a manual session vs the bot's proposals, offline
│   ├── watch.py      watch mode: records Jim's own session (clicks, hovers, reasons, bids)
│   ├── store.py      SQLite
│   └── cli.py        commands
└── tests/            fixtures are real guides and captured pages (tests/fixtures/catalog/ holds
                      public catalogue pages)
```

## Status

Testing phase. The testing lock is on and every approval is test data. The speed rework is in:
lots are judged from the search card and the sheet, the page is opened only when a rule needs
it, and the OCR reads the sheet header first. The judgement layer is in (2026-09-28): a free
vision model pre-reads the lots that need a look, and photos are downloaded only for those. The
dashboard is in (2026-09-29) and was simplified the day after: Learn mode and Bid mode start
from the overview, the single steps sit in a fold on the Runs page, and every guide has its own
page. The switch-over (2026-10-27) made the desk the home page and moved the overview under
System. It has been exercised on stand-in commands and on a throwaway database; no run started
from the page has gone through to the end on real data yet, and the points still to confirm on
the office PC are listed under "Not verified yet" in `docs/dashboard.md`. Next: the first
complete Bid run from the dashboard, the first review pass on the triage view, and the first
look at Insights once decisions exist.

### Open questions for Carbarn

Answer these on the Rulebook page (`/rulebook`, see [docs/rulebook.md](docs/rulebook.md)). Each item
names its Rulebook id in brackets.

- (`interior_years`) Interior years of a build window count as inside it (registered 2020, window 07/2019 to
  06/2023): confirm.
- (`selected_pair`) The selected pair decides the verdict even when another active pair would cover the lot:
  confirm.
- (`no_selected_pair`) With no selected pair the active pairs are alternatives, and the bot may only recommend
  against a lot when every pair fails it: confirm.
- (`model_code_unmatched`) A model code that does not match the SEV text reads "confirm import"; one that cannot be
  parsed is not checked (the card says "eligible (model code not checked)"); neither reads
  "cannot import"; an unknown registration year reads confirm: confirm.
- (`welcab_chips`) Chips with Welcab or seat words (for example "Slope type -180K") are still judged by the
  model, not by the ladder. How should they read?
- (`welcab_refinements`) Two Welcab refinements made during the build on 2026-10-21, to confirm: (1) the maker's
  catalogue alone never makes a lot a Welcab. Its single seat system decides only for a lot the
  sheet or the photos call a Welcab; otherwise only a catalogue grade the model names that is a
  Welcab row decides, and with nothing named the lot fails for want of a seat statement (review
  on a short photo set). (2) A negated Welcab note that names a failing seat system ("No ramp /
  slope type", "No wheelchair lift") is read as the seat requirement itself, so a plain car fails
  it; any other negated Welcab note ("No welcab", "No swivel seat") stays a plain text requirement
  the model judges. "Wheelchair spec" (車いす仕様) is read as the slope type, so "Without
  wheelchair spec" is the seat requirement too and a plain car fails it. Confirm both, and whether
  "Without wheelchair spec" should instead mean "no conversion".
- (`guide_213271`) Guide 213271 has two selected pairs whose cells overwrite each other; not changed here.
- (`odo_margin`) Odometer margin (from the teaching session of 2026-09-15): the reviewer skips a lot that is
  already at the last band's cap ("already 80k") and, for a lot exactly on a band boundary
  ("50k on the mark"), uses the next band's value, because the odometer rises between the
  auction and the warehouse. Bands are upper-bound inclusive today, so the bot would price
  50,000 km in the 40-50K band and would bid on an 80,000 km lot with an 80K cap. With the
  judgement layer on, such a lot is flagged as an odd case and the next band's value is offered
  as a candidate amount, a pre-fill for the reviewer. What margin should the bot itself apply,
  in km, and does it apply to every model? The Rulebook offers none, 1,000, 2,000, 3,000 and 5,000
  km for every model, with a preview before Apply; none is today.
- (`gdh206k`) The GDH206K variant is inactive on the guide although the walkthrough bids on it; the bot
  treats every variant as biddable (the dot is the selected tab). Confirm.
