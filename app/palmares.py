"""Palmares storage — dual SQLite/DynamoDB backend.

Stores racer achievement entries (timed events with audit links) grouped
by competition. Follows the same dual-backend dispatch pattern as database.py.
"""
import asyncio
import logging
import sqlite3
from datetime import datetime, timezone

from app.config import settings
from app.database import _raise_if_auth_error, get_db

try:
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    _BotoError = (BotoCoreError, ClientError)
except ImportError:
    _BotoError = ()  # type: ignore[assignment]
    NoCredentialsError = None  # type: ignore[assignment,misc]

from app.models import PalmaresCompetition, PalmaresEntry

logger = logging.getLogger(__name__)

_PALMARES_SCHEMA = """
CREATE TABLE IF NOT EXISTS palmares_entries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    racer_name        TEXT NOT NULL,
    competition_id    INTEGER NOT NULL,
    competition_name  TEXT NOT NULL,
    competition_date  TEXT,
    session_id        INTEGER NOT NULL,
    session_name      TEXT NOT NULL,
    event_position    INTEGER NOT NULL,
    event_name        TEXT NOT NULL,
    audit_url         TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(racer_name, competition_id, session_id, event_position)
);

CREATE INDEX IF NOT EXISTS idx_palmares_racer ON palmares_entries(racer_name);
"""


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

def init_palmares_db() -> None:
    """Create the palmares_entries table if it doesn't exist (SQLite only)."""
    if settings.palmares_table:
        return  # DynamoDB table managed by CDK
    with get_db() as conn:
        conn.executescript(_PALMARES_SCHEMA)
        _migrate_palmares_schema(conn)


def _migrate_palmares_schema(conn: sqlite3.Connection) -> None:
    """Add team_name column if missing (for databases created before team event support)."""
    cursor = conn.execute("PRAGMA table_info(palmares_entries)")
    existing = {row[1] for row in cursor.fetchall()}
    if "team_name" not in existing:
        conn.execute("ALTER TABLE palmares_entries ADD COLUMN team_name TEXT DEFAULT NULL")


def _save_entries_sqlite(entries: list[PalmaresEntry]) -> int:
    """Insert palmares entries, ignoring duplicates. Returns count inserted."""
    if not entries:
        return 0
    inserted = 0
    with get_db() as conn:
        for entry in entries:
            try:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO palmares_entries
                        (racer_name, competition_id, competition_name, competition_date,
                         session_id, session_name, event_position, event_name, audit_url,
                         team_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (entry.racer_name, entry.competition_id, entry.competition_name,
                     entry.competition_date, entry.session_id, entry.session_name,
                     entry.event_position, entry.event_name, entry.audit_url,
                     entry.team_name),
                )
                if cursor.rowcount > 0:
                    inserted += 1
            except sqlite3.Error:
                logger.error("SQLite error saving palmares entry for %s comp=%d pos=%d",
                             entry.racer_name, entry.competition_id, entry.event_position,
                             exc_info=True)
    return inserted


def _get_palmares_sqlite(racer_name: str) -> list[PalmaresCompetition]:
    """Get all palmares entries grouped by competition, reverse chronological."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT racer_name, competition_id, competition_name, competition_date,
                   session_id, session_name, event_position, event_name, audit_url,
                   team_name
            FROM palmares_entries
            WHERE racer_name = ?
            ORDER BY competition_date DESC, competition_id DESC,
                     session_id ASC, event_position ASC
            """,
            (racer_name,),
        ).fetchall()

    return _group_by_competition(rows)


def _count_competition_sqlite(racer_name: str, competition_id: int) -> int:
    """Count palmares entries for a specific competition."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM palmares_entries WHERE racer_name = ? AND competition_id = ?",
            (racer_name, competition_id),
        ).fetchone()
    return row["cnt"] if row else 0


def _update_competition_sqlite(racer_name: str, competition_id: int, competition_name: str) -> int:
    """Update competition name for all entries. Returns rows updated."""
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE palmares_entries SET competition_name = ? WHERE racer_name = ? AND competition_id = ?",
            (competition_name, racer_name, competition_id),
        )
    return cursor.rowcount


def _delete_competition_sqlite(racer_name: str, competition_id: int) -> int:
    """Delete all palmares entries for a competition. Returns count deleted."""
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM palmares_entries WHERE racer_name = ? AND competition_id = ?",
            (racer_name, competition_id),
        )
    return cursor.rowcount


# ---------------------------------------------------------------------------
# DynamoDB backend
# ---------------------------------------------------------------------------

_palmares_table_cache = None


def _palmares_dynamo_table():
    global _palmares_table_cache
    if _palmares_table_cache is None:
        import boto3
        dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
        _palmares_table_cache = dynamodb.Table(settings.palmares_table)
    return _palmares_table_cache


def _save_entries_dynamo(entries: list[PalmaresEntry]) -> int:
    """Insert palmares entries, skipping existing items. Returns count written.

    Uses conditional put_item (not batch_writer) so existing items are
    not overwritten — this preserves custom competition names set via
    /palmares/rename.
    """
    if not entries:
        return 0
    table = _palmares_dynamo_table()
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    for entry in entries:
        item = {
            "pk": f"RACER#{entry.racer_name}",
            "sk": f"COMP#{entry.competition_id}#S#{entry.session_id}#E#{entry.event_position}",
            "competition_name": entry.competition_name,
            "competition_date": entry.competition_date or "",
            "session_name": entry.session_name,
            "event_name": entry.event_name,
            "audit_url": entry.audit_url,
            "created_at": now,
        }
        if entry.team_name:
            item["team_name"] = entry.team_name
        try:
            table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(pk)",
            )
            written += 1
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                continue  # Item already exists — skip (like INSERT OR IGNORE)
            raise
    return written


def _get_palmares_dynamo(racer_name: str) -> list[PalmaresCompetition]:
    """Query all palmares entries for a racer from DynamoDB."""
    from boto3.dynamodb.conditions import Key
    table = _palmares_dynamo_table()
    pk = f"RACER#{racer_name}"

    response = table.query(KeyConditionExpression=Key("pk").eq(pk))
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.query(
            KeyConditionExpression=Key("pk").eq(pk),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    # Convert DynamoDB items to row-like dicts
    rows = []
    for item in items:
        try:
            sk = item["sk"]
            # Parse sk: COMP#{comp_id}#S#{session_id}#E#{position}
            parts = sk.split("#")
            rows.append({
                "racer_name": racer_name,
                "competition_id": int(parts[1]),
                "competition_name": item.get("competition_name", ""),
                "competition_date": item.get("competition_date") or None,
                "session_id": int(parts[3]),
                "session_name": item.get("session_name", ""),
                "event_position": int(parts[5]),
                "event_name": item.get("event_name", ""),
                "audit_url": item.get("audit_url", ""),
                "team_name": item.get("team_name") or None,
            })
        except (IndexError, ValueError, KeyError):
            logger.error("Malformed palmares DynamoDB item sk=%r, skipping", item.get("sk"))

    # Sort: reverse chronological by date/competition, then session/position ascending
    rows.sort(key=lambda r: (
        r["competition_date"] or "",
        r["competition_id"],
    ), reverse=True)
    # Within each competition, sort by session_id then position
    return _group_by_competition(rows)


def _count_competition_dynamo(racer_name: str, competition_id: int) -> int:
    """Count palmares entries for a specific competition in DynamoDB."""
    from boto3.dynamodb.conditions import Key
    table = _palmares_dynamo_table()
    pk = f"RACER#{racer_name}"
    sk_prefix = f"COMP#{competition_id}#"

    response = table.query(
        KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(sk_prefix),
        Select="COUNT",
    )
    return response.get("Count", 0)


def _update_competition_dynamo(racer_name: str, competition_id: int, competition_name: str) -> int:
    """Update competition name for all entries in DynamoDB."""
    from boto3.dynamodb.conditions import Key
    table = _palmares_dynamo_table()
    pk = f"RACER#{racer_name}"
    sk_prefix = f"COMP#{competition_id}#"

    response = table.query(
        KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(sk_prefix),
        ProjectionExpression="pk, sk",
    )
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(sk_prefix),
            ProjectionExpression="pk, sk",
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    for item in items:
        table.update_item(
            Key={"pk": item["pk"], "sk": item["sk"]},
            UpdateExpression="SET competition_name = :n",
            ExpressionAttributeValues={":n": competition_name},
        )
    return len(items)


def _delete_competition_dynamo(racer_name: str, competition_id: int) -> int:
    """Delete all palmares entries for a competition from DynamoDB."""
    from boto3.dynamodb.conditions import Key
    table = _palmares_dynamo_table()
    pk = f"RACER#{racer_name}"
    sk_prefix = f"COMP#{competition_id}#"

    response = table.query(
        KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(sk_prefix),
        ProjectionExpression="pk, sk",
    )
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(sk_prefix),
            ProjectionExpression="pk, sk",
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    return len(items)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _group_by_competition(rows: list[dict]) -> list[PalmaresCompetition]:
    """Group rows into PalmaresCompetition objects, preserving row order for
    competition ordering and sorting entries within each competition."""
    competitions: dict[int, PalmaresCompetition] = {}
    order: list[int] = []

    entry_fields = (
        "racer_name", "competition_id", "competition_name", "competition_date",
        "session_id", "session_name", "event_position", "event_name", "audit_url",
        "team_name",
    )
    for row in rows:
        comp_id = row["competition_id"]
        entry = PalmaresEntry(**{k: row[k] for k in entry_fields})

        if comp_id not in competitions:
            competitions[comp_id] = PalmaresCompetition(
                competition_id=comp_id,
                competition_name=entry.competition_name,
                competition_date=entry.competition_date,
                entries=[],
            )
            order.append(comp_id)
        competitions[comp_id].entries.append(entry)

    # Sort entries within each competition by session_id, then position
    for comp in competitions.values():
        comp.entries.sort(key=lambda e: (e.session_id, e.event_position))

    return [competitions[cid] for cid in order]


# ---------------------------------------------------------------------------
# Public API — dispatches to DynamoDB or SQLite
# ---------------------------------------------------------------------------

def save_palmares_entries(entries: list[PalmaresEntry]) -> int:
    """Save palmares entries, ignoring duplicates. Returns count saved."""
    if not entries:
        return 0
    try:
        if settings.palmares_table:
            return _save_entries_dynamo(entries)
        return _save_entries_sqlite(entries)
    except Exception as exc:
        _raise_if_auth_error(exc)
        logger.error("Failed to save palmares entries", exc_info=True)
        return 0


def get_competition_name(racer_name: str, competition_id: int) -> str | None:
    """Return the stored competition name, or None if no entries exist."""
    try:
        if settings.palmares_table:
            from boto3.dynamodb.conditions import Key
            table = _palmares_dynamo_table()
            resp = table.query(
                KeyConditionExpression=(
                    Key("pk").eq(f"RACER#{racer_name}")
                    & Key("sk").begins_with(f"COMP#{competition_id}#")
                ),
                ProjectionExpression="competition_name",
                Limit=1,
            )
            items = resp.get("Items", [])
            return items[0]["competition_name"] if items else None
        with get_db() as conn:
            row = conn.execute(
                "SELECT competition_name FROM palmares_entries WHERE racer_name = ? AND competition_id = ? LIMIT 1",
                (racer_name, competition_id),
            ).fetchone()
        return row["competition_name"] if row else None
    except Exception as exc:
        _raise_if_auth_error(exc)
        logger.error("Failed to get competition name for %s comp=%d", racer_name, competition_id, exc_info=True)
        return None


def get_palmares(racer_name: str) -> list[PalmaresCompetition]:
    """Get all palmares entries grouped by competition, reverse chronological."""
    try:
        if settings.palmares_table:
            return _get_palmares_dynamo(racer_name)
        return _get_palmares_sqlite(racer_name)
    except Exception as exc:
        _raise_if_auth_error(exc)
        logger.error("Failed to get palmares for %s", racer_name, exc_info=True)
        return []


def count_competition_palmares(racer_name: str, competition_id: int) -> int:
    """Count palmares entries for a specific competition."""
    try:
        if settings.palmares_table:
            return _count_competition_dynamo(racer_name, competition_id)
        return _count_competition_sqlite(racer_name, competition_id)
    except Exception as exc:
        _raise_if_auth_error(exc)
        logger.error("Failed to count palmares for %s competition %d",
                      racer_name, competition_id, exc_info=True)
        return 0


def update_competition_palmares(racer_name: str, competition_id: int, competition_name: str) -> int:
    """Update competition name for all entries. Returns rows updated."""
    try:
        if settings.palmares_table:
            return _update_competition_dynamo(racer_name, competition_id, competition_name)
        return _update_competition_sqlite(racer_name, competition_id, competition_name)
    except Exception as exc:
        _raise_if_auth_error(exc)
        logger.error("Failed to update palmares for %s competition %d",
                      racer_name, competition_id, exc_info=True)
        return 0


def delete_competition_palmares(racer_name: str, competition_id: int) -> int:
    """Delete all palmares entries for a competition. Returns count deleted."""
    try:
        if settings.palmares_table:
            return _delete_competition_dynamo(racer_name, competition_id)
        return _delete_competition_sqlite(racer_name, competition_id)
    except Exception as exc:
        _raise_if_auth_error(exc)
        logger.error("Failed to delete palmares for %s competition %d",
                      racer_name, competition_id, exc_info=True)
        return 0


async def check_palmares_health() -> dict[str, str]:
    """Check palmares table connectivity. Returns health status dict."""
    backend = "DynamoDB" if settings.palmares_table else "SQLite"

    def _check_sqlite():
        with get_db() as conn:
            conn.execute("SELECT 1 FROM palmares_entries LIMIT 1")

    try:
        if settings.palmares_table:
            await asyncio.to_thread(lambda: _palmares_dynamo_table().table_status)
        else:
            await asyncio.to_thread(_check_sqlite)
        return {"status": "healthy"}
    except Exception:
        logger.warning("Palmares health check failed (%s)", backend, exc_info=True)
        return {"status": "degraded", "detail": f"Palmares {backend} connection failed"}
