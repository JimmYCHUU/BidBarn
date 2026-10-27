# The desk (phases 1, 2 and 3)

The desk is a second review page, beside today's. `/`, `/review`, `/list` and `/p/<n>` work as
before. The desk is at `/desk` (nav item **Desk**) and the bid sheet is at `/bids` (nav item
**Bid sheet**). Both read the store and the evidence files already on disk. They send no requests of
their own to Nichibo or Carbarn. A row thumbnail whose sheet is not on disk is loaded from Nichibo's
sheet address by your own browser, as on `/review`.

## What the desk is

Lots are grouped by auction session (hall and day), sessions in date order. The bid cutoff time is
not known yet, so a session shows its date and no countdown. Lots whose day was never read sit in
one session, "Auction day not known", shown first. Nothing is dropped.

Each session has four lanes:

| Lane | What lands there |
|---|---|
| Needs a look | Anything the model observed, anything unclear or failing, any odd-case flag, an import eligibility verdict other than eligible, an ambiguous variant, a seat count the sheet reading did not give, a reason the rules sent the lot to review (start price unknown, say), a modifier you must judge, or no amount. Each row carries up to two "look at" chips. |
| Card-clear | A lot whose requirements were all answered by the card, the sheet header the OCR reads, or the rules, and nothing else. You still check the sheet, then decide. |
| Waiting | Images not published yet, or the model has not judged the lot yet. |
| Decided | Approved and rejected lots, lots you cleared ("Cleared, not a decision") and lots covered by the other guide's approval, for the last 24 hours. |

Inside a lane the order is today's triage order: model, best grade, fewest km.

Card-clear is strict (Jim, 2026-10-26). With `sheet_check: judge`, most lots whose guide has a text
requirement (cruise control, seat colour, Welcab) are in Needs a look, because the model reads
those notes. Card-clear is mostly the lots whose guide has no text requirement.

A vehicle proposed under two guides is one row marked "2 guides", with a switch between the
guides. Approve it once. The other guide's proposal is never decided for you. Once one guide's
proposal is approved, the other one says "Covered by the approval of #n under ..." and offers no
Approve and no Change amount; Reject and Clear stay.

A lot opens beside the list (three panes from 1280 px wide), opens over the list under 1280 px, and full
screen under 900 px. It shows the facts, the amount with the grid and the used cell lit, the
requirements with their source and verdict (ladder steps for cruise control and Welcab), the import
eligibility card, the model's pre-fill in violet, the sheet and the numbered photos, and a decision
bar. The strip at the top shows the sign-in, the guide snapshot age, the model quota and the
testing lock.

### The 10-second undo

Approve, Reject and a changed amount do not reach the store at once. A decision waits 10 seconds,
with an **Undo** button (key `U`). It is sent when the window ends, or straight away when the page
is left (closing the tab, reloading, following a link). It goes through today's
`POST /p/<n>/decide` route, so every rule of that route applies. If the route refuses it, the desk
says so and nothing is recorded from the desk. A lot decided elsewhere is then in the Decided lane
with that decision; a lot a replay superseded is replaced by its newer evaluation, which waits on
the desk.

**Clear** (key `D`, pressed twice) moves a lot to the Decided lane, marked "Cleared, not a
decision", through today's delete route. It is never a decision: nothing is learned from it, and
the lot is back at the next session's scan if it still matches.

No control decides more than one lot. The second click of a double click never decides or clears,
and for 400 ms after a lot opens, Approve, Approve at this amount, Reject this lot, Clear, `A` and
`D` do nothing. So a double click on Approve cannot also approve the next lot, which opens under
the cursor. The second `D` of a clear must come at least 300 ms after the first.

### The bid sheet

`/bids` lists every approved lot that waits to be placed: the same lots, in the same order, as
`bot place` takes them (oldest proposal first). They are grouped per auction session with the
amount placement types (the approved amount, else the grid amount), the start price, the import
eligibility and the session's exposure (the sum of its amounts, shown and never capped). Each lot
shows its place in that order (#). Placement takes at most `max_bids_per_run` (25 today) per run, so
the first ones are marked **Next dry run**: they are taken by the next dry run, and the line above
the sessions says how many wait for a later run. Lots placed live in the last 24 hours are listed
apart, under Placed.

A band states the testing lock. **Place as dry run** starts today's `place` command, the same as
the dashboard's single step: each lot's page is opened, re-checked, the amount typed and read back,
and a screenshot saved. Nothing is submitted. Live placement cannot be started from this page, and
while `project_status` is `testing` every placement is a dry run anyway. Results and screenshots
are read from the stored placement. A lot placed as a dry run is done: the next dry run does not
type it again (see phase 2 below).

### The scan change

A lot that reaches the review (BID or NEEDS_REVIEW) now has its own page opened once, to read its
auction day and hall. That is one more paced page load per such lot, once: the day is stored and
carried forward while it has not passed (today in Japan time or later). A lot number seen again after
its auction day may be a new listing, so its page is read again. The first scan after the merge fills
in the day for every lot already waiting, one paced load each. The page loads go through the URL guard
like any other.

`day_pages` (pages read only for the day) is in the scan's own summary line and in the run's stored
summary. The run page shows it as "Lot pages read for the auction day".

## Phase 2: the safer store

Phase 2 changes what the store accepts, so a click that would record the wrong thing is refused.
Every refusal records nothing.

What you see:

- **One vehicle, one approval.** Approving a vehicle under its second guide is refused at once. The
  message names the guide and the lot that already hold it. One bid per vehicle stays as before.
- **A lot that changed under you.** An approval names the version of the lot the page showed. If a
  later pass changed the lot after you opened it (photos arrived, the model judged it), if a newer
  scan replaced it, or if another tab decided or cleared it, the approval is refused. The message
  says which, and the desk loads the lot again. This also covers a decision waiting in its
  10-second window.
- **Reopen.** An approved or rejected lot goes back to waiting, until it is placed live. The button
  is in the lot view of the Decided lane, on the lot's own page (`/p/<n>`) and on the bid sheet.
  Reopen is not a decision and approves nothing. It is refused while the bids are being placed, for a
  lot placed live, and for a lot whose live attempt failed. A lot a newer scan replaced (`bot scan
  --rescan`) does not go back to waiting: Reopen retires it, frees the vehicle, and the message names
  the new lot to decide. The desk opens that lot.
- **A dry run is done.** A lot placed as a dry run is not typed again by the next dry run. The bid
  sheet says "Dry run done: not typed again. Reopen it to send it again.", and the next-run marker
  skips it. A blocked dry run is tried again. A live run, when the lock is lifted one day, still
  places every approved lot that holds its vehicle, one per vehicle.
- **A failed live attempt** reads "Live attempt failed" on the bid sheet, not "Placed". It stays out
  of the queue, because it may have half-happened on Nichibo.

What stays:

- A skip line or a note to the model saved with a decision stays when you reopen it.
- The reopened decision is kept in the records, but it is no longer learned from: not by insights,
  not in the past decisions given to the model, not in the overview's recent decisions. The Today
  log still shows that it happened.
- Every placement attempt is now kept as history. The `bids` table and its one row per vehicle are
  unchanged.
- Evidence: each guide's evaluation files (`rule.json`, `hard.json`, `judge.json`, `soft.json`) now go
  to `data/evidence/<lot>/guide_<id>/`, so two guides no longer overwrite each other. The sheet, the
  photos, `facts.json`, `ocr.json` and `catalog.json` stay in the lot folder. Old files stay where they
  are.

### Choices made in phase 2, to confirm

- The other guide's lot is not closed automatically when one guide is approved. It stays on the desk
  marked "Covered by the approval of #n under ...", and an approval of it is refused.
- The check that the lot has not changed runs when the page sends it. All three pages send it. A page
  left open from before the upgrade approves once without it.
- A failed live attempt stays out of the queue, as above.
- Reopen on a lot a newer scan replaced retires the old lot and points at the new one, instead of
  refusing. The old approval is undone, and the new lot is decided as any other.

## Phase 3: Scan now

Phase 3 puts the day's scan on the desk. Until now a scan was started from the Runs page or by Bid
mode, which then waits for your review.

The scan bar is the row under the page heading, above the board. It says what the scan is doing:

| The bar says | What it means | Button |
|---|---|---|
| No scan yet. | No scan has finished that the dashboard knows of. | Scan now |
| Last scan 14:05, 12 new for the desk. | The newest finished scan (Scan now, Scan Nichibo or Bid mode) and how many lots it sent to the desk. A scan from another day shows its day too: "Last scan Mon 26 Oct 14:05, ...". | Scan now |
| Scanning: guide 3 of 12, 5 new for the desk so far. | A scan is running. Before the first guide it says it is checking the sign-in and the guides. | Stop |
| Sign in to Nichibo in the bot's window. The scan carries on after that. | A site was signed out at the start. The bot's window is open. The bar names the site: Nichibo, the Carbarn dashboard, or Nichibo and the Carbarn dashboard. The bar is amber. | Stop |
| Nichibo signed out during the scan. Sign in in the bot's window; the scan carries on after that. | A site signed you out part way through, and the bar names it. Same window, same wait. | Stop |
| Bid mode is running. Scan now is offered again when it has finished. | Another job holds the browser (Bid mode, a placement, a scan from a console). Nothing is started. The name is the other job's. | none |
| The last scan stopped at 14:20: Nichibo signed you out. Press Scan now to sign in and scan again. | The newest scan ended because a site signed you out, and the bar names it. Scan now checks the sign-in first and opens the window. | Scan now |
| The last scan stopped at 14:20: the reason. | The newest scan failed, or ended in another way (the dashboard was closed while it ran, say). With no reason stored it says "The last scan failed at 14:20." | Scan now |
| The last scan was stopped at 14:20. | You pressed Stop. | Scan now |

The times are this PC's local time. A time from an earlier day has its day in front, for example
"The last scan was stopped at Mon 26 Oct 14:20."

If you press Scan now while something else holds the browser, the refusal shows in the bar and
stays until the state changes.

What Scan now does:

- It checks the sign-in, fetches the guides, and scans Nichibo for every active guide, with the same
  pacing and the same cache as any scan. Then it ends.
- It never waits for your review and never places a bid. Lots appear on the desk as they are stored.
  Nothing is approved by it.
- It fetches the guides every time, so a scan never runs on yesterday's guides.

If Nichibo signs you out during the scan, the bot checks both sites again without a window. If one
is signed out, a sign-in window opens, as `bot login --wait` does, and waits up to 15 minutes for
you; the bar names the site to sign in to. When you have signed in, the scan starts again at the
first guide, reusing the cached pages and the lots already stored. The run's counts add up both
parts, and its run page says "Signed in again during the scan: yes". This happens once per run: a
second sign-out stops the scan, and the bar says "Nichibo signed you out. Press Scan now to sign in
and scan again."

Stop is how you pause. Press Stop, and press Scan now again later: the second scan reuses the cached
pages and the lots already stored, so it carries on from where the first one stopped, as far as the
cache goes. Pages older than `cache_hours` are read again. It also carries on the first one's
session, so a lot you cleared stays out (see the choices below).

The board refreshes while a scan runs (every 30 seconds, and once at the end). A refresh keeps a
decision that is waiting in its 10-second undo window, and keeps the open lot open. The bar itself
checks every 5 seconds while a scan runs, every minute when idle, and stops checking while the tab is
hidden.

Bid mode and Learn mode still work on the old pages. They leave the daily routine at the
switch-over, when the desk becomes the home page.

Small fixes in this phase:

- The run page shows "Lot pages read for the auction day".
- The overview's "Scanned" step counts a Scan now.
- Reopen of a lot a newer scan replaced keeps its time, and the desk jumps to the new lot.
- A lot cleared by a newer scan no longer counts as replacing an older decision.

The desk starts one command only, `scan-now`, and can stop it. It cannot start a live placement.

### Choices made in phase 3, to confirm

- The dashboard's job runner is kept. It already runs one Nichibo job at a time, holds the browser
  profile, and has Stop, logs and the run history. The idea of an in-process queue is not built. If
  this is wrong, the only loss is that "checkpoints" stay as the cache plus the stored lots.
- "Pause scanning" is Stop. A stopped scan is carried on by pressing Scan now again, from the cache and
  the stored lots. If this is wrong, a restarted scan reads again the search pages that are older than
  `cache_hours`.
- A Scan now after one that was stopped or failed continues that scan's session, so a lot you cleared
  on the desk stays out, as it does after Pause and Resume in Bid mode. After a scan that finished,
  the next Scan now starts a new session, and the lots you cleared come back to be checked again.
  Deleting the session on `/review` ends it too. If this is wrong, a cleared lot stays out until a
  scan finishes and the next one starts, or comes back sooner than you expect.
- Signing in mid-scan: the scan opens the sign-in window itself and carries on once, instead of ending
  and asking you to press a button. This is the one wait on a person. If this is wrong, a window opens
  on your screen while you are away, and the run ends after 15 minutes.
- Bid mode and Learn mode are not removed in this phase. They leave the daily routine in the
  switch-over.
- Scan now fetches the guides every time, as Bid mode does.

## The keys

| Key | What it does |
|---|---|
| `J` / `K`, or arrows | Next, previous lot in the lane |
| `Enter` | Open the lot (narrow widths) |
| `A` | Approve at the shown amount |
| `E` | Change the amount: the candidates first, or type one. A typed amount wins over the picked candidate, and the edit is recorded as an `amount_edit` event |
| `R` | Reject, with a reason |
| `D` twice | Clear the lot, not a decision |
| `U` | Undo the newest waiting decision |
| `O` | Open the lot on Nichibo (a plain link, in your own browser) |
| `S` | Show the sheet |
| `1` to `9` | Show that photo |
| `Z` | Zoom the sheet or photo shown |
| `?` | The key list |
| `Esc` | Close a panel, or the lot at narrow widths; cancels a first `D` |

Letter keys do nothing while you type in a field. `Esc` still closes the panel, `Enter` in the
amount field approves through the 10-second window, and `Ctrl+Enter` in the reason field rejects.
A held key never decides, clears or undoes a second lot. Approve is disabled, with the reason, for a
lot with no amount, and when the shown amount is below the start price. The page refuses a typed
amount below the start price rounded down to thousands: "The amount is below the start price
(nnK)." The route refuses the rest: an amount between the two (512K for a start price of 512,345
yen, say) waits out the 10 seconds, then the desk shows the route's refusal and nothing is recorded.


## What phases 1 to 3 leave out

- The Rulebook.
- Search across sessions.
- Ctrl+K.

## Parity with today's pages

Every action on `/review` and `/p/<n>`, and where it is on the desk.

| Today | On the desk |
|---|---|
| Approve at the proposed amount | `A` or Approve in the decision bar, with the 10-second undo |
| Change amount | `E` or Change amount: candidates first, a typed amount allowed |
| Reject with a reason | `R` or Reject: reason field, sent with the undo window |
| Standing instruction ("save as standing instruction", `skip:` line suggested from the reason) | The same checkbox and text in the reject panel |
| "The model was wrong about ... because ..." | The same select in the reject panel, for every lot, with "(nothing to correct)" as the default |
| Delete (a lot, without a decision) | Clear, `D` twice. **Delete session** stays on `/review` |
| Open on Nichibo | Open on Nichibo in the lot header, `O` |
| Sheet full size | `S` and `Z`, or a click on the sheet |
| Photos | Numbered photos, `1` to `9`, `Z`; look-at chips jump to a photo |
| `x2 guides` | One row marked "2 guides" with a guide switch |
| Decided state (how long, what was looked at) | The Decided lane; the lot view shows the decision. Timings stay on `/p/<n>` |
| Telemetry (open, sheet zoom, photos click, Nichibo click, amount edit) | Posted to today's event route |
| Triage order and model heads | The same order. The head links to the guide stay on `/review` |
| Next lot after a decision | The next row in the lane is selected |
| Delete session, Finish, Pause, Resume | Stay on the old pages and the overview |
| Start a scan (Runs page, Bid mode) | Scan now in the scan bar (phase 3). It never waits for review and never places. Bid mode and Learn mode stay on the old pages |
| Reopen (phase 2) | **Reopen** in the lot view of the Decided lane, on `/p/<n>` and on the bid sheet |
| All lots table with filters | Stays at `/list` |

## Check list for your hands-on check

Do these on the copy (see below). Each line is one action and what you should see.

- [ ] Open `/desk`. Press `A` on a lot, then `U` within 10 seconds. The lot stays pending; `/p/<n>` shows no decision.
- [ ] Press `A` on a lot and wait 10 seconds. The lot moves to Decided; `/p/<n>` shows Approved.
- [ ] Press `A` on a lot, then close the tab within 10 seconds. Reopen the desk: the lot is Approved.
- [ ] Open a lot on the desk, then approve the same lot on its old page `/p/<n>` in the copy's dashboard. Back on the desk, press `A` on it and wait 10 seconds. The desk says the lot changed and nothing was recorded from the desk; the lot is in the Decided lane with the decision from `/p/<n>`.
- [ ] Double-click Approve on a lot. Only that lot is queued (one "Approved ... · Undo" line); the next lot opens and stays undecided. Press `U` to take the one decision back.
- [ ] Find a vehicle marked "2 guides". Approve it under one guide and wait 10 seconds. Open the other guide's proposal (the guide switch, or its row in Decided): it says "Covered by the approval of #n under ...", has no Approve and no Change amount, and `A` says it is not approved again.
- [ ] Press `R`, type a reason, tick the skip line, send. After 10 seconds the lot is Rejected and the skip line is in the guide's standing instructions.
- [ ] Press `D` twice on a lot. It moves to the Decided lane as "Cleared, not a decision"; `/p/<n>` shows it as deleted, with no decision.
- [ ] Make the browser window 1024 px wide. Select a lot: it opens over the list. Click Close: the list returns.
- [ ] Click in the reject reason field and type `a`, `r`, `d`, `j`. The letters appear in the field and nothing is approved, rejected or cleared. `Esc` closes the panel.
- [ ] Open `/`, `/review`, `/list` and `/p/<n>`. They look and behave as before.
- [ ] Open `/bids`. The lock band says the testing lock is on and placement is a dry run. The lots are numbered in the order placement takes them, the first ones (up to 25) are marked Next dry run, and the line above the sessions says how many wait for a later run.
- [ ] Optional, after the merge (or only if you sign in to Nichibo in the copy): on `/bids`, click Place as dry run. A run starts; each approved lot gets a screenshot and the bid sheet marks it "Dry run done", and nothing is submitted. The lock keeps it a dry run. This opens Nichibo lot pages with the bot's browser profile, which is empty in the worktree, so it needs a sign-in and real Nichibo traffic. Skip it otherwise.

- [ ] Phase 2: approve a vehicle under one guide, then try to approve it under its other guide. It is refused at once, and the message names the first guide and lot.
- [ ] Phase 2: approve a lot, then press Reopen in its lot view (Decided lane). It returns to waiting, and `/p/<n>` shows no decision. The same button is on `/p/<n>` and on `/bids`.
- [ ] Phase 2: on `/bids`, click Place as dry run twice (needs the sign-in, as above). The second run does not type the lot again, and the bid sheet says "Dry run done: not typed again. Reopen it to send it again."
- [ ] Phase 3: on the copy, with the sign-in done in the bot's window, press Scan now in the scan bar. The bar changes to "Scanning: ..." with a Stop button, and lots start to appear on the board. Needs real Nichibo traffic; skip it otherwise.
- [ ] Phase 3: watch the bar. The guide count goes up. A lot you have open stays open, and a decision waiting in its 10 seconds is not lost when the board refreshes.
- [ ] Phase 3: press Stop. The bar says "The last scan was stopped at ..." with Scan now.
- [ ] Phase 3: press Scan now again. The scan carries on from the cache and the lots already stored, and ends with "Last scan ..., n new for the desk."
- [ ] Phase 3: nothing was approved or placed by the scan. `/bids` is the same as before.

## How to try it before the merge

The program reads `data/` and `rules/` from the folder of the code that is imported (`ROOT` in
`auction_bot.config`). The main checkout's venv has an editable install that points at
`D:\Jim\Auction Automation\src`. Started as it is, it imports the main checkout's code, with your real
`data/` and `rules/`, and has no `/desk`. So the copy is started with `PYTHONPATH` set to the
worktree's `src`. Never link or move the originals: copy. `<worktree>` is the desk branch's worktree.

1. Close the real dashboard first. The copy must be the only dashboard running: `config.yaml` uses port 8765, and if the real one is up the command just opens a tab on it.
2. Copy `D:\Jim\Auction Automation\data\bot.sqlite` to `<worktree>\data\bot.sqlite`.
3. Copy the newest `*.json` in `D:\Jim\Auction Automation\rules\snapshots\` to `<worktree>\rules\snapshots\`.
4. For each lot you want to see on the desk, copy its folder `D:\Jim\Auction Automation\data\evidence\<ref>\` to `<worktree>\data\evidence\<ref>\`. Copy only those folders.
5. Open a fresh PowerShell window and go to the worktree:

   ```
   cd <worktree>
   $env:PYTHONPATH = "<worktree>\src"
   ```

6. Check it before starting:

   ```
   & "D:\Jim\Auction Automation\.venv\Scripts\python" -c "import auction_bot.config as c; print(c.ROOT)"
   ```

   It must print the worktree path. If it prints `D:\Jim\Auction Automation`, stop: the copy would run on the real data.
7. Start the dashboard:

   ```
   & "D:\Jim\Auction Automation\.venv\Scripts\python" -m auction_bot.cli dashboard
   ```

8. Open http://127.0.0.1:8765/desk.

Decisions made there are thrown away with the copy. The originals are not touched.

No Nichibo action is needed to try the desk, the lot views or the old pages. Do not start a scan or
a placement from both places at once: the same browser profile lock covers scans and `place`, and
the bot runs one at a time. The optional dry run on the bid sheet and the Scan now lines of the check list are
the things that do need Nichibo.
