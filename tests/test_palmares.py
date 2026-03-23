"""Tests for palmares SQLite backend."""
from app.models import PalmaresEntry
from app.palmares import (
    count_competition_palmares,
    delete_competition_palmares,
    get_palmares,
    save_palmares_entries,
)


def _make_entry(racer="charlie pittard", comp_id=26008, comp_name="Ontario Track Championships",
                comp_date="2026-02-28", session_id=1, session_name="Friday",
                position=3, event_name="U17 Women Pursuit Final",
                audit_url="results/E26008/W1516-IP-2000-F-0-AUDIT-R.htm"):
    return PalmaresEntry(
        racer_name=racer,
        competition_id=comp_id,
        competition_name=comp_name,
        competition_date=comp_date,
        session_id=session_id,
        session_name=session_name,
        event_position=position,
        event_name=event_name,
        audit_url=audit_url,
    )


class TestSavePalmaresEntries:
    def test_save_and_retrieve(self):
        entries = [
            _make_entry(position=3, event_name="U17 Women Pursuit Final"),
            _make_entry(position=5, event_name="U17 Women Scratch Race"),
        ]
        save_palmares_entries(entries)
        result = get_palmares("charlie pittard")
        assert len(result) == 1
        assert result[0].competition_id == 26008
        assert len(result[0].entries) == 2
        assert result[0].entries[0].event_name == "U17 Women Pursuit Final"
        assert result[0].entries[1].event_name == "U17 Women Scratch Race"

    def test_no_duplicates_on_resave(self):
        entries = [_make_entry(position=10, event_name="U17 Women Points Race")]
        save_palmares_entries(entries)
        save_palmares_entries(entries)  # re-save same entries
        result = get_palmares("charlie pittard")
        # Should still have entries from previous test + this one, but no duplicates
        comp = next(c for c in result if c.competition_id == 26008)
        positions = [e.event_position for e in comp.entries]
        assert positions.count(10) == 1

    def test_multiple_competitions(self):
        entries = [
            _make_entry(racer="test racer multi", comp_id=25022, comp_name="Series 1",
                        comp_date="2026-01-15", position=1, event_name="Event A"),
            _make_entry(racer="test racer multi", comp_id=25023, comp_name="Series 2",
                        comp_date="2026-02-20", position=1, event_name="Event B"),
        ]
        save_palmares_entries(entries)
        result = get_palmares("test racer multi")
        assert len(result) == 2
        # Reverse chronological
        assert result[0].competition_id == 25023
        assert result[1].competition_id == 25022


class TestCountCompetition:
    def test_count_returns_correct_value(self):
        entries = [
            _make_entry(racer="count racer", position=1, event_name="Event 1"),
            _make_entry(racer="count racer", position=2, event_name="Event 2"),
            _make_entry(racer="count racer", position=4, event_name="Event 3"),
        ]
        save_palmares_entries(entries)
        assert count_competition_palmares("count racer", 26008) == 3

    def test_count_zero_for_unknown(self):
        assert count_competition_palmares("nobody", 99999) == 0


class TestDeleteCompetition:
    def test_delete_removes_entries(self):
        entries = [
            _make_entry(racer="del racer", comp_id=30001, position=1, event_name="E1"),
            _make_entry(racer="del racer", comp_id=30001, position=2, event_name="E2"),
        ]
        save_palmares_entries(entries)
        assert count_competition_palmares("del racer", 30001) == 2
        deleted = delete_competition_palmares("del racer", 30001)
        assert deleted == 2
        assert count_competition_palmares("del racer", 30001) == 0

    def test_delete_preserves_other_competitions(self):
        entries = [
            _make_entry(racer="del racer2", comp_id=30002, position=1, event_name="E1"),
            _make_entry(racer="del racer2", comp_id=30003, position=1, event_name="E2"),
        ]
        save_palmares_entries(entries)
        delete_competition_palmares("del racer2", 30002)
        result = get_palmares("del racer2")
        assert len(result) == 1
        assert result[0].competition_id == 30003


class TestGetPalmares:
    def test_empty_for_unknown_racer(self):
        result = get_palmares("unknown racer xyz")
        assert result == []

    def test_entries_sorted_by_session_and_position(self):
        entries = [
            _make_entry(racer="sort racer", position=5, session_id=2, event_name="Later Event"),
            _make_entry(racer="sort racer", position=1, session_id=1, event_name="First Event"),
            _make_entry(racer="sort racer", position=3, session_id=1, event_name="Middle Event"),
        ]
        save_palmares_entries(entries)
        result = get_palmares("sort racer")
        assert len(result) == 1
        names = [e.event_name for e in result[0].entries]
        assert names == ["First Event", "Middle Event", "Later Event"]
