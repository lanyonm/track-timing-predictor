"""Tests for app/predictor.py prediction logic."""
import json
from datetime import datetime, time
from pathlib import Path

import pytest

from app.models import EventStatus, Session, TrackEvent
from app.parser import parse_schedule
from app.predictor import (
    _add_minutes,
    _compute_delay,
    predict_schedule,
    predict_session,
    record_heat_count,
)

SAMPLE_PATH = Path(__file__).parent.parent / "sample-event-output.json"


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
        durations = [20.0] * 10   # total_est = 200 min
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


def _make_event(position: int, status: EventStatus, discipline: str = "scratch_race") -> TrackEvent:
    return TrackEvent(
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
                _make_event(0, EventStatus.NOT_READY, "scratch_race"),   # default 12 min
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
        """With 3 keirin heats, duration = 3 × 5.0 + 2.0 changeover = 17.0 min."""
        record_heat_count(self.EVENT_ID, 99, 0, 3)
        session = self._make_session()
        sp = predict_session(self.EVENT_ID, session, now=None)
        assert sp.event_predictions[0].estimated_duration_minutes == pytest.approx(17.0)
        assert sp.event_predictions[0].heat_count == 3
        assert not sp.event_predictions[0].is_observed

    def test_heat_count_reflected_in_second_event_start(self):
        """Second event start shifts by the heat-count-based duration of the first."""
        record_heat_count(self.EVENT_ID, 99, 0, 2)
        session = self._make_session()
        sp = predict_session(self.EVENT_ID, session, now=None)
        # first event: 2 × 5.0 + 2.0 = 12.0 min → second starts at 08:12
        second_start = sp.event_predictions[1].predicted_start
        assert second_start == _add_minutes(time(8, 0), 12.0)

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


# ── predict_schedule ──────────────────────────────────────────────────────────


class TestPredictSchedule:
    def test_session_count(self, sessions):
        schedule = predict_schedule(26008, sessions, now=None)
        assert len(schedule.sessions) == 3

    def test_event_id_preserved(self, sessions):
        schedule = predict_schedule(26008, sessions, now=None)
        assert schedule.event_id == 26008

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
            times_in_minutes = [
                p.predicted_start.hour * 60 + p.predicted_start.minute
                for p in sp.event_predictions
            ]
            assert times_in_minutes == sorted(times_in_minutes), (
                f"Times not sorted in session {sp.session.day}"
            )
