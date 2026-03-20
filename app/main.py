import asyncio
import base64
import binascii
import logging
from contextlib import asynccontextmanager
from datetime import datetime

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Query, Request
from pythonjsonlogger import jsonlogger
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import get_all_learned_durations, init_db
from app.disciplines import DEFAULT_DURATIONS, PER_HEAT_DURATIONS
from app.fetcher import fetch_initial_layout, fetch_live_html, fetch_refresh, fetch_result_html, fetch_start_list_html
from app.models import Session
from app.parser import parse_finish_time, parse_generated_time, parse_heat_count, parse_live_heat, parse_schedule, parse_start_list_riders
from mangum import Mangum

from app.predictor import (
    get_generated_time,
    get_heat_count,
    has_start_list_riders,
    predict_schedule,
    record_generated_time,
    record_heat_count,
    record_live_heat,
    record_observed_duration,
    record_start_list_riders,
    update_status_cache,
)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    ))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # Quiet noisy uvicorn access logs in production; keep warnings
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Track Timing Predictor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")


async def _fetch_live_heats(competition_id: int, sessions: list[Session]) -> None:
    """
    Fetch the live results page for any event that has a live_url and parse
    the current heat number. Called on every refresh since the page changes
    as each heat completes.
    """
    to_fetch = [
        (competition_id, s.session_id, e.position, e.live_url)
        for s in sessions
        for e in s.events
        if e.live_url
    ]
    if not to_fetch:
        return

    sem = asyncio.Semaphore(5)

    async def fetch_one(ev_id: int, sess_id: int, pos: int, url: str) -> None:
        async with sem:
            try:
                html = await fetch_live_html(url)
            except Exception:
                logger.warning("Failed to fetch live heat for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True)
                return
            try:
                heat = parse_live_heat(html)
                if heat is not None:
                    record_live_heat(ev_id, sess_id, pos, heat)
            except Exception:
                logger.warning("Failed to parse live heat for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True)

    await asyncio.gather(*[fetch_one(*args) for args in to_fetch])


async def _fetch_start_lists(competition_id: int, sessions: list[Session]) -> None:
    """
    Concurrently fetch start list pages for all events that have a start_list_url
    and whose heat count or rider list has not yet been cached.
    Records heat counts and rider entries in-memory.
    """
    to_fetch = [
        (competition_id, s.session_id, e.position, e.start_list_url, e.discipline)
        for s in sessions
        for e in s.events
        if e.start_list_url and (
            get_heat_count(competition_id, s.session_id, e.position) is None
            or not has_start_list_riders(competition_id, s.session_id, e.position)
        )
    ]
    if not to_fetch:
        return

    sem = asyncio.Semaphore(10)

    async def fetch_one(ev_id: int, sess_id: int, pos: int, url: str, discipline: str) -> None:
        async with sem:
            try:
                html = await fetch_start_list_html(url)
            except Exception:
                logger.warning("Failed to fetch start list for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True)
                return
            try:
                count = parse_heat_count(html)
                if count:
                    record_heat_count(ev_id, sess_id, pos, count)
                riders = parse_start_list_riders(html)
                record_start_list_riders(ev_id, sess_id, pos, riders)
            except Exception:
                logger.warning("Failed to parse start list for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True)

    await asyncio.gather(*[fetch_one(*args) for args in to_fetch])


async def _fetch_result_pages(competition_id: int, sessions: list[Session]) -> None:
    """
    Fetch result pages for all completed events that don't yet have a Generated
    timestamp cached. Parses both the Generated timestamp (for generated-time
    derived slot durations) and the Finish Time (for bunch-race observed durations).

    Runs on every load/refresh but skips already-cached events, so only new
    completions are fetched. This makes predictions self-correcting throughout
    the day, even when the app is loaded mid-event.
    """
    to_fetch = [
        (competition_id, s.session_id, e.position, e.result_url, e.discipline)
        for s in sessions
        for e in s.events
        if e.result_url and get_generated_time(competition_id, s.session_id, e.position) is None
    ]
    if not to_fetch:
        return

    sem = asyncio.Semaphore(10)

    async def fetch_one(ev_id: int, sess_id: int, pos: int, url: str, discipline: str) -> None:
        async with sem:
            try:
                html = await fetch_result_html(url)
            except Exception:
                logger.warning("Failed to fetch result page for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True)
                return
            try:
                gen_time = parse_generated_time(html)
                if gen_time is not None:
                    record_generated_time(ev_id, sess_id, pos, gen_time)
                finish_time = parse_finish_time(html)
                if finish_time is not None:
                    record_observed_duration(ev_id, sess_id, pos, finish_time, discipline)
            except Exception:
                logger.warning("Failed to parse result page for event %d session %d pos %d", ev_id, sess_id, pos, exc_info=True)

    await asyncio.gather(*[fetch_one(*args) for args in to_fetch])


def _use_learned(request: Request) -> bool:
    return request.cookies.get("use_learned") == "true"


def _resolve_racer_name(request: Request, r: str | None) -> str | None:
    """Resolve racer name from URL-safe Base64 param or cookie."""
    if r:
        try:
            return base64.urlsafe_b64decode(r).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            logger.warning("Malformed base64 racer name param: %r, falling back to cookie", r)
            # Fall through to cookie rather than returning None
    return request.cookies.get("racer_name") or None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/schedule/{event_id}", response_class=HTMLResponse)
async def get_schedule(request: Request, event_id: int, r: str | None = Query(None)):
    """GET version of schedule so links and bookmarks work."""
    try:
        jxn_data = await fetch_initial_layout(event_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch event {event_id}: {e}")

    sessions = parse_schedule(jxn_data)
    if not sessions:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule found for event {event_id}.",
        )

    await asyncio.gather(
        _fetch_start_lists(event_id, sessions),
        _fetch_result_pages(event_id, sessions),
        _fetch_live_heats(event_id, sessions),
    )
    now = datetime.now()
    use_learned = _use_learned(request)
    racer_name = _resolve_racer_name(request, r)
    schedule = predict_schedule(event_id, sessions, now=now, racer_name=racer_name, use_learned=use_learned)

    # Determine name source for logging
    source = "none"
    if r and racer_name:
        source = "url"
    elif racer_name:
        source = "cookie"
    logger.info("racer_name_resolved", extra={
        "source": source, "racer_name": racer_name,
        "competition_id": event_id, "match_count": schedule.match_count,
        "events_without_start_lists": schedule.events_without_start_lists,
        "total_events": schedule.total_events,
    })

    racer_encoded = None
    if racer_name:
        racer_encoded = base64.urlsafe_b64encode(racer_name.encode("utf-8")).decode("ascii")

    response = templates.TemplateResponse("schedule.html", {
        "request": request,
        "schedule": schedule,
        "competition_id": event_id,
        "now": now,
        "refresh_seconds": settings.refresh_interval_seconds,
        "base_url": settings.tracktiming_base_url,
        "use_learned": use_learned,
        "racer_name": racer_name,
        "racer_encoded": racer_encoded,
    })

    # FR-009: refresh cookie on every visit with a resolved name (rolling expiry)
    if racer_name:
        response.set_cookie(
            key="racer_name", value=racer_name,
            httponly=True, secure=True, samesite="lax", max_age=31536000,
        )

    return response


@app.get("/schedule/{event_id}/refresh", response_class=HTMLResponse)
async def refresh_schedule(request: Request, event_id: int, r: str | None = Query(None)):
    """
    HTMX polling endpoint. Called every N seconds to update the schedule.
    Returns only the schedule body partial for injection into the page.
    Also triggers the learning mechanism when event status transitions occur.
    """
    try:
        jxn_data = await fetch_refresh(event_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to refresh event {event_id}: {e}")

    now = datetime.now()
    sessions = parse_schedule(jxn_data)

    await asyncio.gather(
        # Fetch any start lists not yet cached (e.g. newly published since initial load).
        _fetch_start_lists(event_id, sessions),
        # Fetch result pages for completed events not yet in the generated-time cache.
        # This populates Generated timestamps (for inter-event durations) and Finish
        # Times (for bunch-race observed durations), retroactively if needed.
        _fetch_result_pages(event_id, sessions),
        # Fetch live results page to get current heat number (changes each heat).
        _fetch_live_heats(event_id, sessions),
    )

    # Track status transitions for wall-clock fallback learning.
    update_status_cache(event_id, sessions, now)

    racer_name = _resolve_racer_name(request, r)
    schedule = predict_schedule(event_id, sessions, now=now, racer_name=racer_name, use_learned=_use_learned(request))

    return templates.TemplateResponse("_schedule_body.html", {
        "request": request,
        "schedule": schedule,
        "competition_id": event_id,
        "now": now,
        "base_url": settings.tracktiming_base_url,
    })


@app.get("/settings/use-learned")
async def toggle_use_learned(event_id: int = Query(...), use_learned: str = Query("off")):
    """Toggle the learned-durations feature flag for the current browser session."""
    response = RedirectResponse(url=f"/schedule/{event_id}", status_code=303)
    if use_learned == "on":
        response.set_cookie(key="use_learned", value="true", httponly=True, secure=True, samesite="lax")
    else:
        response.delete_cookie(key="use_learned")
    return response


@app.get("/settings/racer-name")
async def set_racer_name(event_id: int = Query(...), name: str = Query("")):
    """Set or clear the racer name cookie, then redirect back to the schedule."""
    if name.strip():
        encoded = base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii")
        response = RedirectResponse(
            url=f"/schedule/{event_id}?r={encoded}#schedule-container",
            status_code=303,
        )
        response.set_cookie(
            key="racer_name", value=name,
            httponly=True, secure=True, samesite="lax", max_age=31536000,
        )
    else:
        response = RedirectResponse(url=f"/schedule/{event_id}", status_code=303)
        response.delete_cookie(key="racer_name")
    return response


@app.get("/defaults", response_class=HTMLResponse)
async def default_durations(request: Request):
    """Display the built-in default durations for inspection."""
    rows = [
        {"discipline": d, "default": DEFAULT_DURATIONS[d], "per_heat": PER_HEAT_DURATIONS.get(d)}
        for d in DEFAULT_DURATIONS
    ]
    return templates.TemplateResponse("defaults.html", {"request": request, "rows": rows})


@app.get("/learned", response_class=HTMLResponse)
async def learned_durations(request: Request):
    """Display the learned duration database for inspection."""
    durations = get_all_learned_durations()
    return templates.TemplateResponse("learned.html", {
        "request": request,
        "durations": durations,
        "min_samples": settings.min_learned_samples,
    })


handler = Mangum(app, lifespan="auto")
