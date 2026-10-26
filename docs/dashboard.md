# The dashboard, for whoever changes it

What the page is for and who uses it is in `PRODUCT.md`. How it looks is in `DESIGN.md`. The
rules it must never break are in `CLAUDE.md`. This file is how it works.

## The shape

One local server (`bot dashboard`, `dashboard.cmd`) serves everything on the address in
`config.yaml` (`review.host`, `review.port`). `review/app.py::create_app(cfg, finish_event, jobs)`
builds it: the review routes are defined there, the dashboard routes are registered from
`dashboard/routes.py`.

| Piece | File | What it owns |
|---|---|---|
| Review routes | `review/app.py` | Triage, lot detail, the list, `decide`, `event`, `finish`, the skip-rule form. **The only place a lot is decided.** |
| Dashboard routes | `dashboard/routes.py` | Overview, runs, a run, insights, settings, and the JSON API. The local-only check. Registers the guide pages. |
| Desk and bid sheet | `review/desk_model.py`, `review/desk_routes.py`, `review/static/desk.js`, `desk.css` | `/desk` and `/bids`. Read the store; decide nothing themselves: `desk.js` posts to the review's own decide, delete and event routes after a 10-second undo window. |
| Guide pages | `dashboard/guides.py` | `/guides` and `/guides/{code}`: a bidding guide as a page (grid, requirements, chips, standing instructions, playbook, decisions). Reads only; its Add form posts to the review app's skip route. |
| State of the day | `dashboard/status.py` | Lock, sign-in, guides, model quota, counts, the five steps and which is next, the day log. Reads only. |
| Commands and runs | `dashboard/jobs.py` | The command table, the job runner, reading runs and logs back. |
| Progress and Stop | `progress.py` | The lines a run prints for the page, and the stop check. |
| One at a time | `lock.py` | The lock on the bot's browser profile. |
| Pages | `review/templates/` | `base.html` (shell), `_parts.html` (mode cell, stages, console, launcher, log entry), one file per page, `guides.html` and `guide.html` for the guides. |
| Look and behaviour | `review/static/` | `bidbarn.css`, `review.js` (review pages), `dashboard.js` (starting commands, the console), `icons.svg`, `vendor/`. |

## Routes

| Route | What |
|---|---|
| `GET /` | Overview |
| `GET /runs`, `GET /runs?command=learn`, `?command=bid`, `?command=steps`, `?command=scan` | The runs; `steps` means every single step, a command name means that one |
| `GET /runs/{id}` | One run: live console, or outcome and log |
| `GET /runs/{id}/log.txt` | The raw log as a file (only from `data/runs/`) |
| `GET /insights?days=&min_support=` | Insights |
| `GET /config` | Settings, read only |
| `GET /guides`, `GET /guides/{code}` | The guides in the snapshot; one guide as a page |
| `POST /finish` | The header's Finish button. Under `bot bid` on a console it sets the event the console waits on; under a Bid run started from the page it writes that run's finish file (see Finish). 404 when no review is waiting |
| `GET /review`, `/list`, `/p/{id}` | The review pages |
| `GET /desk`, `/desk/board`, `/desk/lot/{id}` | The desk: lots by auction session and lane. Reads only; its decisions go to `POST /p/{id}/decide`, `/delete` and `/event`. See `docs/desk.md` |
| `GET /bids` | The bid sheet: approved lots per auction, exposure, and a button that starts the dry-run `place` command |
| `GET /api/status` | The state of the day, as the overview shows it |
| `GET /api/commands` | The command table, for the forms |
| `POST /api/runs` | Start a command. Body `{"command": "scan", "options": {...}}`. 201 the run, 400 unknown command or option, 409 something is running |
| `GET /api/runs`, `/api/runs/{id}` | Runs as data |
| `GET /api/runs/{id}/log?after=&limit=` | Log lines from an index |
| `GET /api/runs/{id}/events` | The live stream (server-sent events) |
| `POST /api/runs/{id}/cancel` | Stop. 202, or 409 when it is not running |
| `POST /api/runs/{id}/pause` | Pause a Bid run while it signs in, fetches the guides, scans or judges. 202, or 409 when it is not running or not at one of those stages |
| `POST /api/sessions/{id}/resume` | Resume the paused session: Bid mode again, in the same session. 201 the run; 409 when that session is not the paused one, or something is running |
| `POST /p/{id}/delete` | Delete one lot that waits for review, without a decision. JSON `{"ok", "decision", "next_id"}` or a 303 to the next lot; 409 unless it waits |
| `POST /sessions/{id}/delete`, `/sessions/earlier/delete` | Delete session: its waiting lots leave the review; a running session is stopped first. 409 while it places bids |

Option values are typed: booleans, whole numbers from 1 to 100000, model codes as a list of
strings. A string where a number belongs is a 400.

## How a command runs

The page never runs a command inside the server. `JobManager.start` checks the request against
the command table, writes a row in the `runs` table, and starts a separate process:

```
python.exe -X utf8 -u -m auction_bot.cli <command> <arguments from the table>
```

Its output goes straight into `data/runs/<id>.log`. The server reads that file as it grows.
So a command started from the page prints exactly what it prints on a console, and if the
dashboard is closed while it runs, it carries on and finishes. The next start of the dashboard
looks at rows still marked `running`: a process that is alive is followed again, one that is
gone is closed as `stopped`.

A run ends as one of:

| Status | When |
|---|---|
| `ok` | The process ended with exit code 0 |
| `failed` | Any other exit code. `error` holds the `STOPPED:` line, or the last line printed |
| `stopped` | Stop was clicked, or the dashboard found the process gone |
| `paused` | Pause was clicked; the run ended at its next check point, or after the 20 s grace |

## The command table is the fence

`jobs.COMMANDS` lists every command the page can start and every option each one takes. The
page sends names and values; the server builds the arguments. Nothing the page sends reaches
the command line as text.

- `place` has no option for a live placement, and `build_argv` refuses any argument list that
  contains that flag, whatever produced it. `Config.placement_mode` and `place_bid` remain the
  real locks; this is a second fence in front of them.
- `judge` always runs as `--pending`.
- `bid` and `learn` are in the table with no options at all: `bid` runs as `bid --no-browser`
  (the review happens on this page, see Finish), `learn` as `learn`. `watch` is not in the
  table: Learn is watch plus the comparison.
- The Runs page shows a subset of each step's options (`routes.SHOWN_OPTIONS`: the model codes
  and the lot limit for a scan, the store switch for replay, nothing else). The table keeps every
  option, so `build_argv` still fills the rest with their defaults (a scan from the page is
  headless), and the API still refuses anything that is not in the table.
- Tests (`test_jobs.py`, `test_dashboard_app.py`) try every option and a list of hostile values.
  One of them reads every file in `dashboard/` and fails if the live flag is written anywhere
  in it, a comment included. To add a command, add it to the table and to those tests.

## What a run prints for the page

With `BIDBARN_PROGRESS=1` in its environment (the job runner sets it) a command prints two
kinds of extra lines. From a console, without the variable, it prints neither.

```
PROGRESS {"stage":"scan","guides":46,"guide":21,"code":"GDH206","codes":[...],"marks":"ffeex...","cards":812,...}
SUMMARY {"guides":46,"cards":1204,"review":9,...}
```

| Stage | Fields |
|---|---|
| `scan` | `guides`, `guide` (1-based, 0 before the first), `code`, `codes` (all guide codes in order), `marks`, and the running totals `cards`, `opened`, `review`, `skip`, `bid`, `reused`, `pending`, `model_calls`, `errors` |
| `judge` | `i`, `total`, `judged`, `unavailable`, `pages`, `errors` |
| `replay` | `checked`, `total`, `changed`, `applied`, `unchanged`, `needs_fetch` |
| `place` | `i`, `total`, `dry_run`, and per lot `ref_no`, `ok`, `message`, `screenshot_rel`, `amount_jpy_k` |
| `signin` | `nichibo`, `carbarn` (true, false), `waiting` (a window is open for Jim) |
| `guides` | `guides`, `snapshot` |
| `review` | `pending`, `approved`, `waiting` (true while the run waits for Finish, false once it has it) |
| `learn` | `phase` (`recording`, `guides`, `scan`, `compare`), `session`, `events`, `lots`, `bids`, `searched` |

A Bid run passes through `signin`, `guides`, `scan` (and `judge`), `review` and `place` in turn.
Each boundary line carries `step` and `steps` (1 to 5); the page draws the five stages from
them. A Learn run's `SUMMARY` is structured: `session`, `searched`, `counts` (`lots`, `agree`,
`disagree`, ...), `lots` (per lot what Jim did, what the bot proposed, what the model said and
whether they agree, the candidate rules), `notes`, `candidates`. The run page renders it as the
session table.

`marks` has one letter per finished guide: `f` lots for review were found, `e` nothing new,
`x` an error. The guide row on the run page is drawn from it, and because the last `PROGRESS`
line is stored with the run, a finished scan shows the same row later.

The page's log leaves these lines out. The file keeps them.

## Stop

`POST /api/runs/{id}/cancel` creates `data/runs/<id>.cancel`. The command looks for that file
before every page load (`Browser.pace`), at the top of each guide and each lot, and between
placements, never inside one. When it sees the file it raises `progress.Cancelled`, closes the
browser, and ends with `STOPPED: stopped from the dashboard`. If it is still alive 20 seconds
later the job runner ends the process and its children.

Two places in the scan used to catch every error per guide and per lot and carry on. They now
let `NotLoggedIn` and `Cancelled` through, so a signed-out scan stops at the first guide and a
stopped scan stops.

## Pause and Resume

`POST /api/runs/{id}/pause` creates `data/runs/<id>.pause`, named in `BIDBARN_PAUSE_FILE`.

- **The run ends at the same check points as Stop.** `progress.check_cancel()` raises `progress.Paused`, a kind of `Cancelled`. The run closes the browser, gives back the profile lock and is recorded as `paused`.
- **Pause is offered only before the review.** It applies to a Bid run that is signing in, fetching the guides, scanning or judging (`PAUSABLE` in `jobs.py`). At the review the run waits for Jim anyway, and a placement is never paused.

Every run has a `session_id`. Normally that is its own id. A Bid run started while a session is paused (`Store.paused_session()`) gets that session's id instead, and that is all Resume is: `POST /api/sessions/{id}/resume` checks that the session is the paused one, then starts `bid`. The scan walks the guides again. Their search pages within `cache_hours` come from the cache with no paced page load, and lots waiting for review are skipped, so it moves quickly to where it stopped.

## Delete

Delete takes lots out of the review without a decision. `proposals.decision` becomes `deleted` and `decided_at` and `decision_reason` are set. Nothing else is written: no `decisions` row, no review event, no judge feedback, no playbook line. No row is removed.

- **Only waiting lots.** Only lots waiting for review can be deleted (`store.WAITING`).
- **Which lots are a session's.** A scan writes the dashboard run on each lot (`proposals.run_id`, from `BIDBARN_RUN_ID`). A session's lots are therefore those of its runs. Lots from a console have no run and show as "Earlier scans".
- **Delete on a running session.** It calls `JobManager.cancel(run_id, delete=True)`, which deletes the waiting lots at once and again when the process ends. A start of the dashboard sweeps sessions marked deleted (`Store.sweep_deleted_sessions`).
- **What the next scan does.** A lot deleted in a session stays out for the rest of that session: a Resume skips it, because its deleted row was written by one of the session's runs (`Store.session_runs`). The next session's scan checks it again, because `_should_skip_existing` treats a deleted newest row from another session as unseen. It passes that row's judgement as `previous_soft`, so the model is not asked again when nothing changed.

## Finish

A Bid run started from the page cannot serve its own review page (the dashboard holds the
address), so it waits instead. `run.wait_for_review` polls for `data/runs/<id>.finish` once a
second, printing the counts as they change; the header's Finish button (`POST /finish`) writes
that file for the run that `jobs.review_waiting()` names (the running `bid` whose latest
progress stage is `review`). The run then removes the file and places the approved bids. The
button appears on every page of the dashboard only while such a run waits (`can_finish()` is a
callable now, checked per request). Stop during the wait ends the run as `stopped`; the
approvals stay in the store for a later Bid or Place. From a console, `bot bid` still serves its
own review page, and stops with a message if the dashboard is already on the address.

## One command at a time

The job runner runs one command at a time and answers 409 to a second start. Under that,
`lock.py` keeps `data/profile.lock` while anything holds the bot's browser profile (it is taken
in `Browser.__enter__` and in watch mode). A console `scan.cmd` started while the dashboard
scans stops with a message naming the run in the way. A lock left by a crash is recognised by
its process being gone and is replaced.

## Sign-in state

Whether the two sites are signed in cannot be read without starting the browser (the cookies
are encrypted in the profile). So it is a command: `bot check-login` starts the browser without
a window, looks, and ends in well under a minute. The overview shows the result of the newest
check with its age, asks for a new one after 12 hours, and distrusts an older "signed in" when
a later run stopped on the login page.

## This PC only

`routes.local_only` runs before every request. It refuses a request whose `Host` is not this
PC, and any request that changes something (`POST`) when the browser says it came from another
site (`Origin`, `Sec-Fetch-Site`). Without it a web page open in another tab could post to
`http://127.0.0.1:8765/p/7/decide` and approve a lot. Keep it in front of every route.

## Static files and the browser's cache

Every link to a file under `review/static/` carries `?v=<stamp>` (`asset_version()` in
`review/app.py`, the newest modification time under `static/`, read when the app is built).
Change a stylesheet, a script or the icon sprite, restart the dashboard, and the browser
fetches the new file. Without the stamp a browser kept an old `icons.svg` and drew nothing for
an icon added later. Use the `icon()` helper for icons; a script that swaps an icon keeps the
address it found and changes only the part after `#`.

## Where things live

Jim found the first build complex (2026-09-30), so each thing has one home:

- The two modes, Learn and Bid, start from the overview and nowhere else. The single steps
  start from the fold on the Runs page and nowhere else, except the two repairs on the overview's
  State card (Sign in, Fetch the guides) that appear only when something is wrong.
- A suggested skip rule and the form to add one live on the guide page (`/guides/{code}`). The
  review page and the Insights page only link there.
- The lock is stated in the header pill, the nav foot, Settings and the finish page, and in the
  approve row's hint. Nowhere else.
- The requirement verdicts of a judged lot are counts on the review page and a list on the lot
  page.

## Looking at the pages without touching real data

The tests render every page on a temporary database (`tests/test_dashboard_app.py`). To look
at them in a browser, build a `Config` that points at a temporary folder the way the `cfg`
fixture in `tests/test_pipeline_offline.py` does, give `create_app` a `JobManager` whose
`_spawn` starts a harmless script, and serve it on another port. Never point a preview at
`data/`.

## Not verified yet

These need a real run on the office PC and were not exercised during the build, which used
stand-in commands only:

- the profile lock with the real browser;
- that a browser window appears when a command started from the page shows its window (a Learn
  run, the sign-in window). The job runner starts commands with `CREATE_NO_WINDOW`, which
  governs the console only; if no Chromium window appears, `_spawn` should start headed
  commands without that flag and with a minimised console (`SW_SHOWMINNOACTIVE`);
- that Stop's forced ending takes the browser down with the command;
- the database's write-ahead mode on the real `data/bot.sqlite`.
