# Endpoints learned from the HAR captures (2026-09-14)

## Carbarn dashboard (rules source)

The bot signs in by hand like a person (`login.cmd`) and calls the API host (`carbarn.api_base` in
`config.yaml`) from that signed-in browser context (Playwright `context.request`). It stores no credentials.
Only the requests below are used.

| Purpose | Request |
|---|---|
| Guide list | `GET /core/api/admin/bidding-guides?page=0&size=2000` -> Spring page `{content:[guide]}` |
| Guide tree | `GET /core/api/admin/bidding-guides/{id}/tree` -> full grid |

Guide (list item): `id, make, model, modelCode, minGrade, notes` (pipe-separated text rules),
`bossBid` (false = Team Bid), `dialPercent` (null = x100%, already baked into values),
`variants[] {id, name, active}`, `active`, `updatedAt`, `avgBidJpyK`, `missingBidRows`.

Tree: `variants[] { name, active, bandTemplate[{odoFrom,odoTo}], blocks[] }`.
Each block is one MRE/SEV approval pair; the one with `selected: true` is the live grid.
`Approval` (`carbarn/models.py`) reads these block keys: `sevNumber, mreNumber, selected, active,
sevBuildFromYm/ToYm, mreBuildFromYm/ToYm, expiryDate, sevModelCodes, sevVariant,
sevVariantDetails, sevNotes, sevCriterion, sevVehicleCategory, mreModel, mreNotes,
mreSpecialNotes, mreModificationCategory, mreComplianceLevel, lastScrapedAt, years[]`. Holder,
contact, phone, website and id keys are ignored.
`block.years[] { year, eligible, rowNoBid, lines[], missingOdoBands[] }`.
`line { year, odoFrom, odoTo, biddingJpyK, effectiveBiddingJpyK, effectiveNoBid, effectiveReason, active, grade, seats }`.
`modifiers[] { variantId, yearFrom, yearTo, resolvedYearFrom, resolvedYearTo, odoFrom, odoTo,
conditions[{attr, op, value}] | null, amountJpyK, percent, noBid, strict, note, active, stale }`.
Observed: `noBid` rows labelled "Row off - <year>"; a grade bonus is stored as
`conditions=[{attr:"GRADE", op:"eq", value:"4"}], amountJpyK=80, years 2018-2021` (the test fixture's invented amount).

## Nichibo AutoSearch

Auth: browser session cookie (login form at /login). Bot reuses the persistent profile.

| Purpose | Request |
|---|---|
| Search | `GET /auction?Make=All&Model[]=All&search-type=global&YearFrom=&YearTo=&KMMin=&KMMax=&Grade=&Engine=0+-+99999&Chassis=GDH206&AuctionPlace[]=All&sortGridSize=6&includeStock=Y&sort=Make,+Model,+Year&order=ASC&perPage=1000&page=1` -> HTML |
| Detail | `GET /auction/{refno}` -> HTML |
| Cart toggle | `GET /auction/cart/{refno}` -> `{"result":"added"|"removed"}` |
| Sheet translator | `GET /auction/jsonAuctionSheetData?AuctionSheet=<url>&AuctionHall=&ExhibitNo=&AuctionDate=YYYYMMDD` -> `{Type, AuctionSheetURL, WordsEN{SalesPoint, VehicleCondition, ModelNo, Chassis, Variant, Comments}}` |
| Suggested range | `GET /auction/jsonSuggested?Grade=&Shift=&Chassis=&km=&Year=&variant=&make=&model=&vid=&Engine=` |
| Sold comparables | `GET /auction/jsonMaketPrice/?<same params>` -> `{data:[{Model,Variant,Year,KM,Grade,SoldPrice}]}` |
| Place bid | `GET /auction/bid?v={refno}&b={yen}&ba={thousands}&cr=false&c={comment}&u=false&bidCurrencyCode=JPY&sa=0` -> `{"type":"success"}`; the page button `#btnPlaceBid` does exactly this. The bot clicks the button instead of calling the URL. |

Search result card (`div.card-item-listings`):
- `a.preview[href]` = detail URL (refno is the last path segment), `data-auction` = sheet image.
  This is the same URL the detail page uses for the sheet (`AuctionSheet` in its JS), so the
  scan downloads the sheet from the card and opens the detail page only when a rule needs
  the chassis suffix, the colour or another page-only fact (`pipeline.needs_detail`). The sheet
  is served at 800x800; a 3.7 KB 400x300 GIF means the auction house has not published it yet.
- `.car-title-h` "2018 Toyota Hiace", `.title-desc-listing small` variant, `.transmission-listing small`
- `p.card-text` "76,000km 2,800cc Grade: 0"
- `span[id^=bidPanelPrice]` start price
- `button.cart-button-list[data-refno][data-incart]`
- Pagination: hidden `#page`, `#perPage` (50/100/200/1000), "N total vehicles"

Detail page:
- `table.table-new` rows: Year of Registration, Make, Model, Variant, Auction Place, Exhibit #, KM, Grade, Colour, Engine, Chassis, Transmission, Equipment
- JS vars: `var refno = '...'; var startPrice = '1290000'; var dataParams = '{"Grade":..,"Chassis":"GDH206V","km":..,"Year":..,"vid":..}'`
- Translator call in page JS has `AuctionSheet`, `AuctionHall`, `ExhibitNo`, `AuctionDate`
- Photos: `a.gallery-demo[href]`; sheet: `img#auction-sheet-img[src]`
- Existing bid: `#currentBidRow` visible with `#currentBidAmount`
- Bid inputs: `#bidAmountDisplay` (thousands, keyup drives `#bidAmount`), `#bidComment`, `#btnPlaceBid`; success shows `#boxBidSuccess`
- "Request images from Live Support" button `#btn-request-image` appears when images are missing

## kurumaerabi.com catalogue (2026-10-20)

The maker's new-car catalogue, read by the equipment ladders (`catalog/`). Public pages, no sign-in,
plain GET. Base address `catalog.base_url`; the client allow-lists `/catalog/` paths on that host,
refuses redirects, paces calls by `catalog.pace_seconds`, and caches pages under `data/cache/catalog`
for `catalog.cache_days` (90).

| Page | URL shape |
|---|---|
| Model-code sitemap | `/catalog/xml-sitemap/type.xml` (the `<loc>` entries ending in `/type/<CODE>/` are model-code pages; per-model `/type/` index pages are skipped) |
| Model-code page | `/catalog/<maker>/<id>/type/<CODE>/` (the code may carry the emissions prefix, `3BA-AGH30W`; the lookup matches on the bare part, `AGH30W`) |
| Grade page | `/catalog/<maker>/<id>/fmc<NNN>/<grade id>/` |

- Model-code page: grades are grouped in `<section id="section_...">` blocks headed "YYYY年M月モデル".
  Each grade is a link box with its name (the `h3` link), its address and the `定員` `dt/dd` (seats, "5名").
  The Welcab seat system is read from the grade's name.
- Grade page `dt/dd` labels read: `型式` (model code), `乗車定員` (seats), `福祉車両` (welfare vehicle),
  `クルーズコントロール` and `アダプティブクルーズコントロール`. The two cruise labels are read only
  after the `運転支援` (driver assistance) heading. Title pattern:
  `YYYY年M月〜販売中モデル <grade name>の新車カタログ` or `YYYY年M月〜YYYY年M月販売終了モデル <grade name>の新車カタログ`
  (the sale period and the grade name come from it).
- Marks, two vocabularies. Older pages write 標準装備 / オプション / ◯ / blank; newer pages write
  ◯ / なし / メーカーオプション / blank.

| Mark | Reads as |
|---|---|
| 標準装備, ◯, ○ | standard |
| オプション, メーカーオプション, ディーラーオプション | optional |
| なし, ー, -, × | none |
| blank, anything else | unknown (the catalogue says nothing; never "none") |

  A mark containing 円 (a price) is read as unknown and never reaches the prompt.
  For `福祉車両`, ◯ marks a Welcab grade; its seat system comes from the grade's name.
- At most 6 model-code pages are read per lookup. Model codes whose bare part is shorter than 4
  characters or has no digit are ignored.
- Other catalogues tried on 2026-10-20: goo-net refused the connection and carsensor was in
  maintenance. Neither is used.
