"""Tests for parse_start_list_riders() in app/parser.py."""
from pathlib import Path

from app.parser import parse_start_list_riders

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "start-list-sample.html"


class TestParseStartListRiders:
    def setup_method(self):
        self.html = FIXTURE_PATH.read_text()
        self.riders = parse_start_list_riders(self.html)

    def test_multi_heat_extraction(self):
        """All riders across 3 heats are extracted."""
        assert len(self.riders) == 9

    def test_correct_heat_assignment(self):
        heat_1 = [r for r in self.riders if r.heat == 1]
        heat_2 = [r for r in self.riders if r.heat == 2]
        heat_3 = [r for r in self.riders if r.heat == 3]
        assert len(heat_1) == 3
        assert len(heat_2) == 3
        assert len(heat_3) == 3

    def test_single_heat_event(self):
        html = "Heat 1\n101  SMITH John\n102  DOE Jane\n"
        riders = parse_start_list_riders(html)
        assert len(riders) == 2
        assert all(r.heat == 1 for r in riders)

    def test_empty_html_returns_empty(self):
        assert parse_start_list_riders("") == []

    def test_malformed_html_returns_empty(self):
        assert parse_start_list_riders("No heats here, just random text.") == []

    def test_normalized_tokens_are_lowercased(self):
        rider = self.riders[0]  # PITTARD Charlie
        assert all(t.islower() for t in rider.normalized_tokens)

    def test_normalized_tokens_order_independent(self):
        """frozenset comparison is order-independent."""
        rider = self.riders[0]  # PITTARD Charlie
        assert rider.normalized_tokens == frozenset({"pittard", "charlie"})

    def test_apostrophe_normalized(self):
        """O'BRIEN normalizes to 'obrien'."""
        obrien = [r for r in self.riders if "obrien" in r.normalized_tokens]
        assert len(obrien) == 1
        assert obrien[0].normalized_tokens == frozenset({"obrien", "liam"})

    def test_diacritics_normalized(self):
        """MÜLLER normalizes to 'muller'."""
        muller = [r for r in self.riders if "muller" in r.normalized_tokens]
        assert len(muller) == 1
        assert muller[0].normalized_tokens == frozenset({"muller", "hans"})
