"""Tests for app/parser.py using event 26008 sample data."""
import json
from datetime import time
from pathlib import Path

import pytest

from app.disciplines import detect_discipline
from app.models import EventStatus
from app.parser import parse_finish_time, parse_heat_count, parse_schedule

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

    def test_start_list_url_present_for_completed(self, sessions):
        # Non-special completed events (actual races) should have a start_list_url;
        # ceremonies and other special events do not.
        completed_races = [
            e for e in sessions[0].events
            if e.status == EventStatus.COMPLETED and not e.is_special
        ]
        assert all(e.start_list_url is not None for e in completed_races), (
            "All non-special completed events should have a start_list_url"
        )

    def test_start_list_url_format(self, sessions):
        url = sessions[0].events[0].start_list_url
        assert url is not None
        assert url.startswith("results/E26008/")
        assert url.endswith("-S.htm")

    def test_start_list_url_present_for_upcoming(self, sessions):
        upcoming = [e for e in sessions[2].events if e.status == EventStatus.UPCOMING]
        assert all(e.start_list_url is not None for e in upcoming), (
            "Upcoming events should have a start_list_url"
        )

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

    def test_live_url_present_for_active_event(self, sessions):
        # "U17 Men Tempo Race / Omni II" has btn-danger (LIVE) in the sample data
        live_events = [
            e for s in sessions for e in s.events if e.live_url is not None
        ]
        assert len(live_events) == 1
        assert live_events[0].live_url == "liveresults.php?EventId=26008"

    def test_live_url_absent_for_completed_events(self, sessions):
        completed = [e for s in sessions for e in s.events if e.status == EventStatus.COMPLETED]
        assert all(e.live_url is None for e in completed)


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

    def test_pursuit_4k_elite_men(self):
        assert detect_discipline("Elite Men Pursuit Final") == "pursuit_4k"

    def test_pursuit_4k_elite_women(self):
        assert detect_discipline("Elite Women Pursuit Final") == "pursuit_4k"

    def test_pursuit_3k_junior_men(self):
        assert detect_discipline("Junior Men Pursuit Final") == "pursuit_3k"

    def test_pursuit_2k_junior_women(self):
        assert detect_discipline("Junior Women Pursuit Final") == "pursuit_2k"

    def test_pursuit_3k_master_a_men(self):
        assert detect_discipline("Master A Men Pursuit Final") == "pursuit_3k"

    def test_pursuit_3k_master_b_men(self):
        assert detect_discipline("Master B Men Pursuit Final") == "pursuit_3k"

    def test_pursuit_2k_master_c_men(self):
        assert detect_discipline("Master C Men Pursuit Final") == "pursuit_2k"

    def test_pursuit_2k_master_d_men(self):
        assert detect_discipline("Master D Men Pursuit Final") == "pursuit_2k"

    def test_pursuit_fallback_u17_men(self):
        assert detect_discipline("U17 Men Pursuit Final") == "pursuit_3k"

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


# ── parse_heat_count ──────────────────────────────────────────────────────────


class TestParseHeatCount:
    # Simulated sprint qualifying start list: 5 riders, each their own heat
    SPRINT_QUAL_HTML = (
        "Heat 1\n212  PITTARD Charlie\n"
        "Heat 2\n211  RANKL Avery\n"
        "Heat 3\n215  ALDEN Calla\n"
        "Heat 4\n214  BURTON Maelle\n"
        "Heat 5\n213  MARCYNUK Addison\n"
    )

    def test_counts_heats(self):
        assert parse_heat_count(self.SPRINT_QUAL_HTML) == 5

    def test_single_heat(self):
        html = "Heat 1\nRider A\nRider B\nRider C\nRider D\nRider E\nRider F\n"
        assert parse_heat_count(html) == 1

    def test_keirin_two_heats(self):
        html = (
            "Heat 1\nRider A\nRider B\nRider C\nRider D\nRider E\nRider F\n"
            "Heat 2\nRider G\nRider H\nRider I\nRider J\nRider K\nRider L\n"
        )
        assert parse_heat_count(html) == 2

    def test_returns_none_when_no_heats(self):
        html = "Generated: 2026-02-27 08:25:21Timing & Results by Racetiming.ca"
        assert parse_heat_count(html) is None

    def test_ignores_partial_word_matches(self):
        # "Heated" or "Heathen" should not match
        html = "Heated debate\nHeathen\nHeat 1\nRider A\n"
        assert parse_heat_count(html) == 1
