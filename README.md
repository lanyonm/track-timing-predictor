# Track Timing Predictor

A web app that predicts per-event start times for track cycling events hosted on [tracktiming.live](https://tracktiming.live/).

## How it works

tracktiming.live publishes event schedules with a session-level start time (e.g. "Friday 08:15") but no per-event timestamps. This app:

1. Fetches the schedule for a given event ID from the tracktiming.live API
2. Detects the discipline for each event (sprint qualifying, pursuit, scratch race, etc.)
3. Estimates each event's duration using built-in defaults or learned averages from past events
4. Computes a predicted start time for every event in the session
5. During live events, adjusts predictions based on how far ahead or behind schedule the session is running
6. Auto-refreshes every 30 seconds so predictions stay current throughout the day

Over time the app learns more accurate duration estimates by observing when events transition from "start list ready" to "results posted".

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
├── database.py      # SQLite storage for learned durations
├── models.py        # Pydantic data models
└── templates/       # Jinja2 HTML templates
static/
└── style.css
```

## Tuning duration estimates

Default durations live in [app/disciplines.py](app/disciplines.py) in the `DEFAULT_DURATIONS` dict. Values are in minutes and represent the full time for one schedule row (one category's event), including warm-up laps, the race itself, and changeover time.

The learning mechanism will refine these automatically after you use the app across a few live events. Three or more observations for a discipline are required before the learned average overrides the default.

## Configuration

| Environment variable | Default | Description |
|----------------------|---------|-------------|
| `DB_PATH` | `timings.db` | Path to the SQLite database |
