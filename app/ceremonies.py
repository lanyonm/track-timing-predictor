"""Medal ceremony podium forecasting: how many podiums each ceremony awards.

A ceremony awards the finals held since the previous ceremony, in schedule order and
across sessions. Rounds (1/N Finals) and placement finals (5-8, 7-12) award nothing.
A sprint Final counts after its last scheduled ride. Team events are one podium
whatever their age range. A combined-age bunch race (``35-49 Women Points
Race``, ``50+ Women``) awards one podium per category entered, taken from the start
list's Category column, else the Rider List, else the five-year bands in the name.

Scope is the formats seen in EventId 26037 (masters world championships), checked
against its CEREMONY-N-R.htm pages. A ceremony whose window holds a final without a
``NN-NN``/``NN+ Men|Women`` band gets no forecast and keeps the default duration.
Pure functions, no I/O.
"""

import re
from collections.abc import Mapping

from app.disciplines import CEREMONY_BASE_MINUTES, CEREMONY_PER_PODIUM_MINUTES, is_placement_final, split_ride
from app.models import Event, RiderListEntry, Session
from app.rider_list import AgeBand, category_band, event_band, event_code

_FINAL_RE = re.compile(r"\bFinal\b")
_ROUND_RE = re.compile(r"\b1/\d+\s+Final\b")

# Bunch races whose combined-age fields are split into per-category podiums.
_CATEGORY_PODIUM_DISCIPLINES = frozenset({"points_race", "scratch_race"})


def ceremony_duration(podiums: int) -> float:
    """Minutes for a ceremony awarding this many podiums."""
    return CEREMONY_BASE_MINUTES + podiums * CEREMONY_PER_PODIUM_MINUTES


def _final_key(event: Event) -> str | None:
    """The medal final an event belongs to (rides collapsed), or None for anything else."""
    if (
        event.is_special
        or not _FINAL_RE.search(event.name)
        or _ROUND_RE.search(event.name)
        or is_placement_final(event.name)
    ):
        return None
    ride = split_ride(event.name)
    return ride[0] if ride else event.name


def _combined_band(event: Event) -> AgeBand | None:
    """The age band of a bunch-race final spanning more than one five-year category."""
    if event.discipline not in _CATEGORY_PODIUM_DISCIPLINES or _final_key(event) is None:
        return None
    band = event_band(event.name)
    if band is None or (band.hi is not None and band.hi - band.lo <= 4):
        return None
    return band


def needs_categories(event: Event) -> bool:
    """True for a combined-age bunch final, whose podium count depends on the categories entered."""
    return _combined_band(event) is not None


def _podiums(event: Event, categories: frozenset[str] | None, rider_list: list[RiderListEntry] | None) -> int | None:
    """Podiums awarded for one final, or None when its name has no masters age band."""
    if event_band(event.name) is None:
        return None
    band = _combined_band(event)
    if band is None:
        return 1
    if categories:
        return len(categories)
    code = event_code(event.discipline)
    if rider_list and code:
        entered = {
            e.category
            for e in rider_list
            if code in e.codes and (cb := category_band(e.category)) is not None and band.contains(cb)
        }
        if entered:
            return len(entered)
    return 1 if band.hi is None else (band.hi - band.lo + 1) // 5


def forecast_podiums(
    sessions: list[Session],
    categories: Mapping[tuple[int, int], frozenset[str]],
    rider_list: list[RiderListEntry] | None,
) -> dict[tuple[int, int], int]:
    """
    Forecast podiums per medal ceremony, keyed by (session_id, position).

    categories maps (session_id, position) to a start list's Category values. Ceremonies
    with no finals in their window, or with a final outside the supported format, are
    left out.
    """
    flat = [(s.session_id, e) for s in sessions for e in s.events]
    last_index: dict[str, int] = {}
    for i, (_, e) in enumerate(flat):
        if (key := _final_key(e)) is not None:
            last_index[key] = i

    forecasts: dict[tuple[int, int], int] = {}
    window_start = 0
    for i, (session_id, e) in enumerate(flat):
        if e.discipline != "ceremony":
            continue
        counts = []
        for j in sorted(j for j in last_index.values() if window_start <= j < i):
            sid, final = flat[j]
            counts.append(_podiums(final, categories.get((sid, final.position)), rider_list))
        window_start = i + 1
        if counts and None not in counts:
            forecasts[(session_id, e.position)] = sum(c for c in counts if c is not None)
    return forecasts
