# Research Findings: 004-racer-palmares

## R-001: Audit Page HTML Structure

**Decision**: Audit pages are standalone HTML files hosted at `tracktiming.live/results/E{comp_id}/{event_code}-AUDIT-R.htm`. They contain per-rider sections with detailed lap/sector timing data.

**Key Format Details**:
- Page title: `{Event Name} - Detailed Rider Data`
- Riders are nested in `div.divcontainer > div.divleft` (and `div.divright` for paired heats)
- Rider name is in a `<p>` element: `{bib} - {LASTNAME} {Firstname}` (e.g., `212 - PITTARD Charlie`)
- Each rider has a `<table class="table table-striped table-condensed">` with columns:
  - Dist, Time, Rk, Lap, Rk, Sect, Rk (7 columns, some Rk headers repeated)
- Heat groupings use `<h3>Heat N</h3>` headings
- Footer contains `Generated: YYYY-MM-DD HH:MM:SS`
- Communique number in `div.panel-title2`

**Rationale**: Fetched a real sample to understand exact parsing needs. The structure is consistent across event types (pursuit shown; other timed events like time trials will follow similar patterns).

**Alternatives Considered**: None — must parse what the upstream provides.

**Evidence**: `tests/fixtures/audit-pursuit-26008.html` (U17 Women Pursuit Final)

---

## R-002: Racer Name Matching for Audit Page CSV Export

**Decision**: Reuse the existing `normalize_rider_name()` function from `app/models.py` for matching racer names in audit page HTML. The audit page format (`LASTNAME Firstname`) is compatible with the token-based matching — normalize both the audit page name and the cookie/param name to token sets and compare.

**Rationale**: The existing rider matching (used for schedule highlighting) already handles diacritics, apostrophes, hyphens, and case normalization via Unicode NFKD decomposition + token set comparison. This same logic correctly matches `PITTARD Charlie` against a user who entered `Charlie Pittard`.

**Alternatives Considered**:
- Exact string match: Rejected — too fragile (case, order, diacritics)
- Fuzzy matching: Rejected — existing token-based normalization is deterministic and already proven

**Evidence**: `app/models.py:50-58` (`normalize_rider_name`), `tests/test_rider_matching.py`

---

## R-003: Palmares Storage — Separate Table Design

**Decision**: Use a dedicated DynamoDB table (`track-timing-palmares-{env}`) and a separate SQLite table (`palmares_entries`) rather than extending the existing durations table. The DynamoDB table uses pk + sort key design (unlike the existing pk-only durations table) to enable efficient per-racer queries.

**Rationale**: User explicitly requested separation of concerns. The palmares data model (racer → competitions → events) is fundamentally different from the learning data model (discipline → aggregate durations). A separate table prevents key namespace collisions and allows independent scaling, backup, and lifecycle management.

**DynamoDB Key Design**:
- Partition key (`pk`): `RACER#{normalized_name}` — groups all entries for one racer
- Sort key (`sk`): `COMP#{comp_id}#S#{session_id}#E#{position}` — unique within racer
- Enables: `Query pk = RACER#name` for full palmares; `begins_with(sk, COMP#{id})` for per-competition operations

**SQLite Schema**:
```sql
CREATE TABLE palmares_entries (
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
```

**Alternatives Considered**:
- Extending existing durations table with palmares item types: Rejected per user requirement
- Client-side localStorage: Rejected — doesn't support sharing via links
- Single DynamoDB table with PALMARES# prefix: Viable but user explicitly requested separate table

**Evidence**: User input: "A separate table with the local and hosted databases should be used to separate concerns from the learning timings." Existing patterns: `app/database.py`, `cdk/track_timing_stack.py`

---

## R-004: Competition Date Source

**Decision**: Use `datetime.now().date().isoformat()` at time of palmares save as the `competition_date` value. This captures the calendar date when the racer viewed the competition schedule.

**Rationale**: The tracktiming.live Jaxon API does not expose a structured competition date. The schedule page shows day names ("Friday") and start times but not calendar dates. Since palmares entries are saved during live or recent schedule views, `now().date()` is a reliable proxy — racers typically view competitions on or near the competition day.

**Alternatives Considered**:
- Parse date from schedule page title: Title format varies and doesn't always include a date
- Store `None` and leave blank: Reduces palmares usefulness (no date context for historical entries)
- Derive from session day name + current week: Fragile and timezone-dependent

**Evidence**: `app/parser.py` — `parse_schedule()` returns `Session` objects with `day: str` and `scheduled_start: time`, neither of which includes a calendar date.

---

## R-005: GET-Only Route Constraint for Palmares Operations

**Decision**: All palmares operations (view, save, delete, export) use GET routes with query parameters. Delete uses GET `/palmares/remove?competition_id=X` with cookie-based authorization (racer_name cookie must be set).

**Rationale**: CloudFront OAC with Lambda Function URLs doesn't support POST request bodies (SigV4 payload signature mismatch causes 403s). This is a documented architectural constraint.

**Alternatives Considered**: None — POST is not possible in the current deployment.

**Evidence**: CLAUDE.md: "All routes must be GET — CloudFront OAC with Lambda Function URLs doesn't support POST request bodies"

---

## R-006: No New Dependencies Required

**Decision**: The palmares feature requires no new Python packages. All functionality is covered by existing dependencies and stdlib.

**Components Mapped to Existing Dependencies**:
- Audit page fetching: `httpx` (existing, shared AsyncClient)
- Audit page HTML parsing: `beautifulsoup4` (existing)
- CSV generation: `csv` (stdlib)
- DynamoDB operations: `boto3` (existing)
- Data models: `pydantic` (existing)
- Route handlers: `fastapi` (existing)
- Template rendering: `jinja2` (existing)

**Rationale**: Constitution Principle IV requires justification for new deps against Lambda constraints. No justification needed since nothing is added.

**Evidence**: `requirements.txt`, Constitution Principle IV
