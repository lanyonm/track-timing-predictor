# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Documentation rule

**Every code change must update the affected documentation in the same commit.** This file, `README.md`, `plans/hosting-plan.md`, and `docs/*.md` describe the code *as it is now*, not as planned. If a change alters a route, env var, cookie, module responsibility, data model, CLI flag, deploy step or anything else stated in these docs, update the statement.

## Commands

Python 3.13 (matches the Lambda base image and CI).

```bash
uv venv --python 3.13 .venv && source .venv/bin/activate   # or python3.13 -m venv .venv
pip install --require-hashes -r requirements-dev.txt       # runtime + pytest, pytest-asyncio, pytest-cov, moto, ruff, mypy

uvicorn app.main:app --reload              # http://localhost:8000, try EventId 26008

pytest                                     # all tests (SQLite temp DB, no network, no AWS)
pytest tests/test_predictor.py::TestComputeDelay::test_positive_delay_when_behind

python -m tools.extract_competition 26008                 # → data/competitions/26008.json (gitignored)
python -m tools.load_durations data/competitions/*.json   # → learning DB; --force skips the dedup prompt
python -m tools.rebuild_aggregates [--apply]              # DynamoDB only: recompute AGGREGATE# items from OBS#; dry run by default
```

**Dependencies:** ranges live in `pyproject.toml` (runtime in `dependencies`, tooling in the `dev` extra). `requirements.txt` (runtime, installed by the Dockerfile) and `requirements-dev.txt` (CI and local) are hashed locks generated from it; `cdk/requirements.txt` is a hashed lock of `cdk/requirements.in`. After changing a range, regenerate the locks and commit them:

```bash
uv pip compile pyproject.toml --universal --python-version 3.13 --generate-hashes -o requirements.txt
uv pip compile pyproject.toml --extra dev -c requirements.txt --universal --python-version 3.13 --generate-hashes -o requirements-dev.txt
uv pip compile cdk/requirements.in --universal --python-version 3.13 --generate-hashes -o cdk/requirements.txt
```

The dev lock is constrained by the runtime lock so shared packages match the image. The CDK CLI is pinned in the workflows (`npm install -g aws-cdk@<version>`); bump it with `aws-cdk-lib`. Dependabot (`.github/dependabot.yml`) proposes monthly updates for pip (root and `cdk/`), GitHub Actions (pinned by commit SHA) and the Dockerfile base image (pinned by digest).

Lint, format and type checking (config in `pyproject.toml`; CI runs all three):

```bash
ruff check .                               # lint (E, F, I, B, UP, ASYNC); --fix applies safe fixes
ruff format .                              # format; CI runs ruff format --check
mypy                                       # type-check app/ (non-strict, check_untyped_defs)
```

**Environment variables** (`app/config.py`, `pydantic_settings`, no prefix):

| Variable | Default | Description |
|---|---|---|
| `TRACKTIMING_BASE_URL` | `https://tracktiming.live` | Upstream base URL for the shared httpx client |
| `DB_PATH` | `timings.db` | SQLite path (used when `DYNAMODB_TABLE` is empty) |
| `DYNAMODB_TABLE` | `""` | Enables the DynamoDB learning backend when set |
| `PALMARES_TABLE` | `""` | Enables the DynamoDB palmares backend when set |
| `AWS_REGION` | `us-east-1` | DynamoDB region |
| `REFRESH_INTERVAL_SECONDS` | `30` | HTMX polling interval passed to templates |
| `MIN_LEARNED_SAMPLES` | `3` | Samples required before a learned average is used |
| `VENUE_TZ` | `America/Toronto` | Fallback IANA timezone for "now" when no live session has results; validated at startup |
| `PUBLIC_BASE_URL` | `""` | Origin for the palmares share link; prod CDK sets `https://ttp.lanyonm.org`, empty uses the request host |

## Taxonomy

Competition (`competition_id`, upstream `EventId`) → Session (a day's block, `Session` model) → Event (one race, `Event` model) → Heat (`heat_count`, `active_heat`).

Route URLs and form fields use `event_id` (e.g. `/schedule/{event_id}`) to keep bookmarks working and match upstream `EventId`. Python code and templates use `competition_id`.

## Architecture

FastAPI app that predicts per-event start times for track cycling competitions on tracktiming.live.

**Deployment:** AWS Lambda (Docker image from ECR, `Dockerfile`) behind a Function URL, adapted with Mangum (`handler` in `app/main.py`). Prod sits behind CloudFront at `ttp.lanyonm.org`, which uses OAC to sign requests to an `AWS_IAM` Function URL. Infra is CDK in `cdk/`; details are in `plans/hosting-plan.md`. **All routes must be GET.** CloudFront OAC can't sign POST bodies to Function URLs (403).

**CI/CD** (`.github/workflows/`):
- `test.yml`: on pushes and PRs to `main`, a `lint` job (ruff check, ruff format --check, mypy) and a `test` job (pytest with coverage); `test` updates the coverage badge gist on `main`. Read-only token.
- `deploy.yml`: runs via `workflow_run` after Tests succeeds for a push to `main`, so a red `main` doesn't deploy. Builds the tested commit's image (SHA tag + `prod-latest`) and runs `cdk deploy` for prod in the `production` environment (main only, no reviewer), serialised by the `deploy-prod` concurrency group.
- `pr-environment.yml`: for same-repo PRs, builds the image (`pr-<N>-<sha>`), deploys `TrackTimingStack-pr-<N>` with a public Function URL, comments the URL on the PR, and destroys the stack on close. Runs serialised per PR.
- `cleanup-pr-stacks.yml`: weekly sweep that deletes `TrackTimingStack-pr-*` stacks whose PR is closed.
- Two OIDC roles (`cdk/base_stack.py`): the prod role trusts only the `production` environment and deploys through the CDK bootstrap roles; the PR role trusts PR and main-branch runs, and PR stacks deploy with its own credentials (`CliCredentialsStackSynthesizer`), so its policy limits PR workflows to `pr-*` stacks and resources. Details in `plans/hosting-plan.md`.

**Configuration:** `app/config.py` exposes a module-level `settings` singleton and `get_settings()` for `Depends()`.

**HTTP client:** the FastAPI `lifespan` creates an `httpx.AsyncClient` (`max_connections=50`, 15 s timeout) on `app.state.http_client`. Routes get it via `Depends(get_http_client)`. Under Mangum the lifespan runs per invocation, so on Lambda the client is not reused across requests.

**Request flow (`/schedule/{event_id}`):**
1. `fetcher.fetch_initial_layout` POSTs to the Jaxon endpoint (refresh uses `fetch_refresh`).
2. `parser.parse_schedule` turns the HTML into `Session`/`Event` models.
3. `main.py` concurrently fetches start lists, result pages and live-heat pages, filling the predictor caches. When a racer is set and some race has no start-list riders, or a combined-age bunch final has no cached start-list categories, the same `gather` fetches the Rider List (`_fetch_rider_list_if_needed`, cached by URL).
4. `predictor.predict_schedule` builds a `SchedulePrediction`.
5. Jinja2 renders `schedule.html`; HTMX polls `/schedule/{id}/refresh`, which returns `_schedule_body.html`.

**tracktiming.live API** (unversioned and undocumented, so parse defensively):
- `POST eventpage.php?EventId={id}` with form body `jxnfun=getInitialPageLayout&jxnr=1` (initial) or `jxnfun=refreshPage&...` (refresh); the response is JSON with a `jxnobj` array.
- The schedule HTML is either a top-level `id="scheduleview"` object or nested in `id="dynarea"` (the live API). The parser handles both.
- Status comes from the event's row buttons (no `disabled` class): `btn-success` means COMPLETED (href is the result page), `btn-primary` means UPCOMING (href is the start list), `btn-info` is the audit page, and `btn-danger` is the live timing page. Anything else is NOT_READY.
- The session summary looks like `"Schedule - Friday - 08:15"`; times are venue-local and naive.
- Both responses carry a top-level `documents` jxnobj (Event Documents table). `parser.parse_rider_list_url` takes the `href` of the row whose `<h4>` is `Rider List` (e.g. `results/E26037/X-RIDERLIST-0-0-S.htm`). Rider List rows are `tbody tr` with cells bib, name, category, team, flag image, nation, space-separated event codes; `parser.parse_rider_list` strips the inline base64 flag images first.

**Routes:**

| Path | Description |
|---|---|
| `/` | Landing page with EventId form (works without JS) |
| `/schedule` | No-JS form target; `?event_id=X` → 303 to `/schedule/X` |
| `/schedule/{event_id}` | Schedule view; optional `?r=` (URL-safe Base64 racer name) |
| `/schedule/{event_id}/refresh` | HTMX partial; optional `?r=` |
| `/settings/racer-name` | Set/clear `racer_name` cookie; `?event_id=&name=` |
| `/settings/use-learned` | Toggle `use_learned` cookie; `?event_id=&use_learned=on\|off` |
| `/palmares` | Palmares page; `name=` sets the cookie and 303s to `?r=`; otherwise the racer comes from `r=`, then the cookie |
| `/palmares/export` | CSV of one rider's (or team's) audit data; `audit_url` must start with `results/` after percent-decoding and `normpath`; pages over 2M chars give 502; `Content-Disposition` carries an ASCII `filename` plus RFC 5987 `filename*` |
| `/palmares/rename` | Rename a competition; requires `racer_name` cookie |
| `/palmares/remove` | Delete a competition's entries; requires `racer_name` cookie (403 otherwise) |
| `/defaults` | Built-in default durations |
| `/learned` | Learned duration averages |
| `/health` | Always 200; per-component `healthy`/`degraded` |

**Cookies:** `racer_name` (`b64.` + unpadded URL-safe Base64 of the name, since Starlette encodes headers as Latin-1; legacy raw-name values are still read and rewritten on the next schedule view; 1 year, HttpOnly, Secure, Lax); `use_learned` (`"true"` when on; off by default); `theme` (`light`/`dark`, set client-side, 1 year).

**In-memory caches** (`predictor.py`, keyed by `(competition_id, session_id, position)`, unbounded, per Lambda container): `_status_cache` (status transitions for wall-clock learning), `_observed_durations`, `_heat_counts`, `_live_heats`, `_generated_times`, `_start_list_riders` (an empty list counts as no start list: `has_start_list_riders` is false and it is refetched, except for COMPLETED events, whose start list is fetched once; an empty parse never replaces cached riders), `_start_list_categories` (non-empty Category column values from `parser.parse_start_list_categories`; only combined-age start lists have the column). `_rider_lists` is keyed by Rider List URL instead and holds non-empty lists forever (the file is immutable for a competition); a failed fetch or 0-row parse goes in `_rider_list_retry_at` and isn't retried for `RIDER_LIST_RETRY_SECONDS` (10 min).

**Duration source priority** (`predictor.predict_session`):
1. Observed: result-page Finish Time + changeover (bunch races).
2. Generated: difference between an event's result-page Generated timestamp and the previous event's, kept if within 0.5×–2.0× of that event's expected duration. Generated marks an event's end, so the gap belongs to the later event. `predictor.generated_gap_duration` does this for both the app and `tools.extract_competition`.
3. Heat count: `heat_count × per_heat_duration + changeover`.
4. Fallback: if the `use_learned` cookie is on, the discipline-level learned average (`get_learned_duration`, ≥ `MIN_LEARNED_SAMPLES`); otherwise `DEFAULT_DURATIONS`.

A medal ceremony with a podium forecast skips all four and uses `CEREMONY_BASE_MINUTES + podiums × CEREMONY_PER_PODIUM_MINUTES` (13 + 3.3, `disciplines.py`). Its Generated timestamp marks its start, so the gap before a ceremony is never used.

The UI labels these as **obs.** (1–2), **N heats** (3), **N podiums** (ceremonies) and **est.** (4).

**Live delay** (`predictor._compute_delay`): applied only while a session has both completed events and pending non-special events (a NOT_READY End of Session doesn't keep a finished session live). The same condition gates the active-event flag. It is clamped to [−30, +120] min and returns 0 once `actual_elapsed > total_est + 60 min`, so post-event views show scheduled times. "Now" comes from `clock.venue_now()`, naive to match the schedule. Upstream exposes no timezone, so the venue's UTC offset is inferred from the newest Generated timestamp in an in-progress session (`predictor.latest_live_generated_time`): Generated ≤ venue-local now, so (Generated − UTC now − 2 min skew allowance) rounded up to the whole hour is the offset while that result is under ~58 min old. Results older than that (a long break) give an offset an hour low, and half-hour zones aren't supported. With no live session, or an offset outside UTC−12..+14, it falls back to `VENUE_TZ`. Both schedule routes use it, and the "Last updated" label shows it (the refresh partial carries it in `#schedule-generated-at`).

**Learning** (`database.py`; DynamoDB when `DYNAMODB_TABLE` is set, otherwise SQLite):
- *Live app writes* go through `record_live_duration(..., source)`: `"observed"` from result-page Finish Times, `"wall_clock"` from the UPCOMING→COMPLETED fallback (capped at 3× static default). They're idempotent per `(competition, session, position)` and keep any existing record, except that an observed value replaces a wall-clock one. Loader records (no `source`) are never replaced. DynamoDB reuses the structured `OBS#` path with a `source` attribute; SQLite stores it in a `source` column.
- *Loader writes* go through `record_duration_structured()` (returns `RecordOutcome`: created/updated/unchanged/error), with classification, gender and per-heat duration. They're idempotent: SQLite uses `INSERT OR REPLACE`; DynamoDB uses an `OBS#<comp>#<sess>#<pos>` item as a commit marker written after the `AGGREGATE#...` updates, with delta correction on re-load.
- *Reads:* the app uses only `get_learned_duration(discipline)` (overrides first, then the average). `get_learned_duration_cascading(discipline, classification, gender)` exists and is tested, but nothing in the app calls it.
- The DynamoDB key layout (`AGGREGATE#` levels, `OVERRIDE#`, `OBS#`) is documented in the comment block near the top of the DynamoDB section in `database.py` (~line 150). `aws_errors.py` holds the optional-botocore import shared by `database.py` and `palmares.py` (`BotoError`, `ClientError`, `raise_if_auth_error`). SQLite tables are `event_durations` (with `_migrate_schema` adding columns to old DBs) and `discipline_overrides`.

**Disciplines:** two classifiers exist.
- `disciplines.detect_discipline` is an ordered keyword list (`DISCIPLINE_KEYWORDS`, more specific phrases first). The live app uses it; `disciplines.py` also holds `DEFAULT_DURATIONS`, `PER_HEAT_DURATIONS` and changeovers.
- `categorizer.categorize_event` is a bilingual strip-and-match parser. It extracts special event → omnium part → ride number → round → classification → gender → discipline, then maps pursuits to `pursuit_4k`/`3k`/`2k`, and returns `(EventCategory, unresolved_text)`. Only `tools/` use it.
- Individual pursuit distance: both classifiers' name-based guess is overridden by `disciplines.pursuit_discipline_from_urls` whenever the event has any URL, since upstream page names encode the distance (`W4044-IP-3000-Q-0-R.htm`). `parser.parse_schedule` and `tools.extract_competition` apply it. Names alone guess wrong for masters age groups, Junior Women, U17 Men and French names.

**Rider List matching** (`rider_list.py`, pure functions; wired in by `predictor.predict_schedule(..., rider_list=)`):
- For a non-special event with no start-list riders, the racer's Rider List row (`find_rider`, same token matching as start lists) is matched by age band, gender and event code. A start list with riders always wins, even if the racer isn't on it.
- Scope is EventId 26037's formats only: categories `[MW]NNNN` (lo–hi) or `[MW]NN` (lo and over), event names with `NN-NN`/`NN+` then `Men`/`Women`, and codes S TT IP TP TS SCR PTS (`CODE_DISCIPLINES`). Other categories (e.g. 26008's `ME`, `MU17`) and codes produce no match. The event band must contain the rider's band.
- `match_events` decides certainty per code over the events this rider matches: a lone event is **Entered**; otherwise Qualifying/Qualifier N rounds are Entered and the rest **If advancing** (`RiderMatch.tentative`). Per-rider grouping keeps finals tentative when overlapping open bands (55+ and 65+) share a qualifying round. Matches have `source="rider_list"` and no heat; next race uses the event's predicted start. When the rider matches several numbered qualifiers for a code (e.g. Scratch Race Qualifier 1 and 2), they ride only one, so those matches set `parallel_qualifier` and next race reads "… (or a later qualifier), be ready by HH:MM".
- The template replaces the start-list warnings with an info line naming the category and codes (`SchedulePrediction.rider_list_entry`, set only when a Rider List match exists). Rider List matches never create palmares entries.

**Ceremony podiums** (`ceremonies.py`, pure functions; `predictor.predict_schedule` calls `forecast_podiums` with the cached start-list categories and the Rider List):
- A ceremony awards the finals since the previous ceremony, across sessions. Rounds (`1/N Final`) don't count; a sprint Final counts after its last scheduled ride; team events are one podium.
- Combined-age points and scratch finals (`35-49 Women`, `50+ Women`) are one podium per category: start-list Category column, else Rider List categories with that event code inside the band, else five-year bands in the name (1 for an open band).
- Scope is 26037's naming: a ceremony whose window has a final without an `event_band` gets no forecast and keeps `DEFAULT_DURATIONS["ceremony"]`. Rationale and data in `docs/medal-ceremony-durations.md`.

**Palmares** (`palmares.py`; DynamoDB when `PALMARES_TABLE` is set, otherwise SQLite `palmares_entries`):
- Collected automatically on schedule views when a racer is identified and matched on a start list to a timed event that has an audit URL.
- Timed disciplines are listed in `_TIMED_DISCIPLINES` in `main.py`: pursuits, `team_pursuit`, `team_sprint` and time trials.
- Team start lists pack the team name and riders into `<h4>` separated by `<br/>`; `parser._extract_names_from_h4` splits them, and `team_name` is stored because audit pages use team names.
- The competition date is the earliest result-page Generated timestamp.
- Public API: `save_palmares_entries`, `get_palmares`, `count_competition_palmares`, `update_competition_palmares`, `delete_competition_palmares`.
- Entries are keyed by the raw racer name string. The DynamoDB keys are `RACER#{name}` and `COMP#{id}#S#{sid}#E#{pos}`.
- `audit_parser.py` parses `-AUDIT-R.htm` pages (riders from `<p>`, heats from `<h3>`), filters with `normalize_rider_name`, and `format_csv` emits Heat, Dist, Time, Rank, Lap, Lap_Rank, Sect, Sect_Rank.

**Frontend:** Jinja2 templates in `app/templates/` (`base.html`, `index.html`, `schedule.html` + `_schedule_body.html`, `palmares.html`, `defaults.html`, `learned.html`). DaisyUI v4 + Tailwind (Play CDN) + HTMX 1.9 are loaded from CDNs in `base.html`. `static/style.css` holds only app-specific overrides; the schedule table becomes cards below 768px (`.schedule-table`).

## Key Patterns

- `tests/conftest.py` points SQLite at a session-scoped temp file, blanks `DYNAMODB_TABLE`/`PALMARES_TABLE`, and empties the learned-duration tables before each test. DynamoDB tests use `moto`.
- Parsers are tested against captured upstream HTML/JSON in `tests/fixtures/` (including `sample-event-output.json`). New parsing of upstream formats needs a captured fixture (constitution, Principle II).
- Special events (`SPECIAL_EVENT_NAMES` in `disciplines.py`: break, pause, end of session, medal ceremonies, medal ceremony) set `is_special`. They're excluded from `is_complete` checks, and their COMPLETED status is deferred until the next event starts. `end_of_session` contributes 0 minutes.

## Repository map

- `pyproject.toml`: project metadata, dependency ranges and ruff/mypy/pytest/coverage config.
- `app/`: application. `tools/`: CLI importers and `rebuild_aggregates` (DynamoDB aggregate repair). `tests/`: pytest suite plus `fixtures/`. `cdk/`: infrastructure. `static/`: CSS.
- `specs/NNN-name/`: speckit feature artifacts (spec, plan, tasks, research, contracts). 001–005 are complete and historical; read them for rationale, not current behaviour.
- `.specify/`: speckit config. Only `memory/constitution.md` (project principles that govern design trade-offs) and `templates/overrides/` (project-specific plan and task rules) are committed. The rest of `.specify/` and the `/speckit.*` commands in `.claude/commands/` are installed locally and gitignored. The project uses Spec Kit **v0.2.1**; to install it, run `uvx --from git+https://github.com/github/spec-kit.git@v0.2.1 specify init --here --ai claude --script sh --force`. This keeps the existing constitution and overrides; check `git status` afterwards.
- `plans/`: pre-speckit design notes. `hosting-plan.md` is the current infrastructure reference; `data-pipeline*.md` and `dynamo-import-reload.md` are historical.
- `docs/`: `duration-data-import.md` (extract/load tooling reference), per-discipline duration rationale (`sprint-`, `mass-start-race-`, `timed-event-`, `medal-ceremony-durations.md`), and historical HTML UI prototypes (`daisyui-*`, `*-mockup.html`).

## Conventions

- Feature branches: speckit features use `NNN-short-name`; other work uses a descriptive branch name.
- `/speckit.plan` runs `.specify/scripts/bash/update-agent-context.sh`, which appends "Active Technologies" and "Recent Changes" sections to this file. Delete them afterwards; this file stays current-state only.
