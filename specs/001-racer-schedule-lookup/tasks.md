# Tasks: Racer Schedule Lookup

**Input**: Design documents from `/specs/001-racer-schedule-lookup/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Each implementation task that adds new functions or modifies behavior MUST include associated tests per constitution (Development Workflow). Tests follow existing patterns (pytest, fixture-based, captured fixtures). Test writing is embedded within implementation tasks, not broken out separately.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: Parser and model infrastructure that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T001 Add all new and modified models to `app/models.py`: `RiderEntry` (name, heat, normalized_tokens), `RiderMatch` (heat, heat_predicted_start), `rider_match: RiderMatch | None` field on `Prediction`, and `racer_name`/`match_count`/`events_without_start_lists` fields on `SchedulePrediction`
- [ ] T002 [P] Implement `parse_start_list_riders(html: str) -> list[RiderEntry]` in `app/parser.py` — parse "Heat N" sections and extract rider names with heat assignments using regex; normalize names to lowercase token frozensets
- [ ] T003 [P] Add `_start_list_riders` in-memory cache to `app/predictor.py` keyed by `(competition_id, session_id, position)` and implement `record_start_list_riders()` / `get_start_list_riders()` functions following the existing `_heat_counts` cache pattern
- [ ] T004 Call `parse_start_list_riders()` in `_fetch_start_lists()` in `app/main.py` alongside existing `parse_heat_count()` call, storing results via `record_start_list_riders()`

**Checkpoint**: Rider names are parsed from start lists and cached. Models ready for matching logic.

---

## Phase 2: User Story 1 — Racer Looks Up Their Personal Schedule (Priority: P1) 🎯 MVP

**Goal**: A racer enters their name on the schedule page and sees their events highlighted with heat-specific predicted start times.

**Independent Test**: Enter a racer name on a competition schedule page → matching events are visually highlighted with heat detail shown for multi-heat events. Enter a non-matching name → "no matches" message displayed.

### Implementation for User Story 1

- [ ] T005 [US1] Implement `_normalize_name(name: str) -> frozenset[str]` helper in `app/predictor.py` — split on whitespace, lowercase each token, return frozenset for order-independent comparison
- [ ] T006 [US1] Implement `get_rider_match(competition_id, session_id, position, racer_name) -> RiderMatch | None` in `app/predictor.py` — normalize input name, compare token sets against cached `_start_list_riders` entries, compute `heat_predicted_start` as `event_start + (heat - 1) × per_heat_duration` when matched
- [ ] T007 [US1] Modify `predict_session()` in `app/predictor.py` to accept optional `racer_name: str | None` parameter; call `get_rider_match()` for each event and populate `Prediction.rider_match`; count `events_without_start_lists`
- [ ] T008 [US1] Modify `predict_schedule()` in `app/predictor.py` to accept and pass through `racer_name` parameter; populate `SchedulePrediction.racer_name`, `.match_count`, and `.events_without_start_lists`
- [ ] T009 [US1] Implement `_resolve_racer_name(request: Request, r: str | None) -> str | None` in `app/main.py` — decode URL-safe Base64 `r` param if present, fall back to `racer_name` cookie, return plain text name or None
- [ ] T010 [US1] Add `GET /settings/racer-name` route to `app/main.py` — accepts `event_id` (int) and `name` (str, optional) query params; if name present: Base64-encode, set `racer_name` cookie (httponly, secure, samesite=lax, max_age=31536000), redirect to `/schedule/{event_id}?r=<encoded>`; if name empty/absent: delete cookie, redirect to `/schedule/{event_id}`
- [ ] T011 [US1] Modify `get_schedule()` in `app/main.py` to accept `r: str | None = Query(None)` param, call `_resolve_racer_name()`, pass resolved name to `predict_schedule()`, pass `racer_name` and `racer_encoded` (Base64) to template context; log racer name resolution outcome at INFO level (source: URL/cookie/none, match count)
- [ ] T012 [US1] Modify `refresh_schedule()` in `app/main.py` to accept `r: str | None = Query(None)` param, resolve name, pass to predictor, include match data in template context
- [ ] T013 [US1] Add name input form to `app/templates/schedule.html` in the meta bar area — GET form targeting `/settings/racer-name` with hidden `event_id` field, text input with `name="name"` and placeholder "Your name to highlight your events", pre-filled with `racer_name` if present, submit button, and clear link
- [ ] T014 [US1] Add `?r={{ racer_encoded }}` to the `hx-get` URL in `app/templates/schedule.html` when `racer_encoded` is set, so HTMX refresh preserves the racer name across polling cycles (FR-007)
- [ ] T015 [US1] Add `.racer-match` CSS class handling to event rows in `app/templates/_schedule_body.html` — apply class when `prediction.rider_match` is present; display heat number badge (e.g., "Heat 3") for multi-heat events; display per-heat predicted start time from `prediction.rider_match.heat_predicted_start`
- [ ] T016 [US1] Add messaging to `app/templates/_schedule_body.html` — "No matching events found" when `racer_name` is set but `match_count == 0`; "N events do not yet have start lists" when `events_without_start_lists > 0` (FR-010)
- [ ] T017 [US1] Add `.racer-match` highlight styles to `static/style.css` — distinct background color and left accent border for matched event rows; ensure completed events retain completed styling with racer match as secondary indicator; ensure highlight is visible in both desktop table and mobile card layouts

**Checkpoint**: User Story 1 fully functional — racer can enter name, see highlighted events with heat detail, HTMX refresh preserves state, no-match and missing-start-list messaging works.

---

## Phase 3: User Story 2 — Racer Checks Schedule on Mobile at the Venue (Priority: P2)

**Goal**: Personalized schedule is easy to read on mobile with highlighted events clearly standing out in the card layout.

**Independent Test**: View personalized schedule on 320px viewport and at 200% zoom — highlighted events are clearly distinguishable, name input is accessible, no horizontal scrolling.

### Implementation for User Story 2

- [ ] T018 [US2] Refine mobile `.racer-match` styles in `static/style.css` within the `@media (max-width: 600px)` block — ensure highlight accent is visible in card layout, heat badge is readable, per-heat time doesn't cause overflow
- [ ] T019 [US2] Ensure name input form in `app/templates/schedule.html` is fully accessible on mobile — input and button should be full-width within the meta bar, touch targets meet minimum size (44px), form doesn't cause layout shift
- [ ] T020 [US2] Validate layout at 200% browser zoom in `static/style.css` — ensure `.racer-match` highlights, heat badges, and name input remain functional and readable (FR-013)

**Checkpoint**: Mobile experience validated — highlighted events clearly distinguishable at 320px and 200% zoom.

---

## Phase 4: User Story 3 — Racer Shares Their Personalized Schedule Link (Priority: P3)

**Goal**: Copying the page URL produces a shareable link with Base64-encoded racer name that loads the personalized schedule for any recipient.

**Independent Test**: After entering a name, copy the URL → open in a new browser/incognito → schedule loads with the racer's events highlighted without any input.

**Note**: Most of US3's mechanics are implemented in US1 (GET form redirect produces URL with `?r=` param, `_resolve_racer_name` decodes it). This phase covers verification and edge cases.

### Implementation for User Story 3

- [ ] T021 [US3] Write test verifying `_resolve_racer_name()` in `app/main.py` correctly prioritizes URL `?r=` parameter over cookie — a shared link should override the recipient's cookie-stored name
- [ ] T022 [US3] Handle invalid Base64 in `_resolve_racer_name()` in `app/main.py` — if `r` param fails to decode (malformed Base64 or non-UTF8), gracefully fall back to cookie or None rather than returning a 500 error

**Checkpoint**: Shared links work for any recipient; invalid URLs degrade gracefully.

---

## Phase 5: User Story 4 — Returning Racer is Recognized (Priority: P3)

**Goal**: A returning racer's name is auto-applied on page load — events highlighted immediately with no interaction needed (day 2 of multi-day competition).

**Independent Test**: Enter a name → navigate away → return to a schedule page → name is pre-filled and events are auto-highlighted without submitting the form.

**Note**: Cookie storage and auto-apply logic are implemented in US1 (`/settings/racer-name` sets cookie, `_resolve_racer_name` reads it). This phase covers the clearing flow and edge cases.

### Implementation for User Story 4

- [ ] T023 [US4] Write test for cookie auto-apply flow end-to-end in `app/main.py` — when `racer_name` cookie is present and no `?r=` param, schedule loads with events highlighted and name input pre-filled
- [ ] T024 [US4] Implement clear-name flow: ensure the clear link/button in `app/templates/schedule.html` navigates to `/settings/racer-name?event_id={id}` (no `name` param) which deletes the cookie and redirects to the schedule without `?r=`, returning the schedule to default unhighlighted view (FR-012)

**Checkpoint**: Returning users see auto-personalized schedule; clearing name fully resets state.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T025 [P] Update CLAUDE.md Architecture section to document racer name resolution flow (URL param → cookie → None) and `_start_list_riders` cache
- [ ] T026 Run full test suite (`pytest`) and fix any regressions introduced by model/predictor/route changes
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
- T005 and T006 (predictor helpers) are sequential
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
