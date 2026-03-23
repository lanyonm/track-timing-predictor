# Tasks: Racer Palmares (Achievements)

**Input**: Design documents from `/specs/004-racer-palmares/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/routes.md

**Tests**: Included — comprehensive testing requested in spec and plan.

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Exact file paths included in descriptions

## Phase 1: Setup

**Purpose**: Configuration and infrastructure for palmares storage

- [ ] T001 Add `palmares_table` setting to `app/config.py` — new string field (empty = SQLite) following existing `dynamodb_table` pattern
- [ ] T002 [P] Add `PalmaresEntry` and `PalmaresCompetition` Pydantic models to `app/models.py` per data-model.md entity definitions
- [ ] T003 [P] Add palmares DynamoDB table (pk+sk design) to `cdk/track_timing_stack.py` — new `dynamodb.Table` with partition key `pk` and sort key `sk`, on-demand billing, same removal/PITR policy pattern as existing durations table. Pass `PALMARES_TABLE` env var to Lambda function.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Palmares database operations — MUST be complete before any user story

**CRITICAL**: No user story work can begin until this phase is complete

- [ ] T004 Create `app/palmares.py` SQLite backend — `init_palmares_db()` (CREATE TABLE with schema from data-model.md), `_save_entries_sqlite()` (INSERT OR IGNORE on natural key), `_get_palmares_sqlite()` (SELECT grouped by competition, reverse chronological), `_count_competition_sqlite()`, `_delete_competition_sqlite()`. Follow `database.py` patterns for connection handling.
- [ ] T005 Add DynamoDB backend to `app/palmares.py` — `_save_entries_dynamo()` (BatchWriteItem with PutItem, pk=`RACER#{name}`, sk=`COMP#{id}#S#{sid}#E#{pos}`), `_get_palmares_dynamo()` (Query on pk), `_count_competition_dynamo()` (Query with begins_with on sk), `_delete_competition_dynamo()` (Query + BatchWriteItem Delete). Follow `database.py` dual-backend dispatch pattern using `settings.palmares_table`.
- [ ] T006 Add public API functions to `app/palmares.py` — `save_palmares_entries()`, `get_palmares()`, `count_competition_palmares()`, `delete_competition_palmares()` that dispatch to SQLite or DynamoDB based on `settings.palmares_table`. Include structured JSON logging for save/delete operations.
- [ ] T007 Update `tests/conftest.py` — call `init_palmares_db()` in the `test_db` fixture, force `settings.palmares_table = ""` for SQLite mode
- [ ] T008 [P] Write `tests/test_palmares.py` — SQLite CRUD tests: save entries, verify no duplicates on re-save, get palmares grouped by competition (reverse chronological), count for specific competition, delete competition entries, empty palmares for unknown racer
- [ ] T009 [P] Write `tests/test_palmares_dynamo.py` — DynamoDB tests using moto `mock_aws()`: same operations as T008 but against DynamoDB backend. Follow existing `tests/test_database_dynamo.py` patterns.

**Checkpoint**: `app/palmares.py` fully tested with both backends. `pytest tests/test_palmares.py tests/test_palmares_dynamo.py` passes.

---

## Phase 3: User Story 1 - Automatic Palmares Collection (Priority: P1)

**Goal**: Schedule views automatically save matched timed events with audit links to the racer's palmares. Racer info area shows palmares count with link.

**Independent Test**: Set racer name → view a competition with completed timed events → verify entries saved in DB → verify "N of your timed events are in your palmares" message with link appears in racer info area.

### Tests for User Story 1

- [ ] T010 [P] [US1] Write palmares collection tests in `tests/test_palmares_routes.py` — test schedule route saves palmares entries for matched events with audit_url; test no entries for unidentified racer; test special events excluded; test no duplicates on re-view; test palmares_count passed to template context

### Implementation for User Story 1

- [ ] T011 [US1] Add palmares collection helper to `app/main.py` — new function `_collect_palmares_entries(schedule, competition_id, sessions)` that iterates `schedule.sessions[].event_predictions[]`, filters for predictions with `rider_match` AND `event.audit_url` AND NOT `event.is_special`, returns `list[PalmaresEntry]` with competition context (name from schedule title, `competition_date` = `datetime.now().date().isoformat()` per R-004). Call `init_palmares_db()` in the FastAPI lifespan alongside existing `init_db()`.
- [ ] T012 [US1] Integrate palmares saving into GET `/schedule/{event_id}` route in `app/main.py` — after `predict_schedule()`, if `racer_name` is set: wrap in `try/except` — call `_collect_palmares_entries()`, call `save_palmares_entries()`, call `count_competition_palmares()`. On any exception: `logger.warning("Palmares save failed", exc_info=True)`, set `palmares_count = 0`, and continue to render the schedule normally (Constitution I: palmares failures must not break the schedule). Add `palmares_count` to template context dict.
- [ ] T013 [US1] Integrate palmares saving into GET `/schedule/{event_id}/refresh` route in `app/main.py` — same logic as T012 for the HTMX refresh partial, including the `try/except` wrapper with warning log and `palmares_count = 0` fallback. Pass `palmares_count` to `_schedule_body.html` context.
- [ ] T014 [US1] Update `app/templates/_schedule_body.html` — add palmares count message in the racer messages block (after existing match count / next race messages). Render as: `<div class="racer-message racer-message-info">{{ palmares_count }} of your timed events are in your <a href="/palmares?r={{ racer_encoded }}">palmares</a></div>` when `palmares_count > 0`.

**Checkpoint**: View a competition while identified → palmares entries saved → count message appears. `pytest tests/test_palmares_routes.py` passes.

---

## Phase 4: User Story 2 - Palmares Profile Page (Priority: P2)

**Goal**: Dedicated profile page with card-based competition groupings, empty state, unidentified name form, and per-competition removal with confirmation.

**Independent Test**: Navigate to `/palmares` with cookie set → see competition cards with audit links. Navigate without identity → see name form. Remove a competition → card disappears after confirmation.

### Tests for User Story 2

- [ ] T015 [P] [US2] Write palmares page route tests in `tests/test_palmares_routes.py` — test identified racer with entries (cards rendered), test identified racer with no entries (empty state), test unidentified visitor (name form), test mobile viewport meta tag present
- [ ] T016 [P] [US2] Write removal route tests in `tests/test_palmares_routes.py` — test GET `/palmares/remove?competition_id=X` with cookie deletes entries and redirects, test removal without cookie returns 403, test removal via `r=` param only (no cookie) returns 403

### Implementation for User Story 2

- [ ] T017 [US2] Create `app/templates/palmares.html` — extends `base.html`. Three conditional states: (1) `racer_name` + `competitions` → card-based layout per wireframe, competition name as link to `/schedule/{id}?r={encoded}`, event rows with audit link + download icon placeholder; (2) `racer_name` + empty `competitions` → empty state card with guidance and link to `/`; (3) no `racer_name` → name input form card (GET form to `/palmares` with `name` param, input has `required` attribute to prevent blank submissions). Include `is_owner` conditional for remove action visibility.
- [ ] T018 [US2] Add GET `/palmares` route to `app/main.py` — resolve racer name from `r=` param or cookie or `name` query param (for form submission). Detect `is_owner` = cookie `racer_name` matches resolved name. If name from form submission: set cookie, redirect to `/palmares?r={encoded}` (303). Otherwise: call `get_palmares(racer_name)`, render template with `competitions`, `racer_name`, `racer_encoded`, `is_owner`, `base_url`.
- [ ] T019 [US2] Add GET `/palmares/remove` route to `app/main.py` — require `racer_name` cookie (return 403 if absent or if identity resolved only from `r=` param). Accept `competition_id` query param. Call `delete_competition_palmares()`. Redirect to `/palmares` (303).
- [ ] T020 [US2] Update `app/templates/base.html` — add "Palmares" link in the header nav, alongside existing site title link. Link to `/palmares` (no `r=` param — uses cookie).
- [ ] T021 [US2] Add palmares CSS to `static/style.css` — `.palmares-card` (background #fff, border 1px solid #ddd, border-radius 6px, padding 1rem, margin-bottom 1rem), `.palmares-card-header` (display flex, justify-content space-between, font-weight 600), `.palmares-event` (padding 0.3rem 0, display flex, justify-content space-between), `.palmares-empty` (text-align center, padding 2rem, color #666), `.palmares-name-form` (max-width 420px, margin 1rem auto — reuse `.entry-form` pattern), mobile media query for 360px+ stacking.
- [ ] T022 [US2] Add remove button styles and confirmation JavaScript to `app/templates/palmares.html` — `.palmares-remove` button styled subtly (small, text-only, color #999, hover #c00). Inline `<script>` for confirmation: `onclick="return confirm('Remove this competition from your palmares?')"` on the remove link. Works without JS (link navigates directly; confirmation is progressive enhancement).

**Checkpoint**: `/palmares` works for all three states. Removal prompts and deletes. `pytest` passes.

---

## Phase 5: User Story 3 - Shareable Palmares Link (Priority: P3)

**Goal**: Copy Link button with clipboard copy and visual confirmation. Shared links are read-only (no cookie set, no remove action visible).

**Independent Test**: Copy the shareable URL → open in incognito → see full palmares read-only (no remove buttons). Verify cookie is NOT set on recipient device.

### Tests for User Story 3

- [ ] T023 [P] [US3] Write sharing tests in `tests/test_palmares_routes.py` — test shared link (`r=` param, no cookie) renders palmares but `is_owner` is False; test cookie is NOT set in response for shared link; test remove action not visible in HTML response for shared link (assert "palmares-remove" not in response)

### Implementation for User Story 3

- [ ] T024 [US3] Add share section to `app/templates/palmares.html` — visible only when `racer_name` is set and `competitions` is not empty. "Copy Link" button with `id="copy-link-btn"`, data attribute `data-url="{{ share_url }}"`. Description text: "Anyone with this link can see your event history." Below button: `<span id="copy-feedback" class="copy-feedback" hidden>Copied!</span>`.
- [ ] T025 [US3] Add share URL to `/palmares` route context in `app/main.py` — compute `share_url` as full URL: `{request.url.scheme}://{request.url.netloc}/palmares?r={racer_encoded}`. Pass to template.
- [ ] T026 [US3] Add Copy Link JavaScript to `app/templates/palmares.html` — inline `<script>`: click handler on `#copy-link-btn` calls `navigator.clipboard.writeText(dataset.url)`, shows `#copy-feedback` span for 2 seconds, then hides. Graceful fallback: if clipboard API unavailable, select text in a hidden input and exec copy.
- [ ] T027 [US3] Add share section CSS to `static/style.css` — `.palmares-share` (margin-bottom 1.5rem, padding 0.75rem, background #f0f4f8, border-radius 6px), `.copy-btn` (background #1a73e8, color #fff, border none, padding 6px 16px, border-radius 4px, cursor pointer), `.copy-feedback` (color #155724, font-size 0.85rem, margin-left 8px)

**Checkpoint**: Share button copies URL. Shared link opens read-only. `pytest` passes.

---

## Phase 6: User Story 4 - CSV Export (Priority: P4)

**Goal**: Per-event CSV download of individual audit result data with loading/error states.

**Independent Test**: Click download icon on palmares page → CSV file downloads with racer's data only. Click for unavailable page → warning icon with error message.

### Tests for User Story 4

- [ ] T028 [P] [US4] Write `tests/test_audit_parser.py` — test `parse_audit_riders()` with `tests/fixtures/audit-pursuit-26008.html` fixture (verify all 5 riders extracted with correct names, bib numbers, heat assignments, and row data); test `filter_rider_data()` matching by normalized name; test `format_csv()` produces valid CSV with correct headers (Heat, Dist, Time, Rank, Lap, Lap_Rank, Sect, Sect_Rank)
- [ ] T029 [P] [US4] Write export route tests in `tests/test_palmares_routes.py` — test GET `/palmares/export?audit_url=...&r=...` returns CSV with Content-Disposition header; test unavailable audit page returns 502; test no matching racer returns CSV with headers only; test missing racer identity returns 400; test SSRF protection: absolute URL (`audit_url=https://evil.com/...`) returns 400, path traversal (`audit_url=../../../etc/passwd`) returns 400, only `results/`-prefixed relative paths accepted

### Implementation for User Story 4

- [ ] T030 [US4] Create `app/audit_parser.py` — `parse_audit_riders(html: str) -> list[dict]`: use BeautifulSoup to find all `div.divcontainer` sections, extract rider name from `<p>` elements (strip bib prefix: split on " - ", take second part), extract heat from preceding `<h3>` heading, extract table rows as list of dicts with keys (heat, dist, time, rank, lap, lap_rank, sect, sect_rank). `filter_rider_data(riders, racer_name)`: normalize both names via `normalize_rider_name()`, return matching entries. `format_csv(data, event_name)`: use stdlib `csv.writer` with StringIO, write header row + data rows, return string.
- [ ] T031 [US4] Add GET `/palmares/export` route to `app/main.py` — resolve racer name (400 if absent), accept `audit_url` query param (400 if absent). **SSRF protection**: validate `audit_url` starts with `results/` and does not contain `://` or `..`; reject with 400 if invalid. Fetch audit page via `client.get(audit_url)` using shared httpx client (relative URL resolved against base_url). On httpx error: return JSON `{"error": "..."}` with 502 status. Parse HTML, filter for racer, format CSV. Return `Response(content=csv_str, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=\"{event_name}-{racer}.csv\""})`. If no matching rows: return CSV with headers only + `X-Palmares-Notice: no-matching-data` header.
- [ ] T032 [US4] Add download icon and loading state styles to `static/style.css` — `.palmares-download` (display inline-block, cursor pointer, text-decoration none, font-size 0.85rem, color #1a73e8), `.palmares-download.loading` (pointer-events none, opacity 0.6), `.palmares-download.error` (color #dc3545), `.palmares-spinner` (inline CSS animation for rotation)
- [ ] T033 [US4] Add CSV download JavaScript to `app/templates/palmares.html` — inline `<script>`: click handler on `.palmares-download` icons. On click: swap icon to spinner (add `.loading` class), fetch `/palmares/export?audit_url={url}&r={encoded}` via JS fetch API. On success: create Blob from response, trigger download via temporary `<a>` element. On error: swap to warning icon with title tooltip from error message. Revert to download icon after 10 seconds on error.
- [ ] T034 [US4] Update event rows in `app/templates/palmares.html` — add download icon (`<a class="palmares-download" data-audit-url="{{ entry.audit_url }}" title="Export CSV">⬇</a>`) next to each audit link in the competition card event rows

**Checkpoint**: CSV downloads work. Error states show warning icon. `pytest tests/test_audit_parser.py tests/test_palmares_routes.py` passes.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Comprehensive testing, documentation updates, and final validation

- [ ] T035 Write integration tests in `tests/test_palmares_routes.py` — end-to-end flow: set racer name via cookie → GET schedule (verify palmares saved) → GET palmares page (verify cards) → GET export (verify CSV) → GET remove (verify deleted)
- [ ] T036 [P] Write edge case tests in `tests/test_palmares_routes.py` — no audit URLs → no entries; special events excluded; duplicate views → no duplicate entries; unidentified visitor → name form; competition removal → entries gone
- [ ] T037 [P] Write shared link integration test in `tests/test_palmares_routes.py` — GET `/palmares?r={encoded}` without cookie → verify response has no `Set-Cookie` header for `racer_name`, verify `palmares-remove` not in HTML, verify palmares data IS displayed
- [ ] T038 Update `CLAUDE.md` — add palmares routes to route table (GET /palmares, GET /palmares/export, GET /palmares/remove), add `PALMARES_TABLE` to environment variables table, add palmares module descriptions to Architecture section, document DynamoDB pk+sk key design, add palmares test patterns to Key Patterns section, update Active Technologies and Recent Changes sections
- [ ] T039 [P] Extend health check in `app/main.py` GET `/health` — add palmares table connectivity check (degraded status on failure, not blocking). Follow existing health check pattern with per-component status.
- [ ] T040 Run full test suite and validate — `pytest` passes with all new and existing tests. Manually verify quickstart.md scenarios work locally. Validate performance against success criteria: populate test DB with 50 entries across 10 competitions, verify `/palmares` renders within 3s (SC-002); verify `/palmares/export` completes within 5s (SC-004).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on T001 + T002 from Setup — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational (Phase 2)
- **US2 (Phase 4)**: Depends on Foundational (Phase 2). Independent of US1 (uses same `get_palmares()` API), but data is more interesting if US1 is done first.
- **US3 (Phase 5)**: Depends on US2 (builds on palmares template)
- **US4 (Phase 6)**: Depends on US2 (download icons in palmares template). Independent of US1 and US3.
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: After Foundational → no dependencies on other stories
- **US2 (P2)**: After Foundational → no dependencies on other stories (can populate test data directly)
- **US3 (P3)**: After US2 → extends palmares template with share features
- **US4 (P4)**: After US2 → extends palmares template with download icons

### Within Each User Story

- Tests written first, expected to fail before implementation
- Models/data layer before route handlers
- Route handlers before template changes
- CSS changes can parallel template work

### Parallel Opportunities

- T002 + T003 (models + CDK) in parallel during Setup
- T004 + T005 (SQLite + DynamoDB backends) — different code paths, can run in parallel if palmares.py is structured with clear separation, but simpler to do sequentially since same file
- T008 + T009 (SQLite tests + DynamoDB tests) in parallel
- T010 + T015 + T016 (tests across stories) in parallel once Foundational is done
- T023, T028, T029 (US3 + US4 tests) in parallel
- T038 + T039 (CLAUDE.md + health check) in parallel

---

## Parallel Example: Foundational Phase

```bash
# After T004-T006 (palmares.py) is complete, launch tests in parallel:
Task: T008 "Write SQLite CRUD tests in tests/test_palmares.py"
Task: T009 "Write DynamoDB tests in tests/test_palmares_dynamo.py"
```

## Parallel Example: User Story Tests

```bash
# Once Foundational is done, launch all story test tasks in parallel:
Task: T010 "US1 palmares collection tests"
Task: T015 "US2 palmares page route tests"
Task: T016 "US2 removal route tests"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 2: Foundational (T004-T009)
3. Complete Phase 3: US1 Auto Collection (T010-T014)
4. **STOP and VALIDATE**: Schedule views save palmares entries, count message appears
5. Deploy if ready — palmares data accumulates even without profile page

### Incremental Delivery

1. Setup + Foundational → Data layer ready
2. Add US1 (auto collection) → Data accumulates during schedule views → **MVP**
3. Add US2 (profile page) → Racers can view their achievements → Deploy
4. Add US3 (sharing) → Racers can share with coaches/friends → Deploy
5. Add US4 (CSV export) → Power users can export data → Deploy
6. Polish → Full test coverage + documentation → Final PR

### Sequential Solo Strategy

With a single developer (LLM agent), execute phases sequentially:
1. Setup → Foundational → US1 → US2 → US3 → US4 → Polish
2. Each phase is a natural commit point
3. Stop at any checkpoint to validate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story
- Each user story is independently testable after Foundational phase
- All routes are GET (CloudFront OAC constraint)
- Audit page fixture already committed: `tests/fixtures/audit-pursuit-26008.html`
- Commit after each phase or logical task group
- Constitution compliance verified in plan.md — no violations
