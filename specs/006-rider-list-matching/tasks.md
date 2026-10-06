---
description: "Task list for 006 Rider List Fallback Matching"
---

# Tasks: Rider List Fallback Matching

**Input**: Design documents from `/specs/006-rider-list-matching/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/schedule-view.md, quickstart.md

**Tests**: Required. The spec's testing section and SC-001–SC-004 call for fixture-driven tests, so each story writes its tests first and checks that they fail.

**Organization**: Tasks are grouped by user story:
- **US1** delivers "Entered" matches only; non-certain rounds produce no match.
- **US2** adds "If advancing" matches.
- **US3** adds the banners and the next-race wording.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1, US2, US3 from spec.md
- Shared references: `specs/006-rider-list-matching/research.md` (R1–R11), `data-model.md`, `contracts/schedule-view.md`

---

## Phase 1: Setup

- [x] T001 Capture the upstream fixtures from live EventId 26037 into `tests/fixtures/`: `schedule-26037.json` (initial layout), `refresh-26037.json` (refresh) and `rider-list-26037.html` (Rider List with inline `data:` `<img>` tags removed). Done during /speckit.plan research; see research.md "Fixtures".

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ No user story work can begin until this phase is complete**

- [ ] T002 Update models in `app/models.py` per data-model.md:
  - Add `RiderListEntry(name: str, category: str, codes: frozenset[str], normalized_tokens: frozenset[str])`, with the same `_compute_tokens` validator pattern as `RiderEntry`.
  - In `RiderMatch`, make `heat` and `heat_count` `int | None = None` (keep `ge=1` when set), and add `source: Literal["start_list", "rider_list"] = "start_list"` and `tentative: bool = False`.
  - In `NextRace`, make `heat` and `heat_count` `int | None = None` and add `tentative: bool = False`.
  - In `SchedulePrediction`, add `tentative_match_count: int = 0` and `rider_list_entry: RiderListEntry | None = None`.
  - Run `pytest` to confirm the existing tests still pass.
- [ ] T003 Make the existing template null-safe for optional heat fields in `app/templates/_schedule_body.html`:
  - Guard each `pred.rider_match.heat_count > 1` and `nr.heat_count > 1` comparison with `is not none`.
  - Behaviour for start-list matches must not change. Run `pytest tests/test_main.py`.
- [ ] T004 [P] Write failing parser tests in `tests/test_parser.py`, in a new `TestRiderList` class.
  Fixture: `tests/fixtures/schedule-26037.json`, `tests/fixtures/refresh-26037.json`, `tests/fixtures/rider-list-26037.html`
  - `parse_rider_list_url` returns `results/E26037/X-RIDERLIST-0-0-S.htm` for both the initial and refresh fixtures.
  - It returns `results/E26008/X-RIDERLIST-0-0-S.htm` for `tests/fixtures/sample-event-output.json`. 26008 also publishes a Rider List, with non-masters categories.
  - It returns `None` for `{"jxnobj": []}` and for a `documents` jxnobj containing only the Medal Standings row (copy that row from the 26037 fixture).
  - `parse_rider_list` on the fixture returns 480 entries, including:
    - `ABERS Brian` → category `M6064`, codes `{"TS","S","TT"}`
    - `ACHILER Becky` → `W4549`, `{"TP","TS"}`
    - `FOWLER Walter` → `M90`, `{"TT"}`
  - `parse_rider_list("<html><body>nonsense</body></html>")` returns `[]`.
  - `parse_rider_list` on the fixture with a long `<img src="data:...">` re-inserted still returns 480 entries.
- [ ] T005 Implement `parse_rider_list_url(jxn_data: dict) -> str | None` and `parse_rider_list(html: str) -> list[RiderListEntry]` in `app/parser.py` until T004 passes.
  Fixture: `tests/fixtures/schedule-26037.json`, `tests/fixtures/refresh-26037.json`, `tests/fixtures/rider-list-26037.html`
  - URL (research R1): find the top-level jxnobj with `cmd == "as"` and `id == "documents"`, parse its `data`, find the `<tr>` whose `<h4>` text is exactly `Rider List`, and return that row's `<a href>`. Return `None` otherwise. It must never raise.
  - Rows (R2/R3): first `re.sub(r'<img[^>]*src="data:[^"]*"[^>]*>', '', html)`. Then for each `tbody tr` with at least 7 `<td>`: name = cell 1 text, category = cell 2 text, codes = `frozenset(cell 6 text .split())`. Skip rows with an empty name or category.
  - Log a warning when there are 0 rows but the page has `<tr>` elements, mirroring `parse_start_list_riders`.
- [ ] T006 [P] Change start-list presence to non-empty in `app/predictor.py` (research R10):
  - `has_start_list_riders` returns `bool(_start_list_riders.get(key))`.
  - Add a test in `tests/test_rider_matching.py`: after `record_start_list_riders(..., [])`, `has_start_list_riders` is False, and `predict_session` with a racer name counts that event in `events_without_start_lists`.
  - Check that `_fetch_start_lists` in `app/main.py` now refetches empty cached lists. Its condition already uses `not has_start_list_riders(...)`, so no code change should be needed; confirm.
- [ ] T007 Add the Rider List cache to `app/predictor.py`: `_rider_lists: dict[str, list[RiderListEntry]] = {}`, `record_rider_list(url, entries)` and `get_rider_list(url) -> list[RiderListEntry] | None`.
  - Add `_rider_lists.clear()` to the autouse cache-clearing fixtures in `tests/test_main.py` and `tests/test_rider_matching.py`.
  - Change the existing autouse `mock_fetchers` in `tests/test_main.py` so `fetch_page_html` returns `""` for paths containing `RIDERLIST` and `start_list_html` otherwise (use `side_effect`). The current fixture (`sample-event-output.json`, E26008) has a Rider List link, and returning start-list HTML for it would feed `parse_rider_list` the wrong page.

**Checkpoint**: The parsers work against the real fixtures, the models accept rider-list matches, and the full suite is green.

---

## Phase 3: User Story 1 - See my events before start lists are posted (Priority: P1) 🎯 MVP

**Goal**: Events with no start-list riders are highlighted "Entered" when the Rider List shows the racer is certainly riding them (Qualifying, Qualifier N, or the only event in their band/gender/code group).

**Independent Test**: Render `/schedule/26037` with the racer `Brian Abers`, with the mocked fetchers returning the 26037 fixtures. "60-64 Men Sprint Qualifying", "55-64 Men Team Sprint Qualifying" and "60-64 Men 500m Time Trial Final" show "Entered"; nothing outside his band, gender or codes is highlighted.

### Tests for User Story 1 ⚠️ write first, confirm failing

- [ ] T008 [P] [US1] Create `tests/test_rider_list.py` with unit tests for the `app/rider_list.py` functions in contracts/schedule-view.md:
  - `category_band`: `M6064` → (M, 60, 64); `W4549` → (W, 45, 49); `M90` → (M, 90, None); `Elite`, `MU17`, `M60` with trailing text, and `""` → `None`.
  - `event_band`: "55-64 Men Team Sprint Qualifying" → (M, 55, 64); "65+ Women Sprint Final Ride 1" → (W, 65, None); "90+ Men 500m Time Trial Final" → (M, 90, None); "Elite Men Keirin" → `None`.
  - Containment: (M, 55, 64) ⊇ M6064; (M, 80, None) ⊇ M90; (M, 65, 74) ⊉ M6064; (W, 55, None) ⊇ W6569 and (W, 65, None) ⊇ W6569; gender mismatch → False.
  - `event_code`: research R8 table, plus `pursuit_4k`, `keirin`, `time_trial_generic` → `None`.
  - `find_rider`: `normalize_rider_name("Brian Abers")` finds ABERS Brian; an unknown name → `None`.
- [ ] T009 [P] [US1] Add fixture-driven "Entered" tests to `tests/test_rider_list.py`, using `parse_schedule(schedule-26037.json)`, `parse_rider_list(rider-list-26037.html)`, `group_counts(sessions)` and `match_event` over all non-special events.
  Fixture: `tests/fixtures/schedule-26037.json`, `tests/fixtures/rider-list-26037.html`
  - ABERS Brian non-tentative matches = exactly {"60-64 Men Sprint Qualifying", "60-64 Men 500m Time Trial Final", "55-64 Men Team Sprint Qualifying"}.
  - FOWLER Walter = {"90+ Men 500m Time Trial Final"}.
  - ACHILER Becky = {"45-54 Women Team Sprint Qualifying", "45-54 Women Team Pursuit Qualifying"}.
  - No match for "65-74 Men Team Sprint Qualifying", "55-59 Men Sprint Qualifying" or "60-64 Men Pursuit Qualifying" for ABERS.
  - Every returned match has `source == "rider_list"`, `heat is None`, `heat_count is None` and `team_name is None`.
- [ ] T010 [P] [US1] Add predictor integration tests in `tests/test_rider_matching.py`, in a new `TestRiderListFallback` class. Use `make_event` with 26037-style names and disciplines, and pass `rider_list=[RiderListEntry(...)]` to `predict_schedule`:
  - An event with no `start_list_url` matches from the Rider List (Entered).
  - An event with non-empty seeded start-list riders that don't include the racer → no match (start list supersedes, US1 #5).
  - An event with an empty seeded start list → Rider List match (US1 #6).
  - A special event ("Medal Ceremonies", `is_special=True`) never matches.
  - The match counts toward `match_count` and sets `has_pending_racer_match` on its session.
  - A racer whose category has no band (`category="Elite"`) gets no Rider List matches, and `rider_list_entry` is `None`.
  - A COMPLETED event with no start-list riders is matched by the same rules as an upcoming one (spec edge case).
  - `rider_list=None` gives identical results to today.
- [ ] T011 [P] [US1] Add route tests in `tests/test_main.py`, in a new `TestRiderListRoutes` class. Add a separate fixture that patches `fetch_initial_layout`/`fetch_refresh` to return `schedule-26037.json`/`refresh-26037.json`, and `fetch_page_html` to return `rider-list-26037.html` for paths containing `RIDERLIST` and an empty string otherwise.
  Fixture: `tests/fixtures/schedule-26037.json`, `tests/fixtures/refresh-26037.json`, `tests/fixtures/rider-list-26037.html`
  - GET `/schedule/26037` with the `racer_name=Brian Abers` cookie: the response contains `Entered` and the row for "60-64 Men Sprint Qualifying" has `racer-row`.
  - GET `/schedule/26037` then `/schedule/26037/refresh`: `fetch_page_html` is called with the RIDERLIST path exactly once (SC-002).
  - Without a racer name, the RIDERLIST path is never fetched.
  - When the RIDERLIST fetch raises `httpx.HTTPError`, the response is still 200 and the existing "do not yet have start lists" warning is present (SC-004).
  - `save_palmares_entries` is never called with entries derived from Rider List matches: patch it and assert no entry names a non-start-list event.

### Implementation for User Story 1

- [ ] T012 [US1] Create `app/rider_list.py` (pure, no I/O) until T008/T009 pass, using research R6–R9 and the data-model.md `AgeBand`:
  - `AgeBand` NamedTuple (`gender`, `lo`, `hi`) with `contains()`
  - `category_band`, `event_band` (regex `\b(\d{2})(?:-(\d{2})|\+)\s+(Men|Women)\b`), `CODE_DISCIPLINES` and `event_code`, `find_rider`, `group_counts`
  - `match_event(entry, event, counts) -> RiderMatch | None`. In this task it returns a match only when the event is certain (`\bQualif(?:ying|ier\s+\d+)\b`, case-insensitive, or a group count of 1) and returns `None` for later rounds. US2 changes this.
  - Module docstring stating the scope: 26037 formats only.
- [ ] T013 [US1] Wire Rider List matching into `app/predictor.py` until T010 passes:
  - `predict_schedule(..., rider_list: list[RiderListEntry] | None = None)` computes `user_tokens`, `entry = find_rider(...)` (kept only if `category_band(entry.category)` is not None) and `counts = group_counts(sessions)` once, and passes `rider_entry` and `counts` to `predict_session`.
  - In `predict_session`'s rider-matching block, when start-list riders are absent or empty: increment `events_without_start_lists` (as today), then, if `rider_entry` is set, call `match_event`.
  - `_build_next_race` copies `heat` and `heat_count` as-is, so they may be None.
- [ ] T014 [US1] Wire fetching into `app/main.py` until T011 passes:
  - Add `async def _fetch_rider_list(client, url) -> list[RiderListEntry] | None` (`get_rider_list` cache hit; otherwise `fetch_page_html` + `parse_rider_list` + `record_rider_list`; on an exception, log a warning with the URL and return `None` without caching; warn when the parse yields 0 rows).
  - In both `get_schedule` and `refresh_schedule`, resolve `racer_name` before the `gather`, compute `url = parse_rider_list_url(jxn_data)`, and add `_fetch_rider_list` to the same `asyncio.gather` when the R5 condition holds: racer set, url set, and some non-special event has no `start_list_url` or `not has_start_list_riders(...)`. Pass the result as `rider_list=` to `predict_schedule`.
  - In `_collect_palmares_entries`, require `pred.rider_match.source == "start_list"`.
  - Add `rider_list_matches` to the `racer_name_resolved` log `extra`.
- [ ] T015 [US1] Render the "Entered" badge in `app/templates/_schedule_body.html`, per the contracts/schedule-view.md row table:
  - When `pred.rider_match.source == 'rider_list'` and not tentative, show `<span class="{{ racer_badge }}">Entered</span>`, keep the `racer-row bg-info/5` tint and `aria-label="Your event"`.
  - Check the `rider_list` source before any heat comparisons.
  - Run `pytest`.

**Checkpoint**: US1 is fully functional. Abers sees 3 Entered events in 26037 and nothing else changes.

---

## Phase 4: User Story 2 - See later rounds I might ride (Priority: P2)

**Goal**: Later rounds for the racer's band, gender and codes show "If advancing", with no row tint.

**Independent Test**: For ABERS Brian on the 26037 fixtures, the 10 tentative matches are exactly the 60-64 Men Sprint 1/4, 1/2 and Final Rides 1–3 plus "55-64 Men Team Sprint Final", and each renders "If advancing" without `racer-row`.

### Tests for User Story 2 ⚠️ write first, confirm failing

- [ ] T016 [P] [US2] Add tentative tests to `tests/test_rider_list.py`.
  Fixture: `tests/fixtures/schedule-26037.json`, `tests/fixtures/rider-list-26037.html`
  - ABERS Brian tentative matches = exactly the 9 events "60-64 Men Sprint {1/4 Final, 1/2 Final, Final} Ride {1,2,3}" plus "55-64 Men Team Sprint Final" (10 in total; 13 matches overall).
  - ACHILER Becky tentative = {"45-54 Women Team Sprint Final", "45-54 Women Team Pursuit Final"}.
  - A 55-59 Men rider with SCR: "55-59 Men Scratch Race Qualifier 1" and "Qualifier 2" are Entered and "55-59 Men Scratch Race Final" is tentative. Pick a real rider from the fixture with category M5559 and SCR in codes, and assert by name.
  - "35-49 Women Points Race Final" is Entered for a W3539 or W4044 rider with PTS (only event in its group).
  - Sprint "Ride 3" events are tentative and have no separate label (clarification).
- [ ] T017 [P] [US2] Add tests to `tests/test_rider_matching.py` (`TestRiderListFallback`): with one Entered and one tentative event, `match_count == 2`, `tentative_match_count == 1`, and a pending tentative match also sets `has_pending_racer_match`.
- [ ] T018 [P] [US2] Add a route test to `tests/test_main.py` (`TestRiderListRoutes`): for Abers, the row for "60-64 Men Sprint 1/4 Final Ride 1" contains `If advancing` and `aria-label="Your event, if advancing"`, and does not have the `racer-row` class.

### Implementation for User Story 2

- [ ] T019 [US2] In `app/rider_list.py` `match_event`, return `RiderMatch(source="rider_list", tentative=True)` for non-certain matching events instead of `None`, until T016 passes.
- [ ] T020 [US2] In `app/predictor.py` `predict_schedule`, count `tentative_match_count` and set it on `SchedulePrediction`, until T017 passes.
- [ ] T021 [US2] In `app/templates/_schedule_body.html`, for tentative rider-list matches render `<span class="{{ racer_badge }} border-dashed opacity-70">If advancing</span>`, omit `racer-row bg-info/5`, and set `aria-label="Your event, if advancing"`. Iterate until T018 passes.

**Checkpoint**: US1 and US2 both work. Abers sees 3 Entered and 10 If advancing.

---

## Phase 5: User Story 3 - Clear status messages (Priority: P3)

**Goal**: The banners reflect Rider List matching; next race flags tentative matches.

**Independent Test**: For Abers on the 26037 fixtures, the page shows "Found 13 events for "Brian Abers" (10 if advancing)" and the Rider List info line with "M6064: S, TS, TT", and shows neither start-list warning.

### Tests for User Story 3 ⚠️ write first, confirm failing

- [ ] T022 [P] [US3] Add predictor tests to `tests/test_rider_matching.py`:
  - `rider_list_entry` is set only when at least one Rider List match exists.
  - When the first pending match is tentative, `next_race.tentative is True` and `heat is None`.
  - With an active start-list match, the existing next-race priority is unchanged.
- [ ] T023 [P] [US3] Add route tests to `tests/test_main.py` (`TestRiderListRoutes`):
  - Abers: the body contains `(10 if advancing)` and `Events without start lists matched from the Rider List (M6064: S, TS, TT)`, and contains neither `do not yet have start lists` nor `Start lists are not yet published`.
  - A racer name not in the Rider List (e.g. `Nobody Here`): the existing `do not yet have start lists` warning is present and there's no Rider List info line.
  - Next race: for Abers, the body contains `Your next race: 60-64 Men Sprint Qualifying` and that line has no `(if advancing)`. His first pending match in the static fixture is session 5's Entered qualifying ride; the tentative next race is covered by T022.

### Implementation for User Story 3

- [ ] T024 [US3] In `app/predictor.py`, set `SchedulePrediction.rider_list_entry` to the racer's entry when there is at least one rider-list match, and copy `tentative` in `_build_next_race`. Iterate until T022 passes.
- [ ] T025 [US3] Update the banners in `app/templates/_schedule_body.html` per the contracts/schedule-view.md "Banners" section:
  - Append ` ({{ schedule.tentative_match_count }} if advancing)` when the count is greater than 0.
  - Add `(if advancing)` after the next-race event name when `nr.tentative`.
  - When `schedule.rider_list_entry` is set, render the info line (`{{ category }}: {{ codes|sort|join(', ') }}`) in place of both start-list warnings.
  - Iterate until T023 passes.
- [ ] T026 [US3] Add `tentative_matches` to the `racer_name_resolved` log `extra` in `app/main.py`.

**Checkpoint**: All three stories work.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T027 [P] Update `CLAUDE.md`. Every statement must describe the code as it now is:
  - Request flow step 3: add the Rider List fetch.
  - tracktiming.live API: add the `documents` block and the Rider List row format.
  - In-memory caches: add `_rider_lists`, keyed by URL.
  - Add a "Rider List matching" paragraph: scope (masters `[MW]NNNN`/`M90` only, codes S TT IP TP TS SCR PTS), Entered vs If advancing, start list supersedes, empty start list counts as absent, not used for palmares, `app/rider_list.py` responsibility.
  - Add `rider_list.py` where the module responsibilities are listed.
- [ ] T028 [P] Update `README.md` "Racer highlight": the line-54 "If start lists aren't published yet" paragraph becomes a description of the Rider List fallback (Entered / If advancing, no heat info), and keep the existing message for competitions without a Rider List.
- [ ] T029 Run the full `pytest` with coverage. Then follow `specs/006-rider-list-matching/quickstart.md` against live 26037 with `uvicorn`, and record any deviation from the expected results.
- [ ] T030 Backport any behaviour-affecting deviations found during implementation into `spec.md`, `research.md`, `data-model.md` and `contracts/schedule-view.md` (constitution: Development Workflow).

---

## Dependencies & Execution Order

- **Phase 1** is done.
- **Phase 2**: T002 before everything else. T003 after T002. T004 → T005. T006 → T007 (both edit `app/predictor.py` and `tests/test_rider_matching.py`), in parallel with T004/T005.
- **US1 (Phase 3)**: depends on Phase 2. Tests T008–T011 run in parallel. Then T012 → T013 → T014 → T015.
- **US2 (Phase 4)**: depends on US1's `match_event` (T012) and the template branch (T015). Tests T016–T018 run in parallel. Then T019 → T020 → T021.
- **US3 (Phase 5)**: depends on US1. It can run alongside US2 apart from shared files (`predictor.py`, `_schedule_body.html`), so in practice it runs after US2.
- **Polish**: after the desired stories. T027 and T028 run in parallel.

## Parallel Example: User Story 1

```text
Task: "T008 unit tests for rider_list.py in tests/test_rider_list.py"
Task: "T010 predictor fallback tests in tests/test_rider_matching.py"
Task: "T011 route tests in tests/test_main.py"
# T009 shares tests/test_rider_list.py with T008: same agent, or sequential
```

## Implementation Strategy

1. Finish Phase 2 and keep the suite green.
2. Deliver US1 (Entered only) and validate it against the fixtures. This is the MVP: the 26037 masters can see their certain events.
3. Add US2 (If advancing), then US3 (banners and next race).
4. Do the Phase 6 docs in the same commit as the code, per CLAUDE.md.

## Notes

- Each task's reference set stays within ~800 lines: `app/predictor.py` (~550), `app/main.py` (~650, so read only the referenced functions), `_schedule_body.html` (~150), and the spec docs.
- Don't add codes, categories or name formats beyond those in the 26037 fixtures (user instruction: build only what live data can test).
