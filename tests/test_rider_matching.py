"""Tests for rider name matching and per-heat timing in app/predictor.py."""
from datetime import datetime, time

import pytest

from app.models import Event, EventStatus, RiderEntry, Session
from app.predictor import (
    get_rider_match,
    predict_schedule,
    predict_session,
    record_heat_count,
    record_start_list_riders,
)


def _make_riders() -> list[RiderEntry]:
    return [
        RiderEntry(name="HALL Sean", heat=1, normalized_tokens=frozenset({"hall", "sean"})),
        RiderEntry(name="SMITH Jane", heat=2, normalized_tokens=frozenset({"smith", "jane"})),
        RiderEntry(name="O'BRIEN Liam", heat=1, normalized_tokens=frozenset({"obrien", "liam"})),
        RiderEntry(name="MÜLLER Hans", heat=3, normalized_tokens=frozenset({"muller", "hans"})),
    ]


def _make_event(position: int, status: EventStatus, discipline: str = "keirin",
                is_special: bool = False) -> Event:
    return Event(
        position=position,
        name=f"Event {position}",
        discipline=discipline,
        status=status,
        is_special=is_special,
    )


def _make_session(session_id: int = 1, events: list[Event] | None = None) -> Session:
    if events is None:
        events = [
            _make_event(0, EventStatus.UPCOMING),
            _make_event(1, EventStatus.NOT_READY),
        ]
    return Session(session_id=session_id, day="Friday", scheduled_start=time(8, 0), events=events)


# Use unique competition IDs per test to avoid cache pollution
_CID = 60000


class TestGetRiderMatch:
    def test_case_insensitive_matching(self):
        cid = _CID + 1
        record_start_list_riders(cid, 1, 0, _make_riders())
        match = get_rider_match(cid, 1, 0, "Sean Hall", time(8, 0), "keirin")
        assert match is not None
        assert match.heat == 1

    def test_order_independent_matching(self):
        cid = _CID + 2
        record_start_list_riders(cid, 1, 0, _make_riders())
        match = get_rider_match(cid, 1, 0, "Hall Sean", time(8, 0), "keirin")
        assert match is not None
        assert match.heat == 1

    def test_no_match_for_partial_name(self):
        cid = _CID + 3
        record_start_list_riders(cid, 1, 0, _make_riders())
        match = get_rider_match(cid, 1, 0, "Sean", time(8, 0), "keirin")
        assert match is None

    def test_no_match_for_empty_input(self):
        cid = _CID + 4
        record_start_list_riders(cid, 1, 0, _make_riders())
        assert get_rider_match(cid, 1, 0, "", time(8, 0), "keirin") is None

    def test_no_match_for_whitespace_only(self):
        cid = _CID + 5
        record_start_list_riders(cid, 1, 0, _make_riders())
        assert get_rider_match(cid, 1, 0, "   ", time(8, 0), "keirin") is None

    def test_per_heat_predicted_start(self):
        cid = _CID + 6
        record_start_list_riders(cid, 1, 0, _make_riders())
        record_heat_count(cid, 1, 0, 3)
        match = get_rider_match(cid, 1, 0, "Jane Smith", time(8, 0), "keirin")
        assert match is not None
        assert match.heat == 2
        assert match.heat_count == 3
        # heat 2: event_start + (2-1) * per_heat_duration(keirin=4.5) = 08:00 + 4.5m = 08:04:30
        assert match.heat_predicted_start.hour == 8
        assert match.heat_predicted_start.minute == 4
        assert match.heat_predicted_start.second == 30

    def test_single_heat_event(self):
        cid = _CID + 7
        record_start_list_riders(cid, 1, 0, _make_riders())
        # No heat count recorded → defaults to 1
        match = get_rider_match(cid, 1, 0, "Sean Hall", time(9, 0), "keirin")
        assert match is not None
        assert match.heat_count == 1
        assert match.heat_predicted_start.hour == 9
        assert match.heat_predicted_start.minute == 0

    def test_apostrophe_name_match(self):
        cid = _CID + 8
        record_start_list_riders(cid, 1, 0, _make_riders())
        match = get_rider_match(cid, 1, 0, "OBrien Liam", time(8, 0), "keirin")
        assert match is not None

    def test_diacritics_name_match(self):
        cid = _CID + 9
        record_start_list_riders(cid, 1, 0, _make_riders())
        match = get_rider_match(cid, 1, 0, "Muller Hans", time(8, 0), "keirin")
        assert match is not None


class TestNextRaceFields:
    def test_active_event_sets_next_race_is_active(self):
        cid = _CID + 20
        events = [
            _make_event(0, EventStatus.COMPLETED),
            _make_event(1, EventStatus.UPCOMING),
        ]
        record_start_list_riders(cid, 1, 0, _make_riders())
        record_start_list_riders(cid, 1, 1, _make_riders())
        session = _make_session(events=events)
        schedule = predict_schedule(cid, [session], now=datetime(2024, 1, 1, 8, 15), racer_name="Sean Hall")
        assert schedule.next_race_event_name == "Event 1"
        assert schedule.next_race_is_active is True

    def test_upcoming_event_sets_next_race_not_active(self):
        cid = _CID + 21
        events = [
            _make_event(0, EventStatus.UPCOMING),
            _make_event(1, EventStatus.NOT_READY),
        ]
        record_start_list_riders(cid, 1, 0, _make_riders())
        record_start_list_riders(cid, 1, 1, _make_riders())
        session = _make_session(events=events)
        # No now → no active events
        schedule = predict_schedule(cid, [session], now=None, racer_name="Sean Hall")
        assert schedule.next_race_event_name == "Event 0"
        assert schedule.next_race_is_active is False

    def test_all_completed_no_next_race(self):
        cid = _CID + 22
        events = [
            _make_event(0, EventStatus.COMPLETED),
            _make_event(1, EventStatus.COMPLETED),
        ]
        record_start_list_riders(cid, 1, 0, _make_riders())
        record_start_list_riders(cid, 1, 1, _make_riders())
        session = _make_session(events=events)
        schedule = predict_schedule(cid, [session], now=datetime(2024, 1, 1, 10, 0), racer_name="Sean Hall")
        assert schedule.next_race_event_name is None


class TestEventsWithoutStartLists:
    def test_count_excludes_special_events(self):
        cid = _CID + 30
        events = [
            _make_event(0, EventStatus.UPCOMING),
            _make_event(1, EventStatus.UPCOMING, is_special=True),
            _make_event(2, EventStatus.NOT_READY),
        ]
        # Only seed event 0 with riders; event 2 has no start list
        record_start_list_riders(cid, 1, 0, _make_riders())
        session = _make_session(events=events)
        schedule = predict_schedule(cid, [session], racer_name="Sean Hall")
        # Event 1 is special (excluded), event 2 has no start list
        assert schedule.events_without_start_lists == 1


class TestTotalEvents:
    def test_excludes_special_events(self):
        cid = _CID + 40
        events = [
            _make_event(0, EventStatus.UPCOMING),
            _make_event(1, EventStatus.UPCOMING, is_special=True),
            _make_event(2, EventStatus.UPCOMING, is_special=True),
            _make_event(3, EventStatus.NOT_READY),
            _make_event(4, EventStatus.NOT_READY),
        ]
        for i in range(5):
            record_start_list_riders(cid, 1, i, _make_riders())
        session = _make_session(events=events)
        schedule = predict_schedule(cid, [session], racer_name="Sean Hall")
        assert schedule.total_events == 3  # 5 events - 2 special


class TestHasRacerMatch:
    def test_session_has_racer_match(self):
        cid = _CID + 50
        events = [_make_event(0, EventStatus.UPCOMING)]
        record_start_list_riders(cid, 1, 0, _make_riders())
        session = _make_session(events=events)
        sp = predict_session(cid, session, racer_name="Sean Hall")
        assert sp.has_racer_match is True

    def test_session_no_racer_match(self):
        cid = _CID + 51
        events = [_make_event(0, EventStatus.UPCOMING)]
        record_start_list_riders(cid, 1, 0, _make_riders())
        session = _make_session(events=events)
        sp = predict_session(cid, session, racer_name="Unknown Person")
        assert sp.has_racer_match is False


class TestPreEventEdgeCase:
    def test_now_none_all_matched_upcoming_not_active(self):
        """When now is None, matched events are upcoming (not active)."""
        cid = _CID + 60
        events = [
            _make_event(0, EventStatus.UPCOMING),
            _make_event(1, EventStatus.UPCOMING),
        ]
        record_start_list_riders(cid, 1, 0, _make_riders())
        record_start_list_riders(cid, 1, 1, _make_riders())
        session = _make_session(events=events)
        schedule = predict_schedule(cid, [session], now=None, racer_name="Sean Hall")
        assert schedule.match_count == 2
        assert schedule.next_race_is_active is False
        # No event should be active
        for sp in schedule.sessions:
            for pred in sp.event_predictions:
                assert pred.is_active is False
