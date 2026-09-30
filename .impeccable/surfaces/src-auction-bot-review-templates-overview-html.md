---
version: 2
slug: "src-auction-bot-review-templates-overview-html"
primary_target: "src/auction_bot/review/templates/overview.html"
related_targets: ["src/auction_bot/review/templates/run.html","src/auction_bot/review/templates/base.html","src/auction_bot/review/templates/triage.html"]
---

# Overview and run console

Scope: the dashboard's home page (`/`) and the run console (`/runs/{id}`), plus the shell they
share with the review pages. Visitor mode: Operate.

Revision 2026-09-30: Jim's verdict on the first build was "feels complex" (the overview, starting
a command, the review page). The Today-log form stays; within it the page now offers two things,
Learn mode and Bid mode, and the single steps move to a fold on the Runs page. The state cards
became one card of chips. Recorded here so the next reader knows the simplification was asked
for, not drift.

Audience and job: Jim, alone, on the office PC, a session or two a day. He opens the page to
start Bid mode (or a Learn session), watch it, review when the page asks, and finish.

Task and proof: the primary task is starting Bid mode; the proof is the day's own record (runs,
review sessions, placements) with the numbers each run ended on, and for a Learn run the
session compared with the bot lot by lot.

Constraints: the page starts Learn, Bid and the single steps, as dry runs where bids are
concerned. The lock and the dry-run flag are shown, never editable. Server on 127.0.0.1 only. No
Node toolchain; Jinja templates, one stylesheet, vanilla JS, GSAP for number and progress motion.

Memorable moment: a scan arriving as the first entry of the log, its guide row filling cell by
cell; a Bid run's five stages lighting one after another until the page asks for the review.

Resolved: the per-guide result is kept (`marks`), so a finished scan shows its guide row exactly.

## Direction contract

THESIS: Home is today's day book. The two things Jim can do sit on top; below them, everything
that happened today in the order it happened. It refuses the admin default of four equal KPI
cards over a chart, and the launcher default of a form per command.

OWN-WORLD: the Carbarn dashboard's language, unchanged. Manrope. Brand blue #0473EA for the one
primary action and the live entry. White cards, 12px radius, 1px #EAECF0 line, on the #F1F4F8
ground. Pill chips with a tinted ground and a 1px border. Uppercase 11px letterspaced table
heads. Lucide icons at 1.75 stroke. The Carbarn wordmark in a 72px white header. Light only.

STORY: Jim reads the date and the one sentence of state, starts Bid mode, watches it arrive as
the top entry of the log with its stages lighting, opens the review when the band asks, finishes
from the header, and sees the placement land in the log. At the end of a session the page is
the record of the day.

FIRST VIEWPORT: header with the wordmark, BidBarn and the lock pill. Nav card at the left. Main
column, full width: the band, with the date at 20px, one sentence of state, and two cells side
by side, Learn mode and Bid mode, each a title, one sentence and one button (Bid in blue). While
a run is live the band shows the run's name, "Watch the run", and for Bid its five stages.
Below, two columns: at the left (about two thirds) the log, newest first, a running run always
first and open; at the right one card of state chips (sign-in, guides, to review, approved,
placed, a model quota only when it is short). No commands, no meters.

FORM: Today log. Dealt by the roll from the ranked list of seven structures (position 6),
chosen by Jim on the decision page. Seed key 817fd76b. Signature interaction: the guide row,
one cell per guide in a single unbroken row; the lit cell is where the scan is now.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
