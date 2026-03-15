# Route Contracts: Racer Schedule Lookup

## Modified Routes

### GET /schedule/{event_id}

**Changed**: Accepts optional `r` query parameter for racer name.

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `event_id` | path | `int` | yes | tracktiming.live EventId |
| `r` | query | `str` | no | URL-safe Base64 encoded racer name |

**Name resolution priority**:
1. `r` query parameter (decoded from Base64)
2. `racer_name` cookie value
3. None (no personalization)

**Side effects**:
- If `r` parameter present and valid, sets/updates `racer_name` cookie
- If name resolved, includes `racer_name` in template context and match data in predictions

**Response**: HTML page (`schedule.html`) with optional racer highlighting.

---

### GET /schedule/{event_id}/refresh

**Changed**: Accepts optional `r` query parameter (same as above).

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `event_id` | path | `int` | yes | tracktiming.live EventId |
| `r` | query | `str` | no | URL-safe Base64 encoded racer name |

**Response**: HTML partial (`_schedule_body.html`) with optional racer highlighting.

**Note**: The `hx-get` URL in `schedule.html` includes the `?r=` parameter so it persists across polling cycles (FR-007).

---

### GET /settings/racer-name

**New route**: Sets or clears the racer name cookie, then redirects back to schedule.

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `event_id` | query | `int` | yes | Competition to redirect back to |
| `name` | query | `str` | no | Racer name (plain text). Empty or absent = clear. |

**Behavior**:
- If `name` present and non-empty: Base64-encode, set cookie, redirect to `/schedule/{event_id}?r=<encoded>#schedule-container` (fragment scrolls to schedule content, FR-016)
- If `name` absent or empty: delete cookie, redirect to `/schedule/{event_id}`

**Response**: 303 redirect.

## Template Contract

### schedule.html

**New template variables**:

| Variable | Type | Description |
|----------|------|-------------|
| `racer_name` | `str \| None` | Decoded racer name for input pre-fill |
| `racer_encoded` | `str \| None` | Base64-encoded name for URL construction |

### _schedule_body.html

**New template variables**:

| Variable | Type | Description |
|----------|------|-------------|
| `racer_name` | `str \| None` | For "no matches" / "N events without start lists" messaging |
| `match_count` | `int` | Number of matched events |
| `events_without_start_lists` | `int` | For FR-010 messaging |
| `total_events` | `int` | Total non-special events across all sessions (for FR-010 "no data" conditional) |

**Per-prediction variables** (via `Prediction.rider_match`):

| Variable | Type | Description |
|----------|------|-------------|
| `prediction.rider_match` | `RiderMatch \| None` | Heat assignment and per-heat predicted start |
