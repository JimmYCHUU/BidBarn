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

## Session 2, 2026-09-15 (49 minutes, seven model codes, teaching run, no bid placed)

Recorded after the reason box was fixed. 26 comments typed, 17 cards put in the cart and
taken out again ("uncarting everything because now we are just testing and teaching you").
The recorder of that day lost lot pages opened in new tabs, so the page visits are missing;
the comments and the cart handling are complete.

**Rules the comments state, in the reviewer's words**

| Comment (paraphrased) | Rule the bot has today | Gap |
|---|---|---|
| "Choose these two because these are the only auction grade 4"; "other one is auction grade 0, so skipped" | min grade from the guide; 0/R/RA skip | none |
| "entered 730 because it was in the 50-60k threshold in 2020 year"; "entered 1290 because 2014 and in 40k odo"; "690 because 2019 variant 80-90k odo range" | grid cell by year and odometer band | none |
| "uncarted this one because it's already 80k odo" (three times); "not gonna take this because already 80k"; "Normally I would skip this vehicle because the odo will increase as we drive this vehicle to our warehouse" | none | **odometer margin**: a lot already at the top of the last band is skipped, because the odometer rises between the auction and the warehouse |
| "odo 50k on the mark, I used 50-60k odo value because the odo is gonna increased till delivery" | bands are upper-bound inclusive: 50,000 falls in 40-50K | **band boundary**: on the mark, the reviewer uses the next band's (lower) value |
| "checked if it's autech or not. I'm not sure ... so i'm gonna bid on the base value" | chips are the reviewer's, never applied by the bot | none: when unsure, the base value |
| "the blue button on the steering wheel, that's pro pilot. If not then as per requirements -220" | No Pro Pilot chip listed on the checklist | none; the amount is the dashboard's chip |
| "8 seater (found each one from auction sheet), 2000 cc that mean 2L engine, and Dark color seats found from the pictures" | seat rule from the sheet; seat colour left to the reviewer | none |
| "did you see that the first two are Stock-Buy Now, Never ever click on them" | stock excluded from the search and the parser; URL guard | none |

The two gaps are one rule with two faces: **the odometer keeps rising after the hammer**, so
the reviewer prices with the next band when a lot is on a band boundary and skips a lot that
is already at the last band's cap. Not implemented: the exact margin is Jim's to state (a
number of km, or "on the mark only"), and it changes bid amounts, so it goes to the README's
open questions first.

## Still to learn

- Which cards raise a doubt and get their page opened, and what is looked at there (sheet,
  photos, equipment), on models such as Hiace, Alphard and Serena.
- More comments on models where the sheet or photos decide; session 2 gave the first 26.
- Whether typed amounts equal the grid values on models with modifiers and chips. In session 1
  both amounts typed into the cart were exactly the guide's grid cell for that year and
  odometer band (default variant, no modifier): the bot's `evaluate_hard` on the same card
  facts gives the same two numbers.
