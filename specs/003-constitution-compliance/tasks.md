# Tasks: Constitution Compliance Fixes

**Input**: Design documents from `/specs/003-constitution-compliance/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Test adaptation tasks are included because the foundational refactoring (BaseSettings, DI, shared client) requires updating the existing test infrastructure to maintain FR-011 (all tests pass).

**Organization**: Tasks are grouped by dependency order. US1/US2 (template changes) are independent and can run in parallel with the foundational backend work. US3/US4/US5/US6 are tightly coupled in main.py and combined into one coordinated refactor phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup

**Purpose**: Dependency changes that must be committed before any code changes

- [x] T001 Update requirements.txt: add `pydantic-settings` after the `jinja2` line; remove the `python-multipart>=0.0.9` line (FR-010). Run `pip install -r requirements.txt` to verify installation succeeds.

**Checkpoint**: `pip install -r requirements.txt` completes without errors

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Migrate config, refactor fetcher signatures, and add health check function — these are prerequisites for the main.py refactor in Phase 5

**Warning**: Phase 5 cannot begin until T002 and T003 are complete

- [x] T002 Migrate app/config.py from `dataclass` + `os.getenv()` to `pydantic_settings.BaseSettings`. Keep identical field names, types, and defaults. Use `model_config = SettingsConfigDict(env_prefix="")` so `DB_PATH`, `DYNAMODB_TABLE`, and `AWS_REGION` env vars are read automatically. Remove `os` import and `os.getenv()` calls. Add a `get_settings()` function that returns a `Settings()` instance (to be used as a FastAPI `Depends()` provider). Keep the module-level `settings = Settings()` singleton for backward compatibility during transition. Reference: research.md R-001, data-model.md Configuration Entity table for field specs.

- [x] T003 [P] Refactor app/fetcher.py: change all 5 async functions (`fetch_initial_layout`, `fetch_result_html`, `fetch_start_list_html`, `fetch_live_html`, `fetch_refresh`) to accept `client: httpx.AsyncClient` and `base_url: str` as parameters instead of creating per-call `async with httpx.AsyncClient()` blocks and importing `settings.tracktiming_base_url`. Remove the `from app.config import settings` import. Remove `_event_url()` helper (inline as f-string using `base_url` param). Keep `_HEADERS` dict as module-level constant. Reference: research.md R-004.

- [x] T004 [P] Add async `check_health()` function to app/database.py. Since `get_db()` and boto3 calls are synchronous, wrap the blocking check in `asyncio.to_thread()` so callers can use `asyncio.wait_for()` for timeout. For SQLite backend (`settings.dynamodb_table` is empty): execute `SELECT 1` via `get_db()` context manager. For DynamoDB backend: call `_get_dynamodb_table().describe()` (or equivalent boto3 describe_table). Return `{"status": "healthy"}` on success or `{"status": "degraded", "detail": "<summary>"}` on exception. The `detail` value MUST be a sanitized human-readable summary (e.g., "SQLite connection failed", "DynamoDB table not accessible"), NOT raw exception messages or tracebacks. Catch all exceptions (including boto3 `ClientError`) and return degraded status — never raise from health check. Reference: research.md R-005, contracts/health-endpoint.md.

**Checkpoint**: `pytest tests/test_predictor.py tests/test_parser.py tests/test_categorizer.py` should still pass (these don't import from main.py directly). Full test suite may fail until Phase 5+6 adapt main.py and conftest.py.

---

## Phase 3: US1 - App Works Without JavaScript (Priority: P1)

**Goal**: Landing page form submits correctly without JavaScript via standard HTML form action + server-side redirect

**Independent Test**: Disable JS in browser, load `/`, enter an Event ID, submit — should navigate to `/schedule/{id}`

- [x] T005 [P] [US1] Update app/templates/index.html: remove the `onsubmit="event.preventDefault(); ..."` handler from the `<form>` tag. Add `action="/schedule"` and `method="get"` attributes. The form will now submit as `GET /schedule?event_id=26008` which the redirect route added in T008 handles. Keep the `id`, `name`, `pattern`, `inputmode`, `required`, and `placeholder` attributes on the input unchanged. Reference: research.md R-002.

**Checkpoint**: Template updated; full US1 validation after T008 adds the redirect route in main.py

---

## Phase 4: US2 - Learned Duration Toggle Without JavaScript (Priority: P2)

**Goal**: The learned-duration toggle on the schedule page works without JavaScript

**Independent Test**: Disable JS, load a schedule page, toggle the checkbox and submit — setting should take effect on page reload

- [x] T007 [P] [US2] Update app/templates/schedule.html: wrap the learned-duration checkbox in a `<form action="/settings/use-learned" method="get">`. Add `<input type="hidden" name="event_id" value="{{ competition_id }}">`. Set the checkbox attributes to `name="use_learned" value="on"` — no hidden field is needed for `use_learned` because when the checkbox is unchecked, browsers omit the field entirely, and the `/settings/use-learned` route already defaults `use_learned` to `"off"` via `Query("off")`. Add a `<noscript><button type="submit">Apply</button></noscript>` element so no-JS users can submit the form. Keep the existing `onchange` JS handler for the JS-enabled instant-toggle experience. Reference: research.md R-003.

**Checkpoint**: With JS disabled, the toggle submits via form and takes effect after page reload

---

## Phase 5: US3+US4+US5 - Config Validation + Shared Client + DI (Priority: P3/P4/P5)

**Goal**: Main.py refactor — lifespan creates/closes shared httpx.AsyncClient, routes use Depends() for settings and client, fetcher calls pass the shared client

**Independent Test**: Start app, make schedule requests, verify single client reused; set invalid env var, verify startup fails with validation error; verify tests can override dependencies

**Depends on**: T002 (config.py migrated), T003 (fetcher.py refactored)

- [x] T008 [US1/US3/US4/US5] Refactor app/main.py — this is the core integration task:

  **Redirect route (US1)**:
  - Add `GET /schedule` redirect route: accepts `event_id: int = Query(...)`, returns `RedirectResponse(url=f"/schedule/{event_id}", status_code=303)`. Place this route BEFORE the existing `GET /schedule/{event_id}` route so FastAPI matches the no-path-param version first. Reference: contracts/schedule-redirect.md.

  **Lifespan changes (US4)**:
  - Import `httpx` at top level for `Limits`
  - In the `lifespan()` async context manager, create `httpx.AsyncClient(timeout=15.0, limits=httpx.Limits(max_connections=50))` and store on `app.state.http_client`
  - After `yield`, call `await app.state.http_client.aclose()`
  - Keep `init_db()` call in lifespan

  **Depends providers (US5)**:
  - Import `get_settings` from `app.config`
  - Add `def get_http_client(request: Request) -> httpx.AsyncClient:` that returns `request.app.state.http_client`
  - Update route handlers (`get_schedule`, `refresh_schedule`, `toggle_use_learned`, `set_racer_name`, `default_durations`, `learned_durations`, `health`) to accept `settings: Settings = Depends(get_settings)` where they use settings, and `client: httpx.AsyncClient = Depends(get_http_client)` where they make upstream calls
  - Remove the top-level `from app.config import settings` import (replace with injected param in each handler)

  **Fetcher integration (US4+US5)**:
  - Update `_fetch_live_heats`, `_fetch_start_lists`, `_fetch_result_pages` to accept `client: httpx.AsyncClient` and `base_url: str` params
  - Update their internal `fetch_one()` closures to pass `client` and `base_url` to the refactored fetcher functions
  - Update the `asyncio.gather()` calls in `get_schedule` and `refresh_schedule` to pass `client` and `settings.tracktiming_base_url`

  **Config validation (US3)**:
  - BaseSettings automatically validates types at construction. No additional code needed — invalid env vars cause `ValidationError` at startup. This is the US3 acceptance criteria met by design.

  Reference files to read: app/main.py, app/config.py (migrated), app/fetcher.py (refactored). Total ~445 lines. Reference: research.md R-001, R-004.

**Checkpoint**: App starts, schedule requests work with shared client, `pytest` may still fail pending Phase 6

---

## Phase 6: US6 - Health Endpoint (Priority: P6) + Test Adaptation

**Goal**: Health endpoint checks database connectivity; tests adapted for BaseSettings + DI

**Depends on**: T004 (check_health in database.py), T008 (main.py refactored with DI)

- [x] T009 [US6] Update the health endpoint in app/main.py to call `check_health()` from `app.database`. Since `check_health()` is now async (wraps sync DB calls via `asyncio.to_thread()`), use `asyncio.wait_for(check_health(), timeout=5.0)` for FR-009 compliance. Return the per-component JSON response per contracts/health-endpoint.md. If `asyncio.TimeoutError` occurs, return `{"status": "degraded", "components": {"database": {"status": "degraded", "detail": "Health check timed out"}}}`. Import `check_health` from `app.database`.

- [x] T010 Update tests/conftest.py for BaseSettings compatibility: the `test_db` fixture should construct a `Settings()` instance after setting env vars via `monkeypatch` (or directly mutating since BaseSettings is non-frozen by default). Use `app.dependency_overrides[get_settings]` to inject the test settings. Keep the same temp DB isolation pattern. Import `get_settings` from `app.config` and `app` from `app.main`. Reference: research.md R-006.

- [x] T011 Update tests/test_main.py: add tests for the new health endpoint contract (healthy response with SQLite, degraded response when DB is unreachable). Add unit tests for `check_health()` function: SQLite healthy path (valid temp DB), SQLite degraded path (invalid/missing DB path), and DynamoDB degraded path (mock boto3 failure via moto or mock). Add test for `GET /schedule?event_id=26008` redirect route (verify 303 redirect to `/schedule/26008`). Update any existing tests that depend on the old health response format `{"status": "ok"}`. Ensure all route tests work with the DI pattern.

**Checkpoint**: `pytest` passes with all tests green

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation updates and final verification

- [x] T012 Update CLAUDE.md: add `pydantic-settings` to dependencies, document shared httpx.AsyncClient pattern in Architecture section, update health endpoint description in Routes table to note per-component status response, document `GET /schedule` redirect route in Routes table, remove `python-multipart` from any dependency lists. Update the config.py description to mention BaseSettings.

- [x] T013 Final verification: run `pytest` and confirm all tests pass (FR-011). Manually verify the 7 constitution violations from plans/constitution-observations.md are resolved (SC-001). Check that the app starts without env vars set (FR-004). Verify schedule predictions, racer highlighting, and live polling are unchanged (FR-012).

**Checkpoint**: All 7 constitution violations resolved, full test suite passes, CLAUDE.md current

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 for pydantic-settings install
- **US1 (Phase 3)**: T005 (template) is independent; redirect route is part of T008 (main.py refactor)
- **US2 (Phase 4)**: Independent — template-only, parallel with Phase 2
- **US3+US4+US5 (Phase 5)**: Depends on T002 + T003 from Phase 2
- **US6 + Tests (Phase 6)**: Depends on T004 + T008 from Phases 2+5
- **Polish (Phase 7)**: Depends on all prior phases

### User Story Dependencies

- **US1 (P1)**: T005 independent (template); redirect route merged into T008 (main.py)
- **US2 (P2)**: Fully independent — template-only change
- **US3 (P3)**: Delivered by T002 (BaseSettings validation) + T008 (integration)
- **US4 (P4)**: Delivered by T003 (fetcher) + T008 (lifespan)
- **US5 (P5)**: Delivered by T008 (Depends injection)
- **US6 (P6)**: Delivered by T004 (check_health) + T009 (endpoint)
- **US7 (P7)**: Delivered by T001 (remove from requirements.txt)

### Parallel Opportunities

```text
Phase 2 parallel group:
  T002 (config.py) ─┐
  T003 (fetcher.py) ─┼─→ T008 (main.py refactor)
  T004 (database.py) ┘

Template parallel group (independent of Phase 2):
  T005 (index.html) ─→ can start anytime
  T007 (schedule.html) ─→ can start anytime
```

---

## Parallel Example: Phases 2-4

```bash
# These 5 tasks can all run in parallel (different files, no dependencies):
Task T002: "Migrate config.py to BaseSettings"
Task T003: "Refactor fetcher.py to accept client params"
Task T004: "Add check_health() to database.py"
Task T005: "Update index.html no-JS form"
Task T007: "Update schedule.html no-JS toggle"

# Then sequentially:
Task T008: "Refactor main.py (redirect route + lifespan + DI + client)"
Task T009: "Update health endpoint in main.py"
Task T010: "Update conftest.py"
Task T011: "Update test_main.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 — No-JS Fixes)

1. Complete Phase 1: Setup (T001)
2. Complete T005 + T007 (template fixes — no backend changes needed)
3. **STOP and VALIDATE**: Test no-JS behavior in browser
4. These deliver immediate user-facing value with minimal risk

### Incremental Delivery

1. T001 → Setup ready
2. T005 + T007 → No-JS template fixes deployed (US1 partial + US2 complete)
3. T002 + T003 + T004 → Foundational backend ready
4. T008 → Main.py refactored (US1 complete + US3 + US4 + US5)
5. T009 → Health endpoint upgraded (US6)
6. T010 + T011 → Tests adapted
7. T012 + T013 → Documentation and final verification

### Parallel Execution Strategy

With subagent-driven development:
1. Launch T002, T003, T004, T005, T007 in parallel (5 different files)
2. Once T002+T003 complete → launch T008 (main.py refactor)
3. Once T004+T008 complete → launch T009, T010, T011
4. Once all complete → T012, T013

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- T008 is the largest task (~445 lines of reference code) but fits within the ~800 line context window constraint
- The redirect route (US1) is included in T008 to avoid merge conflicts with the main.py refactor
- US7 (remove python-multipart) has no dedicated implementation task — it's fully handled by T001
- Commit after each phase completion
