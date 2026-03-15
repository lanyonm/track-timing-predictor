# Research: Racer Schedule Lookup

**Date**: 2026-03-14

## R1: Start List HTML Structure & Rider Name Parsing

**Decision**: Extend `parser.py` with a new `parse_start_list_riders()` function that extracts rider names and their heat assignments from start list HTML.

**Rationale**: Start list HTML follows a consistent plain-text format: `Heat N` headers followed by lines like `212  PITTARD Charlie`. The existing `parse_heat_count()` already identifies heat boundaries using `\bHeat\s+\d+\b`. A new function can reuse this pattern to segment riders into heats.

**Alternatives considered**:
- BeautifulSoup parsing: Start lists are plain text, not structured HTML tables — regex is simpler and consistent with existing `parse_heat_count()` approach.
- Parsing bib numbers: Bib numbers appear in the text (`212  PITTARD Charlie`) but are not needed for name matching. Could be useful for future disambiguation but out of scope.

## R2: Name-Order Normalization Strategy

**Decision**: Normalize both input and start list names to a canonical set of lowercase tokens, then compare as sorted sets.

**Rationale**: tracktiming.live uses "LASTNAME Firstname" format. Users may enter "Firstname Lastname". By splitting both strings into whitespace-separated tokens, lowercasing, and comparing as sorted sets, "Sean Hall" matches "HALL Sean" without needing to detect which token is first/last name. This also handles minor whitespace differences.

**Alternatives considered**:
- Detect first/last name heuristically (e.g., all-caps = last name): Fragile and unnecessary when set comparison achieves the same result.
- Require exact format: Poor UX for first-time visitors who don't know tracktiming format.

## R3: Racer Name Cookie & Constitution Principle VIII

**Decision**: Store racer name in a cookie with `httponly`, `secure`, `samesite=lax` attributes. Document in spec assumptions that this is the user's own name entered voluntarily for personalization, following the existing `use_learned` cookie pattern.

**Rationale**: Constitution Principle VIII says "No unnecessary cookies" and "MUST NOT store PII". The racer name cookie is:
- **Necessary**: Required for the returning-user auto-apply feature (FR-009, SC-007). Without it, day-2 multi-day competition UX degrades.
- **PII consideration**: Racer names are publicly available on tracktiming.live start lists. The cookie stores what the user voluntarily enters for their own convenience. The cookie is httponly (not accessible to JS) and not transmitted to third parties.
- **Precedent**: The `use_learned` cookie pattern already exists and is accepted.

This is a justified use under Principle VIII but should be flagged in the Complexity Tracking table.

**Alternatives considered**:
- localStorage: Not accessible server-side; would require client-side JS to read and inject into requests, breaking the server-rendered HTMX model.
- Session-only (no persistence): Fails the returning-user story (US4).
- No persistence: User re-enters name every visit — poor UX for multi-day competitions.

## R4: URL Encoding Strategy (Base64)

**Decision**: Use standard Base64 encoding for the `?r=` query parameter. Server decodes on receipt; the URL is updated on form submission as a GET redirect.

**Rationale**: Base64 is available in Python stdlib (`base64` module) and browser JS (`btoa`/`atob`). It prevents casual readability of names in URLs (cosmetic concern, not security). The GET-only constraint means the name input form submits as a GET with the encoded name as a query parameter, naturally updating the URL.

**Alternatives considered**:
- URL-safe Base64 (`base64.urlsafe_b64encode`): Better — avoids `+` and `/` characters that need URL-encoding. **Use this variant.**
- Plain text query param: Name visible in URLs, browser history, shared links.
- Encryption: Overkill — names are public data on tracktiming.live.

## R5: HTMX Refresh Name Preservation

**Decision**: The HTMX refresh endpoint (`/schedule/{id}/refresh`) reads the racer name from a query parameter passed via the `hx-get` URL. The cookie serves as the initial source on page load; the query parameter is the authoritative source during polling.

**Rationale**: HTMX `hx-get` sends the URL as configured in the attribute. By including `?r=<encoded_name>` in the `hx-get` URL (set at page render time), every refresh request carries the name. This is consistent with the GET-only constraint and doesn't require HTMX to read cookies.

**Alternatives considered**:
- Read cookie server-side on every refresh: Works but creates implicit coupling between cookie state and refresh behavior. If user clears cookie mid-session, refresh silently loses highlighting.
- HTMX `hx-vals`: Adds the name as a request parameter but requires JS to set. The `hx-get` URL approach is simpler.

## R6: Per-Heat Predicted Start Time Calculation

**Decision**: For a racer in heat H of an event with N heats, their predicted start time = event predicted start + (H-1) × per_heat_duration.

**Rationale**: The predictor already has `get_per_heat_duration(discipline)` which returns the estimated duration per heat. Heat numbering is 1-based. The racer's heat assignment comes from the start list parser. This calculation introduces no additional error beyond what exists in per-heat duration estimates (satisfying SC-003).

**Alternatives considered**:
- Use active_heat to adjust in real-time: The existing `active_heat` tracking already shows which heat is in progress. Combining this with the racer's assigned heat gives a more accurate "time until your heat" during live sessions. This enhancement can be layered on top of the base calculation.
