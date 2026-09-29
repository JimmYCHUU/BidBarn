---
version: 1
slug: "src-auction-bot-review-templates-overview-html"
primary_target: "src/auction_bot/review/templates/overview.html"
related_targets: ["src/auction_bot/review/templates/run.html","src/auction_bot/review/templates/base.html"]
---

# Overview and run console

Scope: the dashboard's home page (`/`) and the run console (`/runs/{id}`), plus the shell they
share with the review pages. Visitor mode: Operate.

Audience and job: Jim, alone, on the office PC, a session or two a day. He opens the page to
find out what is next, start it, watch it, then review and place.

Task and proof: the primary task is starting the next step of the day; the proof is the day's
own record (runs, decisions, placements) with the numbers each run ended on.

Constraints: the page starts only the daily command set, as dry runs where bids are concerned.
The lock and the dry-run flag are shown, never editable. Server on 127.0.0.1 only. No Node
toolchain; Jinja templates, one stylesheet, vanilla JS, GSAP for number and progress motion.

Memorable moment: a scan arriving as the first entry of the log, its guide row filling cell by
cell.

Unresolved: whether a per-guide result (lots found, none, error) should be kept for finished
runs, so the guide row of an old scan can be shown exactly.

## Direction contract

THESIS: Home is today's day book. The next step sits on top; below it, everything that
happened today in the order it happened. It refuses the admin default of four equal KPI cards
over a chart.

OWN-WORLD: the Carbarn dashboard's language, unchanged. Manrope. Brand blue #0473EA for the one
primary action and the live entry. White cards, 12px radius, 1px #EAECF0 line, on the #F1F4F8
ground. Pill chips with a tinted ground and a 1px border. Uppercase 11px letterspaced table
heads. Lucide icons at 1.75 stroke. The Carbarn wordmark in a 72px white header. Light only.

STORY: Jim reads the one next step and the state that justifies it, starts it, watches it
arrive as the top entry of the log, then reviews and places. At the end of a session the page
is the record of the day.

FIRST VIEWPORT: header with the wordmark, BidBarn and the lock pill. Nav card at the left.
Main column, full width: the next-step band, its name at 20px, one sentence of why, the primary
button at the right end, the five day steps as a row under it. Below, two columns: at the left
(about two thirds) the log, newest first, a running run always first and open; at the right
the counts, the judge quota, the guides and sign-in state, and the other commands.

FORM: Today log. Dealt by the roll from the ranked list of seven structures (position 6),
chosen by Jim on the decision page. Seed key 817fd76b. Signature interaction: the guide row,
one cell per guide in a single unbroken row; the lit cell is where the scan is now.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
