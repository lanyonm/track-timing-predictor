# Tasks: Racer Schedule Lookup

**Input**: Design documents from `/specs/001-racer-schedule-lookup/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Each implementation task that adds new functions or modifies behavior MUST include associated tests per constitution (Development Workflow). Tests follow existing patterns (pytest, fixture-based, captured fixtures). Test writing is embedded within implementation tasks, not broken out separately.

**Context budget**: Per constitution, each task must fit within a single LLM context window (~800 lines of reference code). Tasks that modify `app/predictor.py` (405 lines, growing) MUST NOT also require reading `tests/test_predictor.py` (862 lines). Rider-matching tests (T005–T008) go in `tests/test_rider_matching.py` (new file) — use `tests/conftest.py` as the only test pattern reference.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: Parser and model infrastructure that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T001 Add all new and modified models to `app/models.py`: `RiderEntry` (name, heat, normalized_tokens), `RiderMatch` (heat, heat_count, heat_predicted_start), `rider_match: RiderMatch | None` field on `Prediction`, `has_racer_match: bool` field (default `False`) on `SessionPrediction`, and `racer_name`/`match_count`/`events_without_start_lists`/`total_events` fields on `SchedulePrediction`
- [ ] T002 [P] Implement `parse_start_list_riders(html: str) -> list[RiderEntry]` in `app/parser.py` — parse "Heat N" sections and extract rider names with heat assignments using regex; normalize names to lowercase token frozensets. **Reference scoping**: use `parse_heat_count()` and its `TestParseHeatCount` test as the pattern reference (~50 lines each), not the full parser.py/test_parser.py files. **Tests**: add `TestParseStartListRiders` to `tests/test_parser.py` — test multi-heat parsing, single-heat, empty start list, and malformed HTML.
- [ ] T003 [P] Add `_start_list_riders` in-memory cache to `app/predictor.py` keyed by `(competition_id, session_id, position)` and implement `record_start_list_riders()` / `get_start_list_riders()` functions following the existing `_heat_counts` cache pattern. **Tests**: add cache round-trip tests to `tests/test_rider_matching.py` — verify `record_start_list_riders()` / `get_start_list_riders()` store and retrieve correctly, and that missing keys return empty list.
- [ ] T004 Call `parse_start_list_riders()` in `_fetch_start_lists()` in `app/main.py` alongside existing `parse_heat_count()` call, storing results via `record_start_list_riders()`. **Tests**: add integration test to `tests/test_main.py` verifying that `_fetch_start_lists()` populates `_start_list_riders` cache when start list HTML contains rider data.

**Checkpoint**: Rider names are parsed from start lists and cached. Models ready for matching logic.

---

## Phase 2: User Story 1 — Racer Looks Up Their Personal Schedule (Priority: P1) 🎯 MVP

**Goal**: A racer enters their name on the schedule page and sees their events highlighted with heat-specific predicted start times.

**Independent Test**: Enter a racer name on a competition schedule page → matching events are visually highlighted with heat detail shown for multi-heat events. Enter a non-matching name → "no matches" message displayed.

### Implementation for User Story 1

- [ ] T005 [P] [US1] Implement `_normalize_name(name: str) -> frozenset[str]` helper in `app/predictor.py` — split on whitespace, lowercase each token, return frozenset for order-independent comparison. **Tests**: add to `tests/test_rider_matching.py` (new file).
- [ ] T006 [US1] Implement `get_rider_match(competition_id, session_id, position, racer_name) -> RiderMatch | None` in `app/predictor.py` — normalize input name, compare token sets against cached `_start_list_riders` entries, populate `heat_count` from `get_heat_count()` (default 1 if not in cache), compute `heat_predicted_start` as `event_start + (heat - 1) × per_heat_duration` when matched. **Tests**: add to `tests/test_rider_matching.py`.
- [ ] T007 [US1] Modify `predict_session()` in `app/predictor.py` to accept optional `racer_name: str | None` parameter; call `get_rider_match()` for each event and populate `Prediction.rider_match`; count `events_without_start_lists`. **Reference scoping**: read only `predict_session()` and its direct callees in predictor.py, plus `Prediction`/`SchedulePrediction` in models.py. **Tests**: add to `tests/test_rider_matching.py`.
- [ ] T008 [US1] Modify `predict_schedule()` in `app/predictor.py` to accept and pass through `racer_name` parameter; populate `SchedulePrediction.racer_name`, `.match_count`, `.events_without_start_lists`, and `.total_events` (total count of non-special events across all sessions, needed for FR-010 "no data" messaging). Populate `SessionPrediction.has_racer_match` (defined in T001) — set to `True` when any event in the session has a `rider_match` (needed for FR-015 session auto-open). **Reference scoping**: read only `predict_schedule()` in predictor.py, plus `SchedulePrediction`/`SessionPrediction` in models.py. **Tests**: add to `tests/test_rider_matching.py`.
- [ ] T009 [US1] Implement `_resolve_racer_name(request: Request, r: str | None) -> str | None` in `app/main.py` — decode URL-safe Base64 `r` param if present, fall back to `racer_name` cookie, return plain text name or None. **Design note**: Implemented as a plain helper rather than a `Depends()` injectable per constitution III SHOULD. Justification: the function combines query param + cookie resolution with fallback logic specific to racer-name semantics — it's not a reusable cross-cutting concern. Called from exactly two routes (T011, T012).
- [ ] T010 [US1] Add `GET /settings/racer-name` route to `app/main.py` — accepts `event_id` (int) and `name` (str, optional) query params; if name present: Base64-encode, set `racer_name` cookie (httponly, secure, samesite=lax, max_age=31536000), redirect to `/schedule/{event_id}?r=<encoded>#schedule-container`; if name empty/absent: delete cookie, redirect to `/schedule/{event_id}`. The `#schedule-container` fragment scrolls past the header and form to the schedule content after submission (FR-016). **Dev note**: Match the `secure` attribute to the existing `use_learned` cookie pattern in `app/main.py`. If the existing cookie does not set `secure=True`, omit it here for consistency (uvicorn local dev runs on HTTP). If it does, keep it — the browser will still send the cookie on `localhost` in most modern browsers even over HTTP.
- [ ] T011 [US1] Modify `get_schedule()` in `app/main.py` to accept `r: str | None = Query(None)` param, call `_resolve_racer_name()`, pass resolved name to `predict_schedule()`, pass `racer_name` and `racer_encoded` (Base64) to template context; log racer name resolution outcome at INFO level using structured logging matching existing patterns in main.py: `logger.info("racer_name_resolved", extra={"source": "url|cookie|none", "racer_name": name, "match_count": count})`
- [ ] T012 [US1] Modify `refresh_schedule()` in `app/main.py` to accept `r: str | None = Query(None)` param, resolve name, pass to predictor, include match data in template context
- [ ] T013 [US1] Add name input form to `app/templates/schedule.html` as a new `<div class="racer-form-bar">` between the existing `.meta.meta-bar` `<p>` and `<div id="schedule-container">`. Contents: `<form>` (GET, action `/settings/racer-name`) with `<input type="hidden" name="event_id">`, `<input type="text" name="name" aria-label="Racer name" aria-describedby="racer-hint">` (placeholder "Your name to highlight your events", pre-filled with `racer_name`), `<button type="submit">Highlight</button>`, a "Clear" text link (`<a>`, `font-size: 0.82rem; color: #666`) linking to `/settings/racer-name?event_id={{ competition_id }}` — visible only when `racer_name` is set (`{% if racer_name %}`), and a `<small id="racer-hint" class="racer-form-hint">Enter your full name as shown on the start list</small>` hint below the input (always visible, styled `font-size: 0.78rem; color: #888`). Layout: flex row on desktop (input grows, button/link fixed, hint on a new line below via `flex-basis: 100%`); stacks full-width on mobile. See `docs/ui-recommendations-mockup.html` for visual reference.
- [ ] T014 [US1] Add `?r={{ racer_encoded }}` to the `hx-get` URL in `app/templates/schedule.html` when `racer_encoded` is set, so HTMX refresh preserves the racer name across polling cycles (FR-007)
- [ ] T015 [US1] Add `.racer-match` CSS class handling to event rows in `app/templates/_schedule_body.html`: (a) Add `racer-match` class and `aria-label="Your event"` to `<tr>` when `prediction.rider_match` is present. (b) Render heat badge immediately after the event name text in the first `<td>`: for multi-heat events (`prediction.rider_match.heat_count > 1`), render `<span class="badge racer-heat">Heat {{ prediction.rider_match.heat }}</span>`; for single-heat events, render `<span class="badge racer-heat">Racing</span>`. Badge is shown for all racer-matched events. (c) For multi-heat events only (`prediction.rider_match.heat_count > 1` and `prediction.rider_match.heat_predicted_start`), render `<span class="heat-time">Your heat: {{ prediction.rider_match.heat_predicted_start.strftime('%H:%M') }}</span>` on its own line below the duration value in the Est. Duration `<td>`. Single-heat matches show no heat time line (the event start time is the heat start time). (d) Ensure all existing action links (Results, Start List, Audit, Live) remain visible on racer-matched rows (FR-014). (e) Modify the `<details>` open logic: when `racer_name` is set, auto-open sessions that contain at least one racer-matched event even if the session is complete — change `{% if not sp.is_complete %}` to `{% if not sp.is_complete or sp.has_racer_match %}` (FR-015). This requires `has_racer_match` to be computed in the template context (see T008). See `docs/ui-recommendations-mockup.html` for visual reference.
- [ ] T016 [US1] Add messaging to `app/templates/_schedule_body.html` above the `{% for sp in schedule.sessions %}` loop. All message elements use `role="status"` for screen reader announcements. Four states per FR-010: (a) **Success**: when `racer_name` is set and `match_count > 0`: `<p class="racer-message racer-message-info" role="status">Found {{ match_count }} event(s) for "{{ racer_name }}"</p>`. (b) **Missing start lists** (shown alongside success or no-match): when `events_without_start_lists > 0`: `<p class="racer-message racer-message-info" role="status">{{ events_without_start_lists }} event(s) do not yet have start lists</p>`. (c) **No matches** (only when searchable data exists): when `racer_name` is set and `match_count == 0` and `events_without_start_lists < total_events`: `<p class="racer-message" role="status">No matching events found for "{{ racer_name }}"</p>` (amber warning). (d) **No data** (all start lists missing): when `racer_name` is set and `events_without_start_lists == total_events`: `<p class="racer-message racer-message-info" role="status">Start lists are not yet published — check back closer to the event</p>` (suppress "no matches" message). Note: `total_events` must be available in template context (see T008).
- [ ] T017 [US1] Add `.racer-match` highlight styles to `static/style.css`. Desktop rules: `tr.racer-match { background: #e8f0fe; border-left: 4px solid #1a73e8; }`. State interactions: `tr.racer-match.active { background: #fff3cd; border-left: 4px solid #1a73e8; }` (amber bg preserved); `tr.racer-match.status-completed { background: #e8f0fe; opacity: 0.45; }` + line-through; `tr.racer-match.status-upcoming { background: #e8f0fe; border-left: 4px solid #1a73e8; }`. Heat badge: `.badge.racer-heat { background: #1a73e8; color: #fff; font-size: 0.74rem; font-weight: 700; padding: 0.125rem 0.56rem; }`. Heat time: `.heat-time { display: block; font-size: 0.78rem; font-weight: 600; color: #1a53a0; margin-top: 0.125rem; }`. Messages: `.racer-message { font-size: 0.88rem; padding: 0.5rem 0.75rem; margin-bottom: 0.75rem; border-radius: 4px; background: #fff3cd; color: #856404; }`, `.racer-message-info { background: #dce8fc; color: #1a53a0; }`. Form bar: `.racer-form-bar { margin: 0.5rem 0 1rem; display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }`. Form hint: `.racer-form-hint { flex-basis: 100%; font-size: 0.78rem; color: #888; margin-top: -0.25rem; }`. Action button spacing: update `.evt-btn` margin to `0 4px 4px 0`. See `docs/ui-recommendations-mockup.html` and plan.md "UI Design Decisions" for full rules.

**Checkpoint**: User Story 1 fully functional — racer can enter name, see highlighted events with heat detail, HTMX refresh preserves state, no-match and missing-start-list messaging works.

---

## Phase 3: User Story 2 — Racer Checks Schedule on Mobile at the Venue (Priority: P2)

**Goal**: Personalized schedule is easy to read on mobile with highlighted events clearly standing out in the card layout.

**Independent Test**: View personalized schedule on 320px viewport and at 200% zoom — highlighted events are clearly distinguishable, name input is accessible, no horizontal scrolling.

### Implementation for User Story 2

- [ ] T018 [US2] Add mobile `.racer-match` styles in `static/style.css` within the `@media (max-width: 600px)` block. Rules must match desktop behavior: `tr.racer-match { border-left: 4px solid #1a73e8; background: #e8f0fe; }`, `tr.racer-match.status-upcoming { border-left: 4px solid #1a73e8; background: #e8f0fe; }`, `tr.racer-match.active { border-left: 4px solid #1a73e8; background: #fff3cd; }` (amber preserved). Heat time: `.heat-time { font-size: 0.75rem; }`. Form: `.racer-form-bar { flex-wrap: wrap; }` with input full-width and button flex-grow.
- [ ] T019 [US2] Add mobile form accessibility rules to `app/templates/schedule.html` and `static/style.css`: (a) Set `font-size: 16px` on the name input to prevent iOS auto-zoom on focus. (b) Set `min-height: 44px` on input and button for touch target compliance. (c) Verify form doesn't cause layout shift on focus (no width changes). Input and button should be full-width within the form bar on mobile.
- [ ] T020 [US2] Add 200% zoom compatibility rules to `static/style.css` and manually validate (FR-013): (a) Ensure `.racer-form-bar`, `.racer-match`, and `.badge.racer-heat` use relative units (rem/em) not fixed px for font sizes and padding. (b) Verify no horizontal overflow at 320px × 200% zoom (effective 160px logical viewport). (c) Manual validation: open schedule on Chrome at 200% zoom, confirm all racer elements are readable and functional. Document any issues found as follow-up tasks.

**Checkpoint**: Mobile experience validated — highlighted events clearly distinguishable at 320px and 200% zoom.

---

## Phase 4: User Story 3 — Racer Shares Their Personalized Schedule Link (Priority: P3)

**Goal**: Copying the page URL produces a shareable link with Base64-encoded racer name that loads the personalized schedule for any recipient.

**Independent Test**: After entering a name, copy the URL → open in a new browser/incognito → schedule loads with the racer's events highlighted without any input.

**Note**: Most of US3's mechanics are implemented in US1 (GET form redirect produces URL with `?r=` param, `_resolve_racer_name` decodes it). This phase covers verification and edge cases.

### Implementation for User Story 3

- [ ] T021 [US3] Write test verifying `_resolve_racer_name()` in `app/main.py` correctly prioritizes URL `?r=` parameter over cookie — a shared link should override the recipient's cookie-stored name. **Test file**: `tests/test_main.py`
- [ ] T022 [US3] Handle invalid Base64 in `_resolve_racer_name()` in `app/main.py` — if `r` param fails to decode (malformed Base64 or non-UTF8), gracefully fall back to cookie or None rather than returning a 500 error. **Test file**: `tests/test_main.py`

**Checkpoint**: Shared links work for any recipient; invalid URLs degrade gracefully.

---

## Phase 5: User Story 4 — Returning Racer is Recognized (Priority: P3)

**Goal**: A returning racer's name is auto-applied on page load — events highlighted immediately with no interaction needed (day 2 of multi-day competition).

**Independent Test**: Enter a name → navigate away → return to a schedule page → name is pre-filled and events are auto-highlighted without submitting the form.

**Note**: Cookie storage and auto-apply logic are implemented in US1 (`/settings/racer-name` sets cookie, `_resolve_racer_name` reads it). This phase covers the clearing flow and edge cases.

### Implementation for User Story 4

- [ ] T023 [US4] Write test for cookie auto-apply flow end-to-end in `app/main.py` — when `racer_name` cookie is present and no `?r=` param, schedule loads with events highlighted and name input pre-filled. **Test file**: `tests/test_main.py`
- [ ] T024 [US4] Implement clear-name flow: ensure the clear link/button in `app/templates/schedule.html` navigates to `/settings/racer-name?event_id={id}` (no `name` param) which deletes the cookie and redirects to the schedule without `?r=`, returning the schedule to default unhighlighted view (FR-012). **Test file**: `tests/test_main.py`

**Checkpoint**: Returning users see auto-personalized schedule; clearing name fully resets state.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T024b Add lightweight timing assertion to `tests/test_main.py`: time the `/schedule/{id}` endpoint with and without `?r=` param using TestClient, assert delta < 1s (SC-006). Not a strict benchmark — smoke test for regression.
- [ ] T025 [P] Update CLAUDE.md Architecture section to document racer name resolution flow (URL param → cookie → None) and `_start_list_riders` cache
- [ ] T026a Run full test suite (`pytest`) and report all failures with file paths and error summaries — diagnostic only, do not attempt fixes
- [ ] T026b Fix regressions identified by T026a — one commit per failing test file; if failures span multiple modules, handle each module separately to stay within context budget. Track fixes as inline notes in this task, not as separate task IDs.
- [ ] T027 Run quickstart.md end-to-end validation — verify all 5 phases of the build sequence produce expected results, including SC-001 (personalized schedule within 5s) and SC-006 (name matching adds ≤1s) performance spot-checks

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — can start immediately
- **User Story 1 (Phase 2)**: Depends on Phase 1 completion — BLOCKS all other stories
- **User Story 2 (Phase 3)**: Depends on US1 (needs highlight styles to refine for mobile)
- **User Story 3 (Phase 4)**: Depends on US1 (needs URL param flow to verify)
- **User Story 4 (Phase 5)**: Depends on US1 (needs cookie flow to verify)
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Depends on Foundational only — core MVP
- **User Story 2 (P2)**: Depends on US1 — mobile refinement of US1's output
- **User Story 3 (P3)**: Depends on US1 — verification and hardening of URL sharing
- **User Story 4 (P3)**: Depends on US1 — verification and hardening of cookie auto-apply
- **US3 and US4**: Independent of each other — can run in parallel after US1

### Within Each User Story

- Models before predictor logic
- Predictor before routes
- Routes before templates
- Templates before CSS

### Parallel Opportunities

- T002 (parser) and T003 (predictor cache) can run in parallel after T001 (models)
- T005 [P] can run in parallel with non-predictor tasks; T006 depends on T005 (sequential)
- US3 and US4 can run in parallel after US1 completes
- T018, T019, T020 (mobile CSS tasks) can run in parallel
- T025 (docs) can run in parallel with T026/T027

---

## Parallel Example: User Story 1

```bash
# Foundation — after T001 (models), run parser and cache in parallel:
Task: "Implement parse_start_list_riders() in app/parser.py"     # T002
Task: "Add _start_list_riders cache to app/predictor.py"         # T003

# After US1 complete — parallel US3 + US4:
Task: "Write test for URL ?r= priority over cookie"              # T021
Task: "Write test for cookie auto-apply flow"                    # T023
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Foundational (parser, models, cache)
2. Complete Phase 2: User Story 1 (matching, routes, templates, CSS)
3. **STOP and VALIDATE**: Test US1 independently — enter name, see highlights, refresh preserves, clear works
4. Deploy/demo if ready

### Incremental Delivery

1. Complete Foundational → parser and models ready
2. Add User Story 1 → Test independently → Deploy (MVP!)
3. Add User Story 2 → Mobile polish → Validate on device
4. Add User Stories 3 + 4 (parallel) → URL sharing + cookie auto-apply hardened
5. Polish → docs, full test suite, quickstart validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- US3 and US4 are lightweight because their core mechanisms are built into US1
