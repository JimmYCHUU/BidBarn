# What watch mode has shown so far

Notes from recorded manual sessions (`bot watch`). Facts about how the reviewer works, kept
here so the bot's flow and review page can follow them. No amounts or lot details: those stay
in `data/sessions/`. (The few amounts quoted below are invented stand-ins for the real ones.)

## Session 1, 2026-09-15 (8 minutes, one model code, no bid placed)

**The manual flow runs from the result cards; a lot's page is opened in a new tab only when
the card leaves a doubt or the guide asks for more.** (Session 1's recorder lost tabs opened
from links, so those visits are missing from its log; fixed the same day.)

1. Auction Search, then the Carbarn dashboard for about three minutes (reading the guide for
   the model code about to be searched), then back to Nichibo.
2. The search form is filled by hand: chassis code, year range from the guide, odometer cap
   (once), model picked from the dropdown. Stock is left included in the search.
3. The result cards are read in place: year, variant text, km, grade, start price. The first
   two cards get most of the attention; cards outside the grade or odometer limits are passed
   over without any click. Lots that raise a question are opened in a new tab (the reviewer
   said so; the recorder missed those tabs in this session).
4. Candidates are added to the **cart** with the card's `+ cart` button. Two of seven cards
   went in; both were grade 4, the rest were grade 3.5, grade 0, or far above the odometer cap.
5. In the **Auction cart** the amount is typed into each card's `CarCostJPY` field (in
   thousands). Nichibo offers `Place Bid` per car and a bulk `Place All Bids`; the session
   ended before either was pressed.
6. The chassis code was then changed to the next model code and the search repeated.

**What this means for BidBarn**

- For clear-cut cards the human decision is made from the same facts the bot's card stage
  already has (year, km, grade, variant, start); the page, sheet and photos are consulted only
  for the doubtful ones. The card-and-sheet scan with the page opened only when a rule needs
  it matches that shape.
- The triage view should mirror the cards: the facts read at a glance, grade and km first.
- The bot must never use the cart or `Place All Bids` (CLAUDE.md, rule 0b); it bids on the
  lot's own page, one at a time. Amounts should still be the ones the reviewer would type.
- Watch mode should treat `+ cart` clicks as "selected", read the amount typed into a cart
  card, and count time on the Carbarn dashboard as guide reading. Detail-page visits are not
  the unit of work here; cards are.

## What makes a card "doubtful" (Jim, 2026-09-15)

Not intuition: the guide's own requirements that a card cannot answer. A lot's page is
opened when the guide carries a rule or a chip that needs the photos or the sheet:

| Guide requirement | Where the answer is | Example guides |
|---|---|---|
| Seat colour ("Dark Color Seats Only") | Interior photos | Serena GC27 / GFC27, Serena Campervan |
| No Pro Pilot (chip, -220) | Steering wheel photo: the Pro Pilot button | Serena GC27 |
| Autech (chip, +170) | Exterior/interior photos, badges | Serena |
| "S-Hybrid 2 littre 8 seater" | Auction sheet: model code, engine, 乗車定員 (plus the seat photos) | Serena Campervan GC27 |

"S-Hybrid 2 littre 8 seater" means a 2-litre engine and 8 seats; the bot used to read the
"2" as a seat count and has been corrected. There are many more such requirements across
the 46 guides; the pattern is the same: text rules and chips that name something visual or
something printed on the sheet.

**Follow-up worth building.** Classify every guide's text rules and chips into
card-decidable, needs-photos, needs-sheet. On the triage view show per lot "open the page
for: dark seats (photos), Pro Pilot button (photos), S-Hybrid 2L 8 seater (sheet)", and list
card-only lots first so they can be approved without opening anything.

## Still to learn

- Which cards raise a doubt and get their page opened, and what is looked at there (sheet,
  photos, equipment), on models such as Hiace, Alphard and Serena.
- Reasons typed into the box: none yet (the box was off screen in session 1; moved to the
  top right since).
- Whether typed amounts equal the grid values on models with modifiers and chips. In session 1
  both amounts typed into the cart were exactly the guide's grid cell for that year and
  odometer band (default variant, no modifier): the bot's `evaluate_hard` on the same card
  facts gives the same two numbers.
