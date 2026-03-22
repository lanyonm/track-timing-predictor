"""Tests for the duration loader (tools/load_durations.py) and schema migrations."""

from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from app.database import (
    DuplicateRowsError,
    deduplicate_event_durations,
    get_db,
    get_learned_duration,
    get_learned_duration_cascading,
    init_db,
    record_duration_structured,
)
from app.models import CompetitionReport, DurationRecord
from tools.load_durations import load_report, _validate_duration_bounds, _compute_per_heat_duration


# ---------------------------------------------------------------------------
# T012: Schema migration tests
# ---------------------------------------------------------------------------

_OLD_SCHEMA = """
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


@pytest.fixture
def old_schema_db(tmp_path):
    """Create a SQLite DB with the pre-migration schema (without new columns)."""
    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_OLD_SCHEMA)
    # Insert a sample row with old schema
    conn.execute(
        "INSERT INTO event_durations (competition_id, session_id, event_position, event_name, discipline, duration_minutes) "
        "VALUES (1, 1, 0, 'test event', 'sprint_match', 12.0)"
    )
    conn.commit()
    conn.close()
    return db_path


class TestSchemaMigration:
    """T012: SQLite schema migration tests."""

    def test_new_columns_added_after_migration(self, old_schema_db):
        """classification, gender, per_heat_duration_minutes columns are added."""
        original_db = settings.db_path
        original_dynamo = settings.dynamodb_table
        settings.db_path = old_schema_db
        settings.dynamodb_table = ""
        try:
            init_db()
            conn = sqlite3.connect(old_schema_db)
            cursor = conn.execute("PRAGMA table_info(event_durations)")
            columns = {row[1] for row in cursor.fetchall()}
            conn.close()
            assert "classification" in columns
            assert "gender" in columns
            assert "per_heat_duration_minutes" in columns
        finally:
            settings.db_path = original_db
            settings.dynamodb_table = original_dynamo

    def test_existing_rows_retain_null(self, old_schema_db):
        """Existing rows retain NULL for new columns after migration."""
        original_db = settings.db_path
        original_dynamo = settings.dynamodb_table
        settings.db_path = old_schema_db
        settings.dynamodb_table = ""
        try:
            init_db()
            conn = sqlite3.connect(old_schema_db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM event_durations WHERE id = 1").fetchone()
            conn.close()
            assert row["classification"] is None
            assert row["gender"] is None
            assert row["per_heat_duration_minutes"] is None
        finally:
            settings.db_path = original_db
            settings.dynamodb_table = original_dynamo

    def test_cascading_fallback_index_exists(self, old_schema_db):
        """idx_event_durations_category index exists after migration."""
        original_db = settings.db_path
        original_dynamo = settings.dynamodb_table
        settings.db_path = old_schema_db
        settings.dynamodb_table = ""
        try:
            init_db()
            conn = sqlite3.connect(old_schema_db)
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='event_durations'"
            ).fetchall()
            index_names = {row[0] for row in indexes}
            conn.close()
            assert "idx_event_durations_category" in index_names
            assert "idx_event_durations_natural_key" in index_names
        finally:
            settings.db_path = original_db
            settings.dynamodb_table = original_dynamo

    def test_duplicate_rows_raise_error(self, tmp_path):
        """Duplicate natural keys in existing data raise DuplicateRowsError."""
        db_path = str(tmp_path / "dupes.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(_OLD_SCHEMA)
        # Insert duplicate rows for the same natural key
        for dur in [10.0, 12.0, 14.0]:
            conn.execute(
                "INSERT INTO event_durations (competition_id, session_id, event_position, "
                "event_name, discipline, duration_minutes) VALUES (1, 1, 0, 'test', 'sprint_match', ?)",
                (dur,),
            )
        conn.commit()
        conn.close()

        original_db = settings.db_path
        original_dynamo = settings.dynamodb_table
        settings.db_path = db_path
        settings.dynamodb_table = ""
        try:
            with pytest.raises(DuplicateRowsError) as exc_info:
                init_db()
            assert exc_info.value.duplicate_count == 2  # 3 rows, keep 1, remove 2
        finally:
            settings.db_path = original_db
            settings.dynamodb_table = original_dynamo

    def test_deduplicate_keeps_most_recent(self, tmp_path):
        """deduplicate_event_durations keeps the row with the highest id."""
        db_path = str(tmp_path / "dedup.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(_OLD_SCHEMA)
        for dur in [10.0, 12.0, 14.0]:
            conn.execute(
                "INSERT INTO event_durations (competition_id, session_id, event_position, "
                "event_name, discipline, duration_minutes) VALUES (1, 1, 0, 'test', 'sprint_match', ?)",
                (dur,),
            )
        conn.commit()
        conn.close()

        original_db = settings.db_path
        original_dynamo = settings.dynamodb_table
        settings.db_path = db_path
        settings.dynamodb_table = ""
        try:
            # init_db will raise due to duplicates — run schema without unique index first
            conn = sqlite3.connect(db_path)
            conn.executescript(_OLD_SCHEMA)
            conn.close()

            deleted = deduplicate_event_durations()
            assert deleted == 2

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT duration_minutes FROM event_durations").fetchone()
            count = conn.execute("SELECT COUNT(*) FROM event_durations").fetchone()[0]
            conn.close()
            assert count == 1
            assert row["duration_minutes"] == 14.0  # most recent (highest id)
        finally:
            settings.db_path = original_db
            settings.dynamodb_table = original_dynamo


# ---------------------------------------------------------------------------
# T013: Loader integration tests
# ---------------------------------------------------------------------------

def _make_duration_record(**kwargs) -> DurationRecord:
    """Factory helper for DurationRecord with sensible defaults."""
    defaults = {
        "discipline": "sprint_match",
        "classification": "elite",
        "gender": "men",
        "round": "final",
        "omnium_part": None,
        "event_name": "Elite Men Sprint Final",
        "heat_count": None,
        "duration_minutes": 12.0,
        "per_heat_duration_minutes": None,
        "duration_source": "finish_time",
        "competition_id": 26008,
        "session_id": 1,
        "event_position": 0,
    }
    defaults.update(kwargs)
    return DurationRecord(**defaults)


def _make_report(observations: list[DurationRecord]) -> CompetitionReport:
    """Create a minimal CompetitionReport with given observations."""
    return CompetitionReport(
        version="1.0",
        extracted_at="2026-03-21T00:00:00Z",
        competition={"competition_id": 26008, "name": "Test", "url": "http://test"},
        sessions=[],
        duration_observations=observations,
        uncategorized_summary=[],
    )


class TestLoaderIntegration:
    """T013: Loader integration tests."""

    def test_valid_json_loads_records(self):
        """Valid JSON file loads all records into SQLite."""
        records = [
            _make_duration_record(event_position=0, duration_minutes=11.0),
            _make_duration_record(event_position=1, duration_minutes=13.0),
            _make_duration_record(event_position=2, duration_minutes=12.0),
        ]
        report = _make_report(records)
        stats = load_report(report)
        assert stats["loaded"] == 3

    def test_idempotent_reload_no_duplicates(self):
        """Idempotent re-load does not duplicate records (natural key upsert)."""
        records = [
            _make_duration_record(event_position=10, duration_minutes=12.0),
        ]
        report = _make_report(records)
        load_report(report)
        stats = load_report(report)
        # Should still succeed (INSERT OR REPLACE) — count includes the replace
        assert stats["loaded"] == 1

        # Verify only one row exists
        with get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM event_durations WHERE competition_id=26008 AND session_id=1 AND event_position=10"
            ).fetchone()[0]
        assert count == 1

    def test_cascading_fallback_levels(self):
        """Cascading fallback returns correct averages at each level."""
        # Insert 3+ records at different levels
        for i in range(3):
            record_duration_structured(
                competition_id=99000, session_id=1, event_position=100 + i,
                event_name="test", discipline="keirin",
                duration_minutes=6.0 + i * 0.5,
                classification="elite", gender="men",
            )

        # Level 4: discipline+classification+gender
        result = get_learned_duration_cascading("keirin", "elite", "men")
        assert result is not None
        assert abs(result - 6.5) < 0.1  # avg of 6.0, 6.5, 7.0

        # Level 1: discipline only should also work (includes all records for keirin)
        result = get_learned_duration_cascading("keirin")
        assert result is not None

    def test_out_of_bounds_skipped(self):
        """Records outside [0.5x, 2.0x] of static default are skipped."""
        record = _make_duration_record(
            event_position=50,
            discipline="sprint_match",
            duration_minutes=100.0,  # default is 12.0, 2x = 24.0
        )
        report = _make_report([record])
        stats = load_report(report)
        assert stats["skipped_bounds"] == 1
        assert stats["loaded"] == 0

    def test_per_heat_duration_computed(self):
        """Per-heat duration is computed when heat_count is present."""
        record = _make_duration_record(
            event_position=60,
            discipline="keirin",
            duration_minutes=12.0,
            heat_count=2,
        )
        per_heat = _compute_per_heat_duration(record)
        # keirin changeover = 2.0, so per_heat = (12.0 / 2) - 2.0 = 4.0
        assert per_heat is not None
        assert abs(per_heat - 4.0) < 0.01

    def test_per_heat_duration_none_without_heat_count(self):
        """Per-heat duration is NULL when heat_count is absent."""
        record = _make_duration_record(event_position=70, heat_count=None)
        per_heat = _compute_per_heat_duration(record)
        assert per_heat is None

    def test_validate_bounds_accepts_valid(self):
        """Duration within bounds passes validation."""
        record = _make_duration_record(duration_minutes=12.0)  # sprint_match default=12.0
        assert _validate_duration_bounds(record) is True

    def test_validate_bounds_rejects_too_high(self):
        """Duration > 2.0x default is rejected."""
        record = _make_duration_record(duration_minutes=25.0)  # 2x of 12.0 = 24.0
        assert _validate_duration_bounds(record) is False

    def test_validate_bounds_rejects_too_low(self):
        """Duration < 0.5x default is rejected."""
        record = _make_duration_record(duration_minutes=5.0)  # 0.5x of 12.0 = 6.0
        assert _validate_duration_bounds(record) is False

    def test_validate_bounds_uses_heat_count_when_available(self):
        """With heat_count, bounds are computed from per-heat duration, not flat default."""
        # sprint_qualifying: per_heat=1.25, changeover=0.0
        # 39 heats -> expected = 39 * 1.25 = 48.75 min
        # Flat default is 10.0 -> [5.0, 20.0] would reject 48.75
        # Heat-based expected is 48.75 -> [24.375, 97.5] should accept
        record = _make_duration_record(
            event_position=80,
            discipline="sprint_qualifying",
            duration_minutes=48.75,
            heat_count=39,
            duration_source="heat_count",
        )
        assert _validate_duration_bounds(record) is True

    def test_validate_bounds_heat_count_still_rejects_extreme(self):
        """Heat-count-based bounds still reject truly extreme values."""
        # keirin: per_heat=4.5, changeover=2.0
        # 2 heats -> expected = 2 * 4.5 + 2.0 = 11.0
        # [5.5, 22.0] should reject 50.0
        record = _make_duration_record(
            event_position=81,
            discipline="keirin",
            duration_minutes=50.0,
            heat_count=2,
            duration_source="heat_count",
        )
        assert _validate_duration_bounds(record) is False
