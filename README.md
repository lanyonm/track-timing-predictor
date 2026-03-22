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
5. Falls back to built-in defaults or learned averages when no start list is available
6. Computes a predicted start time for every event in the session
7. During live events, adjusts predictions based on how far ahead or behind schedule the session is running
8. When results are posted, refines completed-event durations using the race's actual Finish Time
9. Auto-refreshes every 30 seconds so predictions stay current throughout the day

The duration column in the UI shows the source of each estimate: **obs.** (from a result-page Finish Time), **N heats** (from a start list), or **est.** (default/learned fallback).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running

```bash
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000), enter a tracktiming.live Event ID, and click **Load Schedule**.

Event IDs can be found in the URL of any event on tracktiming.live:
`https://tracktiming.live/eventpage.php?EventId=26008` → ID is `26008`

## Pages

| URL | Description |
|-----|-------------|
| `/` | Enter an Event ID |
| `/schedule/{id}` | Predicted schedule for an event |
| `/learned` | View accumulated duration observations |

## Project layout

```
app/
├── main.py          # FastAPI routes
├── fetcher.py       # HTTP client for tracktiming.live API
├── parser.py        # HTML parsing of Jaxon AJAX responses
├── predictor.py     # Prediction algorithm and live delay detection
├── disciplines.py   # Discipline detection and default duration estimates
├── categorizer.py   # Compositional event name parser (bilingual)
├── database.py      # SQLite/DynamoDB storage for learned durations
├── models.py        # Pydantic data models
└── templates/       # Jinja2 HTML templates
tools/
├── extract_competition.py  # CLI: competition ID → JSON report
└── load_durations.py       # CLI: JSON reports → learning database
data/
└── competitions/    # Extracted JSON reports (gitignored)
static/
└── style.css
```

## Taxonomy

The app organises track cycling data in a four-level hierarchy:

| Level | Term | Definition |
|-------|------|------------|
| 1 | **Competition** | A tracktiming.live event identified by an integer ID (the external API calls this `EventId`) |
| 2 | **Session** | A day's racing block within a competition (e.g. "Friday 08:15") |
| 3 | **Event** | An individual race/discipline entry within a session (e.g. "Elite Men Sprint Qualifying") |
| 4 | **Heat** | One sequential ride within a multi-heat event (e.g. Heat 3 of 8 in sprint qualifying) |

## How durations are estimated

Each event's slot duration is determined by the first available source:

1. **Observed** — once results are posted, the race's `Finish Time` (actual race duration) plus a discipline-specific changeover allowance is used. Shown as **obs.** in the UI.
2. **Heat count** — on page load, start list pages are fetched concurrently for every event. The number of heats × a per-heat duration constant gives the slot estimate. Shown as **N heats** in the UI.
3. **Learned average** — after three or more observations of the same discipline, the SQLite database supplies an average.
4. **Default** — built-in estimates in `DEFAULT_DURATIONS` inside [app/disciplines.py](app/disciplines.py). Shown as **est.** in the UI.

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

The loader validates each observation against [0.5x, 2.0x] bounds of the expected duration (heat-count-derived when available, static default otherwise) and writes to the learning database with structured category info. On first run against an existing database with duplicate rows from live learning, it prompts to deduplicate (or use `--force` to skip the prompt). Re-loading corrected data overwrites previous values. This enables **cascading granularity fallback**: the app first checks for a category-specific average (e.g. elite men sprint), then progressively broader averages (all men sprint, then all sprint), before falling back to the static default.

See [docs/duration-data-import.md](docs/duration-data-import.md) for full documentation including the categorization rules, output format, database schema changes, and reference competitions.

## Configuration

| Environment variable | Default | Description |
|----------------------|---------|-------------|
| `DB_PATH` | `timings.db` | Path to the SQLite database |
