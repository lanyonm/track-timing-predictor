# Implementation Plan: Rider List Fallback Matching

**Branch**: `006-rider-list-matching` | **Date**: 2026-10-06 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/006-rider-list-matching/spec.md`

## Summary

When a competition publishes a Rider List (in the Event Documents block) and events have no start-list riders, find the racer's row, take their masters age band and entered event codes, and match them against schedule events by age band, gender and discipline code. Matches are "entered" (Qualifying, Qualifier N, or the rider's only matched event for that code) or "if advancing", carry no heat, and are rendered with new badges and a replacement banner. The Rider List is fetched once per container, concurrently with the existing upstream fetches, and cached by URL.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: FastAPI, httpx, BeautifulSoup4, Pydantic, Jinja2 (all existing; nothing new)
**Storage**: None new. The in-memory `_rider_lists` cache lives in `app/predictor.py`
**Testing**: pytest with captured fixtures (`tests/fixtures/*26037*`)
**Target Platform**: AWS Lambda (container image) behind CloudFront; local uvicorn
**Project Type**: web service (server-rendered HTML + HTMX)
**Performance Goals**: no added latency on warm requests (cache hit); on a cold container the Rider List download (~1.1 s) runs in the same `gather` as the start-list fetches
**Constraints**: GET-only routes (unchanged); Lambda 512 MB (a cached list is ~480 small objects); scope limited to 26037 formats
**Scale/Scope**: one Rider List per competition; ~300 events × 1 rider comparison per request

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Graceful Degradation | ✅ | A missing document, failed fetch or failed parse leaves today's behaviour. UI is server-rendered and needs no JS |
| II. Testable without external deps | ✅ | Three fixtures captured from live 26037 data; parsers tested against them; route tests mock the fetchers |
| III. Separation of Concerns | ✅ | Parsing in `parser.py`, matching rules in a new pure `rider_list.py`, cache in `predictor.py`, fetching in `main.py`. Upstream codes and categories are mapped inside `rider_list.py` only |
| IV. Minimal Dependencies | ✅ | None added |
| V. Operability | ✅ | Fetch and parse warnings; `racer_name_resolved` log gains rider-list counts |
| VI. Cost-Aware Growth | ✅ | One ~417 KB gzip download per competition per container; no new AWS services |
| VII. Prediction Integrity | ✅ | Predictions are unchanged. Matching is conservative: unknown formats produce no match, and uncertain rounds are labelled |
| VIII. Security & Data Minimization | ✅ | Rider List names, categories and codes are public competition data, held in memory only and never persisted. Team and nation are not kept in the model |
| External Data Sources | ✅ | Defensive parsing; format assertions backed by committed fixtures; fetch → parse → model pattern; shared lifespan httpx client |
| GET-only routes | ✅ | No route changes |

Post-design re-check: still passes; no Complexity Tracking entries.

## Research Phase: External Data Formats

Captured during research and committed to `tests/fixtures/`:
- `schedule-26037.json`: initial layout (contains `documents`)
- `refresh-26037.json`: refresh response (also contains `documents`)
- `rider-list-26037.html`: Rider List, with inline `data:` images stripped

Formats are documented in [research.md](research.md), R1–R3 and R6–R8.

## Research Findings

| Finding | Impact | Evidence |
|---|---|---|
| Rider List link is in the top-level `documents` jxnobj of both initial and refresh responses | Both routes can locate it with no extra request; match on the row label `Rider List` | `schedule-26037.json`, `refresh-26037.json` |
| Rider List rows: bib, name, category, team, flag img, nation, codes | Parser reads cells 1, 2 and 6 | `rider-list-26037.html` |
| 95% of the Rider List bytes are inline base64 flags | Strip them before parsing | `rider-list-26037.html` (stripped from 2.38 MB) |
| Categories are `[MW]\d{4}` or `M90` only; codes are `S TT IP TP TS SCR PTS` only | Narrow category regex and code map | `rider-list-26037.html` |
| Every non-special event name has `NN-NN` or `NN+` followed by `Men`/`Women` | Event band regex | `schedule-26037.json` |
| Sprint qualifying and sprint match have different discipline keys | Group certainty by (band, code), not discipline | `schedule-26037.json` via `detect_discipline` |
| Completed and upcoming events all have start lists; the 240 NOT_READY events have none | The fallback only affects future events in practice | `schedule-26037.json` |

## Project Structure

### Documentation (this feature)

```text
specs/006-rider-list-matching/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/schedule-view.md
├── checklists/requirements.md
└── tasks.md             # /speckit.tasks
```

### Source Code (repository root)

```text
app/
├── models.py            # + RiderListEntry; RiderMatch/NextRace optional heat, source, tentative; SchedulePrediction fields
├── parser.py            # + parse_rider_list_url, parse_rider_list
├── rider_list.py        # NEW: bands, code map, certainty, match_events (pure)
├── predictor.py         # + _rider_lists cache; has_start_list_riders non-empty; rider-list branch in predict_session; next race/tentative counts
├── main.py              # + _fetch_rider_list; resolve racer before gather; palmares skips rider_list matches; log fields
└── templates/_schedule_body.html   # badges, banners, None-safe heat checks

tests/
├── fixtures/schedule-26037.json, refresh-26037.json, rider-list-26037.html
├── test_parser.py       # + rider list URL + rows
├── test_rider_list.py   # NEW: band/category/code/certainty units + fixture-driven matching
├── test_rider_matching.py  # + predictor precedence, empty start list, counts, next race
└── test_main.py         # + route rendering, single fetch across two requests, palmares untouched

CLAUDE.md, README.md     # docs in the same commit
```

**Structure Decision**: Existing single-project layout. A new module, `app/rider_list.py`, keeps the matching rules out of `predictor.py` (already ~500 lines; see issue 011) and testable as pure functions.

## Implementation Notes

- `has_start_list_riders` changes to non-empty. Check the existing tests that seed `[]`; `get_rider_match` already returns None for empty lists.
- `predict_schedule` finds the racer's `RiderListEntry` once (`find_rider`) and computes `match_events` once over all sessions. If the category has no band, treat it as no entry. `predict_session` receives the matches dict.
- Next race: the existing active/upcoming selection is unchanged, and `_build_next_race` copies `tentative`. Heat fields can be None.
- Template: test `source == 'rider_list'` before comparing `heat_count` (Jinja raises on `None > 1`).
- `refresh_schedule` currently resolves the racer after the `gather`; move it before, to decide whether to fetch.
- Docs: in CLAUDE.md, update the request-flow step 3, the API notes (`documents` block), the in-memory caches list (`_rider_lists`), and add a Rider List matching paragraph under Palmares/Disciplines. In README, update the racer matching description.

## Complexity Tracking

No constitution violations.
