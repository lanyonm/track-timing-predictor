# Tasks: Duration Data Import Scripts

**Input**: Design documents from `/specs/002-duration-data-import/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: Tests are included based on plan.md specifying pytest with fixture-based isolation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Create project structure, output directories, and package markers for CLI tools

- [ ] T001 Create `tools/` package directory with `tools/__init__.py`
- [ ] T002 Create `data/competitions/` output directory and append `data/` entry to existing `.gitignore`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core domain logic and model definitions that MUST be complete before either user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 Add `EventCategory` Pydantic model to `app/models.py` with fields: discipline, classification, gender, round, ride_number, omnium_part and validation rules per data-model.md
- [ ] T004 Implement compositional event name categorizer in `app/categorizer.py` — sequential strip-and-match parser extracting: special events, omnium part, ride number, round, classification (age-based + license-category + compound groups + para + age brackets), gender (including French: H/F/Hommes/Femmes/Dames), discipline (bilingual keyword table per research.md §1–§2)
- [ ] T005 [P] Write categorizer unit tests in `tests/test_categorizer.py` covering: English disciplines, French disciplines (research.md §2), compound classifications (Elite/Junior, Master C/D, Cat A/B), para classifications, omnium parts (standard I-IV + extended), round/ride extraction, gender detection (English + French), unresolvable residual text, special events (Break, End of Session, Medal Ceremonies, Pause), exhibition/novelty events (e.g., "Chariot Race", "Kids Race") mapping to a distinct `exhibition` discipline key
- [ ] T006 Add data model classes to `app/models.py`: `DurationRecord`, `UncategorizedEntry`, `CompetitionMeta`, `SessionReport`, `EventReport`, `CompetitionReport` per data-model.md with validation rules

**Checkpoint**: Categorizer and models ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Import Historical Results from tracktiming.live (Priority: P1) MVP

**Goal**: Run a CLI script with a competition ID to fetch schedule/result data from tracktiming.live, extract durations with structured categories, and produce a JSON report file

**Independent Test**: Run `python -m tools.extract_competition <competition_id>` against a known past competition and verify the output JSON contains correctly parsed durations, structured categories, and an uncategorized event summary

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T007 [P] [US1] Write extraction integration tests in `tests/test_extract_competition.py` using captured fixture data — test: schedule parsing produces EventReport entries with categories, duration extraction uses correct source priority (finish_time > generated_diff > heat_count), incomplete events are excluded from duration_observations, uncategorized events appear in uncategorized_summary with partial category info, multi-session competitions produce records for all sessions
- [ ] T008 [P] [US1] Write extraction edge case tests in `tests/test_extract_competition.py` — test: invalid/nonexistent competition ID produces clear actionable error message (no output file), session with no completed events is skipped with warning, duration > 120 min is flagged as outlier but included, French event names are categorized correctly, non-numeric competition ID produces clear error

### Implementation for User Story 1

- [ ] T009 [US1] Implement duration extraction helpers in `tools/extract_competition.py` — functions to: extract observed duration from a result page (Finish Time + changeover for bunch races, following `app/predictor.py` patterns for `_observed_durations`), extract generated-timestamp duration from consecutive result-page Generated timestamps (following `_generated_times` pattern), extract heat count from a start-list page (following `_heat_counts` pattern), select best duration by source priority (finish_time > generated_diff > heat_count), flag outlier durations > 120 min with warning. Reference files: `app/predictor.py`, `app/parser.py`, `app/disciplines.py`
- [ ] T010 [US1] Implement CLI orchestration in `tools/extract_competition.py` — argparse CLI accepting single competition ID, fetches schedule via existing `app/fetcher.py` pattern, parses via `app/parser.py`, iterates sessions and events, fetches result/start-list pages for completed events, calls duration extraction helpers (T009) for each event, applies categorizer to each event name, builds CompetitionReport with sessions/events/duration_observations/uncategorized_summary, writes JSON to `data/competitions/<competition_id>.json`, handles API errors with retry-once-then-exit (edge case spec), skips sessions with no completed events with warning. Reference files: `app/fetcher.py`, `app/models.py`, `app/categorizer.py`
- [ ] T011 [US1] Add test fixture for extraction in `tests/fixtures/` — capture or create a sample competition response fixture that covers: multiple sessions, completed/incomplete events, events with result pages, events with start-list pages showing heat counts, French event names, special events (Break, Medal Ceremonies)

**Checkpoint**: Extraction script produces valid JSON reports from tracktiming.live competitions

---

## Phase 4: User Story 2 — Load Imported Data into the App's Learning Database (Priority: P2)

**Goal**: Run a loader script that reads JSON report files and writes duration observations into the app's learning database with structured category support, enabling granular learned averages via cascading fallback

**Independent Test**: Create a sample JSON report, run `python -m tools.load_durations <file>` against a test database, verify `get_learned_duration()` returns expected averages at each granularity level

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T012 [P] [US2] Write SQLite schema migration tests in `tests/test_loader.py` — test: classification, gender, and per_heat_duration_minutes columns are added to event_durations table, cascading fallback index exists, natural key unique index exists, existing rows retain NULL for new columns
- [ ] T013 [P] [US2] Write loader integration tests in `tests/test_loader.py` — test: valid JSON file loads all records into SQLite, idempotent re-load does not duplicate records (natural key upsert), unrecognized discipline emits warning but stores record, cascading fallback returns correct averages at each level (discipline+classification+gender → discipline+classification → discipline+gender → discipline), invalid JSON file produces clear error message, record with missing required fields produces clear error message, per-heat duration is computed and stored when both duration_minutes and heat_count are present, per-heat duration is NULL when heat_count is absent
- [ ] T014 [P] [US2] Write DynamoDB loader tests in `tests/test_loader_dynamo.py` using boto3 Stubber (following pattern in `tests/test_database_dynamo.py`) — test: records written to correct aggregate key patterns (AGGREGATE#disc#class#gender, AGGREGATE#disc#class, AGGREGATE#disc##gender, AGGREGATE#disc), observation items (OBS#comp#sess#pos) prevent double-counting and include per_heat_duration_minutes when present, cascading fallback queries return most-specific level with ≥3 samples

### Implementation for User Story 2

- [ ] T015 [US2] Extend SQLite schema in `app/database.py` — add `classification`, `gender`, and `per_heat_duration_minutes` columns via ALTER TABLE (safe migration), create `idx_event_durations_category` index on (discipline, classification, gender), create `idx_event_durations_natural_key` unique index on (competition_id, session_id, event_position)
- [ ] T016 [US2] Implement cascading fallback query `get_learned_duration_cascading()` in `app/database.py` — accepts (discipline, classification, gender), queries 4 levels in order per research.md §4, returns first level with count ≥ min_learned_samples (3), falls through to existing `get_learned_duration()` behavior at broadest level
- [ ] T017 [US2] Extend DynamoDB backend in `app/database.py` — implement multi-level aggregate writes (4 AGGREGATE items per observation), implement OBS# observation items for idempotency checking, implement cascading fallback GetItem queries (4 levels, most specific first), extend override items to support structured keys (OVERRIDE#disc#class#gender through OVERRIDE#disc)
- [ ] T018 [US2] Implement loader script in `tools/load_durations.py` — argparse CLI accepting one or more JSON file paths, reads CompetitionReport JSON, validates duration records against constitution bounds (0.5× to 2.0× static default), computes per-heat duration (duration_minutes ÷ heat_count − changeover via `app/disciplines.get_changeover()`) when both duration and heat_count are present and stores in `per_heat_duration_minutes` column, writes to SQLite or DynamoDB based on DYNAMODB_TABLE env var, uses natural key (competition_id, session_id, event_position) for idempotent upsert, emits warning for unrecognized disciplines not in `app/disciplines.py`, reports summary: records loaded, skipped (duplicate), skipped (out of bounds), warnings
- [ ] T019 [US2] Update `tests/conftest.py` with fixture helpers for category testing — add factory helpers for creating DurationRecord instances (using models defined in T006) with structured categories, add temp DB setup that includes the new schema columns (classification, gender, per_heat_duration_minutes)

**Checkpoint**: Loader writes structured observations and the app returns granular learned averages via cascading fallback

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Validation, cleanup, and verification across both user stories

- [ ] T020 [P] Run quickstart.md validation — execute the quickstart workflow end-to-end against a known competition ID using only `--help` output and quickstart.md instructions (not source knowledge) to verify extract → inspect → load → verify pipeline works as documented, verify JSON output is human-readable (FR-010), note elapsed time to confirm < 2 minutes (SC-003)
- [ ] T021 Ensure all existing tests pass — run full `pytest` suite to verify no regressions in existing app behavior from schema changes or model additions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational (Phase 2) — no dependency on US2
- **User Story 2 (Phase 4)**: Depends on Foundational (Phase 2) — no dependency on US1 (reads JSON files, does not depend on extraction script)
- **Polish (Phase 5)**: Depends on both user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Phase 2. Depends on: `EventCategory` model (T003), categorizer (T004), data models (T006). Independent of US2.
- **User Story 2 (P2)**: Can start after Phase 2. Depends on: `EventCategory` model (T003), data models (T006). Independent of US1 (loader reads JSON files directly). However, end-to-end validation (T020) requires both.

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before services/scripts
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T005 (categorizer tests) can run in parallel with T006 (data models)
- T007 + T008 (US1 tests) can run in parallel
- T012 + T013 + T014 (US2 tests) can run in parallel
- US1 (Phase 3) and US2 (Phase 4) can be worked on in parallel after Phase 2
- T020 + T021 (Polish) can run in parallel

---

## Parallel Example: User Story 1

```bash
# Launch US1 tests in parallel:
Task: "Write extraction integration tests in tests/test_extract_competition.py"
Task: "Write extraction edge case tests in tests/test_extract_competition.py"

# Then implement sequentially:
Task: "Implement duration extraction helpers in tools/extract_competition.py"  # T009
Task: "Implement CLI orchestration in tools/extract_competition.py"           # T010
Task: "Add test fixture for extraction in tests/fixtures/"                    # T011
```

## Parallel Example: User Story 2

```bash
# Launch US2 tests in parallel:
Task: "Write SQLite schema migration tests in tests/test_loader.py"
Task: "Write loader integration tests in tests/test_loader.py"
Task: "Write DynamoDB loader tests in tests/test_loader_dynamo.py"

# Then implement sequentially (T015-T017 all modify app/database.py):
Task: "Extend SQLite schema in app/database.py"           # T015
Task: "Implement cascading fallback query in app/database.py"  # T016 (depends on T015)
Task: "Extend DynamoDB backend in app/database.py"        # T017 (depends on T015-T016, same file)
Task: "Implement loader script in tools/load_durations.py" # T018 (depends on T015-T017)
# T019 can run in parallel with T015-T017 (different file):
Task: "Update conftest.py with fixture helpers"            # T019
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T002)
2. Complete Phase 2: Foundational (T003–T006)
3. Complete Phase 3: User Story 1 (T007–T011)
4. **STOP and VALIDATE**: Run extraction against known competitions, verify JSON output
5. Deploy/demo if ready — developers can inspect extracted data and manually review categories

### Incremental Delivery

1. Setup + Foundational → Categorizer and models ready
2. Add User Story 1 → Extract competitions to JSON → Validate independently (MVP!)
3. Add User Story 2 → Load JSON into learning DB → Validate cascading fallback
4. Polish → End-to-end pipeline validation
5. Each story adds value without breaking previous stories
