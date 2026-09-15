# What watch mode has shown so far

Notes from recorded manual sessions (`bot watch`). Facts about how the reviewer works, kept
here so the bot's flow and review page can follow them. No amounts or lot details: those stay
in `data/sessions/`. (The few amounts quoted below are invented stand-ins for the real ones.)

## Session 1, 2026-09-15 (8 minutes, one model code, no bid placed)

**The manual flow does not open a lot's page at all.**

1. Auction Search, then the Carbarn dashboard for about three minutes (reading the guide for
   the model code about to be searched), then back to Nichibo.
2. The search form is filled by hand: chassis code, year range from the guide, odometer cap
   (once), model picked from the dropdown. Stock is left included in the search.
3. The result cards are read in place: year, variant text, km, grade, start price. The first
   two cards get most of the attention; cards outside the grade or odometer limits are passed
   over without any click. No card was opened, quick-viewed, or had its sheet or photos looked
   at in this session.
4. Candidates are added to the **cart** with the card's `+ cart` button. Two of seven cards
   went in; both were grade 4, the rest were grade 3.5, grade 0, or far above the odometer cap.
5. In the **Auction cart** the amount is typed into each card's `CarCostJPY` field (in
   thousands). Nichibo offers `Place Bid` per car and a bulk `Place All Bids`; the session
   ended before either was pressed.
6. The chassis code was then changed to the next model code and the search repeated.

**What this means for BidBarn**

- For this kind of model the human decision is made from the same facts the bot's card stage
  already has (year, km, grade, variant, start). The card-and-sheet scan matches the manual
  flow; the sheet is extra care the human did not take here.
- The triage view should mirror the cards: the facts read at a glance, grade and km first.
- The bot must never use the cart or `Place All Bids` (CLAUDE.md, rule 0b); it bids on the
  lot's own page, one at a time. Amounts should still be the ones the reviewer would type.
- Watch mode should treat `+ cart` clicks as "selected", read the amount typed into a cart
  card, and count time on the Carbarn dashboard as guide reading. Detail-page visits are not
  the unit of work here; cards are.

## Still to learn

- Sessions on models where the sheet or photos matter (Hiace, Alphard, Serena): what is
  looked at, in which order, and what makes a lot fail.
- Reasons typed into the box: none yet (the box was off screen in session 1; moved to the
  top right since).
- Whether typed amounts equal the grid values on models with modifiers and chips. In session 1
  both amounts typed into the cart were exactly the guide's grid cell for that year and
  odometer band (default variant, no modifier): the bot's `evaluate_hard` on the same card
  facts gives the same two numbers.
