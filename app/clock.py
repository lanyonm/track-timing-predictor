from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings


def venue_now() -> datetime:
    """Current wall-clock time at the venue, naive to match parsed schedule times.

    tracktiming.live times are venue-local with no offset, and the Lambda clock is UTC,
    so a bare datetime.now() would sit hours away from the schedule.
    """
    return datetime.now(ZoneInfo(settings.venue_tz)).replace(tzinfo=None)
