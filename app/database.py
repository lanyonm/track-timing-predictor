import sqlite3
from contextlib import contextmanager
from decimal import Decimal

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_durations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    competition_id   INTEGER NOT NULL,
    session_id       INTEGER NOT NULL,
    event_position   INTEGER NOT NULL,
    event_name       TEXT NOT NULL,
    discipline       TEXT NOT NULL,
    duration_minutes REAL NOT NULL,
    recorded_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS discipline_overrides (
    discipline       TEXT PRIMARY KEY,
    duration_minutes REAL NOT NULL,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_event_durations_discipline
    ON event_durations(discipline);
"""


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

@contextmanager
def get_db():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    if settings.dynamodb_table:
        return  # DynamoDB table is managed by CDK; nothing to initialise locally
    with get_db() as conn:
        conn.executescript(_SCHEMA)
        try:
            conn.execute("ALTER TABLE event_durations RENAME COLUMN event_id TO competition_id")
        except Exception:
            pass  # Already renamed or column does not exist under the old name


# ---------------------------------------------------------------------------
# DynamoDB backend
# ---------------------------------------------------------------------------
# Single-table design — partition key "pk" only (matches CDK table definition).
#
# Item types:
#   AGGREGATE#<discipline>  — running total_minutes (N) + count (N); used for avg
#   OVERRIDE#<discipline>   — manual override duration_minutes (N)
# ---------------------------------------------------------------------------

def _dynamo_table():
    import boto3
    dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
    return dynamodb.Table(settings.dynamodb_table)


def _dynamo_record_duration(
    discipline: str,
    duration_minutes: float,
) -> None:
    _dynamo_table().update_item(
        Key={"pk": f"AGGREGATE#{discipline}"},
        UpdateExpression="ADD total_minutes :d, #cnt :one",
        ExpressionAttributeNames={"#cnt": "count"},
        ExpressionAttributeValues={
            ":d": Decimal(str(duration_minutes)),
            ":one": 1,
        },
    )


def _dynamo_get_learned_duration(discipline: str) -> float | None:
    try:
        table = _dynamo_table()
        override = table.get_item(Key={"pk": f"OVERRIDE#{discipline}"}).get("Item")
        if override:
            return float(override["duration_minutes"])
        item = table.get_item(Key={"pk": f"AGGREGATE#{discipline}"}).get("Item")
        if item:
            count = int(item.get("count", 0))
            total = float(item.get("total_minutes", 0))
            if count >= settings.min_learned_samples:
                return total / count
    except Exception:
        pass
    return None


def _dynamo_get_all_learned_durations() -> dict[str, tuple[float, int]]:
    from boto3.dynamodb.conditions import Attr
    items = _dynamo_table().scan(
        FilterExpression=Attr("pk").begins_with("AGGREGATE#")
    ).get("Items", [])
    result = {}
    for item in items:
        discipline = item["pk"][len("AGGREGATE#"):]
        count = int(item.get("count", 0))
        total = float(item.get("total_minutes", 0))
        if count > 0:
            result[discipline] = (total / count, count)
    return result


# ---------------------------------------------------------------------------
# Public API — dispatches to DynamoDB when DYNAMODB_TABLE is configured,
# otherwise falls back to SQLite for local development.
# ---------------------------------------------------------------------------

def record_duration(
    competition_id: int,
    session_id: int,
    event_position: int,
    event_name: str,
    discipline: str,
    duration_minutes: float,
) -> None:
    """Insert one observed event duration into the database."""
    if settings.dynamodb_table:
        _dynamo_record_duration(discipline, duration_minutes)
        return
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO event_durations
                (competition_id, session_id, event_position, event_name, discipline, duration_minutes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (competition_id, session_id, event_position, event_name, discipline, duration_minutes),
        )


def get_learned_duration(discipline: str) -> float | None:
    """
    Return the average observed duration for a discipline if we have enough samples.
    Returns None if there are insufficient observations or the DB is not initialized.
    """
    if settings.dynamodb_table:
        return _dynamo_get_learned_duration(discipline)
    try:
        with get_db() as conn:
            # Check for a manual user override first
            override = conn.execute(
                "SELECT duration_minutes FROM discipline_overrides WHERE discipline = ?",
                (discipline,),
            ).fetchone()
            if override:
                return override["duration_minutes"]

            row = conn.execute(
                """
                SELECT AVG(duration_minutes) AS avg_dur, COUNT(*) AS cnt
                FROM event_durations
                WHERE discipline = ?
                """,
                (discipline,),
            ).fetchone()

        if row and row["cnt"] >= settings.min_learned_samples:
            return row["avg_dur"]
    except Exception:
        pass  # DB not yet initialized; fall through to defaults
    return None


def get_all_learned_durations() -> dict[str, tuple[float, int]]:
    """Return all learned durations as {discipline: (avg_minutes, sample_count)}."""
    if settings.dynamodb_table:
        return _dynamo_get_all_learned_durations()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT discipline, AVG(duration_minutes) AS avg_dur, COUNT(*) AS cnt
            FROM event_durations
            GROUP BY discipline
            ORDER BY discipline
            """
        ).fetchall()
    return {r["discipline"]: (r["avg_dur"], r["cnt"]) for r in rows}
