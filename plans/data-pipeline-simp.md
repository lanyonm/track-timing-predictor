# Data Pipeline: Competition Extraction & Normalization

## Context

The app's `disciplines.py` has 53 keyword entries that grow combinatorially with each new age/gender/round variation. The learning database needs ≥3 samples per discipline before improving on hardcoded defaults. This plan creates a data extraction pipeline to bulk-populate the learning database from completed tracktiming.live competitions.

### Reference competitions

| ID | Name | Key patterns |
|---|---|---|
| 25022 | Masters & Para-Cycling Track Nationals | Age brackets (`35-39`, `80+`), Para C1-5 |
| 25026 | FNUTL Keirin Cup | "Miss And Out", "Point A Lap", "Kids Race" |
| 25027 | FNUTL Omnium Night | "Chariot Race", "American Tempo", "Longest Lap" |
| 25028 | USA Cycling Elite Track Nationals | "Exhibition Flying 200m", "Omnium Qualifier" |
| 25031 | USA Cycling Collegiate Track Nationals | 1/16 Final, 9-12 Final, "Co-Ed Team Sprint" |
| 26001 | Bromont C2 | Standard international |
| 26002 | Canadian Track Championships | Masters A-D, Para |
| 26008 | Ontario Provincial Championships | All ages, compound groups (U11 & U13, Elite/Junior) |
| 26009 | Championnats Québécois Sur Piste | French: Vitesse, Poursuite, CLM, Course Aux Points, F/H, Maitre |

---

## 1. Unified Report Structure

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

`competition.name` is scraped from the tracktiming.live homepage. `duration_observations` flattens all events with observed durations for bulk import.

**Duration sources** (most to least accurate): `finish_time` (result page + changeover) > `generated_diff` (consecutive Generated timestamps) > `heat_count` (heat_count × per_heat_duration + changeover).

---

## 2. Event Name Categorizer (`app/categorizer.py`)

### 2.1 Model

```python
class EventCategory(BaseModel):
    discipline: str          # normalized key matching disciplines.py (e.g., "sprint_match")
    gender: str              # "men", "women", "open"
    age_group: str           # "elite", "u17", "master_a", "age_35_39", "para_c4", etc.
    round: str | None        # "qualifying", "semi_final", "final_1_6", etc.
    ride_number: int | None  # 1, 2, 3 for best-of-three sprint rides
    omnium_part: int | None  # 1-7 for omnium component numbering
```

`ride_number` and `omnium_part` are needed: ride number affects sprint match duration (Ride 3 only happens sometimes), and omnium part identifies which mass-start discipline applies (I=scratch, II=tempo, III=elimination, IV=points in standard omniums).

### 2.2 Approach: alias dictionary + regex decomposition

Instead of a 7-step sequential stripping pipeline, use:

1. **A flat alias dictionary** mapping all known synonyms to normalized keys:
   ```python
   DISCIPLINE_ALIASES = {
       "miss and out": "elimination_race",
       "american tempo": "tempo_race",
       "point a lap": "tempo_race",
       "vitesse": "sprint_match",       # French
       "poursuite par équipe": "team_pursuit",
       "poursuite": "pursuit",          # resolved by age/gender
       "course aux points": "points_race",
       "clm": "time_trial",            # French, resolved by distance prefix
       "flying 200m": "sprint_qualifying",
       "200m": "sprint_qualifying",     # French standalone
       "chariot race": "exhibition",
       "wheel race": "exhibition",
       "kids race": "exhibition",
       "longest lap": "exhibition",
       "flying mile": "exhibition",
       "pause": "break_",
       # ... existing DISCIPLINE_KEYWORDS entries carried over
   }
   ```

2. **Regex patterns for structured components** (applied to the raw name before discipline lookup):
   ```python
   OMNIUM_RE = r'\s*/\s*Omni\s+(I{1,3}V?|IV|V|VI{1,3}|VII)\s*$'
   RIDE_RE = r'\s*Ride\s+(\d+)\s*$'
   ROUND_RE = r'(1/16 Final Repechage|1/8 Final Repechage|...|Final)\s*$'
   AGE_BRACKET_RE = r'^(\d{1,2}-\d{1,2}|\d{1,2}\+)\s+'
   AGE_GROUP_RE = r'^(Elite/Junior|Junior/Maitre/Elite|...)\s+'
   GENDER_RE = r'\b(Women|Men|Open|Co-Ed)\b|\b([FH])\b'  # F/H only after age group
   ```

3. **`categorize_event(name: str) -> EventCategory`** strips components via regex in sequence (omnium → ride → round → age → gender), then looks up the residual in the alias dictionary. Falls back to `detect_discipline()` from `disciplines.py` for compatibility.

This is simpler than a multi-step stripping pipeline because each regex is independent and testable in isolation. The alias dictionary is a flat data structure that's easy to extend.

### 2.3 Key normalization rules

**Gender**: `F` → women, `H` → men, `Co-Ed`/`Mixed` → open, omitted in compound youth → open. Default: `open`.

**Age groups**: Regex `r'(\d+-\d+|\d+\+)'` handles all bracket formats → `age_35_39`, `age_80_plus`. Named prefixes (`Master A`, `Maitre C-D`, `U17`, `Elite`, `Senior`, `Para C4`, etc.) use a lookup table → `master_a`, `master_cd`, `u17`, `elite`, `senior`, `para_c4`.

**Pursuit distance**: Resolved from age/gender when not explicit (Elite/Senior men → 4k, Junior/Master A-B men → 3k, all women/U17-/Master C+ men → 2k).

**Omnium discipline**: Standard I-IV resolves to scratch/tempo/elimination/points. Non-standard (French up to VII) uses the discipline text in the event name.

**Special events**: "Break", "End of Session", "Medal Ceremon*", "Pause - …", "Madison Warm-up" → `is_special=True`.

### 2.4 Test cases

| Event Name | Expected |
|---|---|
| `U17 Women Sprint Qualifying` | sprint_qualifying, women, u17, qualifying |
| `Master C/D Men Sprint 1/8 Final` | sprint_match, men, master_cd, eighth_final |
| `Elite/Junior Women Scratch Race  / Omni I` | scratch_race, women, elite_junior, omni=1 |
| `U11 & U13 Keirin 1-6 Final` | keirin, open, u11_u13, final_1_6 |
| `Elite Men Sprint 1/2 Final Ride 1` | sprint_match, men, elite, semi_final, ride=1 |
| `Open F Madison Final` | madison, women, open, final |
| `Co-Ed Team Sprint Final` | team_sprint, open, open, final |
| `35-39 Women Pursuit Final` | pursuit_2k, women, age_35_39, final |
| `80+ Men Sprint Qualifying` | sprint_qualifying, men, age_80_plus, qualifying |
| `Para C1-5 Elimination Race Final` | elimination_race, open, para_c1_5, final |
| `Junior/Elite F Vitesse Qualifying` | sprint_qualifying, women, junior_elite, qualifying |
| `Maitre A Kilo CLM Final  / Omni III` | time_trial_kilo, open, master_a, final, omni=3 |
| `U15/U17 H Course Aux Points Final  / Omni V` | points_race, men, u15_u17, final, omni=5 |
| `Women Miss And Out Final` | elimination_race, women, final |
| `Junior Men Point A Lap Final` | tempo_race, men, junior, final |
| `Women Sprint 1/16 Final` | sprint_match, women, sixteenth_final |
| `Men Sprint 9-12 Final` | sprint_match, men, final_9_12 |
| `Kids Race` | exhibition, open |
| `Pause - Reprise à 12h30` | break_, special |

---

## 3. Venue Detection

The tracktiming.live homepage lists competition names. The extraction script scrapes this once per run to populate `competition.name`. Venue-level changeover analysis uses `competition_id` as a grouping key (most competitions happen at a single venue). A manual `venues.json` mapping could be added later if needed.

---

## 4. Files

| File | Action | Purpose |
|---|---|---|
| `app/categorizer.py` | **NEW** | `categorize_event()` + alias dictionary + regex patterns |
| `app/models.py` | **MODIFY** | Add `EventCategory` model |
| `tests/test_categorizer.py` | **NEW** | Test cases from all 9 reference competitions |
| `tools/__init__.py` | **NEW** | Package marker |
| `tools/extract_competition.py` | **NEW** | CLI: fetch competition → JSON report |
| `tools/import_durations.py` | **NEW** | CLI: JSON reports → learning DB |
| `data/competitions/` | **NEW** | Output directory (gitignored) |

---

## 5. Scripts

### `tools/extract_competition.py`

```
python -m tools.extract_competition 26001 26002 26008 26009
```

- argparse CLI taking competition IDs
- Scrapes homepage once for competition names
- For each: `fetch_initial_layout()` → `parse_schedule()` → concurrent fetch of result/start-list pages (reuse semaphore pattern from `main.py:92-155`) → compute durations → `categorize_event()` → write `data/competitions/{id}.json`

### `tools/import_durations.py`

```
python -m tools.import_durations data/competitions/*.json
```

- Reads JSON reports, calls `database.record_duration()` for each `duration_observation`

---

## 6. Implementation Sequence

**Phase 1 — Categorizer**: Add `EventCategory` to `models.py`, create `categorizer.py` with alias dict + regex decomposition, create `test_categorizer.py` with cases from all 9 competitions.

**Phase 2 — Extraction script**: Create `tools/extract_competition.py`, `data/competitions/` dir, add `data/` to `.gitignore`. Test against reference competitions.

**Phase 3 — Import script**: Create `tools/import_durations.py`. Test round-trip: extract → import → verify `get_learned_duration()` returns values.

---

## 7. Design Decisions

- **Categorizer in `app/`**: Domain logic reusable by the web app (eventual replacement for `DISCIPLINE_KEYWORDS`)
- **Flat alias dict over nested keyword list**: Easier to extend, no ordering bugs
- **Regex decomposition over sequential stripping**: Each pattern is independent and testable
- **JSON intermediate format**: Inspect, version-control, re-process without re-fetching
- **`"open"` for mixed gender**: User requirement
- **`"exhibition"` discipline**: Catches novelty events without breaking duration lookups

---

## 8. Verification

1. `pytest tests/test_categorizer.py` — all 9 competitions' event names parse correctly
2. `python -m tools.extract_competition 26008` — valid JSON with reasonable durations
3. `python -m tools.import_durations data/competitions/26008.json` — durations in `get_all_learned_durations()`
4. `pytest` — no regressions
