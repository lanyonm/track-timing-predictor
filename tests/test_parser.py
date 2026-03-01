"""Tests for app/parser.py using event 26008 sample data."""
import json
from datetime import time
from pathlib import Path

import pytest

from app.disciplines import detect_discipline
from app.models import EventStatus
from app.parser import parse_finish_time, parse_schedule

SAMPLE_PATH = Path(__file__).parent.parent / "sample-event-output.json"


@pytest.fixture(scope="module")
def sample_data():
    with SAMPLE_PATH.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def sessions(sample_data):
    return parse_schedule(sample_data)


# ── parse_schedule ────────────────────────────────────────────────────────────


class TestParseSchedule:
    def test_session_count(self, sessions):
        assert len(sessions) == 3

    def test_session_days(self, sessions):
        assert [s.day for s in sessions] == ["Friday", "Saturday", "Sunday"]

    def test_session_scheduled_starts(self, sessions):
        assert sessions[0].scheduled_start == time(8, 15)
        assert sessions[1].scheduled_start == time(8, 15)
        assert sessions[2].scheduled_start == time(8, 0)

    def test_session_ids(self, sessions):
        assert sessions[0].session_id == 1
        assert sessions[1].session_id == 2
        assert sessions[2].session_id == 3

    def test_event_counts(self, sessions):
        assert len(sessions[0].events) == 60
        assert len(sessions[1].events) == 43

    def test_first_event_name(self, sessions):
        assert sessions[0].events[0].name == "U17 Women Sprint Qualifying"

    def test_first_event_discipline(self, sessions):
        assert sessions[0].events[0].discipline == "sprint_qualifying"

    def test_completed_status(self, sessions):
        # Friday first event has btn-success active in sample data
        assert sessions[0].events[0].status == EventStatus.COMPLETED

    def test_result_url_present_for_completed(self, sessions):
        # Completed events must have a result_url
        completed = [e for e in sessions[0].events if e.status == EventStatus.COMPLETED]
        assert all(e.result_url is not None for e in completed), (
            "All completed events should have a result_url"
        )

    def test_result_url_format(self, sessions):
        url = sessions[0].events[0].result_url
        assert url is not None
        assert url.startswith("results/E26008/")
        assert url.endswith("-R.htm")

    def test_result_url_absent_for_non_completed(self, sessions):
        not_completed = [e for e in sessions[2].events if e.status != EventStatus.COMPLETED]
        assert all(e.result_url is None for e in not_completed)

    def test_event_positions_are_sequential(self, sessions):
        positions = [e.position for e in sessions[0].events]
        assert positions == sorted(positions)

    def test_sunday_has_upcoming_event(self, sessions):
        statuses = {e.status for e in sessions[2].events}
        assert EventStatus.UPCOMING in statuses

    def test_discipline_variety(self, sessions):
        # Saturday has scratch races
        disciplines = {e.discipline for s in sessions for e in s.events}
        assert "sprint_qualifying" in disciplines
        assert "scratch_race" in disciplines

    def test_is_special_false_for_normal_events(self, sessions):
        assert not sessions[0].events[0].is_special


# ── detect_discipline ─────────────────────────────────────────────────────────


class TestDetectDiscipline:
    def test_madison(self):
        assert detect_discipline("Elite Women Madison 20km") == "madison"

    def test_madison_does_not_match_scratch(self):
        assert detect_discipline("Elite Men Madison Scratch 15km") == "madison"

    def test_keirin(self):
        assert detect_discipline("U17 Men Keirin Final") == "keirin"

    def test_team_pursuit_before_pursuit(self):
        assert detect_discipline("Elite Men Team Pursuit Qualifying") == "team_pursuit"

    def test_team_sprint_before_sprint(self):
        assert detect_discipline("Junior Women Team Sprint Final") == "team_sprint"


# ── parse_finish_time ─────────────────────────────────────────────────────────


class TestParseFinishTime:
    def test_standard_format(self):
        html = "Finish Time: 9:28Speed: 47.5 km/h"
        result = parse_finish_time(html)
        assert result == pytest.approx(9 + 28 / 60, rel=1e-4)

    def test_longer_race(self):
        html = "Finish Time: 15:07Speed: 39.7 km/h"
        result = parse_finish_time(html)
        assert result == pytest.approx(15 + 7 / 60, rel=1e-4)

    def test_returns_none_when_absent(self):
        html = "Generated: 2026-02-27 08:25:21Timing & Results by Racetiming.ca"
        assert parse_finish_time(html) is None

    def test_returns_none_for_sprint_result(self):
        # Sprint qualifying pages show per-rider times, not a Finish Time field
        html = "12.851\n6.453\n56.027 Q\nGenerated: 2026-02-27 08:25:21"
        assert parse_finish_time(html) is None

    def test_whitespace_tolerance(self):
        html = "Finish Time:  12:00"
        result = parse_finish_time(html)
        assert result == pytest.approx(12.0)

    def test_single_digit_seconds(self):
        html = "Finish Time: 8:05"
        result = parse_finish_time(html)
        assert result == pytest.approx(8 + 5 / 60, rel=1e-4)
