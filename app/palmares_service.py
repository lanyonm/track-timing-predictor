"""Palmares collection from schedule views and audit-page CSV export.

Storage lives in palmares.py; this module decides what to store and builds the export.
"""

import asyncio
import logging
import posixpath
from urllib.parse import unquote

import httpx

from app.audit_parser import filter_rider_data, format_csv, parse_audit_riders
from app.models import PalmaresEntry, SchedulePrediction
from app.palmares import count_competition_palmares, save_palmares_entries
from app.predictor import get_generated_time

logger = logging.getLogger(__name__)

# Disciplines that produce per-lap/sector audit data (pursuits + time trials)
TIMED_DISCIPLINES = frozenset(
    {
        "pursuit_4k",
        "pursuit_3k",
        "pursuit_2k",
        "team_pursuit",
        "team_sprint",
        "time_trial_500",
        "time_trial_750",
        "time_trial_kilo",
        "time_trial_generic",
    }
)

# Audit pages are ~25 KB; anything far larger isn't one.
MAX_AUDIT_CHARS = 2_000_000


def collect_palmares_entries(
    schedule: SchedulePrediction,
    competition_id: int,
) -> list[PalmaresEntry]:
    """Collect palmares entries from schedule predictions.

    Filters for timed events (pursuits and time trials) where the racer
    was matched on a start list, has an audit URL, and is not a special event.
    Rider List matches don't prove the racer rode, so they are skipped.
    """
    if not schedule.racer_name:
        return []

    comp_name = f"Competition {competition_id}"

    # The competition date is the earliest result-page Generated timestamp. It's missing
    # when no result or audit page could be fetched yet; saves never overwrite an entry,
    # so collect nothing and let a later view store the entries with their date.
    comp_date = None
    for sp in schedule.sessions:
        for pred in sp.event_predictions:
            gen_time = get_generated_time(competition_id, sp.session.session_id, pred.event.position)
            if gen_time is not None:
                d = gen_time.date().isoformat()
                if comp_date is None or d < comp_date:
                    comp_date = d
    if comp_date is None:
        return []

    entries = []
    for sp in schedule.sessions:
        for pred in sp.event_predictions:
            if (
                pred.rider_match
                and pred.rider_match.source == "start_list"
                and pred.event.audit_url
                and not pred.event.is_special
                and pred.event.discipline in TIMED_DISCIPLINES
            ):
                entries.append(
                    PalmaresEntry(
                        racer_name=schedule.racer_name,
                        competition_id=competition_id,
                        competition_name=comp_name,
                        competition_date=comp_date,
                        session_id=sp.session.session_id,
                        session_name=sp.session.day,
                        event_position=pred.event.position,
                        event_name=pred.event.name,
                        audit_url=pred.event.audit_url,
                        team_name=pred.rider_match.team_name,
                    )
                )
    return entries


async def save_and_count_palmares(
    schedule: SchedulePrediction,
    competition_id: int,
) -> int:
    """Save matched palmares entries and return the count for the competition.

    The database calls run in a worker thread. Returns 0 if no racer name is set or on error.
    """
    racer_name = schedule.racer_name
    if not racer_name:
        return 0

    def save_and_count(entries: list[PalmaresEntry]) -> int:
        if entries:
            save_palmares_entries(entries)
        return count_competition_palmares(racer_name, competition_id)

    try:
        return await asyncio.to_thread(save_and_count, collect_palmares_entries(schedule, competition_id))
    except Exception:
        logger.warning("Palmares save failed", exc_info=True)
        return 0


def safe_audit_path(audit_url: str) -> str | None:
    """Return the normalised upstream path, or None unless it stays under results/.

    SSRF protection: percent-encoding and ".." are resolved before the prefix check.
    """
    path = posixpath.normpath(unquote(audit_url))
    if "://" in path or not path.startswith("results/"):
        return None
    return path


async def fetch_audit_page(client: httpx.AsyncClient, path: str) -> str:
    """GET an audit page; raises on an HTTP error or a page too large to be one."""
    resp = await client.get(path)
    resp.raise_for_status()
    if len(resp.text) > MAX_AUDIT_CHARS:
        raise ValueError(f"audit page too large ({len(resp.text)} chars)")
    return resp.text


def audit_csv(html: str, path: str, filter_name: str) -> tuple[str, str, bool]:
    """Return (file name, CSV, whether any row matched filter_name) for an audit page."""
    filtered = filter_rider_data(parse_audit_riders(html), filter_name)
    event_name = path.split("/")[-1].replace("-AUDIT-R.htm", "")
    return f"{event_name}-{filter_name}.csv", format_csv(filtered, event_name), bool(filtered)
