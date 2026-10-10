# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Documentation rule

**Every code change must update the affected documentation in the same commit.** This file, `README.md`, `plans/hosting-plan.md`, and `docs/*.md` describe the code *as it is now*, not as planned. If a change alters a route, env var, cookie, module responsibility, data model, CLI flag, deploy step or anything else stated in these docs, update the statement.

## Commands

Python 3.13 (matches the Lambda base image and CI).

```bash
uv venv --python 3.13 .venv && source .venv/bin/activate   # or python3.13 -m venv .venv
pip install --require-hashes -r requirements-dev.txt       # runtime + pytest, pytest-asyncio, pytest-cov, moto, ruff, mypy

uvicorn app.main:app --reload              # http://localhost:8000, try EventId 26008

pytest                                     # all tests (SQLite temp DB, no network, no AWS)
pytest tests/test_predictor.py::TestComputeDelay::test_positive_delay_when_behind

python -m tools.extract_competition 26008                 # → data/competitions/26008.json (gitignored)
python -m tools.load_durations data/competitions/*.json   # → learning DB; --force skips the dedup prompt
python -m tools.rebuild_aggregates [--apply]              # DynamoDB only: recompute AGGREGATE# items from OBS#; dry run by default
python -m tools.import_fullgas_26037 team-events.html schedule.html  # one-off: saved organiser pages → app/data/supplements/26037.json (committed)
```

**Dependencies:** ranges live in `pyproject.toml` (runtime in `dependencies`, tooling in the `dev` extra). `requirements.txt` (runtime, installed by the Dockerfile) and `requirements-dev.txt` (CI and local) are hashed locks generated from it; `cdk/requirements.txt` is a hashed lock of `cdk/requirements.in`. After changing a range, regenerate the locks and commit them:

```bash
uv pip compile pyproject.toml --universal --python-version 3.13 --generate-hashes -o requirements.txt
uv pip compile pyproject.toml --extra dev -c requirements.txt --universal --python-version 3.13 --generate-hashes -o requirements-dev.txt
uv pip compile cdk/requirements.in --universal --python-version 3.13 --generate-hashes -o cdk/requirements.txt
```

The dev lock is constrained by the runtime lock so shared packages match the image. The CDK CLI is pinned in the workflows (`npm install -g aws-cdk@<version>`); bump it with `aws-cdk-lib`. Dependabot (`.github/dependabot.yml`) proposes monthly updates for pip (root and `cdk/`), npm (`frontend/`, minor and patch only; rebuild `static/` on those PRs), GitHub Actions (pinned by commit SHA) and the Dockerfile base image (pinned by digest).

Lint, format and type checking (config in `pyproject.toml`; CI runs all three):

```bash
ruff check .                               # lint (E, F, I, B, UP, ASYNC); --fix applies safe fixes
ruff format .                              # format; CI runs ruff format --check
mypy                                       # type-check app/ (non-strict, check_untyped_defs)
```

**Environment variables** (`app/config.py`, `pydantic_settings`, no prefix):

| Variable | Default | Description |
|---|---|---|
| `TRACKTIMING_BASE_URL` | `https://tracktiming.live` | Upstream base URL for the shared httpx client |
| `DB_PATH` | `timings.db` | SQLite path (used when `DYNAMODB_TABLE` is empty) |
| `DYNAMODB_TABLE` | `""` | Enables the DynamoDB learning backend when set |
| `PALMARES_TABLE` | `""` | Enables the DynamoDB palmares backend when set |
| `AWS_REGION` | `us-east-1` | DynamoDB region |
| `REFRESH_INTERVAL_SECONDS` | `30` | HTMX polling interval passed to templates |
| `MIN_LEARNED_SAMPLES` | `3` | Samples required before a learned average is used |
| `VENUE_TZ` | `America/Toronto` | Fallback IANA timezone for "now" when no live session has results; validated at startup |
| `PUBLIC_BASE_URL` | `""` | Origin for the palmares share link; prod CDK sets `https://ttp.lanyonm.org`, empty uses the request host |

## Taxonomy

Competition (`competition_id`, upstream `EventId`) → Session (a day's block, `Session` model) → Event (one race, `Event` model) → Heat (`heat_count`, `active_heat`).

Route URLs and form fields use `event_id` (e.g. `/schedule/{event_id}`) to keep bookmarks working and match upstream `EventId`. Python code and templates use `competition_id`.

## Architecture

FastAPI app that predicts per-event start times for track cycling competitions on tracktiming.live.

**Deployment:** AWS Lambda (Docker image from ECR, `Dockerfile`) behind a Function URL, adapted with Mangum (`handler` in `app/main.py`). Prod sits behind CloudFront at `ttp.lanyonm.org`, which uses OAC to sign requests to an `AWS_IAM` Function URL. Infra is CDK in `cdk/`; details are in `plans/hosting-plan.md`. **All routes must be GET.** CloudFront OAC can't sign POST bodies to Function URLs (403).

**CI/CD** (`.github/workflows/`):
- `test.yml`: on pushes and PRs to `main`, a `lint` job (ruff check, ruff format --check, mypy), an `assets` job (rebuilds `frontend/` and fails if `static/` differs) and a `test` job (pytest with coverage); `test` updates the coverage badge gist on `main`. Read-only token.
- `deploy.yml`: runs via `workflow_run` after Tests succeeds for a push to `main`, so a red `main` doesn't deploy. Builds the tested commit's image (SHA tag + `prod-latest`) and runs `cdk deploy` for prod in the `production` environment (main only, no reviewer), serialised by the `deploy-prod` concurrency group.
- `pr-environment.yml`: for same-repo PRs, except runs started by Dependabot (which get no repo secrets, so the role ARN would be empty), builds the image (`pr-<N>-<sha>`), deploys `TrackTimingStack-pr-<N>` with a public Function URL, comments the URL on the PR, and destroys the stack on close. Runs serialised per PR.
- `cleanup-pr-stacks.yml`: weekly sweep that deletes `TrackTimingStack-pr-*` stacks whose PR is closed.
- Two OIDC roles (`cdk/base_stack.py`): the prod role trusts only the `production` environment and deploys through the CDK bootstrap roles; the PR role trusts PR and main-branch runs, and PR stacks deploy with its own credentials (`CliCredentialsStackSynthesizer`), so its policy limits PR workflows to `pr-*` stacks and resources. Details in `plans/hosting-plan.md`.

**Configuration:** `app/config.py` exposes a module-level `settings` singleton and `get_settings()` for `Depends()`.

**HTTP client:** one `httpx.AsyncClient` (`max_connections=50`, 15 s timeout) lives on `app.state.http_client`; routes get it via `Depends(get_http_client)`. Under uvicorn the FastAPI `lifespan` creates and closes it and runs `init_db()`/`init_palmares_db()`. On Lambda, `handler` wraps `Mangum(app, lifespan="off")` (Mangum's `auto` would run the lifespan per invocation): it initialises the databases on the first invocation, `get_http_client` creates the client on first use, and Mangum's single per-container event loop lets later invocations reuse it and its connection pool.

**Blocking I/O:** SQLite and boto3 calls are synchronous, so `async def` routes run them with `asyncio.to_thread` (learned-duration reads and live-duration writes, palmares reads and writes, `/learned`). Prediction itself (`predict_schedule`) makes no database calls.

**Request flow (`/schedule/{event_id}`):**
1. `fetcher.fetch_initial_layout` POSTs to the Jaxon endpoint (refresh uses `fetch_refresh`).
2. `parser.parse_schedule` turns the HTML into `Session`/`Event` models.
3. `main.py` concurrently fetches start lists, result pages and live-heat pages, filling the predictor caches. Start lists and result pages are fetched once per distinct URL (the rides of a sprint round share both) and the result recorded for every event using it; `parser.parse_start_list` reads heat count, riders, categories and distance from one parse. When a racer is set and some race has no start-list riders, a combined-age bunch final has no cached start-list categories, or a pending sprint/pursuit qualifying round or time trial has no start-list heat count (`rider_list.needs_heat_estimate`), the same `gather` fetches the Rider List (`_fetch_rider_list_if_needed`, cached by URL).
4. `predictor.predict_schedule` builds a `SchedulePrediction`.
5. Jinja2 renders `schedule.html`; HTMX polls `/schedule/{id}/refresh`, which returns `_schedule_body.html`.

**tracktiming.live API** (unversioned and undocumented, so parse defensively):
- `POST eventpage.php?EventId={id}` with form body `jxnfun=getInitialPageLayout&jxnr=1` (initial) or `jxnfun=refreshPage&...` (refresh); the response is JSON with a `jxnobj` array.
- The schedule HTML is either a top-level `id="scheduleview"` object or nested in `id="dynarea"` (the live API). The parser handles both.
- Status comes from the event's row buttons (no `disabled` class): `btn-success` means COMPLETED (href is the result page), `btn-primary` means UPCOMING (href is the start list), `btn-info` is the audit page, and `btn-danger` is the live timing page (`liveresults.php?EventId={id}`). Anything else is NOT_READY. The rides of a best-of-3 sprint round share one result page, so upstream gives Ride 2 and Ride 3 an enabled Results button as soon as Ride 1 is posted; `predictor.apply_sprint_ride_status` (called by both schedule routes after the fetches) turns a ride that the shared page shows isn't done back into UPCOMING (NOT_READY without a start list). As a safety net for a page it can't read, a ride counts as done once a later event in the session is done (a completed non-special event that isn't a ride, or a confirmed ride; `predictor.pending_sprint_rides`), which also ends its page's refetching.
- A GET of the live timing page returns only an empty `<div id="dynarea">`; `fetcher.fetch_live_results` POSTs `jxnfun=updateDynArea&jxnr=1&jxnargs[]=N1` as the browser does, and `parser.parse_live_results_html` takes the `dynarea` jxnobj. `parser.parse_live_heat` reads "Riders On Track for Heat N of M" (pursuits and team events) or counts `Heat N` sections with a time or a Winner (a sprint bye) (sprints, keirin) as finished heats: 0 when heats are shown but none is finished, None when the page shows no heats. A best-of-3 sprint round's live page shows Ride 1, Ride 2 and Decider columns, so for a ride `parser.parse_live_sprint_heat` counts only that ride's times (byes count as finished in Rides 1 and 2). `main._fetch_live_heats` records a live heat only when `parser.live_results_show_event` finds the event's name in the page, so a page still showing the previous event is ignored.
- The session summary looks like `"Schedule - Friday - 08:15"`; times are venue-local and naive.
- Start lists (`parser._start_list_riders`): a `Heat N` row, or a medal final's `Final 3-4`/`Final 1-2` or `For Bronze`/`For Gold` `<h4>` (numbered in page order, so bronze is heat 1 and gold heat 2, matching `parse_heat_count`), starts a heat. Pursuit, time trial and team start lists have an `<h4>` "First rider (or team) listed starts on the home straight"; with it, a heat's first row (rider or team; a team's riders share it) gets `RiderEntry.straight` `"home"` and its second `"back"`, and later rows and pages without it get None.
- Both responses carry a top-level `documents` jxnobj (Event Documents table). `parser.parse_rider_list_url` takes the `href` of the row whose `<h4>` is `Rider List` (e.g. `results/E26037/X-RIDERLIST-0-0-S.htm`). Rider List rows are `tbody tr` with cells bib, name, category, team, flag image, nation, space-separated event codes; `parser.parse_rider_list` strips the inline base64 flag images first.

**Routes:**

| Path | Description |
|---|---|
| `/` | Landing page with EventId form (works without JS) |
| `/schedule` | No-JS form target; `?event_id=X` → 303 to `/schedule/X` |
| `/schedule/{event_id}` | Schedule view; optional `?r=` (URL-safe Base64 racer name) |
| `/schedule/{event_id}/refresh` | HTMX partial; optional `?r=` |
| `/settings/racer-name` | Set/clear `racer_name` cookie; `?event_id=&name=` |
| `/settings/use-learned` | Toggle `use_learned` cookie; `?event_id=&use_learned=on\|off` |
| `/palmares` | Palmares page; `name=` sets the cookie and 303s to `?r=`; otherwise the racer comes from `r=`, then the cookie |
| `/palmares/export` | CSV of one rider's (or team's) audit data; `audit_url` must start with `results/` after percent-decoding and `normpath`; pages over 2M chars give 502; `Content-Disposition` carries an ASCII `filename` plus RFC 5987 `filename*` |
| `/palmares/rename` | Rename a competition; requires `racer_name` cookie |
| `/palmares/remove` | Delete a competition's entries; requires `racer_name` cookie (403 otherwise) |
| `/defaults` | Built-in default and per-heat durations, the rules that replace them (distance, changeover, deciders, ceremonies), the bunch-race pace table, the masters per-heat overrides and the warm-up lead |
| `/learned` | Learned duration averages |
| `/health` | Always 200; per-component `healthy`/`degraded` |

**Cookies:** `racer_name` (`b64.` + unpadded URL-safe Base64 of the name, since Starlette encodes headers as Latin-1; legacy raw-name values are still read and rewritten on the next schedule view; 1 year, HttpOnly, Secure, Lax); `use_learned` (`"true"` when on; off by default); `theme` (`light`/`dark`, set client-side, 1 year; an override of the system `prefers-color-scheme`: toggling to the theme the system prefers deletes it, and without it the page follows system changes live).

**In-memory caches** (`predictor.py`, keyed by `(competition_id, session_id, position)`, unbounded, per Lambda container; upstream can delete or add rows mid-session, such as a Ride 3 nobody needs once Ride 2 ends, so both schedule routes call `predictor.reconcile_positions` right after parsing: it remembers each session's (position, name) layout in `_session_layouts` and, when it changes, moves the position-keyed entries (`_POSITION_CACHES`) to their events' new positions, matching by name and occurrence and dropping entries for events that are gone): `_status_cache` (status transitions for wall-clock learning), `_finish_times` (raw result-page Finish Times; the changeover is added at prediction time), `_heat_counts`, `_live_heats` (finished heats from the live timing page, refetched on every view), `_generated_times` (the earliest Generated timestamp seen per event: `record_generated_time` never replaces an earlier one, and `main._fetch_result_pages` also reads the audit page's for events that have one, because upstream regenerates either page after corrections and the timestamp moves past the event's end; at 26037, 26 of 72 timed events had the two pages more than 2 min apart), `_start_list_riders` (an empty list counts as no start list: `has_start_list_riders` is false and it is refetched, except for COMPLETED events, whose start list is fetched once; an empty parse never replaces cached riders), `_start_list_categories` (non-empty Category column values from `parser.parse_start_list_categories`; only combined-age start lists have the column). `_race_distances` (km from a start list's title, `parser.parse_race_distance_km`). `_sprint_deciders` is keyed by `(competition_id, round name)` (the event name without ` Ride N`, `disciplines.split_ride`) and holds how many pairs need a decider, recorded from a best-of-3 round's shared result page once Ride 2 is posted (`parser.parse_sprint_deciders`). `_sprint_decider_ranges`, keyed the same way, holds (pairs known to need a decider, pairs yet to ride Ride 2) from `parser.parse_sprint_decider_range`, read from the round's result page and, during Ride 2, its live timing page; a reading with more pairs left than the stored one is ignored, and one with none left also sets `_sprint_deciders`. `_sprint_rides_done`, keyed the same way, holds how many rides every pair has finished (`parser.parse_sprint_rides_done`: Ride 1 and 2 when every pair has a time, Ride 3 when no pair is still level at one win each after Ride 2; a bye, one rider with a 0.000 time, is left out of both). `main._fetch_result_pages` refetches a round's shared page on every view until each of its rides is known to be done, and records the page's Generated timestamp for a ride only once that ride is done. `_rider_lists` is keyed by Rider List URL instead and holds non-empty lists forever (the file is immutable for a competition); a failed fetch or 0-row parse goes in `_rider_list_retry_at` and isn't retried for `RIDER_LIST_RETRY_SECONDS` (10 min).

**Duration source priority** (`predictor.predict_session`):
1. Observed: result-page Finish Time + the competition's bunch changeover (bunch races).
2. Generated: difference between an event's result-page Generated timestamp and the previous event's, kept if within 0.5×–2.0× of that event's expected duration. Generated marks an event's end, so the gap belongs to the later event. `predictor.generated_gap_duration` does this for both the app (expected from `_base_estimate`, band-aware) and `tools.extract_competition` (expected = the static default). Both use the earlier of the result and audit pages' Generated timestamps. The extractor skips the gap for events that share a result page (sprint rides), which post-event all carry the page's final timestamp.
3. Heat count: `heat_count × per_heat_duration + changeover`. Per-heat comes from `disciplines.get_per_heat_duration(discipline, band)`: a masters event with an age band in the name (`rider_list.event_band`) uses `MASTERS_PER_HEAT_DURATIONS` by the band's youngest age (2 km pursuit under 70 4.5, 500 m TT 70+ 2.75, team sprint 3.5), anything else `PER_HEAT_DURATIONS`. Without a start-list count, `predictor.infer_heats` supplies one: a masters sprint or pursuit qualifying round or time trial from the Rider List (`rider_list.estimate_heats`: entrants in the band with the code, 1 sprinter or 2 pursuiters/time triallists per heat); a pursuit, team pursuit or team sprint `Final` with a same-named `Qualifying` round in the competition 2 heats (bronze, gold; `disciplines.qualifying_name`); a keirin placement final (`1-6`, `7-12`) 1 heat and a keirin `1/2 Final` 2 (`disciplines.keirin_round_heats`); a team pursuit or team sprint qualifying round from committed field sizes (see **Supplements**), which also give 1 heat to a team final with two or fewer teams. The Rider List wins over field sizes, which win over the round name. A points or scratch race with a start-list distance, else a committed scheduled distance (see **Supplements**), uses `km / pace × 60 + changeover` instead, with pace from `disciplines.bunch_race_kmh(band)`: for a banded masters event `MASTERS_BUNCH_RACE_KMH` by gender and youngest age (men under 70 48, 70-74 41.5, 75+ 36; women under 50 43.5, 50+ 41 km/h), else `BUNCH_RACE_KMH` (46). Without a start list, a sprint 1/2 Final or Final counts 2 pairs and a 1/4 Final 4 (`disciplines.sprint_round_pairs`; placement finals such as `5-8 Final` keep the default). A sprint `Ride 3` (the decider) uses `deciders × SPRINT_DECIDER_MINUTES` (4.25) once Ride 2 is posted; while Ride 2 is ridden, (pairs already tied + pairs yet to ride Ride 2 × `SPRINT_DECIDER_RATE` (0.12)) × `SPRINT_DECIDER_MINUTES`, from `_sprint_decider_ranges`; else pairs (heat count, round name, or default ÷ per-heat) × `SPRINT_DECIDER_RATE` × `SPRINT_DECIDER_MINUTES`; `predictor._base_estimate` computes this and also supplies the expected duration for step 2's bounds.
4. Fallback: if the `use_learned` cookie is on, the discipline-level learned average (`get_learned_duration`, ≥ `MIN_LEARNED_SAMPLES`, passed in as `predict_schedule(..., learned=)`); otherwise `DEFAULT_DURATIONS`.

**Bunch changeover** (`predictor.bunch_changeover`, computed once per `predict_schedule`): for `FINISH_TIME_DISCIPLINES` (scratch, points, elimination, tempo, madison) the live predictor replaces the static `CHANGEOVER_MINUTES` (2.0) with the median of (Generated gap − Finish Time) over this competition's bunch races that follow another bunch race with its own result page, overheads in [0, `MAX_CHANGEOVER_MINUTES`] (20). It needs `MIN_CHANGEOVER_SAMPLES` (3); until then `LIVE_BUNCH_CHANGEOVER_MINUTES` (3.0). It applies to step 1, the distance estimate, and (shifted by calibrated − static) to defaults and learned averages. Keirin keeps its static 2.0. The learning database still records Finish Time + the static 2.0, so learned averages stay comparable across competitions.

A medal ceremony with a podium forecast skips all four and uses `CEREMONY_BASE_MINUTES + podiums × CEREMONY_PER_PODIUM_MINUTES` (13 + 3.3, `disciplines.py`). Its Generated timestamp marks its start, so neither the gap before a ceremony nor the one after it (which includes the ceremony) is used as an event's duration.

`predictor._base_estimate` returns an `_Estimate` (minutes, heats, `heat_basis`, km, km/h, km basis, per-heat); `Prediction` carries `heat_count`, `heat_basis` (`start_list`, `rider_list`, `entry_list`, `round`, `decider`, `decider_pairs`), `per_heat_minutes`, `race_distance_km`, `race_kmh` and `distance_basis` (`start_list`, `schedule`). The UI labels these as **obs.** (1–2), **N heats** (3, start list), **N km** (3, start-list distance), **~N km est.** (3, scheduled distance), **N deciders** (3, Ride 3 after Ride 2), **K–N deciders est.** (3, Ride 3 before Ride 2 is posted; K is the pairs already tied during Ride 2, `Prediction.deciders_known`, else 0, and N is K plus the pairs that may still need one; these don't drive the active-heat counter), **~N heats est.** (3, Rider List, field sizes or round name), **N podiums** (ceremonies) and **est.** (4). The active event shows **heat N/M** instead, with **on track** when the heat comes from the live timing page (`Prediction.active_heat_live`) rather than elapsed time. Each label has a `title` tooltip naming the basis; tooltips use the prediction's `per_heat_minutes` and `race_kmh`, and the template gets `changeover_minutes` and `decider_rate` as Jinja globals set in `main.py`. The same band-aware per-heat value sets the step-2 bounds, a Ride 3's pairs from the default, the active-heat counter and a racer's heat start. Learned averages stay per discipline.

**Live delay**: applied only while a session has both completed events and pending non-special events (a NOT_READY End of Session doesn't keep a finished session live). The same condition gates the active-event flag (the first event not COMPLETED). Delays are clamped to [−30, +120] min and are 0 outside the session window (`_in_session_window`: from the scheduled start to `total_est + 60 min`), so post-event views show scheduled times. The active event's start comes from `predictor._active_event_start`: the last completed event's result-page Generated timestamp (an event's end), plus the estimated durations of completed events after it without one (breaks) and of a ceremony whose Generated marks its start, capped at now. `_anchored_delays` then gives the active event its actual start and shifts later events to its estimated end, or to now + `ACTIVE_MIN_REMAINING_MINUTES` (2) once it overruns, so later predictions don't drift while a long event runs. When the live timing page gives the active event's finished heats, `_live_heat_remaining` sets later events to now + the unfinished heats (the running one counted as half done) × per-heat + changeover, at least `ACTIVE_MIN_REMAINING_MINUTES`; it applies with or without a known start, and not to Ride 3 deciders before Ride 2. The active-heat counter uses the live heat too, and its time-based fallback counts from the active event's start. Without a usable timestamp (none cached, another day, or more than 5 min ahead of now) `_compute_delay` assumes the active event starts now; the counter then counts from the scheduled start plus the estimated durations before it. "Now" comes from `clock.venue_now()`, naive to match the schedule. Upstream exposes no timezone, so the venue's UTC offset is inferred from the newest Generated timestamp in an in-progress session (`predictor.latest_live_generated_time`): Generated ≤ venue-local now, so (Generated − UTC now − 2 min skew allowance) rounded up to the whole hour is the offset while that result is under ~58 min old. Results older than that (a long break) give an offset an hour low, and half-hour zones aren't supported. With no live session, or an offset outside UTC−12..+14, it falls back to `VENUE_TZ`. Both schedule routes use it, and the "Last updated" label shows it (the refresh partial carries it in `#schedule-generated-at`).

**Racer display:** the racer's matched row shows **Heat N · home straight** (or **Racing · back straight** for a one-heat event) when `RiderMatch.straight` is set. The next-race line adds the straight and, before the race is active, "(warm up from HH:MM)": `NextRace.warm_up_from` is its predicted start (the heat's, or the event's for a Rider List match) minus `WARM_UP_LEAD_MINUTES` (45, `disciplines.py`, presentation only; `/defaults` states it).

**Learning** (`database.py`; DynamoDB when `DYNAMODB_TABLE` is set, otherwise SQLite):
- *Live app writes* go through `record_live_duration(..., source)`. `predictor.record_observed_duration` and `update_status_cache` don't write; they return `LiveDuration` records that `main.py` persists with `save_live_durations` in a worker thread. Sources: `"observed"` from result-page Finish Times, `"wall_clock"` from the UPCOMING→COMPLETED fallback (capped at 3× static default). They're idempotent per `(competition, session, position)` and keep any existing record, except that an observed value replaces a wall-clock one. Loader records (no `source`) are never replaced. DynamoDB reuses the structured `OBS#` path with a `source` attribute; SQLite stores it in a `source` column.
- *Loader writes* go through `record_duration_structured()` (returns `RecordOutcome`: created/updated/unchanged/error), with classification, gender and per-heat duration. `tools/` use the unbanded per-heat constants (`get_per_heat_duration(discipline, None)`) for heat-count durations and the loader's bounds check: heat-count records echo the constant into the learning database, whose averages are per discipline, so banded values would make them depend on a competition's age mix, and the tighter unbanded bounds keep out break-inflated slots. They're idempotent: SQLite uses `INSERT OR REPLACE`; DynamoDB uses an `OBS#<comp>#<sess>#<pos>` item as a commit marker written after the `AGGREGATE#...` updates, with delta correction on re-load.
- *Reads:* the app uses only `get_learned_duration(discipline)` (overrides first, then the average), via `predictor.load_learned_durations`, which reads each distinct discipline in the schedule once per request (only when `use_learned` is on). `get_learned_duration_cascading(discipline, classification, gender)` exists and is tested, but nothing in the app calls it.
- The DynamoDB key layout (`AGGREGATE#` levels, `OVERRIDE#`, `OBS#`) is documented in the comment block near the top of the DynamoDB section in `database.py` (~line 150). `aws_errors.py` holds the optional-botocore import shared by `database.py` and `palmares.py` (`BotoError`, `ClientError`, `raise_if_auth_error`). SQLite tables are `event_durations` (with `_migrate_schema` adding columns to old DBs) and `discipline_overrides`.

**Disciplines:** two classifiers exist.
- `disciplines.detect_discipline` is an ordered keyword list (`DISCIPLINE_KEYWORDS`, more specific phrases first). The live app uses it; `disciplines.py` also holds `DEFAULT_DURATIONS`, `PER_HEAT_DURATIONS`, the masters per-heat and pace tables (`AgeBracket`s by youngest age) and changeovers.
- `categorizer.categorize_event` is a bilingual strip-and-match parser. It extracts special event → omnium part → ride number → round → classification → gender → discipline, then maps pursuits to `pursuit_4k`/`3k`/`2k`, and returns `(EventCategory, unresolved_text)`. Only `tools/` use it.
- Individual pursuit distance: both classifiers' name-based guess is overridden by `disciplines.pursuit_discipline_from_urls` whenever the event has any URL, since upstream page names encode the distance (`W4044-IP-3000-Q-0-R.htm`). `parser.parse_schedule` and `tools.extract_competition` apply it. Names alone guess wrong for masters age groups, Junior Women, U17 Men and French names, so before an event has a URL `parser.parse_schedule` takes a masters age band's distance from `disciplines.pursuit_discipline_from_band` (26037: 3 km under 50, 2 km from 50, `MASTERS_PURSUIT_2K_FROM_AGE`).

**Rider List matching** (`rider_list.py`, pure functions; wired in by `predictor.predict_schedule(..., rider_list=)`):
- For a non-special event with no start-list riders, the racer's Rider List row (`find_rider`, same token matching as start lists) is matched by age band, gender and event code. A start list with riders always wins, even if the racer isn't on it.
- Scope is EventId 26037's formats only: categories `[MW]NNNN` (lo–hi) or `[MW]NN` (lo and over), event names with `NN-NN`/`NN+` then `Men`/`Women`, and codes S TT IP TP TS SCR PTS (`CODE_DISCIPLINES`). Other categories (e.g. 26008's `ME`, `MU17`) and codes produce no match. The event band must contain the rider's band.
- `match_events` decides certainty per code over the events this rider matches: a lone event is **Entered**; otherwise Qualifying/Qualifier N rounds are Entered and the rest **If advancing** (`RiderMatch.tentative`). Per-rider grouping keeps finals tentative when overlapping open bands (55+ and 65+) share a qualifying round. Matches have `source="rider_list"` and no heat; next race uses the event's predicted start. When the rider matches several numbered qualifiers for a code (e.g. Scratch Race Qualifier 1 and 2), they ride only one, so those matches set `parallel_qualifier` and next race reads "… (or a later qualifier), be ready by HH:MM".
- `estimate_heats` sizes pending sprint and pursuit qualifying rounds and time trials (`needs_heat_estimate`) from entrants in the band with the code, for any viewer, not just a matched racer. Team events and finals aren't sized.
- The template replaces the start-list warnings with an info line naming the category and codes (`SchedulePrediction.rider_list_entry`, set only when a Rider List match exists). Rider List matches never create palmares entries.

**Supplements** (`supplements.py`; `predict_schedule` calls `load_supplement(competition_id)` and passes `fields` to `predictor.infer_heats(..., fields=)` and `scheduled_distances(distances, sessions)` to `predict_session(..., scheduled_km=)`):
- Pre-event data published off tracktiming.live is captured once by a per-source importer in `tools/` (`import_fullgas_26037`: team entries, the organiser's schedule, and tech-guide distances for the two races the schedule leaves blank) into `app/data/supplements/<competition_id>.json` (`CompetitionSupplement`: sources, capture date, `fields` and `distances`). The file is committed and ships in the image via `COPY app/`; nothing is fetched at runtime, and `load_supplement` caches it per container (None when there's no file).
- `fields` (`FieldSize`: discipline key, gender `M`/`W`, `lo`/`hi` age band, entries): `heats_from_fields` sizes banded team pursuit and team sprint events. A qualifying round gets one heat per team, summing every field whose band lies inside the event's (`55+ Women` = 55-64 + 65+); a final with two or fewer teams gets 1 heat. Basis `entry_list`.
- `distances` (`RaceDistance`: discipline, gender, band, phase `qualifying`/`final`, km): `scheduled_distances` gives a points or scratch race the km whose discipline, gender and band equal the event's and whose phase matches (`Qualifier N` → qualifying, `Final` → final). A start-list distance wins. Basis `schedule`. At 26037 all 26 races with both matched exactly.

**Ceremony podiums** (`ceremonies.py`, pure functions; `predictor.predict_schedule` calls `forecast_podiums` with the cached start-list categories and the Rider List):
- A ceremony awards the finals since the previous ceremony, across sessions. Rounds (`1/N Final`) and placement finals (`5-8 Final`, `7-12 Final`; `disciplines.is_placement_final`) don't count; a sprint Final counts after its last scheduled ride; team events are one podium.
- Combined-age points and scratch finals (`35-49 Women`, `50+ Women`) are one podium per category: start-list Category column, else Rider List categories with that event code inside the band, else five-year bands in the name (1 for an open band).
- Scope is 26037's naming: a ceremony whose window has a final without an `event_band` gets no forecast and keeps `DEFAULT_DURATIONS["ceremony"]`. Rationale and data in `docs/medal-ceremony-durations.md`.

**Palmares** (`palmares.py`; DynamoDB when `PALMARES_TABLE` is set, otherwise SQLite `palmares_entries`):
- Collected automatically on schedule views when a racer is identified and matched on a start list to a timed event that has an audit URL.
- Timed disciplines are listed in `_TIMED_DISCIPLINES` in `main.py`: pursuits, `team_pursuit`, `team_sprint` and time trials.
- Team start lists pack the team name and riders into `<h4>` separated by `<br/>`; `parser._extract_names_from_h4` splits them, and `team_name` is stored because audit pages use team names.
- The competition date is the earliest result-page Generated timestamp.
- Public API: `save_palmares_entries`, `get_palmares`, `count_competition_palmares`, `update_competition_palmares`, `delete_competition_palmares`.
- Entries are keyed by the raw racer name string. The DynamoDB keys are `RACER#{name}` and `COMP#{id}#S#{sid}#E#{pos}`.
- `audit_parser.py` parses `-AUDIT-R.htm` pages (riders from `<p>`, heats from `<h3>`), filters with `normalize_rider_name`, and `format_csv` emits Heat, Dist, Time, Rank, Lap, Lap_Rank, Sect, Sect_Rank.

**Frontend:** Jinja2 templates in `app/templates/` (`base.html`, `index.html`, `schedule.html` + `_schedule_body.html`, `palmares.html`, `defaults.html`, `learned.html`). No third-party assets load at runtime: `base.html` loads `static/app.css` (Tailwind 3 + DaisyUI 4, light and dark themes, compiled from the classes in `app/templates/` and `app/**/*.py`), then `static/style.css`, then `static/htmx.min.js` (1.9.12), each via the `static_url` Jinja global, which appends `?v=<12-char sha256 of the file>`. `VersionedStaticFiles` sends `Cache-Control: public, max-age=31536000, immutable` for `?v=` requests and `no-cache` otherwise; prod CloudFront caches `/static/*` keyed on `v` (default behavior stays uncached). The first and last are built by `frontend/` (`cd frontend && npm ci && npm run build`; versions pinned in `frontend/package-lock.json`) and committed, so rebuild after adding a class; a class built by string concatenation won't be found. `static/style.css` holds only app-specific overrides; the schedule table becomes cards below 768px (`.schedule-table`). Secondary text uses solid colours, not opacity, so it keeps WCAG AA contrast (4.5:1) in both themes however it's nested: `.text-muted` (also table headers and completed and not-ready rows' text) and `.ink-info`/`-success`/`-warning`/`-error` for status-coloured text (darkened in the light theme, where DaisyUI's hues are too pale on white). Use these instead of `opacity-*` or `text-*/NN` on text. `static/app.js` (loaded from `<head>` without `defer`, so it sets `data-theme` before first paint) holds all page behaviour: templates have no inline scripts, `style` attributes or `on*` handlers, and wire up via ids and `data-*` attributes (`data-autosubmit`, `data-edit-toggle`, `data-remove-modal`). The `security_headers` middleware in `main.py` sends a CSP limited to `'self'`, HSTS, `nosniff`, a referrer policy and `X-Frame-Options: DENY` on every response, and `base.html` disables htmx's injected indicator styles (`style.css` has them), which the CSP would block. Tailwind doesn't scan `app.js`, so a class it adds must also appear in a template. Links to tracktiming.live use the `base_url` Jinja global and open with `rel="noopener noreferrer"`.

## Key Patterns

- `tests/conftest.py` points SQLite at a session-scoped temp file, blanks `DYNAMODB_TABLE`/`PALMARES_TABLE`, and empties the learned-duration tables before each test. DynamoDB tests use `moto`.
- Parsers are tested against captured upstream HTML/JSON in `tests/fixtures/` (including `sample-event-output.json`). New parsing of upstream formats needs a captured fixture (constitution, Principle II).
- Special events (`SPECIAL_EVENT_NAMES` in `disciplines.py`: break, pause, end of session, medal ceremonies, medal ceremony) set `is_special`. They're excluded from `is_complete` checks, and their COMPLETED status is deferred until the next event starts. `end_of_session` contributes 0 minutes.

## Repository map

- `pyproject.toml`: project metadata, dependency ranges and ruff/mypy/pytest/coverage config.
- `app/`: application. `app/data/supplements/`: committed per-competition supplements. `tools/`: CLI importers, `rebuild_aggregates` (DynamoDB aggregate repair) and one-off supplement importers. `tests/`: pytest suite plus `fixtures/`. `cdk/`: infrastructure. `static/`: built CSS, vendored htmx, overrides and `app.js`. `frontend/`: npm build for the `static/` assets.
- `specs/NNN-name/`: speckit feature artifacts (spec, plan, tasks, research, contracts). 001–005 are complete and historical; read them for rationale, not current behaviour.
- `.specify/`: speckit config. Only `memory/constitution.md` (project principles that govern design trade-offs) and `templates/overrides/` (project-specific plan and task rules) are committed. The rest of `.specify/` and the `/speckit.*` commands in `.claude/commands/` are installed locally and gitignored. The project uses Spec Kit **v0.2.1**; to install it, run `uvx --from git+https://github.com/github/spec-kit.git@v0.2.1 specify init --here --ai claude --script sh --force`. This keeps the existing constitution and overrides; check `git status` afterwards.
- `plans/`: pre-speckit design notes. `hosting-plan.md` is the current infrastructure reference; `data-pipeline*.md` and `dynamo-import-reload.md` are historical.
- `docs/`: `duration-data-import.md` (extract/load tooling reference), per-discipline duration rationale (`sprint-`, `mass-start-race-`, `timed-event-`, `medal-ceremony-durations.md`), and historical HTML UI prototypes (`daisyui-*`, `*-mockup.html`).

## Conventions

- Feature branches: speckit features use `NNN-short-name`; other work uses a descriptive branch name.
- `/speckit.plan` runs `.specify/scripts/bash/update-agent-context.sh`, which appends "Active Technologies" and "Recent Changes" sections to this file. Delete them afterwards; this file stays current-state only.
