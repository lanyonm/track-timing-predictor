from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.clock import venue_now
from app.config import Settings

# 12:15 UTC on 2024-06-01 is 08:15 EDT in Toronto.
FROZEN_UTC = datetime(2024, 6, 1, 12, 15, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_UTC.astimezone(tz) if tz else FROZEN_UTC.replace(tzinfo=None)


@pytest.fixture
def frozen_utc():
    with patch("app.clock.datetime", _FrozenDatetime):
        yield


class TestVenueNow:
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
