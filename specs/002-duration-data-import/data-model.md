# Data Model: Duration Data Import Scripts

**Phase 1 output** | **Date**: 2026-03-15

## Entities

### EventCategory

Structured decomposition of an event name into component dimensions. Produced by the categorizer, stored in JSON output, and used by the loader to populate structured database fields.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `discipline` | `str` | Yes | Normalized English discipline key (e.g., `sprint_match`, `pursuit_2k`, `scratch_race`, `exhibition`). Maps to existing keys in `disciplines.py` where possible. |
| `classification` | `str \| None` | No | Unified rider grouping. Age-based: `elite`, `junior`, `u17`, `u15`, `u13`, `u11`, `master_a`–`master_e`, `master`, `senior`. License-category: `cat_a`–`cat_g`, `cat_1`–`cat_6`, `cat_3a`/`cat_3b`. Compound: `elite_junior`, `master_ab`, `master_cd`, `u11_u13`, `u15_u17`, `junior_master_elite`. Para: `para_b`, `para_c1_5`, `para_c2`–`para_c5`. Age brackets: `age_35_39`, `age_55_64`, `age_80_plus`. `None` when not determinable. |
| `gender` | `str` | Yes | `men`, `women`, or `open`. Defaults to `open` when not detected. |
| `round` | `str \| None` | No | `qualifying`, `final`, `semi_final`, `quarter_final`, `eighth_final`, `sixteenth_final`, `repechage`, `round_N`, `final_1_6`, `final_7_12`, `final_5_8`, `final_9_12`, `bronze_final`, `non_championship`, `qualifier_N`, `*_repechage`. `None` when not present. |
| `ride_number` | `int \| None` | No | 1-based ride number for multi-ride rounds (e.g., Sprint 1/2 Final Ride 1). `None` for single-ride events. |
| `omnium_part` | `int \| None` | No | 1-based omnium part number (e.g., `/ Omni III` → 3). Supports standard (I-IV) and extended (up to VII+) formats. `None` for non-omnium events. |

**Validation rules**:
- `discipline` must be a non-empty string
- `gender` must be one of `men`, `women`, `open`
- `ride_number` must be ≥ 1 when present
- `omnium_part` must be ≥ 1 when present

### DurationRecord

A single observation of how long an event took. Stored in the JSON output file and consumed by the loader. Note: `ride_number` from EventCategory is intentionally excluded — it does not affect duration learning (rides within the same round have identical structure).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `discipline` | `str` | Yes | From EventCategory |
| `classification` | `str \| None` | No | From EventCategory |
| `gender` | `str` | Yes | From EventCategory |
| `round` | `str \| None` | No | From EventCategory |
| `omnium_part` | `int \| None` | No | From EventCategory |
| `event_name` | `str` | Yes | Original event name as displayed on tracktiming.live |
| `heat_count` | `int \| None` | No | Number of heats when determinable from start-list or result pages |
| `duration_minutes` | `float` | Yes | Total event duration in minutes |
| `per_heat_duration_minutes` | `float \| None` | No | Per-heat duration derived as `(duration_minutes ÷ heat_count) − changeover`. Computed by the **loader** at load time (not by the extraction script) — this field is `null` in JSON output and populated when writing to the learning database. |
| `duration_source` | `str` | Yes | `finish_time`, `generated_diff`, or `heat_count` |
| `competition_id` | `int` | Yes | tracktiming.live EventId |
| `session_id` | `int` | Yes | 1-based session index within competition |
| `event_position` | `int` | Yes | 0-based event position within session (matches existing `event_position` column in `event_durations` table) |

**Validation rules**:
- `duration_minutes` must be > 0
- `duration_source` must be one of `finish_time`, `generated_diff`, `heat_count`
- `competition_id` must be > 0
- `session_id` must be ≥ 1
- `event_position` must be ≥ 0
- Natural key for idempotency: `(competition_id, session_id, event_position)`

### UncategorizedEntry

Summary of an event name that couldn't be fully categorized. Included in the per-competition output file for developer review.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event_name` | `str` | Yes | Original event name |
| `partial_category` | `EventCategory` | Yes | Whatever dimensions could be extracted |
| `unresolved_text` | `str` | Yes | Residual text after extraction that couldn't be matched |
| `frequency` | `int` | Yes | How many times this exact event name appeared |
| `avg_duration_minutes` | `float \| None` | No | Average observed duration across occurrences |
| `has_heats` | `bool` | Yes | Whether heats were detected for any occurrence |

### CompetitionReport

Top-level JSON output file structure. One file per competition.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | `str` | Yes | Schema version (initially `"1.0"`) |
| `extracted_at` | `str` (ISO 8601) | Yes | When the extraction was performed |
| `competition` | `CompetitionMeta` | Yes | Competition metadata |
| `sessions` | `list[SessionReport]` | Yes | Full session/event hierarchy |
| `duration_observations` | `list[DurationRecord]` | Yes | Flattened observations for bulk import |
| `uncategorized_summary` | `list[UncategorizedEntry]` | Yes | Events that couldn't be fully categorized |

### CompetitionMeta

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `competition_id` | `int` | Yes | tracktiming.live EventId |
| `name` | `str \| None` | No | Competition name if scrapeable; defaults to `"Competition {competition_id}"` when the API does not provide one (see spec edge case) |
| `url` | `str` | Yes | Full URL to the competition page |

### SessionReport

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | `int` | Yes | 1-based session index |
| `day` | `str` | Yes | Day name (e.g., "Friday") |
| `scheduled_start` | `str` | Yes | "HH:MM" format |
| `events` | `list[EventReport]` | Yes | Events in session order |

### EventReport

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `position` | `int` | Yes | 0-based position within session |
| `name` | `str` | Yes | Original event name |
| `category` | `EventCategory` | Yes | Structured decomposition |
| `status` | `str` | Yes | `completed`, `upcoming`, `not_ready` |
| `is_special` | `bool` | Yes | Break, ceremony, etc. |
| `heat_count` | `int \| None` | No | When determinable |
| `duration_minutes` | `float \| None` | No | `None` for incomplete events |
| `duration_source` | `str \| None` | No | `None` for incomplete events |

## Database Schema Extensions

### SQLite

```sql
-- Add structured category columns to existing table
ALTER TABLE event_durations ADD COLUMN classification TEXT DEFAULT NULL;
ALTER TABLE event_durations ADD COLUMN gender TEXT DEFAULT NULL;
ALTER TABLE event_durations ADD COLUMN per_heat_duration_minutes REAL DEFAULT NULL;

-- Index for cascading fallback queries
CREATE INDEX IF NOT EXISTS idx_event_durations_category
    ON event_durations(discipline, classification, gender);

-- Unique index for idempotent upsert (natural key)
CREATE UNIQUE INDEX IF NOT EXISTS idx_event_durations_natural_key
    ON event_durations(competition_id, session_id, event_position);
```

**Cascading fallback query order** (return first with count ≥ 3):
1. `WHERE discipline = ? AND classification = ? AND gender = ?`
2. `WHERE discipline = ? AND classification = ?`
3. `WHERE discipline = ? AND gender = ?`
4. `WHERE discipline = ?`

### DynamoDB

**New aggregate item patterns** (in addition to existing `AGGREGATE#<discipline>`):

| Level | PK Pattern | Example |
|-------|------------|---------|
| 4 | `AGGREGATE#<disc>#<class>#<gender>` | `AGGREGATE#sprint_match#elite#men` |
| 3 | `AGGREGATE#<disc>#<class>` | `AGGREGATE#sprint_match#elite` |
| 2 | `AGGREGATE#<disc>##<gender>` | `AGGREGATE#sprint_match##men` |
| 1 | `AGGREGATE#<disc>` | `AGGREGATE#sprint_match` (existing) |

**Observation items** (for idempotency):

| PK Pattern | Example |
|------------|---------|
| `OBS#<comp_id>#<session_id>#<position>` | `OBS#26008#1#0` |

Each observation item stores `discipline`, `classification`, `gender`, `duration_minutes`. On write: check if OBS item exists → if yes, skip; if no, write OBS item + update all applicable AGGREGATE items.

## Relationships

```
CompetitionReport
  ├── CompetitionMeta (1:1)
  ├── SessionReport[] (1:N)
  │     └── EventReport[] (1:N)
  │           └── EventCategory (1:1)
  ├── DurationRecord[] (1:N, flattened from sessions)
  │     └── references EventCategory fields
  └── UncategorizedEntry[] (1:N)
        └── EventCategory (1:1, partial)
```

## State Transitions

### Event Status (during import)

Events are observed in their final state from historical data:
- `completed` → has result page → duration can be extracted
- `upcoming` / `not_ready` → no result → omitted from duration_observations

### Duration Source Priority (during extraction)

Applied per event, highest priority first:
1. `finish_time` — Result page has Finish Time → duration = finish_time + changeover (for bunch races)
2. `generated_diff` — Result page has Generated timestamp → duration = diff between consecutive Generated timestamps
3. `heat_count` — Start list page shows heat count → duration = heat_count × per_heat_duration + changeover

Events with no extractable duration are included in session data but excluded from `duration_observations`.
