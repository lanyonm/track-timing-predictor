import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings

# Allows the venue's timing computer to run a little fast without the
# inferred offset rounding up to the next hour.
_CLOCK_SKEW = timedelta(minutes=2)


def venue_now(latest_generated: datetime | None = None) -> datetime:
    """Current wall-clock time at the venue, naive to match parsed schedule times.

    tracktiming.live times are venue-local with no offset, and the Lambda clock is UTC.
    When the newest result-page Generated timestamp of a live session is supplied, the
    venue's UTC offset is inferred from it: Generated can't be later than venue-local
    now, so rounding (Generated − UTC now) up to the whole hour recovers the offset as
    long as the result is under ~58 minutes old. Otherwise falls back to VENUE_TZ.
    """
    utc_now = datetime.now(UTC)
    if latest_generated is not None:
        naive_utc = utc_now.replace(tzinfo=None)
        hours = math.ceil((latest_generated - naive_utc - _CLOCK_SKEW) / timedelta(hours=1))
        if -12 <= hours <= 14:
            return naive_utc + timedelta(hours=hours)
    return utc_now.astimezone(ZoneInfo(settings.venue_tz)).replace(tzinfo=None)
