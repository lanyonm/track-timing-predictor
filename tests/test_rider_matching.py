"""Tests for rider matching and next-race logic in app/predictor.py."""
from datetime import datetime, time, timedelta

import pytest

from app.disciplines import get_per_heat_duration
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
from app.predictor import (
    get_rider_match,
    predict_schedule,
    predict_session,
    record_heat_count,
    record_start_list_riders,
    _start_list_riders,
    _heat_counts,
)

# ── Constants ────────────────────────────────────────────────────────────────

COMP_ID = 99999
SESSION_ID = 1
DISCIPLINE = "keirin"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_caches():
    from app.predictor import _start_list_riders, _heat_counts
    _start_list_riders.clear()
    _heat_counts.clear()
    yield
    _start_list_riders.clear()
    _heat_counts.clear()


# ── Helpers ──────────────────────────────────────────────────────────────────


def make_event(
    position: int = 0,
    name: str = "Elite Men Keirin",
    discipline: str = DISCIPLINE,
    status: EventStatus = EventStatus.UPCOMING,
    is_special: bool = False,
    start_list_url: str | None = "http://example.com/startlist",
    result_url: str | None = None,
    live_url: str | None = None,
) -> Event:
    return Event(
        position=position,
        name=name,
        discipline=discipline,
        status=status,
        is_special=is_special,
        start_list_url=start_list_url,
        result_url=result_url,
        live_url=live_url,
    )


def make_session(
    session_id: int = SESSION_ID,
    day: str = "Friday",
    scheduled_start: time = time(18, 0),
    events: list[Event] | None = None,
) -> Session:
    return Session(
        session_id=session_id,
        day=day,
        scheduled_start=scheduled_start,
        events=events or [],
    )


def seed_riders(
    position: int,
    riders: list[tuple[str, int]],
    comp_id: int = COMP_ID,
    session_id: int = SESSION_ID,
) -> None:
    """Seed the start list cache with rider entries.

    riders: list of (name, heat) tuples.
    """
    entries = [RiderEntry(name=name, heat=heat) for name, heat in riders]
    record_start_list_riders(comp_id, session_id, position, entries)


# ── TestRiderMatching ────────────────────────────────────────────────────────


class TestRiderMatching:
    """Tests for get_rider_match and related rider matching logic."""

    def test_case_insensitive_matching(self):
        """'Sean Hall' matches entry 'HALL Sean' (case-insensitive)."""
        seed_riders(0, [("HALL Sean", 1)])
        match = get_rider_match(COMP_ID, SESSION_ID, 0, "Sean Hall", None, DISCIPLINE)
        assert match is not None
        assert isinstance(match, RiderMatch)
        assert match.heat == 1

    def test_order_independent_matching(self):
        """'Hall Sean' matches entry 'HALL Sean' (order-independent)."""
        seed_riders(0, [("HALL Sean", 1)])
        match = get_rider_match(COMP_ID, SESSION_ID, 0, "Hall Sean", None, DISCIPLINE)
        assert match is not None
        assert match.heat == 1

    def test_no_match_partial_name(self):
        """Partial name 'Sean' does NOT match 'HALL Sean' (requires full name)."""
        seed_riders(0, [("HALL Sean", 1)])
        match = get_rider_match(COMP_ID, SESSION_ID, 0, "Sean", None, DISCIPLINE)
        assert match is None

    def test_no_match_empty_input(self):
        """Empty string and whitespace-only return None."""
        seed_riders(0, [("HALL Sean", 1)])
        assert get_rider_match(COMP_ID, SESSION_ID, 0, "", None, DISCIPLINE) is None
        assert get_rider_match(COMP_ID, SESSION_ID, 0, "   ", None, DISCIPLINE) is None

    def test_per_heat_predicted_start(self):
        """heat_predicted_start = event_start + (heat - 1) * per_heat_duration."""
        seed_riders(0, [("HALL Sean", 3)])
        record_heat_count(COMP_ID, SESSION_ID, 0, 4)
        event_start = datetime(2024, 6, 1, 18, 30, 0)
        match = get_rider_match(COMP_ID, SESSION_ID, 0, "Sean Hall", event_start, DISCIPLINE)
        assert match is not None
        phd = get_per_heat_duration(DISCIPLINE)
        expected = event_start + timedelta(minutes=(3 - 1) * phd)
        assert match.heat_predicted_start == expected

    def test_single_heat_returns_event_start(self):
        """When heat_count=1, heat_predicted_start equals event_start."""
        seed_riders(0, [("HALL Sean", 1)])
        record_heat_count(COMP_ID, SESSION_ID, 0, 1)
        event_start = datetime(2024, 6, 1, 18, 30, 0)
        match = get_rider_match(COMP_ID, SESSION_ID, 0, "Sean Hall", event_start, DISCIPLINE)
        assert match is not None
        assert match.heat_predicted_start == event_start

    def test_apostrophe_name_matches(self):
        """'OBrien' matches 'O'BRIEN Liam' (apostrophe stripping)."""
        seed_riders(0, [("O'BRIEN Liam", 2)])
        match = get_rider_match(COMP_ID, SESSION_ID, 0, "OBrien Liam", None, DISCIPLINE)
        assert match is not None
        assert match.heat == 2

    def test_diacritics_name_matches(self):
        """'Muller' matches 'MÜLLER Hans' (Unicode NFKD normalization)."""
        seed_riders(0, [("MÜLLER Hans", 1)])
        match = get_rider_match(COMP_ID, SESSION_ID, 0, "Muller Hans", None, DISCIPLINE)
        assert match is not None
        assert match.heat == 1


# ── TestNextRace ─────────────────────────────────────────────────────────────


class TestNextRace:
    """Tests for predict_schedule next_race_* fields and related aggregation."""

    def test_next_race_active_event(self):
        """Active event with rider match sets next_race_is_active=True."""
        # Build a session with: 1 completed event, 1 active event (the match), 1 upcoming
        events = [
            make_event(position=0, name="Elite Men Sprint", discipline="sprint_match",
                       status=EventStatus.COMPLETED),
            make_event(position=1, name="Elite Men Keirin", discipline=DISCIPLINE,
                       status=EventStatus.UPCOMING),
            make_event(position=2, name="Elite Women Keirin", discipline=DISCIPLINE,
                       status=EventStatus.UPCOMING),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        # Seed rider in event at position 1
        seed_riders(1, [("HALL Sean", 1)])

        # now must be set so that completed_count > 0 and has_pending => active_index = 1
        now = datetime(2024, 6, 1, 18, 15, 0)
        result = predict_schedule(COMP_ID, [session], now=now, racer_name="Sean Hall",
                                  use_learned=False)

        assert result.next_race_event_name == "Elite Men Keirin"
        assert result.next_race_is_active is True
        assert result.next_race_heat == 1

    def test_next_race_upcoming_event(self):
        """Upcoming event with rider match sets next_race_is_active=False."""
        events = [
            make_event(position=0, name="Elite Men Sprint", discipline="sprint_match",
                       status=EventStatus.UPCOMING),
            make_event(position=1, name="Elite Men Keirin", discipline=DISCIPLINE,
                       status=EventStatus.UPCOMING),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        # Seed rider only in event at position 1
        seed_riders(1, [("HALL Sean", 1)])

        # No completed events => no active index. now=None means pre-event mode.
        result = predict_schedule(COMP_ID, [session], now=None, racer_name="Sean Hall",
                                  use_learned=False)

        assert result.next_race_event_name == "Elite Men Keirin"
        assert result.next_race_is_active is False

    def test_next_race_all_completed(self):
        """When all events are completed, next_race_event_name is None."""
        events = [
            make_event(position=0, name="Elite Men Sprint", discipline="sprint_match",
                       status=EventStatus.COMPLETED),
            make_event(position=1, name="Elite Men Keirin", discipline=DISCIPLINE,
                       status=EventStatus.COMPLETED),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        seed_riders(0, [("HALL Sean", 1)])
        seed_riders(1, [("HALL Sean", 1)])

        result = predict_schedule(COMP_ID, [session], now=None, racer_name="Sean Hall",
                                  use_learned=False)

        assert result.next_race_event_name is None

    def test_events_without_start_lists_excludes_special(self):
        """Special events (break, ceremony) are excluded from events_without_start_lists."""
        events = [
            make_event(position=0, name="Elite Men Keirin", discipline=DISCIPLINE,
                       status=EventStatus.UPCOMING),
            make_event(position=1, name="Break", discipline="break_",
                       status=EventStatus.UPCOMING, is_special=True,
                       start_list_url=None),
            make_event(position=2, name="Medal Ceremonies", discipline="ceremony",
                       status=EventStatus.UPCOMING, is_special=True,
                       start_list_url=None),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        # No start lists seeded for any event. Only non-special events should count.
        result = predict_schedule(COMP_ID, [session], now=None, racer_name="Sean Hall",
                                  use_learned=False)

        # Only the keirin at position 0 should count as missing a start list
        assert result.events_without_start_lists == 1

    def test_has_racer_match_on_session_prediction(self):
        """SessionPrediction.has_racer_match is True when a rider match exists."""
        events = [
            make_event(position=0, name="Elite Men Keirin", discipline=DISCIPLINE,
                       status=EventStatus.UPCOMING),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))
        seed_riders(0, [("HALL Sean", 1)])

        sp = predict_session(COMP_ID, session, now=None, racer_name="Sean Hall",
                             use_learned=False)

        assert sp.has_racer_match is True

    def test_total_events_excludes_special(self):
        """total_events only counts non-is_special events."""
        events = [
            make_event(position=0, name="Elite Men Keirin", discipline=DISCIPLINE,
                       status=EventStatus.UPCOMING),
            make_event(position=1, name="Elite Women Keirin", discipline=DISCIPLINE,
                       status=EventStatus.UPCOMING),
            make_event(position=2, name="Break", discipline="break_",
                       status=EventStatus.UPCOMING, is_special=True),
            make_event(position=3, name="End of Session", discipline="end_of_session",
                       status=EventStatus.UPCOMING, is_special=True),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        result = predict_schedule(COMP_ID, [session], now=None, use_learned=False)

        assert result.total_events == 2

    def test_pre_event_no_active(self):
        """When now is None, all matched events are upcoming (not active)."""
        events = [
            make_event(position=0, name="Elite Men Keirin", discipline=DISCIPLINE,
                       status=EventStatus.UPCOMING),
            make_event(position=1, name="Elite Women Keirin", discipline=DISCIPLINE,
                       status=EventStatus.UPCOMING),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        seed_riders(0, [("HALL Sean", 1)])
        seed_riders(1, [("HALL Sean", 1)])

        sp = predict_session(COMP_ID, session, now=None, racer_name="Sean Hall",
                             use_learned=False)

        for pred in sp.event_predictions:
            assert pred.is_active is False
