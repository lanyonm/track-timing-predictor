from collections.abc import Iterable, Mapping
from datetime import datetime, time, timedelta
from statistics import median
from typing import NamedTuple

from app.ceremonies import ceremony_duration, forecast_podiums
from app.database import LiveSource, get_learned_duration, record_live_duration
from app.disciplines import (
    DISTANCE_DISCIPLINES,
    FINISH_TIME_DISCIPLINES,
    LIVE_BUNCH_CHANGEOVER_MINUTES,
    MAX_CHANGEOVER_MINUTES,
    MEDAL_FINAL_DISCIPLINES,
    MIN_CHANGEOVER_SAMPLES,
    SPRINT_DECIDER_MINUTES,
    SPRINT_DECIDER_RATE,
    bunch_race_kmh,
    get_changeover,
    get_default_duration,
    get_per_heat_duration,
    keirin_round_heats,
    qualifying_name,
    split_ride,
    sprint_round_pairs,
)
from app.models import (
    DistanceBasis,
    Event,
    EventStatus,
    FieldSize,
    HeatBasis,
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
from app.rider_list import AgeBand, estimate_heats, event_band, find_rider, match_events
from app.supplements import heats_from_fields, load_supplement, scheduled_distances

# Disciplines that contribute zero minutes to the cumulative timeline
_ZERO_DURATION_DISCIPLINES = {"end_of_session"}

# Live delay bounds (minutes): at most 30 ahead, 120 behind.
MIN_DELAY_MINUTES = -30.0
MAX_DELAY_MINUTES = 120.0
# An active event that has run past its estimate is assumed to need at least this much longer.
ACTIVE_MIN_REMAINING_MINUTES = 2.0
# How far a Generated timestamp may sit ahead of venue "now" before it's treated as a clock mismatch.
GENERATED_AHEAD_TOLERANCE_MINUTES = 5.0

# In-memory cache tracking event status transitions for learning.
# Key: (competition_id, session_id, position)
# Value: {"status": EventStatus, "seen_at": datetime}
_status_cache: dict[tuple[int, int, int], dict] = {}

# Result-page Finish Times (race time only, no changeover).
# Finish Time + changeover overrides estimates for completed events in the prediction timeline.
# Key: (competition_id, session_id, position), Value: minutes
_finish_times: dict[tuple[int, int, int], float] = {}

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

# Partial decider counts while a best-of-3 round's Ride 2 is ridden, from its live timing or
# result page (parser.parse_sprint_decider_range): (pairs known to need a decider, pairs yet
# to ride Ride 2). The most progressed reading is kept.
# Key: (competition_id, round name without "Ride N"), Value: (known, open)
_sprint_decider_ranges: dict[tuple[int, str], tuple[int, int]] = {}

# Rides (0-3) every pair of a best-of-3 sprint round has finished, from its shared result
# page (parser.parse_sprint_rides_done). Upstream shows every ride as having results once
# Ride 1 does, so apply_sprint_ride_status uses this instead.
# Key: (competition_id, round name without "Ride N"), Value: rides done
_sprint_rides_done: dict[tuple[int, str], int] = {}

# The caches above keyed by (competition_id, session_id, position). Upstream can add or remove
# rows mid-session (a Ride 3 nobody needs is deleted once Ride 2 ends), which shifts every later
# event's position, so reconcile_positions moves their entries along with the events.
_POSITION_CACHES: tuple[dict, ...] = (
    _status_cache,
    _finish_times,
    _heat_counts,
    _live_heats,
    _generated_times,
    _start_list_riders,
    _start_list_categories,
    _race_distances,
)

# Each session's events as last seen, as (position, name) pairs.
# Key: (competition_id, session_id)
_session_layouts: dict[tuple[int, int], tuple[tuple[int, str], ...]] = {}

# Parsed Rider Lists. The file is immutable for a competition, so entries never expire.
# Key: Rider List relative URL, Value: non-empty list of RiderListEntry
_rider_lists: dict[str, list[RiderListEntry]] = {}

# Rider List URLs whose fetch failed or parsed to 0 rows, so they aren't retried on every poll.
# Key: Rider List relative URL, Value: time.monotonic() after which to retry
_rider_list_retry_at: dict[str, float] = {}
RIDER_LIST_RETRY_SECONDS = 600.0


class LiveDuration(NamedTuple):
    """A duration the live app measured, to be written by save_live_durations."""

    competition_id: int
    session_id: int
    event_position: int
    event_name: str
    discipline: str
    duration_minutes: float
    source: LiveSource


def record_observed_duration(
    competition_id: int,
    session_id: int,
    position: int,
    finish_time_minutes: float,
    discipline: str,
    event_name: str,
) -> LiveDuration:
    """
    Store a result-page Finish Time and return the learning record for it.

    The prediction adds the competition's calibrated changeover (bunch_changeover).
    The learning record is Finish Time + the static changeover, so learned
    averages stay comparable across competitions. The caller persists it with
    save_live_durations, off the event loop.
    """
    _finish_times[(competition_id, session_id, position)] = finish_time_minutes
    return LiveDuration(
        competition_id=competition_id,
        session_id=session_id,
        event_position=position,
        event_name=event_name,
        discipline=discipline,
        duration_minutes=finish_time_minutes + get_changeover(discipline),
        source="observed",
    )


def save_live_durations(durations: Iterable[LiveDuration]) -> None:
    """Write live learning records to the database. Blocking: run it in a worker thread."""
    for d in durations:
        record_live_duration(**d._asdict())


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
    """Store a Generated timestamp for a completed event, keeping the earliest.

    Upstream regenerates result and audit pages after corrections, which moves their
    Generated timestamp later than the event's end; the earliest seen is closest to it.
    """
    key = (competition_id, session_id, position)
    existing = _generated_times.get(key)
    if existing is None or generated_at < existing:
        _generated_times[key] = generated_at


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


def _name_occurrences(layout: tuple[tuple[int, str], ...]) -> dict[tuple[str, int], int]:
    """Position of each (name, nth time the name appears) in a session layout."""
    seen: dict[str, int] = {}
    keys: dict[tuple[str, int], int] = {}
    for position, name in layout:
        keys[(name, seen.get(name, 0))] = position
        seen[name] = seen.get(name, 0) + 1
    return keys


def reconcile_positions(competition_id: int, sessions: list[Session]) -> None:
    """Move position-keyed cache entries when a session's rows change.

    Events are matched by name (and which occurrence of it, for repeated names such as
    Break). An event that's gone loses its entries. Call it right after parsing a schedule,
    before anything reads or writes the caches.
    """
    for session in sessions:
        layout = tuple((e.position, e.name) for e in session.events)
        key = (competition_id, session.session_id)
        old = _session_layouts.get(key)
        _session_layouts[key] = layout
        if old is None or old == layout:
            continue
        new_positions = _name_occurrences(layout)
        moves = {pos: new_positions.get(name_key) for name_key, pos in _name_occurrences(old).items()}
        if all(old_pos == new_pos for old_pos, new_pos in moves.items()):
            continue
        for cache in _POSITION_CACHES:
            entries = [k for k in cache if k[0] == competition_id and k[1] == session.session_id]
            moved = {}
            for k in entries:
                value = cache.pop(k)
                new_pos = moves.get(k[2])
                if new_pos is not None:
                    moved[(competition_id, session.session_id, new_pos)] = value
            cache.update(moved)


def record_sprint_decider_range(competition_id: int, round_name: str, known: int, open_pairs: int) -> None:
    """Store a sprint round's partial decider count; once no pair is left to ride Ride 2 it's exact.

    A reading with more pairs still to ride Ride 2 than the stored one (a result page that
    lags the live page) is ignored.
    """
    key = (competition_id, round_name)
    stored = _sprint_decider_ranges.get(key)
    if stored is not None and open_pairs > stored[1]:
        return
    _sprint_decider_ranges[key] = (known, open_pairs)
    if open_pairs == 0:
        record_sprint_deciders(competition_id, round_name, known)


def record_sprint_rides_done(competition_id: int, round_name: str, rides: int) -> None:
    """Store how many rides of a sprint round every pair has finished."""
    _sprint_rides_done[(competition_id, round_name)] = rides


def sprint_ride_done(competition_id: int, event_name: str) -> bool | None:
    """Whether a best-of-3 ride has been ridden by every pair; None when unknown or not a ride."""
    ride = split_ride(event_name)
    if ride is None:
        return None
    done = _sprint_rides_done.get((competition_id, ride[0]))
    return None if done is None else ride[1] <= done


def apply_sprint_ride_status(competition_id: int, sessions: list[Session]) -> list[Session]:
    """Mark sprint rides not yet ridden as UPCOMING (or NOT_READY without a start list).

    The rides of a best-of-3 round share one result page, so upstream gives Ride 2 and
    Ride 3 an enabled Results button, and parse_schedule marks them COMPLETED, as soon as
    Ride 1 is posted. A ride whose completion isn't known yet keeps its parsed status.
    """
    result = []
    for session in sessions:
        events = []
        for e in session.events:
            if (
                e.status == EventStatus.COMPLETED
                and e.discipline == "sprint_match"
                and sprint_ride_done(competition_id, e.name) is False
            ):
                status = EventStatus.UPCOMING if e.start_list_url else EventStatus.NOT_READY
                e = e.model_copy(update={"status": status})
            events.append(e)
        result.append(session.model_copy(update={"events": events}))
    return result


class _Estimate(NamedTuple):
    """A pre-result duration and what it was built from."""

    minutes: float
    heats: int | None = None
    basis: HeatBasis | None = None
    km: float | None = None
    kmh: float | None = None  # pace used with km
    km_basis: DistanceBasis | None = None
    per_heat: float | None = None  # minutes per heat used with heats
    deciders_known: int | None = None  # with "decider_pairs": pairs already tied after Ride 2


def _base_estimate(
    competition_id: int,
    session_id: int,
    event: Event,
    learned: Mapping[str, float] | None,
    bunch: float = LIVE_BUNCH_CHANGEOVER_MINUTES,
    inferred: tuple[int, HeatBasis] | None = None,
    *,
    band: AgeBand | None,
    scheduled_km: float | None = None,
) -> _Estimate:
    """Pre-result duration: heat count × per-heat + changeover, else the default.

    bunch is the competition's bunch-race changeover (bunch_changeover). Defaults and
    learned averages for Finish-Time races include the static changeover, so it's
    swapped for bunch.

    band is the event's age band (rider_list.event_band). A points or scratch race with a
    start-list distance, else scheduled_km (supplements.scheduled_distances), runs at
    bunch_race_kmh(band); per-heat minutes come from get_per_heat_duration with the same band.
    Without a start list, a sprint round's pairs come from its name (sprint_round_pairs),
    and other events' heats from inferred (infer_heats: Rider List entrants, field sizes or the
    round name).
    A sprint Ride 3 is ridden only by pairs tied after Ride 2: SPRINT_DECIDER_MINUTES per
    decider once Ride 2 is posted, else per expected decider (pairs × SPRINT_DECIDER_RATE).
    """
    if event.discipline in DISTANCE_DISCIPLINES:
        km = _race_distances.get((competition_id, session_id, event.position))
        km_basis: DistanceBasis = "start_list"
        if not km and scheduled_km:
            km, km_basis = scheduled_km, "schedule"
        if km:
            kmh = bunch_race_kmh(band)
            return _Estimate(km / kmh * 60 + bunch, km=km, kmh=kmh, km_basis=km_basis)
    hc = get_heat_count(competition_id, session_id, event.position)
    phd = get_per_heat_duration(event.discipline, band)
    if event.discipline == "sprint_match":
        pairs: float | None = hc if hc is not None else sprint_round_pairs(event.name)
        ride = split_ride(event.name)
        if ride is not None and ride[1] == 3:
            deciders = _sprint_deciders.get((competition_id, ride[0]))
            if deciders is not None:
                return _Estimate(deciders * SPRINT_DECIDER_MINUTES, deciders, "decider")
            decider_range = _sprint_decider_ranges.get((competition_id, ride[0]))
            if decider_range is not None and sum(decider_range) > 0:
                # During Ride 2: tied pairs ride a decider, the rest still at the expected rate.
                known, open_pairs = decider_range
                minutes = (known + open_pairs * SPRINT_DECIDER_RATE) * SPRINT_DECIDER_MINUTES
                return _Estimate(minutes, known + open_pairs, "decider_pairs", deciders_known=known)
            if pairs is None:
                pairs = _get_duration(event.discipline, learned) / phd
            return _Estimate(pairs * SPRINT_DECIDER_MINUTES * SPRINT_DECIDER_RATE, round(pairs), "decider_pairs")
        if hc is None and pairs is not None:
            return _Estimate(pairs * phd, int(pairs), "round", per_heat=phd)
    basis: HeatBasis = "start_list"
    if hc is None and inferred is not None:
        hc, basis = inferred
    if hc is not None:
        return _Estimate(hc * phd + _changeover(event.discipline, bunch), hc, basis, per_heat=phd)
    shift = _changeover(event.discipline, bunch) - get_changeover(event.discipline)
    return _Estimate(_get_duration(event.discipline, learned) + shift)


def infer_heats(
    sessions: list[Session],
    rider_list: list[RiderListEntry] | None,
    fields: list[FieldSize] | None = None,
) -> dict[tuple[int, int], tuple[int, HeatBasis]]:
    """Heat counts for events without a start list, keyed by (session_id, position).

    From the round name: a pursuit, team pursuit or team sprint final that follows a
    qualifying round of the same name is ridden for bronze and gold (2 heats), and a keirin
    round's heats come from keirin_round_heats. From committed field sizes: team qualifying
    rounds and small team finals (fields.heats_from_fields), replacing the round name. From
    the Rider List: individual qualifying rounds and time trials (rider_list.estimate_heats).
    Sprint rounds are sized in _base_estimate, since a Ride 3 depends on them.
    """
    names = {e.name for s in sessions for e in s.events}
    heats: dict[tuple[int, int], tuple[int, HeatBasis]] = {}
    for s in sessions:
        for e in s.events:
            n = None
            if e.discipline in MEDAL_FINAL_DISCIPLINES and qualifying_name(e.name) in names:
                n = 2
            elif e.discipline == "keirin":
                n = keirin_round_heats(e.name)
            if n is not None:
                heats[(s.session_id, e.position)] = (n, "round")
    if fields:
        heats.update({k: (n, "entry_list") for k, n in heats_from_fields(fields, sessions).items()})
    if rider_list:
        heats.update({k: (n, "rider_list") for k, n in estimate_heats(rider_list, sessions).items()})
    return heats


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
    band: AgeBand | None,
) -> RiderMatch | None:
    """
    Match pre-tokenized racer name tokens against cached start list riders.

    Expects a frozenset of lowercased, normalized tokens (computed once via
    _normalize_rider_name) for case-insensitive, order-independent matching.
    band is the event's age band (rider_list.event_band), for its per-heat duration.
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
                phd = get_per_heat_duration(discipline, band)
                heat_predicted_start = event_start + timedelta(minutes=(rider.heat - 1) * phd)
            return RiderMatch(
                heat=rider.heat,
                heat_count=hc,
                heat_predicted_start=heat_predicted_start,
                team_name=rider.team_name,
            )

    return None


def bunch_changeover(competition_id: int, sessions: list[Session]) -> float:
    """
    The competition's bunch-race changeover: median (Generated gap − Finish Time).

    Samples are bunch races (FINISH_TIME_DISCIPLINES) straight after another bunch race
    with its own result page, with an overhead in [0, MAX_CHANGEOVER_MINUTES]. Races
    after a sprint or timed event also include staging the field, so they're left out.
    Returns LIVE_BUNCH_CHANGEOVER_MINUTES until there are MIN_CHANGEOVER_SAMPLES.
    """
    samples = []
    for s in sessions:
        for prev, e in zip(s.events, s.events[1:], strict=False):
            if e.discipline not in FINISH_TIME_DISCIPLINES or prev.discipline not in FINISH_TIME_DISCIPLINES:
                continue
            if prev.result_url is None or prev.result_url == e.result_url:
                continue
            finish = _finish_times.get((competition_id, s.session_id, e.position))
            t0 = _generated_times.get((competition_id, s.session_id, prev.position))
            t1 = _generated_times.get((competition_id, s.session_id, e.position))
            if finish is None or t0 is None or t1 is None:
                continue
            overhead = (t1 - t0).total_seconds() / 60.0 - finish
            if 0 <= overhead <= MAX_CHANGEOVER_MINUTES:
                samples.append(overhead)
    if len(samples) < MIN_CHANGEOVER_SAMPLES:
        return LIVE_BUNCH_CHANGEOVER_MINUTES
    return median(samples)


def _changeover(discipline: str, bunch: float) -> float:
    """Changeover for a discipline: the calibrated bunch value for Finish-Time races, else static."""
    return bunch if discipline in FINISH_TIME_DISCIPLINES else get_changeover(discipline)


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


def load_learned_durations(sessions: list[Session]) -> dict[str, float]:
    """Read the learned average for each distinct discipline in sessions, once each.

    Blocking (SQLite or DynamoDB): run it in a worker thread. Disciplines without
    enough samples are left out, so they fall back to the default.
    """
    learned: dict[str, float] = {}
    for discipline in sorted({e.discipline for s in sessions for e in s.events}):
        value = get_learned_duration(discipline)
        if value is not None:
            learned[discipline] = value
    return learned


def _get_duration(discipline: str, learned: Mapping[str, float] | None = None) -> float:
    """Return the learned duration when one was loaded (use_learned on), otherwise the default."""
    if learned and discipline in learned:
        return learned[discipline]
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


def _session_elapsed(session: Session, now: datetime) -> float:
    """Minutes from the session's scheduled start to now, wrapping sessions that cross midnight."""
    now_minutes = now.hour * 60.0 + now.minute + now.second / 60.0
    elapsed = now_minutes - _time_to_minutes(session.scheduled_start)
    if elapsed < -60:
        elapsed += 1440.0
    return elapsed


def _in_session_window(session: Session, durations: list[float], now: datetime) -> bool:
    """True from the scheduled start until an hour past the estimated end.

    Outside the window predictions show scheduled times, so results viewed hours later
    don't produce an inflated delay. The hour allows for genuine long-running sessions.
    """
    elapsed = _session_elapsed(session, now)
    return 0 < elapsed <= sum(durations) + 60


def _clamp_delay(delay: float) -> float:
    return max(MIN_DELAY_MINUTES, min(delay, MAX_DELAY_MINUTES))


def _compute_delay(
    session: Session,
    durations: list[float],
    completed_count: int,
    now: datetime,
) -> float:
    """
    Estimate how many minutes the session is running behind (positive) or
    ahead (negative) of schedule, assuming the active event starts now.

    The fallback when the active event's start isn't known (_active_event_start).
    Returns 0 outside the session window (_in_session_window).
    """
    if not _in_session_window(session, durations, now):
        return 0.0
    return _clamp_delay(_session_elapsed(session, now) - sum(durations[:completed_count]))


def _active_event_start(
    competition_id: int,
    session: Session,
    durations: list[float],
    completed_count: int,
    now: datetime,
) -> datetime | None:
    """When the active event (the first not completed) started, from result-page Generated timestamps.

    A Generated timestamp marks an event's end, so the active event started at the last
    completed event's Generated time. Completed events after the newest timestamp (breaks
    have no result page) add their estimated durations. A ceremony's Generated timestamp
    marks its start, so its own duration is added too. The result is capped at now, since
    the active event has started by definition. None when no completed event has a
    timestamp, or the newest one is from another day or ahead of now (a clock mismatch).
    """
    offset = 0.0
    for j in range(completed_count - 1, -1, -1):
        event = session.events[j]
        generated = _generated_times.get((competition_id, session.session_id, event.position))
        if generated is None:
            if event.discipline not in _ZERO_DURATION_DISCIPLINES:
                offset += durations[j]
            continue
        if generated.date() != now.date():
            return None
        if generated > now + timedelta(minutes=GENERATED_AHEAD_TOLERANCE_MINUTES):
            return None
        if event.discipline == "ceremony":
            offset += durations[j]
        return min(generated + timedelta(minutes=offset), now)
    return None


def _anchored_delays(
    session: Session,
    durations: list[float],
    completed_count: int,
    now: datetime,
    active_start: datetime,
) -> tuple[float, float]:
    """(active event's delay, delay for the events after it) given when the active event started.

    The active event keeps its actual start. Later events follow its estimated end, or
    now + ACTIVE_MIN_REMAINING_MINUTES once it has run past its estimate.
    """
    start_elapsed = _session_elapsed(session, active_start)
    active_delay = start_elapsed - sum(durations[:completed_count])
    elapsed_in_active = (now - active_start).total_seconds() / 60.0
    overrun = max(0.0, elapsed_in_active + ACTIVE_MIN_REMAINING_MINUTES - durations[completed_count])
    return _clamp_delay(active_delay), _clamp_delay(active_delay + overrun)


def _live_heat_remaining(
    competition_id: int,
    session: Session,
    index: int,
    estimate: _Estimate | None,
    band: AgeBand | None,
    changeover: float,
) -> float | None:
    """Minutes left in the active event from its live heat count, or None without one.

    The unfinished heats with the running one taken as half done, plus the changeover,
    and at least ACTIVE_MIN_REMAINING_MINUTES. Expected deciders aren't heats that will
    all be ridden, so they don't count.
    """
    event = session.events[index]
    finished = get_live_heat(competition_id, session.session_id, event.position)
    if finished is None or estimate is None or estimate.heats is None or estimate.basis == "decider_pairs":
        return None
    left = max(0.0, estimate.heats - finished - 0.5)
    per_heat = get_per_heat_duration(event.discipline, band)
    return max(ACTIVE_MIN_REMAINING_MINUTES, left * per_heat + _changeover(event.discipline, changeover))


def predict_session(
    competition_id: int,
    session: Session,
    now: datetime | None = None,
    racer_name: str | None = None,
    learned: Mapping[str, float] | None = None,
    rider_list_matches: dict[tuple[int, int], RiderMatch] | None = None,
    ceremony_podiums: dict[tuple[int, int], int] | None = None,
    changeover: float = LIVE_BUNCH_CHANGEOVER_MINUTES,
    inferred_heats: dict[tuple[int, int], tuple[int, HeatBasis]] | None = None,
    scheduled_km: dict[tuple[int, int], float] | None = None,
) -> SessionPrediction:
    """
    Compute predicted start times for all events in a session.

    Duration source priority (most to least accurate):
      1. Observed: result-page Finish Time + changeover
      2. Generated: difference between consecutive result-page Generated timestamps
      3. Heat count: start-list heat count × per-heat duration + changeover
         (a start-list or scheduled race distance, sprint round name, sprint deciders or Rider List entrants
         can stand in, see _base_estimate)
      4. Default: learned average or DEFAULT_DURATIONS fallback
    A medal ceremony with forecast podiums uses ceremony_duration instead; its own
    Generated timestamp marks when it starts, so the gaps before and after it are never used.

    now: server wall-clock time used to estimate real-time delay.
         If None, no delay adjustment is applied (pre-event mode).
    racer_name: optional racer name for rider matching.
    rider_list_matches: the racer's Rider List matches from rider_list.match_events,
            keyed by (session_id, position); used for events without start-list riders.
    ceremony_podiums: forecast podiums per medal ceremony from ceremonies.forecast_podiums,
            keyed by (session_id, position).
    changeover: the competition's bunch-race changeover from bunch_changeover.
    inferred_heats: heat counts and their basis from infer_heats, keyed by (session_id, position);
            used for events without a start-list heat count.
    scheduled_km: race distances from supplements.scheduled_distances, keyed by (session_id, position);
            used for points and scratch races without a start-list distance.
    """
    inferred_heats = inferred_heats or {}
    scheduled_km = scheduled_km or {}
    # Pre-tokenize racer name once for the entire session (avoids re-normalizing per event)
    user_tokens = normalize_rider_name(racer_name) if racer_name and racer_name.strip() else None

    durations: list[float] = []
    is_observed_list: list[bool] = []
    estimates: list[_Estimate | None] = []
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
    bands = [event_band(e.name) for e in events]
    gen_durations: dict[int, float] = {}
    for i in range(1, len(events)):
        # A ceremony's Generated timestamp marks its start, so neither the gap ending at a
        # ceremony nor the one starting at it is an event's duration.
        if events[i].discipline == "ceremony" or events[i - 1].discipline == "ceremony":
            continue
        t0 = _generated_times.get((competition_id, session.session_id, events[i - 1].position))
        t1 = _generated_times.get((competition_id, session.session_id, events[i].position))
        # Expected duration: the pre-result estimate with the STATIC default
        # (not learned averages).  Learned data may itself be corrupted by bad
        # gen-duration observations from earlier runs, so it must not influence
        # the bounds used to validate new observations.
        expected = _base_estimate(
            competition_id,
            session.session_id,
            events[i],
            None,
            changeover,
            inferred_heats.get((session.session_id, events[i].position)),
            band=bands[i],
            scheduled_km=scheduled_km.get((session.session_id, events[i].position)),
        ).minutes
        mins = generated_gap_duration(t0, t1, expected)
        if mins is not None:
            gen_durations[i] = mins

    for i, e in enumerate(events):
        finish = _finish_times.get((competition_id, session.session_id, e.position))
        observed = None if finish is None else finish + _changeover(e.discipline, changeover)
        podiums = (ceremony_podiums or {}).get((session.session_id, e.position))
        podium_list.append(podiums)
        if podiums is not None:
            durations.append(ceremony_duration(podiums))
            is_observed_list.append(False)
            estimates.append(None)
        elif observed is not None:
            durations.append(observed)
            is_observed_list.append(True)
            estimates.append(None)
        elif i in gen_durations:
            durations.append(gen_durations[i])
            is_observed_list.append(True)
            estimates.append(None)
        else:
            base = _base_estimate(
                competition_id,
                session.session_id,
                e,
                learned,
                changeover,
                inferred_heats.get((session.session_id, e.position)),
                band=bands[i],
                scheduled_km=scheduled_km.get((session.session_id, e.position)),
            )
            durations.append(base.minutes)
            is_observed_list.append(False)
            estimates.append(base)

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
    active_start: datetime | None = None
    remaining: float | None = None
    if now is not None and completed_count > 0 and has_pending:
        delay_minutes = _compute_delay(session, durations, completed_count, now)
        if _in_session_window(session, durations, now):
            active_start = _active_event_start(competition_id, session, durations, completed_count, now)
            remaining = _live_heat_remaining(
                competition_id,
                session,
                completed_count,
                estimates[completed_count],
                bands[completed_count],
                changeover,
            )
    # The active event starts when the last completed event ended, if that's known; otherwise now.
    active_delay = delay_minutes
    if now is not None and active_start is not None:
        active_delay, delay_minutes = _anchored_delays(session, durations, completed_count, now, active_start)
    # A live heat count says how long the active event has left, so later events follow it.
    if now is not None and remaining is not None:
        delay_minutes = _clamp_delay(_session_elapsed(session, now) + remaining - sum(durations[: completed_count + 1]))

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
        if i < completed_count:
            applied_delay = 0.0
        elif i == completed_count:
            applied_delay = active_delay
        else:
            applied_delay = delay_minutes
        predicted_start = _add_minutes(session.scheduled_start, cumulative + applied_delay)
        is_active = i == active_index
        est = estimates[i]
        hc = est.heats if est else None
        band = bands[i]

        # For an active multi-heat event, determine which heat is currently running.
        # Priority: (1) live results page heat, (2) time-based fallback estimate.
        active_heat: int | None = None
        active_heat_live = False
        # Expected deciders aren't heats that will all be ridden, so they don't drive the heat counter.
        if is_active and now is not None and not (est and est.basis == "decider_pairs"):
            live_heat = get_live_heat(competition_id, session.session_id, event.position)
            if live_heat is not None:
                # live_heat = count of finished heats; the running heat is the next one.
                next_heat = live_heat + 1
                active_heat = min(next_heat, hc) if hc else next_heat
                active_heat_live = True
            elif hc:
                # Time-based fallback: elapsed since the event started ÷ per-heat duration.
                # Without a known start, uses the scheduled (not delay-adjusted) start so
                # prior-event overrun doesn't incorrectly advance the heat counter.
                phd = get_per_heat_duration(event.discipline, band)
                if active_start is not None:
                    elapsed_in_active = (now - active_start).total_seconds() / 60.0
                else:
                    est_before_active = sum(durations[:active_index])
                    elapsed_in_active = max(0.0, _session_elapsed(session, now) - est_before_active)
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
                    band,
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
                heat_count=hc,
                heat_basis=est.basis if est else None,
                race_distance_km=est.km if est else None,
                race_kmh=est.kmh if est else None,
                distance_basis=est.km_basis if est else None,
                per_heat_minutes=est.per_heat if est else None,
                deciders_known=est.deciders_known if est else None,
                podium_count=podium_list[i],
                is_active=is_active,
                active_heat=active_heat,
                active_heat_live=active_heat_live,
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
    learned: Mapping[str, float] | None = None,
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
    changeover = bunch_changeover(competition_id, sessions)
    supplement = load_supplement(competition_id)
    inferred_heats = infer_heats(sessions, rider_list, supplement.fields if supplement else None)
    scheduled_km = scheduled_distances(supplement.distances, sessions) if supplement else None

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
            learned=learned,
            rider_list_matches=rider_list_matches,
            ceremony_podiums=ceremony_podiums,
            changeover=changeover,
            inferred_heats=inferred_heats,
            scheduled_km=scheduled_km,
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
) -> list[LiveDuration]:
    """
    Compare current event statuses against the cache.

    When an event transitions UPCOMING -> COMPLETED, returns the wall-clock
    elapsed time as a learning record (fallback when no Finish Time). The caller
    persists the records with save_live_durations, off the event loop.
    """
    wall_clock: list[LiveDuration] = []

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
                    wall_clock.append(
                        LiveDuration(
                            competition_id=competition_id,
                            session_id=session.session_id,
                            event_position=event.position,
                            event_name=event.name,
                            discipline=event.discipline,
                            duration_minutes=elapsed,
                            source="wall_clock",
                        )
                    )
                _status_cache[key] = {"status": event.status, "seen_at": now}

            elif cached["status"] != event.status:
                _status_cache[key] = {"status": event.status, "seen_at": now}

    return wall_clock
