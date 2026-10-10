"""One-off importer: Full Gas Cycling team-event entry list → app/data/fields/<id>.json.

Usage:
    curl -sL https://fullgascycling.co.uk/team-events/ -o team-events.html
    python -m tools.import_fullgas_teams team-events.html 26037

The page (2026 UCI Masters Track Worlds, EventId 26037) is a TablePress table with one
row per rider (Last Name, First Name, Team Name, Nationality, Age Category, Gender,
Event Type); a blank row separates teams. Each team counts once, under its first
rider's age category, gender and event type.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from app.models import CompetitionFields, FieldSize

_EVENT_DISCIPLINES = {"Team Sprint": "team_sprint", "Team Pursuit": "team_pursuit"}
_GENDERS = {"Male": "M", "Female": "W"}
_BAND_RE = re.compile(r"^(\d{2})(?:-(\d{2})|\+)$")
_OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "fields"


def parse_team_entries(html: str) -> list[FieldSize]:
    """Teams per discipline, gender and age band, in first-seen order."""
    table = BeautifulSoup(html, "html.parser").find("table", class_="tablepress")
    if table is None:
        raise ValueError("no TablePress table found")
    teams: Counter[tuple[str, str, int, int | None]] = Counter()
    in_team = False
    for tr in table.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not any(cells):
            in_team = False
            continue
        if in_team:
            continue
        in_team = True
        band = _BAND_RE.match(cells[4])
        if band is None:
            raise ValueError(f"unrecognised age category {cells[4]!r}")
        hi = int(band.group(2)) if band.group(2) else None
        teams[(_EVENT_DISCIPLINES[cells[6]], _GENDERS[cells[5]], int(band.group(1)), hi)] += 1
    return [FieldSize(discipline=d, gender=g, lo=lo, hi=hi, entries=n) for (d, g, lo, hi), n in teams.items()]  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("html", type=Path, help="saved team-events page")
    parser.add_argument("competition_id", type=int)
    parser.add_argument("--source", default="https://fullgascycling.co.uk/team-events/")
    args = parser.parse_args(argv)

    fields = parse_team_entries(args.html.read_text())
    doc = CompetitionFields(
        competition_id=args.competition_id, source=args.source, captured=date.today(), fields=fields
    )
    out = _OUT_DIR / f"{args.competition_id}.json"
    out.write_text(doc.model_dump_json(indent=2) + "\n")
    print(f"{sum(f.entries for f in fields)} teams in {len(fields)} fields → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
