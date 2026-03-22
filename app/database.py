import logging
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from typing import Literal

try:
    from botocore.exceptions import BotoCoreError, ClientError
    _BotoError = (BotoCoreError, ClientError)
except ImportError:  # boto3 not installed (local dev without AWS deps)
    _BotoError = ()  # type: ignore[assignment]

from app.config import settings

logger = logging.getLogger(__name__)

RecordOutcome = Literal["created", "updated", "unchanged", "error"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_durations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    competition_id   INTEGER NOT NULL,
    session_id       INTEGER NOT NULL,
    event_position   INTEGER NOT NULL,
    event_name       TEXT NOT NULL,
    discipline       TEXT NOT NULL,
    duration_minutes REAL NOT NULL,
    classification   TEXT DEFAULT NULL,
    gender           TEXT DEFAULT NULL,
    per_heat_duration_minutes REAL DEFAULT NULL,
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
        _migrate_schema(conn)


class DuplicateRowsError(Exception):
    """Raised when the unique index cannot be created due to duplicate rows."""
    def __init__(self, duplicate_count: int):
        self.duplicate_count = duplicate_count
        super().__init__(
            f"{duplicate_count} duplicate rows must be removed before the "
            "unique index on (competition_id, session_id, event_position) "
            "can be created"
        )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add classification, gender, per_heat_duration_minutes columns if missing.

    Safe for production DBs created before structured categories existed.
    Existing rows keep NULL for new columns — correct behavior since they
    contribute to Level 1 (discipline-only) aggregates.

    Raises DuplicateRowsError if the unique index cannot be created because
    the existing data contains duplicate natural keys.
    """
    cursor = conn.execute("PRAGMA table_info(event_durations)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    for col, col_type in [
        ("classification", "TEXT DEFAULT NULL"),
        ("gender", "TEXT DEFAULT NULL"),
        ("per_heat_duration_minutes", "REAL DEFAULT NULL"),
    ]:
        if col not in existing_columns:
            conn.execute(f"ALTER TABLE event_durations ADD COLUMN {col} {col_type}")

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_event_durations_category
        ON event_durations(discipline, classification, gender)
    """)

    try:
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_event_durations_natural_key
            ON event_durations(competition_id, session_id, event_position)
        """)
    except sqlite3.IntegrityError:
        dup_count = conn.execute("""
            SELECT COUNT(*) FROM event_durations
            WHERE id NOT IN (
                SELECT MAX(id) FROM event_durations
                GROUP BY competition_id, session_id, event_position
            )
        """).fetchone()[0]
        raise DuplicateRowsError(dup_count)


def deduplicate_event_durations() -> int:
    """Remove duplicate rows, keeping the most recent for each natural key.

    Returns the number of rows deleted.
    """
    with get_db() as conn:
        cursor = conn.execute("""
            DELETE FROM event_durations
            WHERE id NOT IN (
                SELECT MAX(id) FROM event_durations
                GROUP BY competition_id, session_id, event_position
            )
        """)
        deleted = cursor.rowcount
        # Now create the unique index
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_event_durations_natural_key
            ON event_durations(competition_id, session_id, event_position)
        """)
    return deleted


# ---------------------------------------------------------------------------
# DynamoDB backend
# ---------------------------------------------------------------------------
# Single-table design — partition key "pk" only (matches CDK table definition).
#
# Item types:
#   AGGREGATE#<discipline>  — running total_minutes (N) + count (N); used for avg
#   OVERRIDE#<discipline>   — manual override duration_minutes (N)
# ---------------------------------------------------------------------------

_dynamo_table_cache = None


def _dynamo_table():
    global _dynamo_table_cache
    if _dynamo_table_cache is None:
        import boto3
        dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
        _dynamo_table_cache = dynamodb.Table(settings.dynamodb_table)
    return _dynamo_table_cache


def _dynamo_record_duration(
    discipline: str,
    duration_minutes: float,
) -> None:
    try:
        _dynamo_table().update_item(
            Key={"pk": f"AGGREGATE#{discipline}"},
            UpdateExpression="ADD total_minutes :d, #cnt :one",
            ExpressionAttributeNames={"#cnt": "count"},
            ExpressionAttributeValues={
                ":d": Decimal(str(duration_minutes)),
                ":one": 1,
            },
        )
    except _BotoError:
        logger.error("DynamoDB error recording duration for %s", discipline, exc_info=True)


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
    except _BotoError:
        logger.error("DynamoDB error reading learned duration for %s", discipline, exc_info=True)
    return None


def _obs_fields_match(
    existing: dict,
    discipline: str,
    duration_minutes: float,
    classification: str | None,
    gender: str | None,
    per_heat_duration_minutes: float | None,
) -> bool:
    """Compare existing OBS# item fields against new parameters."""
    if existing.get("discipline") != discipline:
        return False
    if existing.get("classification") != classification:
        return False
    if existing.get("gender") != gender:
        return False

    # Decimal→float comparison with epsilon tolerance
    eps = 1e-9
    old_dur = float(existing.get("duration_minutes", 0))
    if abs(old_dur - duration_minutes) > eps:
        return False

    old_per_heat = existing.get("per_heat_duration_minutes")
    if old_per_heat is not None:
        old_per_heat = float(old_per_heat)
    if per_heat_duration_minutes is None and old_per_heat is None:
        pass  # both None — match
    elif per_heat_duration_minutes is None or old_per_heat is None:
        return False  # one is None, the other isn't
    elif abs(old_per_heat - per_heat_duration_minutes) > eps:
        return False

    return True


def _dynamo_record_duration_structured(
    discipline: str,
    duration_minutes: float,
    classification: str | None,
    gender: str | None,
    per_heat_duration_minutes: float | None,
    competition_id: int,
    session_id: int,
    event_position: int,
) -> RecordOutcome:
    """Write structured duration to DynamoDB with multi-level aggregates.

    Three-way branch:
      1. No existing OBS# → create new record (unchanged from original)
      2. Existing OBS# with identical data → return "unchanged"
      3. Existing OBS# with different data → correct aggregates, overwrite OBS#

    Maintains aggregates-first, OBS#-last ordering for crash safety.
    """
    try:
        table = _dynamo_table()
        obs_key = f"OBS#{competition_id}#{session_id}#{event_position}"

        existing = table.get_item(Key={"pk": obs_key}).get("Item")

        if existing:
            # Branch 2: identical data — no writes needed
            if _obs_fields_match(existing, discipline, duration_minutes,
                                 classification, gender, per_heat_duration_minutes):
                return "unchanged"

            # Branch 3: correction path — compute deltas and fix aggregates
            old_discipline = existing.get("discipline", discipline)
            old_classification = existing.get("classification")
            old_gender = existing.get("gender")
            old_duration = float(existing["duration_minutes"])

            old_agg_keys = set(_build_aggregate_keys(old_discipline, old_classification, old_gender))
            new_agg_keys = set(_build_aggregate_keys(discipline, classification, gender))

            removed = old_agg_keys - new_agg_keys
            added = new_agg_keys - old_agg_keys
            shared = old_agg_keys & new_agg_keys

            # Decrement removed aggregate keys
            for agg_key in removed:
                table.update_item(
                    Key={"pk": agg_key},
                    UpdateExpression="ADD total_minutes :d, #cnt :neg_one",
                    ExpressionAttributeNames={"#cnt": "count"},
                    ExpressionAttributeValues={
                        ":d": Decimal(str(-old_duration)),
                        ":neg_one": -1,
                    },
                )

            # Increment added aggregate keys
            for agg_key in added:
                table.update_item(
                    Key={"pk": agg_key},
                    UpdateExpression="ADD total_minutes :d, #cnt :one",
                    ExpressionAttributeNames={"#cnt": "count"},
                    ExpressionAttributeValues={
                        ":d": Decimal(str(duration_minutes)),
                        ":one": 1,
                    },
                )

            # Correct shared aggregate keys (duration delta only, count unchanged)
            duration_delta = duration_minutes - old_duration
            if abs(duration_delta) > 1e-9:
                for agg_key in shared:
                    table.update_item(
                        Key={"pk": agg_key},
                        UpdateExpression="ADD total_minutes :d",
                        ExpressionAttributeValues={
                            ":d": Decimal(str(duration_delta)),
                        },
                    )

            # Overwrite OBS# item
            obs_item: dict = {
                "pk": obs_key,
                "discipline": discipline,
                "duration_minutes": Decimal(str(duration_minutes)),
            }
            if classification:
                obs_item["classification"] = classification
            if gender:
                obs_item["gender"] = gender
            if per_heat_duration_minutes is not None:
                obs_item["per_heat_duration_minutes"] = Decimal(str(per_heat_duration_minutes))
            table.put_item(Item=obs_item)

            logger.info(
                "Corrected OBS %s: discipline=%s duration=%.1f→%.1f",
                obs_key, discipline, old_duration, duration_minutes,
            )
            return "updated"

        # Branch 1: new record — update aggregates FIRST, then write OBS#
        aggregate_keys = _build_aggregate_keys(discipline, classification, gender)
        for agg_key in aggregate_keys:
            table.update_item(
                Key={"pk": agg_key},
                UpdateExpression="ADD total_minutes :d, #cnt :one",
                ExpressionAttributeNames={"#cnt": "count"},
                ExpressionAttributeValues={
                    ":d": Decimal(str(duration_minutes)),
                    ":one": 1,
                },
            )

        obs_item = {
            "pk": obs_key,
            "discipline": discipline,
            "duration_minutes": Decimal(str(duration_minutes)),
        }
        if classification:
            obs_item["classification"] = classification
        if gender:
            obs_item["gender"] = gender
        if per_heat_duration_minutes is not None:
            obs_item["per_heat_duration_minutes"] = Decimal(str(per_heat_duration_minutes))
        try:
            table.put_item(
                Item=obs_item,
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return "unchanged"  # Concurrent write won
            raise
        return "created"
    except _BotoError:
        logger.error("DynamoDB error recording structured duration for %s", discipline, exc_info=True)
        return "error"


def _build_aggregate_keys(
    discipline: str,
    classification: str | None,
    gender: str | None,
) -> list[str]:
    """Build all applicable AGGREGATE key patterns for a duration observation."""
    keys = [f"AGGREGATE#{discipline}"]  # Level 1: always
    if gender:
        keys.append(f"AGGREGATE#{discipline}##{gender}")  # Level 2: disc+gender
    if classification:
        keys.append(f"AGGREGATE#{discipline}#{classification}")  # Level 3: disc+class
    if classification and gender:
        keys.append(f"AGGREGATE#{discipline}#{classification}#{gender}")  # Level 4: all
    return keys


def _dynamo_get_learned_duration_cascading(
    discipline: str,
    classification: str | None,
    gender: str | None,
) -> float | None:
    """Cascading fallback query for DynamoDB: 4 GetItem calls, most specific first."""
    try:
        table = _dynamo_table()
        levels = []
        if classification and gender:
            levels.append(f"AGGREGATE#{discipline}#{classification}#{gender}")
        if classification:
            levels.append(f"AGGREGATE#{discipline}#{classification}")
        if gender:
            levels.append(f"AGGREGATE#{discipline}##{gender}")

        # Check overrides at each level
        override_levels = []
        if classification and gender:
            override_levels.append(f"OVERRIDE#{discipline}#{classification}#{gender}")
        if classification:
            override_levels.append(f"OVERRIDE#{discipline}#{classification}")
        if gender:
            override_levels.append(f"OVERRIDE#{discipline}##{gender}")
        override_levels.append(f"OVERRIDE#{discipline}")

        for override_key in override_levels:
            item = table.get_item(Key={"pk": override_key}).get("Item")
            if item and "duration_minutes" in item:
                try:
                    return float(item["duration_minutes"])
                except (ValueError, TypeError):
                    logger.error("Malformed override value for %s: %r", override_key, item.get("duration_minutes"))

        # Check aggregates at each level
        for agg_key in levels:
            item = table.get_item(Key={"pk": agg_key}).get("Item")
            if item:
                count = int(item.get("count", 0))
                total = float(item.get("total_minutes", 0))
                if count >= settings.min_learned_samples:
                    return total / count

        # Level 1: broadest (existing pattern)
        item = table.get_item(Key={"pk": f"AGGREGATE#{discipline}"}).get("Item")
        if item:
            count = int(item.get("count", 0))
            total = float(item.get("total_minutes", 0))
            if count >= settings.min_learned_samples:
                return total / count
    except _BotoError:
        logger.error("DynamoDB error in cascading fallback for %s", discipline, exc_info=True)
    return None


def _dynamo_get_all_learned_durations() -> dict[str, tuple[float, int]]:
    try:
        from boto3.dynamodb.conditions import Attr
        table = _dynamo_table()
        filter_expr = Attr("pk").begins_with("AGGREGATE#")
        response = table.scan(FilterExpression=filter_expr)
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = table.scan(
                FilterExpression=filter_expr,
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))
        result = {}
        for item in items:
            discipline = item["pk"][len("AGGREGATE#"):]
            count = int(item.get("count", 0))
            total = float(item.get("total_minutes", 0))
            if count > 0:
                result[discipline] = (total / count, count)
        return result
    except _BotoError:
        logger.error("DynamoDB error reading all learned durations (table=%s)", settings.dynamodb_table, exc_info=True)
        return {}


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
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO event_durations
                    (competition_id, session_id, event_position, event_name, discipline, duration_minutes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (competition_id, session_id, event_position, event_name, discipline, duration_minutes),
            )
    except sqlite3.Error:
        logger.error("SQLite error recording duration for %s (db=%s)", discipline, settings.db_path, exc_info=True)


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
    except sqlite3.Error:
        logger.error("SQLite error reading learned duration for %s (db=%s)", discipline, settings.db_path, exc_info=True)
    return None


def record_duration_structured(
    competition_id: int,
    session_id: int,
    event_position: int,
    event_name: str,
    discipline: str,
    duration_minutes: float,
    classification: str | None = None,
    gender: str | None = None,
    per_heat_duration_minutes: float | None = None,
) -> RecordOutcome:
    """Insert one observed event duration with structured category info.

    Uses INSERT OR REPLACE with the natural key (competition_id, session_id,
    event_position) for idempotent upsert.

    Returns "created", "updated", "unchanged", or "error".
    """
    if settings.dynamodb_table:
        return _dynamo_record_duration_structured(
            discipline, duration_minutes, classification, gender,
            per_heat_duration_minutes, competition_id, session_id, event_position,
        )
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO event_durations
                    (competition_id, session_id, event_position, event_name,
                     discipline, duration_minutes, classification, gender,
                     per_heat_duration_minutes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (competition_id, session_id, event_position, event_name,
                 discipline, duration_minutes, classification, gender,
                 per_heat_duration_minutes),
            )
        return "created"
    except sqlite3.Error:
        logger.error(
            "SQLite error recording structured duration for %s (db=%s)",
            discipline, settings.db_path, exc_info=True,
        )
        return "error"


def get_learned_duration_cascading(
    discipline: str,
    classification: str | None = None,
    gender: str | None = None,
) -> float | None:
    """Return learned average using cascading fallback through 4 specificity levels.

    Queries in order:
      Level 4: discipline + classification + gender
      Level 3: discipline + classification
      Level 2: discipline + gender
      Level 1: discipline only

    Returns the first level with count >= min_learned_samples.
    Checks discipline_overrides at Level 1 (same as get_learned_duration).
    When classification or gender is None, higher-specificity levels that use
    WHERE col = ? with NULL will never match in SQL — this naturally falls
    through to broader levels.
    """
    if settings.dynamodb_table:
        return _dynamo_get_learned_duration_cascading(discipline, classification, gender)

    try:
        with get_db() as conn:
            # Level 4: most specific
            if classification is not None and gender is not None:
                row = conn.execute(
                    "SELECT AVG(duration_minutes) AS avg_dur, COUNT(*) AS cnt "
                    "FROM event_durations WHERE discipline = ? AND classification = ? AND gender = ?",
                    (discipline, classification, gender),
                ).fetchone()
                if row and row["cnt"] >= settings.min_learned_samples:
                    return row["avg_dur"]

            # Level 3: discipline + classification
            if classification is not None:
                row = conn.execute(
                    "SELECT AVG(duration_minutes) AS avg_dur, COUNT(*) AS cnt "
                    "FROM event_durations WHERE discipline = ? AND classification = ?",
                    (discipline, classification),
                ).fetchone()
                if row and row["cnt"] >= settings.min_learned_samples:
                    return row["avg_dur"]

            # Level 2: discipline + gender
            if gender is not None:
                row = conn.execute(
                    "SELECT AVG(duration_minutes) AS avg_dur, COUNT(*) AS cnt "
                    "FROM event_durations WHERE discipline = ? AND gender = ?",
                    (discipline, gender),
                ).fetchone()
                if row and row["cnt"] >= settings.min_learned_samples:
                    return row["avg_dur"]

            # Level 1: discipline only — check overrides first (same as get_learned_duration)
            override = conn.execute(
                "SELECT duration_minutes FROM discipline_overrides WHERE discipline = ?",
                (discipline,),
            ).fetchone()
            if override:
                return override["duration_minutes"]

            row = conn.execute(
                "SELECT AVG(duration_minutes) AS avg_dur, COUNT(*) AS cnt "
                "FROM event_durations WHERE discipline = ?",
                (discipline,),
            ).fetchone()
            if row and row["cnt"] >= settings.min_learned_samples:
                return row["avg_dur"]

    except sqlite3.Error:
        logger.error(
            "SQLite error in cascading fallback for %s (db=%s)",
            discipline, settings.db_path, exc_info=True,
        )
    return None


def get_all_learned_durations() -> dict[str, tuple[float, int]]:
    """Return all learned durations as {discipline: (avg_minutes, sample_count)}."""
    if settings.dynamodb_table:
        return _dynamo_get_all_learned_durations()
    try:
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
    except sqlite3.Error:
        logger.error("SQLite error reading all learned durations (db=%s)", settings.db_path, exc_info=True)
        return {}
