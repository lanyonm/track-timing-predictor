from datetime import datetime, time

from app.database import get_learned_duration, record_duration
from app.disciplines import get_changeover, get_default_duration, get_per_heat_duration
from app.models import (
    Event,
    EventStatus,
    Prediction,
    RiderEntry,
    RiderMatch,
    SchedulePrediction,
    Session,
    SessionPrediction,
)

# Disciplines that contribute zero minutes to the cumulative timeline
_ZERO_DURATION_DISCIPLINES = {"end_of_session"}

# In-memory cache tracking event status transitions for learning.
# Key: (competition_id, session_id, position)
# Value: {"status": EventStatus, "seen_at": datetime}
_status_cache: dict[tuple[int, int, int], dict] = {}

# Observed slot durations derived from result-page Finish Times.
# These override estimates for completed events in the prediction timeline.
# Key: (competition_id, session_id, position), Value: duration in minutes
_observed_durations: dict[tuple[int, int, int], float] = {}

# Heat counts derived from start-list pages.
# Used to compute duration as heat_count × per_heat_duration + changeover.
# Key: (competition_id, session_id, position), Value: number of heats
_heat_counts: dict[tuple[int, int, int], int] = {}

# Current heat number derived from the live results page.
# Updated on every refresh while the event is active.
# Key: (competition_id, session_id, position), Value: current heat number (1-based)
_live_heats: dict[tuple[int, int, int], int] = {}

# Parsed rider entries from start-list pages.
# Used to match a racer name against the riders in each event.
# Key: (competition_id, session_id, position), Value: list of RiderEntry
_start_list_riders: dict[tuple[int, int, int], list[RiderEntry]] = {}

# Generated timestamps parsed from result pages.
# The difference between consecutive timestamps gives the actual inter-event
# slot duration for any discipline, including those without a Finish Time field.
# Key: (competition_id, session_id, position), Value: datetime when result was generated
_generated_times: dict[tuple[int, int, int], datetime] = {}


def record_observed_duration(
    competition_id: int,
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
    _observed_durations[(competition_id, session_id, position)] = slot
    record_duration(
        competition_id=competition_id,
        session_id=session_id,
        event_position=position,
        event_name=discipline,
        discipline=discipline,
        duration_minutes=slot,
    )


def record_heat_count(
    competition_id: int,
    session_id: int,
    position: int,
    count: int,
) -> None:
    """Store the number of heats for an event, derived from its start list page."""
    _heat_counts[(competition_id, session_id, position)] = count


def get_heat_count(competition_id: int, session_id: int, position: int) -> int | None:
    """Return cached heat count, or None if not yet fetched."""
    return _heat_counts.get((competition_id, session_id, position))


def record_live_heat(competition_id: int, session_id: int, position: int, heat: int) -> None:
    """Store the current heat number parsed from the live results page."""
    _live_heats[(competition_id, session_id, position)] = heat


def get_live_heat(competition_id: int, session_id: int, position: int) -> int | None:
    """Return the most recently parsed live heat number, or None if not available."""
    return _live_heats.get((competition_id, session_id, position))


def record_generated_time(
    competition_id: int,
    session_id: int,
    position: int,
    generated_at: datetime,
) -> None:
    """Store the result-page Generated timestamp for a completed event."""
    _generated_times[(competition_id, session_id, position)] = generated_at


def get_generated_time(
    competition_id: int,
    session_id: int,
    position: int,
) -> datetime | None:
    """Return the cached Generated timestamp, or None if not yet fetched."""
    return _generated_times.get((competition_id, session_id, position))


def get_observed_duration(competition_id: int, session_id: int, position: int) -> float | None:
    """Return the cached observed slot duration in minutes, or None if not yet recorded."""
    return _observed_durations.get((competition_id, session_id, position))


def record_start_list_riders(
    competition_id: int,
    session_id: int,
    position: int,
    riders: list[RiderEntry],
) -> None:
    """Store parsed rider entries for an event's start list."""
    _start_list_riders[(competition_id, session_id, position)] = riders


def get_start_list_riders(
    competition_id: int,
    session_id: int,
    position: int,
) -> list[RiderEntry]:
    """Return cached rider entries, or an empty list if not yet fetched."""
    return _start_list_riders.get((competition_id, session_id, position), [])


def _normalize_name(name: str) -> frozenset[str]:
    """Split on whitespace, lowercase each token, return frozenset for order-independent comparison."""
    return frozenset(name.lower().split())


def get_rider_match(
    competition_id: int,
    session_id: int,
    position: int,
    racer_name: str,
    event_predicted_start: time,
    discipline: str,
) -> RiderMatch | None:
    """
    Match a racer name against cached start list riders for an event.
    Returns RiderMatch with heat info and per-heat predicted start, or None.
    """
    riders = get_start_list_riders(competition_id, session_id, position)
    if not riders:
        return None
    user_tokens = _normalize_name(racer_name)
    for rider in riders:
        if user_tokens == rider.normalized_tokens:
            hc = get_heat_count(competition_id, session_id, position) or 1
            phd = get_per_heat_duration(discipline)
            heat_start = _add_minutes(event_predicted_start, (rider.heat - 1) * phd) if hc > 1 else None
            return RiderMatch(heat=rider.heat, heat_count=hc, heat_predicted_start=heat_start)
    return None


def _get_duration(discipline: str, use_learned: bool = True) -> float:
    """Return learned duration if available and enabled, otherwise use the default."""
    if use_learned:
        learned = get_learned_duration(discipline)
        if learned is not None:
            return learned
    return get_default_duration(discipline)


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
    competition_id: int,
    session: Session,
    now: datetime | None = None,
    use_learned: bool = True,
    racer_name: str | None = None,
) -> SessionPrediction:
    """
    Compute predicted start times for all events in a session.

    Duration source priority (most to least accurate):
      1. Observed: result-page Finish Time + changeover
      2. Generated: difference between consecutive result-page Generated timestamps
      3. Heat count: start-list heat count × per-heat duration + changeover
      4. Default: learned average or DEFAULT_DURATIONS fallback

    now: server wall-clock time used to estimate real-time delay.
         If None, no delay adjustment is applied (pre-event mode).
    """
    durations: list[float] = []
    is_observed_list: list[bool] = []
    heat_count_list: list[int | None] = []

    # Pre-compute generated-time derived durations.
    # Duration of event[i] = generated_time[i+1] - generated_time[i], when both
    # neighbours have a cached Generated timestamp and the gap is plausible.
    #
    # Plausibility is validated relative to the expected slot duration.  At track
    # cycling championships, result pages for events that share a session block
    # (e.g. keirin finals) are sometimes uploaded in a different order from the
    # schedule, producing consecutive-timestamp gaps that are far too large or too
    # small.  Accepting those blindly would corrupt downstream predictions.  A gap
    # within [0.5×, 2.0×] the discipline's expected duration is considered reliable.
    events = session.events
    gen_durations: dict[int, float] = {}
    for i in range(len(events) - 1):
        t0 = _generated_times.get((competition_id, session.session_id, events[i].position))
        t1 = _generated_times.get((competition_id, session.session_id, events[i + 1].position))
        if t0 is not None and t1 is not None:
            mins = (t1 - t0).total_seconds() / 60.0
            # Expected duration: use heat-count estimate if available, else the
            # STATIC default (not learned averages).  Learned data may itself be
            # corrupted by bad gen-duration observations from earlier runs, so it
            # must not influence the bounds used to validate new observations.
            key_i = (competition_id, session.session_id, events[i].position)
            hc_i = _heat_counts.get(key_i)
            if hc_i is not None:
                expected = hc_i * get_per_heat_duration(events[i].discipline) + get_changeover(events[i].discipline)
            else:
                expected = get_default_duration(events[i].discipline)
            if 0.5 * expected <= mins <= 2.0 * expected:
                gen_durations[i] = mins

    for i, e in enumerate(events):
        observed = get_observed_duration(competition_id, session.session_id, e.position)
        hc = get_heat_count(competition_id, session.session_id, e.position)
        if observed is not None:
            durations.append(observed)
            is_observed_list.append(True)
            heat_count_list.append(None)
        elif i in gen_durations:
            durations.append(gen_durations[i])
            is_observed_list.append(True)
            heat_count_list.append(None)
        elif hc is not None:
            dur = hc * get_per_heat_duration(e.discipline) + get_changeover(e.discipline)
            durations.append(dur)
            is_observed_list.append(False)
            heat_count_list.append(hc)
        else:
            durations.append(_get_duration(e.discipline, use_learned))
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

    # The active event is the first non-COMPLETED event in an in-progress session.
    # Requires now so we only flag "active" when the session is being viewed live.
    active_index = completed_count if (now is not None and completed_count > 0 and has_pending) else -1

    # Fallback: if no active event was found via completed-event count (e.g. the
    # running event is the very first in the session), the presence of a live_url
    # (the LIVE button on the schedule page) definitively identifies the active event.
    if active_index == -1 and now is not None:
        for idx, ev in enumerate(session.events):
            if ev.live_url:
                active_index = idx
                break

    cumulative = 0.0
    predictions: list[Prediction] = []

    for i, event in enumerate(session.events):
        # Only shift upcoming/active events by the current delay.
        # Completed events keep their estimated historical start times.
        applied_delay = delay_minutes if i >= completed_count else 0.0
        predicted_start = _add_minutes(session.scheduled_start, cumulative + applied_delay)
        is_active = i == active_index

        # For an active multi-heat event, determine which heat is currently running.
        # Priority: (1) live results page heat, (2) time-based fallback estimate.
        active_heat: int | None = None
        if is_active and now is not None:
            live_heat = get_live_heat(competition_id, session.session_id, event.position)
            if live_heat is not None:
                # live_heat = count of finished heats; the running heat is the next one.
                next_heat = live_heat + 1
                if heat_count_list[i] is not None:
                    active_heat = min(next_heat, heat_count_list[i])
                else:
                    active_heat = next_heat
            elif heat_count_list[i] is not None:
                # Time-based fallback: elapsed since scheduled event start ÷ per-heat duration.
                # Uses scheduled (not delay-adjusted) start so prior-event overrun doesn't
                # incorrectly advance the heat counter.
                hc = heat_count_list[i]
                phd = get_per_heat_duration(event.discipline)
                sched_start_minutes = _time_to_minutes(session.scheduled_start)
                now_minutes = now.hour * 60.0 + now.minute + now.second / 60.0
                actual_elapsed = now_minutes - sched_start_minutes
                if actual_elapsed < -60:
                    actual_elapsed += 1440.0  # midnight wrap
                est_before_active = sum(durations[:active_index])
                elapsed_in_active = max(0.0, actual_elapsed - est_before_active)
                if phd > 0:
                    active_heat = max(1, min(hc, int(elapsed_in_active / phd) + 1))

        # Rider matching: check if racer name matches a rider in this event's start list.
        rider_match = None
        if racer_name and not event.is_special:
            rider_match = get_rider_match(
                competition_id, session.session_id, event.position,
                racer_name, predicted_start, event.discipline,
            )

        predictions.append(Prediction(
            event=event,
            predicted_start=predicted_start,
            estimated_duration_minutes=durations[i],
            is_adjusted=(applied_delay != 0.0),
            cumulative_delay_minutes=applied_delay,
            is_observed=is_observed_list[i],
            heat_count=heat_count_list[i],
            is_active=is_active,
            active_heat=active_heat,
            rider_match=rider_match,
        ))
        if event.discipline not in _ZERO_DURATION_DISCIPLINES:
            cumulative += durations[i]

    has_racer_match = any(p.rider_match is not None for p in predictions)
    return SessionPrediction(
        session=session,
        event_predictions=predictions,
        observed_delay_minutes=delay_minutes,
        has_racer_match=has_racer_match,
    )


def predict_schedule(
    competition_id: int,
    sessions: list[Session],
    now: datetime | None = None,
    use_learned: bool = True,
    racer_name: str | None = None,
) -> SchedulePrediction:
    session_predictions = [
        predict_session(competition_id, s, now=now, use_learned=use_learned, racer_name=racer_name)
        for s in sessions
    ]

    # Count matches and events without start lists for FR-010 messaging.
    match_count = 0
    events_without_start_lists = 0
    total_events = 0
    for sp in session_predictions:
        for p in sp.event_predictions:
            if p.event.is_special:
                continue
            total_events += 1
            if p.rider_match is not None:
                match_count += 1
            if not get_start_list_riders(competition_id, sp.session.session_id, p.event.position):
                events_without_start_lists += 1

    return SchedulePrediction(
        competition_id=competition_id,
        sessions=session_predictions,
        racer_name=racer_name,
        match_count=match_count,
        events_without_start_lists=events_without_start_lists,
        total_events=total_events,
    )


def update_status_cache(
    competition_id: int,
    sessions: list[Session],
    now: datetime,
) -> list[tuple[int, int, int, str]]:
    """
    Compare current event statuses against the cache.

    - When an event transitions UPCOMING -> COMPLETED, records the wall-clock
      elapsed time to the learning database (fallback when no Finish Time).
    - Returns a list of (competition_id, session_id, position, result_url) for
      newly-completed events that have a result URL, so the caller can fetch
      result pages to obtain precise Finish Times.
    """
    newly_completed: list[tuple[int, int, int, str]] = []

    for session in sessions:
        for event in session.events:
            key = (competition_id, session.session_id, event.position)
            cached = _status_cache.get(key)

            if cached is None:
                _status_cache[key] = {"status": event.status, "seen_at": now}

            elif (
                cached["status"] == EventStatus.UPCOMING
                and event.status == EventStatus.COMPLETED
            ):
                # Wall-clock fallback: record elapsed time for disciplines
                # that don't have a result-page Finish Time.
                # Upper bound is 3× the static default duration for the discipline.
                # The broad 180-min cap is too loose: start lists for some events
                # (e.g. keirin rounds) are published 20-30 min before the race
                # starts, making the UPCOMING→COMPLETED elapsed time far exceed
                # the actual race duration.  Using 3× default rejects those
                # inflated values while still accepting genuinely long events
                # (e.g. a 3-heat keirin round: default 6.5 min × 3 = 19.5 min,
                # which comfortably covers an actual ~17-min round).
                elapsed = (now - cached["seen_at"]).total_seconds() / 60.0
                max_elapsed = 3.0 * get_default_duration(event.discipline)
                if 0.5 <= elapsed <= max_elapsed:
                    record_duration(
                        competition_id=competition_id,
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
                        (competition_id, session.session_id, event.position, event.result_url)
                    )

            elif cached["status"] != event.status:
                _status_cache[key] = {"status": event.status, "seen_at": now}

    return newly_completed
