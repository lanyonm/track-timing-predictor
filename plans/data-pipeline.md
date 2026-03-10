# Data Pipeline: Competition Extraction & Normalization

## Context

The app's `disciplines.py` has 53 keyword entries that suffer from combinatorial explosion — every new age group/gender/round combination requires manual entries. Additionally, the learning database needs ≥3 observed samples per discipline before it can improve on hardcoded defaults.

This plan creates:
1. A **compositional event name categorizer** that handles English, French, and informal event names
2. A **unified JSON report format** for normalized competition data
3. **CLI scripts** to extract data from tracktiming.live and import it into the learning database

### Reference competitions (9 total)

| ID | Name | Notable patterns |
|---|---|---|
| 25022 | Masters & Para-Cycling Track Nationals | Age brackets (35-39, 80+), Para C1-5, combined ranges (55-64, 35+) |
| 25026 | FNUTL Keirin Cup | "Miss And Out", "Point A Lap", "Super Sprint Elimination", "Kids Race" |
| 25027 | FNUTL Omnium Night | "Chariot Race", "American Tempo", "Flying Mile", "Wheel Race", "Longest Lap" |
| 25028 | USA Cycling Elite Track Nationals | "Exhibition Flying 200m", "Omnium Qualifier 1/2", standard international |
| 25031 | USA Cycling Collegiate Track Nationals | 1/16 Final, 9-12 Final, "Co-Ed Team Sprint" |
| 26001 | Bromont C2 | International single-day, standard naming |
| 26002 | Canadian Track Championships | Masters A-D, Para B/C3-C5, "Flying 200m" |
| 26008 | Ontario Provincial Championships | U11-U17, Masters A-D, compound groups (U11 & U13, Elite/Junior) |
| 26009 | Championnats Québécois Sur Piste | French: "Vitesse", "Poursuite", "CLM", "Course Aux Points", "Pause", F/H gender, "Maitre", "Senior", omnium up to VII |

---

## 1. Unified Competition Report Structure

```json
{
  "version": "1.0",
  "extracted_at": "2026-03-09T14:30:00Z",
  "competition": {
    "competition_id": 26008,
    "name": "Ontario Provincial Track Championships",
    "url": "https://tracktiming.live/eventpage.php?EventId=26008"
  },
  "sessions": [
    {
      "session_id": 1,
      "day": "Friday",
      "scheduled_start": "08:15",
      "events": [
        {
          "position": 0,
          "name": "U17 Women Sprint Qualifying",
          "category": {
            "discipline": "sprint_qualifying",
            "gender": "women",
            "age_group": "u17",
            "round": "qualifying",
            "ride_number": null,
            "omnium_part": null
          },
          "status": "completed",
          "is_special": false,
          "heat_count": 5,
          "duration_minutes": 6.25,
          "duration_source": "generated_diff",
          "finish_time_minutes": null,
          "generated_at": "2025-07-11T08:22:15"
        }
      ]
    }
  ],
  "duration_observations": [
    {
      "discipline": "sprint_qualifying",
      "gender": "women",
      "age_group": "u17",
      "round": "qualifying",
      "event_name": "U17 Women Sprint Qualifying",
      "heat_count": 5,
      "duration_minutes": 6.25,
      "duration_source": "generated_diff",
      "competition_id": 26008,
      "session_id": 1,
      "position": 0
    }
  ]
}
```

The `competition.name` is scraped from the tracktiming.live homepage. The top-level `duration_observations` array flattens all events with observed/computed durations for bulk import.

**Duration sources** (most to least accurate):
- `"finish_time"` — Finish Time from result page + changeover
- `"generated_diff"` — difference between consecutive Generated timestamps
- `"heat_count"` — heat_count × per_heat_duration + changeover

---

## 2. Event Name Categorizer (`app/categorizer.py`)

A compositional parser that extracts structured tags from event names by stripping components in sequence. Handles English, French, and informal naming.

### 2.1 Category model (add to `app/models.py`)

```python
class EventCategory(BaseModel):
    discipline: str           # normalized English key (see §2.3)
    gender: str               # "men", "women", "open"
    age_group: str            # see §2.5 for full list
    round: str | None         # see §2.4 for full list
    ride_number: int | None   # 1, 2, 3 for "Ride 1" etc.
    omnium_part: int | None   # 1-7+ for "/ Omni I" etc.
```

### 2.2 Extraction order

Each step matches and strips text from the event name, passing the residual to the next step.

1. **Special events**: "Break", "End of Session", "Medal Ceremon*", "Pause - …", "Madison Warm-up" → set `is_special=True`, discipline directly
2. **Omnium part**: strip `"/ Omni I"` through `"/ Omni VII"` (or `"/ Omni \d+"` regex) → `omnium_part`
3. **Ride number**: strip `"Ride N"` → `ride_number`
4. **Round**: match phrases (most specific first, see §2.4) → `round`
5. **Age group**: match prefixes (compound first, then singles, see §2.5) → `age_group`
6. **Gender**: match keywords (see §2.6) → `gender`
7. **Discipline**: match remaining text against bilingual keyword table (see §2.3) → `discipline`

### 2.3 Discipline keyword table (bilingual)

| Keywords (match in remaining text) | Key | Notes |
|---|---|---|
| `poursuite par équipe` | `team_pursuit` | French, must match before `poursuite` |
| `team pursuit` | `team_pursuit` | |
| `team sprint` | `team_sprint` | |
| `madison` | `madison` | Same in both languages |
| `miss and out` | `elimination_race` | Informal alias |
| `elimination race` | `elimination_race` | |
| `american tempo` | `tempo_race` | Informal alias (point-a-lap) |
| `point a lap` | `tempo_race` | Informal alias |
| `tempo race` | `tempo_race` | |
| `course aux points` | `points_race` | French |
| `points race` | `points_race` | |
| `scratch race` | `scratch_race` | Same in both languages |
| `keirin` | `keirin` | Same in both languages |
| `500m clm`, `500m time trial` | `time_trial_500` | |
| `750m clm`, `750m time trial` | `time_trial_750` | |
| `kilo clm`, `kilo time trial`, `1000m clm`, `1000m time trial` | `time_trial_kilo` | |
| `clm`, `time trial` | `time_trial_generic` | Generic fallback |
| `flying 200m`, `flying mile` | `sprint_qualifying` | |
| `200m` (standalone) | `sprint_qualifying` | French sprint qualifying |
| `vitesse` | `sprint_qualifying` or `sprint_match` | French; `sprint_qualifying` if round=qualifying, else `sprint_match` |
| `sprint qualifying` | `sprint_qualifying` | |
| `sprint` | `sprint_match` | Must be after all sprint-specific keywords |
| `poursuite` | pursuit (distance by age/gender) | French individual pursuit |
| `pursuit` | pursuit (distance by age/gender) | Fallback for unqualified pursuit |
| `super sprint elimination` | `elimination_race` | Informal sprint-discipline hybrid |
| `chariot race`, `wheel race`, `kids race`, `longest lap` | `exhibition` | Novelty/informal events |

**Pursuit distance resolution** (when pursuit has no explicit distance):
- Elite/Senior men → `pursuit_4k`
- Junior men, Master A/B men → `pursuit_3k`
- All women, U17 and younger, Master C+ men → `pursuit_2k`
- Fallback → `pursuit_3k`

### 2.4 Round patterns

Match and strip these phrases (order: most specific first to avoid partial matches):

| Pattern | Key |
|---|---|
| `Qualifying` | `qualifying` |
| `Qualifier N` | `qualifier_N` (e.g., `qualifier_1`) |
| `1/16 Final Repechage` | `sixteenth_final_repechage` |
| `1/8 Final Repechage` | `eighth_final_repechage` |
| `1/16 Final` | `sixteenth_final` |
| `1/8 Final` | `eighth_final` |
| `1/4 Final` | `quarter_final` |
| `1/2 Final` | `semi_final` |
| `Repechage` | `repechage` |
| `Round N` | `round_N` |
| `1-6 Final` | `final_1_6` |
| `7-12 Final` | `final_7_12` |
| `5-8 Final` | `final_5_8` |
| `9-12 Final` | `final_9_12` |
| `Bronze Final` | `bronze_final` |
| `Non Comp` / `Non Championship` | `non_championship` |
| `Final` | `final` (must be last) |

### 2.5 Age group patterns

Match prefixes (compound first to avoid partial matches):

**Compound groups:**
- `Elite/Junior`, `Junior/Elite` → `elite_junior`
- `Junior/Maitre/Elite`, `Junior/Master/Elite` → `junior_master_elite`
- `U17/U15`, `U15/U17` → `u15_u17`
- `U11 & U13` → `u11_u13`
- `Master A/B`, `Maitre A-B` → `master_ab`
- `Master C/D`, `Maitre C-D` → `master_cd`

**Age bracket ranges** (regex `\d+-\d+` or `\d+\+`):
- `35-39`, `40-44`, `45-49`, `50-54`, `55-59`, `60-64`, `65-69`, `70-74`, `75-79`, `80-84` → preserved as-is (e.g., `age_35_39`)
- Combined: `55-64`, `35-49`, `60-69`, `35-44` → preserved (e.g., `age_55_64`)
- Open-ended: `35+`, `45+`, `55+`, `65+`, `70+`, `80+` → preserved (e.g., `age_35_plus`)

**Singles:**
- `Elite`, `Senior` → `elite` and `senior` respectively
- `Junior` → `junior`
- `U17`, `U15`, `U13`, `U11` → `u17`, `u15`, `u13`, `u11`
- `Master A` / `Maitre A` → `master_a` (through `master_e`)
- `Master` / `Maitre` → `master`
- `Open` → `open` (as age group, e.g., "Open F Madison")
- `Co-Ed` → `open` (with gender=`open`)

**Para classifications:**
- `Para C1-5` → `para_c1_5`
- `Para C2` through `Para C5` → `para_c2` through `para_c5`
- `Para B` → `para_b`

### 2.6 Gender patterns

| Pattern | Key | Notes |
|---|---|---|
| `Women`, `F` (French abbrev) | `women` | |
| `Men`, `H` (French abbrev) | `men` | `H` = Hommes |
| `Open`, `Co-Ed`, `Mixed` | `open` | Per user requirement |
| (omitted in compound youth groups) | `open` | Default for U11 & U13 etc. |
| (undetected) | `open` | Fallback default |

**Note on `F`/`H` matching**: These single-letter abbreviations must be matched carefully to avoid false positives. They appear as standalone words in French event names (e.g., "U15 F 200m Final"). Match as `\bF\b` / `\bH\b` only when preceded by an age group token.

### 2.7 Omnium discipline resolution

**Standard international omnium (Omni I-IV):** I=Scratch Race, II=Tempo Race, III=Elimination Race, IV=Points Race. When an event has `omnium_part` 1-4 and the discipline is already detected from the event name text (e.g., "Scratch Race / Omni I"), the detected discipline takes priority. When the name only says "Omni I" without a discipline keyword, resolve by part number.

**Non-standard multi-discipline omniums** (e.g., 26009 with up to Omni VII): The omnium includes non-standard components (200m, pursuit, keirin, CLM, sprint). In these cases, the discipline is always detected from the event name text, and `omnium_part` is stored for reference/grouping only.

### 2.8 Test cases from all 9 competitions

| Event Name | disc | gender | age | round | ride | omni |
|---|---|---|---|---|---|---|
| `U17 Women Sprint Qualifying` | sprint_qualifying | women | u17 | qualifying | — | — |
| `Master C/D Men Sprint 1/8 Final` | sprint_match | men | master_cd | eighth_final | — | — |
| `Elite/Junior Women Scratch Race  / Omni I` | scratch_race | women | elite_junior | — | — | 1 |
| `U11 & U13 Keirin 1-6 Final` | keirin | open | u11_u13 | final_1_6 | — | — |
| `Elite Men Sprint 1/2 Final Ride 1` | sprint_match | men | elite | semi_final | 1 | — |
| `Para C4 Men Flying 200m Final` | sprint_qualifying | men | para_c4 | final | — | — |
| `Open F Madison Final` | madison | women | open | final | — | — |
| `Open H Madison Final` | madison | men | open | final | — | — |
| `Co-Ed Team Sprint Final` | team_sprint | open | open | final | — | — |
| `35-39 Women Pursuit Final` | pursuit_2k | women | age_35_39 | final | — | — |
| `80+ Men Sprint Qualifying` | sprint_qualifying | men | age_80_plus | qualifying | — | — |
| `55-64 Women Sprint 1/2 Final Ride 1` | sprint_match | women | age_55_64 | semi_final | 1 | — |
| `Para C1-5 Elimination Race Final` | elimination_race | open | para_c1_5 | final | — | — |
| `Para B Mixed Team Sprint Final` | team_sprint | open | para_b | final | — | — |
| `Junior/Elite F Vitesse Qualifying` | sprint_qualifying | women | junior_elite | qualifying | — | — |
| `U15 F 200m Final  / Omni I` | sprint_qualifying | women | u15 | final | — | 1 |
| `Maitre A Kilo CLM Final  / Omni III` | time_trial_kilo | open | master_a | final | — | 3 |
| `Senior H Vitesse Final Ride 1 / Omni III` | sprint_match | men | senior | final | 1 | 3 |
| `Junior/Maitre/Elite F Tempo Race Final  / Omni II` | tempo_race | women | junior_master_elite | final | — | 2 |
| `U15/U17 H Course Aux Points Final  / Omni V` | points_race | men | u15_u17 | final | — | 5 |
| `Pause - Reprise à 12h30` | break_ | — | — | — | — | — |
| `Women Miss And Out Final` | elimination_race | women | — | final | — | — |
| `Men American Tempo Final` | tempo_race | men | — | final | — | — |
| `Junior Men Point A Lap Final` | tempo_race | men | junior | final | — | — |
| `Women Sprint 1/16 Final` | sprint_match | women | — | sixteenth_final | — | — |
| `Men Sprint 1/16 Final Repechage` | sprint_match | men | — | sixteenth_final_repechage | — | — |
| `Women Sprint 9-12 Final` | sprint_match | women | — | final_9_12 | — | — |
| `Exhibition Flying 200m Final` | sprint_qualifying | — | — | final | — | — |
| `Men Omnium Qualifier 1` | unknown | men | — | qualifier_1 | — | — |
| `Kids Race` | exhibition | open | — | — | — | — |
| `Junior Open Chariot Race Final` | exhibition | open | junior | final | — | — |

---

## 3. Venue Detection

The tracktiming.live homepage (`GET /`) lists all competitions with their names. The extraction script will scrape this page to build a `{competition_id: name}` mapping and store the name in the report.

**Venue inference** from competition name is not automated — it would require a manual mapping table. Instead:
- Store the competition name in the report (e.g., "Ontario Provincial Track Championships")
- The `duration_observations` are tagged with `competition_id`, enabling downstream grouping by competition
- A future enhancement could add a `venues.json` mapping file (`{competition_id: venue_name}`) for cross-venue changeover analysis

For now, changeover variance analysis is done at the **competition level** (comparing the same discipline across different competition IDs), which serves as a proxy for venue variance since most competitions happen at a single venue.

---

## 4. File Layout

| File | Purpose |
|---|---|
| `app/categorizer.py` | **NEW** — `categorize_event()` function + bilingual extraction helpers |
| `app/models.py` | **MODIFY** — add `EventCategory` model |
| `tests/test_categorizer.py` | **NEW** — test cases from all 9 reference competitions |
| `tools/__init__.py` | **NEW** — package marker |
| `tools/extract_competition.py` | **NEW** — CLI: fetch competition → write JSON report |
| `tools/import_durations.py` | **NEW** — CLI: read JSON reports → bulk insert into learning DB |
| `data/competitions/` | **NEW** — output directory for JSON reports (gitignored) |

---

## 5. Script Details

### `tools/extract_competition.py`

```
python -m tools.extract_competition 26001 26002 26008 26009
```

- Takes competition IDs as CLI args (argparse)
- Reuses `app/fetcher.py` async functions via `asyncio.run()`
- Scrapes tracktiming.live homepage once to get competition names
- For each competition:
  1. `fetch_initial_layout()` → `parse_schedule()` → sessions/events
  2. Concurrent fetch of all result pages + start list pages (reuse semaphore pattern from `main.py:92-155`)
  3. Parse finish times, generated timestamps, heat counts
  4. Compute durations: generated_diff between consecutive events, or finish_time+changeover, or heat_count×per_heat
  5. Run `categorize_event()` on each event name
  6. Write JSON report to `data/competitions/{competition_id}.json`

### `tools/import_durations.py`

```
python -m tools.import_durations data/competitions/*.json
```

- Reads JSON reports, iterates `duration_observations`
- Calls `database.record_duration()` for each observation
- Seeds the learning DB so `get_learned_duration()` returns data-driven averages immediately

---

## 6. Implementation Sequence

### Phase 1: Categorizer
1. Add `EventCategory` model to `app/models.py`
2. Create `app/categorizer.py` with `categorize_event()` and bilingual extraction helpers
3. Create `tests/test_categorizer.py` — validate against event names from all 9 reference competitions

### Phase 2: Extraction script
1. Create `tools/` directory with `__init__.py`
2. Create `tools/extract_competition.py`
3. Create `data/competitions/` directory, add `data/` to `.gitignore`
4. Test against all 9 reference competitions

### Phase 3: Import script
1. Create `tools/import_durations.py`
2. Test round-trip: extract → import → verify `get_learned_duration()` returns values

---

## 7. Key Design Decisions

- **Categorizer in `app/` not `tools/`**: It's domain logic the web app will eventually use to replace `DISCIPLINE_KEYWORDS` with compositional parsing
- **Reuse async fetcher via `asyncio.run()`**: The existing semaphore-bounded concurrency is ideal for bulk scraping
- **JSON intermediate format**: Enables inspection, version control, and re-processing without re-fetching
- **`data/` gitignored**: Generated artifacts shouldn't be committed
- **`"open"` for mixed/co-ed gender**: Per user requirement
- **`"exhibition"` discipline for novelty events**: Chariot Race, Kids Race, etc. get a duration but are flagged as non-standard
- **Venue by competition ID, not automated**: Competition names are stored; manual venue mapping can be added later
- **Omnium resolution**: Standard I-IV maps to scratch/tempo/elimination/points; non-standard (French multi-event) detected from event name text

---

## 8. Verification

1. `pytest tests/test_categorizer.py` — all event names from 9 competitions parse correctly
2. `python -m tools.extract_competition 26008` — produces valid JSON report
3. Inspect `data/competitions/26008.json` — verify duration_observations have reasonable values
4. `python -m tools.import_durations data/competitions/26008.json` — verify durations appear in `get_all_learned_durations()`
5. `pytest` — no regressions in existing tests
