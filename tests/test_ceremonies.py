"""Tests for app/ceremonies.py: medal ceremony podium forecasting (EventId 26037 formats)."""

import json
from datetime import time
from pathlib import Path

import pytest

from app.ceremonies import ceremony_duration, forecast_podiums, needs_categories
from app.disciplines import CEREMONY_BASE_MINUTES, CEREMONY_PER_PODIUM_MINUTES, detect_discipline
from app.models import Event, EventStatus, Session
from app.parser import parse_rider_list, parse_schedule

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def sessions_26037():
    with (FIXTURE_DIR / "schedule-26037.json").open() as f:
        return parse_schedule(json.load(f))


@pytest.fixture(scope="module")
def rider_list_26037():
    return parse_rider_list((FIXTURE_DIR / "rider-list-26037.html").read_text())


def _ceremony_counts(sessions, podiums):
    """Forecast podium counts in schedule order, None where a ceremony has no forecast."""
    return [podiums.get((s.session_id, e.position)) for s in sessions for e in s.events if e.discipline == "ceremony"]


def _event(position: int, name: str) -> Event:
    discipline = detect_discipline(name)
    return Event(
        position=position,
        name=name,
        discipline=discipline,
        status=EventStatus.NOT_READY,
        is_special=discipline == "ceremony",
    )


def _session(*names: str) -> Session:
    return Session(
        session_id=1,
        day="Day",
        scheduled_start=time(10, 0),
        events=[_event(i, n) for i, n in enumerate(names)],
    )


class TestForecastPodiums26037:
    # Podiums on CEREMONY-1..6-R.htm: 8, 7, 4, 10, 11, 6. Ceremony 3's 40-44 Men Points
    # Race podium moved to ceremony 4 (its result was regenerated an hour later), which a
    # schedule-based forecast can't see, so the forecast is 5 and 9 there. The fixture
    # predates the event and still lists a 70-74 Women 500m Time Trial Final that was
    # later dropped, so ceremony 5 forecasts 12.
    def test_completed_ceremonies(self, sessions_26037, rider_list_26037):
        podiums = forecast_podiums(sessions_26037, {}, rider_list_26037)
        assert _ceremony_counts(sessions_26037, podiums)[:6] == [8, 7, 5, 9, 12, 6]

    def test_every_ceremony_forecast(self, sessions_26037, rider_list_26037):
        podiums = forecast_podiums(sessions_26037, {}, rider_list_26037)
        assert None not in _ceremony_counts(sessions_26037, podiums)

    def test_without_rider_list_uses_name_bands(self, sessions_26037):
        # 35-49 Women Points Race → 3 five-year bands; 50+ Women Points Race → 1 (open band).
        podiums = forecast_podiums(sessions_26037, {}, None)
        assert _ceremony_counts(sessions_26037, podiums)[0] == 4

    def test_start_list_categories_override_rider_list(self, sessions_26037, rider_list_26037):
        name = "50+ Women Points Race Final"
        pos = next((s.session_id, e.position) for s in sessions_26037 for e in s.events if e.name == name)
        podiums = forecast_podiums(sessions_26037, {pos: frozenset({"W5054", "W5559"})}, rider_list_26037)
        assert _ceremony_counts(sessions_26037, podiums)[0] == 3 + 2


class TestForecastPodiumsRules:
    def test_sprint_final_counted_after_last_ride(self):
        session = _session(
            "45-49 Men Sprint Final Ride 1",
            "Medal Ceremonies",
            "45-49 Men Sprint Final Ride 2",
            "45-49 Men Sprint Final Ride 3",
            "Medal Ceremonies",
        )
        podiums = forecast_podiums([session], {}, None)
        assert (1, 1) not in podiums
        assert podiums[(1, 4)] == 1

    def test_placement_finals_are_not_podiums(self):
        # 25022 runs "40-44 Men Sprint 5-8 Final"; keirin "1-6 Final" is the medal final.
        session = _session("40-44 Men Sprint 5-8 Final", "45-49 Men Keirin 7-12 Final", "Medal Ceremonies")
        assert forecast_podiums([session], {}, None) == {}

    def test_rounds_are_not_podiums(self):
        session = _session("45-49 Men Sprint 1/2 Final Ride 1", "50-54 Men Sprint 1/8 Final", "Medal Ceremonies")
        assert forecast_podiums([session], {}, None) == {}

    def test_team_event_is_one_podium(self):
        session = _session("55-64 Men Team Pursuit Final", "35-44 Women Team Sprint Final", "Medal Ceremonies")
        assert forecast_podiums([session], {}, None) == {(1, 2): 2}

    def test_window_spans_sessions(self):
        s1 = _session("45-49 Men Pursuit Final")
        s2 = Session(session_id=2, day="Day", scheduled_start=time(15, 0), events=[_event(0, "Medal Ceremonies")])
        assert forecast_podiums([s1, s2], {}, None) == {(2, 0): 1}

    def test_non_masters_final_disables_forecast(self):
        # Other competitions name finals without an age band; their ceremonies keep the default.
        session = _session("45-49 Men Pursuit Final", "U17 Women Pursuit Final", "Medal Ceremony")
        assert forecast_podiums([session], {}, None) == {}


class TestNeedsCategories:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("35-49 Women Points Race Final", True),
            ("55+ Women Scratch Race Final", True),
            ("55-59 Men Scratch Race Final", False),
            ("55-59 Men Points Race Qualifier 1", False),
            ("55-64 Men Team Pursuit Final", False),
            ("U17 Women Scratch Race Final", False),
        ],
    )
    def test_combined_bunch_finals(self, name, expected):
        assert needs_categories(_event(0, name)) is expected


def test_ceremony_duration():
    assert ceremony_duration(8) == pytest.approx(CEREMONY_BASE_MINUTES + 8 * CEREMONY_PER_PODIUM_MINUTES)
