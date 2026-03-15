# Implementation Plan: Duration Data Import Scripts

**Branch**: `002-duration-data-import` | **Date**: 2026-03-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-duration-data-import/spec.md`

## Summary

Create CLI scripts that extract historical competition data from tracktiming.live and load it into the app's learning database. The import script decomposes event names into structured category dimensions (discipline, classification, gender, round, modifiers) using a compositional parser, producing a JSON intermediate file per competition. The loader script reads these files and writes observations into an extended learning database schema that supports cascading granularity fallback: (discipline, classification, gender) → (discipline, classification) → (discipline, gender) → (discipline) → static default.

## Technical Context

**Language/Version**: Python 3.11+ (same as existing app)
**Primary Dependencies**: httpx (HTTP client, existing), beautifulsoup4 (HTML parsing, existing), boto3 (DynamoDB, existing), argparse (CLI, stdlib)
**Storage**: SQLite (local dev) + DynamoDB (production) — extended schema with classification + gender columns; JSON files for intermediate output
**Testing**: pytest with fixture-based isolation (existing pattern from conftest.py)
**Target Platform**: macOS/Linux CLI (developer/operator tool)
**Project Type**: CLI scripts as companion to existing FastAPI web service
**Performance Goals**: Single competition import < 2 minutes (SC-003)
**Constraints**: No new dependencies beyond stdlib (Constitution IV); reuse existing fetcher/parser; scripts run outside FastAPI lifecycle
**Scale/Scope**: 9 reference competitions identified; batch via shell loop

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Graceful Degradation | PASS | Schema extension adds nullable columns; existing app code continues to work with flat discipline queries. New cascading fallback is additive. |
| II. Testable Without External Dependencies | PASS | Import script tests use captured fixtures (existing pattern). Loader tests use temp SQLite DB (conftest.py pattern). No live API calls in tests. |
| III. Separation of Concerns | PASS | Categorizer is domain logic in `app/`. CLI scripts in `tools/`. Loader accesses DB directly (per spec FR-006), same dual-backend pattern as existing code. |
| IV. Minimal Dependencies | PASS | No new dependencies. Uses httpx, beautifulsoup4, boto3, argparse (stdlib). |
| V. Operability | PASS | Scripts produce clear error messages (FR-008). Uncategorized event summary enables systematic improvement (FR-011). |
| VI. Cost-Aware Growth | PASS | No infrastructure changes. DynamoDB items increase but use on-demand billing. |
| VII. Prediction Integrity | PASS | Loader applies validation bounds (0.5×–3× static default, per edge case spec). Outliers flagged in import output. |
| VIII. Security & Data Minimization | PASS | Processes only public competition data. No PII. |
| GET-only constraint | N/A | Scripts are CLI tools, not FastAPI routes. Loader accesses DB directly (FR-006). |

**Pre-Phase 0 gate: PASS** — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/002-duration-data-import/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
app/
├── categorizer.py       # NEW — compositional event name parser
├── models.py            # MODIFY — add EventCategory model
├── database.py          # MODIFY — extend schema + cascading fallback query
├── config.py            # unchanged
├── disciplines.py       # unchanged (categorizer replaces for import; app still uses for runtime)
├── ...

tools/
├── __init__.py          # NEW — package marker
├── extract_competition.py   # NEW — CLI: competition ID → JSON report
└── load_durations.py        # NEW — CLI: JSON reports → learning DB

tests/
├── test_categorizer.py  # NEW — categorizer unit tests
├── test_loader.py       # NEW — loader integration tests
├── fixtures/
│   └── sample-event-output.json  # existing fixture
├── conftest.py          # MODIFY — add fixture helpers for category testing
└── ...

data/
└── competitions/        # NEW — output directory for JSON reports (gitignored)
```

**Structure Decision**: Extend the existing single-project layout. Domain logic (categorizer) lives in `app/` for eventual use by the web app. CLI scripts live in a new `tools/` package, invoked via `python -m tools.extract_competition` / `python -m tools.load_durations`. Output data is gitignored.

## Complexity Tracking

No constitution violations requiring justification.

## Post-Design Constitution Re-check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Graceful Degradation | PASS | Cascading fallback ensures the app always finds a duration: most-specific category first, then progressively broader, then static default. Existing flat `get_learned_duration(discipline)` continues to work. |
| II. Testable Without External Dependencies | PASS | Categorizer tests use string inputs. Loader tests use temp SQLite. Extraction tests use captured fixture JSON. |
| III. Separation of Concerns | PASS | `categorizer.py` = event name parsing. `tools/extract_competition.py` = API fetching + report generation. `tools/load_durations.py` = DB writing. `database.py` = storage abstraction. Clean boundaries. |
| VII. Prediction Integrity | PASS | Loader validates duration bounds. Idempotent upsert prevents double-counting (FR-007). Cascading fallback only returns averages with ≥3 samples. |

**Post-design gate: PASS.**
