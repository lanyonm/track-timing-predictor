"""One-off importer: Full Gas Cycling pages for 26037 → app/data/supplements/26037.json.

Usage:
    curl -sL https://fullgascycling.co.uk/team-events/ -o team-events.html
    curl -sL https://fullgascycling.co.uk/wmtc-schedule/ -o schedule.html
    python -m tools.import_fullgas_26037 team-events.html schedule.html

Both pages (2026 UCI Masters Track Worlds, EventId 26037) are TablePress tables.

- Team events: one row per rider (Last Name, First Name, Team Name, Nationality, Age
  Category, Gender, Event Type); a blank row separates teams. Each team counts once,
  under its first rider's age category, gender and event type.
- Schedule: one table per day (Event No., Gender, Age group, Event, Distance, Phase,
  Sign on close time). Points and scratch rows with a distance give that race's km.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from app.models import CompetitionSupplement, FieldSize, RaceDistance

COMPETITION_ID = 26037
TEAM_EVENTS_URL = "https://fullgascycling.co.uk/team-events/"
SCHEDULE_URL = "https://fullgascycling.co.uk/wmtc-schedule/"

_TEAM_DISCIPLINES = {"Team Sprint": "team_sprint", "Team Pursuit": "team_pursuit"}
_DISTANCE_DISCIPLINES = {"Points Race": "points_race", "Scratch Race": "scratch_race"}
_PHASES = {"Qualifying": "qualifying", "Final": "final", "Finals": "final"}
_GENDERS = {"Male": "M", "Female": "W"}
_BAND_RE = re.compile(r"^(\d{2})(?:-(\d{2})|\+)$")
_KM_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*km$")
_OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "supplements" / f"{COMPETITION_ID}.json"


def _band(text: str) -> tuple[int, int | None]:
    m = _BAND_RE.match(text)
    if m is None:
        raise ValueError(f"unrecognised age category {text!r}")
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


def _rows(html: str) -> list[list[str]]:
    tables = BeautifulSoup(html, "html.parser").find_all("table", class_="tablepress")
    if not tables:
        raise ValueError("no TablePress table found")
    return [[td.get_text(" ", strip=True) for td in tr.find_all("td")] for t in tables for tr in t.select("tbody tr")]


def parse_team_entries(html: str) -> list[FieldSize]:
    """Teams per discipline, gender and age band, in first-seen order."""
    teams: Counter[tuple[str, str, int, int | None]] = Counter()
    in_team = False
    for cells in _rows(html):
        if not any(cells):
            in_team = False
            continue
        if in_team:
            continue
        in_team = True
        lo, hi = _band(cells[4])
        teams[(_TEAM_DISCIPLINES[cells[6]], _GENDERS[cells[5]], lo, hi)] += 1
    return [FieldSize(discipline=d, gender=g, lo=lo, hi=hi, entries=n) for (d, g, lo, hi), n in teams.items()]  # type: ignore[arg-type]


def parse_race_distances(html: str) -> list[RaceDistance]:
    """Points and scratch distances per gender, age band and phase, in schedule order."""
    distances: dict[tuple[str, str, int, int | None, str], float] = {}
    for cells in _rows(html):
        if len(cells) < 6 or cells[3] not in _DISTANCE_DISCIPLINES or cells[5] not in _PHASES:
            continue
        m = _KM_RE.match(cells[4])
        if m is None:
            continue  # no distance published (e.g. 75-79 Men Points Race)
        lo, hi = _band(cells[2])
        key = (_DISTANCE_DISCIPLINES[cells[3]], _GENDERS[cells[1]], lo, hi, _PHASES[cells[5]])
        km = float(m.group(1))
        if distances.setdefault(key, km) != km:
            raise ValueError(f"conflicting distances for {key}: {distances[key]} and {km}")
    return [
        RaceDistance(discipline=d, gender=g, lo=lo, hi=hi, phase=p, km=km)  # type: ignore[arg-type]
        for (d, g, lo, hi, p), km in distances.items()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("team_events", type=Path, help="saved team-events page")
    parser.add_argument("schedule", type=Path, help="saved wmtc-schedule page")
    args = parser.parse_args(argv)

    doc = CompetitionSupplement(
        competition_id=COMPETITION_ID,
        sources=[TEAM_EVENTS_URL, SCHEDULE_URL],
        captured=date.today(),
        fields=parse_team_entries(args.team_events.read_text()),
        distances=parse_race_distances(args.schedule.read_text()),
    )
    _OUT.write_text(doc.model_dump_json(indent=2) + "\n")
    teams = sum(f.entries for f in doc.fields)
    print(f"{teams} teams in {len(doc.fields)} fields, {len(doc.distances)} race distances → {_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
