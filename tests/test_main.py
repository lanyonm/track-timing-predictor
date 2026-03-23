"""Tests for app/main.py route handlers, focused on racer-name functionality."""
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.predictor import (
    _generated_times,
    _heat_counts,
    _live_heats,
    _observed_durations,
    _start_list_riders,
    _status_cache,
)
from app.models import normalize_rider_name

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_EVENT_PATH = FIXTURE_DIR / "sample-event-output.json"
START_LIST_PATH = FIXTURE_DIR / "start-list-sample.html"


@pytest.fixture(scope="module")
def sample_event_data():
    with SAMPLE_EVENT_PATH.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def start_list_html():
    return START_LIST_PATH.read_text()


@pytest.fixture(autouse=True)
def clear_predictor_caches():
    """Clear all in-memory predictor caches before each test."""
    _status_cache.clear()
    _observed_durations.clear()
    _heat_counts.clear()
    _live_heats.clear()
    _generated_times.clear()
    _start_list_riders.clear()
    yield
    _status_cache.clear()
    _observed_durations.clear()
    _heat_counts.clear()
    _live_heats.clear()
    _generated_times.clear()
    _start_list_riders.clear()


@pytest.fixture(autouse=True)
def mock_fetchers(sample_event_data, start_list_html):
    """Mock all external fetcher calls so tests never hit tracktiming.live."""
    with (
        patch("app.main.fetch_initial_layout", new_callable=AsyncMock, return_value=sample_event_data),
        patch("app.main.fetch_refresh", new_callable=AsyncMock, return_value=sample_event_data),
        patch("app.main.fetch_page_html", new_callable=AsyncMock, return_value=start_list_html),
    ):
        yield


@pytest.fixture
def client():
    return TestClient(app)


class TestRacerNameRoutes:

    def test_schedule_with_base64_racer_name(self, client):
        """GET /schedule/26008?r=<base64> includes the racer name in the form input."""
        encoded = base64.urlsafe_b64encode(b"Sean Hall").decode("ascii")
        response = client.get(f"/schedule/26008?r={encoded}")
        assert response.status_code == 200
        assert 'value="Sean Hall"' in response.text

    def test_schedule_with_cookie(self, client):
        """GET /schedule/26008 with racer_name cookie includes the name in the form."""
        client.cookies.set("racer_name", "Sean Hall")
        response = client.get("/schedule/26008")
        assert response.status_code == 200
        assert 'value="Sean Hall"' in response.text

    def test_url_param_takes_precedence_over_cookie(self, client):
        """URL ?r= param overrides cookie; response sets cookie to new name."""
        client.cookies.set("racer_name", "Sean Hall")
        encoded = base64.urlsafe_b64encode(b"Other Name").decode("ascii")
        response = client.get(f"/schedule/26008?r={encoded}")
        assert response.status_code == 200
        assert 'value="Other Name"' in response.text
        # The response should set a cookie updating racer_name to "Other Name"
        set_cookie = response.headers.get("set-cookie", "")
        assert "racer_name" in set_cookie
        assert "Other Name" in set_cookie or "Other+Name" in set_cookie or "Other%20Name" in set_cookie

    def test_set_racer_name_redirect(self, client):
        """GET /settings/racer-name?event_id=26008&name=Sean Hall redirects with ?r= and fragment."""
        response = client.get(
            "/settings/racer-name?event_id=26008&name=Sean Hall",
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers["location"]
        assert location.startswith("/schedule/26008")
        assert "?r=" in location
        assert location.endswith("#schedule-container")
        set_cookie = response.headers.get("set-cookie", "")
        assert "racer_name" in set_cookie

    def test_clear_racer_name_redirect(self, client):
        """GET /settings/racer-name?event_id=26008 (no name) clears the cookie."""
        response = client.get(
            "/settings/racer-name?event_id=26008",
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers["location"]
        assert location == "/schedule/26008"
        # Should delete the cookie (max-age=0 signals deletion)
        set_cookie = response.headers.get("set-cookie", "")
        assert "racer_name" in set_cookie
        assert 'Max-Age=0' in set_cookie or 'max-age=0' in set_cookie

    def test_empty_name_clears(self, client):
        """GET /settings/racer-name?event_id=26008&name= behaves like clear."""
        response = client.get(
            "/settings/racer-name?event_id=26008&name=",
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers["location"]
        assert location == "/schedule/26008"
        set_cookie = response.headers.get("set-cookie", "")
        assert "racer_name" in set_cookie

    def test_malformed_base64_no_error(self, client):
        """Malformed base64 in ?r= should not cause a 500; schedule loads normally."""
        response = client.get("/schedule/26008?r=!!!invalid")
        assert response.status_code == 200

    def test_malformed_base64_falls_back_to_cookie(self, client):
        """Malformed ?r= falls back to cookie rather than dropping the name entirely."""
        client.cookies.set("racer_name", "Sean Hall")
        response = client.get("/schedule/26008?r=!!!invalid")
        assert response.status_code == 200
        assert 'value="Sean Hall"' in response.text

    def test_secure_cookie_flag(self, client):
        """Set-Cookie for racer_name should include the Secure flag."""
        response = client.get(
            "/settings/racer-name?event_id=26008&name=Test",
            follow_redirects=False,
        )
        set_cookie = response.headers.get("set-cookie", "")
        assert "Secure" in set_cookie

    def test_refresh_endpoint_with_racer_name(self, client):
        """GET /schedule/26008/refresh?r=<base64> returns partial with racer context."""
        encoded = base64.urlsafe_b64encode(b"Sean Hall").decode("ascii")
        response = client.get(f"/schedule/26008/refresh?r={encoded}")
        assert response.status_code == 200
        # The partial should contain schedule HTML (details/table structure)
        assert "<details" in response.text


class TestHealthEndpoint:
    """Tests for the /health endpoint (US6)."""

    def test_health_returns_healthy_status(self):
        """Health endpoint returns 200 with per-component healthy status."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "components" in data
        assert data["components"]["database"]["status"] == "healthy"

    def test_health_returns_degraded_on_bad_db(self):
        """Health endpoint returns 200 with degraded status when DB is unreachable."""
        from app.config import settings
        original = settings.db_path
        settings.db_path = "/nonexistent/path/to/db.sqlite"
        try:
            client = TestClient(app)
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "degraded"
            assert data["components"]["database"]["status"] == "degraded"
            assert "detail" in data["components"]["database"]
        finally:
            settings.db_path = original


class TestScheduleRedirect:
    """Tests for the GET /schedule redirect route (US1)."""

    def test_redirect_with_valid_event_id(self):
        """GET /schedule?event_id=26008 redirects to /schedule/26008."""
        client = TestClient(app, follow_redirects=False)
        response = client.get("/schedule?event_id=26008")
        assert response.status_code == 303
        assert response.headers["location"] == "/schedule/26008"

    def test_redirect_missing_event_id(self):
        """GET /schedule without event_id returns 422."""
        client = TestClient(app, follow_redirects=False)
        response = client.get("/schedule")
        assert response.status_code == 422


class TestCheckHealth:
    """Unit tests for the check_health() function in database.py (constitution II compliance)."""

    @pytest.mark.asyncio
    async def test_check_health_sqlite_healthy(self):
        """check_health returns healthy for a valid SQLite DB."""
        from app.database import check_health
        result = await check_health()
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_check_health_sqlite_degraded(self):
        """check_health returns degraded when SQLite DB path is invalid."""
        from app.config import settings
        from app.database import check_health
        original = settings.db_path
        settings.db_path = "/nonexistent/impossible/path.db"
        try:
            result = await check_health()
            assert result["status"] == "degraded"
            assert "detail" in result
            assert "SQLite" in result["detail"]
        finally:
            settings.db_path = original
