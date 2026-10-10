"""Pre-event supplements: team field sizes and race distances published off tracktiming.live.

Some competitions publish who entered each team event, and each mass-start race's
distance, on an organiser's site rather than on tracktiming.live, before start lists
exist. A one-off importer in ``tools/`` turns that into
``app/data/supplements/<competition_id>.json`` (``CompetitionSupplement``), which is
committed and deployed with the app; nothing is fetched at runtime.

Scope is what 26037's started rounds confirm: team pursuit and team sprint qualifying
ride one team per heat, a final with two or fewer teams is one heat, and points and
scratch distances are per gender, age band and phase.
"""

from functools import cache
from pathlib import Path
from typing import Literal

from app.disciplines import DISTANCE_DISCIPLINES
from app.models import CompetitionSupplement, FieldSize, RaceDistance, Session
from app.rider_list import AgeBand, event_band, is_qualifying

_DATA_DIR = Path(__file__).parent / "data" / "supplements"
_TEAM_DISCIPLINES = frozenset({"team_pursuit", "team_sprint"})
_FINAL_MAX_ONE_HEAT = 2  # teams that still fit one heat in a final


@cache
def load_supplement(competition_id: int) -> CompetitionSupplement | None:
    """The committed supplement for a competition, or None when there's no file."""
    path = _DATA_DIR / f"{competition_id}.json"
    if not path.exists():
        return None
    return CompetitionSupplement.model_validate_json(path.read_text())


def heats_from_fields(fields: list[FieldSize], sessions: list[Session]) -> dict[tuple[int, int], int]:
    """Heat counts for team events from field sizes, keyed by (session_id, position).

    Entries count when their band lies inside the event's band, so ``55+ Women`` sums
    the 55-64 and 65+ fields. Qualifying rounds get one heat per team; a final with
    two or fewer teams gets one heat. Other finals, and events with no entries, are left out.
    """
    heats: dict[tuple[int, int], int] = {}
    for s in sessions:
        for e in s.events:
            band = event_band(e.name)
            if e.is_special or e.discipline not in _TEAM_DISCIPLINES or band is None:
                continue
            n = sum(
                f.entries
                for f in fields
                if f.discipline == e.discipline and band.contains(AgeBand(f.gender, f.lo, f.hi))
            )
            if not n:
                continue
            if is_qualifying(e.name):
                heats[(s.session_id, e.position)] = n
            elif "Final" in e.name and n <= _FINAL_MAX_ONE_HEAT:
                heats[(s.session_id, e.position)] = 1
    return heats


def scheduled_distances(distances: list[RaceDistance], sessions: list[Session]) -> dict[tuple[int, int], float]:
    """Race distances in km for points and scratch races, keyed by (session_id, position).

    A distance applies when its discipline, gender and age band equal the event's and its
    phase matches: ``Qualifier N``/``Qualifying`` events take the qualifying distance,
    ``Final`` events the final's.
    """
    by_key = {(d.discipline, AgeBand(d.gender, d.lo, d.hi), d.phase): d.km for d in distances}
    km: dict[tuple[int, int], float] = {}
    for s in sessions:
        for e in s.events:
            band = event_band(e.name)
            if e.is_special or e.discipline not in DISTANCE_DISCIPLINES or band is None:
                continue
            phase: Literal["qualifying", "final"] | None = (
                "qualifying" if is_qualifying(e.name) else "final" if "Final" in e.name else None
            )
            if phase and (d := by_key.get((e.discipline, band, phase))):
                km[(s.session_id, e.position)] = d
    return km
