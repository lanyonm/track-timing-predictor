# Track Timing Predictor

[![Tests](https://github.com/lanyonm/track-timing-predictor/actions/workflows/test.yml/badge.svg)](https://github.com/lanyonm/track-timing-predictor/actions/workflows/test.yml)
![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/lanyonm/4425fffc5da8c86bbd7f97c14b8f42f9/raw/ttp-coverage-badge.json)

A web app that predicts per-event start times for track cycling events hosted on [tracktiming.live](https://tracktiming.live/).

## How it works

tracktiming.live publishes event schedules with a session-level start time (e.g. "Friday 08:15") but no per-event timestamps. This app:

1. Fetches the schedule for a given event ID from the tracktiming.live API
2. Detects the discipline for each event (sprint qualifying, pursuit, scratch race, etc.)
3. Fetches each event's start list to count how many heats are scheduled
4. Computes predicted duration as **heat count × per-heat time** (e.g. 5 sprint qualifying rides × 1.5 min = 7.5 min)
5. Falls back to built-in defaults (or, if you opt in, learned averages) when no start list is available
6. Computes a predicted start time for every event in the session
7. During live events, adjusts predictions based on how far ahead or behind schedule the session is running
8. When results are posted, refines completed-event durations using the race's actual Finish Time, or the gap between consecutive result-page timestamps
9. Auto-refreshes every 30 seconds so predictions stay current throughout the day

The duration column in the UI shows the source of each estimate: **obs.** (from posted results), **N heats** (from a start list), or **est.** (default/learned fallback).

## Taxonomy

The app organises track cycling data in a four-level hierarchy:

| Level | Term | Definition |
|-------|------|------------|
| 1 | **Competition** | A tracktiming.live event identified by an integer ID (the external API calls this `EventId`) |
| 2 | **Session** | A day's racing block within a competition (e.g. "Friday 08:15") |
| 3 | **Event** | An individual race/discipline entry within a session (e.g. "Elite Men Sprint Qualifying") |
| 4 | **Heat** | One sequential ride within a multi-heat event (e.g. Heat 3 of 8 in sprint qualifying) |

## Racer highlight

Enter your name in the text field on the schedule page to highlight every event you're racing in. The app fuzzy-matches the name you enter against start lists fetched from tracktiming.live.

When a match is found:

- **Matched rows** are visually highlighted in the schedule table
- A **summary banner** shows how many events matched (e.g. "Found 3 events for 'Jane Smith'")
- Your **next upcoming race** is called out with its predicted start time (or "Racing now" if active)
- For multi-heat events, your specific **heat number** and **predicted heat start time** are shown
- Sessions containing your pending events **auto-expand**; completed sessions collapse

**How names are resolved:**

1. **URL parameter** (`?r=`) — a URL-safe Base64-encoded name, useful for shareable links and bookmarks
2. **Cookie** — if no `r=` param is present, the app falls back to a `racer_name` cookie (set when you submit the form, persists for one year)

The `r=` parameter is passed through to the HTMX refresh endpoint so highlighting persists across auto-refreshes.

**If start lists aren't published yet**, the app shows a message indicating how many events are still missing start lists. Check back closer to the competition start.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime deps + test tooling
```

Requires Python 3.11.

## Running

```bash
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000), enter a tracktiming.live Event ID, and click **Load Schedule**.

Event IDs can be found in the URL of any event on tracktiming.live:
`https://tracktiming.live/eventpage.php?EventId=26008` → ID is `26008`

## Testing

```bash
pytest
```

Tests use a temporary SQLite database and captured fixtures in `tests/fixtures/`; DynamoDB tests use `moto`. No network or AWS access is needed.

## Pages

| URL | Description |
|-----|-------------|
| `/` | Enter an Event ID |
| `/schedule/{id}` | Predicted schedule for an event (`?r=` highlights a racer) |
| `/palmares` | A racer's timed-event results across competitions, with per-event CSV export |
| `/defaults` | Built-in default durations |
| `/learned` | Learned duration averages |
| `/health` | Health check (JSON) |

## Palmares

When a racer is identified on a schedule page and matched to a timed event (pursuit, team pursuit, team sprint or time trial) that has published audit results, the event is saved to that racer's palmares. The palmares page groups these by competition. It offers a shareable link, CSV export of the racer's (or team's) lap and sector splits, and, for the racer whose name is in the cookie, renaming or removing a competition.

## Project layout

```
app/
├── main.py          # FastAPI routes (Mangum handler for Lambda)
├── config.py        # pydantic-settings configuration
├── fetcher.py       # HTTP client for tracktiming.live API
├── parser.py        # HTML parsing of Jaxon AJAX responses
├── predictor.py     # Prediction algorithm and live delay detection
├── disciplines.py   # Discipline detection and default duration estimates
├── categorizer.py   # Compositional event name parser (bilingual, used by tools/)
├── database.py      # SQLite/DynamoDB storage for learned durations
├── palmares.py      # SQLite/DynamoDB storage for racer palmares
├── audit_parser.py  # Audit result parsing and CSV formatting
├── models.py        # Pydantic data models
└── templates/       # Jinja2 HTML templates (DaisyUI + HTMX)
tools/
├── extract_competition.py  # CLI: competition ID → JSON report
└── load_durations.py       # CLI: JSON reports → learning database
data/
└── competitions/    # Extracted JSON reports (gitignored)
static/
└── style.css
tests/               # pytest suite and captured fixtures
cdk/                 # AWS CDK infrastructure (Lambda, DynamoDB, CloudFront)
specs/               # Feature specs (speckit), historical
```

## How durations are estimated

Each event's slot duration is determined by the first available source:

1. **Observed** — once results are posted, the race's `Finish Time` (actual race duration) plus a discipline-specific changeover allowance is used. Shown as **obs.** in the UI.
2. **Generated timestamps** — for completed events without a Finish Time, the gap between consecutive result pages' `Generated` timestamps (kept only if within 0.5×–2.0× of the expected duration). Also shown as **obs.**
3. **Heat count** — on page load, start list pages are fetched concurrently for every event. The number of heats × a per-heat duration constant gives the slot estimate. Shown as **N heats** in the UI.
4. **Default** — built-in estimates in `DEFAULT_DURATIONS` inside [app/disciplines.py](app/disciplines.py), or, if you turn on "use learned durations" on the schedule page, the learned average for the discipline once it has at least three observations. Shown as **est.** in the UI.

Learned averages are stored in SQLite locally and DynamoDB in production.

Per-heat constants (`PER_HEAT_DURATIONS`) and overall fallback defaults (`DEFAULT_DURATIONS`) can both be adjusted in [app/disciplines.py](app/disciplines.py).

## Importing historical duration data

The built-in defaults work out of the box, but the prediction engine improves significantly when seeded with real data from past competitions. A pair of CLI tools extract historical durations from tracktiming.live and load them into the learning database:

```bash
# Extract a competition's data into a JSON report
python -m tools.extract_competition 26008

# Load the report into the learning database
python -m tools.load_durations data/competitions/26008.json
```

The extraction script decomposes event names (e.g. `"Elite/Junior Women Scratch Race / Omni I"`) into structured categories — discipline, classification, gender, round — using a bilingual parser that handles both English and French naming. Durations are computed from result-page finish times, consecutive generated timestamps, or start-list heat counts (same priority as the live app).

The loader validates each observation against [0.5x, 2.0x] bounds of the expected duration (heat-count-derived when available, static default otherwise) and writes to the learning database with structured category info. On first run against an existing database with duplicate rows from live learning, it prompts to deduplicate (or use `--force` to skip the prompt). Re-loading corrected data overwrites previous values. The database stores averages at four levels of granularity (discipline + classification + gender down to discipline only), and `get_learned_duration_cascading()` can query them. The live app currently reads only the discipline-level average.

See [docs/duration-data-import.md](docs/duration-data-import.md) for full documentation including the categorization rules, output format, database schema changes, and reference competitions.

## Configuration

| Environment variable | Default | Description |
|----------------------|---------|-------------|
| `TRACKTIMING_BASE_URL` | `https://tracktiming.live` | Upstream base URL |
| `DB_PATH` | `timings.db` | Path to the SQLite database (when `DYNAMODB_TABLE` is unset) |
| `DYNAMODB_TABLE` | *(empty)* | DynamoDB table for learned durations; enables the DynamoDB backend |
| `PALMARES_TABLE` | *(empty)* | DynamoDB table for palmares; enables the DynamoDB backend |
| `AWS_REGION` | `us-east-1` | DynamoDB region |
| `REFRESH_INTERVAL_SECONDS` | `30` | Live refresh interval |
| `MIN_LEARNED_SAMPLES` | `3` | Observations required before a learned average is used |

## Deployment

Production runs on AWS Lambda behind CloudFront at [ttp.lanyonm.org](https://ttp.lanyonm.org), deployed by GitHub Actions with AWS CDK on every push to `main`. Pull requests from branches in this repo get an ephemeral environment. See [plans/hosting-plan.md](plans/hosting-plan.md).
