"""CLI tests for tools/load_durations.py main(): arguments, the dedup prompt and the summary."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from app.database import DuplicateRowsError, get_learned_duration_cascading
from tests.test_loader import _make_duration_record, _make_report
from tools import load_durations


def _run(*args: str) -> None:
    with patch.object(sys, "argv", ["load_durations", *args]):
        load_durations.main()


@pytest.fixture
def report_file(tmp_path):
    records = [
        _make_duration_record(event_position=pos, duration_minutes=12.0, classification="age_55_59") for pos in range(3)
    ]
    path = tmp_path / "26008.json"
    path.write_text(_make_report(records).model_dump_json())
    return path


class TestLoadDurationsCli:
    def test_loads_files_and_prints_summary(self, report_file, capsys):
        _run(str(report_file))
        out = capsys.readouterr().out
        assert "26008.json: 3 loaded, 0 updated, 0 unchanged, 0 out-of-bounds" in out
        assert "Total: 3 loaded, 0 updated, 0 unchanged, 0 out-of-bounds, 0 warnings" in out
        assert get_learned_duration_cascading("sprint_match", "age_55_59", "men") == pytest.approx(12.0)

    def test_totals_add_up_across_files(self, report_file, tmp_path, capsys):
        second = tmp_path / "copy.json"
        second.write_text(report_file.read_text())
        _run(str(report_file), str(second))
        assert "Total: 6 loaded" in capsys.readouterr().out

    def test_missing_file_exits_1(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            _run(str(tmp_path / "missing.json"))
        assert exc.value.code == 1

    @pytest.mark.parametrize("content", ["{not json", json.dumps({"version": "1.0"})])
    def test_unreadable_report_exits_1(self, tmp_path, content):
        path = tmp_path / "bad.json"
        path.write_text(content)
        with pytest.raises(SystemExit) as exc:
            _run(str(path))
        assert exc.value.code == 1

    def test_warnings_exit_1(self, report_file):
        stats = {"loaded": 0, "updated": 0, "unchanged": 0, "skipped_bounds": 0, "warnings": 1}
        with patch.object(load_durations, "load_report", return_value=stats), pytest.raises(SystemExit) as exc:
            _run(str(report_file))
        assert exc.value.code == 1

    def test_no_arguments_is_a_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            _run()
        assert exc.value.code == 2


class TestDedupPrompt:
    @pytest.fixture
    def duplicates(self):
        with (
            patch.object(load_durations, "init_db", side_effect=DuplicateRowsError(4)),
            patch.object(load_durations, "deduplicate_event_durations", return_value=4) as dedupe,
        ):
            yield dedupe

    @pytest.mark.parametrize("answer", ["y", "yes", "Y"])
    def test_yes_removes_duplicates_then_loads(self, duplicates, report_file, answer, capsys):
        with patch("builtins.input", return_value=answer) as prompt:
            _run(str(report_file))
        prompt.assert_called_once()
        duplicates.assert_called_once()
        assert "Removed 4 duplicate rows." in capsys.readouterr().out

    @pytest.mark.parametrize("answer", ["", "n", "no"])
    def test_anything_else_aborts(self, duplicates, report_file, answer, capsys):
        with patch("builtins.input", return_value=answer), pytest.raises(SystemExit) as exc:
            _run(str(report_file))
        assert exc.value.code == 1
        duplicates.assert_not_called()
        assert "Aborted. No changes made." in capsys.readouterr().out

    def test_force_skips_the_prompt(self, duplicates, report_file):
        with patch("builtins.input", side_effect=AssertionError("prompted")):
            _run("--force", str(report_file))
        duplicates.assert_called_once()
