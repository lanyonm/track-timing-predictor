from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app import clock
from app.clock import venue_now
from app.config import Settings

# 12:15 UTC on 2024-06-01 is 08:15 EDT in Toronto.
FROZEN_UTC = datetime(2024, 6, 1, 12, 15, tzinfo=UTC)


def frozen_datetime(utc: datetime) -> type[datetime]:
    """A datetime subclass whose now() is pinned to the given aware UTC instant."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc.astimezone(tz) if tz else utc.replace(tzinfo=None)

    return _Frozen


@pytest.fixture
def frozen_utc():
    with patch("app.clock.datetime", frozen_datetime(FROZEN_UTC)):
        yield


class TestVenueNowFallback:
    def test_converts_utc_to_default_venue_tz(self, frozen_utc):
        assert venue_now() == datetime(2024, 6, 1, 8, 15)

    def test_returns_naive_datetime(self, frozen_utc):
        assert venue_now().tzinfo is None

    def test_honours_configured_tz(self, frozen_utc):
        with patch("app.clock.settings.venue_tz", "America/Vancouver"):
            assert venue_now() == datetime(2024, 6, 1, 5, 15)

    def test_invalid_tz_rejected_at_startup(self):
        with pytest.raises(ValidationError):
            Settings(venue_tz="Not/AZone")


class TestVenueNowInferred:
    """The newest Generated timestamp (venue-local) bounds the venue's UTC offset from below."""

    def test_fresh_result_gives_offset(self, frozen_utc):
        # Venue on UTC+1: result generated a minute ago at 13:14 venue time.
        assert venue_now(datetime(2024, 6, 1, 13, 14)) == datetime(2024, 6, 1, 13, 15)

    def test_negative_offset(self, frozen_utc):
        assert venue_now(datetime(2024, 6, 1, 5, 0)) == datetime(2024, 6, 1, 5, 15)

    def test_result_57_minutes_old_still_correct(self, frozen_utc):
        gen = datetime(2024, 6, 1, 13, 15) - timedelta(minutes=57)
        assert venue_now(gen) == datetime(2024, 6, 1, 13, 15)

    def test_venue_clock_slightly_fast(self, frozen_utc):
        # Timing computer a minute ahead of true time must not push the offset to +2h.
        assert venue_now(datetime(2024, 6, 1, 13, 16)) == datetime(2024, 6, 1, 13, 15)

    def test_implausible_offset_falls_back(self, frozen_utc):
        # A result from the previous evening implies an offset outside UTC−12..+14.
        assert venue_now(datetime(2024, 5, 31, 20, 0)) == datetime(2024, 6, 1, 8, 15)


class TestVenueNowRemembered:
    """An inferred offset is remembered per competition for when inference isn't possible."""

    @pytest.fixture(autouse=True)
    def _clear_offsets(self):
        clock._venue_offsets.clear()
        yield
        clock._venue_offsets.clear()

    def test_remembered_once_results_go_stale(self, frozen_utc):
        assert venue_now(datetime(2024, 6, 1, 13, 14), competition_id=1) == datetime(2024, 6, 1, 13, 15)
        # Session over: no live Generated timestamp
        assert venue_now(None, competition_id=1) == datetime(2024, 6, 1, 13, 15)

    def test_other_competitions_fall_back(self, frozen_utc):
        venue_now(datetime(2024, 6, 1, 13, 14), competition_id=1)
        assert venue_now(None, competition_id=2) == datetime(2024, 6, 1, 8, 15)
        assert venue_now() == datetime(2024, 6, 1, 8, 15)

    def test_stale_result_does_not_lower_the_offset(self, frozen_utc):
        # A long break: the newest result is 70 min old, which infers an hour too low
        venue_now(datetime(2024, 6, 1, 13, 14), competition_id=1)
        stale = datetime(2024, 6, 1, 13, 15) - timedelta(minutes=70)
        assert venue_now(stale, competition_id=1) == datetime(2024, 6, 1, 13, 15)

    def test_higher_offset_replaces_a_low_one(self, frozen_utc):
        # First seen during a break (an hour low), then from a fresh result
        assert venue_now(datetime(2024, 6, 1, 12, 5), competition_id=1) == datetime(2024, 6, 1, 12, 15)
        assert venue_now(datetime(2024, 6, 1, 13, 14), competition_id=1) == datetime(2024, 6, 1, 13, 15)
        assert venue_now(None, competition_id=1) == datetime(2024, 6, 1, 13, 15)

    def test_implausible_offset_uses_the_remembered_one(self, frozen_utc):
        venue_now(datetime(2024, 6, 1, 13, 14), competition_id=1)
        assert venue_now(datetime(2024, 5, 31, 20, 0), competition_id=1) == datetime(2024, 6, 1, 13, 15)
