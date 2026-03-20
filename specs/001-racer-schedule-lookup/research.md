# Research: Racer Schedule Lookup

**Date**: 2026-03-14

## R1: Start List HTML Structure & Rider Name Parsing

**Decision**: Extend `parser.py` with a new `parse_start_list_riders()` function that extracts rider names and their heat assignments from start list HTML.

**Rationale**: Start list HTML follows a consistent plain-text format: `Heat N` headers followed by lines like `212  PITTARD Charlie`. The existing `parse_heat_count()` already identifies heat boundaries using `\bHeat\s+\d+\b`. A new function can reuse this pattern to segment riders into heats.

**Format evidence**: The format assertion above is based on observation of live tracktiming.live responses. Per constitution (External Data Sources), this assertion MUST be validated by a captured fixture committed to `tests/fixtures/start-list-sample.html` before parser implementation begins (see T001). The fixture serves as the contract between the parser and the upstream data source.

**Alternatives considered**:
- BeautifulSoup parsing: Start lists are plain text, not structured HTML tables — regex is simpler and consistent with existing `parse_heat_count()` approach.
- Parsing bib numbers: Bib numbers appear in the text (`212  PITTARD Charlie`) but are not needed for name matching. Could be useful for future disambiguation but out of scope.

## R2: Name-Order Normalization Strategy

**Decision**: Normalize both input and start list names to a canonical set of lowercase tokens, then compare as frozensets. The full normalization pipeline is: Unicode NFKD decomposition → strip non-ASCII characters → remove apostrophes, hyphens, and periods → split on whitespace → lowercase each token → frozenset.

**Rationale**: tracktiming.live uses "LASTNAME Firstname" format. Users may enter "Firstname Lastname". By splitting both strings into whitespace-separated tokens, lowercasing, and comparing as frozensets, "Sean Hall" matches "HALL Sean" without needing to detect which token is first/last name. This also handles minor whitespace differences. The Unicode NFKD and punctuation stripping steps are necessary because cycling start lists include international names with diacritics (e.g., "MULLER" for "Müller") and punctuated names (e.g., "O'BRIEN"). See R7 for the full rationale on Unicode normalization.

**Alternatives considered**:
- Detect first/last name heuristically (e.g., all-caps = last name): Fragile and unnecessary when set comparison achieves the same result.
- Require exact format: Poor UX for first-time visitors who don't know tracktiming format.

## R3: Racer Name Cookie & Constitution Principle VIII

**Decision**: Store racer name in a cookie with `httponly`, `secure`, `samesite=lax` attributes and a 1-year expiry (`Max-Age=31536000`). Document in spec assumptions that this is the user's own name entered voluntarily for personalization, following the existing `use_learned` cookie pattern.

**Rationale**: Constitution Principle VIII says "No user accounts, no tracking, no unnecessary cookies" and "The app MUST NOT transmit or store PII or other sensitive data." The racer name cookie is:
- **Necessary**: Required for the returning-user auto-apply feature (FR-009, SC-007). Without it, day-2 multi-day competition UX degrades.
- **PII consideration**: Racer names are publicly available on tracktiming.live start lists. The cookie stores what the user voluntarily enters for their own convenience. The cookie is httponly (not accessible to JS) and not transmitted to third parties.
- **Expiry**: 1-year duration supports multi-competition returning users and lays groundwork for the future palmares feature. A shorter expiry would fail the multi-day competition use case if the competition spans a browser session boundary.
- **Precedent**: The `use_learned` cookie pattern already exists and is accepted.

This is a justified use under Principle VIII but should be flagged in the Complexity Tracking table.

**Alternatives considered**:
- localStorage: Not accessible server-side; would require client-side JS to read and inject into requests, breaking the server-rendered HTMX model.
- Session-only (no persistence): Fails the returning-user story (US4).
- No persistence: User re-enters name every visit — poor UX for multi-day competitions.

## R4: URL Encoding Strategy (Base64)

**Decision**: Use URL-safe Base64 encoding (`base64.urlsafe_b64encode` / `base64.urlsafe_b64decode`) for the `?r=` query parameter. Server decodes on receipt; the URL is updated on form submission as a GET redirect.

**Rationale**: URL-safe Base64 is available in Python stdlib (`base64` module). It prevents casual readability of names in URLs (cosmetic concern, not security). The URL-safe variant avoids `+` and `/` characters that would require additional URL-encoding in query parameters. The GET-only constraint (constitution: Development Workflow) means the name input form submits as a GET with the encoded name as a query parameter, naturally updating the URL.

**Alternatives considered**:
- Standard Base64 (`base64.b64encode`): Produces `+` and `/` characters that require URL-encoding, creating double-encoded values in query strings. URL-safe variant is strictly better.
- Plain text query param: Name visible in URLs, browser history, shared links.
- Encryption: Overkill — names are public data on tracktiming.live.

## R5: HTMX Refresh Name Preservation

**Decision**: The HTMX refresh endpoint (`/schedule/{id}/refresh`) reads the racer name from a query parameter passed via the `hx-get` URL. The cookie serves as the initial source on page load; the query parameter is the authoritative source during polling.

**Rationale**: HTMX `hx-get` sends the URL as configured in the attribute. By including `?r=<encoded_name>` in the `hx-get` URL (set at page render time), every refresh request carries the name. This is consistent with the GET-only constraint and doesn't require HTMX to read cookies.

**No-JS fallback** (constitution I: core functionality MUST work without JavaScript): Without HTMX, there is no automatic polling — but the racer name persists via two mechanisms: (1) the `?r=` parameter in the URL after form submission (FR-008), so manual page refreshes retain the name, and (2) the cookie auto-applies on page load for returning users (FR-009). The HTMX polling path is a progressive enhancement, not a gate.

**Alternatives considered**:
- Read cookie server-side on every refresh: Works but creates implicit coupling between cookie state and refresh behavior. If user clears cookie mid-session, refresh silently loses highlighting.
- HTMX `hx-vals`: Adds the name as a request parameter but requires JS to set. The `hx-get` URL approach is simpler.

## R6: Per-Heat Predicted Start Time Calculation

**Decision**: For a racer in heat H of an event with N heats, their predicted start time = event predicted start + (H-1) × per_heat_duration.

**Rationale**: The predictor already has `get_per_heat_duration(discipline)` which returns the estimated duration per heat. Heat numbering is 1-based. The racer's heat assignment comes from the start list parser. This calculation introduces no additional error beyond what exists in per-heat duration estimates (satisfying SC-003).

**Validation** (constitution VII: new prediction methods MUST include explicit validation logic): The per-heat calculation's inputs are already validated and bounded: `per_heat_duration` comes from `get_per_heat_duration()` which returns discipline-specific constants (already plausibility-checked), and heat numbers are parsed from start list HTML where they are sequential integers starting from 1. The output (heat predicted start) is inherently bounded between the event start time and the event end time (event_start + total_event_duration). No additional validation beyond the input constraints is needed — the formula is a deterministic linear interpolation of already-validated values.

**Alternatives considered**:
- Use active_heat to adjust in real-time: The existing `active_heat` tracking already shows which heat is in progress. Combining this with the racer's assigned heat gives a more accurate "time until your heat" during live sessions. This enhancement can be layered on top of the base calculation.

## R7: Unicode & Diacritics Normalization Strategy

**Decision**: Apply Unicode NFKD decomposition, strip non-ASCII characters, and remove apostrophes, hyphens, and periods before tokenizing names for matching. Implementation: `unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')`, then strip `'`, `-`, `.`.

**Rationale**: International track cycling start lists include names with diacritics (e.g., "MÜLLER Hans", "LÓPEZ Maria") and punctuated names (e.g., "O'BRIEN Liam", "SMITH-JONES Kate"). The tracktiming.live system sometimes normalizes these to ASCII (e.g., "MULLER") and sometimes preserves them. Users entering their own names may or may not include diacritics or punctuation. NFKD decomposition followed by ASCII stripping converts diacritics to their base characters ("ü" → "u"), and punctuation removal ensures "O'Brien" matches "OBRIEN". This makes matching robust across input variations without requiring users to know the exact format stored in the start list.

**Alternatives considered**:
- No Unicode normalization (exact match after lowercasing): Would fail to match "Muller" against "MÜLLER" — common in international cycling. Poor UX for users who don't have easy access to diacritic input on their phone keyboard at the velodrome.
- Fuzzy/Levenshtein matching: More tolerant but introduces false positives (e.g., "Hall" matching "Hull"). Full-name exact-token matching with normalization provides the right balance of tolerance and precision.
- ICU/locale-aware collation: More correct for edge cases but adds a dependency (`PyICU` or similar), violating constitution IV (minimal dependencies). NFKD + ASCII stripping covers the practical cases for cycling names.
