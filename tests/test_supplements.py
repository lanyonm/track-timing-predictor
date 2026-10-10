"""Tests for app/supplements.py (pre-event field sizes and distances) and tools/import_fullgas_26037.py."""

import json
from pathlib import Path

import pytest

from app.models import FieldSize, RaceDistance
from app.parser import parse_rider_list, parse_schedule
from app.predictor import infer_heats, predict_schedule
from app.supplements import heats_from_fields, load_supplement, scheduled_distances
from tools.import_fullgas_26037 import parse_race_distances, parse_team_entries

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def sessions_26037():
    with (FIXTURE_DIR / "schedule-26037.json").open() as f:
        return parse_schedule(json.load(f))


@pytest.fixture(scope="module")
def fields_26037():
    return parse_team_entries((FIXTURE_DIR / "fullgas-team-events-26037.html").read_text())


@pytest.fixture(scope="module")
def distances_26037():
    return parse_race_distances((FIXTURE_DIR / "fullgas-schedule-26037.html").read_text())


def _field(fields, discipline, gender, lo, hi):
    return next(f for f in fields if (f.discipline, f.gender, f.lo, f.hi) == (discipline, gender, lo, hi))


def _by_name(sessions, heats):
    return {
        e.name: heats[(s.session_id, e.position)]
        for s in sessions
        for e in s.events
        if (s.session_id, e.position) in heats
    }


class TestParseTeamEntries:
    def test_counts_teams_per_event_gender_and_band(self, fields_26037):
        assert _field(fields_26037, "team_sprint", "M", 45, 54).entries == 8
        assert _field(fields_26037, "team_pursuit", "M", 35, 44).entries == 6
        assert _field(fields_26037, "team_pursuit", "W", 45, 54).entries == 3

    def test_open_band_has_no_upper_bound(self, fields_26037):
        assert _field(fields_26037, "team_sprint", "W", 65, None).entries == 1
        assert _field(fields_26037, "team_pursuit", "M", 75, None).entries == 2

    def test_all_teams_counted(self, fields_26037):
        assert sum(f.entries for f in fields_26037) == 77
        assert len(fields_26037) == 18


class TestHeatsFromFields:
    def test_team_qualifying_is_one_team_per_heat(self, sessions_26037, fields_26037):
        heats = _by_name(sessions_26037, heats_from_fields(fields_26037, sessions_26037))
        # Matches the start-list heat counts of rounds already ridden.
        assert heats["55-64 Men Team Pursuit Qualifying"] == 6
        assert heats["65-74 Men Team Sprint Qualifying"] == 8
        assert heats["75+ Men Team Sprint Qualifying"] == 4
        assert heats["35-44 Women Team Sprint Qualifying"] == 5
        assert heats["45-54 Men Team Sprint Qualifying"] == 8

    def test_open_event_band_sums_the_bands_inside_it(self, sessions_26037, fields_26037):
        heats = _by_name(sessions_26037, heats_from_fields(fields_26037, sessions_26037))
        assert heats["55+ Women Team Sprint Qualifying"] == 2  # 55-64 + 65+

    def test_final_with_two_or_fewer_teams_is_one_heat(self, sessions_26037, fields_26037):
        heats = _by_name(sessions_26037, heats_from_fields(fields_26037, sessions_26037))
        assert heats["75+ Men Team Pursuit Final"] == 1
        assert heats["55+ Women Team Sprint Final"] == 1
        assert heats["65+ Women Team Sprint Final"] == 1
        assert "45-54 Men Team Sprint Final" not in heats  # 8 teams: left to the round rule

    def test_events_without_a_matching_field_are_left_out(self, sessions_26037):
        fields = [FieldSize(discipline="team_sprint", gender="M", lo=35, hi=44, entries=5)]
        heats = _by_name(sessions_26037, heats_from_fields(fields, sessions_26037))
        assert heats == {"35-44 Men Team Sprint Qualifying": 5}


class TestParseRaceDistances:
    def test_points_and_scratch_distances_by_band_and_phase(self, distances_26037):
        km = {(d.discipline, d.gender, d.lo, d.hi, d.phase): d.km for d in distances_26037}
        assert km[("points_race", "M", 35, 39, "final")] == 30
        assert km[("scratch_race", "M", 55, 59, "qualifying")] == 3.75
        assert km[("scratch_race", "M", 55, 59, "final")] == 7.5
        assert km[("scratch_race", "W", 55, None, "final")] == 5
        assert km[("points_race", "W", 35, 49, "final")] == 10

    def test_rows_without_a_distance_are_skipped(self, distances_26037):
        assert len(distances_26037) == 28
        assert not any(d.lo == 75 and d.discipline == "points_race" for d in distances_26037)


class TestScheduledDistances:
    def test_matches_band_gender_and_phase(self, sessions_26037, distances_26037):
        km = _by_name(sessions_26037, scheduled_distances(distances_26037, sessions_26037))
        assert km["55-59 Men Scratch Race Qualifier 1"] == 3.75
        assert km["55-59 Men Scratch Race Final"] == 7.5
        assert km["55+ Women Scratch Race Final"] == 5
        assert km["40-44 Men Scratch Race Final"] == 10
        assert "75-79 Men Points Race Final" not in km

    def test_band_must_match_exactly(self, sessions_26037):
        distances = [RaceDistance(discipline="scratch_race", gender="W", lo=55, hi=64, phase="final", km=5)]
        assert scheduled_distances(distances, sessions_26037) == {}


class TestLoadSupplement:
    def test_missing_file_is_none(self):
        assert load_supplement(1) is None

    def test_committed_26037_file_matches_the_parsers(self, fields_26037, distances_26037):
        supplement = load_supplement(26037)
        assert supplement is not None
        assert supplement.fields == fields_26037
        assert supplement.distances == distances_26037


class TestInferHeats:
    def test_field_sizes_have_entry_list_basis(self, sessions_26037, fields_26037):
        heats = _by_name(sessions_26037, infer_heats(sessions_26037, None, fields_26037))
        assert heats["45-54 Men Team Sprint Qualifying"] == (8, "entry_list")
        assert heats["75+ Men Team Pursuit Final"] == (1, "entry_list")
        assert heats["45-54 Men Team Sprint Final"] == (2, "round")

    def test_rider_list_still_sizes_individual_events(self, sessions_26037, fields_26037):
        rider_list = parse_rider_list((FIXTURE_DIR / "rider-list-26037.html").read_text())
        heats = infer_heats(sessions_26037, rider_list, fields_26037)
        assert {b for _, b in heats.values()} == {"round", "rider_list", "entry_list"}


class TestPredictSchedule:
    def test_committed_fields_size_team_qualifying(self, sessions_26037):
        result = predict_schedule(26037, sessions_26037)
        pred = next(
            p
            for s in result.sessions
            for p in s.event_predictions
            if p.event.name == "45-54 Men Team Sprint Qualifying"
        )
        assert (pred.heat_count, pred.heat_basis) == (8, "entry_list")

    def test_committed_distances_pace_mass_start_races(self, sessions_26037):
        result = predict_schedule(26037, sessions_26037)
        pred = next(
            p for s in result.sessions for p in s.event_predictions if p.event.name == "35-44 Women Scratch Race Final"
        )
        assert (pred.race_distance_km, pred.distance_basis, pred.race_kmh) == (5, "schedule", 43.5)
        assert pred.estimated_duration_minutes == pytest.approx(5 / 43.5 * 60 + 3.0)

    def test_other_competitions_are_unaffected(self, sessions_26037):
        result = predict_schedule(99999, sessions_26037)
        preds = [p for s in result.sessions for p in s.event_predictions]
        assert all(p.heat_basis != "entry_list" and p.distance_basis is None for p in preds)
