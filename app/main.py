import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import get_all_learned_durations, init_db
from app.fetcher import fetch_initial_layout, fetch_live_html, fetch_refresh, fetch_result_html, fetch_start_list_html
from app.models import Session
from app.parser import parse_finish_time, parse_generated_time, parse_heat_count, parse_live_heat, parse_schedule
from app.predictor import (
    get_generated_time,
    get_heat_count,
    predict_schedule,
    record_generated_time,
    record_heat_count,
    record_live_heat,
    record_observed_duration,
    update_status_cache,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Track Timing Predictor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")


async def _fetch_live_heats(event_id: int, sessions: list[Session]) -> None:
    """
    Fetch the live results page for any event that has a live_url and parse
    the current heat number. Called on every refresh since the page changes
    as each heat completes.
    """
    to_fetch = [
        (event_id, s.session_id, e.position, e.live_url)
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
                heat = parse_live_heat(html)
                if heat is not None:
                    record_live_heat(ev_id, sess_id, pos, heat)
            except Exception:
                pass

    await asyncio.gather(*[fetch_one(*args) for args in to_fetch])


async def _fetch_start_lists(event_id: int, sessions: list[Session]) -> None:
    """
    Concurrently fetch start list pages for all events that have a start_list_url
    and whose heat count has not yet been cached. Records heat counts in-memory.
    """
    to_fetch = [
        (event_id, s.session_id, e.position, e.start_list_url, e.discipline)
        for s in sessions
        for e in s.events
        if e.start_list_url and get_heat_count(event_id, s.session_id, e.position) is None
    ]
    if not to_fetch:
        return

    sem = asyncio.Semaphore(10)

    async def fetch_one(ev_id: int, sess_id: int, pos: int, url: str, discipline: str) -> None:
        async with sem:
            try:
                html = await fetch_start_list_html(url)
                count = parse_heat_count(html)
                if count:
                    record_heat_count(ev_id, sess_id, pos, count)
            except Exception:
                pass

    await asyncio.gather(*[fetch_one(*args) for args in to_fetch])


async def _fetch_result_pages(event_id: int, sessions: list[Session]) -> None:
    """
    Fetch result pages for all completed events that don't yet have a Generated
    timestamp cached. Parses both the Generated timestamp (for generated-time
    derived slot durations) and the Finish Time (for bunch-race observed durations).

    Runs on every load/refresh but skips already-cached events, so only new
    completions are fetched. This makes predictions self-correcting throughout
    the day, even when the app is loaded mid-event.
    """
    to_fetch = [
        (event_id, s.session_id, e.position, e.result_url, e.discipline)
        for s in sessions
        for e in s.events
        if e.result_url and get_generated_time(event_id, s.session_id, e.position) is None
    ]
    if not to_fetch:
        return

    sem = asyncio.Semaphore(10)

    async def fetch_one(ev_id: int, sess_id: int, pos: int, url: str, discipline: str) -> None:
        async with sem:
            try:
                html = await fetch_result_html(url)
                gen_time = parse_generated_time(html)
                if gen_time is not None:
                    record_generated_time(ev_id, sess_id, pos, gen_time)
                finish_time = parse_finish_time(html)
                if finish_time is not None:
                    record_observed_duration(ev_id, sess_id, pos, finish_time, discipline)
            except Exception:
                pass

    await asyncio.gather(*[fetch_one(*args) for args in to_fetch])


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/schedule")
async def show_schedule(event_id: int = Form(...)):
    """Redirect to the bookmarkable GET route for the given event."""
    return RedirectResponse(url=f"/schedule/{event_id}", status_code=303)


@app.get("/schedule/{event_id}", response_class=HTMLResponse)
async def get_schedule(request: Request, event_id: int):
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

    await _fetch_start_lists(event_id, sessions)
    await _fetch_result_pages(event_id, sessions)
    await _fetch_live_heats(event_id, sessions)
    now = datetime.now()
    schedule = predict_schedule(event_id, sessions, now=now)

    return templates.TemplateResponse("schedule.html", {
        "request": request,
        "schedule": schedule,
        "event_id": event_id,
        "now": now,
        "refresh_seconds": settings.refresh_interval_seconds,
        "base_url": settings.tracktiming_base_url,
    })


@app.get("/schedule/{event_id}/refresh", response_class=HTMLResponse)
async def refresh_schedule(request: Request, event_id: int):
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

    # Fetch any start lists not yet cached (e.g. newly published since initial load).
    await _fetch_start_lists(event_id, sessions)

    # Fetch result pages for completed events not yet in the generated-time cache.
    # This populates Generated timestamps (for inter-event durations) and Finish
    # Times (for bunch-race observed durations), retroactively if needed.
    await _fetch_result_pages(event_id, sessions)

    # Fetch live results page to get current heat number (changes each heat).
    await _fetch_live_heats(event_id, sessions)

    # Track status transitions for wall-clock fallback learning.
    update_status_cache(event_id, sessions, now)

    schedule = predict_schedule(event_id, sessions, now=now)

    return templates.TemplateResponse("_schedule_body.html", {
        "request": request,
        "schedule": schedule,
        "event_id": event_id,
        "now": now,
        "base_url": settings.tracktiming_base_url,
    })


@app.get("/learned", response_class=HTMLResponse)
async def learned_durations(request: Request):
    """Display the learned duration database for inspection."""
    durations = get_all_learned_durations()
    return templates.TemplateResponse("learned.html", {
        "request": request,
        "durations": durations,
        "min_samples": settings.min_learned_samples,
    })
