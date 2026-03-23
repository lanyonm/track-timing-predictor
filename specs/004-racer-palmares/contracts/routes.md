# Route Contracts: 004-racer-palmares

All routes are GET (CloudFront OAC constraint).

## GET /palmares

**Purpose**: Palmares profile page

**Parameters**:
| Param | Source | Type | Required | Description |
|-------|--------|------|----------|-------------|
| r | Query | str | No | Base64-encoded racer name |
| racer_name | Cookie | str | No | Plaintext racer name (fallback if `r` absent) |

**Resolution**: `r` param takes priority; decoded via `base64.urlsafe_b64decode`. Falls back to `racer_name` cookie. If neither present, page shows name input form.

**Response**: HTML page
- Identified with entries → card-based palmares grouped by competition
- Identified with no entries → empty state with guidance
- Unidentified → name input form

**Cookie behavior**: Does NOT set racer_name cookie (shared link must be read-only per FR-007). Cookie is only set via `/settings/racer-name`.

---

## GET /palmares/export

**Purpose**: CSV export of individual audit result data for a specific event

**Parameters**:
| Param | Source | Type | Required | Description |
|-------|--------|------|----------|-------------|
| r | Query | str | No | Base64-encoded racer name |
| racer_name | Cookie | str | No | Fallback racer identifier |
| audit_url | Query | str | Yes | Relative audit page URL (from palmares entry) |

**Input Validation (SSRF protection)**:
- `audit_url` MUST start with `results/`
- `audit_url` MUST NOT contain `://` or `..`
- Invalid values return HTTP 400

**Response**:
- Success → `text/csv` file download with `Content-Disposition: attachment; filename="{event}-{racer}.csv"`
- Audit page unavailable → JSON error `{"error": "..."}` with HTTP 502
- Racer not found in audit data → CSV with headers only + `X-Palmares-Notice: no-matching-data` header
- Missing racer identity → HTTP 400
- Invalid audit_url (SSRF attempt) → HTTP 400

---

## GET /palmares/remove

**Purpose**: Delete all palmares entries for a specific competition

**Parameters**:
| Param | Source | Type | Required | Description |
|-------|--------|------|----------|-------------|
| competition_id | Query | int | Yes | Competition to remove |
| racer_name | Cookie | str | Yes | Must be set (cookie-only, not via `r=` param) |

**Authorization**: Requires `racer_name` cookie to be set. If accessed via `r=` param only (shared link), returns HTTP 403. This enforces read-only shared links.

**Response**: Redirect to `/palmares` (303) after deletion.

---

## Modified Routes

### GET /schedule/{event_id} (existing)

**Addition**: After prediction, collect matched events with audit URLs and save to palmares database. Pass `palmares_count` to template context for the racer info message.

### GET /schedule/{event_id}/refresh (existing)

**Addition**: Same palmares collection logic on each HTMX refresh. Updated `palmares_count` included in partial response.

### GET /settings/racer-name (existing)

**Addition**: When name is set and palmares page is desired, the existing redirect to schedule is unchanged. The palmares page has its own name entry form (FR-010).
