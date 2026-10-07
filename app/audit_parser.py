"""Parse tracktiming.live audit result pages for CSV export."""

import csv
import io
import re
from typing import TypedDict

from bs4 import BeautifulSoup

from app.models import normalize_rider_name

CSV_HEADERS = ["Heat", "Dist", "Time", "Rank", "Lap", "Lap_Rank", "Sect", "Sect_Rank"]


# Keys match CSV_HEADERS[1:]
class AuditRow(TypedDict):
    Dist: str
    Time: str
    Rank: str
    Lap: str
    Lap_Rank: str
    Sect: str
    Sect_Rank: str


class AuditRider(TypedDict):
    name: str  # e.g. "PITTARD Charlie"
    heat: str  # e.g. "Heat 1"
    rows: list[AuditRow]


def parse_audit_riders(html: str) -> list[AuditRider]:
    """Parse all riders from an audit page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    riders: list[AuditRider] = []
    current_heat = "Heat 1"

    for container in soup.find_all("div", class_="divcontainer"):
        # Check for heat heading
        h3 = container.find("h3")
        if h3:
            text = h3.get_text(strip=True)
            if re.match(r"Heat\s+\d+", text):
                current_heat = text
                continue

        # Look for rider data in left and right divs
        for div_class in ["divleft", "divright"]:
            div = container.find("div", class_=div_class)
            if not div:
                continue

            # Find rider name from <p> element
            p = div.find("p")
            if not p:
                continue
            name_text = p.get_text(strip=True)
            if not name_text or " - " not in name_text:
                continue

            # Extract name: "212 - PITTARD Charlie" → "PITTARD Charlie"
            name = name_text.split(" - ", 1)[1]

            # Find the data table
            table = div.find("table", class_="table")
            if not table:
                continue

            rows: list[AuditRow] = []
            for tr in table.find_all("tr"):
                cells = tr.find_all("td")
                if not cells:
                    continue
                # Skip header rows (contain <h4>)
                if cells[0].find("h4"):
                    continue
                if len(cells) >= 7:
                    rows.append(
                        {
                            "Dist": cells[0].get_text(strip=True),
                            "Time": cells[1].get_text(strip=True),
                            "Rank": cells[2].get_text(strip=True),
                            "Lap": cells[3].get_text(strip=True),
                            "Lap_Rank": cells[4].get_text(strip=True),
                            "Sect": cells[5].get_text(strip=True),
                            "Sect_Rank": cells[6].get_text(strip=True),
                        }
                    )

            if rows:
                riders.append(
                    {
                        "name": name,
                        "heat": current_heat,
                        "rows": rows,
                    }
                )

    return riders


def filter_rider_data(riders: list[AuditRider], racer_name: str) -> list[AuditRider]:
    """Filter rider data to match the given racer name using normalized matching."""
    target_tokens = normalize_rider_name(racer_name)
    return [r for r in riders if normalize_rider_name(r["name"]) == target_tokens]


def format_csv(rider_data: list[AuditRider], event_name: str) -> str:
    """Format rider data as CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)

    for rider in rider_data:
        for row in rider["rows"]:
            writer.writerow(
                [
                    rider["heat"],
                    row["Dist"],
                    row["Time"],
                    row["Rank"],
                    row["Lap"],
                    row["Lap_Rank"],
                    row["Sect"],
                    row["Sect_Rank"],
                ]
            )

    return output.getvalue()
