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

## 2. Rule semantics are Jim's, not ours

- Bid amounts come from the Carbarn bidding guides (snapshot in `rules/snapshots/`). Do not
  invent, round, cap or "improve" amounts. Do not hard-code prices.
- Confirmed by Jim on 2026-09-14: odo bands are upper-bound inclusive; grade = Nichibo listing
  grade (0/R/RA/blank = skip); a positive grade bonus stored as "grade 4" applies to 4 and above,
  negative grade adjustments stay exact; a percentage dial (the xNN% badge) is already baked into grid values.
- Anything not covered by a stored rule goes to the reviewer as NEEDS_REVIEW. Uncertainty is
  never resolved in favour of bidding.
- Do not change how variants, modifiers or model matching are interpreted without asking Jim.
  Open questions are listed in `README.md`; add to that list rather than guessing.

## 3. No paid model APIs

- Default mode is `sheet_check: none`: Jim judges the auction sheet and photos himself on the
  review page. Do not add Anthropic/Claude API calls. The optional OpenAI path stays opt-in and
  only runs if Jim puts a key in `.env` and sets `sheet_check: openai`.
- Even with a model enabled, model output is a pre-fill for the reviewer, never a bid decision.

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
