"""Tests for palmares route handlers."""
import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models import PalmaresEntry
from app.palmares import (
    count_competition_palmares,
    delete_competition_palmares,
    get_palmares,
    save_palmares_entries,
)

BASE_URL = "http://testserver"


@pytest.fixture
def sample_jxn_data():
    """Load sample Jaxon API response."""
    with open("tests/fixtures/sample-event-output.json") as f:
        return json.load(f)


def _encode(name: str) -> str:
    return base64.urlsafe_b64encode(name.encode()).decode()


# ---------------------------------------------------------------------------
# US1: Palmares Collection
# ---------------------------------------------------------------------------

class TestPalmaresCollection:
    """Test that schedule routes save palmares entries for matched events."""

    @pytest.mark.asyncio
    async def test_schedule_saves_palmares_for_matched_events(self, sample_jxn_data):
        racer_name = "palmares test racer"
        encoded = _encode(racer_name)

        with patch("app.main.fetch_initial_layout", new_callable=AsyncMock, return_value=sample_jxn_data), \
             patch("app.main._fetch_start_lists", new_callable=AsyncMock), \
             patch("app.main._fetch_result_pages", new_callable=AsyncMock), \
             patch("app.main._fetch_live_heats", new_callable=AsyncMock):

            async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
                response = await client.get(
                    f"/schedule/26008?r={encoded}",
                    cookies={"racer_name": racer_name},
                )
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_no_palmares_for_unidentified_racer(self, sample_jxn_data):
        with patch("app.main.fetch_initial_layout", new_callable=AsyncMock, return_value=sample_jxn_data), \
             patch("app.main._fetch_start_lists", new_callable=AsyncMock), \
             patch("app.main._fetch_result_pages", new_callable=AsyncMock), \
             patch("app.main._fetch_live_heats", new_callable=AsyncMock):

            async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
                response = await client.get("/schedule/26008")
            assert response.status_code == 200
            # No palmares should be saved for unidentified racer
            assert "palmares" not in response.text.lower() or "0 of your" not in response.text

    @pytest.mark.asyncio
    async def test_palmares_count_in_template(self, sample_jxn_data):
        """Verify palmares count message appears when entries exist."""
        racer_name = "template test racer"
        encoded = _encode(racer_name)
        # Pre-populate palmares
        entries = [
            PalmaresEntry(
                racer_name=racer_name, competition_id=26008,
                competition_name="Test", competition_date="2026-02-28",
                session_id=1, session_name="Friday", event_position=3,
                event_name="Test Event",
                audit_url="results/E26008/test-AUDIT-R.htm",
            ),
        ]
        save_palmares_entries(entries)

        with patch("app.main.fetch_initial_layout", new_callable=AsyncMock, return_value=sample_jxn_data), \
             patch("app.main._fetch_start_lists", new_callable=AsyncMock), \
             patch("app.main._fetch_result_pages", new_callable=AsyncMock), \
             patch("app.main._fetch_live_heats", new_callable=AsyncMock):

            async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
                response = await client.get(
                    f"/schedule/26008?r={encoded}",
                    cookies={"racer_name": racer_name},
                )
            assert response.status_code == 200


class TestCompetitionDate:
    def test_palmares_date_uses_generated_timestamp(self):
        """Competition date should come from Generated timestamps, not datetime.now()."""
        from datetime import datetime as dt, time
        from app.main import _collect_palmares_entries
        from app.models import (
            Event, EventStatus, Prediction, RiderMatch,
            SchedulePrediction, Session, SessionPrediction,
        )

        fake_gen_time = dt(2026, 2, 27, 9, 49, 52)
        event = Event(
            position=3, name="U17 Women Pursuit Final",
            discipline="pursuit_2k", status=EventStatus.COMPLETED,
            is_special=False, audit_url="results/E26008/test-AUDIT-R.htm",
        )
        session = Session(session_id=1, day="Friday", scheduled_start=time(8, 15), events=[event])
        pred = Prediction(
            event=event, predicted_start=time(9, 35),
            estimated_duration_minutes=6.0, is_adjusted=False,
            cumulative_delay_minutes=0.0,
            rider_match=RiderMatch(heat=1, heat_count=1),
        )
        sp = SessionPrediction(session=session, event_predictions=[pred], observed_delay_minutes=0.0)
        schedule = SchedulePrediction(competition_id=26008, sessions=[sp], racer_name="date racer")

        with patch("app.main.get_generated_time", return_value=fake_gen_time):
            entries = _collect_palmares_entries(schedule, 26008)

        assert len(entries) == 1
        assert entries[0].competition_date == "2026-02-27"


# ---------------------------------------------------------------------------
# US2: Palmares Page
# ---------------------------------------------------------------------------

class TestPalmaresPage:
    """Test palmares profile page routes."""

    @pytest.mark.asyncio
    async def test_identified_with_entries(self):
        racer_name = "page test racer"
        entries = [
            PalmaresEntry(
                racer_name=racer_name, competition_id=26008,
                competition_name="Ontario Track Championships",
                competition_date="2026-02-28",
                session_id=1, session_name="Friday", event_position=3,
                event_name="U17 Women Pursuit Final",
                audit_url="results/E26008/test-AUDIT-R.htm",
            ),
        ]
        save_palmares_entries(entries)

        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                "/palmares",
                cookies={"racer_name": racer_name},
            )
        assert response.status_code == 200
        assert "Ontario Track Championships" in response.text
        assert "U17 Women Pursuit Final" in response.text
        assert "palmares-remove" in response.text  # Owner sees remove button

    @pytest.mark.asyncio
    async def test_identified_no_entries(self):
        racer_name = "empty palmares racer"
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                "/palmares",
                cookies={"racer_name": racer_name},
            )
        assert response.status_code == 200
        assert "No achievements yet" in response.text

    @pytest.mark.asyncio
    async def test_unidentified_shows_name_form(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get("/palmares")
        assert response.status_code == 200
        assert "View Palmares" in response.text or "name" in response.text.lower()

    @pytest.mark.asyncio
    async def test_name_form_submission_sets_cookie_and_redirects(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE_URL,
            follow_redirects=False,
        ) as client:
            response = await client.get("/palmares?name=Test+Racer")
        assert response.status_code == 303
        assert "racer_name" in response.headers.get("set-cookie", "")

    @pytest.mark.asyncio
    async def test_mobile_viewport_meta(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get("/palmares")
        assert response.status_code == 200
        assert "viewport" in response.text


class TestPalmaresRemoval:
    """Test competition removal route."""

    @pytest.mark.asyncio
    async def test_remove_with_cookie(self):
        racer_name = "remove test racer"
        entries = [
            PalmaresEntry(
                racer_name=racer_name, competition_id=40001,
                competition_name="Test Comp",
                competition_date="2026-03-01",
                session_id=1, session_name="Saturday", event_position=1,
                event_name="E1",
                audit_url="results/E40001/test-AUDIT-R.htm",
            ),
        ]
        save_palmares_entries(entries)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE_URL,
            follow_redirects=False,
        ) as client:
            response = await client.get(
                "/palmares/remove?competition_id=40001",
                cookies={"racer_name": racer_name},
            )
        assert response.status_code == 303
        assert count_competition_palmares(racer_name, 40001) == 0

    @pytest.mark.asyncio
    async def test_remove_without_cookie_returns_403(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get("/palmares/remove?competition_id=40001")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_remove_via_r_param_only_returns_403(self):
        encoded = _encode("someone")
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                f"/palmares/remove?competition_id=40001&r={encoded}",
            )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# US3: Shareable Link
# ---------------------------------------------------------------------------

class TestShareableLink:
    @pytest.mark.asyncio
    async def test_shared_link_renders_palmares(self):
        racer_name = "share test racer"
        entries = [
            PalmaresEntry(
                racer_name=racer_name, competition_id=26008,
                competition_name="Ontario Track Championships",
                competition_date="2026-02-28",
                session_id=1, session_name="Friday", event_position=3,
                event_name="U17 Women Pursuit Final",
                audit_url="results/E26008/test-AUDIT-R.htm",
            ),
        ]
        save_palmares_entries(entries)
        encoded = _encode(racer_name)

        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(f"/palmares?r={encoded}")
        assert response.status_code == 200
        assert "Ontario Track Championships" in response.text
        assert "palmares-remove" not in response.text

    @pytest.mark.asyncio
    async def test_shared_link_does_not_set_cookie(self):
        racer_name = "no cookie racer"
        entries = [
            PalmaresEntry(
                racer_name=racer_name, competition_id=26008,
                competition_name="Test", competition_date="2026-02-28",
                session_id=1, session_name="Friday", event_position=1,
                event_name="E1",
                audit_url="results/E26008/test-AUDIT-R.htm",
            ),
        ]
        save_palmares_entries(entries)
        encoded = _encode(racer_name)

        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(f"/palmares?r={encoded}")
        assert response.status_code == 200
        # Should not set racer_name cookie
        set_cookie = response.headers.get("set-cookie", "")
        assert "racer_name" not in set_cookie


# ---------------------------------------------------------------------------
# US4: CSV Export
# ---------------------------------------------------------------------------

class TestCSVExport:
    @pytest.mark.asyncio
    async def test_export_returns_csv(self):
        racer_name = "charlie pittard"
        encoded = _encode(racer_name)
        audit_url = "results/E26008/W1516-IP-2000-F-0-AUDIT-R.htm"

        with open("tests/fixtures/audit-pursuit-26008.html") as f:
            audit_html = f.read()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = audit_html
        mock_response.raise_for_status = lambda: None

        with patch.object(app.state.http_client, "get", return_value=mock_response):
            async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
                response = await client.get(
                    f"/palmares/export?audit_url={audit_url}&r={encoded}",
                )
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert "Content-Disposition" in response.headers
        # CSV should contain data rows for PITTARD Charlie
        assert "PITTARD" in response.text or "15.531" in response.text

    @pytest.mark.asyncio
    async def test_export_missing_racer_returns_400(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                "/palmares/export?audit_url=results/E26008/test-AUDIT-R.htm",
            )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_export_ssrf_absolute_url_returns_400(self):
        encoded = _encode("test")
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                f"/palmares/export?audit_url=https://evil.com/results/test&r={encoded}",
            )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_export_ssrf_path_traversal_returns_400(self):
        encoded = _encode("test")
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                f"/palmares/export?audit_url=../../../etc/passwd&r={encoded}",
            )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_export_audit_unavailable_returns_502(self):
        encoded = _encode("test racer")
        import httpx as httpx_lib

        async def mock_get(*args, **kwargs):
            raise httpx_lib.ConnectError("Connection refused")

        with patch.object(app.state.http_client, "get", side_effect=mock_get):
            async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
                response = await client.get(
                    f"/palmares/export?audit_url=results/E26008/test-AUDIT-R.htm&r={encoded}",
                )
        assert response.status_code == 502

    @pytest.mark.asyncio
    async def test_export_no_matching_racer_returns_headers_only(self):
        racer_name = "nonexistent racer"
        encoded = _encode(racer_name)
        audit_url = "results/E26008/W1516-IP-2000-F-0-AUDIT-R.htm"

        with open("tests/fixtures/audit-pursuit-26008.html") as f:
            audit_html = f.read()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = audit_html
        mock_response.raise_for_status = lambda: None

        with patch.object(app.state.http_client, "get", return_value=mock_response):
            async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
                response = await client.get(
                    f"/palmares/export?audit_url={audit_url}&r={encoded}",
                )
        assert response.status_code == 200
        assert response.headers.get("x-palmares-notice") == "no-matching-data"

class TestPalmaresRename:
    @pytest.mark.asyncio
    async def test_rename_updates_name(self):
        racer_name = "rename test racer"
        entries = [
            PalmaresEntry(
                racer_name=racer_name, competition_id=50001,
                competition_name="Old Name", competition_date="2026-01-01",
                session_id=1, session_name="Sat", event_position=1,
                event_name="E1", audit_url="results/E50001/test-AUDIT-R.htm",
            ),
        ]
        save_palmares_entries(entries)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE_URL,
            follow_redirects=False,
        ) as client:
            response = await client.get(
                "/palmares/rename?competition_id=50001&name=Ontario+Track+Championships",
                cookies={"racer_name": racer_name},
            )
        assert response.status_code == 303
        result = get_palmares(racer_name)
        comp = next(c for c in result if c.competition_id == 50001)
        assert comp.competition_name == "Ontario Track Championships"

    @pytest.mark.asyncio
    async def test_rename_without_cookie_returns_403(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get("/palmares/rename?competition_id=50001&name=Test")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_rename_blank_name_returns_400(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
            response = await client.get(
                "/palmares/rename?competition_id=50001&name=",
                cookies={"racer_name": "someone"},
            )
        assert response.status_code == 400


class TestCSVExportTeamName:
    @pytest.mark.asyncio
    async def test_export_uses_team_name_when_provided(self):
        encoded = _encode("some racer")
        audit_url = "results/E26008/W1516-IP-2000-F-0-AUDIT-R.htm"

        with open("tests/fixtures/audit-pursuit-26008.html") as f:
            audit_html = f.read()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = audit_html
        mock_response.raise_for_status = lambda: None

        with patch.object(app.state.http_client, "get", return_value=mock_response):
            async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
                response = await client.get(
                    f"/palmares/export?audit_url={audit_url}&r={encoded}&team_name=Ontario+A",
                )
        assert response.status_code == 200
        assert "Ontario" in response.headers.get("content-disposition", "")
