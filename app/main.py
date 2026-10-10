import asyncio
import base64
import binascii
import functools
import hashlib
import logging
import posixpath
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mangum import Mangum
from pythonjsonlogger.json import JsonFormatter
from starlette.types import Scope

from app.audit_parser import filter_rider_data, format_csv, parse_audit_riders
from app.ceremonies import needs_categories
from app.clock import venue_now
from app.config import Settings, get_settings
from app.database import check_health, get_all_learned_durations, init_db
from app.disciplines import (
    BUNCH_RACE_KMH,
    CEREMONY_BASE_MINUTES,
    CEREMONY_PER_PODIUM_MINUTES,
    CHANGEOVER_MINUTES,
    DEFAULT_DURATIONS,
    LIVE_BUNCH_CHANGEOVER_MINUTES,
    MASTERS_BUNCH_RACE_KMH,
    MASTERS_PER_HEAT_DURATIONS,
    MIN_CHANGEOVER_SAMPLES,
    PER_HEAT_DURATIONS,
    SPRINT_DECIDER_MINUTES,
    SPRINT_DECIDER_RATE,
    AgeBracket,
    get_changeover,
    split_ride,
)
from app.fetcher import fetch_initial_layout, fetch_live_results, fetch_page_html, fetch_refresh
from app.models import EventStatus, PalmaresEntry, RiderListEntry, SchedulePrediction, Session
from app.palmares import (
    check_palmares_health,
    count_competition_palmares,
    delete_competition_palmares,
    get_competition_name,
    get_palmares,
    init_palmares_db,
    save_palmares_entries,
    update_competition_palmares,
)
from app.parser import (
    live_results_show_event,
    parse_finish_time,
    parse_generated_time,
    parse_live_heat,
    parse_live_results_html,
    parse_live_sprint_heat,
    parse_rider_list,
    parse_rider_list_url,
    parse_schedule,
    parse_sprint_decider_range,
    parse_sprint_rides_done,
    parse_start_list,
)
from app.predictor import (
    LiveDuration,
    apply_sprint_ride_status,
    get_generated_time,
    get_heat_count,
    get_rider_list,
    has_start_list_categories,
    has_start_list_riders,
    is_start_list_cached,
    latest_live_generated_time,
    load_learned_durations,
    pending_sprint_rides,
    predict_schedule,
    reconcile_positions,
    record_generated_time,
    record_heat_count,
    record_live_heat,
    record_observed_duration,
    record_race_distance,
    record_rider_list,
    record_rider_list_failure,
    record_sprint_decider_range,
    record_sprint_rides_done,
    record_start_list_categories,
    record_start_list_riders,
    rider_list_retry_pending,
    save_live_durations,
    update_status_cache,
)
from app.rider_list import needs_heat_estimate

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # Quiet noisy uvicorn access logs in production; keep warnings
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


setup_logging()


def _new_http_client() -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        base_url=settings.tracktiming_base_url,
        timeout=15.0,
        limits=httpx.Limits(max_connections=50),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown under uvicorn. The Lambda handler runs with lifespan off."""
    init_db()
    init_palmares_db()
    app.state.http_client = _new_http_client()
    yield
    await app.state.http_client.aclose()


STATIC_DIR = Path("static")


class VersionedStaticFiles(StaticFiles):
    """Static files that browsers and CloudFront may cache for a year when requested with
    the ?v= content hash from static_url; any other request revalidates (ETag)."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            versioned = "v" in parse_qs(scope.get("query_string", b"").decode("latin-1"))
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "no-cache"
        return response


@functools.cache
def _static_digest(path: str, mtime_ns: int) -> str:
    return hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:12]


def static_url(path: str) -> str:
    """URL for a file in static/, with a content hash so a changed file gets a new URL."""
    return f"/static/{path}?v={_static_digest(path, (STATIC_DIR / path).stat().st_mtime_ns)}"


# Pages load only same-origin assets and have no inline scripts, styles or on* handlers
# (page behaviour is in static/app.js), so scripts and styles can be limited to 'self'.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}

app = FastAPI(title="Track Timing Predictor", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next: Any) -> Response:
    """Add SECURITY_HEADERS to every response, so PR environments (no CloudFront) get them too."""
    response: Response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


app.mount("/static", VersionedStaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_url"] = static_url
templates.env.globals["base_url"] = get_settings().tracktiming_base_url
templates.env.globals["changeover_minutes"] = get_changeover
templates.env.globals["decider_rate"] = SPRINT_DECIDER_RATE


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Return the shared client, creating it on first use when no lifespan ran (Lambda).

    Mangum keeps one event loop per container, so the client and its connection pool
    are reused by every invocation the container serves.
    """
    client: httpx.AsyncClient | None = getattr(request.app.state, "http_client", None)
    if client is None or client.is_closed:
        client = request.app.state.http_client = _new_http_client()
    return client


async def _fetch_live_heats(
    client: httpx.AsyncClient,
    competition_id: int,
    sessions: list[Session],
) -> None:
    """
    Fetch the live results page for any event that has a live_url and parse
    the current heat number. Called on every refresh since the page changes
    as each heat completes.
    """
    to_fetch = [
        (competition_id, s.session_id, e.position, e.name, e.live_url) for s in sessions for e in s.events if e.live_url
    ]
    if not to_fetch:
        return

    sem = asyncio.Semaphore(5)

    async def fetch_one(ev_id: int, sess_id: int, pos: int, name: str, url: str) -> None:
        async with sem:
            try:
                live = await fetch_live_results(client, url)
            except Exception:
                logger.warning(
                    "Failed to fetch live heat for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True
                )
                return
            try:
                html = parse_live_results_html(live)
                heat = None
                if live_results_show_event(html, name):
                    # A best-of-3 round's page shows every ride's column; count only this ride's.
                    ride = split_ride(name)
                    heat = parse_live_sprint_heat(html, ride[1]) if ride else None
                    if heat is None:
                        heat = parse_live_heat(html)
                    # Ride 2's page shows which pairs are already tied (or not) for the decider.
                    if ride and ride[1] == 2 and (decider_range := parse_sprint_decider_range(html)) is not None:
                        record_sprint_decider_range(ev_id, ride[0], *decider_range)
                if heat is not None:
                    record_live_heat(ev_id, sess_id, pos, heat)
            except Exception:
                logger.warning(
                    "Failed to parse live heat for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True
                )

    await asyncio.gather(*[fetch_one(*args) for args in to_fetch])


async def _fetch_start_lists(
    client: httpx.AsyncClient,
    competition_id: int,
    sessions: list[Session],
) -> None:
    """
    Concurrently fetch start list pages for all events that have a start_list_url
    and whose heat count or rider list has not yet been cached.
    Records heat counts and rider entries in-memory. A completed event's start list
    is fetched at most once, since it can't change. Events that share a start list
    (the rides of a sprint round) share one fetch.
    """
    to_fetch: dict[str, list[tuple[int, int]]] = {}
    for s in sessions:
        for e in s.events:
            if (
                e.start_list_url
                and (
                    get_heat_count(competition_id, s.session_id, e.position) is None
                    or not has_start_list_riders(competition_id, s.session_id, e.position)
                )
                and not (
                    e.status == EventStatus.COMPLETED and is_start_list_cached(competition_id, s.session_id, e.position)
                )
            ):
                to_fetch.setdefault(e.start_list_url, []).append((s.session_id, e.position))
    if not to_fetch:
        return

    sem = asyncio.Semaphore(10)

    async def fetch_one(url: str, slots: list[tuple[int, int]]) -> None:
        async with sem:
            try:
                html = await fetch_page_html(client, url)
            except Exception:
                logger.warning("Failed to fetch start list %s for event %d", url, competition_id, exc_info=True)
                return
            try:
                start_list = parse_start_list(html)
                for sess_id, pos in slots:
                    if start_list.heat_count:
                        record_heat_count(competition_id, sess_id, pos, start_list.heat_count)
                    record_start_list_riders(competition_id, sess_id, pos, start_list.riders)
                    record_start_list_categories(competition_id, sess_id, pos, start_list.categories)
                    if start_list.race_distance_km is not None:
                        record_race_distance(competition_id, sess_id, pos, start_list.race_distance_km)
            except Exception:
                logger.warning("Failed to parse start list %s for event %d", url, competition_id, exc_info=True)

    await asyncio.gather(*[fetch_one(url, slots) for url, slots in to_fetch.items()])


async def _fetch_result_pages(
    client: httpx.AsyncClient,
    competition_id: int,
    sessions: list[Session],
) -> None:
    """
    Fetch result pages for all completed events that don't yet have a Generated
    timestamp cached. Parses both the Generated timestamp (for generated-time
    derived slot durations) and the Finish Time (for bunch-race observed durations).

    Runs on every load/refresh but skips already-cached events, so only new
    completions are fetched. This makes predictions self-correcting throughout
    the day, even when the app is loaded mid-event. Observed durations go to the
    learning database in one worker-thread call once every page is parsed. Events
    that share a result page (the rides of a sprint round) share one fetch.

    An event with an audit page also gets that page's Generated timestamp, and the
    earlier of the two is kept (record_generated_time): upstream regenerates either
    page after corrections, moving its timestamp past the event's end.
    """
    to_fetch: dict[str, list[tuple[int, int, str, str]]] = {}
    audits: dict[str, list[tuple[int, int]]] = {}
    for s in sessions:
        # A best-of-3 round's shared page is refetched until each ride is known to be done.
        pending_rides = pending_sprint_rides(competition_id, s)
        for e in s.events:
            cached = get_generated_time(competition_id, s.session_id, e.position) is not None
            if e.result_url and (not cached or e.position in pending_rides):
                to_fetch.setdefault(e.result_url, []).append((s.session_id, e.position, e.discipline, e.name))
                if e.audit_url:
                    audits.setdefault(e.audit_url, []).append((s.session_id, e.position))
    if not to_fetch:
        return

    sem = asyncio.Semaphore(10)
    observed: list[LiveDuration] = []

    async def fetch_one(url: str, slots: list[tuple[int, int, str, str]]) -> None:
        async with sem:
            try:
                html = await fetch_page_html(client, url)
            except Exception:
                logger.warning("Failed to fetch result page %s for event %d", url, competition_id, exc_info=True)
                return
            try:
                gen_time = parse_generated_time(html)
                finish_time = parse_finish_time(html)
                rides = [r for _, _, d, n in slots if d == "sprint_match" and (r := split_ride(n)) is not None]
                rides_done = parse_sprint_rides_done(html) if rides else None
                if rides_done is not None:
                    record_sprint_rides_done(competition_id, rides[0][0], rides_done)
                for sess_id, pos, discipline, name in slots:
                    ride = split_ride(name) if discipline == "sprint_match" else None
                    # The shared page's Generated marks a ride's end only once that ride is done.
                    ride_not_done = ride is not None and rides_done is not None and ride[1] > rides_done
                    if gen_time is not None and not ride_not_done:
                        record_generated_time(competition_id, sess_id, pos, gen_time)
                    if finish_time is not None:
                        observed.append(
                            record_observed_duration(competition_id, sess_id, pos, finish_time, discipline, name)
                        )
                if rides:
                    decider_range = parse_sprint_decider_range(html)
                    if decider_range is not None:
                        record_sprint_decider_range(competition_id, rides[0][0], *decider_range)
            except Exception:
                logger.warning("Failed to parse result page %s for event %d", url, competition_id, exc_info=True)

    async def fetch_audit(url: str, slots: list[tuple[int, int]]) -> None:
        async with sem:
            try:
                gen_time = parse_generated_time(await fetch_page_html(client, url))
            except Exception:
                logger.warning("Failed to read audit page %s for event %d", url, competition_id, exc_info=True)
                return
            if gen_time is not None:
                for sess_id, pos in slots:
                    record_generated_time(competition_id, sess_id, pos, gen_time)

    await asyncio.gather(
        *[fetch_one(url, slots) for url, slots in to_fetch.items()],
        *[fetch_audit(url, slots) for url, slots in audits.items()],
    )
    if observed:
        await asyncio.to_thread(save_live_durations, observed)


async def _fetch_rider_list(client: httpx.AsyncClient, url: str) -> list[RiderListEntry] | None:
    """
    Return the parsed Rider List at url, fetching it on a cache miss.

    The file doesn't change during a competition, so a non-empty parse is cached
    for the life of the container. A failed fetch or a 0-row parse returns None and
    isn't retried for RIDER_LIST_RETRY_SECONDS, so a broken link doesn't cost a
    download (or a timeout) on every poll.
    """
    cached = get_rider_list(url)
    if cached is not None:
        return cached
    if rider_list_retry_pending(url, time.monotonic()):
        return None
    try:
        entries = parse_rider_list(await fetch_page_html(client, url))
        if not entries:
            raise ValueError("no rider rows")
    except Exception:
        logger.warning("Failed to fetch Rider List %s", url, exc_info=True)
        record_rider_list_failure(url, time.monotonic())
        return None
    record_rider_list(url, entries)
    return entries


async def _fetch_rider_list_if_needed(
    client: httpx.AsyncClient,
    competition_id: int,
    jxn_data: dict,
    sessions: list[Session],
    racer_name: str | None,
) -> list[RiderListEntry] | None:
    """
    Fetch the Rider List when it can change the prediction: a racer is set and some
    race may lack start-list riders, a combined-age final has no cached start-list
    categories to forecast its ceremony podiums from, or an individual qualifying
    round has no start-list heat count to size it by.

    Runs alongside the start-list fetches, so it uses the pre-fetch approximation:
    a non-special event with no start_list_url or no cached start-list riders.
    """
    needed_for_racer = (
        racer_name is not None
        and racer_name.strip() != ""
        and any(
            not e.is_special
            and (not e.start_list_url or not has_start_list_riders(competition_id, s.session_id, e.position))
            for s in sessions
            for e in s.events
        )
    )
    needed_for_podiums = any(
        needs_categories(e) and not has_start_list_categories(competition_id, s.session_id, e.position)
        for s in sessions
        for e in s.events
    )
    needed_for_heats = any(
        e.status != EventStatus.COMPLETED
        and needs_heat_estimate(e)
        and get_heat_count(competition_id, s.session_id, e.position) is None
        for s in sessions
        for e in s.events
    )
    if not (needed_for_racer or needed_for_podiums or needed_for_heats):
        return None
    url = parse_rider_list_url(jxn_data)
    return await _fetch_rider_list(client, url) if url else None


def _use_learned(request: Request) -> bool:
    return request.cookies.get("use_learned") == "true"


def _encode_racer_name(name: str) -> str:
    """URL-safe Base64 encode a racer name for use in query parameters."""
    return base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii")


# Cookie values are Base64 behind this prefix because Starlette encodes headers as
# Latin-1 (padding is stripped so the value needs no quoting). Older cookies hold
# the raw name and are still accepted.
_COOKIE_PREFIX = "b64."


def _set_racer_cookie(response: Response, name: str) -> None:
    response.set_cookie(
        key="racer_name",
        value=_COOKIE_PREFIX + _encode_racer_name(name).rstrip("="),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=31536000,
    )


def _cookie_racer_name(request: Request) -> str | None:
    """Return the racer name stored in the cookie, decoding the Base64 form."""
    value = request.cookies.get("racer_name")
    if not value:
        return None
    if value.startswith(_COOKIE_PREFIX):
        try:
            encoded = value[len(_COOKIE_PREFIX) :]
            return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8") or None
        except (binascii.Error, UnicodeDecodeError):
            logger.warning("Malformed racer_name cookie, ignoring it")
            return None
    return value


def _resolve_racer_name(request: Request, r: str | None) -> str | None:
    """Resolve racer name from URL-safe Base64 param or cookie."""
    if r:
        try:
            return base64.urlsafe_b64decode(r).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            logger.warning("Malformed base64 racer name param: %r, falling back to cookie", r)
            # Fall through to cookie rather than returning None
    return _cookie_racer_name(request)


def _content_disposition(filename: str) -> str:
    """Attachment header with an ASCII fallback name and an RFC 5987 UTF-8 name."""
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


@app.get("/health")
async def health() -> dict[str, object]:
    try:
        db_status = await asyncio.wait_for(check_health(), timeout=5.0)
    except TimeoutError:
        db_status = {"status": "degraded", "detail": "Health check timed out"}

    try:
        palmares_status = await asyncio.wait_for(check_palmares_health(), timeout=5.0)
    except TimeoutError:
        palmares_status = {"status": "degraded", "detail": "Palmares health check timed out"}

    components = {"database": db_status, "palmares": palmares_status}
    all_healthy = all(c["status"] == "healthy" for c in components.values())
    return {"status": "healthy" if all_healthy else "degraded", "components": components}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    return templates.TemplateResponse(request, "index.html")


# Disciplines that produce per-lap/sector audit data (pursuits + time trials)
_TIMED_DISCIPLINES = frozenset(
    {
        "pursuit_4k",
        "pursuit_3k",
        "pursuit_2k",
        "team_pursuit",
        "team_sprint",
        "time_trial_500",
        "time_trial_750",
        "time_trial_kilo",
        "time_trial_generic",
    }
)


def _collect_palmares_entries(
    schedule: SchedulePrediction,
    competition_id: int,
) -> list[PalmaresEntry]:
    """Collect palmares entries from schedule predictions.

    Filters for timed events (pursuits and time trials) where the racer
    was matched on a start list, has an audit URL, and is not a special event.
    Rider List matches don't prove the racer rode, so they are skipped.
    """
    if not schedule.racer_name:
        return []

    comp_name = f"Competition {competition_id}"

    # Derive competition date from earliest Generated timestamp on result pages.
    # Every event with an audit URL has a result page with a Generated timestamp,
    # so comp_date will be set whenever entries are collected.
    comp_date = None
    for sp in schedule.sessions:
        for pred in sp.event_predictions:
            gen_time = get_generated_time(competition_id, sp.session.session_id, pred.event.position)
            if gen_time is not None:
                d = gen_time.date().isoformat()
                if comp_date is None or d < comp_date:
                    comp_date = d

    entries = []
    for sp in schedule.sessions:
        for pred in sp.event_predictions:
            if (
                pred.rider_match
                and pred.rider_match.source == "start_list"
                and pred.event.audit_url
                and not pred.event.is_special
                and pred.event.discipline in _TIMED_DISCIPLINES
            ):
                entries.append(
                    PalmaresEntry(
                        racer_name=schedule.racer_name,
                        competition_id=competition_id,
                        competition_name=comp_name,
                        competition_date=comp_date,
                        session_id=sp.session.session_id,
                        session_name=sp.session.day,
                        event_position=pred.event.position,
                        event_name=pred.event.name,
                        audit_url=pred.event.audit_url,
                        team_name=pred.rider_match.team_name,
                    )
                )
    return entries


async def _save_and_count_palmares(
    schedule: SchedulePrediction,
    competition_id: int,
) -> int:
    """Save matched palmares entries and return the count for the competition.

    The database calls run in a worker thread. Returns 0 if no racer name is set or on error.
    """
    racer_name = schedule.racer_name
    if not racer_name:
        return 0

    def save_and_count(entries: list[PalmaresEntry]) -> int:
        if entries:
            save_palmares_entries(entries)
        return count_competition_palmares(racer_name, competition_id)

    try:
        return await asyncio.to_thread(save_and_count, _collect_palmares_entries(schedule, competition_id))
    except Exception:
        logger.warning("Palmares save failed", exc_info=True)
        return 0


@app.get("/schedule", response_class=RedirectResponse)
async def schedule_redirect(event_id: int = Query(...)) -> RedirectResponse:
    """No-JS fallback: redirect GET /schedule?event_id=X to /schedule/X."""
    return RedirectResponse(url=f"/schedule/{event_id}", status_code=303)


@app.get("/schedule/{event_id}", response_class=HTMLResponse)
async def get_schedule(
    request: Request,
    event_id: int,
    r: str | None = Query(None),
    settings: Settings = Depends(get_settings),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """GET version of schedule so links and bookmarks work."""
    try:
        jxn_data = await fetch_initial_layout(client, event_id)
    except Exception:
        logger.warning("Failed to fetch event %d", event_id, exc_info=True)
        raise HTTPException(
            status_code=502, detail=f"Failed to fetch event {event_id} from tracktiming.live."
        ) from None

    sessions = parse_schedule(jxn_data)
    reconcile_positions(event_id, sessions)
    if not sessions:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule found for event {event_id}.",
        )

    racer_name = _resolve_racer_name(request, r)
    _, _, _, rider_list = await asyncio.gather(
        _fetch_start_lists(client, event_id, sessions),
        _fetch_result_pages(client, event_id, sessions),
        _fetch_live_heats(client, event_id, sessions),
        _fetch_rider_list_if_needed(client, event_id, jxn_data, sessions, racer_name),
    )
    sessions = apply_sprint_ride_status(event_id, sessions)
    now = venue_now(latest_live_generated_time(event_id, sessions))
    use_learned = _use_learned(request)
    learned = await asyncio.to_thread(load_learned_durations, sessions) if use_learned else None
    schedule = predict_schedule(
        event_id, sessions, now=now, racer_name=racer_name, learned=learned, rider_list=rider_list
    )

    # Determine name source for logging
    source = "none"
    if r and racer_name:
        source = "url"
    elif racer_name:
        source = "cookie"
    logger.info(
        "racer_name_resolved",
        extra={
            "source": source,
            "competition_id": event_id,
            "match_count": schedule.match_count,
            "events_without_start_lists": schedule.events_without_start_lists,
            "total_events": schedule.total_events,
            "rider_list_matches": schedule.rider_list_match_count,
            "tentative_matches": schedule.tentative_match_count,
        },
    )

    racer_encoded = None
    if racer_name:
        racer_encoded = _encode_racer_name(racer_name)

    palmares_count = await _save_and_count_palmares(schedule, event_id)

    # Use racer's custom competition name if they've set one via /palmares/rename
    competition_name = f"Competition {event_id}"
    if racer_name and palmares_count:
        stored_name = await asyncio.to_thread(get_competition_name, racer_name, event_id)
        competition_name = stored_name or competition_name

    response = templates.TemplateResponse(
        request,
        "schedule.html",
        {
            "schedule": schedule,
            "competition_id": event_id,
            "competition_name": competition_name,
            "now": now,
            "refresh_seconds": settings.refresh_interval_seconds,
            "use_learned": use_learned,
            "racer_name": racer_name,
            "racer_encoded": racer_encoded,
            "palmares_count": palmares_count,
        },
    )

    # FR-009: refresh cookie on every visit with a resolved name (rolling expiry)
    if racer_name:
        _set_racer_cookie(response, racer_name)

    return response


@app.get("/schedule/{event_id}/refresh", response_class=HTMLResponse)
async def refresh_schedule(
    request: Request,
    event_id: int,
    r: str | None = Query(None),
    settings: Settings = Depends(get_settings),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """
    HTMX polling endpoint. Called every N seconds to update the schedule.
    Returns only the schedule body partial for injection into the page.
    Also triggers the learning mechanism when event status transitions occur.
    """
    try:
        jxn_data = await fetch_refresh(client, event_id)
    except Exception:
        logger.warning("Failed to refresh event %d", event_id, exc_info=True)
        raise HTTPException(
            status_code=502, detail=f"Failed to refresh event {event_id} from tracktiming.live."
        ) from None

    sessions = parse_schedule(jxn_data)
    reconcile_positions(event_id, sessions)
    racer_name = _resolve_racer_name(request, r)

    _, _, _, rider_list = await asyncio.gather(
        # Fetch any start lists not yet cached (e.g. newly published since initial load).
        _fetch_start_lists(client, event_id, sessions),
        # Fetch result pages for completed events not yet in the generated-time cache.
        # This populates Generated timestamps (for inter-event durations) and Finish
        # Times (for bunch-race observed durations), retroactively if needed.
        _fetch_result_pages(client, event_id, sessions),
        # Fetch live results page to get current heat number (changes each heat).
        _fetch_live_heats(client, event_id, sessions),
        # Rider List for events without start-list riders; cached after the first fetch.
        _fetch_rider_list_if_needed(client, event_id, jxn_data, sessions, racer_name),
    )

    sessions = apply_sprint_ride_status(event_id, sessions)
    now = venue_now(latest_live_generated_time(event_id, sessions))

    # Track status transitions for wall-clock fallback learning.
    wall_clock = update_status_cache(event_id, sessions, now)
    if wall_clock:
        await asyncio.to_thread(save_live_durations, wall_clock)

    learned = await asyncio.to_thread(load_learned_durations, sessions) if _use_learned(request) else None
    schedule = predict_schedule(
        event_id,
        sessions,
        now=now,
        racer_name=racer_name,
        learned=learned,
        rider_list=rider_list,
    )

    palmares_count = await _save_and_count_palmares(schedule, event_id)
    racer_encoded = _encode_racer_name(racer_name) if racer_name else None

    return templates.TemplateResponse(
        request,
        "_schedule_body.html",
        {
            "schedule": schedule,
            "competition_id": event_id,
            "now": now,
            "palmares_count": palmares_count,
            "racer_encoded": racer_encoded,
        },
    )


@app.get("/settings/use-learned")
async def toggle_use_learned(event_id: int = Query(...), use_learned: str = Query("off")) -> RedirectResponse:
    """Toggle the learned-durations feature flag for the current browser session."""
    response = RedirectResponse(url=f"/schedule/{event_id}", status_code=303)
    if use_learned == "on":
        response.set_cookie(key="use_learned", value="true", httponly=True, secure=True, samesite="lax")
    else:
        response.delete_cookie(key="use_learned")
    return response


@app.get("/settings/racer-name")
async def set_racer_name(event_id: int = Query(...), name: str = Query("")) -> RedirectResponse:
    """Set or clear the racer name cookie, then redirect back to the schedule."""
    if name.strip():
        encoded = _encode_racer_name(name)
        response = RedirectResponse(
            url=f"/schedule/{event_id}?r={encoded}#schedule-container",
            status_code=303,
        )
        _set_racer_cookie(response, name)
    else:
        response = RedirectResponse(url=f"/schedule/{event_id}", status_code=303)
        response.delete_cookie(key="racer_name")
    return response


@app.get("/palmares", response_class=HTMLResponse)
async def palmares_page(
    request: Request,
    r: str | None = Query(None),
    name: str | None = Query(None),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Palmares profile page — shows racer achievements grouped by competition."""
    # Handle name form submission: set cookie and redirect
    if name and name.strip():
        submitted = name.strip()
        response = RedirectResponse(url=f"/palmares?r={_encode_racer_name(submitted)}", status_code=303)
        _set_racer_cookie(response, submitted)
        return response

    racer_name = _resolve_racer_name(request, r)
    cookie_name = _cookie_racer_name(request)
    is_owner = racer_name is not None and cookie_name == racer_name

    if racer_name:
        racer_encoded = _encode_racer_name(racer_name)
        competitions = await asyncio.to_thread(get_palmares, racer_name)
        # Behind CloudFront the request host is the IAM-protected Function URL,
        # so prod sets PUBLIC_BASE_URL to the public domain.
        base = settings.public_base_url.rstrip("/") or f"{request.url.scheme}://{request.url.netloc}"
        share_url = f"{base}/palmares?r={racer_encoded}"
    else:
        racer_encoded = None
        competitions = []
        share_url = None

    return templates.TemplateResponse(
        request,
        "palmares.html",
        {
            "racer_name": racer_name,
            "racer_encoded": racer_encoded,
            "competitions": competitions,
            "is_owner": is_owner,
            "share_url": share_url,
        },
    )


# Audit pages are ~25 KB; anything far larger isn't one.
_MAX_AUDIT_CHARS = 2_000_000


@app.get("/palmares/export")
async def palmares_export(
    request: Request,
    audit_url: str = Query(...),
    r: str | None = Query(None),
    team_name: str | None = Query(None),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """CSV export of individual audit result data for a specific event."""
    racer_name = _resolve_racer_name(request, r)
    if not racer_name:
        raise HTTPException(status_code=400, detail="Racer identity required")

    # SSRF protection: normalise percent-encoding and ".." before checking the prefix
    audit_url = posixpath.normpath(unquote(audit_url))
    if "://" in audit_url or not audit_url.startswith("results/"):
        raise HTTPException(status_code=400, detail="Invalid audit URL")

    try:
        resp = await client.get(audit_url)
        resp.raise_for_status()
        if len(resp.text) > _MAX_AUDIT_CHARS:
            raise ValueError(f"audit page too large ({len(resp.text)} chars)")
    except Exception:
        logger.warning("Failed to fetch audit page: %s", audit_url, exc_info=True)
        return JSONResponse(
            content={"error": "Could not load audit data from tracktiming.live"},
            status_code=502,
        )

    # For team events, filter by team name instead of racer name
    filter_name = team_name.strip() if team_name else racer_name
    riders = parse_audit_riders(resp.text)
    filtered = filter_rider_data(riders, filter_name)
    event_name = audit_url.split("/")[-1].replace("-AUDIT-R.htm", "")
    csv_str = format_csv(filtered, event_name)

    headers = {"Content-Disposition": _content_disposition(f"{event_name}-{filter_name}.csv")}
    if not filtered:
        headers["X-Palmares-Notice"] = "no-matching-data"

    return Response(content=csv_str, media_type="text/csv", headers=headers)


@app.get("/palmares/remove")
async def palmares_remove(
    request: Request,
    competition_id: int = Query(...),
) -> RedirectResponse:
    """Delete all palmares entries for a competition. Cookie-only auth."""
    cookie_name = _cookie_racer_name(request)
    if not cookie_name:
        raise HTTPException(status_code=403, detail="Cookie-based identity required")

    deleted = await asyncio.to_thread(delete_competition_palmares, cookie_name, competition_id)
    if deleted == 0:
        logger.warning("Palmares remove returned 0 for racer=%s comp=%d", cookie_name, competition_id)
    return RedirectResponse(url="/palmares", status_code=303)


@app.get("/palmares/rename")
async def palmares_rename(
    request: Request,
    competition_id: int = Query(...),
    name: str = Query(""),
) -> RedirectResponse:
    """Update competition name. Cookie-only auth."""
    cookie_name = _cookie_racer_name(request)
    if not cookie_name:
        raise HTTPException(status_code=403, detail="Cookie-based identity required")
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name is required")

    updated = await asyncio.to_thread(update_competition_palmares, cookie_name, competition_id, name.strip())
    if updated == 0:
        logger.warning("Palmares rename returned 0 for racer=%s comp=%d", cookie_name, competition_id)
    return RedirectResponse(url="/palmares", status_code=303)


@app.get("/defaults", response_class=HTMLResponse)
async def default_durations(request: Request) -> Response:
    """Display the built-in default durations for inspection."""
    rows = [
        {"discipline": d, "default": DEFAULT_DURATIONS[d], "per_heat": PER_HEAT_DURATIONS.get(d)}
        for d in DEFAULT_DURATIONS
    ]
    genders = {"M": "Men", "W": "Women"}
    paces = [{"group": "No age band in the name", "kmh": BUNCH_RACE_KMH}] + [
        {"group": f"{genders[g]} {_age_label(b)}", "kmh": b.value}
        for g, brackets in MASTERS_BUNCH_RACE_KMH.items()
        for b in brackets
    ]
    masters_per_heat = [
        {"discipline": d, "ages": _age_label(b), "per_heat": b.value, "default": PER_HEAT_DURATIONS[d]}
        for d, brackets in MASTERS_PER_HEAT_DURATIONS.items()
        for b in brackets
    ]
    rules = {
        "ceremony_base": CEREMONY_BASE_MINUTES,
        "ceremony_per_podium": CEREMONY_PER_PODIUM_MINUTES,
        "decider_minutes": SPRINT_DECIDER_MINUTES,
        "decider_rate": SPRINT_DECIDER_RATE,
        "live_changeover": LIVE_BUNCH_CHANGEOVER_MINUTES,
        "min_changeover_samples": MIN_CHANGEOVER_SAMPLES,
        "static_changeover": CHANGEOVER_MINUTES["scratch_race"],
    }
    return templates.TemplateResponse(
        request,
        "defaults.html",
        {"rows": rows, "rules": rules, "paces": paces, "masters_per_heat": masters_per_heat},
    )


def _age_label(bracket: AgeBracket) -> str:
    """'under 70', '70-74', '75+' or 'all ages' for an age bracket's youngest-age range."""
    if bracket.lo == 0:
        return "all ages" if bracket.hi is None else f"under {bracket.hi}"
    return f"{bracket.lo}+" if bracket.hi is None else f"{bracket.lo}-{bracket.hi - 1}"


@app.get("/learned", response_class=HTMLResponse)
async def learned_durations(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    """Display the learned duration database for inspection."""
    durations = await asyncio.to_thread(get_all_learned_durations)
    return templates.TemplateResponse(
        request,
        "learned.html",
        {
            "durations": durations,
            "min_samples": settings.min_learned_samples,
        },
    )


# Mangum's lifespan="auto" runs the lifespan on every invocation, which would create and
# close the HTTP client per request. With it off, the client is created lazily by
# get_http_client and the database schema is initialised once per container.
_mangum = Mangum(app, lifespan="off")
_db_initialised = False


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    global _db_initialised
    if not _db_initialised:
        init_db()
        init_palmares_db()
        _db_initialised = True
    return _mangum(event, context)
