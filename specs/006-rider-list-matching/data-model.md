# Data Model: Rider List Fallback Matching

All models are Pydantic models in `app/models.py`. Nothing is persisted; the Rider List cache is in memory only.

## New: `RiderListEntry`

One row of a competition's Rider List.

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | As published, e.g. `ABERS Brian` |
| `category` | `str` | Raw category, e.g. `M6064`, `M90` |
| `codes` | `frozenset[str]` | Entered event codes, e.g. `{"S", "TS", "TT"}` |
| `normalized_tokens` | `frozenset[str]` | Computed from `name` via `normalize_rider_name` (same validator pattern as `RiderEntry`) |

## New: `AgeBand` (internal to `app/rider_list.py`)

A frozen dataclass or NamedTuple; it isn't exposed to templates.

| Field | Type | Notes |
|---|---|---|
| `gender` | `"M" \| "W"` | From the category letter, or from `Men`/`Women` in an event name |
| `lo` | `int` | |
| `hi` | `int \| None` | `None` = open upper bound (`90`, `80+`) |

`contains(other)`: true when the genders are equal, `self.lo <= other.lo`, and (`self.hi is None` or (`other.hi is not None` and `other.hi <= self.hi`)).

## Changed: `RiderMatch`

| Field | Before | After |
|---|---|---|
| `heat` | `int` (≥1) | `int \| None` (≥1 when set). `None` for Rider List matches |
| `heat_count` | `int` (≥1) | `int \| None` (≥1 when set). `None` for Rider List matches |
| `heat_predicted_start` | unchanged | Always `None` for Rider List matches |
| `team_name` | unchanged | Always `None` for Rider List matches |
| `source` | — | `Literal["start_list", "rider_list"]`, default `"start_list"` |
| `tentative` | — | `bool`, default `False`. True for "if advancing" |
| `parallel_qualifier` | — | `bool`, default `False`. True for one of several numbered qualifiers the rider matches for a code (rides only one, so the time is a be-ready-by time) |

## Changed: `NextRace`

| Field | Change |
|---|---|
| `heat`, `heat_count` | Become `int \| None` |
| `tentative` | New `bool`, default `False` |
| `parallel_qualifier` | New `bool`, default `False`; copied from the match |
| `predicted_start` | For Rider List matches, the event's predicted start (start-list matches keep the heat start) |

## Changed: `SchedulePrediction`

| Field | Type | Notes |
|---|---|---|
| `tentative_match_count` | `int = 0` | Count of matches with `tentative=True` |
| `rider_list_match_count` | `int = 0` | Count of matches with `source="rider_list"`; logged and used to set `rider_list_entry` |
| `rider_list_entry` | `RiderListEntry \| None = None` | The racer's Rider List row. Set only when at least one Rider List match exists; drives the info banner |

`match_count` includes Rider List matches. `events_without_start_lists` counts non-special events whose start-list riders are missing or empty, whether or not they matched from the Rider List.

## Cache: `_rider_lists` (`app/predictor.py`)

`dict[str, list[RiderListEntry]]`, keyed by the Rider List relative URL, non-empty lists only, with no expiry, per container. Accessors: `record_rider_list(url, entries)` and `get_rider_list(url) -> list[RiderListEntry] | None` (`None` = not cached). Failed or 0-row fetches go in `_rider_list_retry_at` (`record_rider_list_failure`, `rider_list_retry_pending`) and are retried after 10 minutes.

## Changed semantics: start-list presence

`has_start_list_riders(...)` returns True only when the cached list is **non-empty** (it used to be "key present"). This affects:
- the predictor's start-list vs Rider List decision and `events_without_start_lists` (FR-008, clarification);
- `_fetch_start_lists`, which now refetches empty cached start lists, except for COMPLETED events (fetched once). An empty parse never replaces cached riders.

## Matching flow (per event, in `predict_session`)

```
special event                         → no match
start-list riders non-empty           → existing get_rider_match (source=start_list)
else, count toward events_without_start_lists, then
  rider list entry for racer present  → rider_list_matches.get((session_id, position))
                                        → RiderMatch(source=rider_list, tentative=…) or None
```

`rider_list_matches` comes from `match_events(entry, sessions)`, computed once per `predict_schedule` call over the whole competition (certainty is per code over the rider's matched events, research R9) and passed into each `predict_session`.
