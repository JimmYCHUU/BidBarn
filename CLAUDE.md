# BidBarn — rules for anyone (human or Claude) working in this repo

This tool places real-money bids on Japanese car auctions for Carbarn. The rules below are
hard constraints. Do not weaken them, work around them, or "temporarily" disable them.

## 0. Testing lock (Jim, 2026-09-14): no live bids until Jim says "The project is finished"

- `config.yaml` has `project_status: testing`. While it is anything other than the exact phrase
  `The project is finished`, `bot place` is always a dry run, even with `dry_run: false` and
  `--live`, and even for approved proposals. `Config.placement_mode()` and `place_bid()` both
  enforce this. Do not remove, bypass or auto-set it. Only Jim changes that line, by hand,
  after he has said those words.
- Until then every run is a test: scan, review, approve, dry-run placement, screenshots. Treat
  approvals recorded during testing as test data.

## 0b. Never touch Nichibo Stock (Jim, 2026-09-14)

- The bot uses only the auction search, auction detail pages, the sheet translator, images and
  the login page. It must never open, request or click anything on the Stock buy side of
  Nichibo, Vehicle Offers, any cart (auction or stock), bulk "Place All Bids", or the paid
  "Request images from Live Support" button.
- `nichibo/browser.py` refuses forbidden URLs (`ForbiddenUrl`) and aborts page requests to
  them; the search runs with `includeStock=N`; the card parser drops non-auction cards. Keep
  all three.
- One documented exception (Jim, 2026-09-15): `bot watch` (`watch.py`) opens the bot's
  signed-in browser for Jim to drive by hand and only records what he does (navigations,
  clicks, hovers, typed reasons, screenshots). The URL guard is off for that window because it
  would block his own clicks. In watch mode the bot makes no request of its own, never touches
  the bid form, and nothing recorded can approve anything. Do not reuse that context for scans.

## 0c. Never throw away scraped data (Jim, 2026-09-14)

- Do not delete `data/bot.sqlite`, `data/evidence/` or `data/cache/`. Every page, translator
  reply and image the bot downloads is cached for `cache_hours` and every evaluation is stored.
- After a rule or code change run `bot replay --apply`: it re-applies the current rules to the
  stored proposals from the saved facts and evidence with no Nichibo traffic, supersedes the
  ones whose outcome changed, and marks vehicles that need a page we never opened so the next
  scan fetches only those. `bot scan --fresh` is the only way to bypass the cache; use it
  deliberately, not by habit.

## 1. No bid without Jim's review

- A bid is placed only for a proposal whose `decision` is `approved` in the SQLite store, and an
  approval can only come from a human clicking Approve on the review page (`review/app.py`).
- Never add code that approves proposals automatically, calls `store.decide(...)` outside the
  review page, or marks a proposal approved on a heuristic, a model verdict, or a rule match.
- Never call Nichibo's `/auction/bid` URL directly and never click `#btnPlaceBid` anywhere except
  `nichibo/bid.py::place_bid`, which re-verifies the vehicle first.
- `dry_run: true` in `config.yaml` is the default and stays the default. Live placement needs
  BOTH `dry_run: false` in config AND `bot place --live`. Do not add a third way.
- Every vehicle can be bid on at most once ever (`bids` table primary key). Keep it that way.
- Never place, cancel or change a bid on Nichibo as part of a test, a demo, or "to check it works".
- Learning features (review telemetry, `skip:` standing instructions, `bot insights`,
  `bot learn --har`) may only add SKIPs, ordering and pre-fills. `skip` is the only verb a
  standing instruction can have; never add a verb that approves, and never let a suggestion
  become a rule without Jim adding it (review page button, `bot skip-line`, or the file).

## 2. Rule semantics are Jim's, not ours

- Bid amounts come from the Carbarn bidding guides (snapshot in `rules/snapshots/`). Do not
  invent, round, cap or "improve" amounts. Do not hard-code prices.
- Confirmed by Jim on 2026-09-14: odo bands are upper-bound inclusive; grade = Nichibo listing
  grade (0/R/RA/blank = skip); a positive grade bonus stored as "grade 4" applies to 4 and above,
  negative grade adjustments stay exact; a percentage dial (the xNN% badge) is already baked into grid values.
- Confirmed by Jim on 2026-09-30: 0 km falls in the lowest odo band; a km that no band covers
  (below the first band, or in a gap between bands) takes the closest band; above the last band
  stays a skip. A lot parked for unpublished images keeps its grid amount from the card, so it can
  be approved.
- Confirmed by Jim on 2026-10-20: cruise control and Welcab seat notes are judged by a ladder, in this
  order and never the reverse. 1) The auction sheet: yes = yes; a bare "CC" is not cruise control (the JU
  form uses ＣＣ for engine size, inspectors write クルコン / クルーズコントロール, and one Japanese
  glossary reads CC as car stereo). 2) The steering-wheel photo: no cruise control visible, or no wheel
  photo at all on a complete photo set = no; a short photo set (some photos could not be downloaded)
  reads review. 3) The maker's catalogue: standard on that grade = yes; optional or not fitted = no; no
  data, or grades that differ and the lot's grade cannot be named = review (when the model names the
  lot's catalogue grade, that grade's mark decides, and the Welcab seat block works the same way).
  Adaptive cruise counts as cruise control; a note that says
  radar/adaptive needs adaptive specifically. A Welcab must have a front swivel/rotating, front lift-up
  or second-row side lift-up seat; a slope/ramp or wheelchair-lift Welcab, a plain car, or a Welcab with
  no seat statement on the sheet, in the photos or in the catalogue is not eligible (a Welcab with no
  seat statement anywhere fails on a complete photo set; on a short photo set it reads review, like
  cruise control). A guide named
  "... Welcab" with no Welcab note gets the seat requirement from its name. The model only reports what
  it sees per step (`judge/ladders.py` decides); a missing or malformed observation reads review, never a
  yes. Do not move a step or change a verdict without asking Jim.
  - Refined during the build on 2026-10-21, to confirm with Jim: the catalogue alone never makes a lot a
    Welcab: for a lot the sheet or the photos call a Welcab, a catalogue that sells one Welcab seat system
    decides by that system; otherwise (several systems, or nothing on the sheet or in the photos says
    Welcab) only a catalogue grade the model names that is a Welcab row decides, by its seat system, and a
    named plain grade or no named grade is no seat statement (for a lot the sheet or the photos call a
    Welcab, a named plain grade conflicts and reads review). Only a cruise note is read as negated. A
    negated Welcab note that names a failing seat system ("No ramp / slope type", "No wheelchair lift") is
    the Welcab seat requirement itself, not negated; any other negated Welcab note ("No welcab", "No swivel
    seat") gets no ladder topic and stays a plain text requirement the model judges, as before the ladders.
- Anything not covered by a stored rule goes to the reviewer as NEEDS_REVIEW. Uncertainty is
  never resolved in favour of bidding.
- Do not change how variants, modifiers or model matching are interpreted without asking Jim.
  Open questions are listed in `README.md`; add to that list rather than guessing.

## 3. No paid model APIs; the free judgement layer is a pre-fill

- `sheet_check: judge` (Jim, 2026-09-28) uses free-tier vision providers chosen by Jim, today
  Gemini Flash-Lite first and Groq Llama 4 Scout as fallback, through their OpenAI-compatible
  endpoints (`judge/client.py`, keys in `.env`). Never add Anthropic/Claude API calls. The
  OpenAI path stays opt-in (`sheet_check: openai`, `OPENAI_API_KEY`). `sheet_check: none`
  means Jim judges the sheet and photos himself.
- The model's output is a pre-fill and a recommendation: per-requirement verdicts with
  evidence, whether a chip applies, odd cases, and one of the candidate amounts computed from
  the grid (`judge/candidates.py`). Every judged lot stays NEEDS_REVIEW; only Jim's click on
  the review page approves, and the model never sets a number outside the candidates.
- Data minimisation: the bid grid is never sent to a provider. A prompt carries the lot's
  facts, the OCR fields, Nichibo's translation, the guide's text requirements and chips, the
  odd-case flags, the selected MRE/SEV pair's (or, with none selected, each active pair's)
  restriction text (SEV variant details and notes, MRE notes and special notes; never holder
  names or contacts), the maker catalogue's grade table for the lot's model code (grade names, seats,
  cruise and welfare marks; never prices), Jim's playbook (`rules/playbook/`, git-ignored), the candidate
  amounts, the sheet and up to `judge.max_photos` photos, or every photo up to `judge.max_photos_ladder`
  for a lot with a cruise or Welcab requirement. Keep it that way.
- The playbook is Jim's words (learn-mode comments, review-page corrections, the seeded
  principles). It is context for the model, not code rules; never turn it into automatic
  approvals. Quotas: respect `rpm`/`rpd` per provider; when every provider is out, lots go
  to review unjudged (`judge_pending`), the scan never fails because of the model.

## 3b. Import eligibility is a review note, never a skip (Jim, 2026-09-30)

- Every lot is checked against the MRE/SEV pair behind its guide (`rules/eligibility.py`): build
  window, expiry, model codes, and the pair's restriction text read by the free judge model as a
  pre-fill. A failing lot stays NEEDS_REVIEW with the verdict "cannot import" and the reason; Jim
  rejects it himself. Never map an eligibility result to SKIP.
- Registered before the window opens = out. Any other lot in a partly covered year goes to review
  with "confirm the build month".
- Source is the guide tree only: no ROVER visit, no bidding-cost-profile call. An old ROVER copy
  is flagged from `lastScrapedAt` (`eligibility.stale_days`).
- Defaults chosen by the plan, to confirm with Jim: the selected pair decides even when an
  alternative covers the lot; with no selected pair the active pairs are alternatives (the bot may
  only recommend against a lot when every pair fails it); an unmatched model code reads
  confirm and an unparseable one is not checked (the card says "eligible (model code not checked)");
  neither reads cannot import; an unknown registration year reads confirm.

## 3c. The maker's catalogue (Jim, 2026-10-20)

- `catalog/` reads kurumaerabi.com (`catalog.base_url`) and no other site outside Nichibo, Carbarn and the
  model providers: its model-code sitemap, model-code pages and grade pages. Requests are allow-listed to
  `/catalog/` paths on that host, redirects are refused, pages are paced by `catalog.pace_seconds` and cached
  under `data/cache/catalog` for `catalog.cache_days` (`bot scan --fresh` does not bypass this cache). A lookup
  reads at most 6 model-code pages, and model codes shorter than 4 characters or with no digit are ignored. It sends only the
  catalogue's own addresses: never a lot, a price or anything from Carbarn.
- A catalogue answer is the third step of a ladder and nothing more: it never sets an amount, never approves,
  and a failed fetch is "unknown", never an error that stops a scan. `bot replay` never contacts the catalogue.

## 4. Data handling

- `har/`, `.env`, `data/` and `rules/snapshots/` are private and git-ignored. Never copy their
  contents into chat, tests, or commits. Test fixtures may hold public auction pages and guide
  trees, nothing with credentials.
- Never store Nichibo or Carbarn passwords. Login is manual via `login.cmd`; the bot reuses the
  browser profile in `data/profile`.

## 5. Working in this repo

- Commits and pull requests are authored by Jim only. Never add `Co-Authored-By` or
  "Generated with" lines (Jim, 2026-09-14).

- Run the CLI with `bot.cmd` / `python -m auction_bot.cli` (the venv `bot.exe` is blocked by
  Windows App Control on Jim's PC). `jiter` is pinned to 0.10.0 for the same reason; do not bump.
- Keep `tests/` green (`.venv\Scripts\python -m pytest -q`). `tests/test_hard_rules.py` holds
  the golden cases from Jim's walkthrough; if a rule change breaks one, ask Jim before changing
  the expected value.
- Pace Nichibo page loads (`pace_seconds`) and never run more than one scan or placement at a time.
- Endpoints learned from the HAR captures are documented in `docs/endpoints.md`; update it when
  something changes rather than leaving knowledge in code comments only.
