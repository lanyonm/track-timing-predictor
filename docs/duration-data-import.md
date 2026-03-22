# Duration Data Import

The duration data import tools extract historical competition data from [tracktiming.live](https://tracktiming.live/) and load it into the app's learning database. This seeds the prediction engine with data-driven duration averages, replacing reliance on hand-tuned defaults.

## Overview

The import pipeline has two stages:

1. **Extract** — fetch a competition's schedule, result pages, and start-list pages; decompose event names into structured categories; compute durations; write a JSON report
2. **Load** — read JSON reports, validate duration bounds, and write observations into the learning database (SQLite or DynamoDB)

```
tracktiming.live API
        │
        ▼
┌──────────────────┐     ┌──────────────────────┐
│ extract_competition │ ──▶ │ data/competitions/*.json │
└──────────────────┘     └──────────┬───────────┘
                                    │
                                    ▼
                          ┌──────────────────┐
                          │  load_durations   │
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐
                          │  Learning DB     │
                          │  (SQLite/DynamoDB)│
                          └──────────────────┘
```

The JSON files act as an inspectable intermediate format — you can review extracted data before committing it to the database.

## Quick Start

```bash
source .venv/bin/activate

# Extract a single competition
python -m tools.extract_competition 26008
# Output: data/competitions/26008.json

# Inspect the output
python -m json.tool data/competitions/26008.json | head -50

# Load into the learning database
python -m tools.load_durations data/competitions/26008.json
```

### Batch Processing

```bash
# Extract multiple competitions
for id in 25022 25026 25027 25028 25031 26001 26002 26008 26009 26010; do
    python -m tools.extract_competition "$id"
done

# Load all at once
python -m tools.load_durations data/competitions/*.json
```

## Extraction Script

```
python -m tools.extract_competition <competition_id>
```

The extractor fetches a competition's schedule from the tracktiming.live Jaxon API, then iterates over each completed event to extract durations using the same priority as the live app:

1. **Finish Time** — from the result page (`Finish Time: MM:SS`) plus a discipline-specific changeover allowance. Available for bunch races (scratch, points, elimination, tempo, madison, keirin).
2. **Generated timestamp diff** — the difference between consecutive `Generated:` timestamps on result pages. A plausibility filter rejects diffs outside [0.5x, 2.0x] of the static default for that discipline.
3. **Heat count** — from the start-list page (`Heat N` labels), computed as `heat_count * per_heat_duration + changeover`.

Each event name is also decomposed into structured categories by the categorizer (see [Event Name Categorization](#event-name-categorization) below).

### Output Format

The extractor writes one JSON file per competition to `data/competitions/<id>.json`:

```json
{
  "version": "1.0",
  "extracted_at": "2026-03-21T15:30:00+00:00",
  "competition": {
    "competition_id": 26008,
    "name": "Competition 26008",
    "url": "https://tracktiming.live/eventpage.php?EventId=26008"
  },
  "sessions": [ ... ],
  "duration_observations": [ ... ],
  "uncategorized_summary": [ ... ]
}
```

| Field | Description |
|-------|-------------|
| `sessions` | Full session/event hierarchy with structured categories and per-event durations |
| `duration_observations` | Flat list of all duration observations suitable for bulk import |
| `uncategorized_summary` | Events where the categorizer could not fully decompose the name (for developer review) |

### Checking Uncategorized Events

After extraction, review the uncategorized summary to identify event names that need keyword additions:

```bash
python -c "
import json
with open('data/competitions/26008.json') as f:
    report = json.load(f)
for entry in report.get('uncategorized_summary', []):
    print(f\"{entry['event_name']} -> residual: '{entry['unresolved_text']}'\")
"
```

## Loader Script

```
python -m tools.load_durations <file> [<file> ...]
```

The loader reads JSON report files and writes duration observations into the app's learning database.

### Validation

Before writing, each observation is checked against the static default for its discipline:

- **Bounds check** — durations outside [0.5x, 2.0x] of `DEFAULT_DURATIONS[discipline]` are rejected as outliers with a warning
- **Idempotent upsert** — uses the natural key `(competition_id, session_id, event_position)` so re-running the loader on the same file is safe

### Per-Heat Duration Computation

When a duration observation includes a `heat_count`, the loader computes and stores a per-heat duration:

```
per_heat_duration_minutes = (duration_minutes / heat_count) - changeover
```

This feeds back into the prediction engine's heat-based estimates.

### Output

The loader prints a summary for each file:

```
  26008.json: 112 loaded, 17 out-of-bounds
  26009.json: 74 loaded, 13 out-of-bounds

Total: 186 loaded, 30 out-of-bounds, 0 warnings
```

### Backend Selection

The loader writes to whichever backend is configured:

| `DYNAMODB_TABLE` env var | Backend | Notes |
|--------------------------|---------|-------|
| Empty (default) | SQLite at `DB_PATH` | Local development |
| Set (e.g. `track-timing-durations`) | DynamoDB | Production; writes multi-level aggregates |

## Event Name Categorization

The categorizer (`app/categorizer.py`) decomposes event names like `"Elite/Junior Women Scratch Race / Omni I"` into structured dimensions:

| Dimension | Example values |
|-----------|---------------|
| **discipline** | `sprint_qualifying`, `sprint_match`, `pursuit_4k`, `scratch_race`, `keirin`, `exhibition` |
| **classification** | `elite`, `junior`, `u17`, `master_a`, `elite_junior`, `para_c4`, `age_35_39`, `cat_a` |
| **gender** | `men`, `women`, `open` |
| **round** | `qualifying`, `final`, `semi_final`, `eighth_final`, `repechage`, `final_7_12` |
| **ride_number** | `1`, `2` (for multi-ride rounds like Sprint Final Ride 1) |
| **omnium_part** | `1`–`7` (for omnium sub-events like `/ Omni III`) |

### Extraction Order

The categorizer strips matched components from the event name in sequence:

1. **Special events** — Break, End of Session, Medal Ceremonies, Pause, Warm-up
2. **Omnium part** — `/ Omni I` through `/ Omni VII` (Roman or Arabic numerals)
3. **Ride number** — `Ride 1`, `Ride 2`
4. **Round** — most specific first (`1/16 Final Repechage` before `Final`)
5. **Classification** — compound groups first (`Elite/Junior` before `Elite`), then age brackets, para, license categories, singles
6. **Gender** — English (`Men`, `Women`) and French (`H`, `F`, `Hommes`, `Femmes`, `Dames`)
7. **Discipline** — bilingual keyword table, most specific first

### French Language Support

Quebec competitions (e.g. competition 26009) use French event names. The categorizer handles these with a bilingual keyword table:

| French | Maps to |
|--------|---------|
| Poursuite par equipe | `team_pursuit` |
| Vitesse | `sprint_qualifying` or `sprint_match` (context-dependent) |
| Course aux points | `points_race` |
| Course a l'elimination | `elimination_race` |
| Course tempo | `tempo_race` |
| Course scratch | `scratch_race` |
| Americaine | `madison` |
| CLM / Essai chronometre | `time_trial_generic` |
| 200m (standalone) | `sprint_qualifying` |
| Poursuite | `pursuit` (distance resolved by classification + gender) |
| Maitre | master classification |
| Cadet / Minime | u17 / u15 classification |
| H / F | men / women gender |
| Pause | `break_` special event |

### Distance-Variant Resolution

After extraction, pursuit disciplines are resolved to a specific distance variant based on classification and gender:

| Classification + Gender | Pursuit key |
|------------------------|-------------|
| Elite/Senior (any gender) | `pursuit_4k` |
| Junior men, Master A/B men | `pursuit_3k` |
| Junior women, U17 and younger, Master C+ men, all women | `pursuit_2k` |
| Unresolvable | `pursuit_3k` (fallback) |

## Cascading Learned Duration Fallback

The extended learning database supports granular averages via a cascading query:

1. **Level 4** — discipline + classification + gender (e.g. sprint_match for elite men)
2. **Level 3** — discipline + classification (e.g. sprint_match for elite, any gender)
3. **Level 2** — discipline + gender (e.g. sprint_match for men, any classification)
4. **Level 1** — discipline only (e.g. all sprint_match observations)

The first level with 3 or more samples is used. If no level qualifies, the system falls through to the static `DEFAULT_DURATIONS` constant.

This means a U17 women's pursuit will use the U17-women-specific average if enough data exists, but gracefully falls back to broader averages when data is sparse.

## Database Schema Changes

### SQLite

Three columns added to the existing `event_durations` table:

| Column | Type | Description |
|--------|------|-------------|
| `classification` | TEXT (nullable) | Rider classification (e.g. `elite`, `junior`, `u17`) |
| `gender` | TEXT (nullable) | `men`, `women`, or `open` |
| `per_heat_duration_minutes` | REAL (nullable) | Computed per-heat duration |

Two new indexes:

- `idx_event_durations_category` on `(discipline, classification, gender)` — supports cascading fallback queries
- `idx_event_durations_natural_key` unique on `(competition_id, session_id, event_position)` — supports idempotent upsert

Migration is automatic: `init_db()` detects missing columns and adds them via `ALTER TABLE ADD COLUMN`. Existing rows retain NULL for new columns, contributing correctly to Level 1 (discipline-only) aggregates.

### DynamoDB

New item types added to the existing single-table design:

| Item pattern | Example | Purpose |
|-------------|---------|---------|
| `AGGREGATE#<disc>#<class>#<gender>` | `AGGREGATE#sprint_match#elite#men` | Level 4 running total |
| `AGGREGATE#<disc>#<class>` | `AGGREGATE#sprint_match#elite` | Level 3 running total |
| `AGGREGATE#<disc>##<gender>` | `AGGREGATE#sprint_match##men` | Level 2 running total (double-hash separator) |
| `AGGREGATE#<disc>` | `AGGREGATE#sprint_match` | Level 1 running total (existing) |
| `OBS#<comp>#<sess>#<pos>` | `OBS#26008#1#0` | Observation marker for idempotent upsert |

When recording an observation, the loader updates all applicable aggregate levels and then writes the OBS# item last (so partial failures on aggregates are retryable).

## Reference Competitions

These competitions were used during development and provide good coverage of naming patterns:

| ID | Name | Notable patterns |
|----|------|------------------|
| 25022 | Masters & Para-Cycling Track Nationals | Age brackets (35-39, 80+), Para C1-5 |
| 25026 | FNUTL Keirin Cup | Informal names: "Miss And Out", "Point A Lap" |
| 25027 | FNUTL Omnium Night | Exhibition: "Chariot Race", "Flying Mile", "Wheel Race" |
| 25028 | USA Cycling Elite Track Nationals | "Exhibition Flying 200m", "Omnium Qualifier" |
| 25031 | USA Cycling Collegiate Track Nationals | 1/16 Final, 9-12 Final, "Co-Ed Team Sprint" |
| 26001 | Bromont C2 | International single-day, standard naming |
| 26002 | Canadian Track Championships | Masters A-D, Para B/C3-C5, "Flying 200m" |
| 26008 | Ontario Provincial Championships | U11-U17, compound groups (U11 & U13, Elite/Junior) |
| 26009 | Championnats Quebecois Sur Piste | French: Vitesse, Poursuite, CLM, Course Aux Points |
| 26010 | Local club event | License categories (Cat A, Cat B, Cat C) |

## Verifying Results

After loading data, verify the learned averages:

```bash
# Via the web app
uvicorn app.main:app --reload
# Open http://localhost:8000/learned

# Or query directly
python -c "
from app.database import init_db, get_all_learned_durations
init_db()
for disc, (avg, count) in sorted(get_all_learned_durations().items()):
    print(f'{disc}: {avg:.1f} min ({count} samples)')
"
```

To test cascading fallback:

```bash
python -c "
from app.database import init_db, get_learned_duration_cascading
init_db()
result = get_learned_duration_cascading('sprint_match', 'elite', 'men')
print(f'sprint_match/elite/men: {result:.1f} min' if result else 'No data')
"
```
