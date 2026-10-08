"""Tests for rider matching and next-race logic in app/predictor.py."""

from datetime import datetime, time, timedelta

import pytest

from app.disciplines import get_per_heat_duration
from app.models import (
    Event,
    EventStatus,
    RiderEntry,
    RiderListEntry,
    RiderMatch,
    Session,
    normalize_rider_name,
)
from app.predictor import (
    _heat_counts,
    _rider_lists,
    _start_list_riders,
    get_rider_match,
    has_start_list_riders,
    predict_schedule,
    predict_session,
    record_heat_count,
    record_start_list_riders,
)

# ── Constants ────────────────────────────────────────────────────────────────

COMP_ID = 99999
SESSION_ID = 1
DISCIPLINE = "keirin"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_caches():
    _start_list_riders.clear()
    _heat_counts.clear()
    _rider_lists.clear()
    yield
    _start_list_riders.clear()
    _heat_counts.clear()
    _rider_lists.clear()


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
        match = get_rider_match(COMP_ID, SESSION_ID, 0, normalize_rider_name("Sean Hall"), None, DISCIPLINE)
        assert match is not None
        assert isinstance(match, RiderMatch)
        assert match.heat == 1

    def test_order_independent_matching(self):
        """'Hall Sean' matches entry 'HALL Sean' (order-independent)."""
        seed_riders(0, [("HALL Sean", 1)])
        match = get_rider_match(COMP_ID, SESSION_ID, 0, normalize_rider_name("Hall Sean"), None, DISCIPLINE)
        assert match is not None
        assert match.heat == 1

    def test_no_match_partial_name(self):
        """Partial name 'Sean' does NOT match 'HALL Sean' (requires full name)."""
        seed_riders(0, [("HALL Sean", 1)])
        match = get_rider_match(COMP_ID, SESSION_ID, 0, normalize_rider_name("Sean"), None, DISCIPLINE)
        assert match is None

    def test_no_match_empty_input(self):
        """Empty frozenset returns None."""
        seed_riders(0, [("HALL Sean", 1)])
        assert get_rider_match(COMP_ID, SESSION_ID, 0, frozenset(), None, DISCIPLINE) is None

    def test_per_heat_predicted_start(self):
        """heat_predicted_start = event_start + (heat - 1) * per_heat_duration."""
        seed_riders(0, [("HALL Sean", 3)])
        record_heat_count(COMP_ID, SESSION_ID, 0, 4)
        event_start = datetime(2024, 6, 1, 18, 30, 0)
        match = get_rider_match(COMP_ID, SESSION_ID, 0, normalize_rider_name("Sean Hall"), event_start, DISCIPLINE)
        assert match is not None
        phd = get_per_heat_duration(DISCIPLINE)
        expected = event_start + timedelta(minutes=(3 - 1) * phd)
        assert match.heat_predicted_start == expected

    def test_single_heat_returns_event_start(self):
        """When heat_count=1, heat_predicted_start equals event_start."""
        seed_riders(0, [("HALL Sean", 1)])
        record_heat_count(COMP_ID, SESSION_ID, 0, 1)
        event_start = datetime(2024, 6, 1, 18, 30, 0)
        match = get_rider_match(COMP_ID, SESSION_ID, 0, normalize_rider_name("Sean Hall"), event_start, DISCIPLINE)
        assert match is not None
        assert match.heat_predicted_start == event_start

    def test_apostrophe_name_matches(self):
        """'OBrien' matches 'O'BRIEN Liam' (apostrophe stripping)."""
        seed_riders(0, [("O'BRIEN Liam", 2)])
        match = get_rider_match(COMP_ID, SESSION_ID, 0, normalize_rider_name("OBrien Liam"), None, DISCIPLINE)
        assert match is not None
        assert match.heat == 2

    def test_diacritics_name_matches(self):
        """'Muller' matches 'MÜLLER Hans' (Unicode NFKD normalization)."""
        seed_riders(0, [("MÜLLER Hans", 1)])
        match = get_rider_match(COMP_ID, SESSION_ID, 0, normalize_rider_name("Muller Hans"), None, DISCIPLINE)
        assert match is not None
        assert match.heat == 1


# ── TestNextRace ─────────────────────────────────────────────────────────────


class TestNextRace:
    """Tests for predict_schedule next_race_* fields and related aggregation."""

    def test_next_race_active_event(self):
        """Active event with rider match sets next_race_is_active=True."""
        # Build a session with: 1 completed event, 1 active event (the match), 1 upcoming
        events = [
            make_event(position=0, name="Elite Men Sprint", discipline="sprint_match", status=EventStatus.COMPLETED),
            make_event(position=1, name="Elite Men Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
            make_event(position=2, name="Elite Women Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        # Seed rider in event at position 1
        seed_riders(1, [("HALL Sean", 1)])

        # now must be set so that completed_count > 0 and has_pending => active_index = 1
        now = datetime(2024, 6, 1, 18, 15, 0)
        result = predict_schedule(COMP_ID, [session], now=now, racer_name="Sean Hall", use_learned=False)

        assert result.next_race is not None
        assert result.next_race.event_name == "Elite Men Keirin"
        assert result.next_race.is_active is True
        assert result.next_race.heat == 1

    def test_next_race_upcoming_event(self):
        """Upcoming event with rider match sets next_race_is_active=False."""
        events = [
            make_event(position=0, name="Elite Men Sprint", discipline="sprint_match", status=EventStatus.UPCOMING),
            make_event(position=1, name="Elite Men Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        # Seed rider only in event at position 1
        seed_riders(1, [("HALL Sean", 1)])

        # No completed events => no active index. now=None means pre-event mode.
        result = predict_schedule(COMP_ID, [session], now=None, racer_name="Sean Hall", use_learned=False)

        assert result.next_race is not None
        assert result.next_race.event_name == "Elite Men Keirin"
        assert result.next_race.is_active is False

    def test_next_race_all_completed(self):
        """When all events are completed, next_race_event_name is None."""
        events = [
            make_event(position=0, name="Elite Men Sprint", discipline="sprint_match", status=EventStatus.COMPLETED),
            make_event(position=1, name="Elite Men Keirin", discipline=DISCIPLINE, status=EventStatus.COMPLETED),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        seed_riders(0, [("HALL Sean", 1)])
        seed_riders(1, [("HALL Sean", 1)])

        result = predict_schedule(COMP_ID, [session], now=None, racer_name="Sean Hall", use_learned=False)

        assert result.next_race is None

    def test_events_without_start_lists_excludes_special(self):
        """Special events (break, ceremony) are excluded from events_without_start_lists."""
        events = [
            make_event(position=0, name="Elite Men Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
            make_event(
                position=1,
                name="Break",
                discipline="break_",
                status=EventStatus.UPCOMING,
                is_special=True,
                start_list_url=None,
            ),
            make_event(
                position=2,
                name="Medal Ceremonies",
                discipline="ceremony",
                status=EventStatus.UPCOMING,
                is_special=True,
                start_list_url=None,
            ),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        # No start lists seeded for any event. Only non-special events should count.
        result = predict_schedule(COMP_ID, [session], now=None, racer_name="Sean Hall", use_learned=False)

        # Only the keirin at position 0 should count as missing a start list
        assert result.events_without_start_lists == 1

    def test_empty_start_list_counts_as_absent(self):
        """A start list that parsed to 0 riders is treated as no start list."""
        record_start_list_riders(COMP_ID, SESSION_ID, 0, [])
        assert has_start_list_riders(COMP_ID, SESSION_ID, 0) is False

        session = make_session(events=[make_event(position=0)])
        sp = predict_session(COMP_ID, session, now=None, racer_name="Sean Hall")
        assert sp.events_without_start_lists == 1

    def test_has_racer_match_on_session_prediction(self):
        """SessionPrediction.has_racer_match is True when a rider match exists."""
        events = [
            make_event(position=0, name="Elite Men Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))
        seed_riders(0, [("HALL Sean", 1)])

        sp = predict_session(COMP_ID, session, now=None, racer_name="Sean Hall", use_learned=False)

        assert sp.has_racer_match is True

    def test_has_pending_racer_match_true_when_upcoming(self):
        """has_pending_racer_match is True when a matched event is still upcoming."""
        events = [
            make_event(position=0, name="Elite Men Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))
        seed_riders(0, [("HALL Sean", 1)])

        sp = predict_session(COMP_ID, session, now=None, racer_name="Sean Hall", use_learned=False)

        assert sp.has_pending_racer_match is True

    def test_has_pending_racer_match_false_when_all_completed(self):
        """has_pending_racer_match is False when all matched events are completed."""
        events = [
            make_event(position=0, name="Elite Men Keirin", discipline=DISCIPLINE, status=EventStatus.COMPLETED),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))
        seed_riders(0, [("HALL Sean", 1)])

        sp = predict_session(COMP_ID, session, now=None, racer_name="Sean Hall", use_learned=False)

        assert sp.has_racer_match is True
        assert sp.has_pending_racer_match is False

    def test_total_events_excludes_special(self):
        """total_events only counts non-is_special events."""
        events = [
            make_event(position=0, name="Elite Men Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
            make_event(position=1, name="Elite Women Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
            make_event(position=2, name="Break", discipline="break_", status=EventStatus.UPCOMING, is_special=True),
            make_event(
                position=3,
                name="End of Session",
                discipline="end_of_session",
                status=EventStatus.UPCOMING,
                is_special=True,
            ),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        result = predict_schedule(COMP_ID, [session], now=None, use_learned=False)

        assert result.total_events == 2

    def test_next_race_active_prioritized_over_upcoming_across_sessions(self):
        """Active match in session 2 takes priority over upcoming match in session 1."""
        session1_events = [
            make_event(position=0, name="Elite Men Sprint", discipline="sprint_match", status=EventStatus.UPCOMING),
        ]
        session1 = make_session(session_id=1, events=session1_events, scheduled_start=time(10, 0))
        seed_riders(0, [("HALL Sean", 1)], session_id=1)

        session2_events = [
            make_event(position=0, name="Elite Women Sprint", discipline="sprint_match", status=EventStatus.COMPLETED),
            make_event(position=1, name="Elite Men Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
        ]
        session2 = make_session(session_id=2, events=session2_events, scheduled_start=time(18, 0))
        seed_riders(1, [("HALL Sean", 2)], session_id=2)

        now = datetime(2024, 6, 1, 18, 15, 0)
        result = predict_schedule(COMP_ID, [session1, session2], now=now, racer_name="Sean Hall", use_learned=False)

        assert result.next_race is not None
        assert result.next_race.event_name == "Elite Men Keirin"
        assert result.next_race.is_active is True
        assert result.next_race.heat == 2

    def test_pre_event_no_active(self):
        """When now is None, all matched events are upcoming (not active)."""
        events = [
            make_event(position=0, name="Elite Men Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
            make_event(position=1, name="Elite Women Keirin", discipline=DISCIPLINE, status=EventStatus.UPCOMING),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))

        seed_riders(0, [("HALL Sean", 1)])
        seed_riders(1, [("HALL Sean", 1)])

        sp = predict_session(COMP_ID, session, now=None, racer_name="Sean Hall", use_learned=False)

        for pred in sp.event_predictions:
            assert pred.is_active is False


# ── TestRiderListFallback ────────────────────────────────────────────────────

ABERS = RiderListEntry(name="ABERS Brian", category="M6064", codes=frozenset({"S", "TS", "TT"}))


def rl_event(
    position: int,
    name: str,
    discipline: str,
    status: EventStatus = EventStatus.NOT_READY,
    start_list_url: str | None = None,
    is_special: bool = False,
) -> Event:
    return make_event(
        position=position,
        name=name,
        discipline=discipline,
        status=status,
        start_list_url=start_list_url,
        is_special=is_special,
    )


def rl_predict(events: list[Event], rider_list: list[RiderListEntry] | None = None, racer: str = "Brian Abers"):
    session = make_session(events=events)
    return predict_schedule(COMP_ID, [session], now=None, racer_name=racer, rider_list=rider_list)


class TestRiderListFallback:
    """Rider List matching inside predict_session / predict_schedule."""

    def test_no_start_list_matches_from_rider_list(self):
        result = rl_predict([rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying")], [ABERS])
        match = result.sessions[0].event_predictions[0].rider_match
        assert match is not None
        assert match.source == "rider_list"
        assert match.tentative is False

    def test_start_list_supersedes_rider_list(self):
        seed_riders(0, [("SMITH John", 1)])
        result = rl_predict(
            [rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying", start_list_url="sl.htm")], [ABERS]
        )
        assert result.sessions[0].event_predictions[0].rider_match is None
        assert result.match_count == 0

    def test_empty_start_list_falls_back(self):
        record_start_list_riders(COMP_ID, SESSION_ID, 0, [])
        result = rl_predict(
            [rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying", start_list_url="sl.htm")], [ABERS]
        )
        match = result.sessions[0].event_predictions[0].rider_match
        assert match is not None
        assert match.source == "rider_list"
        assert result.events_without_start_lists == 1

    def test_special_event_never_matches(self):
        result = rl_predict(
            [rl_event(0, "60-64 Men Sprint Qualifying", "ceremony", is_special=True)],
            [ABERS],
        )
        assert result.sessions[0].event_predictions[0].rider_match is None

    def test_counts_and_pending_flag(self):
        result = rl_predict([rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying")], [ABERS])
        assert result.match_count == 1
        assert result.sessions[0].has_pending_racer_match is True

    def test_unbanded_category_no_match(self):
        elite = RiderListEntry(name="ABERS Brian", category="Elite", codes=frozenset({"S"}))
        result = rl_predict([rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying")], [elite])
        assert result.match_count == 0
        assert result.rider_list_entry is None

    def test_completed_event_matches_same_rules(self):
        result = rl_predict(
            [rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying", status=EventStatus.COMPLETED)], [ABERS]
        )
        match = result.sessions[0].event_predictions[0].rider_match
        assert match is not None
        assert match.source == "rider_list"
        assert result.sessions[0].has_pending_racer_match is False

    def test_no_rider_list_unchanged(self):
        events = [rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying")]
        baseline = rl_predict(events, None)
        assert baseline.match_count == 0
        assert baseline.events_without_start_lists == 1
        assert baseline.rider_list_entry is None
        # A Rider List without the racer leaves the prediction exactly as without one.
        other = RiderListEntry(name="SMITH John", category="M6064", codes=frozenset({"S"}))
        assert rl_predict(events, [other]) == baseline

    def test_tentative_counts(self):
        result = rl_predict(
            [
                rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying"),
                rl_event(1, "60-64 Men Sprint Final Ride 1", "sprint_match"),
            ],
            [ABERS],
        )
        assert result.match_count == 2
        assert result.tentative_match_count == 1

    def test_pending_tentative_sets_pending_flag(self):
        events = [
            rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying", status=EventStatus.COMPLETED),
            rl_event(1, "60-64 Men Sprint Final Ride 1", "sprint_match"),
        ]
        sp = rl_predict(events, [ABERS]).sessions[0]
        assert sp.event_predictions[1].rider_match is not None
        assert sp.event_predictions[1].rider_match.tentative is True
        assert sp.has_pending_racer_match is True

    def test_rider_list_entry_set_only_with_match(self):
        matched = rl_predict([rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying")], [ABERS])
        assert matched.rider_list_entry == ABERS
        unmatched = rl_predict([rl_event(0, "60-64 Men Pursuit Qualifying", "pursuit_3k")], [ABERS])
        assert unmatched.match_count == 0
        assert unmatched.rider_list_entry is None

    def test_tentative_next_race(self):
        events = [
            rl_event(0, "60-64 Men Sprint Qualifying", "sprint_qualifying", status=EventStatus.COMPLETED),
            rl_event(1, "60-64 Men Sprint Final Ride 1", "sprint_match"),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))
        now = datetime(2024, 6, 1, 17, 0, 0)
        result = predict_schedule(COMP_ID, [session], now=now, racer_name="Brian Abers", rider_list=[ABERS])
        nr = result.next_race
        assert nr is not None
        assert nr.event_name == "60-64 Men Sprint Final Ride 1"
        assert nr.tentative is True
        assert nr.heat is None
        assert nr.heat_count is None
        # Event start, not a per-heat time: the slot after the qualifying round.
        pred = result.sessions[0].event_predictions[1]
        assert nr.predicted_start == now.replace(hour=pred.predicted_start.hour, minute=pred.predicted_start.minute)

    def test_active_start_list_match_keeps_priority(self):
        events = [
            make_event(position=0, name="Elite Men Sprint", discipline="sprint_match", status=EventStatus.COMPLETED),
            make_event(position=1, name="60-64 Men Sprint Qualifying", discipline="sprint_qualifying"),
            rl_event(2, "60-64 Men 500m Time Trial Final", "time_trial_500"),
        ]
        seed_riders(1, [("ABERS Brian", 1)])
        session = make_session(events=events, scheduled_start=time(18, 0))
        now = datetime(2024, 6, 1, 18, 15, 0)
        result = predict_schedule(COMP_ID, [session], now=now, racer_name="Brian Abers", rider_list=[ABERS])
        assert result.next_race is not None
        assert result.next_race.event_name == "60-64 Men Sprint Qualifying"
        assert result.next_race.is_active is True
        assert result.next_race.tentative is False
        assert result.match_count == 2

    def test_parallel_qualifier_next_race(self):
        scr = RiderListEntry(name="ABERS Brian", category="M6064", codes=frozenset({"SCR"}))
        events = [
            rl_event(0, "60-64 Men Scratch Race Qualifier 1", "scratch_race"),
            rl_event(1, "60-64 Men Scratch Race Qualifier 2", "scratch_race"),
            rl_event(2, "60-64 Men Scratch Race Final", "scratch_race"),
        ]
        session = make_session(events=events, scheduled_start=time(18, 0))
        result = predict_schedule(
            COMP_ID, [session], now=datetime(2024, 6, 1, 17, 0), racer_name="Brian Abers", rider_list=[scr]
        )
        nr = result.next_race
        assert nr is not None
        assert nr.event_name == "60-64 Men Scratch Race Qualifier 1"
        assert nr.parallel_qualifier is True
        assert nr.tentative is False
        assert nr.predicted_start == datetime(2024, 6, 1, 18, 0)
