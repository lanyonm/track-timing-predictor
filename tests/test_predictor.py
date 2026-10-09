"""Tests for app/predictor.py prediction logic."""

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.disciplines import (
    BUNCH_RACE_KMH,
    CEREMONY_BASE_MINUTES,
    CEREMONY_PER_PODIUM_MINUTES,
    CHANGEOVER_MINUTES,
    DEFAULT_DURATIONS,
    LIVE_BUNCH_CHANGEOVER_MINUTES,
    PER_HEAT_DURATIONS,
    SPRINT_DECIDER_MINUTES,
    SPRINT_DECIDER_RATE,
    get_changeover,
)
from app.models import Event, EventStatus, Session
from app.parser import parse_schedule
from app.predictor import (
    _add_minutes,
    _compute_delay,
    bunch_changeover,
    latest_live_generated_time,
    load_learned_durations,
    predict_schedule,
    predict_session,
    record_generated_time,
    record_heat_count,
    record_live_heat,
    record_observed_duration,
    record_race_distance,
    record_sprint_deciders,
    save_live_durations,
    update_status_cache,
)

SAMPLE_PATH = Path(__file__).parent / "fixtures" / "sample-event-output.json"


@pytest.fixture(scope="module")
def sessions():
    with SAMPLE_PATH.open() as f:
        data = json.load(f)
    return parse_schedule(data)


# ── _add_minutes ──────────────────────────────────────────────────────────────


class TestAddMinutes:
    def test_basic_addition(self):
        assert _add_minutes(time(8, 15), 10) == time(8, 25)

    def test_hour_rollover(self):
        assert _add_minutes(time(8, 50), 20) == time(9, 10)

    def test_zero_minutes(self):
        assert _add_minutes(time(9, 0), 0) == time(9, 0)

    def test_fractional_minutes(self):
        result = _add_minutes(time(8, 0), 8.5)
        assert result == time(8, 8, 30)

    def test_midnight_wrap(self):
        result = _add_minutes(time(23, 50), 20)
        assert result == time(0, 10)


# ── _compute_delay ────────────────────────────────────────────────────────────


class TestComputeDelay:
    def _make_session(self, start_hour: int, start_min: int) -> Session:
        return Session(
            session_id=1,
            day="Friday",
            scheduled_start=time(start_hour, start_min),
            events=[],
        )

    def test_no_delay_when_on_schedule(self):
        """If 2 events of 10 min each ran in 20 min, delay is 0."""
        session = self._make_session(8, 0)
        durations = [10.0, 10.0, 10.0]
        # 2 completed events, session started at 08:00, now is 08:20
        now = datetime(2024, 1, 1, 8, 20)
        delay = _compute_delay(session, durations, 2, now)
        assert delay == pytest.approx(0.0, abs=0.1)

    def test_positive_delay_when_behind(self):
        """If 2 events of 10 min each took 30 min total, delay is +10."""
        session = self._make_session(8, 0)
        durations = [10.0, 10.0, 10.0]
        now = datetime(2024, 1, 1, 8, 30)
        delay = _compute_delay(session, durations, 2, now)
        assert delay == pytest.approx(10.0, abs=0.1)

    def test_negative_delay_when_ahead(self):
        """If 2 events of 10 min each took only 15 min, delay is -5."""
        session = self._make_session(8, 0)
        durations = [10.0, 10.0, 10.0]
        now = datetime(2024, 1, 1, 8, 15)
        delay = _compute_delay(session, durations, 2, now)
        assert delay == pytest.approx(-5.0, abs=0.1)

    def test_clamped_to_max(self):
        """
        Delay is clamped to +120 min when the session is still within its window
        but running very far behind.

        Scenario: 10 events × 20min each (total_est=200min). After 2 events the
        schedule says 08:40, but it's now 10:50 (actual_elapsed=170min).
        actual_elapsed=170 < total_est+60=260 → within window.
        raw_delay = 170 - 40 = 130 → clamped to 120.
        """
        session = self._make_session(8, 0)
        durations = [20.0] * 10  # total_est = 200 min
        now = datetime(2024, 1, 1, 10, 50)  # actual_elapsed = 170 min
        delay = _compute_delay(session, durations, 2, now)
        assert delay == pytest.approx(120.0)

    def test_clamped_to_min(self):
        """Delay cannot go below -30 minutes."""
        session = self._make_session(8, 0)
        durations = [60.0, 60.0]
        # Very fast: 2 events of 60 min each done in only 10 min → wildly ahead
        now = datetime(2024, 1, 1, 8, 10)
        delay = _compute_delay(session, durations, 2, now)
        assert delay == pytest.approx(-30.0)

    def test_zero_actual_elapsed_returns_zero(self):
        """If now is at or before session start, return 0."""
        session = self._make_session(9, 0)
        durations = [10.0]
        now = datetime(2024, 1, 1, 8, 55)
        delay = _compute_delay(session, durations, 1, now)
        assert delay == 0.0

    def test_no_delay_when_past_session_window(self):
        """
        When viewing results hours after a session ended, actual_elapsed can
        far exceed total_est, which would naively produce a large positive delay
        clamped at +120 min (2 hours). Instead, return 0 so post-event
        predictions show scheduled times rather than an inflated delay.

        Scenario: session of 3×10min events (total_est=30min) started at 08:00.
        Viewing results at 17:00 → actual_elapsed = 540min >> total_est+60=90min.
        """
        session = self._make_session(8, 0)
        durations = [10.0, 10.0, 10.0]
        # 2 events completed, viewing results 9 hours later
        now = datetime(2024, 1, 1, 17, 0)
        delay = _compute_delay(session, durations, 2, now)
        assert delay == 0.0

    def test_delay_still_applied_during_overrun(self):
        """
        A session that is running genuinely late (within total_est + 60min buffer)
        should still have delay applied.

        Scenario: session of 3×10min events (total_est=30min) started at 08:00.
        After 2 events the session should be at 08:20 but it's now 08:50 (+30min delay).
        actual_elapsed=50min < total_est+60=90min → delay computed.
        """
        session = self._make_session(8, 0)
        durations = [10.0, 10.0, 10.0]
        now = datetime(2024, 1, 1, 8, 50)
        delay = _compute_delay(session, durations, 2, now)
        assert delay == pytest.approx(30.0, abs=0.1)


# ── predict_session ───────────────────────────────────────────────────────────


# Finish-Time races swap the static changeover in their defaults for the live one, which is
# LIVE_BUNCH_CHANGEOVER_MINUTES until a competition has enough races to calibrate it.
BUNCH_SHIFT = LIVE_BUNCH_CHANGEOVER_MINUTES - CHANGEOVER_MINUTES["scratch_race"]
SCRATCH_SLOT = DEFAULT_DURATIONS["scratch_race"] + BUNCH_SHIFT


def _make_event(position: int, status: EventStatus, discipline: str = "scratch_race") -> Event:
    return Event(
        position=position,
        name=f"Event {position}",
        discipline=discipline,
        status=status,
        is_special=False,
    )


class TestPredictSession:
    def test_pre_event_first_event_at_scheduled_start(self):
        """Without any completed events, first event starts at session scheduled start."""
        session = Session(
            session_id=1,
            day="Friday",
            scheduled_start=time(8, 15),
            events=[
                _make_event(0, EventStatus.NOT_READY),
                _make_event(1, EventStatus.NOT_READY),
            ],
        )
        sp = predict_session(99, session, now=None)
        assert sp.event_predictions[0].predicted_start == time(8, 15)

    def test_pre_event_second_event_offset_by_duration(self):
        """Second event starts at session start + duration of first event."""
        session = Session(
            session_id=1,
            day="Friday",
            scheduled_start=time(8, 0),
            events=[
                _make_event(0, EventStatus.NOT_READY, "scratch_race"),  # default 12 min
                _make_event(1, EventStatus.NOT_READY, "scratch_race"),
            ],
        )
        sp = predict_session(99, session, now=None)
        first_start = sp.event_predictions[0].predicted_start
        second_start = sp.event_predictions[1].predicted_start
        gap = (second_start.hour * 60 + second_start.minute) - (first_start.hour * 60 + first_start.minute)
        assert gap == pytest.approx(12, abs=1)

    def test_all_completed_no_delay_applied(self):
        """No delay is applied when all events are already completed."""
        session = Session(
            session_id=1,
            day="Saturday",
            scheduled_start=time(8, 15),
            events=[
                _make_event(0, EventStatus.COMPLETED),
                _make_event(1, EventStatus.COMPLETED),
            ],
        )
        sp = predict_session(99, session, now=datetime(2024, 1, 1, 10, 0))
        assert sp.observed_delay_minutes == 0.0

    def test_no_completed_no_delay_applied(self):
        """No delay when no events are completed (pre-event)."""
        session = Session(
            session_id=1,
            day="Sunday",
            scheduled_start=time(8, 0),
            events=[
                _make_event(0, EventStatus.NOT_READY),
                _make_event(1, EventStatus.UPCOMING),
            ],
        )
        sp = predict_session(99, session, now=datetime(2024, 1, 1, 8, 30))
        assert sp.observed_delay_minutes == 0.0

    def test_live_delay_shifts_future_predictions(self):
        """When session is running 10 min behind, future events shift by +10 min."""
        # Session starts 08:00. 1 event of 10 min. Now = 08:20 (10 min behind).
        session = Session(
            session_id=1,
            day="Friday",
            scheduled_start=time(8, 0),
            events=[
                _make_event(0, EventStatus.COMPLETED, "scratch_race"),
                _make_event(1, EventStatus.UPCOMING, "scratch_race"),
            ],
        )
        now = datetime(2024, 1, 1, 8, 20)
        sp = predict_session(99, session, now=now)
        # Event 1 (upcoming) should be at 08:00 + 12min_est + ~8min_delay ≈ 08:20
        upcoming_start = sp.event_predictions[1].predicted_start
        # Should be later than the scheduled 08:12 (no delay) start
        scheduled_no_delay = _add_minutes(time(8, 0), 12)
        upcoming_minutes = upcoming_start.hour * 60 + upcoming_start.minute
        no_delay_minutes = scheduled_no_delay.hour * 60 + scheduled_no_delay.minute
        assert upcoming_minutes > no_delay_minutes

    def test_prediction_count_matches_event_count(self, sessions):
        sp = predict_session(26008, sessions[0], now=None)
        assert len(sp.event_predictions) == len(sessions[0].events)

    def test_completed_events_not_shifted_by_delay(self):
        """
        Completed events must show their estimated historical start times —
        delay_minutes must NOT be added to them. Only upcoming events shift.
        Session: 08:15 start, 2 events (scratch_race = 12 min each).
        1st event COMPLETED, 2nd UPCOMING. now = 08:45 → ~18 min behind.
        Expected: event[0] predicted at 08:15 (no delay); event[1] shifted.
        """
        session = Session(
            session_id=1,
            day="Sunday",
            scheduled_start=time(8, 15),
            events=[
                _make_event(0, EventStatus.COMPLETED, "scratch_race"),
                _make_event(1, EventStatus.UPCOMING, "scratch_race"),
            ],
        )
        # now = 08:45 → actual_elapsed = 30 min; est_elapsed = 12 min → delay ≈ 18 min
        now = datetime(2024, 1, 1, 8, 45)
        sp = predict_session(99, session, now=now)

        completed_pred = sp.event_predictions[0]
        upcoming_pred = sp.event_predictions[1]

        # Completed event must start exactly at the session scheduled start.
        assert completed_pred.predicted_start == time(8, 15)
        assert not completed_pred.is_adjusted

        # Upcoming event must be shifted by the delay (starts after 08:27 = 08:15+12).
        upcoming_minutes = upcoming_pred.predicted_start.hour * 60 + upcoming_pred.predicted_start.minute
        assert upcoming_minutes > 8 * 60 + 27
        assert upcoming_pred.is_adjusted

    def test_three_events_only_upcoming_shifted(self):
        """
        With two completed events and one upcoming, only the upcoming event is
        shifted; both completed events start at their estimated historical times.
        """
        session = Session(
            session_id=2,
            day="Sunday",
            scheduled_start=time(9, 0),
            events=[
                _make_event(0, EventStatus.COMPLETED, "scratch_race"),  # 12 min
                _make_event(1, EventStatus.COMPLETED, "scratch_race"),  # 12 min
                _make_event(2, EventStatus.UPCOMING, "scratch_race"),
            ],
        )
        # est_elapsed = 24 min; now = 09:34 → actual_elapsed = 34 → delay = 10 min
        now = datetime(2024, 1, 1, 9, 34)
        sp = predict_session(99, session, now=now)

        assert sp.event_predictions[0].predicted_start == time(9, 0)
        assert sp.event_predictions[1].predicted_start == _add_minutes(time(9, 0), SCRATCH_SLOT)
        assert not sp.event_predictions[0].is_adjusted
        assert not sp.event_predictions[1].is_adjusted
        # Third event should be shifted by ~10 min
        upcoming_minutes = (
            sp.event_predictions[2].predicted_start.hour * 60 + sp.event_predictions[2].predicted_start.minute
        )
        assert upcoming_minutes > 9 * 60 + 24  # later than 09:24 (no-delay time)
        assert sp.event_predictions[2].is_adjusted


# ── heat count duration ────────────────────────────────────────────────────────


class TestHeatCountDuration:
    """predict_session uses heat_count × per_heat_duration when heat count is cached."""

    EVENT_ID = 99999  # unique to avoid polluting other tests' caches

    def _make_session(self) -> Session:
        return Session(
            session_id=99,
            day="Friday",
            scheduled_start=time(8, 0),
            events=[
                _make_event(0, EventStatus.NOT_READY, "keirin"),
                _make_event(1, EventStatus.NOT_READY, "keirin"),
            ],
        )

    def test_heat_count_overrides_default(self):
        """With 3 keirin heats, duration = 3 × per_heat + changeover."""
        record_heat_count(self.EVENT_ID, 99, 0, 3)
        session = self._make_session()
        sp = predict_session(self.EVENT_ID, session, now=None)
        expected = 3 * PER_HEAT_DURATIONS["keirin"] + CHANGEOVER_MINUTES["keirin"]
        assert sp.event_predictions[0].estimated_duration_minutes == pytest.approx(expected)
        assert sp.event_predictions[0].heat_count == 3
        assert not sp.event_predictions[0].is_observed

    def test_heat_count_reflected_in_second_event_start(self):
        """Second event start shifts by the heat-count-based duration of the first."""
        record_heat_count(self.EVENT_ID, 99, 0, 2)
        session = self._make_session()
        sp = predict_session(self.EVENT_ID, session, now=None)
        expected_duration = 2 * PER_HEAT_DURATIONS["keirin"] + CHANGEOVER_MINUTES["keirin"]
        second_start = sp.event_predictions[1].predicted_start
        assert second_start == _add_minutes(time(8, 0), expected_duration)

    def test_no_heat_count_uses_default(self):
        """Without a cached heat count, falls back to DEFAULT_DURATIONS."""
        session = Session(
            session_id=100,
            day="Saturday",
            scheduled_start=time(9, 0),
            events=[_make_event(0, EventStatus.NOT_READY, "sprint_qualifying")],
        )
        sp = predict_session(self.EVENT_ID, session, now=None)
        assert sp.event_predictions[0].heat_count is None


# ── is_active detection ────────────────────────────────────────────────────────


class TestIsActive:
    def _make_session(self, statuses: list[EventStatus]) -> Session:
        return Session(
            session_id=1,
            day="Friday",
            scheduled_start=time(8, 0),
            events=[_make_event(i, s) for i, s in enumerate(statuses)],
        )

    def test_active_is_first_pending_when_session_in_progress(self):
        """First non-COMPLETED event is active when session has started."""
        session = self._make_session(
            [
                EventStatus.COMPLETED,
                EventStatus.UPCOMING,
                EventStatus.NOT_READY,
            ]
        )
        sp = predict_session(99, session, now=datetime(2024, 1, 1, 8, 15))
        assert not sp.event_predictions[0].is_active
        assert sp.event_predictions[1].is_active
        assert not sp.event_predictions[2].is_active

    def test_no_active_when_all_events_not_ready(self):
        """No event is active before the session starts."""
        session = self._make_session(
            [
                EventStatus.NOT_READY,
                EventStatus.NOT_READY,
            ]
        )
        sp = predict_session(99, session, now=datetime(2024, 1, 1, 8, 15))
        assert not any(p.is_active for p in sp.event_predictions)

    def test_no_active_when_all_events_completed(self):
        """No event is active once the session is fully complete."""
        session = self._make_session(
            [
                EventStatus.COMPLETED,
                EventStatus.COMPLETED,
            ]
        )
        sp = predict_session(99, session, now=datetime(2024, 1, 1, 10, 0))
        assert not any(p.is_active for p in sp.event_predictions)

    def test_no_active_without_now(self):
        """is_active is not set when now is None (pre-event mode)."""
        session = self._make_session(
            [
                EventStatus.COMPLETED,
                EventStatus.UPCOMING,
            ]
        )
        sp = predict_session(99, session, now=None)
        assert not any(p.is_active for p in sp.event_predictions)

    def test_exactly_one_active_at_a_time(self):
        """At most one event is active per session."""
        session = self._make_session(
            [
                EventStatus.COMPLETED,
                EventStatus.COMPLETED,
                EventStatus.UPCOMING,
                EventStatus.NOT_READY,
            ]
        )
        sp = predict_session(99, session, now=datetime(2024, 1, 1, 8, 30))
        active_count = sum(1 for p in sp.event_predictions if p.is_active)
        assert active_count == 1
        assert sp.event_predictions[2].is_active

    def test_live_url_fallback_marks_active_when_no_completed_events(self):
        """
        An event with live_url is marked active even when it is the first event
        in the session (completed_count == 0). The LIVE button on the schedule is
        the definitive signal that the event is running right now.
        """
        session = Session(
            session_id=1,
            day="Friday",
            scheduled_start=time(8, 0),
            events=[
                Event(
                    position=0,
                    name="Keirin R1",
                    discipline="keirin",
                    status=EventStatus.UPCOMING,
                    is_special=False,
                    live_url="liveresults.php?EventId=1",
                ),
                Event(
                    position=1,
                    name="Keirin R2",
                    discipline="keirin",
                    status=EventStatus.NOT_READY,
                    is_special=False,
                ),
            ],
        )
        sp = predict_session(99, session, now=datetime(2024, 1, 1, 8, 15))
        assert sp.event_predictions[0].is_active
        assert not sp.event_predictions[1].is_active

    def test_live_url_fallback_not_used_when_completed_events_exist(self):
        """
        When the status-based active_index already points to an event, the
        live_url fallback is not used (the status-based event takes priority).
        """
        session = Session(
            session_id=1,
            day="Friday",
            scheduled_start=time(8, 0),
            events=[
                Event(
                    position=0,
                    name="Event 0",
                    discipline="scratch_race",
                    status=EventStatus.COMPLETED,
                    is_special=False,
                ),
                Event(
                    position=1,
                    name="Event 1",
                    discipline="keirin",
                    status=EventStatus.UPCOMING,
                    is_special=False,
                    live_url="liveresults.php?EventId=1",
                ),
                Event(
                    position=2,
                    name="Event 2",
                    discipline="keirin",
                    status=EventStatus.NOT_READY,
                    is_special=False,
                ),
            ],
        )
        sp = predict_session(99, session, now=datetime(2024, 1, 1, 8, 15))
        # Status-based: event 1 is active (completed_count=1)
        assert not sp.event_predictions[0].is_active
        assert sp.event_predictions[1].is_active
        assert not sp.event_predictions[2].is_active


# ── active_heat estimation ─────────────────────────────────────────────────────


class TestActiveHeat:
    """
    Keirin per_heat_duration = 5.0 min.
    Session starts 08:00. First event (scratch_race, default 12 min) is COMPLETED.
    Second event (keirin, 3 heats) is UPCOMING and active.
    Predicted start of keirin = 08:00 + 12 min = 08:12 (no delay in these tests).
    """

    EVENT_ID = 88888

    def _make_session(self) -> Session:
        return Session(
            session_id=88,
            day="Saturday",
            scheduled_start=time(8, 0),
            events=[
                _make_event(0, EventStatus.COMPLETED, "scratch_race"),
                _make_event(1, EventStatus.UPCOMING, "keirin"),
                _make_event(2, EventStatus.NOT_READY, "scratch_race"),
            ],
        )

    def _setup(self) -> None:
        record_heat_count(self.EVENT_ID, 88, 1, 3)  # 3 keirin heats

    def test_active_heat_first_heat_at_start(self):
        """At predicted start time (elapsed=0), active_heat should be 1."""
        self._setup()
        session = self._make_session()
        # no delay: keirin starts at 08:12; now = 08:12 → elapsed=0
        sp = predict_session(self.EVENT_ID, session, now=datetime(2024, 1, 1, 8, 12))
        active = sp.event_predictions[1]
        assert active.is_active
        assert active.active_heat == 1

    def test_active_heat_second_heat(self):
        """After 6 min elapsed (> 5 min/heat), active_heat should be 2."""
        self._setup()
        session = self._make_session()
        # keirin starts at 08:12; now = 08:18 → elapsed=6 min → heat 2
        sp = predict_session(self.EVENT_ID, session, now=datetime(2024, 1, 1, 8, 18))
        active = sp.event_predictions[1]
        assert active.active_heat == 2

    def test_active_heat_third_heat(self):
        """After 11 min elapsed (> 10 min), active_heat should be 3."""
        self._setup()
        session = self._make_session()
        # keirin starts at 08:12; now = 08:23 → elapsed=11 min → heat 3
        sp = predict_session(self.EVENT_ID, session, now=datetime(2024, 1, 1, 8, 23))
        active = sp.event_predictions[1]
        assert active.active_heat == 3

    def test_active_heat_clamped_to_heat_count(self):
        """active_heat never exceeds heat_count even if overrunning."""
        self._setup()
        session = self._make_session()
        # keirin starts at 08:12; now = 08:40 → elapsed=28 min → would be heat 6, clamped to 3
        sp = predict_session(self.EVENT_ID, session, now=datetime(2024, 1, 1, 8, 40))
        active = sp.event_predictions[1]
        assert active.active_heat == 3

    def test_active_heat_none_without_heat_count(self):
        """active_heat is None when no heat count is available."""
        session = self._make_session()
        # No heat count recorded for position 1 in this unique session
        sp = predict_session(self.EVENT_ID + 1, session, now=datetime(2024, 1, 1, 8, 20))
        active = sp.event_predictions[1]
        assert active.is_active
        assert active.active_heat is None

    def test_active_heat_none_for_non_active_events(self):
        """active_heat is only set for the active event."""
        self._setup()
        session = self._make_session()
        sp = predict_session(self.EVENT_ID, session, now=datetime(2024, 1, 1, 8, 18))
        assert sp.event_predictions[0].active_heat is None
        assert sp.event_predictions[2].active_heat is None


# ── live heat priority ─────────────────────────────────────────────────────────


class TestLiveHeatPriority:
    """
    Verify that live page heat data takes priority over the time-based fallback.

    Session: starts 08:00, scratch_race (12 min default) COMPLETED, then keirin UPCOMING.
    Keirin per_heat_duration = 5.0 min. Heat count = 3.
    Without a live heat, time-based elapsed = now - 08:00 - 12 min.
    """

    EVENT_ID = 77777

    def _make_session(self) -> Session:
        return Session(
            session_id=77,
            day="Sunday",
            scheduled_start=time(8, 0),
            events=[
                _make_event(0, EventStatus.COMPLETED, "scratch_race"),
                _make_event(1, EventStatus.UPCOMING, "keirin"),
                _make_event(2, EventStatus.NOT_READY, "scratch_race"),
            ],
        )

    def test_live_heat_overrides_time_based_estimate(self):
        """
        With 1 completed heat on the live page, active_heat=2 (completed+1).
        At event start the time-based fallback would give heat 1 (elapsed=0),
        but the live page correctly shows heat 1 is already done.
        """
        record_heat_count(self.EVENT_ID, 77, 1, 3)
        record_live_heat(self.EVENT_ID, 77, 1, 1)  # 1 heat completed
        session = self._make_session()
        # At 08:12, time-based gives heat 1; live: 1 done → active = 2
        sp = predict_session(self.EVENT_ID, session, now=datetime(2024, 1, 1, 8, 12))
        active = sp.event_predictions[1]
        assert active.is_active
        assert active.active_heat == 2

    def test_live_heat_takes_priority_over_elapsed_time(self):
        """
        With 1 completed heat on the live page, active_heat=2 even when
        elapsed time would estimate heat 3.
        """
        record_heat_count(self.EVENT_ID, 77, 1, 3)
        record_live_heat(self.EVENT_ID, 77, 1, 1)  # 1 heat completed
        session = self._make_session()
        # At 08:23, elapsed_in_active=11 min → time-based heat 3; live: 1 done → active=2
        sp = predict_session(self.EVENT_ID, session, now=datetime(2024, 1, 1, 8, 23))
        active = sp.event_predictions[1]
        assert active.active_heat == 2

    def test_no_live_heat_falls_back_to_time_based(self):
        """When no live heat is cached, time-based estimate is used instead."""
        fallback_id = self.EVENT_ID + 100  # no live heat recorded for this ID
        record_heat_count(fallback_id, 77, 1, 3)
        session = self._make_session()
        # At 08:18, elapsed_in_active = 18-12 = 6 min → int(6/5)+1 = 2
        sp = predict_session(fallback_id, session, now=datetime(2024, 1, 1, 8, 18))
        active = sp.event_predictions[1]
        assert active.is_active
        assert active.active_heat == 2


# ── generated-time derived durations ──────────────────────────────────────────


class TestGeneratedTimeDuration:
    """
    Consecutive result-page Generated timestamps are differenced to produce
    actual inter-event slot durations, replacing estimates for all disciplines.
    Generated marks the end of an event, so each gap belongs to the later event.

    Session: starts 08:00 with three events (positions 10, 11, 12).
    Generated times:  pos 10 → 08:10, pos 11 → 08:22, pos 12 → 08:35.
    Expected durations: pos 10 = default (no previous event to diff against),
    pos 11 = 12 min, pos 12 = 13 min.
    """

    EVENT_ID = 55555

    def _make_session(self, statuses: list[EventStatus]) -> Session:
        return Session(
            session_id=55,
            day="Saturday",
            scheduled_start=time(8, 0),
            events=[
                Event(position=10, name="E10", discipline="scratch_race", status=statuses[0], is_special=False),
                Event(position=11, name="E11", discipline="keirin", status=statuses[1], is_special=False),
                Event(position=12, name="E12", discipline="keirin", status=statuses[2], is_special=False),
            ],
        )

    def _setup(self) -> None:
        record_generated_time(self.EVENT_ID, 55, 10, datetime(2026, 1, 1, 8, 10, 0))
        record_generated_time(self.EVENT_ID, 55, 11, datetime(2026, 1, 1, 8, 22, 0))
        record_generated_time(self.EVENT_ID, 55, 12, datetime(2026, 1, 1, 8, 35, 0))

    def test_first_event_falls_back_to_default(self):
        """pos 10 has no previous generated time → uses the scratch_race default."""
        self._setup()
        session = self._make_session([EventStatus.COMPLETED, EventStatus.COMPLETED, EventStatus.UPCOMING])
        sp = predict_session(self.EVENT_ID, session, now=None)
        assert sp.event_predictions[0].estimated_duration_minutes == pytest.approx(SCRATCH_SLOT)
        assert sp.event_predictions[0].is_observed is False

    def test_generated_duration_used_for_middle_event(self):
        """pos 11 duration = generated(11) - generated(10) = 12 min."""
        self._setup()
        session = self._make_session([EventStatus.COMPLETED, EventStatus.COMPLETED, EventStatus.UPCOMING])
        sp = predict_session(self.EVENT_ID, session, now=None)
        assert sp.event_predictions[1].estimated_duration_minutes == pytest.approx(12.0)

    def test_generated_duration_used_for_last_event(self):
        """pos 12 duration = generated(12) - generated(11) = 13 min."""
        self._setup()
        session = self._make_session([EventStatus.COMPLETED, EventStatus.COMPLETED, EventStatus.UPCOMING])
        sp = predict_session(self.EVENT_ID, session, now=None)
        assert sp.event_predictions[2].estimated_duration_minutes == pytest.approx(13.0)

    def test_generated_duration_marked_as_observed(self):
        """Generated-time derived durations are flagged is_observed=True."""
        self._setup()
        session = self._make_session([EventStatus.COMPLETED, EventStatus.COMPLETED, EventStatus.UPCOMING])
        sp = predict_session(self.EVENT_ID, session, now=None)
        assert sp.event_predictions[0].is_observed is False
        assert sp.event_predictions[1].is_observed is True
        assert sp.event_predictions[2].is_observed is True

    def test_generated_duration_shifts_subsequent_predictions(self):
        """Accurate duration for event 1 propagates to event 2's predicted start."""
        self._setup()
        session = self._make_session([EventStatus.COMPLETED, EventStatus.COMPLETED, EventStatus.UPCOMING])
        sp = predict_session(self.EVENT_ID, session, now=None)
        # Event 2 starts at 08:00 + scratch default + 12 (generated)
        assert sp.event_predictions[2].predicted_start == _add_minutes(time(8, 0), SCRATCH_SLOT + 12)

    def test_observed_takes_priority_over_generated(self):
        """Finish-Time observed duration overrides the generated-time derived one."""
        from app.predictor import record_observed_duration

        self._setup()
        # Record an observed duration of 9.0 min for pos 10 (overrides 12-min generated)
        record_observed_duration(self.EVENT_ID, 55, 10, 7.0, "scratch_race", "E10")
        session = self._make_session([EventStatus.COMPLETED, EventStatus.COMPLETED, EventStatus.UPCOMING])
        sp = predict_session(self.EVENT_ID, session, now=None)
        # slot = Finish Time + the live (uncalibrated) bunch changeover
        assert sp.event_predictions[0].estimated_duration_minutes == pytest.approx(7.0 + LIVE_BUNCH_CHANGEOVER_MINUTES)

    def test_implausible_gap_falls_back_to_default(self):
        """A generated-time gap > 2× the expected slot duration is discarded."""
        # Override pos 11 to be 3 hours after pos 10 (implausible for keirin ~6.5 min)
        record_generated_time(self.EVENT_ID + 1, 55, 10, datetime(2026, 1, 1, 8, 0, 0))
        record_generated_time(self.EVENT_ID + 1, 55, 11, datetime(2026, 1, 1, 11, 0, 0))  # 180 min gap
        session = self._make_session([EventStatus.COMPLETED, EventStatus.COMPLETED, EventStatus.UPCOMING])
        sp = predict_session(self.EVENT_ID + 1, session, now=None)
        # The gap belongs to pos 11; falls back to keirin default = 6.5 min
        assert sp.event_predictions[1].estimated_duration_minutes == pytest.approx(6.5)
        assert sp.event_predictions[1].is_observed is False

    def test_out_of_order_keirin_gen_duration_rejected(self):
        """
        Keirin finals at track championships are often uploaded out of schedule
        order, producing consecutive-timestamp gaps far larger than one keirin
        race.  A gap > 2× the keirin default (6.5 min) must be rejected so the
        prediction falls back to the default instead of using a bogus duration.
        """
        # Simulate a keirin repechage (pos 12) whose result was uploaded 55
        # minutes after the previous final's because the schedule ran out of order.
        record_generated_time(self.EVENT_ID + 2, 55, 11, datetime(2026, 1, 1, 12, 46, 0))
        record_generated_time(self.EVENT_ID + 2, 55, 12, datetime(2026, 1, 1, 13, 41, 0))  # 55 min gap
        session = Session(
            session_id=55,
            day="Sunday",
            scheduled_start=time(8, 0),
            events=[
                Event(
                    position=11,
                    name="U11 Keirin Final",
                    discipline="keirin",
                    status=EventStatus.COMPLETED,
                    is_special=False,
                ),
                Event(
                    position=12,
                    name="Keirin Repechage",
                    discipline="keirin",
                    status=EventStatus.UPCOMING,
                    is_special=False,
                ),
            ],
        )
        sp = predict_session(self.EVENT_ID + 2, session, now=None)
        # 55 min >> 2 × 6.5 = 13 min → rejected; falls back to keirin default
        assert sp.event_predictions[1].estimated_duration_minutes == pytest.approx(6.5)
        assert sp.event_predictions[1].is_observed is False


# ── update_status_cache ────────────────────────────────────────────────────────


class TestUpdateStatusCacheWallClockBound:
    """
    update_status_cache records wall-clock elapsed time when an event
    transitions UPCOMING → COMPLETED.  The elapsed time is capped at
    3× the discipline's static default to prevent inflated values when
    start lists are published well before the race starts.
    """

    def _make_session(self, status: EventStatus, discipline: str = "keirin") -> Session:
        return Session(
            session_id=99,
            day="Sunday",
            scheduled_start=time(12, 0),
            events=[
                Event(position=1, name="E1", discipline=discipline, status=status, is_special=False),
            ],
        )

    def test_reasonable_elapsed_is_recorded(self):
        """An elapsed time within 3× default is recorded without issue."""

        sessions = [self._make_session(EventStatus.UPCOMING, "keirin")]
        t_seen = datetime(2026, 1, 1, 12, 0, 0)
        update_status_cache(88001, sessions, t_seen)

        # Transition UPCOMING → COMPLETED 10 min later (within 3 × 6.5 = 19.5 min)
        sessions2 = [self._make_session(EventStatus.COMPLETED, "keirin")]
        t_done = datetime(2026, 1, 1, 12, 10, 0)
        (record,) = update_status_cache(88001, sessions2, t_done)
        assert record.duration_minutes == pytest.approx(10.0)
        assert record.source == "wall_clock"
        assert (record.competition_id, record.session_id, record.event_position) == (88001, 99, 1)

    def test_inflated_elapsed_exceeding_cap_is_not_recorded(self):
        """
        Elapsed > 3× default must NOT enter the database.
        Keirin default = 6.5 min → cap = 19.5 min.
        A 37-min elapsed (start list published 20+ min before race) is rejected.
        """
        from app.database import get_db

        def _keirin_row_count() -> int:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM event_durations WHERE discipline = 'keirin' AND competition_id = 88002"
                ).fetchone()
            return row["cnt"]

        assert _keirin_row_count() == 0

        sessions = [self._make_session(EventStatus.UPCOMING, "keirin")]
        update_status_cache(88002, sessions, datetime(2026, 1, 1, 12, 0, 0))

        # Transition after 37 min (exceeds 3 × 6.5 = 19.5 min cap)
        sessions2 = [self._make_session(EventStatus.COMPLETED, "keirin")]
        records = update_status_cache(88002, sessions2, datetime(2026, 1, 1, 12, 37, 0))
        save_live_durations(records)

        assert records == []
        assert _keirin_row_count() == 0  # no row was inserted


# ── predict_schedule ──────────────────────────────────────────────────────────


class TestPredictSchedule:
    def test_session_count(self, sessions):
        schedule = predict_schedule(26008, sessions, now=None)
        assert len(schedule.sessions) == 3

    def test_competition_id_preserved(self, sessions):
        schedule = predict_schedule(26008, sessions, now=None)
        assert schedule.competition_id == 26008

    def test_fully_completed_session_delay_is_bounded(self, sessions):
        """
        Friday has 58/60 completed events; the remaining two are 'End of Session'
        (zero duration) and a ceremony. Delay may be non-zero but should stay
        within the clamped bounds (-30, +120).
        """
        schedule = predict_schedule(26008, sessions, now=datetime.now())
        friday = schedule.sessions[0]
        assert -30.0 <= friday.observed_delay_minutes <= 120.0

    def test_predictions_are_non_decreasing(self, sessions):
        """Predicted start times within a session must be non-decreasing."""
        schedule = predict_schedule(26008, sessions, now=None)
        for sp in schedule.sessions:
            times_in_minutes = [p.predicted_start.hour * 60 + p.predicted_start.minute for p in sp.event_predictions]
            assert times_in_minutes == sorted(times_in_minutes), f"Times not sorted in session {sp.session.day}"


# ── latest_live_generated_time ─────────────────────────────────────────────────


class TestLatestLiveGeneratedTime:
    COMP = 7001
    C, U = EventStatus.COMPLETED, EventStatus.UPCOMING

    def _session(self, session_id: int, statuses: list[EventStatus]) -> Session:
        return Session(
            session_id=session_id,
            day="Tuesday",
            scheduled_start=time(10, 0),
            events=[_make_event(i, s) for i, s in enumerate(statuses)],
        )

    def test_newest_timestamp_of_in_progress_session(self):
        live = self._session(1, [self.C, self.C, self.U])
        record_generated_time(self.COMP, 1, 0, datetime(2026, 10, 6, 10, 30))
        record_generated_time(self.COMP, 1, 1, datetime(2026, 10, 6, 10, 55))
        assert latest_live_generated_time(self.COMP, [live]) == datetime(2026, 10, 6, 10, 55)

    def test_finished_session_ignored(self):
        done = self._session(2, [self.C, self.C])
        record_generated_time(self.COMP, 2, 1, datetime(2026, 10, 5, 18, 0))
        assert latest_live_generated_time(self.COMP, [done]) is None

    def test_unstarted_session_ignored(self):
        assert latest_live_generated_time(self.COMP, [self._session(3, [self.U, self.U])]) is None


class TestFinishedSessionWithPendingSpecial:
    """A session whose races are all done is finished, even if End of Session is still NOT_READY."""

    def _session(self) -> Session:
        events = [_make_event(i, EventStatus.COMPLETED) for i in range(3)]
        events.append(
            Event(
                position=3,
                name="End of Session",
                discipline="end_of_session",
                status=EventStatus.NOT_READY,
                is_special=True,
            )
        )
        return Session(session_id=1, day="Monday", scheduled_start=time(10, 0), events=events)

    def test_no_delay(self):
        # 50 min after the scheduled start: inside the delay window if the session counted as live.
        sp = predict_session(7002, self._session(), now=datetime(2026, 10, 6, 10, 50))
        assert sp.observed_delay_minutes == 0.0

    def test_no_active_event(self):
        sp = predict_session(7002, self._session(), now=datetime(2026, 10, 6, 13, 47))
        assert not any(p.is_active for p in sp.event_predictions)


class TestUseLearnedDefault:
    """Learned durations are opt-in in the app; the predictor's defaults must match."""

    def _session(self) -> Session:
        return Session(
            session_id=1,
            day="Friday",
            scheduled_start=time(8, 0),
            events=[_make_event(0, EventStatus.UPCOMING), _make_event(1, EventStatus.UPCOMING)],
        )

    @pytest.fixture
    def learned_scratch_race(self):
        from app.database import record_duration_structured

        for pos in range(5):
            record_duration_structured(7003, 1, pos, "Scratch", "scratch_race", 99.0)

    def test_default_ignores_learned(self, learned_scratch_race):
        sp = predict_session(7003, self._session())
        assert sp.event_predictions[1].predicted_start == _add_minutes(time(8, 0), SCRATCH_SLOT)

    def test_opt_in_uses_learned(self, learned_scratch_race):
        sp = predict_session(7003, self._session(), learned=load_learned_durations([self._session()]))
        # The learned average includes the static changeover, swapped for the live one.
        assert sp.event_predictions[1].predicted_start == _add_minutes(time(8, 0), 99.0 + BUNCH_SHIFT)

    def test_loads_each_discipline_once(self, learned_scratch_race):
        session = self._session()
        disciplines = {e.discipline for e in session.events}
        with patch("app.predictor.get_learned_duration", return_value=None) as read:
            assert load_learned_durations([session, session]) == {}
        assert sorted(c.args[0] for c in read.call_args_list) == sorted(disciplines)

    def test_omits_disciplines_without_enough_samples(self, learned_scratch_race):
        assert load_learned_durations([self._session()]) == {"scratch_race": pytest.approx(99.0)}

    def test_prediction_makes_no_database_reads(self, learned_scratch_race):
        learned = load_learned_durations([self._session()])
        with patch("app.predictor.get_learned_duration", side_effect=AssertionError("DB read")):
            sched = predict_schedule(7003, [self._session()], learned=learned)
        start = sched.sessions[0].event_predictions[1].predicted_start
        assert start == _add_minutes(time(8, 0), 99.0 + BUNCH_SHIFT)

    def test_schedule_default_ignores_learned(self, learned_scratch_race):
        sched = predict_schedule(7003, [self._session()])
        start = sched.sessions[0].event_predictions[1].predicted_start
        assert start == _add_minutes(time(8, 0), SCRATCH_SLOT)


class TestGeneratedGapAssignment:
    """A Generated timestamp marks when an event's results were published (its end), so the
    gap between consecutive timestamps is the duration of the later event.

    Captured 26037 Tuesday morning: field sizes confirm it (6 TP teams took 46.9 min,
    2 TP teams 14.5, 3 pursuiters 12.4)."""

    COMP = 26037

    @pytest.fixture
    def tuesday(self):
        import json

        from app.parser import parse_generated_time

        fixtures = Path(__file__).parent / "fixtures"
        session = parse_schedule(json.loads((fixtures / "schedule-26037-live.json").read_text()))[2]
        for e in session.events:
            if e.result_url:
                page = fixtures / "26037-results" / e.result_url.rsplit("/", 1)[-1]
                record_generated_time(self.COMP, session.session_id, e.position, parse_generated_time(page.read_text()))
        return session

    def _durations(self, session) -> dict[int, tuple[float, bool]]:
        sp = predict_session(self.COMP, session)
        return {p.event.position: (round(p.estimated_duration_minutes, 1), p.is_observed) for p in sp.event_predictions}

    def test_gap_lands_on_later_event(self, tuesday):
        d = self._durations(tuesday)
        # 75+ TP (2 teams): gap from 65-74 TP's result to its own.
        assert d[4] == (14.5, True)
        # 45-49 sprint 1/8 (12 min default): gap from the 45-49 pursuit's result to its own.
        assert d[8] == (22.4, True)

    def test_implausible_gap_not_used(self, tuesday):
        # 65-74 TP's own gap (46.9) is over 2x its 10-min default, so it isn't used.
        # The old off-by-one gave it 75+ TP's 14.5 instead.
        assert self._durations(tuesday)[3][1] is False

    def test_tool_agrees(self, tuesday):
        from app.predictor import get_generated_time
        from tools.extract_competition import extract_generated_diff_duration

        gen = {e.position: get_generated_time(self.COMP, tuesday.session_id, e.position) for e in tuesday.events}
        assert round(extract_generated_diff_duration(gen[3], gen[4], "team_pursuit"), 1) == 14.5
        assert round(extract_generated_diff_duration(gen[7], gen[8], "sprint_match"), 1) == 22.4


# ── Medal ceremony duration from forecast podiums ─────────────────────────────


class TestCeremonyDuration:
    def _session(self) -> Session:
        names = ["45-49 Men Pursuit Final", "50-54 Men Pursuit Final", "Medal Ceremonies", "55-59 Men Pursuit Final"]
        events = [
            Event(
                position=i,
                name=n,
                discipline="ceremony" if n == "Medal Ceremonies" else "pursuit_3k",
                status=EventStatus.NOT_READY,
                is_special=n == "Medal Ceremonies",
            )
            for i, n in enumerate(names)
        ]
        return Session(session_id=1, day="Day", scheduled_start=time(10, 0), events=events)

    def test_duration_from_podiums(self):
        session = self._session()
        schedule = predict_schedule(26101, [session], now=None)
        ceremony = schedule.sessions[0].event_predictions[2]
        assert ceremony.podium_count == 2
        assert ceremony.estimated_duration_minutes == pytest.approx(
            CEREMONY_BASE_MINUTES + 2 * CEREMONY_PER_PODIUM_MINUTES
        )
        assert schedule.sessions[0].event_predictions[3].predicted_start == _add_minutes(
            time(10, 0), 2 * DEFAULT_DURATIONS["pursuit_3k"] + ceremony.estimated_duration_minutes
        )

    def test_generated_gap_ignored_for_ceremony(self):
        # A ceremony page is generated seconds after the previous result, at the ceremony's start.
        session = self._session()
        record_generated_time(26102, 1, 1, datetime(2026, 10, 7, 17, 29, 27))
        record_generated_time(26102, 1, 2, datetime(2026, 10, 7, 17, 44, 0))
        schedule = predict_schedule(26102, [session], now=None)
        ceremony = schedule.sessions[0].event_predictions[2]
        assert not ceremony.is_observed
        assert ceremony.podium_count == 2

    def test_generated_gap_after_ceremony_ignored(self):
        # The gap from a ceremony's Generated time (its start) to the next result includes the
        # ceremony, so crediting it to the next event would count the ceremony twice.
        events = [
            Event(
                position=0,
                name="45-49 Men Pursuit Final",
                discipline="pursuit_3k",
                status=EventStatus.COMPLETED,
                is_special=False,
            ),
            Event(
                position=1,
                name="Medal Ceremonies",
                discipline="ceremony",
                status=EventStatus.COMPLETED,
                is_special=True,
            ),
            Event(
                position=2,
                name="65-74 Men Team Pursuit Qualifying",
                discipline="team_pursuit",
                status=EventStatus.COMPLETED,
                is_special=False,
            ),
        ]
        session = Session(session_id=1, day="Day", scheduled_start=time(10, 0), events=events)
        record_heat_count(26104, 1, 2, 6)
        record_generated_time(26104, 1, 0, datetime(2026, 10, 6, 11, 0, 0))
        record_generated_time(26104, 1, 1, datetime(2026, 10, 6, 11, 0, 6))
        record_generated_time(26104, 1, 2, datetime(2026, 10, 6, 12, 6, 0))  # ceremony + 40 min of TP
        tp = predict_schedule(26104, [session], now=None).sessions[0].event_predictions[2]
        assert not tp.is_observed
        assert tp.estimated_duration_minutes == pytest.approx(6 * PER_HEAT_DURATIONS["team_pursuit"])

    def test_unforecast_ceremony_keeps_default(self):
        session = self._session()
        session.events[0].name = "U17 Men Pursuit Final"
        ceremony = predict_schedule(26103, [session], now=None).sessions[0].event_predictions[2]
        assert ceremony.podium_count is None
        assert ceremony.estimated_duration_minutes == DEFAULT_DURATIONS["ceremony"]


# ── Sprint Ride 3 (decider) duration ──────────────────────────────────────────


class TestSprintRide3:
    """A best-of-3 round's Ride 3 is ridden only by pairs tied 1-1 after Ride 2."""

    ROUND = "55-59 Men Sprint 1/4 Final"

    def _session(self, ride3_status: EventStatus = EventStatus.NOT_READY) -> Session:
        def event(pos: int, name: str, status: EventStatus) -> Event:
            return Event(position=pos, name=name, discipline="sprint_match", status=status, is_special=False)

        return Session(
            session_id=1,
            day="Day",
            scheduled_start=time(10, 0),
            events=[
                event(0, f"{self.ROUND} Ride 2", EventStatus.COMPLETED),
                event(1, f"{self.ROUND} Ride 3", ride3_status),
            ],
        )

    def test_expected_deciders_before_ride_2_results(self):
        record_heat_count(26111, 1, 1, 4)
        ride3 = predict_session(26111, self._session()).event_predictions[1]
        assert ride3.estimated_duration_minutes == pytest.approx(4 * SPRINT_DECIDER_MINUTES * SPRINT_DECIDER_RATE)
        assert (ride3.heat_count, ride3.heat_basis) == (4, "decider_pairs")

    def test_expected_deciders_without_start_list(self):
        ride3 = predict_session(26112, self._session()).event_predictions[1]
        assert ride3.estimated_duration_minutes == pytest.approx(4 * SPRINT_DECIDER_MINUTES * SPRINT_DECIDER_RATE)

    def test_known_deciders(self):
        record_heat_count(26113, 1, 1, 4)
        record_sprint_deciders(26113, self.ROUND, 1)
        ride3 = predict_session(26113, self._session()).event_predictions[1]
        assert ride3.estimated_duration_minutes == pytest.approx(SPRINT_DECIDER_MINUTES)
        assert ride3.heat_count == 1
        assert ride3.heat_basis == "decider"

    def test_no_deciders(self):
        record_heat_count(26114, 1, 1, 4)
        record_sprint_deciders(26114, self.ROUND, 0)
        ride3 = predict_session(26114, self._session()).event_predictions[1]
        assert ride3.estimated_duration_minutes == 0.0
        assert ride3.heat_count == 0

    def test_ride_2_unaffected(self):
        record_heat_count(26115, 1, 0, 4)
        record_sprint_deciders(26115, self.ROUND, 1)
        ride2 = predict_session(26115, self._session()).event_predictions[0]
        assert ride2.estimated_duration_minutes == pytest.approx(4 * PER_HEAT_DURATIONS["sprint_match"])

    def test_long_decider_still_observed(self):
        # 40-44 Men Sprint Final Ride 3 at 26037: one decider took 7.3 min.
        record_heat_count(26117, 1, 1, 2)
        record_sprint_deciders(26117, self.ROUND, 1)
        record_generated_time(26117, 1, 0, datetime(2026, 10, 5, 18, 17, 58))
        record_generated_time(26117, 1, 1, datetime(2026, 10, 5, 18, 25, 16))
        ride3 = predict_session(26117, self._session(EventStatus.COMPLETED)).event_predictions[1]
        assert ride3.is_observed
        assert ride3.estimated_duration_minutes == pytest.approx(7.3)

    def test_completed_ride_3_uses_generated_gap(self):
        # 3.2 min is outside 0.5x-2x of the full 4-pair estimate (12 min) but plausible for one decider.
        record_heat_count(26116, 1, 1, 4)
        record_sprint_deciders(26116, self.ROUND, 1)
        record_generated_time(26116, 1, 0, datetime(2026, 10, 7, 17, 0, 0))
        record_generated_time(26116, 1, 1, datetime(2026, 10, 7, 17, 3, 12))
        ride3 = predict_session(26116, self._session(EventStatus.COMPLETED)).event_predictions[1]
        assert ride3.is_observed
        assert ride3.estimated_duration_minutes == pytest.approx(3.2)


# ── Sprint round size from the round name ─────────────────────────────────────


class TestSprintRoundPairs:
    """Without a start list, a sprint round's pairs come from its name (1/2 Final and Final have 2)."""

    def _predict(self, competition_id: int, name: str):
        event = Event(position=0, name=name, discipline="sprint_match", status=EventStatus.NOT_READY, is_special=False)
        session = Session(session_id=1, day="Day", scheduled_start=time(10, 0), events=[event])
        return predict_session(competition_id, session).event_predictions[0]

    @pytest.mark.parametrize(
        ("name", "pairs"),
        [
            ("65-69 Men Sprint Final Ride 1", 2),
            ("35-39 Women Sprint 1/2 Final Ride 2", 2),
            ("70-74 Men Sprint 1/4 Final Ride 1", 4),
        ],
    )
    def test_pairs_from_round_name(self, name, pairs):
        pred = self._predict(26121, name)
        assert pred.estimated_duration_minutes == pytest.approx(pairs * PER_HEAT_DURATIONS["sprint_match"])
        assert pred.heat_count == pairs
        assert pred.heat_basis == "round"

    def test_decider_scaled_from_round_name(self):
        pred = self._predict(26122, "65+ Women Sprint 1/2 Final Ride 3")
        assert pred.estimated_duration_minutes == pytest.approx(2 * SPRINT_DECIDER_MINUTES * SPRINT_DECIDER_RATE)
        assert (pred.heat_count, pred.heat_basis) == (2, "decider_pairs")

    def test_placement_final_keeps_default(self):
        # A sprint 5-8 Final is one race of 4 riders, not 2 pairs.
        pred = self._predict(26125, "40-44 Men Sprint 5-8 Final")
        assert pred.estimated_duration_minutes == DEFAULT_DURATIONS["sprint_match"]

    def test_other_rounds_keep_default(self):
        # 1/8 Finals vary with byes (4 heats at 26008, 8 at 26037), so they keep the default.
        pred = self._predict(26123, "65-69 Men Sprint 1/8 Final")
        assert pred.estimated_duration_minutes == DEFAULT_DURATIONS["sprint_match"]

    def test_start_list_wins(self):
        record_heat_count(26124, 1, 0, 1)
        pred = self._predict(26124, "65-69 Men Sprint Final Ride 1")
        assert pred.estimated_duration_minutes == pytest.approx(PER_HEAT_DURATIONS["sprint_match"])
        assert pred.heat_count == 1
        assert pred.heat_basis == "start_list"


# ── Heat counts from the round name ────────────────────────────────────────────


def _ev(position: int, name: str, discipline: str) -> Event:
    return Event(position=position, name=name, discipline=discipline, status=EventStatus.NOT_READY, is_special=False)


class TestRoundNameHeats:
    """Without a start list, medal finals after a qualifying round and keirin finals are sized by name."""

    def _preds(self, competition_id: int, events: list[Event]):
        session = Session(session_id=1, day="Day", scheduled_start=time(10, 0), events=events)
        return predict_schedule(competition_id, [session]).sessions[0].event_predictions

    @pytest.mark.parametrize(
        ("discipline", "name"),
        [
            ("pursuit_2k", "55-59 Women Pursuit"),
            ("team_pursuit", "55-64 Men Team Pursuit"),
            ("team_sprint", "65-74 Men Team Sprint"),
        ],
    )
    def test_final_after_qualifying_is_bronze_and_gold(self, discipline, name):
        preds = self._preds(26141, [_ev(0, f"{name} Qualifying", discipline), _ev(1, f"{name} Final", discipline)])
        final = preds[1]
        assert (final.heat_count, final.heat_basis) == (2, "round")
        assert final.estimated_duration_minutes == pytest.approx(
            2 * PER_HEAT_DURATIONS[discipline] + get_changeover(discipline)
        )
        assert preds[0].heat_count is None

    def test_final_without_qualifying_keeps_default(self):
        # A regional "Pursuit Final" is the only round: every rider rides it.
        final = self._preds(26142, [_ev(0, "Master A Women Pursuit Final", "pursuit_2k")])[0]
        assert final.heat_count is None
        assert final.estimated_duration_minutes == DEFAULT_DURATIONS["pursuit_2k"]

    @pytest.mark.parametrize(
        ("name", "heats"),
        [("U15 Men Keirin 7-12 Final", 1), ("U15 Men Keirin 1-6 Final", 1), ("Elite/Junior Men Keirin 1/2 Final", 2)],
    )
    def test_keirin_rounds(self, name, heats):
        pred = self._preds(26143, [_ev(0, name, "keirin")])[0]
        assert (pred.heat_count, pred.heat_basis) == (heats, "round")

    def test_keirin_first_round_keeps_default(self):
        assert self._preds(26144, [_ev(0, "Elite Men Keirin Round 1", "keirin")])[0].heat_count is None

    def test_start_list_wins(self):
        record_heat_count(26145, 1, 1, 1)
        events = [
            _ev(0, "75+ Men Team Pursuit Qualifying", "team_pursuit"),
            _ev(1, "75+ Men Team Pursuit Final", "team_pursuit"),
        ]
        final = self._preds(26145, events)[1]
        assert (final.heat_count, final.heat_basis) == (1, "start_list")


# ── Points and scratch race duration from distance ────────────────────────────────────────


class TestBunchRaceDistance:
    def _predict(self, competition_id: int, discipline: str):
        event = Event(position=0, name="Race", discipline=discipline, status=EventStatus.NOT_READY, is_special=False)
        session = Session(session_id=1, day="Day", scheduled_start=time(10, 0), events=[event])
        return predict_session(competition_id, session).event_predictions[0]

    def test_distance_sets_duration(self):
        record_race_distance(26131, 1, 0, 20.0)
        pred = self._predict(26131, "points_race")
        assert pred.estimated_duration_minutes == pytest.approx(
            20.0 / BUNCH_RACE_KMH * 60 + LIVE_BUNCH_CHANGEOVER_MINUTES
        )
        assert not pred.is_observed
        assert pred.race_distance_km == 20.0

    def test_no_distance_uses_default(self):
        assert (
            self._predict(26132, "points_race").estimated_duration_minutes
            == DEFAULT_DURATIONS["points_race"] + BUNCH_SHIFT
        )

    def test_scratch_race_distance(self):
        record_race_distance(26133, 1, 0, 5.0)
        assert self._predict(26133, "scratch_race").estimated_duration_minutes == pytest.approx(
            5.0 / BUNCH_RACE_KMH * 60 + LIVE_BUNCH_CHANGEOVER_MINUTES
        )

    def test_unmeasured_bunch_races_unaffected(self):
        record_race_distance(26135, 1, 0, 3.0)
        assert (
            self._predict(26135, "tempo_race").estimated_duration_minutes
            == DEFAULT_DURATIONS["tempo_race"] + BUNCH_SHIFT
        )

    def test_finish_time_wins(self):
        record_race_distance(26134, 1, 0, 20.0)
        record_observed_duration(26134, 1, 0, 26.15, "points_race", "Race")
        pred = self._predict(26134, "points_race")
        assert pred.is_observed
        assert pred.estimated_duration_minutes == pytest.approx(26.15 + LIVE_BUNCH_CHANGEOVER_MINUTES)


# ── Per-competition bunch-race changeover ─────────────────────────────────────


class TestBunchChangeover:
    """Changeover for bunch races is calibrated from (Generated gap − Finish Time) per competition."""

    def _session(self, disciplines: list[str]) -> Session:
        events = [
            Event(
                position=i,
                name=f"Race {i}",
                discipline=d,
                status=EventStatus.COMPLETED,
                is_special=False,
                result_url=f"results/R{i}.htm",
            )
            for i, d in enumerate(disciplines)
        ]
        return Session(session_id=1, day="Day", scheduled_start=time(10, 0), events=events)

    def _record(self, competition_id: int, finishes: dict[int, float], gaps: list[float]) -> None:
        """Generated timestamps with the given gaps between consecutive events, and Finish Times."""
        t = datetime(2026, 10, 7, 10, 0)
        record_generated_time(competition_id, 1, 0, t)
        for pos, gap in enumerate(gaps, start=1):
            t += timedelta(minutes=gap)
            record_generated_time(competition_id, 1, pos, t)
        for pos, fin in finishes.items():
            record_observed_duration(competition_id, 1, pos, fin, "scratch_race", f"Race {pos}")

    def test_default_before_enough_samples(self):
        session = self._session(["scratch_race"] * 3)
        self._record(26141, {1: 5.0, 2: 5.0}, [13.0, 13.0])
        assert bunch_changeover(26141, [session]) == LIVE_BUNCH_CHANGEOVER_MINUTES

    def test_median_of_samples(self):
        session = self._session(["points_race"] + ["scratch_race"] * 3)
        self._record(26142, {1: 5.0, 2: 6.0, 3: 4.0}, [13.0, 14.5, 10.0])  # overheads 8.0, 8.5, 6.0
        assert bunch_changeover(26142, [session]) == pytest.approx(8.0)

    def test_ignores_races_after_other_events(self):
        # A bunch race after a sprint includes staging the field; only back-to-back bunch races count.
        session = self._session(["sprint_match", "scratch_race", "scratch_race", "scratch_race"])
        self._record(26143, {1: 5.0, 2: 5.0, 3: 5.0}, [25.0, 13.0, 13.0])
        assert bunch_changeover(26143, [session]) == LIVE_BUNCH_CHANGEOVER_MINUTES

    def test_ignores_implausible_gaps(self):
        session = self._session(["scratch_race"] * 5)
        self._record(26144, {1: 5.0, 2: 5.0, 3: 5.0, 4: 5.0}, [13.0, 13.0, 60.0, 3.0])  # 55 and -2 dropped
        assert bunch_changeover(26144, [session]) == LIVE_BUNCH_CHANGEOVER_MINUTES

    def test_calibrated_changeover_applied(self):
        session = self._session(["scratch_race"] * 4 + ["points_race"])
        session.events[4].status = EventStatus.NOT_READY
        session.events[4].result_url = None
        self._record(26145, {1: 5.0, 2: 5.0, 3: 5.0}, [13.0, 13.0, 13.0])
        record_race_distance(26145, 1, 4, 23.0)
        preds = predict_schedule(26145, [session]).sessions[0].event_predictions
        assert preds[1].estimated_duration_minutes == pytest.approx(5.0 + 8.0)
        assert preds[4].estimated_duration_minutes == pytest.approx(23.0 / BUNCH_RACE_KMH * 60 + 8.0)

    def test_default_duration_shifted_by_changeover(self):
        session = self._session(["scratch_race"] * 4 + ["tempo_race"])
        session.events[4].status = EventStatus.NOT_READY
        session.events[4].result_url = None
        self._record(26146, {1: 5.0, 2: 5.0, 3: 5.0}, [13.0, 13.0, 13.0])
        tempo = predict_schedule(26146, [session]).sessions[0].event_predictions[4]
        assert tempo.estimated_duration_minutes == pytest.approx(
            DEFAULT_DURATIONS["tempo_race"] - CHANGEOVER_MINUTES["tempo_race"] + 8.0
        )

    def test_keirin_keeps_static_changeover(self):
        session = self._session(["scratch_race"] * 4 + ["keirin"])
        session.events[4].status = EventStatus.NOT_READY
        self._record(26147, {1: 5.0, 2: 5.0, 3: 5.0}, [13.0, 13.0, 13.0])
        record_heat_count(26147, 1, 4, 2)
        keirin = predict_schedule(26147, [session]).sessions[0].event_predictions[4]
        assert keirin.estimated_duration_minutes == pytest.approx(
            2 * PER_HEAT_DURATIONS["keirin"] + CHANGEOVER_MINUTES["keirin"]
        )
