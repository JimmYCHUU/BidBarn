# The desk (phase 1)

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
are read from the stored placement. A dry run leaves a lot approved and waiting, so the next run
takes it again.

### The scan change

A lot that reaches the review (BID or NEEDS_REVIEW) now has its own page opened once, to read its
auction day and hall. That is one more paced page load per such lot, once: the day is stored and
carried forward while it has not passed (today in Japan time or later). A lot number seen again after
its auction day may be a new listing, so its page is read again. The first scan after the merge fills
in the day for every lot already waiting, one paced load each. The page loads go through the URL guard
like any other.

`day_pages` (pages read only for the day) is in the scan's own summary line and in the run's stored
summary. It is not on the dashboard run page yet. That is a small follow-up after the pending
dashboard edits are committed.

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


## What phase 1 leaves out

- The Rulebook.
- Scan now on the desk. The Runs page still starts scans.
- Reopening a sent decision. A decision is final once sent, as on today's pages.
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
| Delete (a lot, without a decision) | Clear, `D` twice. **Delete session** stays on `/review` (phase 2) |
| Open on Nichibo | Open on Nichibo in the lot header, `O` |
| Sheet full size | `S` and `Z`, or a click on the sheet |
| Photos | Numbered photos, `1` to `9`, `Z`; look-at chips jump to a photo |
| `x2 guides` | One row marked "2 guides" with a guide switch |
| Decided state (how long, what was looked at) | The Decided lane; the lot view shows the decision. Timings stay on `/p/<n>` (phase 2) |
| Telemetry (open, sheet zoom, photos click, Nichibo click, amount edit) | Posted to today's event route |
| Triage order and model heads | The same order. The head links to the guide stay on `/review` (phase 2) |
| Next lot after a decision | The next row in the lane is selected |
| Delete session, Finish, Pause, Resume | Stay on the old pages and the overview (phase 2) |
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
- [ ] Optional, after the merge (or only if you sign in to Nichibo in the copy): on `/bids`, click Place as dry run. A run starts; each approved lot gets a screenshot and "Dry-run placed", and nothing is submitted. The lock keeps it a dry run. This opens Nichibo lot pages with the bot's browser profile, which is empty in the worktree, so it needs a sign-in and real Nichibo traffic. Skip it otherwise.

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
the bot runs one at a time. The optional dry run on the bid sheet (last item of the check list) is
the one thing that does need Nichibo.
