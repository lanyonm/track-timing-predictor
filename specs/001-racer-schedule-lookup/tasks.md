# Tasks: Racer Schedule Lookup

**Input**: Design documents from `/specs/001-racer-schedule-lookup/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/routes.md, quickstart.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Fixture capture and shared data models

- [X] T001 [P] Fetch a real start list page from tracktiming.live and save to `tests/fixtures/start-list-sample.html`. URL pattern: `POST https://tracktiming.live/eventpage.php?EventId={id}` with payload `jxnfun=getStartList&jxnr=1&jxnargs[]={event_index}`. Find a suitable competition by browsing tracktiming.live for an event with a "Start List" button on a multi-heat discipline (e.g., Team Sprint, Keirin, Individual Pursuit). The response body contains HTML with `Heat N` headers and rider lines. Save the HTML content (not the JSON wrapper). If no live competition is available, use EventId `26008` (historic) or create a realistic synthetic fixture based on the format in `research.md` (`Heat N` headers followed by lines like `212  PITTARD Charlie`). Include at least 2 heats with 3+ riders each, including one rider name with an apostrophe (e.g., "O'Brien"). This fixture is the contract between the parser and the upstream data source (constitution: captured real-world fixtures required for parsing functions).
- [X] T002 [P] Add new and extended models to `app/models.py`. New models: `RiderEntry` (fields: `name: str`, `heat: int`, `normalized_tokens: frozenset[str]`); `RiderMatch` (fields: `heat: int`, `heat_count: int`, `heat_predicted_start: datetime | None`). Extend `Prediction` with `rider_match: RiderMatch | None = None`. Extend `SessionPrediction` with `has_racer_match: bool = False`. Extend `SchedulePrediction` with: `racer_name: str | None = None`, `match_count: int = 0`, `events_without_start_lists: int = 0`, `total_events: int = 0`, `next_race_event_name: str | None = None`, `next_race_heat: int | None = None`, `next_race_heat_count: int | None = None`, `next_race_time: datetime | None = None`, `next_race_is_active: bool = False`. See `data-model.md` for full field descriptions. **Pydantic note**: `normalized_tokens` should use `model_config = ConfigDict(arbitrary_types_allowed=True)` with `frozenset[str]` as the type, or compute it via a `model_validator(mode='after')` that sets `self.normalized_tokens = frozenset(name_tokens)` after normalization. Since `RiderEntry` is immutable once created, either approach works.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Parser and predictor core — MUST be complete before any user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Implement `parse_start_list_riders(html: str) -> list[RiderEntry]` in `app/parser.py`. Parse plain-text start list HTML: identify `Heat N` headers using `\bHeat\s+\d+\b` regex (consistent with existing `parse_heat_count()`), extract rider lines below each header (format: `NNN  LASTNAME Firstname`), create `RiderEntry` for each with `normalized_tokens` as `frozenset` of lowercased whitespace-split name tokens (excluding bib number). **Name normalization**: before tokenizing, apply Unicode NFKD normalization and strip non-ASCII characters (`unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')`), then remove apostrophes, hyphens, and periods. This ensures names like "O'Brien" and "Müller" normalize to matchable tokens. Return empty list if no heats/riders found (defensive parsing per constitution). **Reference**: read `app/parser.py` (236 lines) + `app/models.py` for `RiderEntry`.
  Fixture: tests/fixtures/start-list-sample.html
- [X] T004 [P] Write tests for `parse_start_list_riders()` in `tests/test_parser.py`. Add `TestParseStartListRiders` class. Test cases: (a) multi-heat extraction (verify correct heat assignment per rider), (b) single-heat event, (c) empty/malformed HTML returns empty list, (d) `normalized_tokens` are lowercased and order-independent, (e) name with apostrophe ("O'Brien") normalizes correctly, (f) name with diacritics ("Müller") normalizes to ASCII equivalent. Load fixture with `Path("tests/fixtures/start-list-sample.html").read_text()`.
  Fixture: tests/fixtures/start-list-sample.html
- [X] T005 Add rider matching infrastructure to `app/predictor.py`: (a) `_start_list_riders: dict[tuple, list[RiderEntry]]` cache (keyed by `(competition_id, session_id, position)`, same pattern as `_heat_counts`). (b) `record_start_list_riders(competition_id, session_id, position, riders)` to populate cache. (c) `get_rider_match(competition_id, session_id, position, racer_name, event_start, discipline) -> RiderMatch | None` — normalize and tokenize `racer_name` using the same Unicode NFKD + punctuation stripping as `parse_start_list_riders()` (T003), then compare frozenset against each `RiderEntry.normalized_tokens` in cache. On match: get `heat_count` from `get_heat_count()` (default 1), compute `heat_predicted_start = event_start + (heat - 1) × per_heat_duration` (from `get_per_heat_duration(discipline)`), return `RiderMatch`. **Reference**: read `app/predictor.py` (405 lines) focusing on cache patterns and `get_heat_count()`/`get_per_heat_duration()`, plus `app/models.py`.
- [X] T006 Modify `predict_session()` and `predict_schedule()` in `app/predictor.py` to support racer matching. **New signatures**: `predict_session(competition_id: int, session: Session, now: datetime | None = None, racer_name: str | None = None, use_learned: bool = True) -> SessionPrediction` and `predict_schedule(competition_id: int, sessions: list[Session], now: datetime | None = None, racer_name: str | None = None, use_learned: bool = True) -> SchedulePrediction`. (a) `predict_session()`: for each event, call `get_rider_match()` and set `prediction.rider_match`; count `events_without_start_lists` (events where `_start_list_riders` cache has no entry AND `event.is_special is False`). (b) `predict_schedule()`: pass `racer_name` through to each `predict_session()` call; populate `SchedulePrediction.racer_name`, `.match_count` (sum of events with `rider_match`), `.events_without_start_lists`, `.total_events` (count of events where `event.is_special is False` across all sessions — matching the exclusion logic in `SessionPrediction.is_complete`); set `SessionPrediction.has_racer_match = True` when any event in session has `rider_match`. (c) Compute `next_race_*` fields on `SchedulePrediction` after all `SessionPrediction` objects are built: iterate through all event predictions across all sessions in order, find the first non-completed event with a `rider_match` — if `prediction.is_active` is True, set `next_race_is_active = True`; populate `next_race_event_name`, `next_race_heat`, `next_race_heat_count`, `next_race_time` from the match. Active events take priority over upcoming. Note: `prediction.is_active` requires `now` to be set and at least one prior event to be completed — before a session starts, all matched events will be upcoming (not active). **Reference**: read `predict_session()` and `predict_schedule()` in `app/predictor.py`, plus `Prediction`/`SessionPrediction`/`SchedulePrediction` in `app/models.py`.
- [X] T007 [P] Write rider matching and next-race tests in `tests/test_rider_matching.py` (new file). **Setup**: tests must call `record_start_list_riders()` to seed the in-memory cache before asserting on `get_rider_match()` or prediction results — there is no conftest helper for this cache, so each test explicitly seeds. Test cases: (a) case-insensitive matching ("Sean Hall" matches "HALL Sean"), (b) order-independent matching ("Hall Sean" matches "HALL Sean"), (c) no match for partial names ("Sean" does not match "HALL Sean"), (d) no match for empty/whitespace-only input, (e) per-heat predicted start calculation (`event_start + (heat - 1) × per_heat_duration`), (f) single-heat event returns `heat_count=1` and `heat_predicted_start = event_start`, (g) `next_race_*` fields: active event sets `next_race_is_active=True`, upcoming event sets `next_race_is_active=False`, all-completed returns `next_race_event_name=None`, (h) `events_without_start_lists` count excludes special events, (i) `has_racer_match` on `SessionPrediction`, (j) name with apostrophe: "OBrien" matches "O'BRIEN Liam", (k) name with diacritics: "Muller" matches "MÜLLER Hans", (l) `total_events` excludes `is_special=True` events (a session with 5 events where 2 are special should produce `total_events=3`), (m) pre-event edge case: when `now` is None, all matched events are upcoming (not active). Follow `conftest.py` patterns for test isolation.

**Checkpoint**: Foundation ready — models, parser, and matching logic complete. User story implementation can begin.

---

## Phase 3: User Story 1 — Racer Looks Up Their Personal Schedule (Priority: P1) 🎯 MVP

**Goal**: A racer enters their name and sees highlighted events with heat-specific predicted start times, contextual messaging including "Your next race:" / "Racing now:", and auto-refresh persistence.

**Independent Test**: Enter a racer name on a competition schedule → matching events highlighted with heat badges and times → success message shows match count and next race info → HTMX refresh preserves highlighting → clear name removes all personalization.

### Implementation for User Story 1

- [X] T008 [US1] Implement `_resolve_racer_name(request: Request, r: str | None) -> str | None` helper and `GET /settings/racer-name` route in `app/main.py`. (a) `_resolve_racer_name`: decode URL-safe Base64 `r` param if present (via `base64.urlsafe_b64decode`). Wrap the decode in `try/except (binascii.Error, UnicodeDecodeError)` — return None on failure (treat malformed `?r=` as no name provided). Fall back to `racer_name` cookie value, return plain text name or None. Design note: plain helper, not `Depends()` — single-use racer-specific logic per plan.md Complexity Tracking. (b) `/settings/racer-name` route: accept `event_id: int` and `name: str = ""` query params. If name non-empty: Base64-encode with `base64.urlsafe_b64encode`, set `racer_name` cookie (`httponly=True, samesite="lax", max_age=31536000`), redirect 303 to `/schedule/{event_id}?r=<encoded>#schedule-container`. If name empty: delete cookie, redirect 303 to `/schedule/{event_id}`. Set `secure` attribute to match the existing `use_learned` cookie in `app/main.py`. Note: the existing pattern sets `secure=True` unconditionally, which means the cookie will not be set by the browser during local HTTP development (uvicorn on `localhost`). This is a known limitation — the cookie works in production (HTTPS via CloudFront) and in most modern browsers on `localhost` over HTTP. Do not conditionally omit `secure` for dev since it would diverge from the established pattern. **Reference**: read `app/main.py` (279 lines) focusing on existing routes and cookie patterns.
- [X] T009 [US1] Modify `get_schedule()`, `refresh_schedule()`, and `_fetch_start_lists()` in `app/main.py`. (a) `get_schedule()`: add `r: str | None = Query(None)` param, call `_resolve_racer_name(request, r)`, pass resolved name to `predict_schedule()`. When `r` param is present and resolves to a name, update the `racer_name` cookie to match (FR-009: URL param wins and updates cookie). Pass `racer_name` (plain text) and `racer_encoded` (Base64) to template context. Log resolution after `predict_schedule()` returns so match metrics are available: `logger.info("racer_name_resolved", extra={"source": "url|cookie|none", "racer_name": name, "competition_id": event_id, "match_count": schedule.match_count, "events_without_start_lists": schedule.events_without_start_lists, "total_events": schedule.total_events})`. (b) `refresh_schedule()`: add `r: str | None = Query(None)` param, resolve name, pass to predictor, include `SchedulePrediction` fields in template context. (c) `_fetch_start_lists()`: after existing `parse_heat_count()` call, also call `parse_start_list_riders()` and `record_start_list_riders()` to populate the rider cache. **Cache skip logic**: the existing function short-circuits when `get_heat_count()` returns a cached value. Since `_start_list_riders` is a separate cache, the skip condition must check BOTH caches: only skip the fetch when `get_heat_count(competition_id, session_id, position) is not None AND (competition_id, session_id, position) in _start_list_riders`. Otherwise, warm Lambda invocations that already cached heat counts will never populate the rider cache. **Reference**: read `app/main.py` focusing on `get_schedule()`, `refresh_schedule()`, `_fetch_start_lists()`, plus `app/predictor.py` public API.
- [X] T010 [US1] Add name input form, HTMX refresh parameter, and update JS session handler in `app/templates/schedule.html`. (a) Insert `<div class="racer-form-bar">` between the existing `.meta.meta-bar` `<p>` and `<div id="schedule-container">`. Form: `<form>` (GET, action `/settings/racer-name`) containing `<input type="hidden" name="event_id" value="{{ competition_id }}">`, `<input type="text" name="name" placeholder="Your name to highlight your events" value="{{ racer_name or '' }}" aria-label="Racer name" aria-describedby="racer-hint">`, `<button type="submit">Highlight</button>`, `{% if racer_name %}<a href="/settings/racer-name?event_id={{ competition_id }}" class="clear-link">Clear</a>{% endif %}`, and `<small id="racer-hint" class="racer-form-hint">Enter your full name as shown on the start list</small>`. (b) Update `hx-get` URL on `#schedule-container`: `hx-get="/schedule/{{ competition_id }}/refresh{% if racer_encoded %}?r={{ racer_encoded }}{% endif %}"` (FR-007). (c) Update the `htmx:afterSwap` JavaScript handler: after restoring previously-open sessions from `openSessions`, also force-open any `<details>` element that has the `data-pending-racer` attribute (set by the server when `has_pending_racer_match` is true). Implementation: add `container.querySelectorAll('details[data-pending-racer]').forEach(function(el) { el.setAttribute('open', ''); });` after the existing open-state restoration loop. This distinguishes sessions the user manually closed from those that should auto-open because the racer still has pending events. Without this, FR-015 auto-open only works on initial page load — the JS handler overrides the server-set `open` attribute on every HTMX refresh. **Reference**: read `app/templates/schedule.html` (64 lines).
- [X] T011 [US1] Add `.racer-match` row highlighting, heat badge, heat time, and session auto-open to `app/templates/_schedule_body.html`. (a) On the `<tr>` element: add `racer-match` class and `aria-label="Your event"` when `pred.rider_match` is present. (b) After event name text in the first `<td>`: if `pred.rider_match` and `pred.rider_match.heat_count > 1`, render `<span class="badge racer-heat">Heat {{ pred.rider_match.heat }}</span>`; if `pred.rider_match.heat_count == 1`, render `<span class="badge racer-heat">Racing</span>`. (c) In the Est. Duration `<td>`: if `pred.rider_match` and `pred.rider_match.heat_count > 1` and `pred.rider_match.heat_predicted_start`, render `<span class="heat-time">Your heat: {{ pred.rider_match.heat_predicted_start.strftime('%H:%M') }}</span>` below the duration value. (d) Modify `<details>` open logic: change `{% if not sp.is_complete %}` to `{% if not sp.is_complete or sp.has_pending_racer_match %}` (FR-015 — only auto-open sessions with pending racer events; completed sessions collapse to reduce noise). **Reference**: read `app/templates/_schedule_body.html` (59 lines).
- [X] T012 [US1] Add contextual messaging to `app/templates/_schedule_body.html` above the `{% for sp in schedule.sessions %}` loop. Each message is a separate `<div>` with its own `role="status"` (do NOT nest status lines as child `<span>` elements — separate `role="status"` elements ensure screen readers announce each change independently during HTMX swaps). States per FR-010: (a) **Success** (blue info): when `schedule.racer_name` and `schedule.match_count > 0`: `<div class="racer-message racer-message-info" role="status">Found {{ schedule.match_count }} event(s) for "{{ schedule.racer_name }}"</div>`. Below, if `schedule.next_race_event_name`: render status line as a **separate** `<div class="racer-message racer-message-info" role="status">` — if `schedule.next_race_is_active`: `Racing now: {{ schedule.next_race_event_name }}{% if schedule.next_race_heat_count and schedule.next_race_heat_count > 1 %}, Heat {{ schedule.next_race_heat }}{% endif %}` (omit time for active events since the predicted time is in the past); else (upcoming): `Your next race: {{ schedule.next_race_event_name }}{% if schedule.next_race_heat_count and schedule.next_race_heat_count > 1 %}, Heat {{ schedule.next_race_heat }}{% endif %}{% if schedule.next_race_time %} at {{ schedule.next_race_time.strftime('%H:%M') }}{% endif %}`. The `{% if schedule.next_race_time %}` guard prevents a `NoneType` strftime error when per-heat duration is unavailable. (b) **Missing start lists** (amber warning): when `schedule.racer_name` AND `schedule.events_without_start_lists > 0`: `<div class="racer-message" role="status">{{ schedule.events_without_start_lists }} event(s) do not yet have start lists</div>`. Note the `schedule.racer_name` guard — this message is contextual to a name search and should not appear when no racer name is active. (c) **No matches** (amber warning): when `schedule.racer_name` and `schedule.match_count == 0` and `schedule.events_without_start_lists < schedule.total_events`: `<div class="racer-message" role="status">No matching events found for "{{ schedule.racer_name }}"</div>`. (d) **No data** (amber warning): when `schedule.racer_name` and `schedule.events_without_start_lists == schedule.total_events`: `<div class="racer-message" role="status">Start lists are not yet published — check back closer to the event</div>` (suppress no-match). **Reference**: read `app/templates/_schedule_body.html` + `SchedulePrediction` fields in `app/models.py`.
- [X] T013 [US1] Add racer-match, form bar, and messaging desktop styles to `static/style.css`. Rows: `tr.racer-match { background: #e8f0fe; border-left: 4px solid #1a73e8; }`. State interactions: `tr.racer-match.active { background: #fff3cd; border-left: 4px solid #1a73e8; }` + `td { font-weight: 600; }`; `tr.racer-match.status-completed { background: #e8f0fe; }` (no opacity on `tr` — the blue left border must retain full contrast); `tr.racer-match.status-completed td { opacity: 0.45; }` (fade content only); `tr.racer-match.status-completed:not(.special) { text-decoration: line-through; text-decoration-color: #aaa; }`; `tr.racer-match.status-upcoming { background: #e8f0fe; border-left: 4px solid #1a73e8; }`; `tr.racer-match.status-not_ready { background: #e8f0fe; border-left: 4px solid #1a73e8; }` (racer identity signal present even for not-yet-ready events). Badge: `.badge.racer-heat { background: #1a73e8; color: #fff; font-size: 0.74rem; font-weight: 700; padding: 2px 9px; margin-left: 6px; vertical-align: middle; }`. Heat time: `.heat-time { display: block; font-size: 0.78rem; font-weight: 600; color: #1a53a0; margin-top: 2px; }`. Form bar: `.racer-form-bar { margin: 0.5rem 0 1rem; display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }`, input/button/clear-link/hint sub-styles per mockup. `.racer-form-bar button { ... min-height: 44px; }` (meets Apple HIG touch target guideline for mobile use). Messages: `.racer-message { font-size: 0.88rem; padding: 0.5rem 0.75rem; margin-bottom: 0.75rem; border-radius: 4px; background: #fff3cd; color: #856404; }`, `.racer-message-info { background: #dce8fc; color: #1a53a0; }`. Update `.evt-btn` margin to `0 4px 4px 0`. **Reference**: read `static/style.css` (431 lines) for existing patterns, plus `docs/ui-recommendations-mockup.html` for proposed styles.
- [X] T014 [US1] [P] Write route tests in `tests/test_main.py` (new file). Use FastAPI `TestClient` — instantiate with `from fastapi.testclient import TestClient` and `from app.main import app`. Test cases: (a) `GET /schedule/{id}?r=<base64>` resolves name and includes match data, (b) `GET /schedule/{id}` with `racer_name` cookie resolves name, (c) `?r=` param takes precedence over cookie and updates cookie in response, (d) `GET /settings/racer-name?event_id=X&name=Y` sets cookie and redirects — assert Location header ends with `#schedule-container` (FR-016), (e) `GET /settings/racer-name?event_id=X` (no name) deletes cookie and redirects, (f) empty name submission behaves like clear, (g) malformed Base64 `?r=!!!invalid` returns schedule without error (no 500), (h) verify `Secure` flag is present on Set-Cookie response header. Follow `conftest.py` isolation patterns.

**Checkpoint**: User Story 1 fully functional — racer can enter name, see highlighted events with heat detail and "Your next race:" / "Racing now:" messaging, HTMX refresh preserves state, shared links update cookie, clear removes personalization.

---

## Phase 4: User Story 2 — Racer Checks Schedule on Mobile at the Venue (Priority: P2)

**Goal**: Personalized schedule is easy to read on mobile with highlighted events clearly standing out in the card layout.

**Independent Test**: View personalized schedule on a ≤600px viewport and verify racer-matched cards have blue left border, form stacks full-width, heat time is readable, and 200% zoom works without horizontal scrolling.

### Implementation for User Story 2

- [X] T015 [US2] Add `.racer-match` mobile card styles and form stacking to `static/style.css` inside the existing `@media (max-width: 600px)` block. Mobile racer-match: `tr.racer-match { border-left: 4px solid #1a73e8; background: #e8f0fe; }`, `tr.racer-match.status-upcoming { border-left: 4px solid #1a73e8; background: #e8f0fe; }`, `tr.racer-match.active { border-left: 4px solid #1a73e8; background: #fff3cd; }`. Heat time mobile sizing: `.heat-time { font-size: 0.75rem; }`. Form stacking: `.racer-form-bar { flex-wrap: wrap; }`, `.racer-form-bar input[type="text"] { flex: 0 0 100%; order: 1; }`, `.racer-form-bar button { flex: 1; order: 2; }`, `.racer-form-bar .clear-link { order: 3; }`, `.racer-form-hint { order: 4; }` — explicit ordering ensures visual stacking matches source order at 320px and at 200% zoom. **Reference**: read `static/style.css` `@media (max-width: 600px)` section (lines 234–431) for existing mobile card patterns.

**Checkpoint**: Mobile schedule with racer highlighting is usable at 320px and 200% zoom.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Align reference documentation with current spec decisions

- [X] T016 Update `docs/ui-recommendations-mockup.html` to match current spec decisions: (a) change "missing start lists" messages from `racer-message-info` class (blue) to plain `racer-message` (amber warning), (b) change "no data" message similarly, (c) add "Your next race:" / "Racing now:" status line to success message examples, (d) update in-context example at bottom of mockup, (e) ensure CSS class for blue info messages is canonically `racer-message-info` (not `.info`) — also update `docs/racer-input-states-mockup.html` to use `.racer-message.racer-message-info` matching the production CSS class in T013. **Reference**: read current `docs/ui-recommendations-mockup.html` + FR-010 in `spec.md` for authoritative styling rules.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately. T001 and T002 are parallel.
- **Foundational (Phase 2)**: T003 depends on T001 (fixture) and T002 (models). T005 depends on T002. T004 and T007 are test tasks that can run in parallel with later implementation.
- **US1 (Phase 3)**: Depends on Phase 2 completion. T008 → T009 (routes are sequential). T010 depends on T009 having defined `racer_encoded` template variable. T010-T013 can begin after T009. T014 can run in parallel.
- **US2 (Phase 4)**: Depends on T013 (desktop CSS must exist first for mobile overrides)
- **Polish (Phase 5)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational (Phase 2) — No dependencies on other stories
- **US2 (P2)**: Can start after US1's CSS task (T013) — Adds mobile overrides to desktop rules
- **US3 (P3)**: Fully delivered by US1 tasks (T008–T009 implement `?r=` URL encoding, cookie update on shared link). No additional implementation needed. Verify via T014 test cases (c).
- **US4 (P3)**: Fully delivered by US1 tasks (T008 implements cookie set/read with 1-year expiry, auto-apply on page load). No additional implementation needed. Verify via T014 test cases (a, b).

### Within Each Phase

- Models before parser
- Parser before predictor cache/matching
- Predictor before routes
- Routes before templates
- Templates before CSS
- Tests can run in parallel with implementation in different files

### Parallel Opportunities

- T001 and T002 (fixture + models) — different files
- T004 and T005 (parser tests + predictor implementation) — different files
- T007 and T008+ (matching tests + route implementation) — different files
- T014 and T010-T013 (route tests + template/CSS) — different files

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003-T007)
3. Complete Phase 3: User Story 1 (T008-T014)
4. **STOP and VALIDATE**: Test full flow — enter name, see highlights + "Your next race:", refresh, clear
5. Deploy/demo if ready — US3 and US4 are already working at this point

### Incremental Delivery

1. Setup + Foundational → Parser and matching ready
2. Add US1 → Full racer lookup functional → Deploy (includes US3+US4 behavior)
3. Add US2 → Mobile polished → Deploy
4. Polish → Documentation aligned

---

## Fixture Requirement for Parsing Tasks

Any task that implements a parsing function for external data (e.g., HTML from
tracktiming.live, JSON API responses) MUST reference a specific fixture file in
`tests/fixtures/`. The fixture captures the real upstream format and serves as
the contract between the parser and its data source.

Tasks T003 and T004 reference `tests/fixtures/start-list-sample.html` — this
fixture MUST be captured from a real tracktiming.live start list page (T001)
before those tasks can execute.

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- US3 and US4 require no additional code — their behaviors are implemented by US1's route and cookie handling
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
