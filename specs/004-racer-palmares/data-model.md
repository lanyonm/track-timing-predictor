# Data Model: 004-racer-palmares

## Entities

### PalmaresEntry (Pydantic model)

Represents a single timed event in a racer's palmares.

| Field | Type | Description |
|-------|------|-------------|
| racer_name | str | Normalized racer name (lowercase token set, same as rider matching) |
| competition_id | int | tracktiming.live EventId |
| competition_name | str | Human-readable competition name (from schedule page title) |
| competition_date | str \| None | ISO date string (YYYY-MM-DD) extracted from session schedule |
| session_id | int | Session identifier within competition |
| session_name | str | Session display name (e.g., "Friday") |
| event_position | int | 0-based event position within session |
| event_name | str | Full event name (e.g., "U17 Women Pursuit Final") |
| audit_url | str | Relative URL path to audit page (e.g., `results/E26008/W1516-IP-2000-F-0-AUDIT-R.htm`) |

### PalmaresCompetition (display grouping model)

Groups palmares entries by competition for template rendering.

| Field | Type | Description |
|-------|------|-------------|
| competition_id | int | tracktiming.live EventId |
| competition_name | str | Human-readable name |
| competition_date | str \| None | ISO date string |
| entries | list[PalmaresEntry] | Events within this competition, ordered by session_id then event_position |

## Storage

### SQLite Table: `palmares_entries`

```sql
CREATE TABLE IF NOT EXISTS palmares_entries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    racer_name        TEXT NOT NULL,
    competition_id    INTEGER NOT NULL,
    competition_name  TEXT NOT NULL,
    competition_date  TEXT,
    session_id        INTEGER NOT NULL,
    session_name      TEXT NOT NULL,
    event_position    INTEGER NOT NULL,
    event_name        TEXT NOT NULL,
    audit_url         TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(racer_name, competition_id, session_id, event_position)
);
CREATE INDEX IF NOT EXISTS idx_palmares_racer ON palmares_entries(racer_name);
```

**Uniqueness**: Natural key is `(racer_name, competition_id, session_id, event_position)`. Duplicate inserts are rejected via `INSERT OR IGNORE`.

### DynamoDB Table: `track-timing-palmares-{env}`

| Attribute | Key | Type | Description |
|-----------|-----|------|-------------|
| pk | Partition | String | `RACER#{normalized_name}` |
| sk | Sort | String | `COMP#{comp_id}#S#{session_id}#E#{position}` |
| competition_name | - | String | Human-readable name |
| competition_date | - | String | ISO date or empty |
| session_name | - | String | Session display name |
| event_name | - | String | Full event name |
| audit_url | - | String | Relative URL path |
| created_at | - | String | ISO datetime of creation |

**Access Patterns**:

| Operation | Key Condition | Use Case |
|-----------|--------------|----------|
| List all for racer | `pk = RACER#{name}` | Palmares profile page |
| List by competition | `pk = RACER#{name} AND begins_with(sk, COMP#{id})` | Schedule page count, deletion |
| Save entry | PutItem (idempotent by pk+sk) | Auto-save during schedule view |
| Delete competition | Query + BatchWriteItem Delete | Per-competition removal |

## Relationships

```
Racer (normalized name)
  └── Competition (competition_id)
        └── Session (session_id)
              └── Event (event_position) → audit_url
```

## State Transitions

Palmares entries have no state transitions — they are created once and either exist or are deleted (per-competition removal). There is no update operation; entries are immutable once written.

## Audit Page Data (for CSV export)

Parsed from tracktiming.live audit HTML, not stored in palmares database.

| Field | Type | Source |
|-------|------|--------|
| rider_name | str | `<p>` element: `{bib} - {LASTNAME} {Firstname}` |
| heat | str | `<h3>Heat N</h3>` heading above the rider section |
| dist | str | Table column 1 |
| time | str | Table column 2 |
| rank | str | Table column 3 |
| lap | str | Table column 4 |
| lap_rank | str | Table column 5 |
| sect | str | Table column 6 |
| sect_rank | str | Table column 7 |
