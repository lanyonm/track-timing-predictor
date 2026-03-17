"""Tests for rider name matching and per-heat timing in app/predictor.py."""
from datetime import time

from app.models import Event, EventStatus, RiderEntry, Session
from app.predictor import (
    _heat_counts,
    _normalize_name,
    _start_list_riders,
    get_rider_match,
    get_start_list_riders,
    predict_schedule,
    predict_session,
    record_heat_count,
    record_start_list_riders,
)


def _make_riders(*names_and_heats):
    """Helper: create RiderEntry list from (name, heat) tuples."""
    return [
        RiderEntry(name=n, heat=h, normalized_tokens=frozenset(n.lower().split()))
        for n, h in names_and_heats
    ]


def _make_session(events=None):
    """Helper: create a minimal Session for testing."""
    if events is None:
        events = [
            Event(position=0, name="Sprint Qualifying", discipline="sprint_qualifying",
                  status=EventStatus.UPCOMING, is_special=False, start_list_url="test-S.htm"),
            Event(position=1, name="Keirin Round 1", discipline="keirin",
                  status=EventStatus.UPCOMING, is_special=False, start_list_url="test2-S.htm"),
        ]
    return Session(session_id=1, day="Friday", scheduled_start=time(8, 15), events=events)


# ── _start_list_riders cache ────────────────────────────────────────────────


class TestStartListRidersCache:
    def setup_method(self):
        _start_list_riders.clear()

    def test_record_and_retrieve(self):
        riders = [
            RiderEntry(name="SMITH John", heat=1, normalized_tokens=frozenset({"smith", "john"})),
        ]
        record_start_list_riders(1, 1, 0, riders)
        result = get_start_list_riders(1, 1, 0)
        assert len(result) == 1
        assert result[0].name == "SMITH John"

    def test_missing_key_returns_empty_list(self):
        assert get_start_list_riders(999, 999, 999) == []

    def test_multiple_events_independent(self):
        riders_a = [RiderEntry(name="A", heat=1, normalized_tokens=frozenset({"a"}))]
        riders_b = [RiderEntry(name="B", heat=2, normalized_tokens=frozenset({"b"}))]
        record_start_list_riders(1, 1, 0, riders_a)
        record_start_list_riders(1, 1, 1, riders_b)
        assert get_start_list_riders(1, 1, 0)[0].name == "A"
        assert get_start_list_riders(1, 1, 1)[0].name == "B"


# ── _normalize_name ─────────────────────────────────────────────────────────


class TestNormalizeName:
    def test_basic(self):
        assert _normalize_name("Sean Hall") == frozenset({"sean", "hall"})

    def test_case_insensitive(self):
        assert _normalize_name("HALL Sean") == frozenset({"hall", "sean"})

    def test_extra_whitespace(self):
        assert _normalize_name("  Sean   Hall  ") == frozenset({"sean", "hall"})

    def test_order_independent(self):
        assert _normalize_name("Sean Hall") == _normalize_name("Hall Sean")
        assert _normalize_name("HALL Sean") == _normalize_name("Sean HALL")


# ── get_rider_match ─────────────────────────────────────────────────────────


class TestGetRiderMatch:
    def setup_method(self):
        _start_list_riders.clear()
        _heat_counts.clear()

    def test_match_case_insensitive(self):
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 1)))
        match = get_rider_match(1, 1, 0, "sean hall", time(8, 15), "sprint_qualifying")
        assert match is not None
        assert match.heat == 1

    def test_match_reversed_name_order(self):
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 1)))
        match = get_rider_match(1, 1, 0, "Hall Sean", time(8, 15), "sprint_qualifying")
        assert match is not None

    def test_no_match(self):
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 1)))
        match = get_rider_match(1, 1, 0, "Jane Doe", time(8, 15), "sprint_qualifying")
        assert match is None

    def test_no_riders_cached(self):
        match = get_rider_match(1, 1, 0, "Sean Hall", time(8, 15), "sprint_qualifying")
        assert match is None

    def test_multi_heat_predicted_start(self):
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 3)))
        record_heat_count(1, 1, 0, 5)
        match = get_rider_match(1, 1, 0, "Sean Hall", time(8, 15), "sprint_qualifying")
        assert match is not None
        assert match.heat == 3
        assert match.heat_count == 5
        assert match.heat_predicted_start is not None
        # Heat 3 start = event start + 2 × per_heat_duration
        assert match.heat_predicted_start > time(8, 15)

    def test_single_heat_no_heat_time(self):
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 1)))
        # No heat count cached → defaults to 1
        match = get_rider_match(1, 1, 0, "Sean Hall", time(8, 15), "sprint_qualifying")
        assert match is not None
        assert match.heat_count == 1
        assert match.heat_predicted_start is None


# ── predict_session with racer_name ─────────────────────────────────────────


class TestPredictSessionRacerName:
    def setup_method(self):
        _start_list_riders.clear()
        _heat_counts.clear()

    def test_racer_match_populated(self):
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 1)))
        session = _make_session()
        sp = predict_session(1, session, racer_name="Sean Hall")
        assert sp.event_predictions[0].rider_match is not None
        assert sp.event_predictions[0].rider_match.heat == 1

    def test_no_racer_name_no_match(self):
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 1)))
        session = _make_session()
        sp = predict_session(1, session)
        assert sp.event_predictions[0].rider_match is None

    def test_has_racer_match_set(self):
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 1)))
        session = _make_session()
        sp = predict_session(1, session, racer_name="Sean Hall")
        assert sp.has_racer_match is True

    def test_has_racer_match_false_when_no_match(self):
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 1)))
        session = _make_session()
        sp = predict_session(1, session, racer_name="Jane Doe")
        assert sp.has_racer_match is False

    def test_special_events_skipped(self):
        events = [
            Event(position=0, name="Break", discipline="break",
                  status=EventStatus.UPCOMING, is_special=True),
        ]
        record_start_list_riders(1, 1, 0, _make_riders(("Break", 1)))
        session = _make_session(events=events)
        sp = predict_session(1, session, racer_name="Break")
        assert sp.event_predictions[0].rider_match is None


# ── predict_schedule with racer_name ────────────────────────────────────────


class TestPredictScheduleRacerName:
    def setup_method(self):
        _start_list_riders.clear()
        _heat_counts.clear()

    def test_schedule_racer_fields_populated(self):
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 1)))
        session = _make_session()
        schedule = predict_schedule(1, [session], racer_name="Sean Hall")
        assert schedule.racer_name == "Sean Hall"
        assert schedule.match_count == 1

    def test_schedule_no_name(self):
        session = _make_session()
        schedule = predict_schedule(1, [session])
        assert schedule.racer_name is None
        assert schedule.match_count == 0

    def test_events_without_start_lists_counted(self):
        # Event at position 0 has riders cached, position 1 does not
        record_start_list_riders(1, 1, 0, _make_riders(("HALL Sean", 1)))
        session = _make_session()
        schedule = predict_schedule(1, [session], racer_name="Sean Hall")
        assert schedule.events_without_start_lists == 1
        assert schedule.total_events == 2

    def test_total_events_excludes_special(self):
        events = [
            Event(position=0, name="Sprint Qualifying", discipline="sprint_qualifying",
                  status=EventStatus.UPCOMING, is_special=False),
            Event(position=1, name="Break", discipline="break",
                  status=EventStatus.UPCOMING, is_special=True),
        ]
        session = _make_session(events=events)
        schedule = predict_schedule(1, [session], racer_name="Test")
        assert schedule.total_events == 1
