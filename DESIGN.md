---
name: BidBarn
description: Carbarn's auction bidding assistant for Nichibo. The dashboard and the review pages, in the style of the Carbarn dashboard, light only.
colors:
  blue: "#0473ea"
  blue-hover: "#0366d0"
  blue-press: "#035bbb"
  blue-soft: "#e7f1ff"
  blue-line: "#b9d6fb"
  link: "#0366d0"
  navy: "#061d33"
  navy-2: "#0c3a66"
  ground: "#f1f4f8"
  surface: "#ffffff"
  surface-2: "#f8fafc"
  surface-3: "#eef2f6"
  ink: "#101828"
  ink-2: "#344054"
  ink-3: "#5f6f82"
  ink-4: "#667085"
  ink-faint: "#98a2b3"
  line: "#eaecf0"
  line-2: "#e5e7eb"
  line-strong: "#d0d8e2"
  line-field: "#8692a3"
  ok-ink: "#067647"
  ok-bg: "#ecfdf3"
  ok-line: "#abefc6"
  ok: "#079455"
  warn-ink: "#b54708"
  warn-bg: "#fef6e7"
  warn-line: "#f4d9b0"
  warn: "#dc6803"
  bad-ink: "#b42318"
  bad-bg: "#fff6f5"
  bad-line: "#fecdca"
  bad: "#d92d20"
  info-ink: "#175cd3"
  info-bg: "#eff8ff"
  info-line: "#b2ddff"
  violet-ink: "#5925dc"
  violet-bg: "#f4f3ff"
  violet-line: "#d9d6fe"
  tan-ink: "#8a4b1c"
  tan-bg: "#faead7"
  tan-line: "#cf9963"
  slate-bg: "#e9eef4"
  slate-line: "#b4c2d2"
typography:
  display:
    fontFamily: "Manrope, 'Segoe UI', system-ui, sans-serif"
    fontSize: "32px"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Manrope, 'Segoe UI', system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Manrope, 'Segoe UI', system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Manrope, 'Segoe UI', system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 500
    lineHeight: 1.5
    letterSpacing: "normal"
  control:
    fontFamily: "Manrope, 'Segoe UI', system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.3
  meta:
    fontFamily: "Manrope, 'Segoe UI', system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 500
    lineHeight: 1.5
  label:
    fontFamily: "Manrope, 'Segoe UI', system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.3
  table-head:
    fontFamily: "Manrope, 'Segoe UI', system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 700
    letterSpacing: "0.05em"
  mono:
    fontFamily: "'Cascadia Mono', Consolas, 'Lucida Console', ui-monospace, monospace"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.6
rounded:
  xs: "6px"
  sm: "8px"
  md: "12px"
  pill: "999px"
spacing:
  header-h: "72px"
  nav-w: "238px"
  page-w: "1680px"
components:
  header:
    backgroundColor: "{colors.surface}"
    height: "{spacing.header-h}"
    padding: "0 24px 0 12px"
  nav:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    width: "{spacing.nav-w}"
    padding: "10px 8px"
  nav-item:
    textColor: "{colors.navy}"
    rounded: "{rounded.sm}"
    padding: "11px 12px"
  nav-item-hover:
    backgroundColor: "{colors.surface-2}"
  nav-item-current:
    backgroundColor: "{colors.blue-soft}"
    textColor: "{colors.blue-press}"
  button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-2}"
    typography: "{typography.control}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "34px"
  button-hover:
    backgroundColor: "{colors.surface-2}"
  button-primary:
    backgroundColor: "{colors.blue}"
    textColor: "#ffffff"
    typography: "{typography.control}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "34px"
  button-primary-hover:
    backgroundColor: "{colors.blue-hover}"
  button-danger:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.bad-ink}"
    typography: "{typography.control}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "34px"
  button-danger-hover:
    backgroundColor: "{colors.bad-bg}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink-3}"
  button-quiet-hover:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.ink-2}"
  button-armed:
    backgroundColor: "{colors.navy}"
    textColor: "#ffffff"
  button-sm:
    rounded: "7px"
    padding: "0 10px"
    height: "28px"
  button-lg:
    padding: "0 18px"
    height: "40px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
  card-head:
    typography: "{typography.title}"
    padding: "12px 16px"
  card-body:
    padding: "14px 16px"
  card-foot:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink-3}"
    padding: "9px 16px"
  tile:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "9px 12px 10px"
  tile-warn:
    backgroundColor: "{colors.warn-bg}"
    textColor: "{colors.warn-ink}"
  tile-bad:
    backgroundColor: "{colors.bad-bg}"
    textColor: "{colors.bad-ink}"
  pill:
    backgroundColor: "{colors.slate-bg}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "1px 10px"
    height: "22px"
  pill-ok:
    backgroundColor: "{colors.ok-bg}"
    textColor: "{colors.ok-ink}"
  pill-warn:
    backgroundColor: "{colors.warn-bg}"
    textColor: "{colors.warn-ink}"
  pill-bad:
    backgroundColor: "{colors.bad-bg}"
    textColor: "{colors.bad-ink}"
  pill-info:
    backgroundColor: "{colors.info-bg}"
    textColor: "{colors.info-ink}"
  pill-violet:
    backgroundColor: "{colors.violet-bg}"
    textColor: "{colors.violet-ink}"
  pill-tan:
    backgroundColor: "{colors.tan-bg}"
    textColor: "{colors.tan-ink}"
  pill-plain:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-3}"
  pill-lock:
    padding: "3px 12px"
    height: "30px"
  count:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    padding: "0 7px"
  count-hot:
    backgroundColor: "{colors.warn-bg}"
    textColor: "{colors.warn-ink}"
  badge:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.xs}"
    padding: "1px 7px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "7px 11px"
    height: "36px"
  banner:
    backgroundColor: "{colors.warn-bg}"
    textColor: "#7a3306"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
  banner-info:
    backgroundColor: "{colors.info-bg}"
    textColor: "#184b91"
  banner-bad:
    backgroundColor: "{colors.bad-bg}"
    textColor: "#8a1c13"
  banner-ok:
    backgroundColor: "{colors.ok-bg}"
    textColor: "#085d3a"
  table-head:
    textColor: "#4b5a6c"
    typography: "{typography.table-head}"
    padding: "10px 12px"
  table-row-selected:
    backgroundColor: "#f5f9ff"
  entry:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "12px 16px 12px 10px"
  entry-icon:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    size: "32px"
  guide-cell:
    backgroundColor: "#a3afbf"
    rounded: "2px"
    height: "4px"
  guide-cell-found:
    backgroundColor: "{colors.blue}"
    height: "28px"
  guide-cell-empty:
    backgroundColor: "#7d93ad"
    height: "46%"
  guide-cell-error:
    backgroundColor: "{colors.bad}"
    height: "28px"
  guide-cell-now:
    backgroundColor: "{colors.blue-soft}"
    height: "28px"
  meter-track:
    backgroundColor: "#d6e6fb"
    rounded: "3px"
    height: "6px"
  meter-fill:
    backgroundColor: "{colors.blue}"
  log:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink-2}"
    typography: "{typography.mono}"
    rounded: "{rounded.sm}"
    padding: "10px 12px"
---

# Design System: BidBarn

## Overview

**Creative North Star: "One Family"**

BidBarn's pages are written in the visual language of the Carbarn dashboard, so that the two read as one family when they sit beside each other on the office PC. Jim pinned this on 2026-09-29. The stylesheet is BidBarn's own: one file, `bidbarn.css`, with every token on `:root`. It is light only and declares `color-scheme: light`.

It is a working tool for one person who opens it a session or two a day. The pages are dense enough to hold a day of runs or a queue of lots, and quiet enough that the one thing to do next is easy to find. Text is Manrope at 14px. Surfaces are white cards with a 1px line on a grey-blue ground. The brand blue marks the action that moves the day forward and whatever is live or selected. State is shown as a pill that carries a word.

Motion follows change and nothing else. A page opens still. After that, a number counts to its new value, a meter grows to its share, and the guide row sweeps once when a scan ends. CSS transitions are 120 to 300ms. A reader who asks for reduced motion gets the final values with no movement.

**Key Characteristics:**
- Manrope, one self-hosted variable font (weights 200 to 800), body at 14px and weight 500.
- The brand blue for the primary action, the live entry, the selected row and the focus ring.
- White cards with a 12px radius and a 1px line, no shadow, on the grey-blue ground.
- Pill chips with a tinted ground, a 1px border and ink of the same hue, always with a word.
- Uppercase 11px letterspaced labels for table column heads, fact terms and counter labels.
- Lucide icons from one sprite, stroke 1.75, 16px by default.
- A 72px white sticky header with the Carbarn wordmark, a hairline, the name BidBarn, and the lock pill at the right end.
- Tabular numerals wherever numbers are compared or change.
- Every font, script and icon is served from the project's own `static/vendor/` folder.

## Colors

One brand blue, a navy, cool grey neutrals, and status families that each come as an ink, a ground and a line.

### Primary
- **Carbarn Blue** (`blue`, #0473ea): the fill of the primary button and of Approve, the border of the live log entry, the outline of the selected row, the fill of a meter and of a guide cell where lots were found, the focus ring, the caret, and the wordmark. White text on it measures 4.52:1.
- **Blue Hover** (`blue-hover`, #0366d0): the primary button under the pointer.
- **Link Blue** (`link`, #0366d0): text links. Underlined on hover with a 3px offset. 5.5:1 on white, 4.99:1 on the ground.
- **Blue Press** (`blue-press`, #035bbb): text and icon of the current nav item.
- **Blue Soft** (`blue-soft`, #e7f1ff): ground of the current nav item, of the icon tile of a running entry, and of the guide cell being scanned.
- **Blue Line** (`blue-line`, #b9d6fb): border of a linked tile under the pointer.

### Secondary
- **Carbarn Navy** (`navy`, #061d33): nav text, the name BidBarn in the header, and the fill of a button armed for confirmation. The lightbox backdrop is this navy at 86% opacity.
- **Navy 2** (`navy-2`, #0c3a66): nav icons.

### Tertiary
Each status family has an ink for text, a ground and a line for the pill or notice, and in three cases a solid for marks.
- **Ok** (`ok-ink`, `ok-bg`, `ok-line`, solid `ok`): status BID, decision approved, a run that finished, signed in, guides fresh.
- **Warn** (`warn-ink`, `warn-bg`, `warn-line`, solid `warn`): status NEEDS_REVIEW, a stopped run, the testing lock, a count that waits for the reviewer, the default notice.
- **Bad** (`bad-ink`, `bad-bg`, `bad-line`, solid `bad`): status SKIP, a failed run, signed out, a form error, a guide cell with an error. The lock pill takes this family only when placement is live.
- **Info** (`info-ink`, `info-bg`, `info-line`): decision pending, a running run, dry run.
- **Violet** (`violet-ink`, `violet-bg`, `violet-line`): a lot proposed under two guides.
- **Tan** (`tan-ink`, `tan-bg`, `tan-line`): the auction grade of a lot.
- **Slate** (`slate-bg`, `slate-line`): the default pill, with `ink` text.

### Neutral
- **Ground** (`ground`, #f1f4f8): the page behind the cards.
- **Surface** (`surface`, #ffffff): cards, the header, the nav, inputs, buttons.
- **Surface 2** (`surface-2`, #f8fafc): card foot, the log, preformatted blocks, hover of a default button or nav item.
- **Surface 3** (`surface-3`, #eef2f6): count chips, the icon tile of a log entry, hover of a quiet button.
- **Ink** (`ink`, #101828): headings, values, input text. 17.75:1 on white.
- **Ink 2** (`ink-2`, #344054): body text. 10.46:1 on white.
- **Ink 3** (`ink-3`, #5f6f82): secondary text, help text, times. 5.14:1 on white, 4.66:1 on the ground.
- **Ink 4** (`ink-4`, #667085): muted text, placeholders, the time stamp of a log line. 4.97:1 on white, 4.51:1 on the ground.
- **Faint Ink** (`ink-faint`, #98a2b3): the icon of an empty state and the icon of an idle day step. It measures 2.58:1 on white, so it is never used for text and never for a mark that has to be read by itself.
- **Line** (`line`, #eaecf0): card borders, row dividers, hairlines between facts.
- **Line 2** (`line-2`, #e5e7eb): borders of tiles and of the segmented control.
- **Line Strong** (`line-strong`, #d0d8e2): borders of buttons, plain pills, badges, thumbnails, and the hairline beside the wordmark.
- **Field Line** (`line-field`, #8692a3): input borders. 3.16:1 on white.

A small number of values are written straight into rules and are not tokens: the title ink of the page title (#0f172a), the ink of uppercase labels (#4b5a6c), the tint of the selected row (#f5f9ff), the meter tracks (#d6e6fb, #fbe3bf, #fbd0cc), the deeper inks of notice text, the selection colour (#cfe3fc), the scrollbar thumb (#c3ccd8), and the two greys of the guide row. They belong to the component that uses them. They are not steps of the palette.

### Named Rules
**The Word With The Colour Rule.** A status colour never stands alone. Every pill carries its word. A pill that reports a run or a check also carries its Lucide icon. In the guide row the height of a cell changes with its state as well as its colour, and the legend names each state.

**The Empty Track Exception.** The guide cells that are not scanned yet are #a3afbf on white, about 2.2:1, below the 3:1 floor for graphics. This is deliberate and it is the only such case. Those cells are the empty track of the row, 4px high. Their meaning is carried elsewhere: the legend names them ("to come", or "not scanned" after the run), the line above the row says "Guide 8 of 46", and the row's accessible label counts them. The value is not a text colour and is not available to any other component.

## Typography

**Display Font:** Manrope (with Segoe UI, system-ui, sans-serif)
**Body Font:** Manrope (with Segoe UI, system-ui, sans-serif)
**Label/Mono Font:** Cascadia Mono (with Consolas, Lucida Console, ui-monospace, monospace)

**Character:** One family does all the work, from the 32px bid amount to the 11px column head. Hierarchy comes from weight (500, 600, 700) and size, not from a second typeface. The mono stack is for what is code or measurement: the log, model codes, chassis codes, file paths and key caps.

### Hierarchy
- **Display** (700, 32px, line-height 1.15, letter-spacing -0.02em): the bid amount on the lot's detail page. Its unit follows at 14px and weight 500, and the grid cell it came from is on the line below.
- **Headline** (700, 20px, letter-spacing -0.01em): the page title, and the name of the next step on the overview, where the line-height is 1.3 and the letter-spacing -0.015em. One per page.
- **Title** (700, 14px, letter-spacing -0.01em): card heads, the title of a lot, the title of a log entry. The two section titles of the overview ("Today", "Earlier") and the heading of an empty state are 15px.
- **Body** (500, 14px, line-height 1.5): running text. The sentence under the next step is held to 72ch.
- **Control** (600, 13px, line-height 1.3): button text. Notices, page subtitles and the "why" column are 13px at weight 500.
- **Meta** (500, 12.5px): the facts line of a log entry or a lot, and the options of a run. Entry times and field labels are the same size at weight 600. Help text and faint notes are 12px.
- **Label** (600, 12px, line-height 1.3): pills.
- **Table head** (700, 11px, letter-spacing 0.05em, uppercase): column heads, the term of a fact pair, the label of a counter.
- **Mono** (400, 12.5px, line-height 1.6): the log of a run. Preformatted blocks are 12px at weight 500. Inline codes take the mono stack at 0.93em.

Four more sizes are set in single components: 11.5px (count chips, badges, key caps, the note of a tile), 13.5px (check lines, the position line of the console, a command's summary), 15px (nav items) and 18px (counter values, and the page title on a phone). They are recorded with those components and are not steps of the ramp.

### Named Rules
**The Data Label Rule.** The uppercase 11px label names a piece of data: a column, a fact, a counter. It sits directly over its value or its column. It is never placed above a heading or a section title.

**The Tabular Figures Rule.** Counts, amounts, times and durations use tabular numerals. Numeric table columns are right aligned.

## Layout

The page is a sticky header over a two-column shell. The shell is centred, at most 1680px wide, with 22px at the sides and an 18px gap. The nav column is 238px and stays in view under the header. The main column takes the rest.

Inside the main column a page starts with its head: the title, a subtitle in secondary ink, and the page's controls at the right end. Cards follow, 14px apart. A section title has 22px above it and 8px below. Cards hold 16px at the sides, 12px above and below in the head, 14px in the body. Rows of controls use a 10px gap. Most spacing falls on 2, 4, 6, 8, 10, 12, 14, 16 and 18px. These values are written in the rules. They are not tokens.

Page compositions in the build:
- **Overview:** the band across the full width (the date and the two mode cells, or the live run and its stages), then two columns that start alike, each with a 15px section title ("Today", "State") 14px under the band and its first card 8px under that. The log takes 1.9 parts and the State column 1 part with a 300px minimum, 18px apart. Nothing else sits beside the log: no commands, no meters.
- **Guide:** everything across the full width except one pair: the lot counts as a strip of pills under the head, the requirements, the bid grid of each variant (as many as sixteen odometer bands), then the standing instructions beside the playbook in two even columns, then the recent decisions as a wide table. Nothing short stands beside anything tall.
- **Lot detail:** two columns, 1 to 1.25, 16px apart. The evidence column (sheet, photos) stays in view while the left column is read down to the decision.
- **Settings:** two even columns.
- **Review, all lots, runs:** full-width cards that hold tables. A wide table keeps a 960px minimum and scrolls sideways inside its card. The review table pins nothing; below 1130px of its own box it lays each lot out as a block instead.

Responsive behaviour:
- **Up to 1279px** (beside Nichibo on half a screen): the nav becomes a wrapping strip above the page, its foot is hidden, and the shell's side padding is 14px.
- **Up to 1279px also:** the overview becomes one column, the State card above the log, its chips as a wrapping row.
- **Up to 860px:** the two mode cells stack, with a hairline between them.
- **The review table answers to its own box, not to the window** (a container query on `.triage-wrap`), because the nav column and a half screen both take room from it. With 1130px or more it is a table. Below that a lot is a block: status, lot, bid, sheet and the two buttons on one line, the reasons on a line of their own under the lot. Below 830px the lot reads top to bottom: status and bid, the lot beside its sheet, the reasons, the buttons last. Nothing is pinned and nothing scrolls sideways.
- **Up to 860px:** the two-column pages stack. From 861px the evidence column is sticky.
- **Up to 640px:** the header is 60px, the wordmark is 15px high, the lock pill shows its short word, the finish button leaves the header, the step connectors are hidden, and table cells take 8px by 9px of padding.

## Elevation & Depth

The system is flat. A card is declared by its 1px line and carries no shadow. Depth comes from the ground behind the cards and from lines, including the hairlines between facts and counters, which are drawn as 1px spreads in the line colour. The nav is a white panel without a line.

### Shadow Vocabulary
- **Header** (`box-shadow: 0 8px 28px rgba(16, 24, 40, 0.05)`): under the sticky header, so that the page passes beneath it.
- **Segment** (`box-shadow: 0 1px 2px rgba(16, 24, 40, 0.06)`): the chosen item of a segmented control.
- **Field focus** (`box-shadow: 0 0 0 3px rgba(4, 115, 234, 0.18)`): a ring around a focused input, with a blue border. It is a focus mark, not depth.
- **Popover** (`shadow-pop`, `0 14px 32px -10px rgba(16, 24, 40, 0.2), 0 2px 6px rgba(16, 24, 40, 0.06)`): the keys popover on the review page, the one surface that floats above the page.

Layers, lowest first: the header at 30, the popover at 40, the lightbox at 50.

### Named Rules
**The Line Not Shadow Rule.** A surface is declared once, by its line. Cards, tiles, log entries, tables and notices have a 1px border and no shadow. A shadow belongs to the header, to the chosen segment and to the one popover, nowhere else.

## Shapes

Corners are gently rounded and consistent by size of object. Cards, log entries and the nav have a 12px radius. Controls and small containers have 8px: buttons, inputs, tiles, notices, the fact grid, the log. Small marks have 6px: badges, thumbnails, the items of a segmented control. Pills and count chips are fully round (999px). Small buttons are 7px, key caps 5px, meter tracks 3px and guide cells 2px.

Borders are 1px throughout. The only thicker strokes are the 2px focus ring, the 2px inset outline of the guide cell being scanned, and the 2px bottom edge of a key cap. The selected row of a table has a pale blue tint (#f5f9ff) and one 1px blue line around it, drawn once on the row as an inset shadow. When the decide row is open under it, the two share one box.

Icons are Lucide 1.48.0, drawn from one sprite with a stroke of 1.75, round caps and joins, no fill, in the colour of the text around them. They are 16px by default, 14px in small controls and 20px in the nav. The favicon is a blue square with a 7px radius (on a 32px canvas) and the white C of the wordmark.

## Components

### Buttons
Plain and quick. They change colour under the pointer and give 2% when pressed.
- **Shape:** 8px radius, 34px high, 14px at the sides, a 7px gap between icon and label. Small is 28px high with 10px at the sides and 12.5px text. Large is 40px high with 18px at the sides and 14px text. Icon-only is square.
- **Default:** white, `line-strong` border, `ink-2` text.
- **Primary:** `blue` fill and border, white text. Start a command, Approve, open the review.
- **Danger:** white, `bad-line` border, `bad-ink` text. Reject, Stop.
- **Quiet:** no border or ground, `ink-3` text. Cancel.
- **Hover / Focus:** hover styles apply only to a fine pointer that can hover. Default goes to `surface-2`, primary to `blue-hover`, danger to `bad-bg`, quiet to `surface-3`. Colour changes take 150ms. Pressing scales the button to 0.98 over 120ms with the ease-out curve. Focus is the 2px blue ring, 2px away.
- **Disabled:** 50% opacity, no press movement, no hover change.
- **Armed:** `navy` fill, white text, the label replaced by the confirm words. See the Confirm In Place Rule.

**The Confirm In Place Rule.** An action that deserves a second thought is confirmed on the button itself. The first click arms it: the label becomes the confirm words ("Confirm stop", "Confirm approve") and the button turns navy. The second click acts. An armed button disarms after 5 seconds. The button is given the width of its longer label beforehand, so arming moves nothing. No dialog is opened.

### Chips
- **Pill:** fully round, at least 22px high, 10px at the sides, 12px text at weight 600, a 1px border, an optional 13px icon. Slate by default. The variants are ok, warn, bad, info, violet, tan and plain (white, `line-strong` border, `ink-3` text, used for rejected, superseded and unknown).
- **Lock pill:** the pill in the header, 30px high with 12px at the sides and 12.5px text. Warn while the testing lock is on, info for a dry run, bad for live placement.
- **Count:** a round chip for a number, `surface-3` ground, 11.5px at weight 700, tabular. When something waits for the reviewer it takes the warn ground and ink with a 1px inset line.
- **Badge:** a requirement's verdict. 6px radius, `line-strong` border, 11.5px at weight 600, a 12px icon. Ok, bad and unclear variants take the ok, bad and warn families. On the lot page each requirement has its own badge with its evidence in the tooltip.
- **Verdict counts:** on the review page the badges are counts, one per family (a check and 3, a cross and 1, an alert and 1), only the non-zero ones, in a row under the one line of why; the whole list of requirements is the tooltip and a screen reader hears "3 pass, 1 fail, 1 unclear". The list itself lives on the lot page.

### Cards / Containers
- **Corner Style:** 12px.
- **Background:** `surface`.
- **Shadow Strategy:** none. See the Line Not Shadow Rule.
- **Border:** 1px `line`.
- **Internal Padding:** head 12px by 16px with a line below it, body 14px by 16px with 10px between its children, foot 9px by 16px on `surface-2` with a line above it.
- **Head:** a 14px title at weight 700, then a pill or a faint note, pushed to the right end when it reports the card's state.
- **Tile:** a label over a value. White, `line-2` border, 8px radius. The label is 12px in `ink-3`, the value 20px at weight 700 in `ink`, tabular. Warn and bad variants tint the ground and border and colour both lines. The ok variant colours the value only. A linked tile takes the `blue-line` border under the pointer.
- **Fact grid:** label and value pairs that wrap, each at least 150px wide, separated by hairlines inside one 8px-radius frame. The term is the uppercase 11px label, the value 14px at weight 600 in `ink`, a note under it 12px in `ink-3`.
- **Notice:** an icon, then the text, on a tinted ground with a 1px border and an 8px radius, 13px text. Warn by default, with info, bad and ok variants. The text takes a deeper ink of the same hue.
- **Empty state:** centred in its card. A 28px icon in `ink-faint`, a 15px heading, one sentence in `ink-3`, and a button when there is a next thing to do.

### Inputs / Fields
- **Style:** white, 1px `line-field` border, 8px radius, at least 36px high, 14px text in `ink`. The label above is 12.5px at weight 600. Help below is 12px in `ink-3`. Placeholders are `ink-4`.
- **Hover:** the border darkens to #667085.
- **Focus:** the border turns `blue` and a 3px ring of blue at 18% surrounds the field. Transitions take 150ms.
- **Error:** a line of 12px text at weight 600 in `bad-ink`, in a region announced as an alert.
- **Checkbox:** 16px, accented in `blue`, with its label beside it.
- **Segmented control:** a row of choices in a frame with a 3px inset. The chosen item is white, in `ink` at weight 600, with the segment shadow.
- **Key cap:** mono 11.5px at weight 600 in a white cap with a `line-strong` border and a 2px bottom edge.

### Navigation
- **Header:** sticky, white, 72px. The Carbarn wordmark is 18px high and blue. A `line-strong` hairline separates it from the name BidBarn, 16px at weight 700 in `navy`. The lock pill is at the right end.
- **Nav:** a white panel with a 12px radius, 238px wide, as tall as the window below the header. Items are 15px at weight 500 in `navy` with a 20px icon in `navy-2`, 8px radius. Under the pointer an item takes `surface-2`. The current item takes `blue-soft` with `blue-press` text and icon at weight 600. A count sits at the right end of its item. The foot states the lock in words.
- **Narrow:** up to 1279px the nav is a strip of 14px items that wraps.

### Tables
- **Head:** the uppercase 11px label in #4b5a6c with a line below, 10px by 12px of padding, never wrapped.
- **Rows:** top aligned, 1px `line` between them, none under the last. The first and last cells of a lot table take 16px at the outer side to line up with the card head.
- **Selected row:** the tint #f5f9ff and a 1px blue outline. The decision row that opens under it shares the outline.
- **Decision row:** the form to approve or reject opens under the selected row, in place: a field, the primary or danger button, a quiet Cancel, and the keys that do the same.

**The Keyboard Is Instant Rule.** When the decision row is opened with the pointer it fades in and grows from 0.97 over 150ms, from its top right corner. When it is opened with a key it appears at once.

### Meter
A track 6px high with a 3px radius and a fill that scales from the left. Blue on #d6e6fb by default. The warn variant is `warn-ink` on #fbe3bf, the bad variant `bad` on #fbd0cc. The label is at the left above the track and the value at the right, bold and tabular. The fill moves over 300ms with the ease-out curve.

### Day log entry
One thing that happened, as a card in a list: the time, an icon tile, and the body.
- **Shape:** white, 1px `line`, 12px radius, 8px between entries. The time column is 44px, or 92px when the entry carries a date.
- **Time:** 12.5px at weight 600 in `ink-3`, tabular, right aligned.
- **Icon tile:** 32px, 8px radius, `surface-3`. It takes the ok, bad or warn ground and ink for a finished, failed or stopped run, and `blue-soft` with a blue icon for a running one.
- **Body:** the title, the options in meta text, the status pill, and the duration at the right end. Under it, the numbers the run ended on as a line of meta text with the numbers in bold `ink`, or the line that stopped it in `bad-ink` at weight 600.
- **Live:** the entry of a running run has a `blue` border and holds the compact console.

### Run console
The signature component. It shows one run while it happens and keeps the guide row and the log afterwards.
- **Bar:** the status pill, the position in words ("Guide 8 of 46, ZWR80") at 13.5px and weight 600, the time it has been running, and a small danger button to stop, confirmed in place.
- **Guide row:** one cell per guide in a single unbroken row 28px high, cells 2px apart, each between 3px and 30px wide. A cell to come is 4px high. Where lots for review were found the cell is full height and `blue`. Where there was nothing new it is 46% high and #7d93ad. An error is full height and `bad`. The guide being scanned is full height in `blue-soft` with a 2px blue outline, and its opacity pulses to 0.35 and back every 1.2 seconds. Each cell names its guide and its state in a tooltip.
- **Legend:** under the row, each state with a small cell and its name. Found, nothing new and error carry their counts.
- **Counters:** a wrapping grid like the fact grid. The uppercase 11px label over an 18px value at weight 700.
- **Log:** mono 12.5px on `surface-2` with a 1px line and an 8px radius. 420px high while the run is live (172px in the compact console), at most 520px afterwards. The time stamp of a line is `ink-4`. A heading line is bold `ink`, a warning line `warn-ink`, an error line `bad-ink` at weight 600. "Follow the log" keeps the last line in view and turns itself off when the reader scrolls up.

**The Still On Load Rule.** Nothing animates when a page opens. What the page opens with is drawn as it is. Only a change that arrives afterwards moves: a counter counts to its new value in 0.4 seconds, a meter grows to its share in 0.3 seconds, a new log line fades in over 150ms unless more than five arrive together. When a scan ends well the cells of the guide row sweep once from 30% to full opacity, 12ms apart. The spinning icon and the pulsing cell mark a run that is running and stop with it.

### Mode cell
One of the two things the overview offers when nothing runs: Learn mode and Bid mode, side by side under the date with a hairline between them. Each is a 15px title with a blue 18px icon, one sentence at 13px in `ink-3` held to 52ch, and one button. Bid's button is primary, Learn's is default. Both cells leave the band while a run is going; the band then shows the run instead. An error from a refused start appears under the cell's own button.

### State chip list
The state of the day as a list of pills in one card, one per thing: sign-in, guides, lots to review, approved bids not placed, placed, and a model quota only when it is short. A pill carries its word and its family (ok, warn, bad, info, plain). A faint note beside it gives the age or the count. A small default button appears only when something is wrong and the page can repair it: Sign in, Fetch the guides. Pills that lead somewhere are links and take the `blue-line` border under the pointer. Under 1280px the list becomes a wrapping row.

### Stages
The five stages of a Bid run (Sign in, Guides, Scan, Review, Place) in one row, joined by 20px hairlines, under the run's name on the overview and above the console on the run page. Each is a 15px icon and a 12.5px label in `ink-3`. A done stage has the ok check and `ink-2` text. The running stage has the spinning blue icon and `ink` at weight 700. A failed stage has the `bad` cross. A stage to come has the `ink-faint` circle. Each also states its condition in text for a screen reader. The row is shown for Bid runs only; nothing runs, nothing is shown.

### Keys popover
The keyboard list of the review page, opened by the keyboard icon button in the page head or by the ? key. A white card with the 8px radius, a 1px line and the popover shadow, anchored under the head's right end at layer 40, holding the key caps and their words in a wrapping row. Esc or a click outside closes it. It does not animate: a key opens it.

### Session table
A Learn run's session on its run page: one row per lot Jim touched, in five columns. Lot (the title linking to the lot when the bot has it, the reference in mono under it); You (pills for what he did: "bid 1,400K" in ok, "carted" in info, "opened" in plain, or "passed"; his typed reasons as faint lines); The bot (its status pill and amount, his difference against the grid, its reason); The model (its recommendation pill, its amount, then "agree" in ok or "disagree" in warn); Guide (the code in mono, linking to the guide page). Notes follow as a check list, then the candidate rules in the suggest strip, each with an Add button that stores it only when clicked.

### Guide grid
A bidding guide's grid as a table: years down, odometer bands across, amounts in K JPY as tabular figures, "no bid" in `ink-4` where the guide bids nothing, its reason in the tooltip. Cells take 8px of padding so that sixteen bands fit the main column at 1440px; narrower, the table scrolls inside its card. Under it the card foot names the modifiers the bot applies and the rows that are off.

### Lightbox
The auction sheet at full size. It covers the window in `navy` at 86%, above every other layer, with the image contained in it. A click closes it. On the review pages any key closes it as well.

### Browser surfaces
Selection is #cfe3fc with `ink` text. The caret is `blue`. Scrollbar thumbs are #c3ccd8 on a transparent track. Focus is a 2px `blue` outline, 2px away, on everything except inputs, which use the field ring.

## Do's and Don'ts

### Do:
- **Do** build every page inside the shared shell: the 72px header with the wordmark and the lock pill, the nav, then the page head and its cards.
- **Do** give every state a pill with its word, in the family this document assigns to it.
- **Do** use tabular numerals for counts, amounts, times and durations, and right align numeric columns.
- **Do** show an amount with where it came from: the grid cell under the bid, "grid" or "base" under a changed amount.
- **Do** confirm on the button itself, following the Confirm In Place Rule.
- **Do** keep hover styles behind a fine pointer that can hover, and keep the 2px blue focus ring on every control.
- **Do** use the ease-out curve (`cubic-bezier(0.23, 1, 0.32, 1)`) for movement, 150ms for colour, 120ms for a press, and 300ms at most for a CSS transition.
- **Do** honour reduced motion: write the final value and move nothing.
- **Do** take icons from the Lucide sprite, at stroke 1.75.
- **Do** use the tokens on `:root`. Where the stylesheet writes a value straight into a rule, treat it as that component's own value.

### Don't:
- **Don't** add a dark theme or a `prefers-color-scheme` branch. The pages are light only, like the Carbarn dashboard.
- **Don't** place the uppercase 11px label above a heading or a section title. It names data.
- **Don't** use the grey of the cells to come (#a3afbf) anywhere else, and don't use `ink-faint` for text.
- **Don't** put a shadow on a card, a tile, a log entry or a notice.
- **Don't** animate anything when a page opens, and don't animate what a key opens.
- **Don't** open a dialog to confirm an action.
- **Don't** show a state by colour alone.
- **Don't** use a Unicode mark or an emoji where an icon belongs.
- **Don't** load a font, a script or an icon from the network. Everything is served from `static/vendor/`.
- **Don't** introduce a second typeface for headings. Manrope carries every level.
- **Don't** state the lock outside the header pill, the nav foot, Settings, the finish page and the approve row's hint.
- **Don't** put a command's options in front of Learn or Bid. The single steps and their options live in the fold on the Runs page.
- **Don't** offer a suggested skip rule anywhere but the guide page and a Learn run's page; elsewhere, link there.
