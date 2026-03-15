# Implementation Plan: Racer Schedule Lookup

**Branch**: `001-racer-schedule-lookup` | **Date**: 2026-03-14 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-racer-schedule-lookup/spec.md`

## Summary

Allow a racer to enter their name on a competition schedule page and see their events highlighted with heat-specific predicted start times. Names are parsed from existing start list pages, matched using case-insensitive order-independent full-name comparison, persisted via cookie for returning users, and encoded in the URL for sharing. No new dependencies required — extends existing parsing, prediction, and template infrastructure.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI, Pydantic, httpx, Jinja2, BeautifulSoup (all existing)
**Storage**: In-memory caches (existing pattern) — no database changes
**Testing**: pytest with fixture-based tests, SQLite isolation via conftest.py
**Target Platform**: Lambda + Function URL (Docker/ECR), local dev via uvicorn
**Project Type**: Web service (server-rendered HTML with HTMX enhancement)
**Performance Goals**: Name matching adds ≤1s to schedule load (SC-006); personalized view within 5s (SC-001)
**Constraints**: All routes GET-only (CloudFront OAC); Lambda 60s timeout / 512MB memory; mobile-first (320px+, 200% zoom)
**Scale/Scope**: Single-competition view, ~60 events per competition, ~5-30 riders per start list

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Graceful Degradation | PASS | Feature is additive — schedule works without name input. Missing start lists shown as "unavailable" (FR-010). No JS required for core flow (GET form). |
| II. Testable Without External Dependencies | PASS | Rider parsing testable with plain-text fixtures. Name matching is pure logic. Route tests use TestClient. |
| III. Separation of Concerns | PASS | Parser extracts riders, predictor matches and computes timing, routes handle cookie/URL, templates render. No cross-boundary leakage. |
| IV. Minimal Dependencies | PASS | No new dependencies. Uses only `base64` from stdlib plus existing packages. |
| V. Operability | PASS | No new infrastructure. Logging can include matched rider count per request. |
| VI. Cost-Aware Growth | PASS | No new AWS services. Start list pages already fetched; parsing extends existing work. In-memory cache, no additional storage. |
| VII. Prediction Integrity | PASS | Per-heat timing derived from existing per_heat_duration — no new prediction source. SC-003 ensures no additional error. |
| VIII. Security & Data Minimization | JUSTIFIED | Racer name cookie stores voluntarily-entered public data (names on tracktiming.live). Cookie is httponly/secure/samesite. See Complexity Tracking. |

**POST-DESIGN RE-CHECK**: All gates remain satisfied. Name cookie is the only tension point with Principle VIII, justified and tracked below.

## Project Structure

### Documentation (this feature)

```text
specs/001-racer-schedule-lookup/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: research decisions
├── data-model.md        # Phase 1: entity definitions
├── quickstart.md        # Phase 1: build sequence
├── contracts/           # Phase 1: route contracts
│   └── routes.md
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
app/
├── main.py              # Routes: add ?r= param, /settings/racer-name, cookie handling
├── models.py            # Add RiderEntry, RiderMatch; extend Prediction, SchedulePrediction
├── parser.py            # Add parse_start_list_riders()
├── predictor.py         # Add _start_list_riders cache, rider matching, per-heat timing
├── fetcher.py           # No changes (start lists already fetched)
├── database.py          # No changes
├── disciplines.py       # No changes
└── templates/
    ├── schedule.html         # Add name input form, ?r= in hx-get
    └── _schedule_body.html   # Add .racer-match class, heat detail, messaging

static/
└── style.css            # Add .racer-match highlight styles (desktop + mobile)

tests/
├── test_parser.py       # Add TestParseStartListRiders
├── test_predictor.py    # Add rider matching and per-heat timing tests
└── test_main.py         # Add racer name route tests
```

**Structure Decision**: Existing single-project layout. All changes are extensions to existing modules — no new files except test additions. This follows the established pattern where each module has a single responsibility.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Principle VIII: `racer_name` cookie stores user-entered name | Required for returning-user auto-apply (FR-009, SC-007) — core multi-day competition UX. Names are publicly available on tracktiming.live. Cookie is httponly/secure/samesite. | No persistence: user re-enters name every visit, fails US4. localStorage: not server-accessible, breaks HTMX model. Session-only: doesn't survive browser close, fails multi-day use case. |
