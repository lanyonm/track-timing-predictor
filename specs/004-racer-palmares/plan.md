# Implementation Plan: Racer Palmares (Achievements)

**Branch**: `004-racer-palmares` | **Date**: 2026-03-22 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-racer-palmares/spec.md`

## Summary

Add a palmares (achievements) feature that automatically saves audit result links for identified racers during schedule viewing, presents them on a shareable profile page grouped by competition, and enables per-event CSV export of individual audit result data. Uses a dedicated database table (separate from learning durations) with the existing dual SQLite/DynamoDB backend pattern.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI, httpx, Pydantic, pydantic-settings, Jinja2, BeautifulSoup, boto3 (all existing — no new dependencies)
**Storage**: SQLite (local dev) + DynamoDB (production) — NEW separate table for palmares data
**Testing**: pytest with fixture-based tests, conftest.py SQLite isolation pattern
**Target Platform**: Lambda + Function URL (Docker image from ECR), local dev via uvicorn
**Project Type**: Web service (FastAPI)
**Performance Goals**: Profile page < 3s, CSV export < 5s, palmares save within schedule page load
**Constraints**: GET-only routes, 512 MB Lambda memory, 60s timeout, no new dependencies
**Scale/Scope**: Typical racer: 10 competitions × 5 events = 50 entries; page served from single DynamoDB query

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Graceful Degradation | PASS | Palmares failures don't break predictions. Page works without JS (Copy Link is progressive enhancement). Mobile-first layout. |
| II. Testable Without External Dependencies | PASS | Captured audit page fixture (`tests/fixtures/audit-pursuit-26008.html`). SQLite backend for all tests via conftest.py. No live API calls needed. |
| III. Separation of Concerns | PASS | New `palmares.py` for storage (separate from `database.py`). New `audit_parser.py` for HTML parsing. GET-only routes. Shared httpx.AsyncClient via lifespan. Pydantic BaseSettings for config. |
| IV. Minimal Dependencies | PASS | Zero new dependencies. All needs covered by existing deps + stdlib `csv`. |
| V. Operability | PASS | Structured logging for palmares save/delete/export operations. Health check extended with palmares table status (degraded, not blocking). |
| VI. Cost-Aware Growth | PASS | New DynamoDB table (user requested for concern separation). On-demand billing — no fixed cost. Table is small (< 1KB per entry). |
| VII. Prediction Integrity | N/A | Palmares feature does not affect prediction calculations. |
| VIII. Security & Data Minimization | PASS | Racer names + competition data are publicly available on tracktiming.live. No new PII collected. No new cookies — uses existing `racer_name` cookie. Shared links are read-only (no cookie set). |

## Research Phase: External Data Formats

Audit page fixture fetched and committed: `tests/fixtures/audit-pursuit-26008.html`

Format documented in [research.md](research.md) (R-001).

## Research Findings

| Finding | Impact | Evidence |
|---------|--------|----------|
| Audit pages use `<p>` elements for rider names in `{bib} - {LASTNAME} {Firstname}` format | Parser must extract name from `<p>`, handle bib prefix | `tests/fixtures/audit-pursuit-26008.html` |
| Rider data tables have 7 columns: Dist, Time, Rk, Lap, Rk, Sect, Rk | CSV export columns determined; repeated "Rk" headers need disambiguation | `tests/fixtures/audit-pursuit-26008.html` |
| Riders grouped by heat in `div.divleft` / `div.divright` | Parser must traverse div structure, not rely on table nesting | `tests/fixtures/audit-pursuit-26008.html` |
| Existing `normalize_rider_name()` handles diacritics, case, token order | Reuse for audit page name matching — no new matching logic needed | `app/models.py:50-58` |
| DynamoDB pk+sk design enables efficient per-racer queries | Separate table allows pk+sk (unlike existing pk-only durations table) | `cdk/track_timing_stack.py`, user requirement |

## Proposed Visuals

### Schedule Page — Racer Info Area (updated)

```
┌──────────────────────────────────────────────────────┐
│ ⓘ Found in 4 events                                 │
│ 🏁 Your next race: U17 Women Scratch Race (Heat 2)  │
│    Predicted start: 10:45                            │
│ 📋 3 of your timed events are in your palmares →     │
└──────────────────────────────────────────────────────┘
```

The palmares message (bottom line) appears in the existing `.racer-message-info` block with a link (`→`) to `/palmares?r={encoded}`. The count reflects total entries for this competition, incrementing on HTMX refreshes as new audit results become available.

### Palmares Page — With Entries (identified racer)

```
┌─────────────────────────────────────────────────────────────┐
│  Track Timing Predictor                    [Palmares] [Home]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Palmares for Charlie Pittard                               │
│                                                             │
│  Share your achievements with others:                       │
│  [Copy Link]  "Copied!"                                    │
│  Anyone with this link can see your event history.          │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 🔗 Ontario Track Championships  •  2026-02-28       │   │
│  │                                              [✕]    │   │
│  │                                                     │   │
│  │  U17 Women Pursuit Final         [Audit] [⬇]       │   │
│  │  U17 Women Scratch Race          [Audit] [⬇]       │   │
│  │  U17 Women Points Race           [Audit] [⬇]       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 🔗 Eastern Track Series #1  •  2026-01-15           │   │
│  │                                              [✕]    │   │
│  │                                                     │   │
│  │  U17 Women Individual Pursuit    [Audit] [⬇]       │   │
│  │  U17 Women Time Trial            [Audit] [⬇]       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘

Legend:
  🔗  Competition name = link to /schedule/{id}?r={encoded}
  [Audit]  = link to tracktiming.live audit page (opens in new tab)
  [⬇]  = CSV export download icon (becomes spinner during load,
          warning icon on error)
  [✕]  = Remove competition (visible only via cookie, not shared links;
          shows confirmation prompt before deletion)
  [Copy Link]  = copies shareable URL to clipboard
```

### Palmares Page — Empty State (identified, no entries)

```
┌─────────────────────────────────────────────────────────────┐
│  Track Timing Predictor                    [Palmares] [Home]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Palmares for Charlie Pittard                               │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                                                     │   │
│  │   No achievements yet                               │   │
│  │                                                     │   │
│  │   View a competition schedule while your name is    │   │
│  │   set, and your timed events with audit results     │   │
│  │   will appear here automatically.                   │   │
│  │                                                     │   │
│  │   [View a competition →]                            │   │
│  │                                                     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Palmares Page — Unidentified Visitor

```
┌─────────────────────────────────────────────────────────────┐
│  Track Timing Predictor                    [Palmares] [Home]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Palmares                                                   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                                                     │   │
│  │   Enter your name to view your achievements         │   │
│  │                                                     │   │
│  │   Your full name as shown on the start list:        │   │
│  │   [____________________________]  [View Palmares]   │   │
│  │                                                     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Mobile Layout (360px+)

On mobile viewports, competition cards stack full-width. Within each card:
- Competition header: name + date on one line, remove action on right
- Event rows: event name full-width, [Audit] + [⬇] inline on next line
- Share section: copy button full-width, description text below
- Name form: input and button stack vertically

### CSV Export Loading States

```
Normal:     U17 Women Pursuit Final    [Audit] [⬇]
Loading:    U17 Women Pursuit Final    [Audit] [⏳]
Error:      U17 Women Pursuit Final    [Audit] [⚠️ Could not load audit data]
```

## Project Structure

### Documentation (this feature)

```text
specs/004-racer-palmares/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 research findings
├── data-model.md        # Phase 1 data model
├── quickstart.md        # Phase 1 quickstart guide
├── contracts/
│   └── routes.md        # Route contracts
└── checklists/
    └── requirements.md  # Spec quality checklist
```

### Source Code (repository root)

```text
app/
├── main.py              # MODIFIED: new palmares routes, schedule route integration
├── config.py            # MODIFIED: add palmares_table setting
├── palmares.py          # NEW: palmares database operations (dual SQLite/DynamoDB)
├── audit_parser.py      # NEW: parse audit page HTML for CSV export
├── models.py            # MODIFIED: add PalmaresEntry, PalmaresCompetition models
└── templates/
    ├── base.html        # MODIFIED: add palmares nav link
    ├── palmares.html    # NEW: palmares profile page
    └── _schedule_body.html  # MODIFIED: add palmares count message

static/
└── style.css            # MODIFIED: add palmares card, download icon, spinner styles

cdk/
└── track_timing_stack.py  # MODIFIED: add palmares DynamoDB table

tests/
├── test_palmares.py       # NEW: palmares database operations (SQLite)
├── test_palmares_dynamo.py # NEW: palmares DynamoDB operations (moto)
├── test_audit_parser.py   # NEW: audit page parsing tests
├── test_palmares_routes.py # NEW: route handler tests
├── conftest.py            # MODIFIED: add palmares table setup
└── fixtures/
    └── audit-pursuit-26008.html  # NEW: captured audit page fixture
```

**Structure Decision**: Follows the existing single-project FastAPI layout. New modules (`palmares.py`, `audit_parser.py`) are co-located in `app/` alongside existing modules. Separation of concerns maintained: `palmares.py` handles storage, `audit_parser.py` handles external HTML parsing, routes in `main.py`.

## Implementation Phases

### Phase 1: Data Layer + Models (P1 foundation)

**Goal**: Palmares storage working end-to-end with both backends.

1. Add `PalmaresEntry` and `PalmaresCompetition` models to `app/models.py`
2. Add `palmares_table` setting to `app/config.py` (empty string = SQLite)
3. Create `app/palmares.py` with dual-backend operations:
   - `init_palmares_db()` — create SQLite table
   - `save_palmares_entries(racer_name, entries: list[PalmaresEntry])` — batch upsert
   - `get_palmares(racer_name) -> list[PalmaresCompetition]` — grouped by competition, reverse chronological
   - `count_competition_palmares(racer_name, competition_id) -> int` — for schedule message
   - `delete_competition_palmares(racer_name, competition_id) -> int` — returns count deleted
4. Update `conftest.py` — initialize palmares table in test DB, clear palmares DynamoDB cache
5. Write `tests/test_palmares.py` — SQLite CRUD tests
6. Write `tests/test_palmares_dynamo.py` — DynamoDB tests with moto mock
7. Add palmares DynamoDB table to `cdk/track_timing_stack.py`

### Phase 2: Palmares Collection (P1 auto-save)

**Goal**: Schedule views automatically save matched events to palmares.

1. Integrate palmares saving into the schedule route handler in `app/main.py`:
   - After `predict_schedule()`, iterate `schedule.sessions[].event_predictions[]`
   - For each prediction with `rider_match` and `event.audit_url` and not `event.is_special` and `event.discipline` in `_TIMED_DISCIPLINES`: collect as `PalmaresEntry`
   - `_TIMED_DISCIPLINES` = pursuits (pursuit_4k, pursuit_3k, pursuit_2k, team_pursuit), team_sprint, and time trials (time_trial_500, time_trial_750, time_trial_kilo, time_trial_generic) — disciplines that produce per-lap/sector audit data
   - Call `save_palmares_entries()` with collected entries
   - Call `count_competition_palmares()` to get count for template
2. Same integration for the `/schedule/{event_id}/refresh` route
3. Pass `palmares_count` to template context
4. Update `_schedule_body.html` — add palmares count message in racer info area
5. Write route-level tests for palmares collection

### Phase 3: Palmares Profile Page (P2 + P3)

**Goal**: Full profile page with sharing and deletion.

1. Create `app/templates/palmares.html`:
   - Extends `base.html`
   - Three states: identified+entries, identified+empty, unidentified
   - Card-based layout for competition groups
   - Share section with Copy Link button + description
   - Per-competition remove action (conditional on cookie vs shared link)
   - Mobile-responsive CSS
2. Add GET `/palmares` route to `app/main.py`:
   - Resolve racer name (r= param with cookie fallback)
   - Detect "is_owner" (has cookie set AND cookie matches resolved name)
   - Fetch palmares entries grouped by competition
   - Render template with appropriate state
3. Add GET `/palmares/remove` route:
   - Require racer_name cookie (not r= param)
   - Delete entries for specified competition_id
   - Redirect to `/palmares` (303)
4. Update `app/templates/base.html` — add "Palmares" nav link
5. Add palmares card styles, share styles, remove button styles to `static/style.css`
6. Write route tests for all three page states + removal + shared link read-only behavior

### Phase 4: CSV Export (P4)

**Goal**: Per-event audit data export as CSV.

1. Create `app/audit_parser.py`:
   - `parse_audit_riders(html: str) -> list[dict]` — parse all riders with their data tables
   - `filter_rider_data(riders: list[dict], racer_name: str) -> list[dict]` — match using `normalize_rider_name()`
   - `format_csv(rider_data: list[dict], event_name: str) -> str` — generate CSV string
2. Add GET `/palmares/export` route to `app/main.py`:
   - Resolve racer name
   - Fetch audit page HTML via shared httpx client
   - Parse and filter for racer's data
   - Return CSV response with appropriate headers
   - Handle errors: audit page unavailable (502), no matching data (CSV with headers only)
3. Add download icon, spinner, and warning icon CSS to `static/style.css`
4. Add minimal JavaScript to `palmares.html` for async CSV download with loading states
5. Write `tests/test_audit_parser.py` using fixture
6. Write route tests for export success, error, and no-match cases

### Phase 5: Testing + Documentation

**Goal**: Comprehensive test coverage and documentation updates.

1. Integration tests:
   - End-to-end: set racer name → view schedule → verify palmares saved → view palmares page → export CSV
   - Shared link flow: generate shared link → open without cookie → verify read-only
   - HTMX refresh: verify palmares count updates during simulated live event
2. Edge case tests:
   - No audit URLs available → no palmares entries created
   - Special events excluded
   - Duplicate schedule views → no duplicate entries
   - Competition removal → entries deleted, page re-renders
   - Unidentified visitor → name form displayed
3. Update `CLAUDE.md`:
   - Add palmares routes to route table
   - Add `PALMARES_TABLE` to environment variables table
   - Add palmares module descriptions to Architecture section
   - Document palmares key design in Learning mechanism section
   - Add palmares test patterns to Key Patterns section
4. Update `README.md` if present with palmares feature description
5. Verify all tests pass: `pytest`

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| FR-015 deletion confirmation requires JavaScript (Constitution I says core functionality must work without JS) | The confirmation prompt uses `onclick="return confirm(...)"` which is JS-only. Without JS, the remove link navigates directly and deletes without confirmation. | A no-JS confirmation page (separate GET route) was considered but rejected: it adds a route, a template, and complexity for a rare edge case (JS-disabled users removing competitions). Deletion is recoverable (re-viewing the competition re-populates palmares). The tradeoff is explicitly accepted. |

The new DynamoDB table is explicitly requested by the user and uses on-demand billing (zero cost when idle).
