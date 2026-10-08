from datetime import datetime, time, timedelta

from app.ceremonies import ceremony_duration, forecast_podiums
from app.database import get_learned_duration, record_live_duration
from app.disciplines import (
    BUNCH_RACE_KMH,
    DISTANCE_DISCIPLINES,
    SPRINT_DECIDER_MINUTES,
    SPRINT_DECIDER_RATE,
    get_changeover,
    get_default_duration,
    get_per_heat_duration,
    split_ride,
    sprint_round_pairs,
)
from app.models import (
    Event,
    EventStatus,
    NextRace,
    Prediction,
    RiderEntry,
    RiderListEntry,
    RiderMatch,
    SchedulePrediction,
    Session,
    SessionPrediction,
    normalize_rider_name,
)
from app.rider_list import find_rider, match_events

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

# Generated timestamps parsed from result pages.
# The difference between consecutive timestamps gives the actual inter-event
# slot duration for any discipline, including those without a Finish Time field.
# Key: (competition_id, session_id, position), Value: datetime when result was generated
_generated_times: dict[tuple[int, int, int], datetime] = {}

# Parsed rider entries from start list pages.
# Key: (competition_id, session_id, position), Value: list of RiderEntry
_start_list_riders: dict[tuple[int, int, int], list[RiderEntry]] = {}

# Category column values from combined-age start lists, used to forecast ceremony podiums.
# Only non-empty sets are stored.
# Key: (competition_id, session_id, position), Value: frozenset of categories
_start_list_categories: dict[tuple[int, int, int], frozenset[str]] = {}

# Race distances in km from start-list titles (parser.parse_race_distance_km).
# Key: (competition_id, session_id, position), Value: km
_race_distances: dict[tuple[int, int, int], float] = {}

# Pairs needing a decider in a best-of-3 sprint round, from its shared result page once
# Ride 2 is posted (parser.parse_sprint_deciders).
# Key: (competition_id, round name without "Ride N"), Value: number of deciders
_sprint_deciders: dict[tuple[int, str], int] = {}

# Parsed Rider Lists. The file is immutable for a competition, so entries never expire.
# Key: Rider List relative URL, Value: non-empty list of RiderListEntry
_rider_lists: dict[str, list[RiderListEntry]] = {}

# Rider List URLs whose fetch failed or parsed to 0 rows, so they aren't retried on every poll.
# Key: Rider List relative URL, Value: time.monotonic() after which to retry
_rider_list_retry_at: dict[str, float] = {}
RIDER_LIST_RETRY_SECONDS = 600.0


def record_observed_duration(
    competition_id: int,
    session_id: int,
    position: int,
    finish_time_minutes: float,
    discipline: str,
    event_name: str,
) -> None:
    """
    Store an observed slot duration derived from a result-page Finish Time.
    The total slot = race finish time + discipline changeover.
    Also persists to the learning database.
    """
    slot = finish_time_minutes + get_changeover(discipline)
    _observed_durations[(competition_id, session_id, position)] = slot
    record_live_duration(
        competition_id=competition_id,
        session_id=session_id,
        event_position=position,
        event_name=event_name,
        discipline=discipline,
        duration_minutes=slot,
        source="observed",
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


def latest_live_generated_time(
    competition_id: int,
    sessions: list[Session],
) -> datetime | None:
    """Newest cached Generated timestamp among sessions that are in progress.

    A session is in progress when it has both completed and pending non-special
    events. Finished sessions are ignored so that yesterday's results can't skew
    the venue offset inferred by ``clock.venue_now``.
    """
    latest = None
    for s in sessions:
        real = [e for e in s.events if not e.is_special]
        if not any(e.status == EventStatus.COMPLETED for e in real):
            continue
        if all(e.status == EventStatus.COMPLETED for e in real):
            continue
        for e in s.events:
            t = _generated_times.get((competition_id, s.session_id, e.position))
            if t is not None and (latest is None or t > latest):
                latest = t
    return latest


def get_generated_time(
    competition_id: int,
    session_id: int,
    position: int,
) -> datetime | None:
    """Return the cached Generated timestamp, or None if not yet fetched."""
    return _generated_times.get((competition_id, session_id, position))


def record_start_list_riders(
    competition_id: int,
    session_id: int,
    position: int,
    riders: list[RiderEntry],
) -> None:
    """Store parsed rider entries for an event's start list.

    An empty list never replaces riders already cached, so a transient bad fetch
    (e.g. while upstream regenerates the page) doesn't drop the racer's match.
    """
    key = (competition_id, session_id, position)
    if riders or not _start_list_riders.get(key):
        _start_list_riders[key] = riders


def has_start_list_riders(competition_id: int, session_id: int, position: int) -> bool:
    """Return True if at least one rider has been cached for this event.

    A start list that parsed to 0 riders counts as absent, so it is refetched and
    the event can fall back to Rider List matching.
    """
    return bool(_start_list_riders.get((competition_id, session_id, position)))


def record_start_list_categories(
    competition_id: int,
    session_id: int,
    position: int,
    categories: frozenset[str],
) -> None:
    """Store a start list's Category values; an empty set never replaces cached ones."""
    if categories:
        _start_list_categories[(competition_id, session_id, position)] = categories


def has_start_list_categories(competition_id: int, session_id: int, position: int) -> bool:
    """Return True if Category values have been cached for this event."""
    return (competition_id, session_id, position) in _start_list_categories


def record_race_distance(competition_id: int, session_id: int, position: int, km: float) -> None:
    """Store a race's distance from its start list."""
    _race_distances[(competition_id, session_id, position)] = km


def record_sprint_deciders(competition_id: int, round_name: str, deciders: int) -> None:
    """Store how many pairs in a sprint round need (or rode) a decider."""
    _sprint_deciders[(competition_id, round_name)] = deciders


def _base_estimate(competition_id: int, session_id: int, event: Event, use_learned: bool) -> tuple[float, int | None]:
    """Pre-result duration and heat count: heat count × per-heat + changeover, else the default.

    A points or scratch race with a start-list distance runs at BUNCH_RACE_KMH.
    Without a start list, a sprint round's pairs come from its name (sprint_round_pairs).
    A sprint Ride 3 is ridden only by pairs tied after Ride 2: SPRINT_DECIDER_MINUTES per
    decider once Ride 2 is posted (the count is reported as its heat count), else per
    expected decider (pairs × SPRINT_DECIDER_RATE).
    """
    if event.discipline in DISTANCE_DISCIPLINES and (
        km := _race_distances.get((competition_id, session_id, event.position))
    ):
        return km / BUNCH_RACE_KMH * 60 + get_changeover(event.discipline), None
    hc = get_heat_count(competition_id, session_id, event.position)
    is_sprint = event.discipline == "sprint_match"
    pairs = hc if hc is not None or not is_sprint else sprint_round_pairs(event.name)
    ride = split_ride(event.name) if is_sprint else None
    if ride is not None and ride[1] == 3:
        deciders = _sprint_deciders.get((competition_id, ride[0]))
        if deciders is not None:
            return deciders * SPRINT_DECIDER_MINUTES, deciders
        if pairs is not None:
            return pairs * SPRINT_DECIDER_MINUTES * SPRINT_DECIDER_RATE, None
        full = _get_duration(event.discipline, use_learned) / get_per_heat_duration(event.discipline)
        return full * SPRINT_DECIDER_MINUTES * SPRINT_DECIDER_RATE, None
    if hc is None and pairs is not None:
        return pairs * get_per_heat_duration(event.discipline), None
    if hc is not None:
        return hc * get_per_heat_duration(event.discipline) + get_changeover(event.discipline), hc
    return _get_duration(event.discipline, use_learned), None


def is_start_list_cached(competition_id: int, session_id: int, position: int) -> bool:
    """Return True if a start list has been fetched and parsed for this event, even with 0 riders."""
    return (competition_id, session_id, position) in _start_list_riders


def record_rider_list(url: str, entries: list[RiderListEntry]) -> None:
    """Store a parsed, non-empty Rider List, keyed by its relative URL."""
    _rider_lists[url] = entries
    _rider_list_retry_at.pop(url, None)


def get_rider_list(url: str) -> list[RiderListEntry] | None:
    """Return the cached Rider List for this URL, or None if not yet fetched."""
    return _rider_lists.get(url)


def record_rider_list_failure(url: str, now: float) -> None:
    """Hold off refetching a Rider List that failed or was empty for RIDER_LIST_RETRY_SECONDS."""
    _rider_list_retry_at[url] = now + RIDER_LIST_RETRY_SECONDS


def rider_list_retry_pending(url: str, now: float) -> bool:
    """True while a failed or empty Rider List is inside its retry interval."""
    return now < _rider_list_retry_at.get(url, 0.0)


def get_rider_match(
    competition_id: int,
    session_id: int,
    position: int,
    user_tokens: frozenset[str],
    event_start: datetime | None,
    discipline: str,
) -> RiderMatch | None:
    """
    Match pre-tokenized racer name tokens against cached start list riders.

    Expects a frozenset of lowercased, normalized tokens (computed once via
    _normalize_rider_name) for case-insensitive, order-independent matching.
    """
    key = (competition_id, session_id, position)
    riders = _start_list_riders.get(key)
    if not riders:
        return None

    if not user_tokens:
        return None

    for rider in riders:
        if user_tokens == rider.normalized_tokens:
            hc = get_heat_count(competition_id, session_id, position)
            if hc is None:
                hc = 1
            heat_predicted_start = None
            if event_start is not None:
                phd = get_per_heat_duration(discipline)
                heat_predicted_start = event_start + timedelta(minutes=(rider.heat - 1) * phd)
            return RiderMatch(
                heat=rider.heat,
                heat_count=hc,
                heat_predicted_start=heat_predicted_start,
                team_name=rider.team_name,
            )

    return None


def get_observed_duration(competition_id: int, session_id: int, position: int) -> float | None:
    """Return the cached observed slot duration in minutes, or None if not yet recorded."""
    return _observed_durations.get((competition_id, session_id, position))


def generated_gap_duration(
    prev_generated: datetime | None,
    curr_generated: datetime | None,
    expected: float,
) -> float | None:
    """Duration of an event from its own and the previous event's Generated timestamps.

    Generated marks when an event's results were published (roughly its end), so the
    gap between consecutive timestamps belongs to the later event. Returns None when a
    timestamp is missing or the gap is outside [0.5x, 2.0x] of ``expected``. Shared by
    the live predictor and ``tools.extract_competition``.
    """
    if prev_generated is None or curr_generated is None:
        return None
    mins = (curr_generated - prev_generated).total_seconds() / 60.0
    if mins <= 0 or not (0.5 * expected <= mins <= 2.0 * expected):
        return None
    return mins


def _get_duration(discipline: str, use_learned: bool = False) -> float:
    """Return learned duration if available and enabled, otherwise use the default."""
    if use_learned:
        learned = get_learned_duration(discipline)
        if learned is not None:
            return learned
    return get_default_duration(discipline)


def _on_day_of(now: datetime | None, t: time) -> datetime | None:
    """Combine a predicted start time with now's date; None when there is no wall clock."""
    if now is None:
        return None
    return now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)


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
    racer_name: str | None = None,
    use_learned: bool = False,
    rider_list_matches: dict[tuple[int, int], RiderMatch] | None = None,
    ceremony_podiums: dict[tuple[int, int], int] | None = None,
) -> SessionPrediction:
    """
    Compute predicted start times for all events in a session.

    Duration source priority (most to least accurate):
      1. Observed: result-page Finish Time + changeover
      2. Generated: difference between consecutive result-page Generated timestamps
      3. Heat count: start-list heat count × per-heat duration + changeover
         (a sprint Ride 3 uses its decider count, see _base_estimate)
      4. Default: learned average or DEFAULT_DURATIONS fallback
    A medal ceremony with forecast podiums uses ceremony_duration instead; its own
    Generated timestamp marks when it starts, so the gap before it is never used.

    now: server wall-clock time used to estimate real-time delay.
         If None, no delay adjustment is applied (pre-event mode).
    racer_name: optional racer name for rider matching.
    rider_list_matches: the racer's Rider List matches from rider_list.match_events,
            keyed by (session_id, position); used for events without start-list riders.
    ceremony_podiums: forecast podiums per medal ceremony from ceremonies.forecast_podiums,
            keyed by (session_id, position).
    """
    # Pre-tokenize racer name once for the entire session (avoids re-normalizing per event)
    user_tokens = normalize_rider_name(racer_name) if racer_name and racer_name.strip() else None

    durations: list[float] = []
    is_observed_list: list[bool] = []
    heat_count_list: list[int | None] = []
    podium_list: list[int | None] = []

    # Pre-compute generated-time derived durations.
    # Duration of event[i] = generated_time[i] - generated_time[i-1], when both
    # have a cached Generated timestamp and the gap is plausible.
    #
    # Plausibility is validated relative to the expected slot duration.  At track
    # cycling championships, result pages for events that share a session block
    # (e.g. keirin finals) are sometimes uploaded in a different order from the
    # schedule, producing consecutive-timestamp gaps that are far too large or too
    # small.  Accepting those blindly would corrupt downstream predictions.  A gap
    # within [0.5×, 2.0×] the discipline's expected duration is considered reliable.
    events = session.events
    gen_durations: dict[int, float] = {}
    for i in range(1, len(events)):
        if events[i].discipline == "ceremony":
            continue
        t0 = _generated_times.get((competition_id, session.session_id, events[i - 1].position))
        t1 = _generated_times.get((competition_id, session.session_id, events[i].position))
        # Expected duration: the pre-result estimate with the STATIC default
        # (not learned averages).  Learned data may itself be corrupted by bad
        # gen-duration observations from earlier runs, so it must not influence
        # the bounds used to validate new observations.
        expected, _ = _base_estimate(competition_id, session.session_id, events[i], use_learned=False)
        mins = generated_gap_duration(t0, t1, expected)
        if mins is not None:
            gen_durations[i] = mins

    for i, e in enumerate(events):
        observed = get_observed_duration(competition_id, session.session_id, e.position)
        podiums = (ceremony_podiums or {}).get((session.session_id, e.position))
        podium_list.append(podiums)
        if podiums is not None:
            durations.append(ceremony_duration(podiums))
            is_observed_list.append(False)
            heat_count_list.append(None)
        elif observed is not None:
            durations.append(observed)
            is_observed_list.append(True)
            heat_count_list.append(None)
        elif i in gen_durations:
            durations.append(gen_durations[i])
            is_observed_list.append(True)
            heat_count_list.append(None)
        else:
            dur, hc = _base_estimate(competition_id, session.session_id, e, use_learned)
            durations.append(dur)
            is_observed_list.append(False)
            heat_count_list.append(hc)

    # Count leading completed events (events run sequentially)
    completed_count = 0
    for event in session.events:
        if event.status == EventStatus.COMPLETED:
            completed_count += 1
        else:
            break

    # Only compute delay when the session is actively in progress:
    # some events are done and at least one race is still pending. Special
    # events don't count, so a finished session whose End of Session row is
    # still NOT_READY isn't treated as live (matches SessionPrediction.is_complete).
    has_pending = any(e.status != EventStatus.COMPLETED for e in session.events if not e.is_special)
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
    has_racer_match = False
    has_pending_racer_match = False
    events_without_start_lists = 0

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
                hc = heat_count_list[i]
                active_heat = min(next_heat, hc) if hc else next_heat
            elif hc := heat_count_list[i]:
                # Time-based fallback: elapsed since scheduled event start ÷ per-heat duration.
                # Uses scheduled (not delay-adjusted) start so prior-event overrun doesn't
                # incorrectly advance the heat counter.
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

        # Rider matching
        rider_match = None
        if user_tokens and not event.is_special:
            if not has_start_list_riders(competition_id, session.session_id, event.position):
                events_without_start_lists += 1
                if rider_list_matches:
                    rider_match = rider_list_matches.get((session.session_id, event.position))
            else:
                rider_match = get_rider_match(
                    competition_id,
                    session.session_id,
                    event.position,
                    user_tokens,
                    _on_day_of(now, predicted_start),
                    event.discipline,
                )
            if rider_match:
                has_racer_match = True
                if event.status != EventStatus.COMPLETED:
                    has_pending_racer_match = True

        predictions.append(
            Prediction(
                event=event,
                predicted_start=predicted_start,
                estimated_duration_minutes=durations[i],
                is_adjusted=(applied_delay != 0.0),
                cumulative_delay_minutes=applied_delay,
                is_observed=is_observed_list[i],
                heat_count=heat_count_list[i],
                podium_count=podium_list[i],
                is_active=is_active,
                active_heat=active_heat,
                rider_match=rider_match,
            )
        )
        if event.discipline not in _ZERO_DURATION_DISCIPLINES:
            cumulative += durations[i]

    return SessionPrediction(
        session=session,
        event_predictions=predictions,
        observed_delay_minutes=delay_minutes,
        has_racer_match=has_racer_match,
        has_pending_racer_match=has_pending_racer_match,
        events_without_start_lists=events_without_start_lists,
    )


def _build_next_race(pred: Prediction, match: RiderMatch, now: datetime | None) -> NextRace:
    """Build a NextRace from a matched Prediction.

    Start-list matches use the racer's heat start. Rider List matches have no heat,
    so they use the event's predicted start.
    """
    predicted_start = match.heat_predicted_start
    if match.source == "rider_list":
        predicted_start = _on_day_of(now, pred.predicted_start)
    return NextRace(
        event_name=pred.event.name,
        heat=match.heat,
        heat_count=match.heat_count,
        predicted_start=predicted_start,
        is_active=pred.is_active,
        tentative=match.tentative,
        parallel_qualifier=match.parallel_qualifier,
    )


def predict_schedule(
    competition_id: int,
    sessions: list[Session],
    now: datetime | None = None,
    racer_name: str | None = None,
    use_learned: bool = False,
    rider_list: list[RiderListEntry] | None = None,
) -> SchedulePrediction:
    rider_entry = None
    rider_list_matches = None
    if rider_list and racer_name and racer_name.strip():
        rider_entry = find_rider(rider_list, normalize_rider_name(racer_name))
    if rider_entry is not None:
        rider_list_matches = match_events(rider_entry, sessions)
    categories = {(s, p): c for (comp, s, p), c in _start_list_categories.items() if comp == competition_id}
    ceremony_podiums = forecast_podiums(sessions, categories, rider_list)

    session_predictions = []
    total_events_without_start_lists = 0
    total_events = 0
    match_count = 0
    tentative_match_count = 0
    rider_list_match_count = 0
    active_candidate: Prediction | None = None
    upcoming_candidate: Prediction | None = None

    for s in sessions:
        sp = predict_session(
            competition_id,
            s,
            now=now,
            racer_name=racer_name,
            use_learned=use_learned,
            rider_list_matches=rider_list_matches,
            ceremony_podiums=ceremony_podiums,
        )
        session_predictions.append(sp)
        total_events_without_start_lists += sp.events_without_start_lists
        for e in s.events:
            if not e.is_special:
                total_events += 1
        for pred in sp.event_predictions:
            if pred.rider_match:
                match_count += 1
                if pred.rider_match.tentative:
                    tentative_match_count += 1
                if pred.rider_match.source == "rider_list":
                    rider_list_match_count += 1
                # Track next-race candidates: active takes priority over upcoming
                if pred.is_active and active_candidate is None:
                    active_candidate = pred
                elif pred.event.status != EventStatus.COMPLETED and upcoming_candidate is None:
                    upcoming_candidate = pred

    # Active match takes priority; fall back to first upcoming match.
    best = active_candidate or upcoming_candidate
    next_race = _build_next_race(best, best.rider_match, now) if best and best.rider_match else None

    return SchedulePrediction(
        competition_id=competition_id,
        sessions=session_predictions,
        racer_name=racer_name,
        match_count=match_count,
        events_without_start_lists=total_events_without_start_lists,
        total_events=total_events,
        next_race=next_race,
        tentative_match_count=tentative_match_count,
        rider_list_match_count=rider_list_match_count,
        rider_list_entry=rider_entry if rider_list_match_count else None,
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

            elif cached["status"] == EventStatus.UPCOMING and event.status == EventStatus.COMPLETED:
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
                    record_live_duration(
                        competition_id=competition_id,
                        session_id=session.session_id,
                        event_position=event.position,
                        event_name=event.name,
                        discipline=event.discipline,
                        duration_minutes=elapsed,
                        source="wall_clock",
                    )
                _status_cache[key] = {"status": event.status, "seen_at": now}

                # Signal caller to fetch result page if URL is available.
                if event.result_url:
                    newly_completed.append((competition_id, session.session_id, event.position, event.result_url))

            elif cached["status"] != event.status:
                _status_cache[key] = {"status": event.status, "seen_at": now}

    return newly_completed
