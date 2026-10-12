import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings

# Allows the venue's timing computer to run a little fast without the
# inferred offset rounding up to the next hour.
_CLOCK_SKEW = timedelta(minutes=2)


# Venue UTC offset last inferred per competition, per container. Used when there's
# no live Generated timestamp to infer from (a long break, after a session ends).
_venue_offsets: dict[int, timedelta] = {}


def venue_now(latest_generated: datetime | None = None, competition_id: int | None = None) -> datetime:
    """Current wall-clock time at the venue, naive to match parsed schedule times.

    tracktiming.live times are venue-local with no offset, and the Lambda clock is UTC.
    When the newest result-page Generated timestamp of a live session is supplied, the
    venue's UTC offset is inferred from it: Generated can't be later than venue-local
    now, so rounding (Generated − UTC now) up to the whole hour recovers the offset as
    long as the result is under ~58 minutes old. An older result infers an offset too
    low, so with a competition_id the highest offset inferred is remembered, and used
    whenever inference gives a lower one or none. Otherwise falls back to VENUE_TZ.
    """
    utc_now = datetime.now(UTC)
    naive_utc = utc_now.replace(tzinfo=None)
    remembered = _venue_offsets.get(competition_id) if competition_id is not None else None
    if latest_generated is not None:
        hours = math.ceil((latest_generated - naive_utc - _CLOCK_SKEW) / timedelta(hours=1))
        if -12 <= hours <= 14:
            offset = timedelta(hours=hours)
            if remembered is None or offset > remembered:
                remembered = offset
                if competition_id is not None:
                    _venue_offsets[competition_id] = offset
            return naive_utc + remembered
    if remembered is not None:
        return naive_utc + remembered
    return utc_now.astimezone(ZoneInfo(settings.venue_tz)).replace(tzinfo=None)
