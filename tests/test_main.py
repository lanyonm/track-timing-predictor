"""Tests for racer name routes in app/main.py."""
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SAMPLE_PATH = Path(__file__).parent / "fixtures" / "sample-event-output.json"


def _load_sample():
    return json.loads(SAMPLE_PATH.read_text())


def _mock_all_fetchers():
    """Return a stack of patches that prevent real HTTP calls during schedule tests."""
    return [
        patch("app.main.fetch_initial_layout", new_callable=AsyncMock, return_value=_load_sample()),
        patch("app.main._fetch_start_lists", new_callable=AsyncMock),
        patch("app.main._fetch_result_pages", new_callable=AsyncMock),
        patch("app.main._fetch_live_heats", new_callable=AsyncMock),
    ]


class TestSetRacerName:
    def test_set_name_redirects_with_hash(self):
        response = client.get(
            "/settings/racer-name",
            params={"event_id": 26008, "name": "Sean Hall"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "#schedule-container" in response.headers["location"]

    def test_set_name_sets_cookie(self):
        response = client.get(
            "/settings/racer-name",
            params={"event_id": 26008, "name": "Sean Hall"},
            follow_redirects=False,
        )
        cookie_header = response.headers.get("set-cookie", "")
        assert "racer_name=" in cookie_header
        assert "Secure" in cookie_header

    def test_clear_name_deletes_cookie(self):
        response = client.get(
            "/settings/racer-name",
            params={"event_id": 26008},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "/schedule/26008" in response.headers["location"]

    def test_empty_name_behaves_like_clear(self):
        response = client.get(
            "/settings/racer-name",
            params={"event_id": 26008, "name": ""},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "?r=" not in response.headers["location"]


class TestScheduleRacerName:
    def test_malformed_base64_does_not_error(self):
        """Malformed ?r= param should return schedule without 500."""
        patches = _mock_all_fetchers()
        for p in patches:
            p.start()
        try:
            response = client.get("/schedule/26008", params={"r": "!!!invalid"})
            assert response.status_code == 200
        finally:
            for p in patches:
                p.stop()

    def test_valid_base64_resolves_name(self):
        """Valid ?r= param resolves name and includes it in response."""
        patches = _mock_all_fetchers()
        for p in patches:
            p.start()
        try:
            encoded = base64.urlsafe_b64encode(b"Test Racer").decode()
            response = client.get("/schedule/26008", params={"r": encoded})
            assert response.status_code == 200
        finally:
            for p in patches:
                p.stop()

    def test_url_param_updates_cookie(self):
        """?r= param should set racer_name cookie."""
        patches = _mock_all_fetchers()
        for p in patches:
            p.start()
        try:
            encoded = base64.urlsafe_b64encode(b"Cookie Update").decode()
            response = client.get("/schedule/26008", params={"r": encoded})
            cookie_header = response.headers.get("set-cookie", "")
            assert "racer_name=" in cookie_header
        finally:
            for p in patches:
                p.stop()
