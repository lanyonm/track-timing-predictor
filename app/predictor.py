from datetime import datetime, time

from app.database import get_learned_duration, record_duration
from app.disciplines import get_default_duration
from app.models import (
    EventStatus,
    Prediction,
    SchedulePrediction,
    Session,
    SessionPrediction,
    TrackEvent,
)

# Disciplines that contribute zero minutes to the cumulative timeline
_ZERO_DURATION_DISCIPLINES = {"end_of_session"}

# In-memory cache tracking event status transitions for learning.
# Key: (event_id, session_id, position)
# Value: {"status": EventStatus, "seen_at": datetime}
_status_cache: dict[tuple[int, int, int], dict] = {}


def _get_duration(discipline: str) -> float:
    """Return learned duration if available, otherwise use the default."""
    learned = get_learned_duration(discipline)
    return learned if learned is not None else get_default_duration(discipline)


def _time_to_minutes(t: time) -> float:
    return t.hour * 60.0 + t.minute + t.second / 60.0


def _add_minutes(t: time, minutes: float) -> time:
    total_seconds = int(t.hour * 3600 + t.minute * 60 + t.second + minutes * 60)
    total_seconds = max(0, total_seconds) % 86400  # wrap at midnight
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return time(h, m, s)


def _compute_delay(
    session: Session,
    durations: list[float],
    completed_count: int,
    now: datetime,
) -> float:
    """
    Estimate how many minutes the session is running behind (positive) or
    ahead (negative) of schedule based on wall-clock time.

    This works by comparing:
      - estimated time through the schedule after completed_count events
      - actual elapsed time since the session's scheduled start (using now)

    The scheduled_start has no date component, so we assume the session is
    occurring today (same date as now).
    """
    est_elapsed = sum(durations[:completed_count])
    sched_start_minutes = _time_to_minutes(session.scheduled_start)
    now_minutes = now.hour * 60.0 + now.minute + now.second / 60.0

    # Handle sessions that started before midnight and now is after
    actual_elapsed = now_minutes - sched_start_minutes
    if actual_elapsed < -60:
        # Likely a midnight rollover; add 24 hours
        actual_elapsed += 1440.0

    # Only apply delay if time has actually passed since session start
    if actual_elapsed <= 0:
        return 0.0

    delay = actual_elapsed - est_elapsed
    # Clamp to reasonable bounds: max 2h behind, 30min ahead
    return max(-30.0, min(delay, 120.0))


def predict_session(
    session: Session,
    now: datetime | None = None,
) -> SessionPrediction:
    """
    Compute predicted start times for all events in a session.

    now: server wall-clock time used to estimate real-time delay.
         If None, no delay adjustment is applied (pre-event mode).
    """
    durations = [_get_duration(e.discipline) for e in session.events]

    # Count leading completed events (events run sequentially)
    completed_count = 0
    for event in session.events:
        if event.status == EventStatus.COMPLETED:
            completed_count += 1
        else:
            break

    # Only compute delay when the session is actively in progress:
    # some events are done and at least one event is still pending.
    has_pending = any(e.status != EventStatus.COMPLETED for e in session.events)
    delay_minutes = 0.0
    if now is not None and completed_count > 0 and has_pending:
        delay_minutes = _compute_delay(session, durations, completed_count, now)

    cumulative = 0.0
    predictions: list[Prediction] = []

    for i, event in enumerate(session.events):
        predicted_start = _add_minutes(session.scheduled_start, cumulative + delay_minutes)
        predictions.append(Prediction(
            event=event,
            predicted_start=predicted_start,
            estimated_duration_minutes=durations[i],
            is_adjusted=(delay_minutes != 0.0),
            cumulative_delay_minutes=delay_minutes,
        ))
        if event.discipline not in _ZERO_DURATION_DISCIPLINES:
            cumulative += durations[i]

    return SessionPrediction(
        session=session,
        event_predictions=predictions,
        observed_delay_minutes=delay_minutes,
    )


def predict_schedule(
    event_id: int,
    sessions: list[Session],
    now: datetime | None = None,
) -> SchedulePrediction:
    session_predictions = [predict_session(s, now=now) for s in sessions]
    return SchedulePrediction(event_id=event_id, sessions=session_predictions)


def update_status_cache(
    event_id: int,
    sessions: list[Session],
    now: datetime,
) -> None:
    """
    Compare current event statuses against the cache.
    When an event transitions UPCOMING -> COMPLETED, record the observed
    duration in the database for future learning.
    """
    for session in sessions:
        for event in session.events:
            key = (event_id, session.session_id, event.position)
            cached = _status_cache.get(key)

            if cached is None:
                _status_cache[key] = {"status": event.status, "seen_at": now}

            elif (
                cached["status"] == EventStatus.UPCOMING
                and event.status == EventStatus.COMPLETED
            ):
                elapsed = (now - cached["seen_at"]).total_seconds() / 60.0
                # Only record plausible durations (30s to 3h)
                if 0.5 <= elapsed <= 180.0:
                    record_duration(
                        event_id=event_id,
                        session_id=session.session_id,
                        event_position=event.position,
                        event_name=event.name,
                        discipline=event.discipline,
                        duration_minutes=elapsed,
                    )
                _status_cache[key] = {"status": event.status, "seen_at": now}

            elif cached["status"] != event.status:
                _status_cache[key] = {"status": event.status, "seen_at": now}
