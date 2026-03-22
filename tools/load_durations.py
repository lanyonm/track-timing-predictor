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
from app.database import DuplicateRowsError, deduplicate_event_durations, init_db, record_duration_structured
from app.disciplines import get_changeover, get_default_duration, get_per_heat_duration, DEFAULT_DURATIONS
from app.models import CompetitionReport, DurationRecord

logger = logging.getLogger(__name__)


def _validate_duration_bounds(record: DurationRecord) -> bool:
    """Check if duration is within [0.5x, 2.0x] of the expected duration.

    When heat_count is available, the expected duration is computed from
    heat_count * per_heat_duration + changeover (matching how the extraction
    script derived it). Otherwise, falls back to the static default.

    Returns True if valid, False if out of bounds.
    """
    if record.heat_count is not None and record.heat_count > 0:
        expected = (record.heat_count * get_per_heat_duration(record.category.discipline)
                    + get_changeover(record.category.discipline))
    else:
        expected = get_default_duration(record.category.discipline)
    lo = 0.5 * expected
    hi = 2.0 * expected
    return lo <= record.duration_minutes <= hi


def _compute_per_heat_duration(record: DurationRecord) -> float | None:
    """Compute per-heat duration when both duration and heat_count are present.

    Inverse of the forward formula: total = count * per_heat + changeover
    Therefore: per_heat = (duration_minutes - changeover) / heat_count
    """
    if record.heat_count is None or record.heat_count == 0:
        return None
    changeover = get_changeover(record.category.discipline)
    per_heat = (record.duration_minutes - changeover) / record.heat_count
    if per_heat <= 0:
        logger.warning(
            "Negative per-heat duration %.2f for %s (duration=%.1f, heats=%d, changeover=%.1f)",
            per_heat, record.category.discipline, record.duration_minutes, record.heat_count, changeover,
        )
        return None
    return per_heat


def load_report(report: CompetitionReport) -> dict[str, int]:
    """Load duration observations from a report into the learning database.

    Returns summary counts: loaded, updated, unchanged, skipped_bounds, warnings.
    """
    stats = {"loaded": 0, "updated": 0, "unchanged": 0, "skipped_bounds": 0, "warnings": 0}

    for record in report.duration_observations:
        # Validate duration bounds
        if not _validate_duration_bounds(record):
            logger.warning(
                "Out-of-bounds duration %.1f min for %s (competition %d, session %d, pos %d) — skipping",
                record.duration_minutes, record.category.discipline,
                record.competition_id, record.session_id, record.event_position,
            )
            stats["skipped_bounds"] += 1
            continue

        # Warn for unrecognized disciplines
        if record.category.discipline not in DEFAULT_DURATIONS and record.category.discipline != "exhibition":
            logger.warning(
                "Unrecognized discipline '%s' for event '%s' — storing anyway",
                record.category.discipline, record.event_name,
            )
            stats["warnings"] += 1

        # Compute per-heat duration
        per_heat = _compute_per_heat_duration(record)

        try:
            outcome = record_duration_structured(
                competition_id=record.competition_id,
                session_id=record.session_id,
                event_position=record.event_position,
                event_name=record.event_name,
                discipline=record.category.discipline,
                duration_minutes=record.duration_minutes,
                classification=record.category.classification,
                gender=record.category.gender,
                per_heat_duration_minutes=per_heat,
            )
            if outcome == "created":
                stats["loaded"] += 1
            elif outcome == "updated":
                stats["updated"] += 1
            elif outcome == "unchanged":
                stats["unchanged"] += 1
            else:
                stats["warnings"] += 1
        except Exception:
            logger.error(
                "Failed to record duration for %s (competition %d, session %d, pos %d)",
                record.category.discipline, record.competition_id, record.session_id,
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Automatically deduplicate existing rows without prompting",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        init_db()
    except DuplicateRowsError as exc:
        print(f"\nThe database at {settings.db_path} has {exc.duplicate_count} "
              "duplicate rows that conflict with the new unique index.")
        print("The most recent row for each (competition_id, session_id, "
              "event_position) will be kept; older duplicates will be removed.\n")
        if args.force:
            proceed = True
        else:
            answer = input(f"Remove {exc.duplicate_count} duplicate rows? [y/N] ").strip().lower()
            proceed = answer in ("y", "yes")
        if not proceed:
            print("Aborted. No changes made.")
            sys.exit(1)
        deleted = deduplicate_event_durations()
        print(f"Removed {deleted} duplicate rows.\n")

    total_stats = {"loaded": 0, "updated": 0, "unchanged": 0, "skipped_bounds": 0, "warnings": 0}

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
        print(f"  {filepath.name}: {stats['loaded']} loaded, "
              f"{stats['updated']} updated, {stats['unchanged']} unchanged, "
              f"{stats['skipped_bounds']} out-of-bounds")

    print(f"\nTotal: {total_stats['loaded']} loaded, "
          f"{total_stats['updated']} updated, {total_stats['unchanged']} unchanged, "
          f"{total_stats['skipped_bounds']} out-of-bounds, "
          f"{total_stats['warnings']} warnings")

    if total_stats["warnings"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
