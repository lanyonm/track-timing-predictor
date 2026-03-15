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
```

Install dependencies: `pip install -r requirements.txt`

**Environment variables:**

| Variable | Default | Description |
|---|---|---|
| `DB_PATH` | `timings.db` | SQLite database path (local dev only) |
| `DYNAMODB_TABLE` | `""` | DynamoDB table name; enables DynamoDB backend when set |
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

**Request flow:**
1. `main.py` receives a tracktiming.live EventId via form or URL
2. `fetcher.py` POSTs to the Jaxon API to get schedule HTML
3. `parser.py` parses the HTML into `Session`/`Event` models
4. `main.py` concurrently fetches start lists, result pages, and live heat pages
5. `predictor.py` computes predicted start times and returns a `SchedulePrediction`
6. Jinja2 renders the schedule; HTMX polls `/schedule/{id}/refresh` every 30s for live updates

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
- DynamoDB single-table design: `AGGREGATE#<discipline>` items store running totals; `OVERRIDE#<discipline>` items store manual overrides
- SQLite tables: `event_durations` accumulates observations, `discipline_overrides` for manual overrides
- `get_learned_duration()` returns the average when ≥ `min_learned_samples` (3) rows exist
- Wall-clock learning (UPCOMING→COMPLETED transition) is a fallback, capped at 3× the static default to reject inflated values when start lists are published before the race

**Discipline detection** (`disciplines.py`):
- Keyword list in `DISCIPLINE_KEYWORDS` matched against lowercase event names
- Order matters — more specific phrases must appear before less specific ones (e.g. `"elite men individual pursuit"` before `"individual pursuit"`)

**Live delay adjustment** (`predictor.py::_compute_delay`):
- Only applied when session is in-progress (has both completed and pending events)
- Clamped to [−30, +120] minutes
- Returns 0 when `actual_elapsed > total_est + 60min` so post-event viewing shows scheduled times

## Key Patterns

- Tests use `conftest.py` to redirect SQLite to a temp file and force SQLite mode (prevents production DB contamination)
- `sample-event-output.json` is a captured Jaxon API response used as a test fixture
- `is_special` events (Break, End of Session, Medal Ceremonies) are excluded from `is_complete` checks and their COMPLETED status is deferred until the next event starts
- `end_of_session` discipline contributes 0 minutes to the cumulative timeline
- Templates: `schedule.html` is the full page; `_schedule_body.html` is the HTMX partial returned by `/schedule/{id}/refresh`

## Active Technologies
- Python 3.11+ + FastAPI, Pydantic, httpx, Jinja2, BeautifulSoup (all existing) (001-racer-schedule-lookup)
- In-memory caches (existing pattern) — no database changes (001-racer-schedule-lookup)

## Recent Changes
- 001-racer-schedule-lookup: Added Python 3.11+ + FastAPI, Pydantic, httpx, Jinja2, BeautifulSoup (all existing)
