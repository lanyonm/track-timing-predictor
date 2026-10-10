import asyncio
import logging
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Literal

from app.aws_errors import BotoError as _BotoError, ClientError, raise_if_auth_error as _raise_if_auth_error
from app.config import settings

logger = logging.getLogger(__name__)

RecordOutcome = Literal["created", "updated", "unchanged", "error"]
# How the live app measured a duration. Loader rows have no source.
LiveSource = Literal["observed", "wall_clock"]
# (discipline, classification, gender) for a cascading learned-duration lookup
LearnedKey = tuple[str, str | None, str | None]

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
    source           TEXT DEFAULT NULL,
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
    """Add classification, gender, per_heat_duration_minutes, source columns if missing.

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
        ("source", "TEXT DEFAULT NULL"),
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
    except sqlite3.IntegrityError as exc:
        dup_count = conn.execute("""
            SELECT COUNT(*) FROM event_durations
            WHERE id NOT IN (
                SELECT MAX(id) FROM event_durations
                GROUP BY competition_id, session_id, event_position
            )
        """).fetchone()[0]
        raise DuplicateRowsError(dup_count) from exc


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
#   AGGREGATE#<disc>                       — Level 1: broadest running total (N) + count (N)
#   AGGREGATE#<disc>##<gender>             — Level 2: discipline + gender (double-hash separator)
#   AGGREGATE#<disc>#<class>               — Level 3: discipline + classification
#   AGGREGATE#<disc>#<class>#<gender>      — Level 4: most specific
#   OVERRIDE#<disc> (through #<class>#<gender>) — manual override at each level
#   OBS#<comp_id>#<sess_id>#<pos>          — observation item for idempotent upsert;
#                                            live writes set source (observed/wall_clock),
#                                            loader writes leave it unset
# ---------------------------------------------------------------------------

# boto3 DynamoDB Table resource; boto3 ships no type stubs
_dynamo_table_cache: Any = None


def _dynamo_table() -> Any:
    global _dynamo_table_cache
    if _dynamo_table_cache is None:
        import boto3

        dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
        _dynamo_table_cache = dynamodb.Table(settings.dynamodb_table)
    return _dynamo_table_cache


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
    raw_dur = existing.get("duration_minutes")
    if raw_dur is None:
        logger.warning("OBS item missing duration_minutes field: %s", existing.get("pk"))
        return False
    old_dur = float(raw_dur)
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
    source: LiveSource | None = None,
) -> RecordOutcome:
    """Write structured duration to DynamoDB with multi-level aggregates.

    Three-way branch:
      1. No existing OBS# → create new record (increment aggregates, write OBS#)
      2. Existing OBS# with identical data → return "unchanged"
      3. Existing OBS# with different data → correct aggregates, overwrite OBS#

    Maintains aggregates-first, OBS#-last ordering for crash safety.

    Note: Branch 3 performs multiple non-transactional update_item calls.
    If a partial failure occurs, aggregates may be inconsistent until the
    next re-load (which will recompute deltas from the unchanged OBS# item).
    """
    # Normalize empty strings to None for consistent key generation
    classification = classification or None
    gender = gender or None
    try:
        table = _dynamo_table()
        obs_key = f"OBS#{competition_id}#{session_id}#{event_position}"

        existing = table.get_item(Key={"pk": obs_key}).get("Item")

        if existing:
            # Branch 2: identical data — no writes needed
            if _obs_fields_match(
                existing, discipline, duration_minutes, classification, gender, per_heat_duration_minutes
            ):
                return "unchanged"

            # Branch 3: correction path — compute deltas and fix aggregates
            old_discipline = existing.get("discipline", discipline)
            old_classification = existing.get("classification")
            old_gender = existing.get("gender")
            raw_old_duration = existing.get("duration_minutes")
            if raw_old_duration is None:
                logger.error(
                    "OBS item %s missing duration_minutes — cannot compute correction delta; overwriting with new data",
                    obs_key,
                )
                old_duration = 0.0
            else:
                old_duration = float(raw_old_duration)

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
            if source:
                obs_item["source"] = source
            table.put_item(Item=obs_item)

            logger.info(
                "Corrected OBS %s: discipline=%s duration=%.1f→%.1f",
                obs_key,
                discipline,
                old_duration,
                duration_minutes,
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
        if source:
            obs_item["source"] = source
        try:
            table.put_item(
                Item=obs_item,
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Concurrent write won — roll back aggregate increments
                try:
                    for agg_key in aggregate_keys:
                        table.update_item(
                            Key={"pk": agg_key},
                            UpdateExpression="ADD total_minutes :d, #cnt :neg_one",
                            ExpressionAttributeNames={"#cnt": "count"},
                            ExpressionAttributeValues={
                                ":d": Decimal(str(-duration_minutes)),
                                ":neg_one": -1,
                            },
                        )
                except _BotoError as rollback_exc:
                    _raise_if_auth_error(rollback_exc)
                    logger.error(
                        "Failed to roll back aggregate increments for %s; aggregates may be over-counted for keys: %s",
                        obs_key,
                        aggregate_keys,
                        exc_info=True,
                    )
                return "unchanged"
            raise
        return "created"
    except _BotoError as exc:
        _raise_if_auth_error(exc)
        logger.error(
            "DynamoDB error recording structured duration for %s; "
            "partial aggregate updates may have occurred and will self-correct on re-load",
            discipline,
            exc_info=True,
        )
        return "error"


def _replaces(existing_source: str | None, source: LiveSource) -> bool:
    """A live write replaces an existing observation only when observed beats wall-clock.

    Loader observations (no source) are never replaced: the loader's categorisation is
    richer than the live app's, and replacing it would flip aggregates back and forth.
    """
    return existing_source == "wall_clock" and source == "observed"


def _dynamo_record_live_duration(
    competition_id: int,
    session_id: int,
    event_position: int,
    discipline: str,
    duration_minutes: float,
    source: LiveSource,
    classification: str | None,
    gender: str | None,
) -> RecordOutcome:
    obs_key = f"OBS#{competition_id}#{session_id}#{event_position}"
    try:
        existing = _dynamo_table().get_item(Key={"pk": obs_key}).get("Item")
    except _BotoError as exc:
        _raise_if_auth_error(exc)
        logger.error("DynamoDB error recording live duration for %s", discipline, exc_info=True)
        return "error"
    if existing is not None and not _replaces(existing.get("source"), source):
        return "unchanged"
    return _dynamo_record_duration_structured(
        discipline,
        duration_minutes,
        classification,
        gender,
        None,
        competition_id,
        session_id,
        event_position,
        source=source,
    )


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


def _cascade_keys(discipline: str, classification: str | None, gender: str | None) -> tuple[list[str], list[str]]:
    """OVERRIDE# and AGGREGATE# keys a cascading lookup checks, each most specific first."""
    suffixes = []
    if classification and gender:
        suffixes.append(f"#{classification}#{gender}")  # Level 4
    if classification:
        suffixes.append(f"#{classification}")  # Level 3
    if gender:
        suffixes.append(f"##{gender}")  # Level 2
    suffixes.append("")  # Level 1
    return [f"OVERRIDE#{discipline}{x}" for x in suffixes], [f"AGGREGATE#{discipline}{x}" for x in suffixes]


def _dynamo_batch_get(pks: set[str]) -> dict[str, dict]:
    """Fetch items by pk with BatchGetItem (100 keys per call), returning those that exist.

    The Table resource's client converts DynamoDB types both ways, like Table.get_item.
    """
    table = _dynamo_table()
    items: dict[str, dict] = {}
    ordered = sorted(pks)
    for i in range(0, len(ordered), 100):
        request: dict = {table.name: {"Keys": [{"pk": pk} for pk in ordered[i : i + 100]]}}
        while request:
            response = table.meta.client.batch_get_item(RequestItems=request)
            for item in response.get("Responses", {}).get(table.name, []):
                items[item["pk"]] = item
            request = response.get("UnprocessedKeys") or {}
    return items


def _resolve_cascade(
    discipline: str, classification: str | None, gender: str | None, items: dict[str, dict]
) -> float | None:
    """Apply the cascade to fetched items: the first override, else the first aggregate
    with at least min_learned_samples."""
    override_keys, aggregate_keys = _cascade_keys(discipline, classification, gender)
    for key in override_keys:
        item = items.get(key)
        if item and "duration_minutes" in item:
            try:
                return float(item["duration_minutes"])
            except (ValueError, TypeError):
                logger.error("Malformed override value for %s: %r", key, item.get("duration_minutes"), exc_info=True)
    for key in aggregate_keys:
        item = items.get(key)
        if not item:
            continue
        try:
            count = int(item.get("count", 0))
            total = float(item.get("total_minutes", 0))
        except (ValueError, TypeError):
            logger.error(
                "Malformed aggregate values for %s: count=%r total=%r",
                key,
                item.get("count"),
                item.get("total_minutes"),
                exc_info=True,
            )
            continue
        if count >= settings.min_learned_samples:
            return total / count
    return None


def _dynamo_get_learned_durations_cascading(keys: list[LearnedKey]) -> dict[LearnedKey, float]:
    """Cascading lookups for many keys, with every item they need fetched in one batch."""
    keys = [(d, c or None, g or None) for d, c, g in keys]
    pks = {pk for key in keys for group in _cascade_keys(*key) for pk in group}
    try:
        items = _dynamo_batch_get(pks)
    except _BotoError as exc:
        _raise_if_auth_error(exc)
        logger.error("DynamoDB error in cascading fallback for %d keys", len(keys), exc_info=True)
        return {}
    result = {}
    for key in keys:
        value = _resolve_cascade(*key, items)
        if value is not None:
            result[key] = value
    return result


def _dynamo_scan_aggregates() -> list[dict]:
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
    return items


def _parse_aggregate_key(pk: str) -> LearnedKey:
    """Invert _build_aggregate_keys: AGGREGATE#<disc>[##<gender> | #<class>[#<gender>]]."""
    discipline, _, rest = pk[len("AGGREGATE#") :].partition("#")
    if not rest:
        return discipline, None, None
    if rest.startswith("#"):
        return discipline, None, rest[1:]
    classification, _, gender = rest.partition("#")
    return discipline, classification, gender or None


def _dynamo_get_all_learned_levels() -> dict[LearnedKey, tuple[float, int]]:
    try:
        result = {}
        for item in _dynamo_scan_aggregates():
            count = int(item.get("count", 0))
            total = float(item.get("total_minutes", 0))
            if count > 0:
                result[_parse_aggregate_key(item["pk"])] = (total / count, count)
        return result
    except _BotoError as exc:
        _raise_if_auth_error(exc)
        logger.error("DynamoDB error reading all learned durations (table=%s)", settings.dynamodb_table, exc_info=True)
        return {}


def _dynamo_get_all_learned_durations() -> dict[str, tuple[float, int]]:
    """Discipline-level (Level 1) aggregates only."""
    levels = _dynamo_get_all_learned_levels()
    return dict(sorted((d, v) for (d, c, g), v in levels.items() if c is None and g is None))


# ---------------------------------------------------------------------------
# Public API — dispatches to DynamoDB when DYNAMODB_TABLE is configured,
# otherwise falls back to SQLite for local development.
# ---------------------------------------------------------------------------


def record_live_duration(
    competition_id: int,
    session_id: int,
    event_position: int,
    event_name: str,
    discipline: str,
    duration_minutes: float,
    source: LiveSource,
    classification: str | None = None,
    gender: str | None = None,
) -> RecordOutcome:
    """Record a duration the live app measured, at most once per event.

    Every cold start, container and viewer re-records the same events, so this is
    keyed by (competition, session, position) like the loader. An existing record is
    kept unless an observed value is replacing a wall-clock one (see ``_replaces``).
    classification and gender (from categorizer.categorize_event) feed the finer
    learned levels that get_learned_duration_cascading reads.
    """
    if settings.dynamodb_table:
        return _dynamo_record_live_duration(
            competition_id,
            session_id,
            event_position,
            discipline,
            duration_minutes,
            source,
            classification,
            gender,
        )
    try:
        with get_db() as conn:
            existing = conn.execute(
                """
                SELECT source FROM event_durations
                WHERE competition_id = ? AND session_id = ? AND event_position = ?
                """,
                (competition_id, session_id, event_position),
            ).fetchone()
            if existing is not None and not _replaces(existing["source"], source):
                return "unchanged"
            conn.execute(
                """
                INSERT OR REPLACE INTO event_durations
                    (competition_id, session_id, event_position, event_name,
                     discipline, duration_minutes, classification, gender, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    competition_id,
                    session_id,
                    event_position,
                    event_name,
                    discipline,
                    duration_minutes,
                    classification or None,
                    gender or None,
                    source,
                ),
            )
        return "created" if existing is None else "updated"
    except sqlite3.Error:
        logger.error(
            "SQLite error recording live duration for %s (db=%s) "
            "competition_id=%d session_id=%d event_position=%d event_name=%r",
            discipline,
            settings.db_path,
            competition_id,
            session_id,
            event_position,
            event_name,
            exc_info=True,
        )
        return "error"


def get_learned_duration(discipline: str) -> float | None:
    """Return the discipline-level learned average (override first), or None below
    MIN_LEARNED_SAMPLES. The discipline-only case of get_learned_duration_cascading."""
    return get_learned_duration_cascading(discipline)


def get_learned_durations_cascading(keys: Iterable[LearnedKey]) -> dict[LearnedKey, float]:
    """get_learned_duration_cascading for each (discipline, classification, gender) key.

    Keys without a learned value are left out. DynamoDB reads every item the lookups
    need in one BatchGetItem pass instead of up to 8 GetItems per key.
    """
    unique = list(dict.fromkeys(keys))
    if settings.dynamodb_table:
        return _dynamo_get_learned_durations_cascading(unique)
    result = {}
    for key in unique:
        value = get_learned_duration_cascading(*key)
        if value is not None:
            result[key] = value
    return result


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

    Note: The SQLite path always returns "created" even when replacing an
    existing row, since INSERT OR REPLACE does not distinguish insert from
    update. The DynamoDB path returns all four outcome values.
    """
    if settings.dynamodb_table:
        return _dynamo_record_duration_structured(
            discipline,
            duration_minutes,
            classification,
            gender,
            per_heat_duration_minutes,
            competition_id,
            session_id,
            event_position,
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
                (
                    competition_id,
                    session_id,
                    event_position,
                    event_name,
                    discipline,
                    duration_minutes,
                    classification,
                    gender,
                    per_heat_duration_minutes,
                ),
            )
        return "created"
    except sqlite3.Error:
        logger.error(
            "SQLite error recording structured duration for %s (db=%s)",
            discipline,
            settings.db_path,
            exc_info=True,
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

    Returns the first level with count >= min_learned_samples. An override wins
    over every aggregate: SQLite has discipline-level overrides only
    (discipline_overrides); DynamoDB checks overrides at all 4 levels, most
    specific first, before any aggregate.

    When classification or gender is None, higher-specificity levels that use
    WHERE col = ? with NULL will never match in SQL — this naturally falls
    through to broader levels.
    """
    if settings.dynamodb_table:
        return _dynamo_get_learned_durations_cascading([(discipline, classification, gender)]).get(
            (discipline, classification or None, gender or None)
        )

    classification = classification or None
    gender = gender or None
    try:
        with get_db() as conn:
            override = conn.execute(
                "SELECT duration_minutes FROM discipline_overrides WHERE discipline = ?",
                (discipline,),
            ).fetchone()
            if override:
                return override["duration_minutes"]

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

            # Level 1: discipline only
            row = conn.execute(
                "SELECT AVG(duration_minutes) AS avg_dur, COUNT(*) AS cnt FROM event_durations WHERE discipline = ?",
                (discipline,),
            ).fetchone()
            if row and row["cnt"] >= settings.min_learned_samples:
                return row["avg_dur"]

    except sqlite3.Error:
        logger.error(
            "SQLite error in cascading fallback for %s (db=%s)",
            discipline,
            settings.db_path,
            exc_info=True,
        )
    return None


def get_all_learned_durations() -> dict[str, tuple[float, int]]:
    """Return all discipline-level learned durations as {discipline: (avg_minutes, sample_count)}."""
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


def get_finer_learned_durations() -> dict[LearnedKey, tuple[float, int]]:
    """Return the classification and gender levels the cascade can use, as
    {(discipline, classification, gender): (avg_minutes, sample_count)}, for those with
    at least min_learned_samples samples. Level 2 keys have no classification, Level 3
    keys no gender."""
    if settings.dynamodb_table:
        levels = _dynamo_get_all_learned_levels()
    else:
        queries = [
            # Level 2: discipline + gender
            "SELECT discipline, NULL, gender, AVG(duration_minutes), COUNT(*) FROM event_durations "
            "WHERE gender IS NOT NULL GROUP BY discipline, gender",
            # Level 3: discipline + classification
            "SELECT discipline, classification, NULL, AVG(duration_minutes), COUNT(*) FROM event_durations "
            "WHERE classification IS NOT NULL GROUP BY discipline, classification",
            # Level 4: all three
            "SELECT discipline, classification, gender, AVG(duration_minutes), COUNT(*) FROM event_durations "
            "WHERE classification IS NOT NULL AND gender IS NOT NULL GROUP BY discipline, classification, gender",
        ]
        levels = {}
        try:
            with get_db() as conn:
                for query in queries:
                    for d, c, g, avg, cnt in conn.execute(query).fetchall():
                        levels[(d, c, g)] = (avg, cnt)
        except sqlite3.Error:
            logger.error("SQLite error reading finer learned durations (db=%s)", settings.db_path, exc_info=True)
            return {}
    usable = {
        k: v
        for k, v in levels.items()
        if (k[1] is not None or k[2] is not None) and v[1] >= settings.min_learned_samples
    }
    return dict(sorted(usable.items(), key=lambda kv: tuple(x or "" for x in kv[0])))


async def check_health() -> dict[str, str]:
    """Check database connectivity and return health status.

    Returns {"status": "healthy"} on success, or
    {"status": "degraded", "detail": "<summary>"} on failure.
    Never raises.
    """
    backend = "DynamoDB" if settings.dynamodb_table else "SQLite"
    try:
        if settings.dynamodb_table:
            await asyncio.to_thread(lambda: _dynamo_table().table_status)
        else:

            def _check_sqlite():
                with get_db() as conn:
                    conn.execute("SELECT 1")

            await asyncio.to_thread(_check_sqlite)
        return {"status": "healthy"}
    except Exception:
        return {"status": "degraded", "detail": f"{backend} connection failed"}
