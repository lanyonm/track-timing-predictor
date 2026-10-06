# Research: Rider List Fallback Matching

**Feature**: 006-rider-list-matching | **Date**: 2026-10-06

All findings come from EventId 26037, captured on 2026-10-06 while sessions 1–3 were complete or in progress.

## Fixtures

| File | Source | Notes |
|---|---|---|
| `tests/fixtures/schedule-26037.json` | `POST eventpage.php?EventId=26037`, `getInitialPageLayout` | Unmodified. 14 sessions; 240 non-special events NOT_READY with no start list, 25 completed, 8 upcoming with start lists |
| `tests/fixtures/refresh-26037.json` | `POST eventpage.php?EventId=26037`, `refreshPage` | Unmodified |
| `tests/fixtures/rider-list-26037.html` | `GET results/E26037/X-RIDERLIST-0-0-S.htm` | Inline `data:` flag `<img>` tags removed (2.38 MB → 125 KB); everything else byte-identical |

## R1: Where the Rider List link lives

**Decision**: Read the `documents` jxnobj (`cmd: "as"`, `id: "documents"`), find the table row whose `<h4>` text is exactly `Rider List`, and take the `href` of the `<a>` in that row.

**Rationale**: Both the initial-layout and refresh responses carry `documents` as a top-level jxnobj, so both routes can find the link without an extra request. The refresh version also has a "Click for Live Results!" banner ahead of the `<details>`, so the lookup has to match on the row label rather than position. Medal Standings (`X-SUMMARY-0-0-R.htm`) sits in the same table and must be ignored.

**Evidence**: `schedule-26037.json` and `refresh-26037.json`, `documents` objects.

**Alternatives considered**: Building the URL from the pattern `results/E{id}/X-RIDERLIST-0-0-S.htm` was rejected because nothing shows the name is stable across competitions, and the user expects a revised list would get a new name.

## R2: Rider List format

**Decision**: Parse `<table>` → `<tbody>` → `<tr>`. Cells by index: 0 bib, 1 name, 2 category, 3 team, 4 flag image, 5 nation code, 6 event codes (whitespace-separated). Rows with fewer than 7 cells are skipped.

**Rationale**: All 480 rows follow this layout. The header row is in `<thead>`. Names use the same `SURNAME Given` format as start lists, so `normalize_rider_name` token-set equality works unchanged.

**Evidence**: `rider-list-26037.html`: "Number of Entries: 480", first row `250 | ABERS Brian | M6064 | UNITED STATES | (img) | USA | TS S TT`.

**Observed values** (the complete sets in 26037):
- Categories: `M3539 M4044 M4549 M5054 M5559 M6064 M6569 M7074 M7579 M8084 M8589 M90 W3539 W4044 W4549 W5054 W5559 W6064 W6569 W7074 W7579`
- Codes: `S TT IP TP TS SCR PTS`

**Other competitions** (found while writing tasks): 26008 (`tests/fixtures/sample-event-output.json`) and 26009 also link a Rider List, with the same 7-column table. Their categories are non-masters (`ME`, `WE`, `MMA`–`MMD`, `MJ`, `WJ`, `MU17`, `WU17`, `U11`, `U13`, `WM`) and they have extra codes: `K` Keirin, `O` International Omnium, `TEMPO` Tempo, `MAD` Madison (meanings confirmed by the user, 2026-10-06). Under this feature's scope those categories produce no fallback matches. Supporting them is follow-up work and needs these lists captured as fixtures.

## R3: Stripping inline images

**Decision**: Before parsing, apply `re.sub(r'<img[^>]*src="data:[^"]*"[^>]*>', '', html)`.

**Rationale**: The images are 95% of the bytes. Stripping takes under 1 ms; parsing the stripped HTML takes about 43 ms, against about 58 ms unstripped. The saving is small, but it keeps BeautifulSoup's tree and memory use down on Lambda.

**Evidence**: Timing measured locally on the live file.

## R4: Fetch cost and caching

**Decision**: Cache the parsed entries by Rider List URL in a module-level dict for the life of the container, with no TTL. Cache a successful parse even when it yields 0 rows (with a warning). Don't cache a failed fetch, so the next request retries.

**Rationale**: The download is 417 KB gzip and takes about 1.1–1.3 s. The file is immutable, according to the user. In-memory caching matches the existing predictor caches (`_start_list_riders` and the rest). A 0-row parse is deterministic for an immutable file, so retrying it would only repeat a 1 s download every 30 s.

**Alternatives considered**: A TTL was dropped after the user confirmed the file doesn't change. Persisting the cache in DynamoDB or SQLite was rejected: it adds a storage concern for a value that costs one download per container.

## R5: When to fetch

**Decision**: Fetch, concurrently with the start-list/result/live-heat fetches, when a racer name is set AND at least one non-special event either has no `start_list_url` or has an empty cached start list.

**Rationale**: The real condition ("lacks start-list riders") isn't known until the start lists are fetched. Waiting for them would add the rider-list download to the critical path. The pre-fetch approximation is exact for 26037 (240 events with no start-list URL). An event whose start list later parses to 0 riders is picked up on the next refresh through the empty-cache check.

## R6: Event name → age band and gender

**Decision**: Use the regex `\b(\d{2})(?:-(\d{2})|\+)\s+(Men|Women)\b` on the event name. `NN+` means an open upper bound.

**Rationale**: It matches all 240+33 non-special event names in 26037 (prototype: 0 names without a band). Band shapes seen are `35-39`, `35-44`, `35-49`, `55-64`, `65-74`, `50+`, `55+`, `65+`, `75+`, `80+` and `90+`.

**Evidence**: `schedule-26037.json`.

## R7: Rider category → age band

**Decision**: Use `^([MW])(\d{2})(\d{2})?$`. Four digits means lo–hi; two digits means lo and over (only `M90` is seen). A rider matches an event when `event.lo ≤ rider.lo` and `rider.hi ≤ event.hi` (with open = ∞) and the genders match.

**Rationale**: It covers every category in R2. Containment, rather than overlap, keeps `M6064` out of "65-74", and still puts `W6569` into both "55+" and "65+" Women Team Sprint, as the spec agreed.

## R8: Code → discipline and grouping

**Decision**: Map discipline keys from `detect_discipline` to codes:

| Discipline key(s) (seen in 26037) | Code |
|---|---|
| `sprint_qualifying`, `sprint_match` | S |
| `time_trial_500`, `time_trial_750`, `time_trial_kilo` | TT |
| `pursuit_2k`, `pursuit_3k` | IP |
| `team_pursuit` | TP |
| `team_sprint` | TS |
| `scratch_race` | SCR |
| `points_race` | PTS |

The "only event in its group" certainty rule groups on **(band, gender, code)**, not on discipline key.

**Rationale**: Sprint qualifying and sprint match rounds have different discipline keys. Grouping by discipline would wrongly make every sprint group look like it has only one event. Keys not seen in 26037 (`pursuit_4k`, `time_trial_generic`, `keirin`, etc.) are left out of the map, per the user's narrow-scope instruction.

**Evidence**: Prototype run against the fixtures:
- ABERS Brian (M6064; S TS TT): 13 matches. Entered: 60-64 Sprint Qualifying, 60-64 500m TT Final, 55-64 Team Sprint Qualifying. The other 10 are if advancing.
- FOWLER Walter (M90; TT): 90+ Men 500m Time Trial Final, entered.
- ACHILER Becky (W4549; TP TS): 45-54 Women TS/TP Qualifying entered; the finals are if advancing.

## R9: Certainty

**Decision**: The match is entered when the name matches `\bQualif(?:ying|ier\s+\d+)\b` (case-insensitive) or its (band, gender, code) group has exactly one event in the whole competition. Otherwise it is if advancing.

**Rationale**: This implements FR-010. The prototype on 26037 shows sprint rides, pursuit/TP/TS finals, and points/scratch finals with qualifiers all come out as if advancing, while solo TT and points finals are entered.

## R10: Empty start lists

**Decision**: A start list cached with 0 riders counts as no start list, both for the Rider List fallback and for `events_without_start_lists`. `_fetch_start_lists` refetches start lists cached as empty.

**Rationale**: This implements the clarification. Refetching an empty start list costs one small request per refresh, and it also picks up a start list that gets filled in later, which the current code never does.

## R11: Palmares

**Decision**: `_collect_palmares_entries` only considers matches with `source == "start_list"`.

**Rationale**: FR-012. A Rider List match doesn't prove the racer rode the event, and in practice every timed event with an audit URL has a start list.
