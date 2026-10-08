"""Rider List fallback matching: a racer's events from their category and entered codes.

Some competitions publish start lists only after per-session sign-on, but publish a
Rider List up front with each rider's category and entered event codes. This module
matches one rider's entry against schedule events by age band, gender and code.

Scope is the formats seen in EventId 26037 (masters world championships): categories
``[MW]`` + four digits (lo-hi) or two digits (lo and over), event names carrying
``NN-NN`` or ``NN+`` followed by ``Men``/``Women``, and the codes in
``CODE_DISCIPLINES``. Anything else yields no match. Pure functions, no I/O.

``categorizer.categorize_event`` also parses age brackets and rounds, but it is
used only by ``tools/`` and accepts formats (French names, three-digit brackets)
that this narrow, fixture-backed matcher deliberately doesn't.
"""

import re
from collections import defaultdict
from typing import NamedTuple

from app.models import Event, RiderListEntry, RiderMatch, Session

_CATEGORY_RE = re.compile(r"^([MW])(\d{2})(\d{2})?$")
_EVENT_BAND_RE = re.compile(r"\b(\d{2})(?:-(\d{2})|\+)\s+(Men|Women)\b")
_QUALIFYING_RE = re.compile(r"\bQualif(?:ying|ier\s+\d+)\b", re.IGNORECASE)
_NUMBERED_QUALIFIER_RE = re.compile(r"\bQualifier\s+\d+\b", re.IGNORECASE)

# Rider List event code → discipline keys from disciplines.detect_discipline.
CODE_DISCIPLINES: dict[str, frozenset[str]] = {
    "S": frozenset({"sprint_qualifying", "sprint_match"}),
    "TT": frozenset({"time_trial_500", "time_trial_750", "time_trial_kilo"}),
    "IP": frozenset({"pursuit_2k", "pursuit_3k"}),
    "TP": frozenset({"team_pursuit"}),
    "TS": frozenset({"team_sprint"}),
    "SCR": frozenset({"scratch_race"}),
    "PTS": frozenset({"points_race"}),
}
_DISCIPLINE_CODES = {d: code for code, ds in CODE_DISCIPLINES.items() for d in ds}


class AgeBand(NamedTuple):
    gender: str  # "M" or "W"
    lo: int
    hi: int | None  # None = open upper bound

    def contains(self, other: "AgeBand") -> bool:
        """True when ``other`` lies wholly inside this band, with the same gender."""
        if self.gender != other.gender or other.lo < self.lo:
            return False
        return self.hi is None or (other.hi is not None and other.hi <= self.hi)


def category_band(category: str) -> AgeBand | None:
    """Age band of a masters category (``M6064``, ``M90``), or None for any other category."""
    m = _CATEGORY_RE.match(category)
    if not m:
        return None
    return AgeBand(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)


def event_band(event_name: str) -> AgeBand | None:
    """Age band and gender in an event name (``55-64 Men``, ``65+ Women``), or None."""
    m = _EVENT_BAND_RE.search(event_name)
    if not m:
        return None
    gender = "M" if m.group(3) == "Men" else "W"
    return AgeBand(gender, int(m.group(1)), int(m.group(2)) if m.group(2) else None)


def event_code(discipline: str) -> str | None:
    """Rider List code for a discipline key, or None when the discipline isn't mapped."""
    return _DISCIPLINE_CODES.get(discipline)


def find_rider(entries: list[RiderListEntry], user_tokens: frozenset[str]) -> RiderListEntry | None:
    """First entry whose normalized name equals the racer's tokens."""
    if not user_tokens:
        return None
    return next((e for e in entries if e.normalized_tokens == user_tokens), None)


def match_events(entry: RiderListEntry, sessions: list[Session]) -> dict[tuple[int, int], RiderMatch]:
    """Rider List matches for one rider across the competition, keyed by (session_id, position).

    An event matches when its band contains the rider's category band and its code is
    one the rider entered. Certainty is decided per code over the events this rider
    matches: a lone event is certain, otherwise qualifying rounds are certain and the
    rest are tentative ("if advancing"). Deciding per rider rather than per event band
    keeps finals tentative when overlapping open bands (55+ and 65+) share a qualifying
    round. When the rider matches several numbered qualifiers for a code, they ride
    only one of them, so those matches are flagged ``parallel_qualifier``. Special
    events never match. Start-list precedence is the caller's job.
    """
    rider_band = category_band(entry.category)
    if rider_band is None:
        return {}

    by_code: dict[str, list[tuple[tuple[int, int], Event]]] = defaultdict(list)
    for s in sessions:
        for e in s.events:
            band = event_band(e.name)
            code = event_code(e.discipline)
            if e.is_special or band is None or code is None:
                continue
            if code in entry.codes and band.contains(rider_band):
                by_code[code].append(((s.session_id, e.position), e))

    matches: dict[tuple[int, int], RiderMatch] = {}
    for events in by_code.values():
        parallel = sum(1 for _, e in events if _NUMBERED_QUALIFIER_RE.search(e.name)) > 1
        for key, e in events:
            certain = len(events) == 1 or bool(_QUALIFYING_RE.search(e.name))
            matches[key] = RiderMatch(
                source="rider_list",
                tentative=not certain,
                parallel_qualifier=parallel and bool(_NUMBERED_QUALIFIER_RE.search(e.name)),
            )
    return matches
