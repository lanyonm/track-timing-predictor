"""Tests for app/main.py route handlers, focused on racer-name functionality."""

import asyncio
import base64
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.main import _fetch_result_pages, _fetch_rider_list_if_needed, _fetch_start_lists, app
from app.models import EventStatus
from app.parser import parse_schedule
from app.predictor import (
    _finish_times,
    _generated_times,
    _heat_counts,
    _live_heats,
    _race_distances,
    _rider_list_retry_at,
    _rider_lists,
    _sprint_deciders,
    _start_list_categories,
    _start_list_riders,
    _status_cache,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# Outside Latin-1, which Starlette uses to encode response headers.
NON_LATIN1_NAMES = ["Łukasz Ćwik", "Jiří Dvořák", "山田太郎"]


def _cookie_name(set_cookie: str) -> str:
    """Decode the racer name from a racer_name Set-Cookie header."""
    value = set_cookie.split(";")[0].split("=", 1)[1]
    assert value.startswith("b64.")
    encoded = value[len("b64.") :]
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()


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
    _finish_times.clear()
    _heat_counts.clear()
    _live_heats.clear()
    _generated_times.clear()
    _start_list_riders.clear()
    _start_list_categories.clear()
    _sprint_deciders.clear()
    _race_distances.clear()
    _rider_lists.clear()
    _rider_list_retry_at.clear()
    yield
    _status_cache.clear()
    _finish_times.clear()
    _heat_counts.clear()
    _live_heats.clear()
    _generated_times.clear()
    _start_list_riders.clear()
    _rider_lists.clear()
    _rider_list_retry_at.clear()


@pytest.fixture(autouse=True)
def mock_fetchers(sample_event_data, start_list_html):
    """Mock all external fetcher calls so tests never hit tracktiming.live."""
    with (
        patch("app.main.fetch_initial_layout", new_callable=AsyncMock, return_value=sample_event_data),
        patch("app.main.fetch_refresh", new_callable=AsyncMock, return_value=sample_event_data),
        # The 26008 fixture links a Rider List; don't feed it start-list HTML.
        patch(
            "app.main.fetch_page_html",
            new_callable=AsyncMock,
            side_effect=lambda _client, path: "" if "RIDERLIST" in path else start_list_html,
        ),
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
        assert _cookie_name(set_cookie) == "Other Name"

    @pytest.mark.parametrize("name", NON_LATIN1_NAMES)
    def test_schedule_with_non_latin1_name(self, client, name):
        encoded = base64.urlsafe_b64encode(name.encode()).decode("ascii")
        response = client.get(f"/schedule/26008?r={encoded}")
        assert response.status_code == 200
        assert f'value="{name}"' in response.text
        assert _cookie_name(response.headers["set-cookie"]) == name

    @pytest.mark.parametrize("name", NON_LATIN1_NAMES)
    def test_set_non_latin1_racer_name(self, client, name):
        response = client.get(
            "/settings/racer-name",
            params={"event_id": 26008, "name": name},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert _cookie_name(response.headers["set-cookie"]) == name

    @pytest.mark.parametrize("name", NON_LATIN1_NAMES)
    def test_encoded_cookie_round_trips(self, client, name):
        set_resp = client.get("/settings/racer-name", params={"event_id": 26008, "name": name}, follow_redirects=False)
        # Secure cookies aren't sent back over http://testserver, so set it by hand
        client.cookies.set("racer_name", set_resp.headers["set-cookie"].split(";")[0].split("=", 1)[1])
        response = client.get("/schedule/26008")
        assert response.status_code == 200
        assert f'value="{name}"' in response.text

    def test_legacy_raw_cookie_is_rewritten_encoded(self, client):
        client.cookies.set("racer_name", "Sean Hall")
        response = client.get("/schedule/26008")
        assert response.headers["set-cookie"].startswith("racer_name=b64.")
        assert _cookie_name(response.headers["set-cookie"]) == "Sean Hall"

    def test_malformed_encoded_cookie_is_ignored(self, client):
        client.cookies.set("racer_name", "b64.!!!")
        response = client.get("/schedule/26008")
        assert response.status_code == 200
        assert "set-cookie" not in response.headers

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
        assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie

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


class TestVenueLocalClock:
    """Routes must compute "now" in the venue's timezone, not the server's (UTC on Lambda)."""

    def _captured_now(self, client, path):
        import app.main as main_module

        with patch("app.main.predict_schedule", wraps=main_module.predict_schedule) as spy:
            resp = client.get(path)
        assert resp.status_code == 200
        return resp, spy.call_args.kwargs["now"]

    @pytest.fixture
    def frozen_toronto_morning(self):
        from tests.test_clock import FROZEN_UTC, frozen_datetime

        with patch("app.clock.datetime", frozen_datetime(FROZEN_UTC)):
            yield

    def test_schedule_falls_back_to_venue_tz(self, client, frozen_toronto_morning):
        # sample-event-output.json has no session in progress, so VENUE_TZ applies.
        resp, now = self._captured_now(client, "/schedule/26008")
        assert now == datetime(2024, 6, 1, 8, 15)
        assert 'id="last-updated" class="font-medium">08:15:00<' in resp.text

    def test_refresh_falls_back_to_venue_tz(self, client, frozen_toronto_morning):
        resp, now = self._captured_now(client, "/schedule/26008/refresh")
        assert now == datetime(2024, 6, 1, 8, 15)
        assert 'data-generated-at="08:15:00"' in resp.text


class TestVenueOffsetInferredFromResults:
    """26037 ran on UTC+1, captured mid-session at 12:43:11 UTC (13:43:11 venue time)."""

    @pytest.fixture(autouse=True)
    def live_26037(self):

        from tests.test_clock import frozen_datetime

        schedule = json.loads((FIXTURE_DIR / "schedule-26037-live.json").read_text())
        results_dir = FIXTURE_DIR / "26037-results"

        async def page(client, url):
            # Tuesday morning result pages as captured; everything else is empty.
            path = results_dir / url.rsplit("/", 1)[-1]
            return path.read_text() if path.exists() else ""

        captured = datetime(2026, 10, 6, 12, 43, 11, tzinfo=UTC)
        with (
            patch("app.clock.datetime", frozen_datetime(captured)),
            patch("app.main.fetch_initial_layout", new_callable=AsyncMock, return_value=schedule),
            patch("app.main.fetch_refresh", new_callable=AsyncMock, return_value=schedule),
            patch("app.main.fetch_page_html", new=page),
        ):
            yield

    @pytest.mark.parametrize("path", ["/schedule/26037", "/schedule/26037/refresh"])
    def test_now_uses_inferred_offset(self, client, path):
        import app.main as main_module

        with patch("app.main.predict_schedule", wraps=main_module.predict_schedule) as spy:
            resp = client.get(path)
        assert resp.status_code == 200
        assert spy.call_args.kwargs["now"] == datetime(2026, 10, 6, 13, 43, 11)
        assert "13:43:11" in resp.text


class TestUseLearnedToggle:
    def test_on_sets_cookie(self, client):
        resp = client.get("/settings/use-learned?event_id=26008&use_learned=on", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/schedule/26008"
        assert "use_learned=true" in resp.headers["set-cookie"]

    def test_off_deletes_cookie(self, client):
        client.cookies.set("use_learned", "true")
        resp = client.get("/settings/use-learned?event_id=26008&use_learned=off", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/schedule/26008"
        assert 'use_learned=""' in resp.headers["set-cookie"]
        assert "Max-Age=0" in resp.headers["set-cookie"]


class TestScheduleErrors:
    def test_unknown_event_returns_404(self, client):
        # Captured response for EventId 99999999: an empty scheduleview.
        unknown = json.loads((FIXTURE_DIR / "schedule-unknown-event.json").read_text())
        with patch("app.main.fetch_initial_layout", new_callable=AsyncMock, return_value=unknown):
            resp = client.get("/schedule/99999999")
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "path,fetcher",
        [
            ("/schedule/26008", "app.main.fetch_initial_layout"),
            ("/schedule/26008/refresh", "app.main.fetch_refresh"),
        ],
    )
    def test_fetch_failure_returns_502_without_exception_text(self, client, path, fetcher):
        err = httpx.ConnectError("secret-upstream-host.internal refused")
        with patch(fetcher, new_callable=AsyncMock, side_effect=err):
            resp = client.get(path)
        assert resp.status_code == 502
        assert "secret-upstream-host" not in resp.text


# ── Rider List fallback (EventId 26037) ─────────────────────────────────────

RIDER_LIST_HTML = (FIXTURE_DIR / "rider-list-26037.html").read_text()


def _load_fixture(name: str) -> dict:
    with (FIXTURE_DIR / name).open() as f:
        return json.load(f)


def _event_row(html: str, event_name: str):
    """The schedule <tr> whose first cell starts with event_name."""
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        td = tr.find("td")
        if td and next(td.stripped_strings, None) == event_name:
            return tr
    raise AssertionError(f"No row for {event_name!r}")


def _rider_list_pages(_client, path: str) -> str:
    # Start lists, results and live pages come back empty: no start-list riders anywhere.
    return RIDER_LIST_HTML if "RIDERLIST" in path else ""


@pytest.fixture
def mock_26037():
    initial = _load_fixture("schedule-26037.json")
    refresh = _load_fixture("refresh-26037.json")
    with (
        patch("app.main.fetch_initial_layout", new_callable=AsyncMock, return_value=initial),
        patch("app.main.fetch_refresh", new_callable=AsyncMock, return_value=refresh),
        patch("app.main.fetch_page_html", new_callable=AsyncMock, side_effect=_rider_list_pages) as page,
    ):
        yield page


def _rider_list_calls(page_mock) -> int:
    return sum(1 for c in page_mock.call_args_list if "RIDERLIST" in c.args[1])


class TestRiderListRoutes:
    def test_entered_event_highlighted(self, client, mock_26037):
        client.cookies.set("racer_name", "Brian Abers")
        response = client.get("/schedule/26037")
        assert response.status_code == 200
        row = _event_row(response.text, "60-64 Men Sprint Qualifying")
        assert "racer-row" in row["class"]
        assert "Entered" in row.get_text()

    def test_rider_list_fetched_once(self, client, mock_26037):
        client.cookies.set("racer_name", "Brian Abers")
        assert client.get("/schedule/26037").status_code == 200
        assert client.get("/schedule/26037/refresh").status_code == 200
        assert _rider_list_calls(mock_26037) == 1

    def test_fetched_without_racer_for_podiums(self, client, mock_26037):
        # Combined-age finals without start-list categories need the Rider List to forecast podiums.
        text = client.get("/schedule/26037").text
        assert _rider_list_calls(mock_26037) == 1
        assert "8 podiums" in _event_row(text, "Medal Ceremonies").get_text()

    def test_not_fetched_without_racer_or_masters_finals(self):
        sessions = parse_schedule(_load_fixture("sample-event-output.json"))
        page = AsyncMock()
        with patch("app.main.fetch_page_html", page):
            assert asyncio.run(_fetch_rider_list_if_needed(None, 26008, {}, sessions, None)) is None
        page.assert_not_called()

    def test_fetch_failure_degrades_then_retries(self, client, mock_26037):
        def failing(_client, path):
            if "RIDERLIST" in path:
                raise httpx.ConnectError("boom")
            return ""

        mock_26037.side_effect = failing
        client.cookies.set("racer_name", "Brian Abers")
        response = client.get("/schedule/26037")
        assert response.status_code == 200
        assert "do not yet have start lists" in response.text
        assert "Entered" not in response.text

        # Inside the retry interval the failing URL isn't fetched again.
        assert client.get("/schedule/26037/refresh").status_code == 200
        assert _rider_list_calls(mock_26037) == 1

        # Once the interval has passed, the next request retries and recovers.
        _rider_list_retry_at.clear()
        mock_26037.side_effect = _rider_list_pages
        response = client.get("/schedule/26037")
        assert _rider_list_calls(mock_26037) == 2
        assert "Entered" in response.text

    def test_empty_rider_list_not_cached(self, client, mock_26037):
        mock_26037.side_effect = lambda _client, path: ""
        client.cookies.set("racer_name", "Brian Abers")
        assert client.get("/schedule/26037").status_code == 200
        assert _rider_list_retry_at
        assert not _rider_lists

    def test_whitespace_racer_name_skips_fetch(self):
        sessions = parse_schedule(_load_fixture("sample-event-output.json"))
        page = AsyncMock()
        with patch("app.main.fetch_page_html", page):
            assert asyncio.run(_fetch_rider_list_if_needed(None, 26008, {}, sessions, "   ")) is None
        page.assert_not_called()

    def test_rider_list_matches_not_saved_to_palmares(self, client, mock_26037):
        # ALVIS Norman (M6064, TP) matches the completed 55-64 Men Team Pursuit
        # events from the Rider List; they have audit URLs but no start-list riders.
        client.cookies.set("racer_name", "Norman Alvis")
        with patch("app.main.save_palmares_entries") as save:
            response = client.get("/schedule/26037")
        assert response.status_code == 200
        assert "55-64 Men Team Pursuit Qualifying" in response.text
        assert "racer-row" in _event_row(response.text, "55-64 Men Team Pursuit Qualifying")["class"]
        save.assert_not_called()

    def test_tentative_event_rendering(self, client, mock_26037):
        client.cookies.set("racer_name", "Brian Abers")
        response = client.get("/schedule/26037")
        row = _event_row(response.text, "60-64 Men Sprint 1/4 Final Ride 1")
        assert "If advancing" in row.get_text()
        assert row.get("aria-label") == "Your event, if advancing"
        assert "racer-row" not in row.get("class", [])

    def test_rider_list_banners(self, client, mock_26037):
        client.cookies.set("racer_name", "Brian Abers")
        text = " ".join(client.get("/schedule/26037").text.split())
        assert 'Found 13 events for "Brian Abers" (10 if advancing)' in text
        assert "Events without start lists matched from the Rider List (M6064: S, TS, TT)." in text
        assert "do not yet have start lists" not in text
        assert "Start lists are not yet published" not in text

    def test_racer_not_in_rider_list(self, client, mock_26037):
        client.cookies.set("racer_name", "Nobody Here")
        text = client.get("/schedule/26037").text
        assert "do not yet have start lists" in text
        assert "matched from the Rider List" not in text

    def test_next_race_from_rider_list(self, client, mock_26037):
        client.cookies.set("racer_name", "Brian Abers")
        text = " ".join(client.get("/schedule/26037").text.split())
        assert re.search(r"Your next race: 60-64 Men Sprint Qualifying at \d{2}:\d{2}", text)
        assert "60-64 Men Sprint Qualifying (if advancing)" not in text


class TestFetchStartListsCaching:
    """_fetch_start_lists refetch rules for empty and completed start lists."""

    @pytest.fixture
    def sessions(self):
        return parse_schedule(_load_fixture("schedule-26037.json"))

    @staticmethod
    def _run(sessions, html):
        page = AsyncMock(side_effect=lambda _client, _path: html)
        with patch("app.main.fetch_page_html", page):
            asyncio.run(_fetch_start_lists(None, 26037, sessions))
        return page.call_count

    def test_completed_empty_start_lists_fetched_once(self, sessions):
        with_lists = [e for s in sessions for e in s.events if e.start_list_url]
        completed = [e for e in with_lists if e.status == EventStatus.COMPLETED]
        assert completed
        assert self._run(sessions, "") == len(with_lists)
        # Second pass: only non-completed events with empty lists are retried.
        assert self._run(sessions, "") == len(with_lists) - len(completed)

    def test_records_categories(self, sessions):
        html = (FIXTURE_DIR / "start-list-points-race-combined-26037.html").read_text()
        self._run(sessions, html)
        assert _start_list_categories
        assert set(_start_list_categories.values()) == {frozenset({"W5054", "W5559", "W6064", "W6569", "W7074"})}

    def test_records_race_distance(self, sessions):
        html = (FIXTURE_DIR / "start-list-points-race-combined-26037.html").read_text()
        self._run(sessions, html)
        assert _race_distances
        assert set(_race_distances.values()) == {10.0}

    def test_empty_parse_keeps_cached_riders(self, sessions, start_list_html):
        self._run(sessions, start_list_html)
        cached = {k: v for k, v in _start_list_riders.items() if v}
        assert cached
        _heat_counts.clear()  # force a refetch of every start list
        self._run(sessions, "")
        for key, riders in cached.items():
            assert _start_list_riders[key] == riders


class TestParallelQualifierRoute:
    def test_be_ready_by(self, client, mock_26037):
        client.cookies.set("racer_name", "Paul Baisch")
        text = " ".join(client.get("/schedule/26037").text.split())
        assert re.search(
            r"Your next race: 55-59 Men Scratch Race Qualifier 1 \(or a later qualifier\), be ready by \d{2}:\d{2}",
            text,
        )
        for name in ("55-59 Men Scratch Race Qualifier 1", "55-59 Men Scratch Race Qualifier 2"):
            assert "Entered" in _event_row(client.get("/schedule/26037").text, name).get_text()


class TestFetchResultPagesDeciders:
    def test_records_deciders_from_completed_rides(self):
        sessions = parse_schedule(_load_fixture("schedule-26037.json"))
        html = (FIXTURE_DIR / "result-sprint-quarter-final-26037.html").read_text()
        page = AsyncMock(side_effect=lambda _client, _path: html)
        with patch("app.main.fetch_page_html", page):
            asyncio.run(_fetch_result_pages(None, 26037, sessions))
        completed_rounds = {
            e.name.rsplit(" Ride ", 1)[0]
            for s in sessions
            for e in s.events
            if e.discipline == "sprint_match" and " Ride " in e.name and e.result_url
        }
        assert completed_rounds
        assert {k[1] for k in _sprint_deciders} == completed_rounds
        assert set(_sprint_deciders.values()) == {1}
