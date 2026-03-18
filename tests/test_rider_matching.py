"""Tests for rider matching: caches, name normalization, and match logic."""
from datetime import time

import pytest

from app.models import Event, EventStatus, RiderEntry, Session
from app.predictor import (
    _normalize_name,
    get_rider_match,
    get_start_list_riders,
    predict_schedule,
    predict_session,
    record_heat_count,
    record_start_list_riders,
)


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear predictor caches between tests."""
    from app.predictor import _heat_counts, _start_list_riders
    _start_list_riders.clear()
    _heat_counts.clear()
    yield
    _start_list_riders.clear()
    _heat_counts.clear()


class TestStartListRidersCache:
    def test_record_and_retrieve(self):
        riders = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
            RiderEntry(name="Bob Jones", heat=1, normalized_tokens=frozenset({"bob", "jones"})),
        ]
        record_start_list_riders(100, 1, 0, riders)
        result = get_start_list_riders(100, 1, 0)
        assert result == riders

    def test_missing_key_returns_empty_list(self):
        result = get_start_list_riders(999, 1, 0)
        assert result == []

    def test_overwrite_existing_entry(self):
        riders_v1 = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        riders_v2 = [
            RiderEntry(name="Charlie Brown", heat=2, normalized_tokens=frozenset({"charlie", "brown"})),
        ]
        record_start_list_riders(100, 1, 0, riders_v1)
        record_start_list_riders(100, 1, 0, riders_v2)
        assert get_start_list_riders(100, 1, 0) == riders_v2

    def test_different_keys_are_independent(self):
        riders_a = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        riders_b = [
            RiderEntry(name="Bob Jones", heat=1, normalized_tokens=frozenset({"bob", "jones"})),
        ]
        record_start_list_riders(100, 1, 0, riders_a)
        record_start_list_riders(100, 1, 1, riders_b)
        assert get_start_list_riders(100, 1, 0) == riders_a
        assert get_start_list_riders(100, 1, 1) == riders_b

    def test_empty_rider_list(self):
        record_start_list_riders(100, 1, 0, [])
        assert get_start_list_riders(100, 1, 0) == []


class TestNormalizeName:
    def test_case_insensitive(self):
        assert _normalize_name("Alice SMITH") == frozenset({"alice", "smith"})

    def test_order_independent(self):
        assert _normalize_name("Smith Alice") == _normalize_name("Alice Smith")

    def test_extra_whitespace(self):
        assert _normalize_name("  Alice   Smith  ") == frozenset({"alice", "smith"})

    def test_single_name(self):
        assert _normalize_name("Alice") == frozenset({"alice"})


class TestGetRiderMatch:
    def test_match_found(self):
        riders = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)
        match = get_rider_match(100, 1, 0, "Alice Smith", time(10, 0), "keirin")
        assert match is not None
        assert match.heat == 1
        assert match.heat_count == 1  # no heat count cached, defaults to 1

    def test_no_match(self):
        riders = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)
        match = get_rider_match(100, 1, 0, "Bob Jones", time(10, 0), "keirin")
        assert match is None

    def test_case_insensitive_match(self):
        riders = [
            RiderEntry(name="Alice Smith", heat=2, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)
        match = get_rider_match(100, 1, 0, "alice smith", time(10, 0), "keirin")
        assert match is not None
        assert match.heat == 2

    def test_multi_heat_predicted_start(self):
        """Heat predicted start = event_start + (heat - 1) * per_heat_duration."""
        riders = [
            RiderEntry(name="Alice Smith", heat=3, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)
        record_heat_count(100, 1, 0, 5)
        # keirin per_heat_duration = 4.5 min; heat 3 offset = (3-1)*4.5 = 9 min
        match = get_rider_match(100, 1, 0, "Alice Smith", time(10, 0), "keirin")
        assert match is not None
        assert match.heat == 3
        assert match.heat_count == 5
        assert match.heat_predicted_start == time(10, 9)

    def test_single_heat_returns_heat_count_1(self):
        riders = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)
        match = get_rider_match(100, 1, 0, "Alice Smith", time(10, 0), "scratch_race")
        assert match is not None
        assert match.heat_count == 1
        assert match.heat_predicted_start == time(10, 0)  # heat 1 offset = 0


def _make_session(events=None):
    """Helper to build a Session with sensible defaults."""
    if events is None:
        events = [
            Event(position=0, name="Keirin Round 1", discipline="keirin",
                  status=EventStatus.UPCOMING, is_special=False,
                  start_list_url="http://example.com/sl/0"),
            Event(position=1, name="Scratch Race", discipline="scratch_race",
                  status=EventStatus.UPCOMING, is_special=False,
                  start_list_url="http://example.com/sl/1"),
        ]
    return Session(session_id=1, day="Friday", scheduled_start=time(10, 0), events=events)


class TestPredictSessionRacerName:
    def test_rider_match_populated(self):
        session = _make_session()
        riders = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)

        result = predict_session(100, session, racer_name="Alice Smith")
        assert result.event_predictions[0].rider_match is not None
        assert result.event_predictions[0].rider_match.heat == 1
        assert result.event_predictions[1].rider_match is None

    def test_no_match_returns_none(self):
        session = _make_session()
        riders = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)

        result = predict_session(100, session, racer_name="Bob Jones")
        assert all(p.rider_match is None for p in result.event_predictions)

    def test_has_racer_match_flag(self):
        session = _make_session()
        riders = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)

        result = predict_session(100, session, racer_name="Alice Smith")
        assert result.has_racer_match is True

        result_no_match = predict_session(100, session, racer_name="Bob Jones")
        assert result_no_match.has_racer_match is False

    def test_no_racer_name_skips_matching(self):
        session = _make_session()
        riders = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)

        result = predict_session(100, session)
        assert all(p.rider_match is None for p in result.event_predictions)
        assert result.has_racer_match is False


class TestPredictScheduleRacerName:
    def test_racer_name_passthrough(self):
        session = _make_session()
        riders = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)

        result = predict_schedule(100, [session], racer_name="Alice Smith")
        assert result.racer_name == "Alice Smith"
        assert result.match_count == 1
        # Both events have start_list_url; event 0 has cached riders, event 1 does not
        assert result.events_without_start_lists == 1

    def test_total_events_excludes_special(self):
        events = [
            Event(position=0, name="Keirin", discipline="keirin",
                  status=EventStatus.UPCOMING, is_special=False,
                  start_list_url="http://example.com/sl/0"),
            Event(position=1, name="Break", discipline="break_",
                  status=EventStatus.UPCOMING, is_special=True),
        ]
        session = _make_session(events=events)

        result = predict_schedule(100, [session])
        assert result.total_events == 1  # Break is special, excluded

    def test_no_racer_name_defaults(self):
        session = _make_session()
        result = predict_schedule(100, [session])
        assert result.racer_name is None
        assert result.match_count == 0
        assert result.events_without_start_lists == 0

    def test_session_has_racer_match(self):
        session = _make_session()
        riders = [
            RiderEntry(name="Alice Smith", heat=1, normalized_tokens=frozenset({"alice", "smith"})),
        ]
        record_start_list_riders(100, 1, 0, riders)

        result = predict_schedule(100, [session], racer_name="Alice Smith")
        assert result.sessions[0].has_racer_match is True

    def test_events_without_start_lists_count(self):
        """Events with start_list_url but no cached riders are counted."""
        events = [
            Event(position=0, name="Keirin", discipline="keirin",
                  status=EventStatus.UPCOMING, is_special=False,
                  start_list_url="http://example.com/sl/0"),
            Event(position=1, name="Scratch", discipline="scratch_race",
                  status=EventStatus.UPCOMING, is_special=False),  # no start_list_url
        ]
        session = _make_session(events=events)
        # No riders cached for either event, but only event 0 has a start_list_url
        result = predict_schedule(100, [session], racer_name="Alice Smith")
        assert result.events_without_start_lists == 1
