# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Documentation rule

**Every code change must update the affected documentation in the same commit.** This file, `README.md`, `plans/hosting-plan.md`, and `docs/*.md` describe the code *as it is now*, not as planned. If a change alters a route, env var, cookie, module responsibility, data model, CLI flag, deploy step or anything else stated in these docs, update the statement.

## Commands

Python 3.11 (matches the Lambda base image and CI).

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # runtime + pytest, pytest-asyncio, pytest-cov, moto

uvicorn app.main:app --reload              # http://localhost:8000, try EventId 26008

pytest                                     # all tests (SQLite temp DB, no network, no AWS)
pytest tests/test_predictor.py::TestComputeDelay::test_positive_delay_when_behind

python -m tools.extract_competition 26008                 # → data/competitions/26008.json (gitignored)
python -m tools.load_durations data/competitions/*.json   # → learning DB; --force skips the dedup prompt
```

No linter, formatter or type checker is configured.

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
- `test.yml`: pytest with coverage on pushes and PRs to `main`; updates the coverage badge gist on `main`.
- `pr-environment.yml`: for same-repo PRs, builds the image and deploys `TrackTimingStack-pr-<N>` with a public Function URL, comments the URL on the PR, and destroys the stack on close.
- `deploy.yml`: on push to `main`, builds the image (SHA tag + `prod-latest`) and runs `cdk deploy` for prod. It does not wait for `test.yml`.

**Configuration:** `app/config.py` exposes a module-level `settings` singleton and `get_settings()` for `Depends()`.

**HTTP client:** the FastAPI `lifespan` creates an `httpx.AsyncClient` (`max_connections=50`, 15 s timeout) on `app.state.http_client`. Routes get it via `Depends(get_http_client)`. Under Mangum the lifespan runs per invocation, so on Lambda the client is not reused across requests.

**Request flow (`/schedule/{event_id}`):**
1. `fetcher.fetch_initial_layout` POSTs to the Jaxon endpoint (refresh uses `fetch_refresh`).
2. `parser.parse_schedule` turns the HTML into `Session`/`Event` models.
3. `main.py` concurrently fetches start lists, result pages and live-heat pages, filling the predictor caches.
4. `predictor.predict_schedule` builds a `SchedulePrediction`.
5. Jinja2 renders `schedule.html`; HTMX polls `/schedule/{id}/refresh`, which returns `_schedule_body.html`.

**tracktiming.live API** (unversioned and undocumented, so parse defensively):
- `POST eventpage.php?EventId={id}` with form body `jxnfun=getInitialPageLayout&jxnr=1` (initial) or `jxnfun=refreshPage&...` (refresh); the response is JSON with a `jxnobj` array.
- The schedule HTML is either a top-level `id="scheduleview"` object or nested in `id="dynarea"` (the live API). The parser handles both.
- Status comes from the event's row buttons (no `disabled` class): `btn-success` means COMPLETED (href is the result page), `btn-primary` means UPCOMING (href is the start list), `btn-info` is the audit page, and `btn-danger` is the live timing page. Anything else is NOT_READY.
- The session summary looks like `"Schedule - Friday - 08:15"`; times are venue-local and naive.

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
| `/palmares/export` | CSV of one rider's (or team's) audit data; `audit_url` must start with `results/` |
| `/palmares/rename` | Rename a competition; requires `racer_name` cookie |
| `/palmares/remove` | Delete a competition's entries; requires `racer_name` cookie (403 otherwise) |
| `/defaults` | Built-in default durations |
| `/learned` | Learned duration averages |
| `/health` | Always 200; per-component `healthy`/`degraded` |

**Cookies:** `racer_name` (raw name, 1 year, HttpOnly, Secure, Lax); `use_learned` (`"true"` when on; off by default); `theme` (`light`/`dark`, set client-side, 1 year).

**In-memory caches** (`predictor.py`, keyed by `(competition_id, session_id, position)`, unbounded, per Lambda container): `_status_cache` (status transitions for wall-clock learning), `_observed_durations`, `_heat_counts`, `_live_heats`, `_generated_times`, `_start_list_riders`.

**Duration source priority** (`predictor.predict_session`):
1. Observed: result-page Finish Time + changeover (bunch races).
2. Generated: difference between an event's result-page Generated timestamp and the previous event's, kept if within 0.5×–2.0× of that event's expected duration. Generated marks an event's end, so the gap belongs to the later event. `predictor.generated_gap_duration` does this for both the app and `tools.extract_competition`.
3. Heat count: `heat_count × per_heat_duration + changeover`.
4. Fallback: if the `use_learned` cookie is on, the discipline-level learned average (`get_learned_duration`, ≥ `MIN_LEARNED_SAMPLES`); otherwise `DEFAULT_DURATIONS`.

The UI labels these as **obs.** (1–2), **N heats** (3) and **est.** (4).

**Live delay** (`predictor._compute_delay`): applied only while a session has both completed events and pending non-special events (a NOT_READY End of Session doesn't keep a finished session live). The same condition gates the active-event flag. It is clamped to [−30, +120] min and returns 0 once `actual_elapsed > total_est + 60 min`, so post-event views show scheduled times. "Now" comes from `clock.venue_now()`, naive to match the schedule. Upstream exposes no timezone, so the venue's UTC offset is inferred from the newest Generated timestamp in an in-progress session (`predictor.latest_live_generated_time`): Generated ≤ venue-local now, so (Generated − UTC now − 2 min skew allowance) rounded up to the whole hour is the offset while that result is under ~58 min old. Results older than that (a long break) give an offset an hour low, and half-hour zones aren't supported. With no live session, or an offset outside UTC−12..+14, it falls back to `VENUE_TZ`. Both schedule routes use it, and the "Last updated" label shows it (the refresh partial carries it in `#schedule-generated-at`).

**Learning** (`database.py`; DynamoDB when `DYNAMODB_TABLE` is set, otherwise SQLite):
- *Live app writes* go through `record_live_duration(..., source)`: `"observed"` from result-page Finish Times, `"wall_clock"` from the UPCOMING→COMPLETED fallback (capped at 3× static default). They're idempotent per `(competition, session, position)` and keep any existing record, except that an observed value replaces a wall-clock one. Loader records (no `source`) are never replaced. DynamoDB reuses the structured `OBS#` path with a `source` attribute; SQLite stores it in a `source` column.
- *Loader writes* go through `record_duration_structured()` (returns `RecordOutcome`: created/updated/unchanged/error), with classification, gender and per-heat duration. They're idempotent: SQLite uses `INSERT OR REPLACE`; DynamoDB uses an `OBS#<comp>#<sess>#<pos>` item as a commit marker written after the `AGGREGATE#...` updates, with delta correction on re-load.
- *Reads:* the app uses only `get_learned_duration(discipline)` (overrides first, then the average). `get_learned_duration_cascading(discipline, classification, gender)` exists and is tested, but nothing in the app calls it.
- The DynamoDB key layout (`AGGREGATE#` levels, `OVERRIDE#`, `OBS#`) is documented in the comment block near the top of the DynamoDB section in `database.py` (~line 165). SQLite tables are `event_durations` (with `_migrate_schema` adding columns to old DBs) and `discipline_overrides`.

**Disciplines:** two classifiers exist.
- `disciplines.detect_discipline` is an ordered keyword list (`DISCIPLINE_KEYWORDS`, more specific phrases first). The live app uses it; `disciplines.py` also holds `DEFAULT_DURATIONS`, `PER_HEAT_DURATIONS` and changeovers.
- `categorizer.categorize_event` is a bilingual strip-and-match parser. It extracts special event → omnium part → ride number → round → classification → gender → discipline, then maps pursuits to `pursuit_4k`/`3k`/`2k`, and returns `(EventCategory, unresolved_text)`. Only `tools/` use it.
- Individual pursuit distance: both classifiers' name-based guess is overridden by `disciplines.pursuit_discipline_from_urls` whenever the event has any URL, since upstream page names encode the distance (`W4044-IP-3000-Q-0-R.htm`). `parser.parse_schedule` and `tools.extract_competition` apply it. Names alone guess wrong for masters age groups, Junior Women, U17 Men and French names.

**Palmares** (`palmares.py`; DynamoDB when `PALMARES_TABLE` is set, otherwise SQLite `palmares_entries`):
- Collected automatically on schedule views when a racer is identified and matched to a timed event that has an audit URL.
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

- `app/`: application. `tools/`: CLI importers. `tests/`: pytest suite plus `fixtures/`. `cdk/`: infrastructure. `static/`: CSS.
- `specs/NNN-name/`: speckit feature artifacts (spec, plan, tasks, research, contracts). 001–005 are complete and historical; read them for rationale, not current behaviour.
- `.specify/`: speckit config. Only `memory/constitution.md` (project principles that govern design trade-offs) and `templates/overrides/` (project-specific plan and task rules) are committed. The rest of `.specify/` and the `/speckit.*` commands in `.claude/commands/` are installed locally and gitignored. The project uses Spec Kit **v0.2.1**; to install it, run `uvx --from git+https://github.com/github/spec-kit.git@v0.2.1 specify init --here --ai claude --script sh --force`. This keeps the existing constitution and overrides; check `git status` afterwards.
- `plans/`: pre-speckit design notes. `hosting-plan.md` is the current infrastructure reference; `data-pipeline*.md` and `dynamo-import-reload.md` are historical.
- `docs/`: `duration-data-import.md` (extract/load tooling reference), per-discipline duration rationale (`sprint-`, `mass-start-race-`, `timed-event-durations.md`), and historical HTML UI prototypes (`daisyui-*`, `*-mockup.html`).

## Conventions

- Feature branches: speckit features use `NNN-short-name`; other work uses a descriptive branch name.
- `/speckit.plan` runs `.specify/scripts/bash/update-agent-context.sh`, which appends "Active Technologies" and "Recent Changes" sections to this file. Delete them afterwards; this file stays current-state only.
