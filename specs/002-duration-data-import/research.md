# Research: Duration Data Import Scripts

**Phase 0 output** | **Date**: 2026-03-15

## 1. Event Name Decomposition Strategy

**Decision**: Compositional strip-and-match parser that extracts structured dimensions in a fixed order from event names.

**Rationale**: Event names from tracktiming.live follow a semi-regular pattern: `[Classification] [Gender] [Discipline] [Round] [Ride] [/ Omni Part]`. A sequential extraction approach — stripping matched components and passing residual text to the next stage — handles the combinatorial variety without requiring exhaustive keyword lists. The existing `plans/data-pipeline.md` documents this approach with 27 test cases across 9 reference competitions.

**Alternatives considered**:
- **Regex-only approach**: A single regex per event name would be fragile and unmaintainable given bilingual naming and compound classifications.
- **ML/NLP classification**: Overkill for structured naming patterns; adds dependencies; not deterministic.
- **Extend existing `DISCIPLINE_KEYWORDS`**: The flat keyword list already has 53 entries and suffers from combinatorial explosion. Every new classification/gender/round combination would require manual entries.

**Key design details** (from `plans/data-pipeline.md` §2.2):

Extraction order:
1. Special events (Break, End of Session, Medal Ceremonies, Pause)
2. Omnium part (`/ Omni I` through `/ Omni VII`)
3. Ride number (`Ride N`)
4. Round (most specific first: `1/16 Final Repechage` before `Final`)
5. Classification (compound first: `Elite/Junior` before `Elite`)
6. Gender (`Men`, `Women`, `H`, `F`, `Open`, `Co-Ed`)
7. Discipline (bilingual keyword table, most specific first)

**Classification dimension** (spec §FR-012): Unifies two naming systems:
- Age-based (championships): Elite, Junior, U11–U17, Master A–D, Para B/C1–C5
- License-category (local/club events): Cat A–G, Cat 1–6, Cat 3A/3B
- Compound groups: Elite/Junior, Master A/B, U11 & U13, etc.
- Age bracket ranges: 35-39, 55-64, 80+, etc.

These systems never co-occur in the same event name, making a single "classification" dimension viable.

## 2. French Terminology Handling

**Decision**: Bilingual keyword table with French terms mapped to the same English discipline keys.

**Rationale**: Quebec competitions on tracktiming.live (e.g., competition 26009) use French discipline names, gender abbreviations, and classification terms. A lookup table is sufficient — there are ~15 French cycling terms to support.

**Key mappings** (from `plans/data-pipeline.md` §2.3, §2.5, §2.6):

| French | English Key | Category |
|--------|------------|----------|
| Poursuite par équipe | team_pursuit | discipline |
| Vitesse | sprint_qualifying/sprint_match | discipline (context-dependent) |
| Course aux points | points_race | discipline |
| Course à l'élimination | elimination_race | discipline |
| Course tempo | tempo_race | discipline |
| Course scratch | scratch_race | discipline |
| Américaine | madison | discipline |
| CLM / Essai chronométré | time_trial | discipline |
| 200m (standalone) | sprint_qualifying | discipline |
| Poursuite | pursuit | discipline |
| Maitre | Master | classification |
| Senior | senior | classification |
| Cadet | U17 | classification |
| Minime | U15 | classification |
| H / Hommes | men | gender |
| F / Femmes / Dames | women | gender |
| Pause | break_ | special |

**Alternatives considered**:
- **Translation library**: Adds dependency, overkill for ~15 terms.
- **Separate French parser**: Unnecessary since both languages follow the same structural pattern.

## 3. DynamoDB Schema Extension for Structured Categories

**Decision**: Multi-level aggregate items with compound partition keys. No sort key needed.

**Rationale**: The existing single-table design uses `AGGREGATE#<discipline>` as the partition key for running totals. Extending this pattern to include classification and gender in the key enables multi-level aggregates while preserving the existing pk-only design.

**DynamoDB item key patterns**:

| Level | Partition Key Pattern | Example |
|-------|----------------------|---------|
| 4 (most specific) | `AGGREGATE#<disc>#<class>#<gender>` | `AGGREGATE#sprint_match#elite#men` |
| 3 | `AGGREGATE#<disc>#<class>` | `AGGREGATE#sprint_match#elite` |
| 2 | `AGGREGATE#<disc>#<gender>` | `AGGREGATE#<disc>##<gender>` → `AGGREGATE#sprint_match##men` |
| 1 (broadest) | `AGGREGATE#<disc>` | `AGGREGATE#sprint_match` |

Each item stores `total_minutes` (N) + `count` (N), same as existing.

**Cascading fallback query**: Up to 4 `GetItem` calls (most specific first). Stop at the first level with `count ≥ min_learned_samples` (3). Each GetItem is ~5ms on DynamoDB, so worst case is ~20ms — negligible compared to upstream API latency.

**Override items**: Extend similarly: `OVERRIDE#<disc>#<class>#<gender>` through `OVERRIDE#<disc>`. Check overrides at each level before aggregates.

**Loader write strategy**: When recording a duration with (discipline, classification, gender), atomically update ALL applicable aggregate levels (up to 4 items). This pre-computes all fallback levels at write time, avoiding expensive scan/aggregation at read time.

**Alternatives considered**:
- **Add sort key**: Would require table recreation and CDK changes. The pk-only design with compound keys is sufficient.
- **GSI for category queries**: Overkill for point lookups at known key patterns.
- **Store raw observations in DynamoDB**: Expensive for aggregation queries; the running-total pattern is already proven.

## 4. SQLite Schema Extension

**Decision**: Add `classification` and `gender` columns to `event_durations` table. Cascading fallback uses parameterized SQL queries at each specificity level.

**Rationale**: Adding columns to the existing table preserves the single-table simplicity. NULL values for classification/gender are valid (meaning "unspecified") and naturally fall through to broader aggregates via SQL `WHERE` clauses.

**Schema changes**:

```sql
-- Add to existing event_durations table
ALTER TABLE event_durations ADD COLUMN classification TEXT DEFAULT NULL;
ALTER TABLE event_durations ADD COLUMN gender TEXT DEFAULT NULL;

-- Index for cascading queries
CREATE INDEX IF NOT EXISTS idx_event_durations_category
    ON event_durations(discipline, classification, gender);

-- Unique index for idempotent upsert (FR-007)
CREATE UNIQUE INDEX IF NOT EXISTS idx_event_durations_natural_key
    ON event_durations(competition_id, session_id, event_position);
```

**Cascading fallback query** (in Python, try each in order):

```sql
-- Level 4: most specific
SELECT AVG(duration_minutes) AS avg_dur, COUNT(*) AS cnt
FROM event_durations WHERE discipline = ? AND classification = ? AND gender = ?

-- Level 3: drop gender
SELECT ... WHERE discipline = ? AND classification = ?

-- Level 2: drop classification
SELECT ... WHERE discipline = ? AND gender = ?

-- Level 1: broadest (existing behavior)
SELECT ... WHERE discipline = ?
```

Return the first level with `cnt >= min_learned_samples`.

**Migration strategy**: Use `ALTER TABLE ADD COLUMN` which is safe in SQLite (no table lock, no data rewrite). Existing rows get NULL for classification/gender, which means they contribute to Level 1 (discipline-only) aggregates — correct behavior since they were recorded before structured categories existed.

**Alternatives considered**:
- **New table for category observations**: Would split data between two tables and complicate queries. Adding columns is simpler.
- **Replace discipline column with composite key**: Would break existing app code. Additive columns are backward-compatible.

## 5. Intermediate Output Format

**Decision**: JSON file per competition, with structure defined in `plans/data-pipeline.md` §1.

**Rationale**: JSON is human-readable (FR-010), supports nested structures (event categories), and is natively handled by Python stdlib. One file per competition enables re-processing individual competitions without re-fetching all data.

**Key structure**:
- Top-level: `version`, `extracted_at`, `competition` metadata
- `sessions[]`: Full session/event hierarchy with categories and durations
- `duration_observations[]`: Flattened array of all observations for bulk import
- `uncategorized_summary[]`: Events that couldn't be fully categorized (FR-011)

**Alternatives considered**:
- **CSV**: Can't represent nested category structures without flattening.
- **JSON Lines**: One record per line — loses the hierarchical session/event context.
- **SQLite file**: Not human-readable; adds complexity for a data exchange format.

## 6. Idempotent Loading Strategy

**Decision**: Natural key `(competition_id, session_id, event_position)` for upsert/skip (FR-007).

**Rationale**: This triple uniquely identifies an event within the tracktiming.live data model. Using a natural key avoids synthetic IDs and makes idempotency transparent.

**SQLite**: `INSERT OR REPLACE` with unique index on the natural key.

**DynamoDB**: The loader must update aggregate totals. For idempotency, store individual observation items keyed by `OBS#<comp_id>#<session_id>#<position>`. Before updating aggregates, check if the observation already exists. If it does, skip. This adds one GetItem per observation but ensures no double-counting.

**Alternatives considered**:
- **Hash-based dedup**: More complex, no advantage over natural key.
- **Delete-and-reinsert**: Risky for aggregate totals; not atomic.
