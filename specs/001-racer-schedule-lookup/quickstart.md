# Quickstart: Racer Schedule Lookup

## Build Sequence

### Phase 1: Parser — Extract riders from start lists
1. Add `parse_start_list_riders(html: str) -> list[RiderEntry]` to `app/parser.py`
2. Add `RiderEntry` model to `app/models.py`
3. Write tests using existing fixture patterns (plain-text start list HTML)
4. Verify: `pytest tests/test_parser.py -k rider`

### Phase 2: Predictor — Store and match riders
1. Add `_start_list_riders` cache to `app/predictor.py`
2. Add `record_start_list_riders()` and `get_rider_match()` functions
3. Add `RiderMatch` model and `rider_match` field to `Prediction` in `app/models.py`
4. Implement name normalization (tokenize, lowercase, frozenset comparison)
5. Calculate per-heat predicted start time: `event_start + (heat - 1) × per_heat_duration`
6. Add `racer_name`, `match_count`, `events_without_start_lists` to `SchedulePrediction`
7. Write tests for matching logic (case-insensitive, order-independent, no-match)
8. Verify: `pytest tests/test_predictor.py`

### Phase 3: Routes — Wire up name input and cookies
1. Add `_resolve_racer_name(request, r_param)` helper to `app/main.py`
2. Modify `get_schedule()` to accept `r` query param, resolve name, pass to predictor
3. Modify `refresh_schedule()` to accept `r` query param
4. Add `GET /settings/racer-name` route (cookie set/clear + redirect)
5. Call `parse_start_list_riders()` in `_fetch_start_lists()` alongside `parse_heat_count()`
6. Verify: `pytest tests/test_main.py`

### Phase 4: Templates — Name input and highlighting
1. Add name input form to `schedule.html` (in meta bar area, GET form to `/settings/racer-name`)
2. Add `?r=` to `hx-get` URL for refresh persistence
3. Add racer-match CSS class and heat detail to `_schedule_body.html`
4. Add "no matches" / "start lists unavailable" messaging
5. Add `.racer-match` styles to `static/style.css` (highlight color, mobile card accent)
6. Verify: manual browser testing + existing template render tests

### Phase 5: Integration — End-to-end validation
1. Test full flow: enter name → see highlights → refresh preserves → clear name
2. Test URL sharing: copy URL with `?r=` → open in new browser → highlights shown
3. Test cookie persistence: enter name → close tab → reopen schedule → auto-apply
4. Test mobile viewport: card layout with highlights at 320px and 200% zoom
5. Verify: `pytest` (full suite)

## Key Files to Modify

| File | Changes |
|------|---------|
| `app/models.py` | Add `RiderEntry`, `RiderMatch`; extend `Prediction`, `SchedulePrediction` |
| `app/parser.py` | Add `parse_start_list_riders()` |
| `app/predictor.py` | Add `_start_list_riders` cache, `record_start_list_riders()`, `get_rider_match()`; modify `predict_session()` |
| `app/main.py` | Add `r` query param handling, `_resolve_racer_name()`, `/settings/racer-name` route; modify `get_schedule()`, `refresh_schedule()`, `_fetch_start_lists()` |
| `app/templates/schedule.html` | Add name input form, `?r=` in `hx-get` URL |
| `app/templates/_schedule_body.html` | Add `.racer-match` class, heat detail, messaging |
| `static/style.css` | Add `.racer-match` highlight styles |
| `tests/test_parser.py` | Add `TestParseStartListRiders` |
| `tests/test_predictor.py` | Add rider matching tests |
| `tests/test_main.py` | Add racer name route tests |

## Dependencies

No new packages required. Uses only:
- `base64` (stdlib) — URL-safe encoding/decoding
- `re` (stdlib, already imported in parser.py) — rider name extraction
- Existing: FastAPI, Pydantic, httpx, Jinja2
