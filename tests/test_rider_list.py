"""Tests for app/rider_list.py: Rider List fallback matching (EventId 26037 formats)."""

import json
from datetime import time
from pathlib import Path

import pytest

from app.models import Event, EventStatus, RiderListEntry, Session, normalize_rider_name
from app.parser import parse_rider_list, parse_schedule
from app.rider_list import (
    AgeBand,
    category_band,
    estimate_heats,
    event_band,
    event_code,
    find_rider,
    match_events,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def sessions_26037():
    with (FIXTURE_DIR / "schedule-26037.json").open() as f:
        return parse_schedule(json.load(f))


@pytest.fixture(scope="module")
def rider_list_26037():
    return parse_rider_list((FIXTURE_DIR / "rider-list-26037.html").read_text())


def _rider(entries: list[RiderListEntry], name: str) -> RiderListEntry:
    return next(e for e in entries if e.name == name)


def _matches(sessions, entry):
    """Map event name → RiderMatch for every event matched from the Rider List."""
    matches = match_events(entry, sessions)
    return {
        e.name: matches[(s.session_id, e.position)]
        for s in sessions
        for e in s.events
        if (s.session_id, e.position) in matches
    }


def _entered(sessions, entry) -> set[str]:
    return {name for name, m in _matches(sessions, entry).items() if not m.tentative}


# ── Bands, codes, lookup ─────────────────────────────────────────────────────


class TestCategoryBand:
    @pytest.mark.parametrize(
        ("category", "expected"),
        [
            ("M6064", AgeBand("M", 60, 64)),
            ("W4549", AgeBand("W", 45, 49)),
            ("M90", AgeBand("M", 90, None)),
        ],
    )
    def test_masters_categories(self, category, expected):
        assert category_band(category) == expected

    @pytest.mark.parametrize("category", ["Elite", "MU17", "M60 Open", ""])
    def test_other_categories(self, category):
        assert category_band(category) is None


class TestEventBand:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("55-64 Men Team Sprint Qualifying", AgeBand("M", 55, 64)),
            ("65+ Women Sprint Final Ride 1", AgeBand("W", 65, None)),
            ("90+ Men 500m Time Trial Final", AgeBand("M", 90, None)),
        ],
    )
    def test_banded_names(self, name, expected):
        assert event_band(name) == expected

    def test_unbanded_name(self):
        assert event_band("Elite Men Keirin") is None


class TestContains:
    def test_closed_band(self):
        assert AgeBand("M", 55, 64).contains(AgeBand("M", 60, 64))

    def test_open_band_contains_open_category(self):
        assert AgeBand("M", 80, None).contains(AgeBand("M", 90, None))

    def test_disjoint_band(self):
        assert not AgeBand("M", 65, 74).contains(AgeBand("M", 60, 64))

    def test_rider_in_two_open_bands(self):
        w6569 = AgeBand("W", 65, 69)
        assert AgeBand("W", 55, None).contains(w6569)
        assert AgeBand("W", 65, None).contains(w6569)

    def test_closed_band_excludes_open_category(self):
        assert not AgeBand("M", 85, 89).contains(AgeBand("M", 90, None))

    def test_gender_mismatch(self):
        assert not AgeBand("W", 55, 64).contains(AgeBand("M", 60, 64))


class TestEventCode:
    @pytest.mark.parametrize(
        ("discipline", "code"),
        [
            ("sprint_qualifying", "S"),
            ("sprint_match", "S"),
            ("time_trial_500", "TT"),
            ("time_trial_750", "TT"),
            ("time_trial_kilo", "TT"),
            ("pursuit_2k", "IP"),
            ("pursuit_3k", "IP"),
            ("team_pursuit", "TP"),
            ("team_sprint", "TS"),
            ("scratch_race", "SCR"),
            ("points_race", "PTS"),
        ],
    )
    def test_mapped(self, discipline, code):
        assert event_code(discipline) == code

    @pytest.mark.parametrize("discipline", ["pursuit_4k", "keirin", "time_trial_generic"])
    def test_unmapped(self, discipline):
        assert event_code(discipline) is None


class TestFindRider:
    def test_found(self, rider_list_26037):
        entry = find_rider(rider_list_26037, normalize_rider_name("Brian Abers"))
        assert entry is not None
        assert entry.name == "ABERS Brian"

    def test_unknown(self, rider_list_26037):
        assert find_rider(rider_list_26037, normalize_rider_name("Nobody Here")) is None


# ── Fixture-driven matching ──────────────────────────────────────────────────


class TestEnteredMatches:
    def test_abers(self, sessions_26037, rider_list_26037):
        entry = _rider(rider_list_26037, "ABERS Brian")
        assert _entered(sessions_26037, entry) == {
            "60-64 Men Sprint Qualifying",
            "60-64 Men 500m Time Trial Final",
            "55-64 Men Team Sprint Qualifying",
        }

    def test_fowler(self, sessions_26037, rider_list_26037):
        entry = _rider(rider_list_26037, "FOWLER Walter")
        assert _entered(sessions_26037, entry) == {"90+ Men 500m Time Trial Final"}

    def test_achiler(self, sessions_26037, rider_list_26037):
        entry = _rider(rider_list_26037, "ACHILER Becky")
        assert _entered(sessions_26037, entry) == {
            "45-54 Women Team Sprint Qualifying",
            "45-54 Women Team Pursuit Qualifying",
        }

    @pytest.mark.parametrize(
        "name",
        [
            "65-74 Men Team Sprint Qualifying",  # band doesn't contain M6064
            "55-59 Men Sprint Qualifying",  # band doesn't contain M6064
            "60-64 Men Pursuit Qualifying",  # not entered in IP
        ],
    )
    def test_abers_non_matches(self, sessions_26037, rider_list_26037, name):
        entry = _rider(rider_list_26037, "ABERS Brian")
        assert name not in _matches(sessions_26037, entry)

    def test_match_shape(self, sessions_26037, rider_list_26037):
        matches = _matches(sessions_26037, _rider(rider_list_26037, "ABERS Brian"))
        assert matches
        for m in matches.values():
            assert m.source == "rider_list"
            assert m.heat is None
            assert m.heat_count is None
            assert m.team_name is None

    def test_special_event_never_matches(self, rider_list_26037):
        entry = _rider(rider_list_26037, "ABERS Brian")
        special = Event(
            position=0,
            name="60-64 Men Sprint Qualifying",
            discipline="sprint_qualifying",
            status=EventStatus.NOT_READY,
            is_special=True,
        )
        session = Session(session_id=1, day="Friday", scheduled_start=time(9, 0), events=[special])
        assert match_events(entry, [session]) == {}

    def test_unbanded_category_never_matches(self, sessions_26037):
        entry = RiderListEntry(name="ABERS Brian", category="Elite", codes=frozenset({"S", "TS", "TT"}))
        assert _matches(sessions_26037, entry) == {}


def _tentative(sessions, entry) -> set[str]:
    return {name for name, m in _matches(sessions, entry).items() if m.tentative}


class TestTentativeMatches:
    def test_abers(self, sessions_26037, rider_list_26037):
        entry = _rider(rider_list_26037, "ABERS Brian")
        sprint_rounds = {
            f"60-64 Men Sprint {rnd} Ride {ride}" for rnd in ("1/4 Final", "1/2 Final", "Final") for ride in (1, 2, 3)
        }
        assert _tentative(sessions_26037, entry) == sprint_rounds | {"55-64 Men Team Sprint Final"}
        assert len(_matches(sessions_26037, entry)) == 13

    def test_achiler(self, sessions_26037, rider_list_26037):
        entry = _rider(rider_list_26037, "ACHILER Becky")
        assert _tentative(sessions_26037, entry) == {
            "45-54 Women Team Sprint Final",
            "45-54 Women Team Pursuit Final",
        }

    def test_scratch_qualifiers_entered_final_tentative(self, sessions_26037, rider_list_26037):
        # BAISCH Paul: M5559, entered in SCR
        matches = _matches(sessions_26037, _rider(rider_list_26037, "BAISCH Paul"))
        assert matches["55-59 Men Scratch Race Qualifier 1"].tentative is False
        assert matches["55-59 Men Scratch Race Qualifier 2"].tentative is False
        assert matches["55-59 Men Scratch Race Final"].tentative is True

    @pytest.mark.parametrize("name", ["KERCSO-MAGOS Zsuzsanna", "BELL Jennifer"])  # W3539, W4044; both PTS
    def test_only_event_in_group_is_entered(self, sessions_26037, rider_list_26037, name):
        matches = _matches(sessions_26037, _rider(rider_list_26037, name))
        assert matches["35-49 Women Points Race Final"].tentative is False

    def test_sprint_ride_3_is_tentative(self, sessions_26037, rider_list_26037):
        matches = _matches(sessions_26037, _rider(rider_list_26037, "ABERS Brian"))
        ride_3 = [m for name, m in matches.items() if name.endswith("Ride 3")]
        assert len(ride_3) == 3
        assert all(m.tentative for m in ride_3)

    def test_overlapping_open_bands_share_qualifying(self, sessions_26037, rider_list_26037):
        # TRAN Lan (W7579, TS) falls inside both 55+ and 65+ Women. The 65+ final is the
        # only event in its own band, but it hangs off the shared 55+ qualifying round.
        matches = _matches(sessions_26037, _rider(rider_list_26037, "TRAN Lan"))
        assert matches["55+ Women Team Sprint Qualifying"].tentative is False
        assert matches["55+ Women Team Sprint Final"].tentative is True
        assert matches["65+ Women Team Sprint Final"].tentative is True
        assert matches["55+ Women Scratch Race Final"].tentative is False


class TestParallelQualifiers:
    """Numbered qualifiers run in parallel: the rider rides one, so times are be-ready-by times."""

    def test_scratch_qualifiers_flagged(self, sessions_26037, rider_list_26037):
        matches = _matches(sessions_26037, _rider(rider_list_26037, "BAISCH Paul"))
        assert matches["55-59 Men Scratch Race Qualifier 1"].parallel_qualifier is True
        assert matches["55-59 Men Scratch Race Qualifier 2"].parallel_qualifier is True
        assert matches["55-59 Men Scratch Race Final"].parallel_qualifier is False

    def test_single_qualifying_round_not_flagged(self, sessions_26037, rider_list_26037):
        matches = _matches(sessions_26037, _rider(rider_list_26037, "ABERS Brian"))
        assert not any(m.parallel_qualifier for m in matches.values())


class TestEstimateHeats:
    """Individual qualifying rounds are sized from Rider List entrants in the band."""

    @pytest.fixture(scope="class")
    def heats(self, sessions_26037, rider_list_26037):
        names = {(s.session_id, e.position): e.name for s in sessions_26037 for e in s.events}
        return {names[k]: v for k, v in estimate_heats(rider_list_26037, sessions_26037).items()}

    def test_sprint_qualifying_one_heat_per_rider(self, heats):
        # 17 M5559 sprinters; 20 of the 21 45-49 entrants rode at 26037.
        assert heats["55-59 Men Sprint Qualifying"] == 17

    def test_pursuit_qualifying_two_riders_per_heat(self, heats):
        # 22 M5559 pursuiters → 11 heats; 13 75-79 entrants rode 6 heats at 26037.
        assert heats["55-59 Men Pursuit Qualifying"] == 11

    def test_open_band_counts_every_category_inside(self, heats):
        # 80+ Men: M8084 and older.
        assert heats["80+ Men Sprint Qualifying"] == 8

    def test_finals_and_team_events_left_out(self, heats):
        assert "55-59 Men Pursuit Final" not in heats
        assert "55-59 Men Sprint 1/8 Final" not in heats
        assert "55+ Women Team Pursuit Qualifying" not in heats
        assert "35-39 Women Kilo Time Trial Final" not in heats

    def test_no_entrants_left_out(self):
        event = Event(
            position=0,
            name="90+ Women Sprint Qualifying",
            discipline="sprint_qualifying",
            status=EventStatus.NOT_READY,
            is_special=False,
        )
        session = Session(session_id=1, day="Day", scheduled_start=time(10, 0), events=[event])
        assert estimate_heats([RiderListEntry(name="A B", category="M90", codes=frozenset({"S"}))], [session]) == {}
