"""CLI script to rebuild DynamoDB learned-duration aggregates from OBS# items.

Usage:
    DYNAMODB_TABLE=track-timing-prod python -m tools.rebuild_aggregates          # dry run
    DYNAMODB_TABLE=track-timing-prod python -m tools.rebuild_aggregates --apply  # write

Scans every OBS# item, recomputes every AGGREGATE# level from them, and prints a
per-key diff. With --apply it overwrites each changed aggregate with put_item and
deletes any AGGREGATE# key that no OBS# item supports. OVERRIDE# items are left
alone. Run it while no competition is live: a live write landing between the scan
and the writes can be dropped. Re-running is safe and a dry run afterwards should
report no changes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from decimal import Decimal

from app.config import settings
from app.database import _build_aggregate_keys, _dynamo_table

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Aggregate:
    total_minutes: Decimal
    count: int

    def average(self) -> float:
        return float(self.total_minutes) / self.count if self.count else 0.0


@dataclass(frozen=True)
class Change:
    key: str
    before: Aggregate | None
    after: Aggregate | None

    @property
    def action(self) -> str:
        if self.after is None:
            return "delete"
        if self.before is None:
            return "create"
        return "update"


def _scan_all(table) -> list[dict]:
    response = table.scan()
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return items


def compute_aggregates(obs_items: list[dict]) -> dict[str, Aggregate]:
    """Sum OBS# items into every aggregate level they contribute to."""
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for item in obs_items:
        discipline = item.get("discipline")
        duration = item.get("duration_minutes")
        if not discipline or duration is None:
            logger.warning("Skipping OBS item %s with no discipline or duration", item["pk"])
            continue
        classification = item.get("classification") or None
        gender = item.get("gender") or None
        for key in _build_aggregate_keys(discipline, classification, gender):
            totals[key] = totals.get(key, Decimal(0)) + Decimal(duration)
            counts[key] = counts.get(key, 0) + 1
    return {key: Aggregate(totals[key], counts[key]) for key in totals}


def plan_changes(items: list[dict]) -> list[Change]:
    """Diff the stored AGGREGATE# items against the sums over OBS# items."""
    obs_items = [i for i in items if i["pk"].startswith("OBS#")]
    current = {
        i["pk"]: Aggregate(Decimal(i.get("total_minutes", 0)), int(i.get("count", 0)))
        for i in items
        if i["pk"].startswith("AGGREGATE#")
    }
    expected = compute_aggregates(obs_items)
    changes = []
    for key in sorted(current.keys() | expected.keys()):
        before, after = current.get(key), expected.get(key)
        if before != after:
            changes.append(Change(key, before, after))
    return changes


def apply_changes(table, changes: list[Change]) -> None:
    for change in changes:
        if change.after is None:
            table.delete_item(Key={"pk": change.key})
        else:
            table.put_item(
                Item={
                    "pk": change.key,
                    "total_minutes": change.after.total_minutes,
                    "count": change.after.count,
                }
            )


def _fmt(agg: Aggregate | None) -> str:
    if agg is None:
        return "—"
    return f"{agg.count} × {agg.average():.1f} min"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild DynamoDB AGGREGATE# items from OBS# items. Dry run by default.",
    )
    parser.add_argument("--apply", action="store_true", help="Write the changes (default: dry run)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not settings.dynamodb_table:
        print("DYNAMODB_TABLE is not set; this tool only rebuilds DynamoDB aggregates.")
        sys.exit(1)

    table = _dynamo_table()
    items = _scan_all(table)
    obs_count = sum(1 for i in items if i["pk"].startswith("OBS#"))
    changes = plan_changes(items)

    print(f"Table {settings.dynamodb_table}: {obs_count} OBS# items, {len(changes)} aggregate changes\n")
    width = max((len(c.key) for c in changes), default=0)
    for c in changes:
        print(f"  {c.action:<6}  {c.key:<{width}}  {_fmt(c.before):>18}  →  {_fmt(c.after)}")

    if not changes:
        return
    if not args.apply:
        print("\nDry run; re-run with --apply to write these changes.")
        return
    apply_changes(table, changes)
    print(f"\nApplied {len(changes)} changes.")


if __name__ == "__main__":
    main()
