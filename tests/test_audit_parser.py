"""Tests for audit page HTML parsing."""
from app.audit_parser import parse_audit_riders, filter_rider_data, format_csv


def _load_fixture():
    with open("tests/fixtures/audit-pursuit-26008.html") as f:
        return f.read()


class TestParseAuditRiders:
    def test_extracts_all_five_riders(self):
        html = _load_fixture()
        riders = parse_audit_riders(html)
        assert len(riders) == 5

    def test_rider_names(self):
        html = _load_fixture()
        riders = parse_audit_riders(html)
        names = [r["name"] for r in riders]
        assert "PITTARD Charlie" in names
        assert "BURTON Maelle" in names
        assert "ALDEN Calla" in names
        assert "RANKL Avery" in names
        assert "MARCYNUK Addison" in names

    def test_heat_assignments(self):
        html = _load_fixture()
        riders = parse_audit_riders(html)
        heats = {r["name"]: r["heat"] for r in riders}
        assert heats["PITTARD Charlie"] == "Heat 1"
        assert heats["BURTON Maelle"] == "Heat 2"
        assert heats["ALDEN Calla"] == "Heat 2"
        assert heats["RANKL Avery"] == "Heat 3"
        assert heats["MARCYNUK Addison"] == "Heat 3"

    def test_rider_data_rows(self):
        html = _load_fixture()
        riders = parse_audit_riders(html)
        pittard = next(r for r in riders if r["name"] == "PITTARD Charlie")
        # 16 data rows for a 2km pursuit (8 laps × 2 checkpoints)
        assert len(pittard["rows"]) == 16
        # First row: 125m checkpoint
        assert pittard["rows"][0]["Dist"] == "125"
        assert pittard["rows"][0]["Time"] == "15.531"
        assert pittard["rows"][0]["Rank"] == "5"

    def test_bib_number_stripped(self):
        html = _load_fixture()
        riders = parse_audit_riders(html)
        # No rider name should start with a digit (bib was stripped)
        for rider in riders:
            assert not rider["name"][0].isdigit()


class TestFilterRiderData:
    def test_match_by_normalized_name(self):
        html = _load_fixture()
        riders = parse_audit_riders(html)
        # Match "Charlie Pittard" against "PITTARD Charlie"
        result = filter_rider_data(riders, "Charlie Pittard")
        assert len(result) == 1
        assert result[0]["name"] == "PITTARD Charlie"

    def test_no_match_returns_empty(self):
        html = _load_fixture()
        riders = parse_audit_riders(html)
        result = filter_rider_data(riders, "Nonexistent Person")
        assert result == []

    def test_case_insensitive_match(self):
        html = _load_fixture()
        riders = parse_audit_riders(html)
        result = filter_rider_data(riders, "charlie pittard")
        assert len(result) == 1


class TestFormatCSV:
    def test_produces_valid_csv(self):
        riders = [{
            "name": "PITTARD Charlie",
            "heat": "Heat 1",
            "rows": [
                {"Dist": "125", "Time": "15.531", "Rank": "5",
                 "Lap": "", "Lap_Rank": "", "Sect": "15.531", "Sect_Rank": "5"},
            ],
        }]
        csv_str = format_csv(riders, "test-event")
        lines = csv_str.strip().splitlines()
        assert len(lines) == 2  # header + 1 data row
        assert "Heat" in lines[0] and "Dist" in lines[0]
        assert "Heat 1" in lines[1]
        assert "15.531" in lines[1]

    def test_empty_data_produces_headers_only(self):
        csv_str = format_csv([], "test-event")
        lines = csv_str.strip().splitlines()
        assert len(lines) == 1
        assert "Heat" in lines[0] and "Dist" in lines[0]
