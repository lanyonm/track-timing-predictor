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

- [x] T001 Create `tools/` package directory with `tools/__init__.py`
- [x] T002 Create `data/competitions/` output directory and append `data/` entry to existing `.gitignore`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core domain logic and model definitions that MUST be complete before either user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 Add `EventCategory` Pydantic model to `app/models.py` with fields: discipline, classification, gender, round, ride_number, omnium_part and validation rules per data-model.md
- [x] T004 Implement compositional event name categorizer in `app/categorizer.py` — sequential strip-and-match parser extracting: special events (must output `break_` with trailing underscore to match `disciplines.py` key), omnium part, ride number, round, classification (age-based + license-category + compound groups + para + age brackets), gender (including French: H/F/Hommes/Femmes/Dames), discipline (bilingual keyword table per research.md §1–§2). **Post-extraction mapping step**: after all dimensions are extracted, resolve distance-variant discipline keys using (discipline, classification, gender) — e.g., pursuit + elite + men → `pursuit_4k`, pursuit + junior + women → `pursuit_2k`, time_trial + women → `time_trial_500`, time_trial + men → `time_trial_kilo`. This mapping is required so that discipline keys align with existing `DEFAULT_DURATIONS` and `PER_HEAT_DURATIONS` in `app/disciplines.py`. When classification/gender are insufficient to determine the variant, use the generic key (e.g., `pursuit`). Reference files: `app/disciplines.py` (lines 5–102 for keyword list and default durations)
- [x] T005 [P] Write categorizer unit tests in `tests/test_categorizer.py` covering: English disciplines, French disciplines (research.md §2), compound classifications (Elite/Junior, Master C/D, Cat A/B), para classifications, omnium parts (standard I-IV + extended), round/ride extraction, gender detection (English + French), unresolvable residual text, special events (Break → `break_` with underscore, End of Session → `end_of_session`, Medal Ceremonies, Pause), exhibition/novelty events (e.g., "Chariot Race", "Kids Race") mapping to a distinct `exhibition` discipline key, **distance-variant key resolution** (e.g., "Elite Men Individual Pursuit" → discipline=`pursuit_4k`, "Junior Women Individual Pursuit" → discipline=`pursuit_2k`, "Women 500m Time Trial" → discipline=`time_trial_500`, "Men Kilo Time Trial" → discipline=`time_trial_kilo`, generic fallback when classification/gender insufficient to determine variant)
- [x] T006 Add data model classes to `app/models.py`: `DurationRecord`, `UncategorizedEntry`, `CompetitionMeta`, `SessionReport`, `EventReport`, `CompetitionReport` per data-model.md with validation rules

**Checkpoint**: Categorizer and models ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Import Historical Results from tracktiming.live (Priority: P1) MVP

**Goal**: Run a CLI script with a competition ID to fetch schedule/result data from tracktiming.live, extract durations with structured categories, and produce a JSON report file

**Independent Test**: Run `python -m tools.extract_competition <competition_id>` against a known past competition and verify the output JSON contains correctly parsed durations, structured categories, and an uncategorized event summary

### Fixtures for User Story 1

- [x] T007 [US1] Capture test fixtures for extraction in `tests/fixtures/` — **capture** (not synthesize — Constitution II requires real-world fixtures) the following from tracktiming.live: (a) a Jaxon API schedule response from a competition with multiple sessions, completed/incomplete events, French event names, and special events (Break, Medal Ceremonies) — e.g., a Quebec competition per research.md §2; (b) at least one result page HTML from a bunch race (containing `Finish Time:` field) and one from a non-bunch event (containing `Generated:` timestamp but no Finish Time); (c) a start-list page HTML showing heat counts (supplement the existing `start-list-sample.html` if needed). These fixtures enable T008/T009 tests to exercise the full extraction pipeline without live API calls.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T008 [P] [US1] Write extraction integration tests in `tests/test_extract_competition.py` using captured fixture data (T007) — test: schedule parsing produces EventReport entries with categories, duration extraction uses correct source priority (finish_time > generated_diff > heat_count), incomplete events are excluded from duration_observations, uncategorized events appear in uncategorized_summary with partial category info, multi-session competitions produce records for all sessions. Follow existing test patterns: use `Path(__file__).parent / "fixtures"` for fixture paths, patch `app/fetcher` functions to prevent live API calls (following `tests/test_main.py` mock pattern), clear predictor in-memory caches with an `autouse` fixture if extraction helpers touch them
- [x] T009 [US1] Write extraction edge case tests in `tests/test_extract_competition.py` — test: invalid/nonexistent competition ID produces clear actionable error message (no output file), session with no completed events is skipped with warning, duration > 120 min is flagged as outlier but included, French event names are categorized correctly, non-numeric competition ID produces clear error. **Depends on T008** (same file — T008 creates the file and shared fixtures; T009 adds edge case test classes)

### Implementation for User Story 1

- [x] T010 [US1] Implement duration extraction helpers in `tools/extract_competition.py` — functions to: extract observed duration from a result page (call `app/parser.parse_finish_time()` + `app/disciplines.get_changeover()` arithmetic — do NOT call `predictor.record_observed_duration()` as that persists to the live DB), extract generated-timestamp duration from consecutive result-page Generated timestamps (call `app/parser.parse_generated_time()`, diff consecutive pairs within the same session only — never diff across session boundaries, **apply the [0.5×, 2.0×] plausibility filter** against the static default before accepting a generated-diff value, attribute diff to event `i` not `i+1`, last event in a session cannot have a generated-diff), extract heat count from a start-list page (call `app/parser.parse_heat_count()`, compute total duration as `heat_count × get_per_heat_duration(discipline) + get_changeover(discipline)`), select best duration by source priority (finish_time > generated_diff > heat_count), flag outlier durations > 120 min with warning. Reference: `app/predictor.py` lines 52–73 and 276–310 (cache patterns), `app/parser.py` lines 181–256 (parse functions), `app/disciplines.py` lines 66–151 (durations and changeovers)
- [x] T011 [US1] Implement CLI orchestration in `tools/extract_competition.py` — argparse CLI accepting single competition ID with descriptive `--help` text (SC-004). **Async strategy**: all `app/fetcher.py` functions are `async def`; the CLI MUST use a single `asyncio.run(main_async())` wrapper so all fetches share one event loop (do NOT call `asyncio.run()` per fetch). Fetches schedule via `app/fetcher.fetch_initial_layout()`, parses via `app/parser.parse_schedule()`, iterates sessions and events, fetches result/start-list pages for completed events via `app/fetcher.fetch_result_html()` / `fetch_start_list_html()` (verify that `event.result_url` / `start_list_url` paths are relative, not full URLs — `fetcher.py` prepends `settings.tracktiming_base_url`), calls duration extraction helpers (T010) for each event, applies categorizer to each event name, builds CompetitionReport with sessions/events/duration_observations/uncategorized_summary, writes JSON to `data/competitions/<competition_id>.json`, handles API errors with retry-once-then-exit (edge case spec), skips sessions with no completed events with warning. Reference files: `app/fetcher.py`, `app/models.py`, `app/categorizer.py`

**Checkpoint**: Extraction script produces valid JSON reports from tracktiming.live competitions

---

## Phase 4: User Story 2 — Load Imported Data into the App's Learning Database (Priority: P2)

**Goal**: Run a loader script that reads JSON report files and writes duration observations into the app's learning database with structured category support, enabling granular learned averages via cascading fallback

**Independent Test**: Create a sample JSON report, run `python -m tools.load_durations <file>` against a test database, verify `get_learned_duration()` returns expected averages at each granularity level

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T012 [P] [US2] Write SQLite schema migration tests in `tests/test_loader.py` — test: classification, gender, and per_heat_duration_minutes columns are added to event_durations table, cascading fallback index exists, natural key unique index exists, existing rows retain NULL for new columns. **Note**: migration tests need their own isolated SQLite file (not the shared session-scoped `test_db` fixture) — create a function-scoped fixture that builds the OLD schema (without new columns), inserts sample rows, then runs the migration and verifies the new columns appear with NULLs for existing rows
- [x] T013 [P] [US2] Write loader integration tests in `tests/test_loader.py` — test: valid JSON file loads all records into SQLite, idempotent re-load does not duplicate records (natural key upsert), unrecognized discipline emits warning but stores record, cascading fallback returns correct averages at each level (discipline+classification+gender → discipline+classification → discipline+gender → discipline), invalid JSON file produces clear error message, record with missing required fields produces clear error message, per-heat duration is computed and stored when both duration_minutes and heat_count are present, per-heat duration is NULL when heat_count is absent
- [x] T014 [P] [US2] Write DynamoDB loader tests in `tests/test_loader_dynamo.py` using moto `mock_aws` (following the `dynamo_env` / `dynamo_table` fixture pattern in `tests/test_database_dynamo.py`, NOT boto3 Stubber) — test: records written to correct aggregate key patterns (AGGREGATE#disc#class#gender, AGGREGATE#disc#class, AGGREGATE#disc##gender, AGGREGATE#disc), observation items (OBS#comp#sess#pos) prevent double-counting and include per_heat_duration_minutes when present, cascading fallback queries return most-specific level with ≥3 samples. Reproduce the `monkeypatch.setattr(settings, "dynamodb_table", TABLE_NAME)` + `mock_aws()` setup from the existing test file

### Implementation for User Story 2

- [x] T015 [US2] Extend SQLite schema and add `record_duration_structured()` in `app/database.py` — (a) update the `_SCHEMA` `CREATE TABLE` statement to include classification, gender, and per_heat_duration_minutes columns so that `init_db()` on a fresh DB creates the full schema (needed for tests); (b) add ALTER TABLE migrations for production DBs created before this feature; (c) create `idx_event_durations_category` index on (discipline, classification, gender); (d) create `idx_event_durations_natural_key` unique index on (competition_id, session_id, event_position); (e) add a new `record_duration_structured(discipline, classification, gender, ...)` function for structured writes (per spec assumption: existing `record_duration()` signature must remain stable for the live app path)
- [x] T016 [US2] Implement cascading fallback query `get_learned_duration_cascading()` in `app/database.py` — accepts (discipline, classification, gender), queries 4 levels in order per research.md §4, returns first level with count ≥ min_learned_samples (3), falls through to existing `get_learned_duration()` behavior at broadest level. **Important**: Level 1 must check `discipline_overrides` the same way existing `get_learned_duration()` does (avoid divergent override behavior). When classification or gender is None, higher-specificity levels (which use `WHERE classification = ?` with NULL) will never match in SQL — this is correct behavior that naturally falls through to broader levels, but document it in code comments
- [x] T017 [US2] Extend DynamoDB backend in `app/database.py` — implement multi-level aggregate writes (4 AGGREGATE items per observation), implement OBS# observation items for idempotency checking, implement cascading fallback GetItem queries (4 levels, most specific first), extend override items to support structured keys (OVERRIDE#disc#class#gender through OVERRIDE#disc)
- [x] T018 [US2] Implement loader script in `tools/load_durations.py` — argparse CLI accepting one or more JSON file paths, reads CompetitionReport JSON, validates duration records against constitution bounds (0.5× to 2.0× static default), computes per-heat duration (duration_minutes ÷ heat_count − changeover via `app/disciplines.get_changeover()`) when both duration and heat_count are present and stores in `per_heat_duration_minutes` column, writes to SQLite or DynamoDB based on DYNAMODB_TABLE env var, uses natural key (competition_id, session_id, event_position) for idempotent upsert, emits warning for unrecognized disciplines not in `app/disciplines.py`, reports summary: records loaded, skipped (duplicate), skipped (out of bounds), warnings
- [x] T019 [P] [US2] Update `tests/conftest.py` with fixture helpers for category testing — (a) add factory helpers for creating DurationRecord instances (using models defined in T006) with structured categories; (b) promote the moto `dynamo_env` / `dynamo_table` fixture from `tests/test_database_dynamo.py` to `conftest.py` so both `test_database_dynamo.py` and `test_loader_dynamo.py` (T014) can share it; (c) add a function-scoped `old_schema_db` fixture for T012 migration tests that creates a SQLite DB with the pre-migration schema (without new columns). Note: the `_SCHEMA` CREATE TABLE update is handled in T015 (same file as other database.py changes)

**Checkpoint**: Loader writes structured observations and the app returns granular learned averages via cascading fallback

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Validation, cleanup, and verification across both user stories

- [x] T020 [P] Write extract-to-load round-trip test in `tests/test_pipeline.py` — using captured fixture data (T011), run extraction logic to produce a `CompetitionReport`, serialize to JSON, deserialize via loader, write to test SQLite DB, then verify: (a) `get_learned_duration_cascading()` returns expected averages at each granularity level, (b) records with `classification=None` and `gender="open"` (defaults) correctly fall through to Level 1, (c) `per_heat_duration_minutes` is computed correctly for records with heat counts, (d) outlier-flagged records from the extractor (duration > 120 min) are rejected by the loader's 2.0× bounds validation, (e) uncategorized events from the extractor don't appear in `duration_observations` fed to the loader. This test closes the contract gap between extraction output and loader input.
- [x] T021 [P] Run quickstart.md validation — execute the quickstart workflow end-to-end against a known competition ID using only `--help` output and quickstart.md instructions (not source knowledge) to verify extract → inspect → load → verify pipeline works as documented, verify JSON output is human-readable (FR-010), note elapsed time to confirm < 2 minutes (SC-003)
- [x] T022 Ensure all existing tests pass — run full `pytest` suite to verify no regressions in existing app behavior from schema changes or model additions
- [x] T023 Update `CLAUDE.md` with architectural changes — add: `tools/` package description and invocation pattern (`python -m tools.extract_competition`, `python -m tools.load_durations`), `app/categorizer.py` module and its role, extended database schema (classification, gender, per_heat_duration_minutes columns), updated learning mechanism description (cascading fallback: discipline+classification+gender → discipline+classification → discipline+gender → discipline → static default), `record_duration_structured()` function, `data/competitions/` output directory. **Constitution mandate**: "CLAUDE.md MUST be kept current with architectural changes"

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational (Phase 2) — no dependency on US2
- **User Story 2 (Phase 4)**: Depends on Foundational (Phase 2) — no dependency on US1 (reads JSON files, does not depend on extraction script)
- **Polish (Phase 5)**: Depends on both user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Phase 2. Depends on: `EventCategory` model (T003), categorizer (T004), data models (T006). Independent of US2. Within US1: T007 (fixtures) → T008/T009 (tests) → T010/T011 (implementation).
- **User Story 2 (P2)**: Can start after Phase 2. Depends on: `EventCategory` model (T003), data models (T006). Independent of US1 (loader reads JSON files directly). However, end-to-end validation (T020) requires both.

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before services/scripts
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T005 (categorizer tests) can run in parallel with T006 (data models)
- T007 (capture fixtures) first, then T008 creates `test_extract_competition.py`, then T009 adds to it (sequential — same file)
- T012 + T013 + T014 (US2 tests) can run in parallel
- US1 (Phase 3) and US2 (Phase 4) can be worked on in parallel after Phase 2
- T020 + T021 (Polish) can run in parallel; T022 (regression) and T023 (CLAUDE.md) after

---

## Parallel Example: User Story 1

```bash
# Capture fixtures first (prerequisite for tests):
Task: "Capture test fixtures for extraction in tests/fixtures/"                   # T007

# US1 tests sequentially (same file):
Task: "Write extraction integration tests in tests/test_extract_competition.py"   # T008
Task: "Write extraction edge case tests in tests/test_extract_competition.py"     # T009 (adds to T008's file)

# Then implement sequentially:
Task: "Implement duration extraction helpers in tools/extract_competition.py"     # T010
Task: "Implement CLI orchestration in tools/extract_competition.py"              # T011
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
3. Complete Phase 3: User Story 1 (T007–T011: fixtures → tests → implementation)
4. **STOP and VALIDATE**: Run extraction against known competitions, verify JSON output
5. Deploy/demo if ready — developers can inspect extracted data and manually review categories

### Incremental Delivery

1. Setup + Foundational → Categorizer and models ready
2. Add User Story 1 → Extract competitions to JSON → Validate independently (MVP!)
3. Add User Story 2 → Load JSON into learning DB → Validate cascading fallback
4. Polish → End-to-end pipeline validation
5. Each story adds value without breaking previous stories
