import asyncio
import base64
import binascii
import logging
import posixpath
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote, unquote

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mangum import Mangum
from pythonjsonlogger.json import JsonFormatter

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
    MIN_CHANGEOVER_SAMPLES,
    PER_HEAT_DURATIONS,
    SPRINT_DECIDER_MINUTES,
    SPRINT_DECIDER_RATE,
    get_changeover,
    get_per_heat_duration,
    split_ride,
)
from app.fetcher import fetch_initial_layout, fetch_page_html, fetch_refresh
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
    parse_finish_time,
    parse_generated_time,
    parse_heat_count,
    parse_live_heat,
    parse_race_distance_km,
    parse_rider_list,
    parse_rider_list_url,
    parse_schedule,
    parse_sprint_deciders,
    parse_start_list_categories,
    parse_start_list_riders,
)
from app.predictor import (
    get_generated_time,
    get_heat_count,
    get_rider_list,
    has_start_list_categories,
    has_start_list_riders,
    is_start_list_cached,
    latest_live_generated_time,
    predict_schedule,
    record_generated_time,
    record_heat_count,
    record_live_heat,
    record_observed_duration,
    record_race_distance,
    record_rider_list,
    record_rider_list_failure,
    record_sprint_deciders,
    record_start_list_categories,
    record_start_list_riders,
    rider_list_retry_pending,
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    init_palmares_db()
    settings = get_settings()
    app.state.http_client = httpx.AsyncClient(
        base_url=settings.tracktiming_base_url,
        timeout=15.0,
        limits=httpx.Limits(max_connections=50),
    )
    yield
    await app.state.http_client.aclose()


app = FastAPI(title="Track Timing Predictor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["per_heat_minutes"] = get_per_heat_duration
templates.env.globals["changeover_minutes"] = get_changeover
templates.env.globals["bunch_race_kmh"] = BUNCH_RACE_KMH
templates.env.globals["decider_rate"] = SPRINT_DECIDER_RATE


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


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
        (competition_id, s.session_id, e.position, e.live_url) for s in sessions for e in s.events if e.live_url
    ]
    if not to_fetch:
        return

    sem = asyncio.Semaphore(5)

    async def fetch_one(ev_id: int, sess_id: int, pos: int, url: str) -> None:
        async with sem:
            try:
                html = await fetch_page_html(client, url)
            except Exception:
                logger.warning(
                    "Failed to fetch live heat for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True
                )
                return
            try:
                heat = parse_live_heat(html)
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
    is fetched at most once, since it can't change.
    """
    to_fetch = [
        (competition_id, s.session_id, e.position, e.start_list_url, e.discipline)
        for s in sessions
        for e in s.events
        if e.start_list_url
        and (
            get_heat_count(competition_id, s.session_id, e.position) is None
            or not has_start_list_riders(competition_id, s.session_id, e.position)
        )
        and not (e.status == EventStatus.COMPLETED and is_start_list_cached(competition_id, s.session_id, e.position))
    ]
    if not to_fetch:
        return

    sem = asyncio.Semaphore(10)

    async def fetch_one(ev_id: int, sess_id: int, pos: int, url: str, discipline: str) -> None:
        async with sem:
            try:
                html = await fetch_page_html(client, url)
            except Exception:
                logger.warning(
                    "Failed to fetch start list for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True
                )
                return
            try:
                count = parse_heat_count(html)
                if count:
                    record_heat_count(ev_id, sess_id, pos, count)
                riders = parse_start_list_riders(html)
                record_start_list_riders(ev_id, sess_id, pos, riders)
                record_start_list_categories(ev_id, sess_id, pos, parse_start_list_categories(html))
                if (km := parse_race_distance_km(html)) is not None:
                    record_race_distance(ev_id, sess_id, pos, km)
            except Exception:
                logger.warning(
                    "Failed to parse start list for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True
                )

    await asyncio.gather(*[fetch_one(*args) for args in to_fetch])


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
    the day, even when the app is loaded mid-event.
    """
    to_fetch = [
        (competition_id, s.session_id, e.position, e.result_url, e.discipline, e.name)
        for s in sessions
        for e in s.events
        if e.result_url and get_generated_time(competition_id, s.session_id, e.position) is None
    ]
    if not to_fetch:
        return

    sem = asyncio.Semaphore(10)

    async def fetch_one(ev_id: int, sess_id: int, pos: int, url: str, discipline: str, name: str) -> None:
        async with sem:
            try:
                html = await fetch_page_html(client, url)
            except Exception:
                logger.warning(
                    "Failed to fetch result page for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True
                )
                return
            try:
                gen_time = parse_generated_time(html)
                if gen_time is not None:
                    record_generated_time(ev_id, sess_id, pos, gen_time)
                finish_time = parse_finish_time(html)
                if finish_time is not None:
                    record_observed_duration(ev_id, sess_id, pos, finish_time, discipline, name)
                if discipline == "sprint_match" and (ride := split_ride(name)) is not None:
                    deciders = parse_sprint_deciders(html)
                    if deciders is not None:
                        record_sprint_deciders(ev_id, ride[0], deciders)
            except Exception:
                logger.warning(
                    "Failed to parse result page for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True
                )

    await asyncio.gather(*[fetch_one(*args) for args in to_fetch])


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


def _save_and_count_palmares(
    schedule: SchedulePrediction,
    competition_id: int,
) -> int:
    """Save matched palmares entries and return the count for the competition.

    Returns 0 if no racer name is set or on error.
    """
    racer_name = schedule.racer_name
    if not racer_name:
        return 0
    try:
        entries = _collect_palmares_entries(schedule, competition_id)
        if entries:
            save_palmares_entries(entries)
        return count_competition_palmares(racer_name, competition_id)
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
    now = venue_now(latest_live_generated_time(event_id, sessions))
    use_learned = _use_learned(request)
    schedule = predict_schedule(
        event_id, sessions, now=now, racer_name=racer_name, use_learned=use_learned, rider_list=rider_list
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

    palmares_count = _save_and_count_palmares(schedule, event_id)

    # Use racer's custom competition name if they've set one via /palmares/rename
    competition_name = f"Competition {event_id}"
    if racer_name and palmares_count:
        competition_name = get_competition_name(racer_name, event_id) or competition_name

    response = templates.TemplateResponse(
        request,
        "schedule.html",
        {
            "schedule": schedule,
            "competition_id": event_id,
            "competition_name": competition_name,
            "now": now,
            "refresh_seconds": settings.refresh_interval_seconds,
            "base_url": settings.tracktiming_base_url,
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

    now = venue_now(latest_live_generated_time(event_id, sessions))

    # Track status transitions for wall-clock fallback learning.
    update_status_cache(event_id, sessions, now)

    schedule = predict_schedule(
        event_id,
        sessions,
        now=now,
        racer_name=racer_name,
        use_learned=_use_learned(request),
        rider_list=rider_list,
    )

    palmares_count = _save_and_count_palmares(schedule, event_id)
    racer_encoded = _encode_racer_name(racer_name) if racer_name else None

    return templates.TemplateResponse(
        request,
        "_schedule_body.html",
        {
            "schedule": schedule,
            "competition_id": event_id,
            "now": now,
            "base_url": settings.tracktiming_base_url,
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
        competitions = get_palmares(racer_name)
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
            "base_url": settings.tracktiming_base_url,
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

    deleted = delete_competition_palmares(cookie_name, competition_id)
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

    updated = update_competition_palmares(cookie_name, competition_id, name.strip())
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
    rules = {
        "ceremony_base": CEREMONY_BASE_MINUTES,
        "ceremony_per_podium": CEREMONY_PER_PODIUM_MINUTES,
        "bunch_kmh": BUNCH_RACE_KMH,
        "decider_minutes": SPRINT_DECIDER_MINUTES,
        "decider_rate": SPRINT_DECIDER_RATE,
        "live_changeover": LIVE_BUNCH_CHANGEOVER_MINUTES,
        "min_changeover_samples": MIN_CHANGEOVER_SAMPLES,
        "static_changeover": CHANGEOVER_MINUTES["scratch_race"],
    }
    return templates.TemplateResponse(request, "defaults.html", {"rows": rows, "rules": rules})


@app.get("/learned", response_class=HTMLResponse)
async def learned_durations(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    """Display the learned duration database for inspection."""
    durations = get_all_learned_durations()
    return templates.TemplateResponse(
        request,
        "learned.html",
        {
            "durations": durations,
            "min_samples": settings.min_learned_samples,
        },
    )


handler = Mangum(app, lifespan="auto")
