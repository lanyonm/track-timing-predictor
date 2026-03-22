"""CLI script to load extracted competition data into the learning database.

Usage:
    python -m tools.load_durations data/competitions/26008.json [more files...]

Reads JSON report files produced by extract_competition.py and writes duration
observations into the app's learning database (SQLite or DynamoDB based on
DYNAMODB_TABLE env var). Uses natural key (competition_id, session_id,
event_position) for idempotent upsert.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.config import settings
from app.database import init_db, record_duration_structured
from app.disciplines import get_changeover, get_default_duration, DEFAULT_DURATIONS
from app.models import CompetitionReport, DurationRecord

logger = logging.getLogger(__name__)


def _validate_duration_bounds(record: DurationRecord) -> bool:
    """Check if duration is within [0.5x, 2.0x] of the static default.

    Returns True if valid, False if out of bounds.
    """
    default = get_default_duration(record.discipline)
    lo = 0.5 * default
    hi = 2.0 * default
    return lo <= record.duration_minutes <= hi


def _compute_per_heat_duration(record: DurationRecord) -> float | None:
    """Compute per-heat duration when both duration and heat_count are present.

    per_heat = (duration_minutes / heat_count) - changeover
    """
    if record.heat_count is None or record.heat_count == 0:
        return None
    changeover = get_changeover(record.discipline)
    per_heat = (record.duration_minutes / record.heat_count) - changeover
    return per_heat if per_heat > 0 else None


def load_report(report: CompetitionReport) -> dict[str, int]:
    """Load duration observations from a report into the learning database.

    Returns summary counts: loaded, skipped_duplicate, skipped_bounds, warnings.
    """
    stats = {"loaded": 0, "skipped_bounds": 0, "warnings": 0}

    for record in report.duration_observations:
        # Validate duration bounds
        if not _validate_duration_bounds(record):
            logger.warning(
                "Out-of-bounds duration %.1f min for %s (competition %d, session %d, pos %d) — skipping",
                record.duration_minutes, record.discipline,
                record.competition_id, record.session_id, record.event_position,
            )
            stats["skipped_bounds"] += 1
            continue

        # Warn for unrecognized disciplines
        if record.discipline not in DEFAULT_DURATIONS and record.discipline != "exhibition":
            logger.warning(
                "Unrecognized discipline '%s' for event '%s' — storing anyway",
                record.discipline, record.event_name,
            )
            stats["warnings"] += 1

        # Compute per-heat duration
        per_heat = _compute_per_heat_duration(record)

        try:
            record_duration_structured(
                competition_id=record.competition_id,
                session_id=record.session_id,
                event_position=record.event_position,
                event_name=record.event_name,
                discipline=record.discipline,
                duration_minutes=record.duration_minutes,
                classification=record.classification,
                gender=record.gender,
                per_heat_duration_minutes=per_heat,
            )
            stats["loaded"] += 1
        except Exception:
            logger.error(
                "Failed to record duration for %s (competition %d, session %d, pos %d)",
                record.discipline, record.competition_id, record.session_id,
                record.event_position, exc_info=True,
            )
            stats["warnings"] += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load extracted competition data into the learning database. "
        "Reads JSON report files and writes duration observations using "
        "natural key (competition_id, session_id, event_position) for "
        "idempotent upsert.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Path(s) to JSON report files (e.g. data/competitions/26008.json)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    init_db()

    total_stats = {"loaded": 0, "skipped_bounds": 0, "warnings": 0}

    for filepath in args.files:
        if not filepath.exists():
            logger.error("File not found: %s", filepath)
            sys.exit(1)

        try:
            with open(filepath) as f:
                data = json.load(f)
            report = CompetitionReport(**data)
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.error("Failed to parse %s: %s", filepath, exc)
            sys.exit(1)

        stats = load_report(report)
        for k in total_stats:
            total_stats[k] += stats[k]
        print(f"  {filepath.name}: {stats['loaded']} loaded, {stats['skipped_bounds']} out-of-bounds")

    print(f"\nTotal: {total_stats['loaded']} loaded, "
          f"{total_stats['skipped_bounds']} out-of-bounds, "
          f"{total_stats['warnings']} warnings")


if __name__ == "__main__":
    main()
