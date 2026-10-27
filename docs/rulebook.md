# The Rulebook (phase 4)

The Rulebook is a page at `/rulebook` (nav item **Rulebook**). It is where you answer the open
questions about the rules, on the page, instead of in a chat. It has three parts:

- **Decided.** The rules you and Carbarn have already confirmed, each with where it is written, and
  with its date where the source gives one. Below them, **Your answers**: every answer you have given,
  as a numbered version.
- **Open questions.** The questions the README lists under "Open questions for Carbarn". Each shows
  the question, what the bot does today, and how many waiting lots it touches. For the odometer
  margin that is how many waiting lots are within 5,000 km of a band's upper bound or the cap.
- **Locked.** The hard rules (the testing lock, no bid without your approval, one bid per vehicle,
  never the Stock side of Nichibo, scraped data kept, no paid model APIs, eligibility is a note and
  never a skip). They have no control at all. No page changes them.

Answering a question never approves or places a bid. An answer only changes how the bot reads a lot,
and every lot still waits for you.

## How to answer

A question has one of two kinds.

**A question with a setting** (today only the odometer margin). You pick one of the listed choices,
press **Preview**, read what it would change, and press **Apply** if you are happy.

**A question to confirm** (every other open question). Two buttons:

- **Keep it as it is** records that you confirmed what the bot does today, with the date. No lot
  changes and the question leaves the open list.
- **Ask for a change** records your words as a change request, listed under the question. Nothing in
  the bot changes until the code does. It is a note to whoever changes the code, not a switch.

## Preview and Apply

**Preview** replays the stored proposals as if your choice were already the answer, using
`bot replay --answer odo_margin_km=2000`. It is a normal run, so it shows in Runs as "Rulebook
preview", and only one job runs at a time. A preview:

- sends nothing to Nichibo, Carbarn or the maker's catalogue, and calls no model. In `judge` mode
  replay calls none anyway; the opt-in paid `openai` mode is forced off for a preview.
- writes no file and stores nothing. It leaves the evidence files and the proposals as they are.
- lists only what your answer itself changes: each waiting lot read under today's answers and again
  with the new one. It counts a lot changed in exactly the way Apply does, so it includes a lot whose
  recommendation or proposed amount changes, and says how many lots would need a fresh judgement.
  The table shows at most 500 lots; the sentence above it counts every lot.
- says separately how many other stored lots are already out of date with today's rules. Apply
  stores those changes too, because it is `replay --apply`, which brings every waiting lot up to date.
  That includes a lot parked because its images are not published yet: it is priced again from its
  search card, the way a scan parks it.

**Apply** is enabled only after a preview that finished. When you press it, the page saves your answer
as a new version, then starts `replay --apply`. That supersedes waiting proposals whose outcome
changed. An approved or rejected lot keeps its decision. Apply is refused, with words, when the
preview:

- is of another value than the one chosen;
- was made before a newer version (preview again);
- is still running, or did not finish;
- could not check some lots (it has errors);
- cannot be checked against the Rulebook version;
- was made before the guides or the waiting lots changed: a newer guide snapshot, or a lot stored,
  decided, reopened or judged since the preview.

An answer is a version in `data/bot.sqlite`, never edited. A later answer adds a version holding every
answer so far. Nothing is deleted. The command line cannot apply an answer: `bot replay --answer`
only previews.

## The odometer margin

From the teaching session of 2026-09-15. A margin of N km prices a lot as if it had N more km. It is
used for two things only: which odometer band the lot falls in, and the cap of the variant. A
modifier that names km still reads the km listed. The same km is used everywhere the bot decides on a
band: the grid, the card check before a lot's page is opened, the judge's odd-case flags and its "next
band" candidate. Your two examples:

- "50k on the mark": a lot listed at 50,000 km sits in the 40-50K band today. With a margin of 1,000
  km it is read as 51,000 km and uses the next band.
- "Already 80k": a lot listed at 80,000 km against an 80K cap is bid on today. With any margin it is
  read as over the cap and is skipped.

The choices are none, 1,000, 2,000, 3,000 and 5,000 km, the same for every model. No margin (0) is
today's behaviour and the default. A margin per model is a change request.

## What stays locked

Everything in the Locked list. The Rulebook also offers no free-form setting, no amount, and no choice
that is not in its catalogue (`src/auction_bot/rulebook.py`). The golden test cases
(`tests/test_hard_rules.py`) are not touched; they run with no margin.

## Choices made in the Rulebook build, to confirm

- The odometer margin is the one setting built. It comes from your teaching session and the README's
  open question. Nothing changes until you answer and apply after a preview. If this is wrong, the loss
  is a setting nobody uses.
- The margin means "N more km, for the band and the cap only". A modifier that names km still reads the
  listed km. If this is wrong, a km modifier on a lot near a boundary reads differently from what you
  meant, and the preview shows every lot it changes before you confirm.
- The choices are none, 1,000, 2,000, 3,000 and 5,000 km, for every model. If this is wrong, say so in
  a change request.
- The margin also moves the judge's odometer flags and its "next band" candidate, so the flag you see
  agrees with the amount the grid gives. If this is wrong, a flag you rely on moves with the margin.
- Every other open question is a "confirm" question. Keep it as it is records your confirmation; Ask
  for a change records your words and changes nothing in the engine.
- The golden cases do not run in the preview. They test the code at its defaults. A Rulebook answer is
  itself the "ask Jim" that CLAUDE.md requires before a golden value changes, so a lot a golden case
  describes can change, and the preview lists it like any other.
- Apply needs a finished preview of the same answer, made after the newest version. Otherwise the page
  refuses with words.
- A preview shows only what your answer changes, counted as Apply counts, and reports the lots already
  out of date separately. If this is wrong, the preview shows less than Apply will do.
- A preview writes no file and never calls a model. If this is wrong, the only loss is that a preview
  under the opt-in `openai` mode reads soft lots as manual.
- An approved lot keeps the amount you approved after an Apply; Reopen it to have the new rule price
  it again.
- A lot parked because its images are not published yet keeps its amount from the search card, so
  Apply prices it again from that card under the new answer, and the preview lists it. It stays
  parked, with its auction day and hall. If the margin takes it over the cap, it is skipped at the
  card, as a scan would skip it. Nothing is downloaded for it. If this is wrong, a parked lot you
  expected to keep its old amount changes, and the preview shows it before you confirm.
- Answers live in `data/bot.sqlite` (private, never deleted), not in a file in git. The README's list
  points to the Rulebook.
