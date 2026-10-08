# Contract: Schedule View with Rider List Matches

Routes, query parameters and cookies don't change. `/schedule/{event_id}` and `/schedule/{event_id}/refresh` render `_schedule_body.html` with the changes below.

## Module interfaces

### `app/parser.py`

```python
def parse_rider_list_url(jxn_data: dict) -> str | None
    """href of the 'Rider List' row in the top-level `documents` jxnobj, or None."""

def parse_rider_list(html: str) -> list[RiderListEntry]
    """Rows of a Rider List page. Strips inline data: images first. [] if no rows."""
```

### `app/rider_list.py` (new; pure functions, no I/O)

```python
CODE_DISCIPLINES: dict[str, frozenset[str]]       # R8 table
def category_band(category: str) -> AgeBand | None
def event_band(event_name: str) -> AgeBand | None
def event_code(discipline: str) -> str | None
def find_rider(entries: list[RiderListEntry], user_tokens: frozenset[str]) -> RiderListEntry | None
def match_events(entry: RiderListEntry, sessions: list[Session]) -> dict[tuple[int, int], RiderMatch]
    """Keyed by (session_id, position); certainty per code over the rider's matched events."""
```

### `app/predictor.py`

```python
def record_rider_list(url: str, entries: list[RiderListEntry]) -> None
def get_rider_list(url: str) -> list[RiderListEntry] | None
def record_rider_list_failure(url: str, now: float) -> None
def rider_list_retry_pending(url: str, now: float) -> bool
def predict_schedule(..., rider_list: list[RiderListEntry] | None = None) -> SchedulePrediction
def predict_session(..., rider_list_matches: dict[tuple[int, int], RiderMatch] | None = None) -> SessionPrediction
```

### `app/main.py`

```python
async def _fetch_rider_list(client, url: str) -> list[RiderListEntry] | None
    """Cached by URL; fetch+parse on miss; None on fetch failure or 0 rows (retried after 10 min)."""

async def _fetch_rider_list_if_needed(client, competition_id, jxn_data, sessions, racer_name) -> list[RiderListEntry] | None
    """Applies the R5 condition, then calls _fetch_rider_list. One entry in each route's gather."""
```

Both schedule routes resolve the racer name **before** the upstream `gather`. When the fetch condition (research R5) holds, they add `_fetch_rider_list` to the `gather`. `_collect_palmares_entries` skips matches where `source != "start_list"`.

## Rendered output

### Event row (`pred.rider_match` set)

| Match | Badge | Row classes | `aria-label` | Heat line |
|---|---|---|---|---|
| start list, `heat_count > 1` | `Heat N` (unchanged) | `racer-row bg-info/5` | `Your event` | `Your heat: HH:MM` (unchanged) |
| start list, single heat | `Racing` (unchanged) | `racer-row bg-info/5` | `Your event` | — |
| rider list, not tentative | `Entered` (`racer_badge`) | `racer-row bg-info/5` | `Your event` | — |
| rider list, tentative | `If advancing` (`racer_badge` + `border-dashed opacity-70`) | no racer tint | `Your event, if advancing` | — |

The template checks `source == "rider_list"` before any `heat_count` comparison, because `heat_count` is `None` for those matches.

### Banners (in order)

1. `match_count > 0`: `Found {N} event(s) for "{name}"`, with ` ({M} if advancing)` appended when `tentative_match_count > 0`.
2. Next race: unchanged wording, with ` (if advancing)` after the event name when `next_race.tentative` (for both "Your next race" and "Racing now"). The heat suffix only appears when `heat_count` is set and greater than 1. For a Rider List match, the time is the event's predicted start, since there is no heat. When `next_race.parallel_qualifier`, the line reads `Your next race: {event} (or a later qualifier), be ready by HH:MM` (or `Racing now: {event} (or a later qualifier)`).
3. Palmares count: unchanged.
4. If `rider_list_entry` is set: info line `Events without start lists matched from the Rider List ({category}: {codes sorted, comma-separated}). Start lists replace these once posted.` Otherwise the two existing start-list warnings, with their current conditions.
5. `No matching events found …`: unchanged.

### Session `<details>`

`has_pending_racer_match` is true for any pending match, from either source. The auto-open behaviour is unchanged.

## Logging

- `racer_name_resolved` gains `rider_list_matches` and `tentative_matches`.
- Warnings: Rider List fetch failure (with URL) and parse returning 0 rows (with URL), following the `_fetch_start_lists` pattern.
