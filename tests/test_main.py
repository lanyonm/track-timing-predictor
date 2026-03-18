"""Tests for app/main.py — racer name resolution and settings routes."""
import base64
import time as time_module
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import _resolve_racer_name, app

client = TestClient(app)


# ── _resolve_racer_name ──────────────────────────────────────────────────────


class TestResolveRacerName:
    def test_url_param_takes_priority_over_cookie(self):
        request = MagicMock()
        request.cookies = {"racer_name": "Cookie Name"}
        encoded = base64.urlsafe_b64encode(b"URL Name").decode()
        result = _resolve_racer_name(request, r=encoded)
        assert result == "URL Name"

    def test_invalid_base64_falls_back_to_cookie(self):
        request = MagicMock()
        request.cookies = {"racer_name": "Fallback Name"}
        result = _resolve_racer_name(request, r="not-valid-base64!!!")
        assert result == "Fallback Name"

    def test_invalid_base64_no_cookie_returns_none(self):
        request = MagicMock()
        request.cookies = {}
        result = _resolve_racer_name(request, r="not-valid-base64!!!")
        assert result is None

    def test_cookie_only_resolves_name(self):
        request = MagicMock()
        request.cookies = {"racer_name": "Sean Hall"}
        result = _resolve_racer_name(request, r=None)
        assert result == "Sean Hall"

    def test_no_param_no_cookie_returns_none(self):
        request = MagicMock()
        request.cookies = {}
        result = _resolve_racer_name(request, r=None)
        assert result is None


# ── /settings/racer-name ─────────────────────────────────────────────────────


class TestSetRacerNameRoute:
    def test_set_racer_name_sets_cookie_and_redirects(self):
        response = client.get(
            "/settings/racer-name?event_id=123&name=Sean+Hall",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "racer_name" in response.cookies
        assert "/schedule/123?r=" in response.headers["location"]
        assert "#schedule-container" in response.headers["location"]

    def test_clear_racer_name_deletes_cookie_and_redirects(self):
        response = client.get(
            "/settings/racer-name?event_id=123",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "/schedule/123" in response.headers["location"]
        assert "?r=" not in response.headers["location"]


# ── Performance (SC-006 smoke test) ─────────────────────────────────────────


class TestPerformance:
    def test_racer_name_route_responds_quickly(self):
        """SC-006 smoke test: name-related routes should respond in <1s."""
        start = time_module.monotonic()
        client.get("/settings/racer-name?event_id=123&name=Test+Racer", follow_redirects=False)
        elapsed = time_module.monotonic() - start
        assert elapsed < 1.0, f"Setting racer name took {elapsed:.2f}s, expected <1s"

    def test_clear_name_route_responds_quickly(self):
        start = time_module.monotonic()
        client.get("/settings/racer-name?event_id=123", follow_redirects=False)
        elapsed = time_module.monotonic() - start
        assert elapsed < 1.0, f"Clearing racer name took {elapsed:.2f}s, expected <1s"
