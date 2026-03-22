# DynamoDB Import Reload: Fix Idempotent Re-Load of Corrected Data

The duration data loader (`tools/load_durations.py`) has an idempotency gap in the DynamoDB backend that prevents re-loading corrected data.

## Current behavior

- **SQLite**: `INSERT OR REPLACE` on the natural key `(competition_id, session_id, event_position)` correctly overwrites the old row. Re-loading with fixed data works.
- **DynamoDB**: The `OBS#<comp>#<sess>#<pos>` item from the first load causes the function to return immediately on line 184 of `app/database.py`. Fixed data is silently ignored. The running-total aggregates (`AGGREGATE#` items at 4 levels) are never corrected.

## Desired behavior

Re-running `python -m tools.load_durations` on a JSON file with corrected durations should update both the observation record and all aggregate totals, for both backends. The common case (identical re-load) should remain cheap.

## Constraints

- The DynamoDB table uses a pk-only single-table design (no sort key) — see CLAUDE.md for item patterns
- Aggregates are running totals (`total_minutes` + `count`), not raw observation stores — correcting them requires subtracting the old value and adding the new one
- Concurrent Lambda invocations may call `record_duration_structured()` via the live app path while the CLI loader runs — the fix must not introduce double-counting
- The existing `record_duration()` function (used by the live app's wall-clock learning) should not be affected
- Constitution VII (Prediction Integrity) requires that aggregate data remains accurate

## Key files

- `app/database.py` — `_dynamo_record_duration_structured()` (line 160), `_build_aggregate_keys()` (line 226)
- `tools/load_durations.py` — `load_report()` (line 55)
- `tests/test_loader_dynamo.py` — existing DynamoDB loader tests
- `specs/002-duration-data-import/research.md` §6 — original idempotent loading design decisions
