"""Tests for app/main.py route handlers."""
import base64
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import app, _resolve_racer_name
from app.predictor import _start_list_riders, get_start_list_riders


@pytest.fixture
def client():
    return TestClient(app)


SAMPLE_START_LIST_HTML = (
    '<table><tbody>'
    '<tr><td><h4>Heat 1</h4></td><td><h4><Strong>212</Strong></h4></td><td><h4>PITTARD Charlie</h4></td></tr>'
    '<tr><td><h4>Heat 2</h4></td><td><h4><Strong>211</Strong></h4></td><td><h4>RANKL Avery</h4></td></tr>'
    '<tr><td><h4>Heat 3</h4></td><td><h4><Strong>215</Strong></h4></td><td><h4>ALDEN Calla</h4></td></tr>'
    '</tbody></table>'
)


# ── T004: _fetch_start_lists populates rider cache ──────────────────────────


class TestFetchStartListsPopulatesRiderCache:
    def setup_method(self):
        _start_list_riders.clear()

    @pytest.mark.anyio
    async def test_fetch_start_lists_stores_riders(self):
        from app.main import _fetch_start_lists
        from app.models import Session, Event, EventStatus
        from datetime import time

        sessions = [Session(
            session_id=1,
            day="Friday",
            scheduled_start=time(8, 15),
            events=[Event(
                position=0,
                name="U17 Women Sprint Qualifying",
                discipline="sprint_qualifying",
                status=EventStatus.UPCOMING,
                is_special=False,
                start_list_url="results/E26008/test-S.htm",
            )],
        )]

        with patch("app.main.fetch_start_list_html", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = SAMPLE_START_LIST_HTML
            await _fetch_start_lists(26008, sessions)

        riders = get_start_list_riders(26008, 1, 0)
        assert len(riders) == 3
        assert riders[0].name == "PITTARD Charlie"
        assert riders[0].heat == 1
        assert riders[2].heat == 3


# ── T021: _resolve_racer_name URL priority ──────────────────────────────────


class TestResolveRacerName:
    def test_url_param_takes_priority_over_cookie(self, client):
        """URL ?r= param overrides cookie — shared links work for recipients."""
        # Simulate: cookie has "Alice", URL has Base64("Bob")
        encoded_bob = base64.urlsafe_b64encode(b"Bob").decode("ascii")

        # Build a mock request with a racer_name cookie
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/schedule/26008",
            "query_string": b"",
            "headers": [(b"cookie", f"racer_name=Alice".encode())],
        }
        request = Request(scope)
        result = _resolve_racer_name(request, encoded_bob)
        assert result == "Bob"

    def test_cookie_fallback_when_no_url_param(self, client):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/schedule/26008",
            "query_string": b"",
            "headers": [(b"cookie", b"racer_name=Alice")],
        }
        request = Request(scope)
        result = _resolve_racer_name(request, None)
        assert result == "Alice"

    def test_returns_none_when_no_source(self, client):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/schedule/26008",
            "query_string": b"",
            "headers": [],
        }
        request = Request(scope)
        result = _resolve_racer_name(request, None)
        assert result is None


# ── T022: Invalid Base64 handling ───────────────────────────────────────────


class TestInvalidBase64:
    def test_invalid_base64_returns_none_despite_cookie(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/schedule/26008",
            "query_string": b"",
            "headers": [(b"cookie", b"racer_name=Alice")],
        }
        request = Request(scope)
        result = _resolve_racer_name(request, "!!!not-valid-base64!!!")
        assert result is None

    def test_invalid_base64_no_cookie_returns_none(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/schedule/26008",
            "query_string": b"",
            "headers": [],
        }
        request = Request(scope)
        result = _resolve_racer_name(request, "!!!not-valid-base64!!!")
        assert result is None


# ── T023: Cookie auto-apply flow ────────────────────────────────────────────


class TestCookieAutoApply:
    def test_set_racer_name_sets_cookie_and_redirects(self, client):
        response = client.get(
            "/settings/racer-name?event_id=26008&name=Sean+Hall",
            follow_redirects=False,
        )
        assert response.status_code == 303
        cookie_header = response.headers.get("set-cookie", "")
        assert "racer_name=" in cookie_header
        assert "Sean Hall" in cookie_header
        location = response.headers["location"]
        assert "/schedule/26008?r=" in location
        assert "#schedule-container" in location


# ── T024: Clear-name flow ───────────────────────────────────────────────────


# ── T024b: Timing assertion ──────────────────────────────────────────────


class TestRacerNamePerformance:
    def test_name_matching_overhead_under_1s(self, client):
        """Smoke test: racer name resolution adds <1s overhead (SC-006)."""
        import time as time_mod

        # Measure with racer name
        encoded = base64.urlsafe_b64encode(b"Sean Hall").decode("ascii")
        start = time_mod.perf_counter()
        # Just test the resolve function directly (route would need mocked API)
        scope = {
            "type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": [],
        }
        req = Request(scope)
        for _ in range(100):
            _resolve_racer_name(req, encoded)
        elapsed = time_mod.perf_counter() - start
        assert elapsed < 1.0, f"100 resolve calls took {elapsed:.2f}s, expected <1s"


# ── T024: Clear-name flow ───────────────────────────────────────────────────


class TestClearName:
    def test_clear_racer_name_deletes_cookie_and_redirects(self, client):
        # First set the cookie
        client.get("/settings/racer-name?event_id=26008&name=Sean+Hall", follow_redirects=False)
        # Then clear it
        response = client.get(
            "/settings/racer-name?event_id=26008",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/schedule/26008"
        # Cookie should be deleted (max-age=0 or explicit delete)
        cookie_header = response.headers.get("set-cookie", "")
        assert "racer_name" in cookie_header
