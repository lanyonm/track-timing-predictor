from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import get_all_learned_durations, init_db
from app.fetcher import fetch_initial_layout, fetch_refresh
from app.parser import parse_schedule
from app.predictor import predict_schedule, update_status_cache


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Track Timing Predictor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/schedule", response_class=HTMLResponse)
async def show_schedule(request: Request, event_id: int = Form(...)):
    """Handle form submission: fetch, parse, predict, and render the full schedule."""
    try:
        jxn_data = await fetch_initial_layout(event_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch event {event_id}: {e}")

    sessions = parse_schedule(jxn_data)
    if not sessions:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule found for event {event_id}. Check that the event ID is correct.",
        )

    now = datetime.now()
    schedule = predict_schedule(event_id, sessions, now=now)

    return templates.TemplateResponse("schedule.html", {
        "request": request,
        "schedule": schedule,
        "event_id": event_id,
        "now": now,
        "refresh_seconds": settings.refresh_interval_seconds,
    })


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

    now = datetime.now()
    schedule = predict_schedule(event_id, sessions, now=now)

    return templates.TemplateResponse("schedule.html", {
        "request": request,
        "schedule": schedule,
        "event_id": event_id,
        "now": now,
        "refresh_seconds": settings.refresh_interval_seconds,
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

    # Record any newly-completed events for learning
    update_status_cache(event_id, sessions, now)

    schedule = predict_schedule(event_id, sessions, now=now)

    return templates.TemplateResponse("_schedule_body.html", {
        "request": request,
        "schedule": schedule,
        "event_id": event_id,
        "now": now,
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
