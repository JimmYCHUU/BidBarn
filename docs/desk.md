# The desk (phase 1)

The desk is a second review page, beside today's. `/`, `/review`, `/list` and `/p/<n>` work as
before. The desk is at `/desk` (nav item **Desk**) and the bid sheet is at `/bids` (nav item
**Bid sheet**). Both read the store and the evidence files already on disk. They send nothing to
Nichibo or Carbarn.

## What the desk is

Lots are grouped by auction session (hall and day), sessions in date order. The bid cutoff time is
not known yet, so a session shows its date and no countdown. Lots whose day was never read sit in
one session, "Auction day not known", shown first. Nothing is dropped.

Each session has four lanes:

| Lane | What lands there |
|---|---|
| Needs a look | Anything the model observed, anything unclear or failing, any odd-case flag, an import eligibility verdict other than eligible, an ambiguous variant, or no amount. Each row carries up to two "look at" chips. |
| Card-clear | A lot whose requirements were all answered by the card, the sheet header the OCR reads, or the rules, and nothing else. You still check the sheet, then decide. |
| Waiting | Images not published yet, or the model has not judged the lot yet. |
| Decided | Approved and rejected lots. |

Inside a lane the order is today's triage order: model, best grade, fewest km.

Card-clear is strict (Jim, 2026-10-26). With `sheet_check: judge`, most lots whose guide has a text
requirement (cruise control, seat colour, Welcab) are in Needs a look, because the model reads
those notes. Card-clear is mostly the lots whose guide has no text requirement.

A vehicle proposed under two guides is one row marked "2 guides", with a switch between the
guides. Approve it once. The other guide's proposal is never decided for you.

A lot opens beside the list (three panes from 1280 px wide), over the list under 1280 px, and full
screen under 900 px. It shows the facts, the amount with the grid and the used cell lit, the
requirements with their source and verdict (ladder steps for cruise control and Welcab), the import
eligibility card, the model's pre-fill in violet, the sheet and the numbered photos, and a decision
bar. The strip at the top shows the sign-in, the guide snapshot age, the model quota and the
testing lock.

### The 10-second undo

Approve, Reject and a changed amount do not reach the store at once. A decision waits 10 seconds,
with an **Undo** button (key `U`). It is sent when the window ends, or straight away when the page
is left (closing the tab, reloading, following a link). It goes through today's
`POST /p/<n>/decide` route, so every rule of that route applies. If the route refuses it (the lot
was decided elsewhere, or a replay superseded it), the desk says so and puts the lot back.

**Clear** (key `D`, pressed twice) takes a lot off the desk through today's delete route. It is
never a decision: nothing is learned from it, and the lot is back at the next session's scan if it
still matches.

No control decides more than one lot.

### The bid sheet

`/bids` lists the approved lots per auction session with the amount, the start price, the import
eligibility and the session's exposure (the sum of its amounts, shown and never capped). A band
states the testing lock. **Place as dry run** starts today's `place` command, the same as the
dashboard's single step: each lot's page is opened, re-checked, the amount typed and read back, and
a screenshot saved. Nothing is submitted. Live placement cannot be started from this page, and
while `project_status` is `testing` every placement is a dry run anyway. Results and screenshots
are read from the stored placement.

### The scan change

A lot that reaches the review (BID or NEEDS_REVIEW) now has its own page opened once, to read its
auction day and hall. That is one more paced page load per such lot, once: the day is stored and
carried forward. The first scan after the merge fills in the day for every lot already waiting, one
paced load each. The page loads go through the URL guard like any other.

`day_pages` (pages read only for the day) is in the scan's own summary line and in the run's stored
summary. It is not on the dashboard run page yet. That is a small follow-up after the pending
dashboard edits are committed.

## The keys

| Key | What it does |
|---|---|
| `J` / `K`, or arrows | Next, previous lot in the lane |
| `Enter` | Open the lot (narrow widths) |
| `A` | Approve at the shown amount |
| `E` | Change the amount: the candidates first, or type one (flagged "not a candidate") |
| `R` | Reject, with a reason |
| `D` twice | Clear the lot, not a decision |
| `U` | Undo the newest waiting decision |
| `O` | Open the lot on Nichibo (a plain link, in your own browser) |
| `S` | Show the sheet |
| `1` to `9` | Show that photo |
| `Z` | Zoom the sheet or photo shown |
| `?` | The key list |
| `Esc` | Close a panel, or the lot at narrow widths; cancels a first `D` |

Keys do nothing while you type in a field. A held key never decides, clears or undoes a second lot.
Approve is disabled, with the reason, for a lot with no amount, and a typed amount below the start
price shows the route's own message.

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
| "The model was wrong about ... because ..." | The same fields in the reject panel, for a judged lot |
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
- [ ] Press `A` on a lot, then run `replay --apply` (or approve it on `/p/<n>`) in another window before the 10 seconds end. The desk says the lot could not be decided and the lot comes back, undecided.
- [ ] Press `R`, type a reason, tick the skip line, send. After 10 seconds the lot is Rejected and the skip line is in the guide's standing instructions.
- [ ] Press `D` twice on a lot. It leaves the desk; `/p/<n>` shows it as deleted, with no decision.
- [ ] Make the browser window 1024 px wide. Select a lot: it slides over the list. Click Close: the list returns.
- [ ] Click in the reject reason field and type `a`, `r`, `d`, `j`. The letters appear in the field and nothing is approved, rejected or cleared.
- [ ] Open `/`, `/review`, `/list` and `/p/<n>`. They look and behave as before.
- [ ] Open `/bids`. The lock band says the testing lock is on and placement is a dry run.
- [ ] On `/bids`, click Place as dry run. A run starts; each approved lot gets a screenshot and "Dry-run placed", and nothing is submitted. The lock keeps it a dry run.

## How to try it before the merge

Run the dashboard from the branch's worktree. `load_config` resolves `data/` and `rules/` under the
folder the code runs from, so the worktree needs its own copy of the data. Never link or move the
originals: copy. `<main>` is your main checkout (`D:\Jim\Auction Automation`) and `<worktree>` is the
desk branch's worktree.

1. Copy `<main>\data\bot.sqlite` to `<worktree>\data\bot.sqlite`.
2. Copy the newest file in `<main>\rules\snapshots\` (the newest `*.json`) to `<worktree>\rules\snapshots\`.
3. For each lot you want to see on the desk, copy its folder `<main>\data\evidence\<ref>\` to `<worktree>\data\evidence\<ref>\`. Copy only those folders.
4. From `<worktree>`, start the dashboard with the main checkout's Python:

   ```
   cd <worktree>
   "<main>\.venv\Scripts\python" -m auction_bot.cli dashboard
   ```

5. Open http://127.0.0.1:8765/desk.

Decisions made there are thrown away with the copy. The originals are not touched.

No Nichibo action is needed to try the desk. Do not run a scan from both places at once: the same
browser profile lock applies, and the bot runs one scan or placement at a time.
