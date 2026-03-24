# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app
source .venv/bin/activate
uvicorn app.main:app --reload

# Run all tests
pytest

# Run a single test file
pytest tests/test_predictor.py

# Run a single test class or function
pytest tests/test_predictor.py::TestComputeDelay
pytest tests/test_predictor.py::TestComputeDelay::test_positive_delay_when_behind

# Extract competition data from tracktiming.live
python -m tools.extract_competition 26008

# Load extracted data into the learning database
python -m tools.load_durations data/competitions/26008.json

# Batch extract + load
for id in 25022 25026 25027 25028 25031 26001 26002 26008 26009 26010; do
    python -m tools.extract_competition "$id"
done
python -m tools.load_durations data/competitions/*.json
```

Install dependencies: `pip install -r requirements.txt`

**Environment variables:**

| Variable | Default | Description |
|---|---|---|
| `DB_PATH` | `timings.db` | SQLite database path (local dev only) |
| `DYNAMODB_TABLE` | `""` | DynamoDB table name; enables DynamoDB backend when set |
| `PALMARES_TABLE` | `""` | DynamoDB table name for palmares data; enables DynamoDB palmares backend when set |
| `AWS_REGION` | `us-east-1` | AWS region for DynamoDB client |

## Taxonomy

| Level | Term | Definition |
|---|---|---|
| 1 | **Competition** | A tracktiming.live competition identified by an integer `competition_id` (the external API calls this `EventId`) |
| 2 | **Session** | A day's racing block within a competition (`Session` model) |
| 3 | **Event** | An individual race/discipline entry within a session (`Event` model) |
| 4 | **Heat** | One sequential ride within a multi-heat event (`heat_count`, `active_heat`) |

**Note:** Route URLs and HTML form fields still use `event_id` (bound to `/schedule/{event_id}`) to avoid breaking bookmarks and match the upstream tracktiming.live `EventId` parameter. Python code and templates use `competition_id`.

## Architecture

The app predicts per-event start times for track cycling competitions fetched from tracktiming.live.

**Deployment:** Lambda + Function URL (Docker image from ECR). Mangum adapts FastAPI to the Lambda handler. Local dev uses uvicorn. See `plans/hosting-plan.md` for full infrastructure details. **All routes must be GET** — CloudFront OAC with Lambda Function URLs doesn't support POST request bodies (SigV4 payload signature mismatch causes 403s).

**Configuration:** `app/config.py` uses `pydantic_settings.BaseSettings` for validated configuration with automatic environment variable loading. A `get_settings()` function provides the settings instance via FastAPI `Depends()`.

**HTTP client:** A shared `httpx.AsyncClient` is created in the FastAPI lifespan (with `max_connections=50`) and stored on `app.state.http_client`. Route handlers receive it via `Depends(get_http_client)`. Fetcher functions accept the client and base URL as parameters.

**Request flow:**
1. `main.py` receives a tracktiming.live EventId via form or URL
2. `fetcher.py` POSTs to the Jaxon API using the shared `httpx.AsyncClient` to get schedule HTML
3. `parser.py` parses the HTML into `Session`/`Event` models
4. `main.py` concurrently fetches start lists, result pages, and live heat pages
5. `predictor.py` computes predicted start times and returns a `SchedulePrediction`
6. Jinja2 renders the schedule; HTMX polls `/schedule/{id}/refresh` every 30s for live updates

**Routes:**

| Method | Path | Description |
|---|---|---|
| GET | `/` | Landing page with EventId form (works without JavaScript) |
| GET | `/schedule` | No-JS fallback redirect; `?event_id=X` → `/schedule/X` (303) |
| GET | `/schedule/{event_id}` | Schedule view; optional `?r=` Base64-encoded racer name |
| GET | `/schedule/{event_id}/refresh` | HTMX partial for live polling; optional `?r=` param |
| GET | `/settings/racer-name` | Set/clear racer name cookie; `?event_id=&name=` |
| GET | `/settings/use-learned` | Toggle learned-durations cookie; `?event_id=&use_learned=on\|off` |
| GET | `/palmares` | Palmares profile page; resolves racer from `r=` param, cookie, or `name` form submission |
| GET | `/palmares/export` | CSV export of individual audit result data; requires `audit_url` and racer identity; optional `team_name` for team events |
| GET | `/palmares/rename` | Update competition name; cookie-only auth; accepts `competition_id` and `name` |
| GET | `/palmares/remove` | Delete all palmares entries for a competition; cookie-only auth (403 for shared links) |
| GET | `/defaults` | Display built-in default durations |
| GET | `/learned` | Display learned duration database |
| GET | `/health` | Health check; returns HTTP 200 with per-component status (healthy/degraded) |

**In-memory caches in `predictor.py`** (keyed by `(competition_id, session_id, position)`):
- `_status_cache` — tracks event status transitions for the learning fallback
- `_observed_durations` — Finish Time + changeover from result pages (most accurate)
- `_heat_counts` — heat counts parsed from start-list pages
- `_live_heats` — current heat number from live results pages
- `_generated_times` — Generated timestamps from result pages

**Note:** On Lambda, these caches persist within a warm execution environment but reset on cold starts and are not shared across concurrent invocations. This may cause more frequent re-fetching and slightly less accurate predictions during cold starts.

**Duration source priority** (highest to lowest accuracy):
1. Observed: result-page Finish Time + changeover (bunch races)
2. Generated: difference between consecutive result-page Generated timestamps
3. Heat count: `heat_count × per_heat_duration + changeover`
4. Default: learned average from database (if ≥3 samples) or `DEFAULT_DURATIONS` fallback

**Learning mechanism** (`database.py`):
- Dual backend: DynamoDB in production (`DYNAMODB_TABLE` set), SQLite for local dev
- DynamoDB single-table design (pk-only, no sort key):
  - `AGGREGATE#<disc>` — broadest running total (Level 1, existing)
  - `AGGREGATE#<disc>##<gender>` — discipline+gender (Level 2, double-hash separates from Level 3)
  - `AGGREGATE#<disc>#<class>` — discipline+classification (Level 3)
  - `AGGREGATE#<disc>#<class>#<gender>` — most specific (Level 4)
  - `OVERRIDE#<disc>` (through `OVERRIDE#<disc>#<class>#<gender>`) — manual overrides at each level
  - `OBS#<comp_id>#<sess_id>#<pos>` — observation items for idempotent upsert; stores field values to detect corrections on re-load
- SQLite tables: `event_durations` accumulates observations (with `classification`, `gender`, `per_heat_duration_minutes` columns), `discipline_overrides` for manual overrides
- `get_learned_duration()` returns the average when ≥ `min_learned_samples` (3) rows exist
- `get_learned_duration_cascading(discipline, classification, gender)` queries 4 specificity levels: discipline+classification+gender → discipline+classification → discipline+gender → discipline → static default
- `record_duration_structured()` returns `RecordOutcome` (`"created"`, `"updated"`, `"unchanged"`, `"error"`); DynamoDB path uses delta-based aggregate correction when re-loaded data differs from existing OBS# item; SQLite path uses `INSERT OR REPLACE`
- Wall-clock learning (UPCOMING→COMPLETED transition) is a fallback, capped at 3× the static default to reject inflated values when start lists are published before the race

**Palmares module** (`palmares.py`):
- Dual SQLite/DynamoDB backend for racer achievement storage (separate table from learning durations)
- DynamoDB pk+sk design: `RACER#{name}` partition key, `COMP#{id}#S#{sid}#E#{pos}` sort key
- SQLite table `palmares_entries` with UNIQUE constraint on `(racer_name, competition_id, session_id, event_position)`
- Public API: `save_palmares_entries()`, `get_palmares()`, `count_competition_palmares()`, `delete_competition_palmares()`
- Auto-collected during schedule views for identified racers with matched **timed events** that have audit URLs
- Competition date derived from earliest Generated timestamp on result pages (not `datetime.now()`)
- **Timed events** are disciplines that produce per-lap/sector audit data: individual pursuits (`pursuit_4k`, `pursuit_3k`, `pursuit_2k`), team events (`team_pursuit`, `team_sprint`), and time trials (`time_trial_500`, `time_trial_750`, `time_trial_kilo`, `time_trial_generic`). Defined in `_TIMED_DISCIPLINES` in `main.py`. Team event start lists pack team name + riders in `<h4>` with `<br/>` separators — `_extract_names_from_h4` in `parser.py` splits these to extract individual riders. Team name stored per entry (`team_name` field) for CSV export since audit pages use team names.

**Audit parser** (`audit_parser.py`):
- Parses tracktiming.live audit result HTML (`-AUDIT-R.htm`) for CSV export
- Extracts rider names from `<p>` elements (strips bib prefix), heat assignments from `<h3>` headings
- `filter_rider_data()` uses `normalize_rider_name()` for name matching
- `format_csv()` outputs Heat, Dist, Time, Rank, Lap, Lap_Rank, Sect, Sect_Rank columns

**Discipline detection** (`disciplines.py`):
- Keyword list in `DISCIPLINE_KEYWORDS` matched against lowercase event names
- Order matters — more specific phrases must appear before less specific ones (e.g. `"elite men individual pursuit"` before `"individual pursuit"`)

**Event name categorizer** (`categorizer.py`):
- Compositional strip-and-match parser extracting: special events, omnium part, ride number, round, classification (age/license/compound/para), gender (English + French), discipline (bilingual keyword table)
- Post-extraction mapping resolves distance-variant discipline keys (e.g., pursuit → pursuit_4k/3k/2k based on classification + gender)
- Returns `(EventCategory, unresolved_text)` tuple

**CLI import tools** (`tools/` package, invoked via `python -m`):
- `python -m tools.extract_competition <competition_id>` — fetches schedule/result/start-list pages, extracts durations, writes JSON to `data/competitions/<id>.json`
- `python -m tools.load_durations <file>...` — reads JSON reports, validates duration bounds (0.5×–2.0× static default), writes to learning DB with structured categories
- `data/competitions/` output directory is gitignored

**Live delay adjustment** (`predictor.py::_compute_delay`):
- Only applied when session is in-progress (has both completed and pending events)
- Clamped to [−30, +120] minutes
- Returns 0 when `actual_elapsed > total_est + 60min` so post-event viewing shows scheduled times

## Key Patterns

- Tests use `conftest.py` to redirect SQLite to a temp file and force SQLite mode (prevents production DB contamination)
- `sample-event-output.json` is a captured Jaxon API response used as a test fixture
- `is_special` events (Break, End of Session, Medal Ceremonies, Warm-up) are excluded from `is_complete` checks and their COMPLETED status is deferred until the next event starts
- `end_of_session` discipline contributes 0 minutes to the cumulative timeline
- Templates: `schedule.html` is the full page; `_schedule_body.html` is the HTMX partial returned by `/schedule/{id}/refresh`
- Categorizer extraction order: special events → omnium part → ride number → round → classification → gender → discipline (most specific patterns matched first within each step)
- Extraction test fixtures in `tests/fixtures/`: captured Jaxon schedule responses, result page HTML (bunch race with Finish Time, non-bunch with Generated timestamp), start-list HTML with heats
- DynamoDB structured writes: aggregates updated BEFORE OBS# item written — ensures partial failures are retryable on next load (OBS# acts as the commit marker). Correction path computes deltas between old and new aggregate key sets (removed/added/shared) before overwriting the OBS# item
- SQLite schema migration (`_migrate_schema`) adds `classification`, `gender`, `per_heat_duration_minutes` columns to existing databases via `ALTER TABLE ADD COLUMN`

## Active Technologies
- Python 3.11+ + FastAPI, Pydantic, httpx, Jinja2, BeautifulSoup (all existing) (001-racer-schedule-lookup)
- In-memory caches (existing pattern) — no database changes (001-racer-schedule-lookup)
- Python 3.11+ (same as existing app) + httpx (HTTP client, existing), beautifulsoup4 (HTML parsing, existing), boto3 (DynamoDB, existing), argparse (CLI, stdlib) (002-duration-data-import)
- SQLite (local dev) + DynamoDB (production) — extended schema with classification + gender columns; JSON files for intermediate output (002-duration-data-import)
- Python 3.11+ + FastAPI, httpx, Pydantic, pydantic-settings (new), Jinja2, BeautifulSoup, boto3, Mangum (003-constitution-compliance)
- SQLite (local dev) / DynamoDB (production) — no schema changes (003-constitution-compliance)
- Python 3.11+ + FastAPI, httpx, Pydantic, pydantic-settings, Jinja2, BeautifulSoup, boto3 (all existing — no new dependencies) (004-racer-palmares)
- SQLite (local dev) + DynamoDB (production) — NEW separate table for palmares data (004-racer-palmares)

## Recent Changes
- 001-racer-schedule-lookup: Added Python 3.11+ + FastAPI, Pydantic, httpx, Jinja2, BeautifulSoup (all existing)
- 002-duration-data-import: Added Python 3.11+ (same as existing app) + httpx (HTTP client, existing), beautifulsoup4 (HTML parsing, existing), boto3 (DynamoDB, existing), argparse (CLI, stdlib)
- 004-racer-palmares: Added palmares (achievements) feature — auto-collects audit result links during schedule views, dedicated profile page with card layout, shareable links, per-event CSV export, per-competition removal. New modules: `palmares.py`, `audit_parser.py`, `palmares.html`. New DynamoDB table with pk+sk design.
