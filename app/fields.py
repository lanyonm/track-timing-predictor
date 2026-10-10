"""Pre-event field sizes: heat counts for team events before their start lists post.

Some competitions publish who entered each team event (teams, not riders) on an
organiser's site rather than on tracktiming.live. A one-off importer in ``tools/``
turns that into ``app/data/fields/<competition_id>.json`` (``CompetitionFields``),
which is committed and deployed with the app; nothing is fetched at runtime.

Scope is what 26037's started rounds confirm: team pursuit and team sprint qualifying
ride one team per heat, and a final with two or fewer teams is one heat.
"""

from functools import cache
from pathlib import Path

from app.models import CompetitionFields, FieldSize, Session
from app.rider_list import AgeBand, event_band, is_qualifying

_DATA_DIR = Path(__file__).parent / "data" / "fields"
_TEAM_DISCIPLINES = frozenset({"team_pursuit", "team_sprint"})
_FINAL_MAX_ONE_HEAT = 2  # teams that still fit one heat in a final


@cache
def load_fields(competition_id: int) -> list[FieldSize] | None:
    """The committed field sizes for a competition, or None when there's no file."""
    path = _DATA_DIR / f"{competition_id}.json"
    if not path.exists():
        return None
    return CompetitionFields.model_validate_json(path.read_text()).fields


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
