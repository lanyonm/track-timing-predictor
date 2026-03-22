"""Tests for the competition extraction script (tools/extract_competition.py)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models import EventStatus
from tools.extract_competition import (
    _fetch_with_retry,
    extract_competition,
    extract_finish_time_duration,
    extract_generated_diff_duration,
    extract_heat_count_duration,
    select_best_duration,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _load_json_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Unit tests for duration extraction helpers
# ---------------------------------------------------------------------------


class TestExtractFinishTimeDuration:
    def test_bunch_race_with_finish_time(self):
        html = _load_fixture("result-scratch-race-26008.html")
        dur = extract_finish_time_duration(html, "scratch_race")
        assert dur is not None
        assert dur > 0
        # scratch_race changeover is 2.0 min
        # Finish Time is the race time, so dur = race_time + 2.0
        assert dur > 2.0

    def test_no_finish_time(self):
        html = _load_fixture("result-team-pursuit-26009.html")
        dur = extract_finish_time_duration(html, "team_pursuit")
        assert dur is None


class TestExtractGeneratedDiffDuration:
    def test_valid_diff(self):
        prev = datetime(2025, 7, 11, 8, 0, 0)
        curr = datetime(2025, 7, 11, 8, 10, 0)
        dur = extract_generated_diff_duration(prev, curr, "sprint_qualifying")
        assert dur == 10.0

    def test_none_when_prev_missing(self):
        curr = datetime(2025, 7, 11, 8, 10, 0)
        dur = extract_generated_diff_duration(None, curr, "sprint_qualifying")
        assert dur is None

    def test_none_when_curr_missing(self):
        prev = datetime(2025, 7, 11, 8, 0, 0)
        dur = extract_generated_diff_duration(prev, None, "sprint_qualifying")
        assert dur is None

    def test_none_when_negative_diff(self):
        prev = datetime(2025, 7, 11, 8, 10, 0)
        curr = datetime(2025, 7, 11, 8, 0, 0)
        dur = extract_generated_diff_duration(prev, curr, "sprint_qualifying")
        assert dur is None

    def test_plausibility_filter_rejects_too_large(self):
        # Default for sprint_qualifying is 10.0 min; 2.0x = 20.0
        prev = datetime(2025, 7, 11, 8, 0, 0)
        curr = datetime(2025, 7, 11, 8, 30, 0)  # 30 min diff, > 2.0x
        dur = extract_generated_diff_duration(prev, curr, "sprint_qualifying")
        assert dur is None

    def test_plausibility_filter_rejects_too_small(self):
        # Default for points_race is 20.0 min; 0.5x = 10.0
        prev = datetime(2025, 7, 11, 8, 0, 0)
        curr = datetime(2025, 7, 11, 8, 3, 0)  # 3 min diff, < 0.5x
        dur = extract_generated_diff_duration(prev, curr, "points_race")
        assert dur is None


class TestExtractHeatCountDuration:
    def test_start_list_with_heats(self):
        html = _load_fixture("start-list-sprint-qualifying-26009.html")
        dur, count = extract_heat_count_duration(html, "sprint_qualifying")
        assert dur is not None
        assert count is not None
        assert count > 0
        assert dur > 0

    def test_no_heats_returns_none(self):
        dur, count = extract_heat_count_duration("<html></html>", "sprint_qualifying")
        assert dur is None
        assert count is None


class TestSelectBestDuration:
    def test_finish_time_priority(self):
        dur, source = select_best_duration(10.0, 8.0, 12.0)
        assert dur == 10.0
        assert source == "finish_time"

    def test_generated_diff_fallback(self):
        dur, source = select_best_duration(None, 8.0, 12.0)
        assert dur == 8.0
        assert source == "generated_diff"

    def test_heat_count_fallback(self):
        dur, source = select_best_duration(None, None, 12.0)
        assert dur == 12.0
        assert source == "heat_count"

    def test_all_none(self):
        dur, source = select_best_duration(None, None, None)
        assert dur is None
        assert source is None


# ---------------------------------------------------------------------------
# Integration tests for extract_competition
# ---------------------------------------------------------------------------


class TestExtractCompetitionIntegration:
    """Integration tests using captured fixture data."""

    @pytest.fixture
    def schedule_data(self):
        return _load_json_fixture("schedule-26009.json")

    @pytest.fixture
    def result_html(self):
        return _load_fixture("result-scratch-race-26008.html")

    @pytest.fixture
    def start_list_html(self):
        return _load_fixture("start-list-sprint-qualifying-26009.html")

    @pytest.fixture
    def tp_result_html(self):
        return _load_fixture("result-team-pursuit-26009.html")

    @pytest.mark.asyncio
    async def test_extract_produces_event_reports_with_categories(self, schedule_data, tp_result_html, start_list_html):
        """Schedule parsing produces EventReport entries with structured categories."""
        with patch("tools.extract_competition.fetch_initial_layout", new_callable=AsyncMock) as mock_fetch, \
             patch("tools.extract_competition.fetch_result_html", new_callable=AsyncMock) as mock_result, \
             patch("tools.extract_competition.fetch_start_list_html", new_callable=AsyncMock) as mock_sl:
            mock_fetch.return_value = schedule_data
            mock_result.return_value = tp_result_html
            mock_sl.return_value = start_list_html

            report, _ = await extract_competition(26009)

        assert len(report.sessions) > 0
        for session in report.sessions:
            for event in session.events:
                assert event.category is not None
                assert event.category.discipline != ""

    @pytest.mark.asyncio
    async def test_incomplete_events_excluded_from_observations(self, schedule_data, tp_result_html, start_list_html):
        """Incomplete events should not appear in duration_observations."""
        with patch("tools.extract_competition.fetch_initial_layout", new_callable=AsyncMock) as mock_fetch, \
             patch("tools.extract_competition.fetch_result_html", new_callable=AsyncMock) as mock_result, \
             patch("tools.extract_competition.fetch_start_list_html", new_callable=AsyncMock) as mock_sl:
            mock_fetch.return_value = schedule_data
            mock_result.return_value = tp_result_html
            mock_sl.return_value = start_list_html

            report, _ = await extract_competition(26009)

        for obs in report.duration_observations:
            # Every observation should reference a completed event
            assert obs.competition_id == 26009
            assert obs.duration_minutes > 0

    @pytest.mark.asyncio
    async def test_multi_session_competition(self, schedule_data, tp_result_html, start_list_html):
        """Multi-session competitions produce records for all sessions."""
        with patch("tools.extract_competition.fetch_initial_layout", new_callable=AsyncMock) as mock_fetch, \
             patch("tools.extract_competition.fetch_result_html", new_callable=AsyncMock) as mock_result, \
             patch("tools.extract_competition.fetch_start_list_html", new_callable=AsyncMock) as mock_sl:
            mock_fetch.return_value = schedule_data
            mock_result.return_value = tp_result_html
            mock_sl.return_value = start_list_html

            report, _ = await extract_competition(26009)

        session_ids = {s.session_id for s in report.sessions}
        assert len(session_ids) >= 1


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestExtractCompetitionEdgeCases:
    """Edge case tests for extraction."""

    @pytest.mark.asyncio
    async def test_invalid_competition_raises(self):
        """Invalid competition ID produces clear error after retry."""
        with patch("tools.extract_competition.fetch_initial_layout", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = httpx.HTTPError("HTTP 404: Not Found")
            with pytest.raises(ValueError, match="Failed to fetch schedule"):
                await extract_competition(99999)

    @pytest.mark.asyncio
    async def test_no_sessions_raises_value_error(self):
        """Competition with no parseable sessions raises ValueError."""
        empty_response = {"jxnobj": [{"cmd": "as", "id": "scheduleview", "data": "<div></div>"}]}
        with patch("tools.extract_competition.fetch_initial_layout", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = empty_response
            with pytest.raises(ValueError, match="No sessions found"):
                await extract_competition(12345)

    def test_duration_source_priority(self):
        """Duration source priority: finish_time > generated_diff > heat_count."""
        dur, source = select_best_duration(10.0, 8.0, 12.0)
        assert source == "finish_time"

        dur, source = select_best_duration(None, 8.0, 12.0)
        assert source == "generated_diff"

        dur, source = select_best_duration(None, None, 12.0)
        assert source == "heat_count"


# ---------------------------------------------------------------------------
# Fetch retry tests
# ---------------------------------------------------------------------------


class TestFetchWithRetry:
    """Test the _fetch_with_retry helper."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """Successful fetch returns the result."""
        result = await _fetch_with_retry(
            lambda: AsyncMock(return_value="ok")(),
            "test fetch",
        )
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        """Fetch succeeds on retry after first failure."""
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.HTTPError("transient")
            return "recovered"

        result = await _fetch_with_retry(flaky, "test fetch")
        assert result == "recovered"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_fail_returns_none(self):
        """All retries failing returns None."""
        async def always_fail():
            raise httpx.HTTPError("permanent")

        result = await _fetch_with_retry(always_fail, "test fetch")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_http_error_caught(self):
        """Non-HTTP exceptions (e.g. JSONDecodeError) are also caught."""
        async def json_fail():
            raise ValueError("Expecting value: line 1 column 1")

        result = await _fetch_with_retry(json_fail, "test fetch")
        assert result is None
