# Data Model: Racer Schedule Lookup

**Date**: 2026-03-14

## New Entities

### RiderEntry

Represents a rider's presence in a specific event's start list.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Rider name as it appears in the start list (e.g., "HALL Sean") |
| `heat` | `int` | 1-based heat number the rider is assigned to |
| `normalized_tokens` | `frozenset[str]` | Lowercase name tokens for matching (e.g., `{"hall", "sean"}`) |

**Source**: Parsed from start list HTML by `parse_start_list_riders()`.

**Lifecycle**: Created during start list parsing. Immutable once created. Cached in-memory alongside heat counts in `predictor.py`.

### RiderMatch

Represents a matched rider within a specific event prediction.

| Field | Type | Description |
|-------|------|-------------|
| `heat` | `int` | The heat number the matched rider is in |
| `heat_count` | `int` | Total number of heats for this event (from `_heat_counts` cache; 1 if not in cache) |
| `heat_predicted_start` | `datetime \| None` | Predicted start time for the rider's specific heat |

**Source**: Computed in `predictor.py` when a racer name matches a `RiderEntry`.

**Lifecycle**: Ephemeral — computed per request, included in `Prediction` model for template rendering.

## Modified Entities

### Prediction (existing, app/models.py)

New optional field:

| Field | Type | Description |
|-------|------|-------------|
| `rider_match` | `RiderMatch \| None` | Present when the queried racer name matches a rider in this event's start list |

### SessionPrediction (existing, app/models.py)

New optional field:

| Field | Type | Description |
|-------|------|-------------|
| `has_racer_match` | `bool` | True when any event in this session has a `rider_match`. Used by FR-015 to auto-open sessions containing racer events. Default `False`. |

### SchedulePrediction (existing, app/models.py)

New optional field:

| Field | Type | Description |
|-------|------|-------------|
| `racer_name` | `str \| None` | The racer name being searched for (decoded from URL param), or None if no name provided |
| `match_count` | `int` | Number of events where the racer was matched (0 if no name or no matches) |
| `events_without_start_lists` | `int` | Number of events that have no start list data (for FR-010 messaging) |
| `total_events` | `int` | Total count of non-special events across all sessions (for FR-010 "no data" conditional) |

## In-Memory Caches (predictor.py)

### New Cache

| Cache | Key | Value | Description |
|-------|-----|-------|-------------|
| `_start_list_riders` | `(competition_id, session_id, position)` | `list[RiderEntry]` | Parsed rider entries per event, populated alongside heat counts |

**Note**: This cache follows the same pattern as existing `_heat_counts` and `_observed_durations` caches. On Lambda, it persists within a warm execution environment but resets on cold starts.

## Name Matching Algorithm

```
Input: user_name (str)
Output: matching RiderEntry or None

1. Tokenize user_name: split on whitespace, lowercase each token → user_tokens: frozenset[str]
2. For each RiderEntry in event's start list:
   a. Compare user_tokens == rider.normalized_tokens
   b. If match → return RiderEntry
3. Return None
```

**Properties**:
- Case-insensitive: all tokens lowercased
- Order-independent: frozenset comparison
- Whitespace-tolerant: split handles multiple spaces
- Full-name only: exact token set match required

## Cookie Schema

| Cookie | Value | Attributes | Purpose |
|--------|-------|------------|---------|
| `racer_name` | Plain text name as entered by user | `httponly=True, samesite=lax, max_age=31536000` (1 year); match existing `use_learned` cookie pattern for `secure` attribute | Pre-fill and auto-apply on return visits |

**Note**: Cookie stores the user's original input (not normalized), so the input field shows what they typed. Normalization happens at match time.

## URL Parameter Schema

| Parameter | Encoding | Example |
|-----------|----------|---------|
| `r` | URL-safe Base64 of UTF-8 name string | `/schedule/26008?r=U2VhbiBIYWxs` |

**Decode flow**: `base64.urlsafe_b64decode(r_param).decode("utf-8")`

**Priority**: URL `?r=` parameter takes precedence over cookie. If both present, URL wins (supports shared links overriding the recipient's cookie).
