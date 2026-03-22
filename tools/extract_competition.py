"""CLI script to extract historical competition data from tracktiming.live.

Usage:
    python -m tools.extract_competition <competition_id>

Fetches schedule, result pages, and start-list pages for a single competition,
decomposes event names into structured categories, extracts durations using the
same priority as the app (finish time > generated timestamps > heat count),
and writes a JSON report to data/competitions/<competition_id>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.categorizer import categorize_event
from app.config import settings
from app.disciplines import get_changeover, get_default_duration, get_per_heat_duration
from app.fetcher import fetch_initial_layout, fetch_result_html, fetch_start_list_html
from app.models import (
    CompetitionMeta,
    CompetitionReport,
    DurationRecord,
    EventReport,
    EventStatus,
    SessionReport,
    UncategorizedEntry,
)
from app.parser import parse_finish_time, parse_generated_time, parse_heat_count, parse_schedule

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("data/competitions")


# ---------------------------------------------------------------------------
# Duration extraction helpers
# ---------------------------------------------------------------------------

def extract_finish_time_duration(result_html: str, discipline: str) -> float | None:
    """Extract observed duration from a result page Finish Time + changeover.

    Returns total slot duration in minutes, or None if no Finish Time found.
    """
    finish_minutes = parse_finish_time(result_html)
    if finish_minutes is None:
        return None
    return finish_minutes + get_changeover(discipline)


def extract_generated_diff_duration(
    prev_generated: datetime | None,
    curr_generated: datetime | None,
    discipline: str,
) -> float | None:
    """Compute duration from difference between consecutive Generated timestamps.

    Applies a [0.5x, 2.0x] plausibility filter against the static default.
    Returns duration in minutes, or None if implausible or timestamps missing.
    """
    if prev_generated is None or curr_generated is None:
        return None
    diff = (curr_generated - prev_generated).total_seconds() / 60.0
    if diff <= 0:
        return None
    default = get_default_duration(discipline)
    if not (0.5 * default <= diff <= 2.0 * default):
        logger.info(
            "Generated diff %.1f min for %s outside [%.1f, %.1f] plausibility range — skipping",
            diff, discipline, 0.5 * default, 2.0 * default,
        )
        return None
    return diff


def extract_heat_count_duration(start_list_html: str, discipline: str) -> tuple[float | None, int | None]:
    """Extract duration from start-list heat count.

    Returns (total_duration_minutes, heat_count) or (None, None).
    """
    count = parse_heat_count(start_list_html)
    if count is None or count == 0:
        return None, None
    per_heat = get_per_heat_duration(discipline)
    changeover = get_changeover(discipline)
    return count * per_heat + changeover, count


def select_best_duration(
    finish_time_dur: float | None,
    generated_diff_dur: float | None,
    heat_count_dur: float | None,
) -> tuple[float | None, str | None]:
    """Select best duration by source priority.

    Returns (duration_minutes, source_name) or (None, None).
    """
    if finish_time_dur is not None:
        return finish_time_dur, "finish_time"
    if generated_diff_dur is not None:
        return generated_diff_dur, "generated_diff"
    if heat_count_dur is not None:
        return heat_count_dur, "heat_count"
    return None, None


# ---------------------------------------------------------------------------
# CLI orchestration
# ---------------------------------------------------------------------------

_fetch_failure_count: int = 0


async def _fetch_with_retry(coro_factory, description: str, retries: int = 1):
    """Execute an async fetch with one retry on failure.

    Catches all exceptions (not just httpx.HTTPError) so that SSL errors,
    JSON decode errors, and other non-HTTP failures are retried and counted
    rather than crashing the entire extraction.
    """
    global _fetch_failure_count
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            if attempt < retries:
                logger.warning("Retry %s after error: %s", description, exc)
            else:
                logger.error("Failed to fetch %s: %s", description, exc)
                _fetch_failure_count += 1
                return None


async def extract_competition(competition_id: int) -> CompetitionReport:
    """Extract competition data and build a CompetitionReport."""
    # Fetch and parse schedule (with retry for transient errors)
    jxn_data = await _fetch_with_retry(
        lambda: fetch_initial_layout(competition_id),
        f"initial layout for competition {competition_id}",
    )
    if jxn_data is None:
        raise ValueError(f"Failed to fetch schedule for competition {competition_id}")
    sessions = parse_schedule(jxn_data)

    if not sessions:
        raise ValueError(f"No sessions found for competition {competition_id}")

    session_reports: list[SessionReport] = []
    duration_observations: list[DurationRecord] = []
    uncategorized_counts: dict[str, dict] = {}  # event_name -> tracking info

    for session in sessions:
        event_reports: list[EventReport] = []

        # Collect generated timestamps for consecutive diffing within session
        generated_times: dict[int, datetime] = {}  # position -> generated datetime

        # First pass: fetch result pages and start lists for completed events
        result_htmls: dict[int, str | None] = {}
        start_list_htmls: dict[int, str | None] = {}

        for event in session.events:
            if event.status == EventStatus.COMPLETED and not event.is_special:
                if event.result_url:
                    result_htmls[event.position] = await _fetch_with_retry(
                        lambda url=event.result_url: fetch_result_html(url),
                        f"result for {event.name}",
                    )
                if event.start_list_url:
                    start_list_htmls[event.position] = await _fetch_with_retry(
                        lambda url=event.start_list_url: fetch_start_list_html(url),
                        f"start list for {event.name}",
                    )

        # Parse generated timestamps from result pages
        for pos, html in result_htmls.items():
            if html:
                gen_time = parse_generated_time(html)
                if gen_time:
                    generated_times[pos] = gen_time

        # Second pass: extract durations and build event reports
        for event in session.events:
            category, residual = categorize_event(event.name)

            finish_time_dur = None
            generated_diff_dur = None
            heat_count_dur = None
            heat_count = None

            if event.status == EventStatus.COMPLETED and not event.is_special:
                result_html = result_htmls.get(event.position)
                start_list_html = start_list_htmls.get(event.position)

                if result_html:
                    finish_time_dur = extract_finish_time_duration(result_html, category.discipline)

                # Generated diff: use previous event's generated timestamp
                prev_positions = sorted(p for p in generated_times if p < event.position)
                prev_gen = generated_times[prev_positions[-1]] if prev_positions else None
                curr_gen = generated_times.get(event.position)
                generated_diff_dur = extract_generated_diff_duration(
                    prev_gen, curr_gen, category.discipline,
                )

                if start_list_html:
                    heat_count_dur, heat_count = extract_heat_count_duration(
                        start_list_html, category.discipline,
                    )

            duration_minutes, duration_source = select_best_duration(
                finish_time_dur, generated_diff_dur, heat_count_dur,
            )

            # Flag outliers
            if duration_minutes is not None and duration_minutes > 120:
                logger.warning(
                    "Outlier duration %.1f min for %s (session %d, pos %d)",
                    duration_minutes, event.name, session.session_id, event.position,
                )

            event_report = EventReport(
                position=event.position,
                name=event.name,
                category=category,
                status=event.status,
                is_special=event.is_special,
                heat_count=heat_count,
                duration_minutes=duration_minutes,
                duration_source=duration_source,
            )
            event_reports.append(event_report)

            # Build duration observation for completed events with durations
            if (duration_minutes is not None and duration_source is not None
                    and event.status == EventStatus.COMPLETED and not event.is_special):
                duration_observations.append(DurationRecord(
                    category=category,
                    event_name=event.name,
                    heat_count=heat_count,
                    duration_minutes=duration_minutes,
                    per_heat_duration_minutes=None,  # computed by loader, not extractor
                    duration_source=duration_source,
                    competition_id=competition_id,
                    session_id=session.session_id,
                    event_position=event.position,
                ))

            # Track uncategorized events
            if residual:
                key = event.name
                if key not in uncategorized_counts:
                    uncategorized_counts[key] = {
                        "partial_category": category,
                        "unresolved_text": residual,
                        "frequency": 0,
                        "total_duration": 0.0,
                        "has_heats": False,
                    }
                uncategorized_counts[key]["frequency"] += 1
                if duration_minutes:
                    uncategorized_counts[key]["total_duration"] += duration_minutes
                if heat_count:
                    uncategorized_counts[key]["has_heats"] = True

        session_reports.append(SessionReport(
            session_id=session.session_id,
            day=session.day,
            scheduled_start=session.scheduled_start.strftime("%H:%M"),
            events=event_reports,
        ))

    # Build uncategorized summary
    uncategorized_summary: list[UncategorizedEntry] = []
    for event_name, info in uncategorized_counts.items():
        avg_dur = None
        if info["frequency"] > 0 and info["total_duration"] > 0:
            avg_dur = info["total_duration"] / info["frequency"]
        uncategorized_summary.append(UncategorizedEntry(
            event_name=event_name,
            partial_category=info["partial_category"],
            unresolved_text=info["unresolved_text"],
            frequency=info["frequency"],
            avg_duration_minutes=avg_dur,
            has_heats=info["has_heats"],
        ))

    return CompetitionReport(
        version="1.0",
        extracted_at=datetime.now(timezone.utc),
        competition=CompetitionMeta(
            competition_id=competition_id,
            name=f"Competition {competition_id}",
            url=f"{settings.tracktiming_base_url}/eventpage.php?EventId={competition_id}",
        ),
        sessions=session_reports,
        duration_observations=duration_observations,
        uncategorized_summary=uncategorized_summary,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract historical competition data from tracktiming.live. "
        "Fetches schedule, result pages, and start-list pages, then produces "
        "a JSON report with structured categories and duration observations.",
    )
    parser.add_argument(
        "competition_id",
        type=int,
        help="tracktiming.live competition/event ID (e.g. 26008)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    global _fetch_failure_count
    _fetch_failure_count = 0

    try:
        report = asyncio.run(extract_competition(args.competition_id))
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Failed to extract competition %d: %s", args.competition_id, exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("Unexpected error extracting competition %d: %s", args.competition_id, exc, exc_info=True)
        sys.exit(2)

    output_path = OUTPUT_DIR / f"{args.competition_id}.json"
    with open(output_path, "w") as f:
        json.dump(report.model_dump(mode="json"), f, indent=2)

    n_obs = len(report.duration_observations)
    n_uncat = len(report.uncategorized_summary)
    n_sessions = len(report.sessions)
    print(f"Extracted {n_obs} duration observations from {n_sessions} sessions")
    if n_uncat > 0:
        print(f"  {n_uncat} uncategorized event names — see uncategorized_summary in output")
    if _fetch_failure_count > 0:
        print(f"  WARNING: {_fetch_failure_count} page fetches failed — durations may be incomplete")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
