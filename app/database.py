import sqlite3
from contextlib import contextmanager

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_durations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER NOT NULL,
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
    with get_db() as conn:
        conn.executescript(_SCHEMA)


def record_duration(
    competition_id: int,
    session_id: int,
    event_position: int,
    event_name: str,
    discipline: str,
    duration_minutes: float,
) -> None:
    """Insert one observed event duration into the database."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO event_durations
                (event_id, session_id, event_position, event_name, discipline, duration_minutes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (competition_id, session_id, event_position, event_name, discipline, duration_minutes),
        )


def get_learned_duration(discipline: str) -> float | None:
    """
    Return the average observed duration for a discipline if we have enough samples.
    Returns None if there are insufficient observations or the DB is not initialized.
    """
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
