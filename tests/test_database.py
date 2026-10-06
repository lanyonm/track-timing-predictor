"""SQLite code paths in app/database.py (conftest points settings at a temp DB)."""
import pytest

from app.database import (
    get_all_learned_durations,
    record_duration_structured,
    record_live_duration,
)


class TestRecordLiveDurationSqlite:
    def test_repeat_write_is_idempotent(self):
        assert record_live_duration(1, 1, 1, "Scratch", "scratch_race", 12.0, "observed") == "created"
        assert record_live_duration(1, 1, 1, "Scratch", "scratch_race", 12.0, "observed") == "unchanged"
        assert get_all_learned_durations() == {"scratch_race": pytest.approx((12.0, 1))}

    def test_observed_replaces_wall_clock(self):
        record_live_duration(1, 1, 1, "Scratch", "scratch_race", 20.0, "wall_clock")
        assert record_live_duration(1, 1, 1, "Scratch", "scratch_race", 12.0, "observed") == "updated"
        assert get_all_learned_durations() == {"scratch_race": pytest.approx((12.0, 1))}

    def test_wall_clock_does_not_replace_observed(self):
        record_live_duration(1, 1, 1, "Scratch", "scratch_race", 12.0, "observed")
        assert record_live_duration(1, 1, 1, "Scratch", "scratch_race", 20.0, "wall_clock") == "unchanged"
        assert get_all_learned_durations() == {"scratch_race": pytest.approx((12.0, 1))}

    def test_does_not_replace_loader_row(self):
        record_duration_structured(1, 1, 1, "Elite Men Scratch", "scratch_race", 15.0, "elite", "men")
        assert record_live_duration(1, 1, 1, "Scratch", "scratch_race", 12.0, "observed") == "unchanged"
        assert get_all_learned_durations() == {"scratch_race": pytest.approx((15.0, 1))}


class TestGetAllLearnedDurationsSqlite:
    def test_groups_by_discipline(self):
        for pos, dur in enumerate([10.0, 12.0, 14.0]):
            record_live_duration(1, 1, pos, "Sprint", "sprint", dur, "observed")
        record_live_duration(1, 2, 0, "Points", "points_race", 20.0, "observed")
        assert get_all_learned_durations() == {
            "points_race": pytest.approx((20.0, 1)),
            "sprint": pytest.approx((12.0, 3)),
        }

    def test_empty(self):
        assert get_all_learned_durations() == {}
