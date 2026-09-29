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
| Dashboard routes | `dashboard/routes.py` | Overview, runs, a run, insights, settings, and the JSON API. The local-only check. |
| State of the day | `dashboard/status.py` | Lock, sign-in, guides, model quota, counts, the five steps and which is next, the day log. Reads only. |
| Commands and runs | `dashboard/jobs.py` | The command table, the job runner, reading runs and logs back. |
| Progress and Stop | `progress.py` | The lines a run prints for the page, and the stop check. |
| One at a time | `lock.py` | The lock on the bot's browser profile. |
| Pages | `review/templates/` | `base.html` (shell), `_parts.html` (console, launcher, log entry), one file per page. |
| Look and behaviour | `review/static/` | `bidbarn.css`, `review.js` (review pages), `dashboard.js` (starting commands, the console), `icons.svg`, `vendor/`. |

## Routes

| Route | What |
|---|---|
| `GET /` | Overview |
| `GET /runs`, `GET /runs?command=scan` | The runs, optionally of one command |
| `GET /runs/{id}` | One run: live console, or outcome and log |
| `GET /runs/{id}/log.txt` | The raw log as a file (only from `data/runs/`) |
| `GET /insights?days=&min_support=` | Insights |
| `GET /config` | Settings, read only |
| `GET /review`, `/list`, `/p/{id}` | The review pages |
| `GET /api/status` | The state of the day, as the overview shows it |
| `GET /api/commands` | The command table, for the forms |
| `POST /api/runs` | Start a command. Body `{"command": "scan", "options": {...}}`. 201 the run, 400 unknown command or option, 409 something is running |
| `GET /api/runs`, `/api/runs/{id}` | Runs as data |
| `GET /api/runs/{id}/log?after=&limit=` | Log lines from an index |
| `GET /api/runs/{id}/events` | The live stream (server-sent events) |
| `POST /api/runs/{id}/cancel` | Stop. 202, or 409 when it is not running |

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

## The command table is the fence

`jobs.COMMANDS` lists every command the page can start and every option each one takes. The
page sends names and values; the server builds the arguments. Nothing the page sends reaches
the command line as text.

- `place` has no option for a live placement, and `build_argv` refuses any argument list that
  contains that flag, whatever produced it. `Config.placement_mode` and `place_bid` remain the
  real locks; this is a second fence in front of them.
- `judge` always runs as `--pending`.
- `bid`, `learn` and `watch` are not in the table. `bid` serves its own review page on the same
  address; the other two need Jim at the bot's browser.
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
- that a browser window appears when a command started from the page shows its window
  (scan with "Hide the browser window" off, and the sign-in window);
- that Stop's forced ending takes the browser down with the command;
- the database's write-ahead mode on the real `data/bot.sqlite`.
