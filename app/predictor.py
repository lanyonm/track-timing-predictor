from datetime import datetime, time

from app.database import get_learned_duration, record_duration
from app.disciplines import get_changeover, get_default_duration, get_per_heat_duration
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

# Observed slot durations derived from result-page Finish Times.
# These override estimates for completed events in the prediction timeline.
# Key: (event_id, session_id, position), Value: duration in minutes
_observed_durations: dict[tuple[int, int, int], float] = {}

# Heat counts derived from start-list pages.
# Used to compute duration as heat_count × per_heat_duration + changeover.
# Key: (event_id, session_id, position), Value: number of heats
_heat_counts: dict[tuple[int, int, int], int] = {}


def record_observed_duration(
    event_id: int,
    session_id: int,
    position: int,
    finish_time_minutes: float,
    discipline: str,
) -> None:
    """
    Store an observed slot duration derived from a result-page Finish Time.
    The total slot = race finish time + discipline changeover.
    Also persists to the learning database.
    """
    slot = finish_time_minutes + get_changeover(discipline)
    _observed_durations[(event_id, session_id, position)] = slot
    record_duration(
        event_id=event_id,
        session_id=session_id,
        event_position=position,
        event_name=discipline,
        discipline=discipline,
        duration_minutes=slot,
    )


def record_heat_count(
    event_id: int,
    session_id: int,
    position: int,
    count: int,
) -> None:
    """Store the number of heats for an event, derived from its start list page."""
    _heat_counts[(event_id, session_id, position)] = count


def get_heat_count(event_id: int, session_id: int, position: int) -> int | None:
    """Return cached heat count, or None if not yet fetched."""
    return _heat_counts.get((event_id, session_id, position))


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

    Only applies delay when we are inside the session window — i.e., when
    actual elapsed time is less than the estimated total session duration plus
    a one-hour buffer. Once we appear to be past the session's estimated end,
    we return 0 so that post-event predictions show scheduled times rather than
    an inflated delay caused by viewing old results hours after they happened.
    """
    est_elapsed = sum(durations[:completed_count])
    total_est = sum(durations)
    sched_start_minutes = _time_to_minutes(session.scheduled_start)
    now_minutes = now.hour * 60.0 + now.minute + now.second / 60.0

    # Handle sessions that started before midnight and now is after
    actual_elapsed = now_minutes - sched_start_minutes
    if actual_elapsed < -60:
        actual_elapsed += 1440.0

    # No delay before the session starts or after its estimated window closes.
    # A 60-minute buffer past total_est allows for genuine long-running sessions.
    if actual_elapsed <= 0 or actual_elapsed > total_est + 60:
        return 0.0

    delay = actual_elapsed - est_elapsed
    # Clamp to reasonable bounds: max 2h behind, 30min ahead
    return max(-30.0, min(delay, 120.0))


def predict_session(
    event_id: int,
    session: Session,
    now: datetime | None = None,
) -> SessionPrediction:
    """
    Compute predicted start times for all events in a session.

    Duration source priority (most to least accurate):
      1. Observed: result-page Finish Time + changeover
      2. Heat count: start-list heat count × per-heat duration + changeover
      3. Default: learned average or DEFAULT_DURATIONS fallback

    now: server wall-clock time used to estimate real-time delay.
         If None, no delay adjustment is applied (pre-event mode).
    """
    durations: list[float] = []
    is_observed_list: list[bool] = []
    heat_count_list: list[int | None] = []

    for e in session.events:
        key = (event_id, session.session_id, e.position)
        if key in _observed_durations:
            durations.append(_observed_durations[key])
            is_observed_list.append(True)
            heat_count_list.append(None)
        elif key in _heat_counts:
            hc = _heat_counts[key]
            dur = hc * get_per_heat_duration(e.discipline) + get_changeover(e.discipline)
            durations.append(dur)
            is_observed_list.append(False)
            heat_count_list.append(hc)
        else:
            durations.append(_get_duration(e.discipline))
            is_observed_list.append(False)
            heat_count_list.append(None)

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
            is_observed=is_observed_list[i],
            heat_count=heat_count_list[i],
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
    session_predictions = [predict_session(event_id, s, now=now) for s in sessions]
    return SchedulePrediction(event_id=event_id, sessions=session_predictions)


def update_status_cache(
    event_id: int,
    sessions: list[Session],
    now: datetime,
) -> list[tuple[int, int, int, str]]:
    """
    Compare current event statuses against the cache.

    - When an event transitions UPCOMING -> COMPLETED, records the wall-clock
      elapsed time to the learning database (fallback when no Finish Time).
    - Returns a list of (event_id, session_id, position, result_url) for
      newly-completed events that have a result URL, so the caller can fetch
      result pages to obtain precise Finish Times.
    """
    newly_completed: list[tuple[int, int, int, str]] = []

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
                # Wall-clock fallback: record elapsed time for disciplines
                # that don't have a result-page Finish Time.
                elapsed = (now - cached["seen_at"]).total_seconds() / 60.0
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

                # Signal caller to fetch result page if URL is available.
                if event.result_url:
                    newly_completed.append(
                        (event_id, session.session_id, event.position, event.result_url)
                    )

            elif cached["status"] != event.status:
                _status_cache[key] = {"status": event.status, "seen_at": now}

    return newly_completed
