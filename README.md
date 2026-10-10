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
8. When results are posted, refines completed-event durations using the race's actual Finish Time, or the gap between consecutive result-page timestamps, and calibrates the competition's bunch-race changeover from them
9. Auto-refreshes every 30 seconds so predictions stay current throughout the day

The duration column in the UI shows the source of each estimate: **obs.** (from posted results), **N heats** (from a start list), **N km** (a points or scratch race's start-list distance), **N deciders** (sprint Ride 3 after Ride 2), **0–N deciders est.** (sprint Ride 3 before Ride 2), **~N heats est.** (heats estimated from a round name or the Rider List), **N podiums** (medal ceremonies), or **est.** (default/learned fallback). Hover the label for how the duration was worked out.

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

**If start lists aren't published yet**, the app falls back to the competition's Rider List when it publishes one (some competitions only post start lists after each session's sign-on). The Rider List gives each rider's category and entered events, so events in your age band, gender and entered disciplines are highlighted without heat information:

- **Entered**: qualifying rounds, and events that are the only one for your band and discipline
- **If advancing**: later rounds you only ride if you get through, shown without the row highlight
- When you're in one of several qualifiers (e.g. Scratch Race Qualifier 1 and 2), both are marked Entered and your next race gives the first one's time as a "be ready by" time

A banner names the category and event codes used (e.g. "M6064: S, TS, TT") so a wrong-person match is obvious. Each event switches to start-list matching once its start list is posted. Only masters age-band categories (`M6064`, `W4549`, `M90`) are matched for now. Without a Rider List, or for other categories, the app shows how many events are still missing start lists; check back closer to the competition start.

## Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements-dev.txt   # runtime deps + test and lint tooling
```

Requires Python 3.13. `requirements*.txt` are hashed locks generated from `pyproject.toml`; see `CLAUDE.md` for how to regenerate them.

## Running

```bash
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000), enter a tracktiming.live Event ID, and click **Load Schedule**.

Event IDs can be found in the URL of any event on tracktiming.live:
`https://tracktiming.live/eventpage.php?EventId=26008` → ID is `26008`

### Frontend assets

The pages load no third-party CSS or JS. `static/app.css` (Tailwind 3 + DaisyUI 4, compiled from the classes used in `app/templates/` and `app/`) and `static/htmx.min.js` are built from `frontend/` and committed. After adding or changing a Tailwind/DaisyUI class, rebuild them (Node required; versions are pinned in `frontend/package-lock.json`):

```bash
cd frontend && npm ci && npm run build
```

CI fails if the committed files don't match a fresh build.

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
| `/defaults` | Built-in default and per-heat durations, the rules that replace them (distance, changeover, deciders, ceremonies), the bunch-race pace table and the masters per-heat overrides |
| `/learned` | Learned duration averages |
| `/health` | Health check (JSON) |

## Palmares

When a racer is identified on a schedule page and matched on a start list to a timed event (pursuit, team pursuit, team sprint or time trial) that has published audit results, the event is saved to that racer's palmares. The palmares page groups these by competition. It offers a shareable link, CSV export of the racer's (or team's) lap and sector splits, and, for the racer whose name is in the cookie, renaming or removing a competition.

## Project layout

```
app/
├── main.py          # FastAPI routes (Mangum handler for Lambda)
├── config.py        # pydantic-settings configuration
├── fetcher.py       # HTTP client for tracktiming.live API
├── parser.py        # HTML parsing of Jaxon AJAX responses
├── predictor.py     # Prediction algorithm and live delay detection
├── rider_list.py    # Rider List fallback matching (age band, gender, event codes)
├── ceremonies.py    # Medal ceremony podium forecasting
├── disciplines.py   # Discipline detection and default duration estimates
├── categorizer.py   # Compositional event name parser (bilingual, used by tools/)
├── database.py      # SQLite/DynamoDB storage for learned durations
├── palmares.py      # SQLite/DynamoDB storage for racer palmares
├── aws_errors.py    # Shared botocore exception handling (optional dependency)
├── audit_parser.py  # Audit result parsing and CSV formatting
├── models.py        # Pydantic data models
└── templates/       # Jinja2 HTML templates (DaisyUI + HTMX)
tools/
├── extract_competition.py  # CLI: competition ID → JSON report
├── load_durations.py       # CLI: JSON reports → learning database
└── rebuild_aggregates.py   # CLI: recompute DynamoDB aggregates from OBS# items
data/
└── competitions/    # Extracted JSON reports (gitignored)
static/
├── app.css          # Built Tailwind + DaisyUI (from frontend/, committed)
├── app.js           # Page behaviour (hand-written; the CSP blocks inline scripts)
├── htmx.min.js      # Vendored htmx (from frontend/, committed)
└── style.css        # App-specific overrides
frontend/            # npm build for static/app.css and static/htmx.min.js
tests/               # pytest suite and captured fixtures
cdk/                 # AWS CDK infrastructure (Lambda, DynamoDB, CloudFront)
specs/               # Feature specs (speckit), historical
```

## How durations are estimated

Each event's slot duration is determined by the first available source:

1. **Observed** — once results are posted, a bunch race's `Finish Time` (actual race duration) plus the competition's changeover (below). Shown as **obs.** in the UI.
2. **Generated timestamps** — for completed events without a Finish Time, the gap between its result page's `Generated` timestamp and the previous event's (kept only if within 0.5×–2.0× of the expected duration). Also shown as **obs.**
3. **Start list** — on page load, start list pages are fetched concurrently for every event. Shown as **N heats** in the UI where a heat count is used.
   - Heat count × a per-heat duration constant. Masters events with an age band in the name (`70-74 Men`) use their own value where the data differs: 2 km pursuits under 70 (4.5 min), 500 m time trials 70+ (2.75) and team sprints (3.5). Medal finals label their heats `Final 3-4`/`Final 1-2` (sprints) or `For Bronze`/`For Gold` (pursuits, team events).
   - Points and scratch races use their distance from the start list title (`- 10km - 40 Laps`) at 46 km/h, plus changeover. Masters races with an age band go at their group's pace, set by the youngest age in the band: men under 70 48 km/h, 70-74 41.5, 75+ 36; women under 50 43.5, 50+ 41. Shown as **N km**; the tooltip names the pace.
   - A best-of-3 sprint `Ride 3` is ridden only by pairs tied 1–1: 4.25 min per decider once Ride 2's results show how many (shown as **N deciders**), else 12% of the pairs (shown as **0–N deciders est.**).
   - Before the start list is posted, the round name gives the heats where it's fixed: sprint 1/2 Finals and Finals count 2 pairs and 1/4 Finals 4; pursuit, team pursuit and team sprint finals after a qualifying round 2 heats (bronze and gold); keirin placement finals (1-6, 7-12) 1 heat and 1/2 Finals 2. Masters sprint and pursuit qualifying rounds and time trials are sized from the Rider List: one heat per sprinter, one per two pursuiters or time triallists entered in the age band. Shown as **~N heats est.**
4. **Default** — built-in estimates in `DEFAULT_DURATIONS` inside [app/disciplines.py](app/disciplines.py), or, if you turn on "use learned durations" on the schedule page, the learned average for the discipline once it has at least three observations. Shown as **est.** in the UI.

**Medal ceremonies** at masters competitions take 13 min plus 3.3 min per podium. The podium count is forecast from the finals since the previous ceremony, with combined-age races split by category. Shown as **N podiums** in the UI. See [docs/medal-ceremony-durations.md](docs/medal-ceremony-durations.md).

**Bunch-race changeover** (the time between one bunch race's result and the next race's start) is calibrated per competition: the median of Generated gap minus Finish Time over back-to-back bunch races, once there are three. Until then it's 3 min. It came out at ~8 min at the 2026 masters worlds and 2.5–3.4 min at national events. See [docs/mass-start-race-durations.md](docs/mass-start-race-durations.md).

Rationale and data for each constant: [sprint](docs/sprint-durations.md), [mass start](docs/mass-start-race-durations.md), [timed events](docs/timed-event-durations.md) and [medal ceremonies](docs/medal-ceremony-durations.md).

Learned averages are stored in SQLite locally and DynamoDB in production.

Per-heat constants (`PER_HEAT_DURATIONS`, with masters overrides in `MASTERS_PER_HEAT_DURATIONS`), bunch-race paces (`BUNCH_RACE_KMH`, `MASTERS_BUNCH_RACE_KMH`) and overall fallback defaults (`DEFAULT_DURATIONS`) are all set in [app/disciplines.py](app/disciplines.py).

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

If the DynamoDB aggregates ever drift from the stored observations, `python -m tools.rebuild_aggregates` recomputes them (dry run by default, `--apply` to write).

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
| `VENUE_TZ` | `America/Toronto` | Fallback venue timezone; during a live session the offset is inferred from result-page timestamps |
| `PUBLIC_BASE_URL` | *(empty)* | Origin for the palmares share link; empty uses the request host |

## Deployment

Production runs on AWS Lambda behind CloudFront at [ttp.lanyonm.org](https://ttp.lanyonm.org), deployed by GitHub Actions with AWS CDK after the tests pass on each push to `main`. Pull requests from branches in this repo get an ephemeral environment. See [plans/hosting-plan.md](plans/hosting-plan.md).
