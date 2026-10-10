import logging
import re
from datetime import datetime, time
from typing import Literal, NamedTuple

from bs4 import BeautifulSoup, Tag

from app.disciplines import (
    SPECIAL_EVENT_NAMES,
    detect_discipline,
    pursuit_discipline_from_band,
    pursuit_discipline_from_urls,
)
from app.models import Event, EventStatus, RiderEntry, RiderListEntry, Session, normalize_rider_name

logger = logging.getLogger(__name__)


def _extract_section_html(jxn_data: dict, section_id: str) -> str:
    """
    Pull the innerHTML string for a named section from a Jaxon response.

    Handles two response formats:
    1. Top-level: cmd="as", id="scheduleview" (some responses)
    2. Nested: cmd="as", id="dynarea" with a <div id="scheduleview"> inside (live API)
    """
    # Try top-level first
    for obj in jxn_data.get("jxnobj", []):
        if obj.get("cmd") == "as" and obj.get("id") == section_id:
            return obj["data"]

    # Fall back to searching inside dynarea
    for obj in jxn_data.get("jxnobj", []):
        if obj.get("cmd") == "as" and obj.get("id") == "dynarea":
            soup = BeautifulSoup(obj["data"], "html.parser")
            div = soup.find("div", id=section_id)
            if div:
                return str(div)

    raise ValueError(f"Section '{section_id}' not found in Jaxon response")


def _parse_time(time_str: str) -> time:
    h, m = time_str.strip().split(":")
    return time(int(h), int(m))


def _parse_summary(text: str) -> tuple[str, time]:
    """
    Parse 'Schedule - Friday - 08:15' into ('Friday', time(8, 15)).
    Raises ValueError if the format does not match.
    """
    match = re.match(r"Schedule\s*-\s*(.+?)\s*-\s*(\d{1,2}:\d{2})", text.strip())
    if not match:
        raise ValueError(f"Unexpected summary format: {text!r}")
    return match.group(1), _parse_time(match.group(2))


def _parse_row(row: Tag) -> tuple[EventStatus, str | None, str | None, str | None, str | None]:
    """
    Return (status, result_url, start_list_url, audit_url, live_url) from a schedule row.

    Status priority:
      btn-success (no disabled) -> COMPLETED  (result_url = that button's href)
      btn-primary (no disabled) -> UPCOMING   (start_list_url = that button's href)
      otherwise                 -> NOT_READY

    btn-info (no disabled) = audit URL, captured independently of status.
    btn-danger (no disabled) = live timing URL for the currently active event.
    All URLs are captured when present; completed events typically have all three.
    """
    buttons = row.find_all("a", class_="btn")
    result_url: str | None = None
    start_list_url: str | None = None
    audit_url: str | None = None
    live_url: str | None = None

    for btn in buttons:
        classes = " ".join(btn.get_attribute_list("class"))
        href = btn.get("href")
        href = href if isinstance(href, str) else None
        if "btn-success" in classes and "disabled" not in classes:
            result_url = href
        if "btn-primary" in classes and "disabled" not in classes:
            start_list_url = href
        if "btn-info" in classes and "disabled" not in classes:
            audit_url = href
        if "btn-danger" in classes and "disabled" not in classes:
            live_url = href

    if result_url:
        return EventStatus.COMPLETED, result_url, start_list_url, audit_url, live_url
    if start_list_url:
        return EventStatus.UPCOMING, None, start_list_url, audit_url, live_url
    return EventStatus.NOT_READY, None, None, audit_url, live_url


def _is_rider_name(text: str) -> bool:
    """Check if text looks like a rider name (e.g. 'LASTNAME Firstname').

    The first whitespace-separated token must contain at least 2 uppercase letters
    (to distinguish real names from incidental text like 'No riders').
    The full text must have at least two tokens.
    """
    parts = text.split()
    if len(parts) < 2:
        return False
    first_token = parts[0]
    uppercase_count = sum(1 for c in first_token if c.isupper())
    return uppercase_count >= 2


def _extract_names_from_h4(h4) -> list[tuple[str, str | None]]:
    """Extract candidate rider names from an <h4> element.

    Team event start lists pack team name + riders into a single <h4>
    separated by <br/> tags::

        <h4>TEAM NAME<br/>95 BAYZAEE Aram<br/>72 BONDY Jacob</h4>

    When <br/> tags are present, the first segment is the team name and
    subsequent segments are ``{bib} {LASTNAME} {Firstname}``. Returns
    ``(name, team_name)`` tuples — riders get the team name, the team
    name segment itself gets ``None``.

    For non-team <h4> elements (no <br/> tags), returns the plain text
    with ``team_name=None`` as a single-element list.
    """
    if h4.find("br"):
        segments = []
        for part in h4.stripped_strings:
            part = part.strip()
            if part:
                segments.append(part)
        if not segments:
            return []
        team = segments[0]
        results: list[tuple[str, str | None]] = [(team, None)]
        for seg in segments[1:]:
            # Strip leading bib number: "95 BAYZAEE Aram" → "BAYZAEE Aram"
            stripped = re.sub(r"^\d+\s+", "", seg)
            if stripped:
                results.append((stripped, team))
        return results

    text = h4.get_text(strip=True)
    if (
        not text
        or re.match(r"^Heat\s+\d+$", text)
        or re.match(r"^\d+$", text)
        or text == "\xa0"
        or re.match(r"^Number of Riders", text)
    ):
        return []
    return [(text, None)]


def parse_start_list_riders(html: str) -> list[RiderEntry]:
    """
    Parse rider names and heat assignments from a start list page.

    Start list pages are HTML tables with four layout patterns:

    Sprint qualifying (1 rider per heat):
      <td><h4>Heat 1</h4></td><td><h4><Strong>212</Strong></h4></td><td><h4>NAME</h4></td>

    Multi-rider heats (keirin, etc.):
      Heat header row: <td colspan="6"><h4><Strong>Heat 1</Strong></h4></td>
      Rider rows:      <td><h4><Strong>14</Strong></h4></td><td>...</td><td><h4>NAME</h4></td>

    Bunch races (scratch, points, elimination, tempo, madison):
      No "Heat N" labels — all riders listed together.
      Rider rows:      <td><h4><Strong>101</Strong></h4></td><td>...</td><td><h4>NAME</h4></td>
      These riders are assigned heat=1.

    Team events (team pursuit, team sprint):
      Heat header row as above, then a single <h4> with <br/>-separated content:
      <h4>TEAM NAME<br/>95 RIDER1<br/>72 RIDER2<br/>65 RIDER3</h4>
      Individual rider names are extracted alongside the team name.

    Medal finals label their heats "Final 3-4"/"Final 1-2" (sprints) or
    "For Bronze"/"For Gold" (pursuits, team events) instead of "Heat N"; each
    label starts the next heat, so bronze is heat 1 and gold heat 2.

    Pursuit, time trial and team start lists carry an <h4> "First rider (team)
    listed starts on the home straight": a heat's first row gets
    straight="home" and its second "back". Without it, straight is None.

    Returns an empty list if no riders are found.
    """
    return _start_list_riders(BeautifulSoup(html, "html.parser"))


# Medal finals label their two heats with <h4> headings instead of "Heat N" (see parse_heat_count).
_FINAL_HEAT_LABEL_RE = re.compile(r"^(?:Final\s+\d+-\d+|For\s+(?:Bronze|Gold))$", re.IGNORECASE)
_HOME_STRAIGHT_RE = re.compile(r"First\s+(?:rider|team)\s+listed\s+starts\s+on\s+the\s+home\s+straight", re.IGNORECASE)


def _heat_label(row: Tag, current_heat: int) -> int | None:
    """The heat a row starts, or None if it starts none: "Heat N" gives N, and a medal
    final's "Final 3-4"/"Final 1-2" or "For Bronze"/"For Gold" heading the next heat."""
    heat_match = re.search(r"\bHeat\s+(\d+)\b", row.get_text(" ", strip=True))
    if heat_match:
        return int(heat_match.group(1))
    if any(_FINAL_HEAT_LABEL_RE.match(h4.get_text(" ", strip=True)) for h4 in row.find_all("h4")):
        return current_heat + 1
    return None


def _start_list_riders(soup: BeautifulSoup) -> list[RiderEntry]:
    riders: list[RiderEntry] = []
    current_heat = 0
    order = 0  # rider (or team) rows so far in the current heat
    home_first = any(_HOME_STRAIGHT_RE.search(h4.get_text(" ", strip=True)) for h4 in soup.find_all("h4"))

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue

        heat = _heat_label(row, current_heat)
        if heat is not None:
            current_heat = heat
            order = 0

        # A labelled row can carry its heat's first rider (sprint qualifying, pursuit finals)
        # or team. Rows without a label belong to the current heat, or heat 1 in a bunch race
        # (no labels at all).
        row_riders = [
            (name, team)
            for h4 in row.find_all("h4")
            for name, team in _extract_names_from_h4(h4)
            if _is_rider_name(name)
        ]
        if not row_riders:
            continue
        order += 1
        straight: Literal["home", "back"] | None = None
        if home_first and order <= 2:
            straight = "home" if order == 1 else "back"
        for name, team in row_riders:
            riders.append(
                RiderEntry(
                    name=name,
                    heat=current_heat or 1,
                    normalized_tokens=normalize_rider_name(name),
                    team_name=team,
                    straight=straight,
                )
            )

    if not riders and soup.find("tr"):
        logger.warning("parse_start_list_riders found 0 riders in HTML with %d rows", len(soup.find_all("tr")))

    return riders


# Inline base64 flag images make up ~95% of a Rider List page's bytes.
_DATA_IMG_RE = re.compile(r'<img[^>]*src="data:[^"]*"[^>]*>')


def parse_rider_list_url(jxn_data: dict) -> str | None:
    """Return the href of the "Rider List" row in the top-level ``documents`` jxnobj, or None.

    The Event Documents block appears in both initial-layout and refresh responses.
    The row is matched on its label because other documents (Medal Standings,
    communiqués) share the table and refresh responses prepend a live-results banner.
    """
    try:
        html = _extract_section_html(jxn_data, "documents")
    except ValueError:
        return None
    for row in BeautifulSoup(html, "html.parser").find_all("tr"):
        h4 = row.find("h4")
        link = row.find("a", href=True)
        if h4 and link and h4.get_text(strip=True) == "Rider List":
            return str(link["href"])
    return None


def parse_rider_list(html: str) -> list[RiderListEntry]:
    """
    Parse a competition's Rider List page into entries.

    Each ``tbody`` row has cells: 0 bib, 1 name, 2 category, 3 team, 4 flag image,
    5 nation code, 6 whitespace-separated event codes. Rows with fewer than seven
    cells or an empty name or category are skipped. Returns an empty list if no rows match.
    """
    soup = BeautifulSoup(_DATA_IMG_RE.sub("", html), "html.parser")
    entries: list[RiderListEntry] = []
    for row in soup.select("tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        name = cells[1].get_text(" ", strip=True)
        category = cells[2].get_text(strip=True)
        if not name or not category:
            continue
        entries.append(RiderListEntry(name=name, category=category, codes=frozenset(cells[6].get_text().split())))

    if not entries and soup.find("tr"):
        logger.warning("parse_rider_list found 0 riders in HTML with %d rows", len(soup.find_all("tr")))

    return entries


def parse_start_list_categories(html: str) -> frozenset[str]:
    """
    Distinct values of a start list's Category column.

    Only combined-age races carry the column (e.g. 50+ Women Points Race lists
    W5054 … W7074), and each category gets its own podium. Returns an empty set
    when there is no Category column.
    """
    return _start_list_categories(BeautifulSoup(html, "html.parser"))


def _start_list_categories(soup: BeautifulSoup) -> frozenset[str]:
    categories: set[str] = set()
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if "Category" not in headers:
            continue
        col = headers.index("Category")
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) == len(headers) and (value := cells[col].get_text(strip=True)):
                categories.add(value)
    return frozenset(categories)


_RACE_DISTANCE_RE = re.compile(r"-\s*(\d+(?:\.\d+)?)\s*km\s*-\s*\d+(?:\.\d+)?\s*Laps\b", re.IGNORECASE)


def parse_race_distance_km(html: str) -> float | None:
    """
    Race distance in km from a start list's title, e.g. 'Points Race Final - 10km - 40 Laps'.

    Some titles add a suffix ('- Sprint Every 5 Laps'). Distances in metres (sprints,
    time trials) return None.
    """
    return _race_distance_km(BeautifulSoup(html, "html.parser"))


def _race_distance_km(soup: BeautifulSoup) -> float | None:
    for h3 in soup.find_all("h3"):
        if m := _RACE_DISTANCE_RE.search(h3.get_text(" ", strip=True)):
            return float(m.group(1))
    return None


_SPRINT_PAIR_RE = re.compile(r"^(?:Heat\s+\d+|Final\s+\d+-\d+)$")


def _sprint_pairs(html: str, byes: bool = False) -> list[tuple[int, list[int]]] | None:
    """(rides timed, wins per rider) for each pair on a best-of-3 sprint round's page.

    All rides of a round share one result page, and its live timing page has the same
    table: Ride 1, Ride 2 and Decider columns. Each pair has a header row ('Heat N', or
    'Final 3-4'/'Final 1-2' on a Final) whose last three cells hold each ride's 200m
    time, then one row per rider whose last three cells hold 'Winner' or a gap (a
    relegated rider shows 'REL'). A bye is a pair with one rider (its Ride 1 time is
    0.000); it rides no more, so it's left out unless ``byes``. None for any page
    without a Decider column.
    """
    soup = BeautifulSoup(html, "html.parser")
    headers = [th.get_text(strip=True) for th in soup.find_all("th")]
    if "Decider" not in headers:
        return None
    tbody = soup.find("tbody")
    if tbody is None:
        return None

    pairs: list[tuple[int, list[int]]] = []
    for row in tbody.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        rides = cells[-3:]
        if _SPRINT_PAIR_RE.match(cells[0].get_text(" ", strip=True)):
            pairs.append((sum(1 for c in rides if re.search(r"\b[1-9]\d*\.\d{2,}", c.get_text())), []))
        elif pairs:
            pairs[-1][1].append(sum(1 for c in rides if c.get_text(strip=True) == "Winner"))
    return [(timed, wins) for timed, wins in pairs if byes or len(wins) >= 2]


def parse_live_sprint_heat(html: str, ride: int) -> int | None:
    """Finished heats of one ride on a best-of-3 sprint round's live timing page.

    The page shows every ride's column, so only this ride's times count. Byes count as
    finished in Ride 1 and Ride 2; the decider (Ride 3) counts only pairs that rode it.
    None for a page without a Decider column.
    """
    pairs = _sprint_pairs(html, byes=True)
    if pairs is None:
        return None
    bye_count = sum(1 for _, wins in pairs if len(wins) < 2)
    ridden = sum(1 for timed, wins in pairs if len(wins) >= 2 and timed >= ride)
    return ridden if ride >= 3 else ridden + bye_count


def _needs_decider(timed: int, wins: list[int]) -> bool:
    return timed >= 3 or wins == [1, 1]


def parse_sprint_decider_range(html: str) -> tuple[int, int] | None:
    """(pairs known to need a decider, pairs yet to ride Ride 2) on a best-of-3 round's page.

    A pair that has ridden Ride 2 needs a decider when it rode one or each rider won
    once; one still to ride Ride 2 may or may not. Works on the result page and on the
    live timing page, which updates heat by heat during Ride 2. None for any page
    without a Decider column.
    """
    pairs = _sprint_pairs(html)
    if pairs is None:
        return None
    known = sum(1 for timed, wins in pairs if timed >= 2 and _needs_decider(timed, wins))
    return known, sum(1 for timed, _ in pairs if timed < 2)


def parse_sprint_deciders(html: str) -> int | None:
    """
    Count the pairs in a best-of-3 sprint round that need (or rode) a decider.

    Returns None until every pair has ridden Ride 2, or for any other page.
    """
    pairs = _sprint_pairs(html)
    if not pairs or any(timed < 2 for timed, _ in pairs):
        return None
    return sum(1 for timed, wins in pairs if _needs_decider(timed, wins))


def parse_sprint_rides_done(html: str) -> int | None:
    """
    How many rides (0-3) of a best-of-3 sprint round every pair has finished.

    Ride 1 and Ride 2 are done when every pair has that ride's time; Ride 3 (the
    decider) when, after Ride 2, no pair is still level at one win each. The rides
    share this result page, so upstream shows Ride 2 and Ride 3 as having results
    as soon as Ride 1 does. None for any page without a Decider column.
    """
    pairs = _sprint_pairs(html)
    if pairs is None:
        return None
    if not pairs:
        return 3
    done = min(timed for timed, _ in pairs)
    if done >= 2 and not any(timed < 3 and wins == [1, 1] for timed, wins in pairs):
        return 3
    return min(done, 2)


def parse_heat_count(html: str) -> int | None:
    """
    Count the number of heats in a start list page.

    Each sequential time slot is labeled 'Heat N' in the page text. Medal
    finals label theirs with '<h4>' headings instead: 'Final 3-4' and
    'Final 1-2' for sprints, 'For Bronze' and 'For Gold' for pursuits, team
    pursuit and team sprint. Only headings count, since keirin pages mention
    'Final 1-6' in qualification-rule prose.
    Returns None if no heats are found (e.g., page unavailable or format changed).
    """
    heats = re.findall(r"\bHeat\s+\d+\b", html)
    heats += re.findall(r"<h4>(?:<strong>)?\s*(?:Final\s+\d+-\d+|For\s+(?:Bronze|Gold))\b", html, re.IGNORECASE)
    return len(heats) if heats else None


class StartList(NamedTuple):
    heat_count: int | None
    riders: list[RiderEntry]
    categories: frozenset[str]
    race_distance_km: float | None


def parse_start_list(html: str) -> StartList:
    """
    Everything the app reads from a start list page, from a single parse.

    Equivalent to calling parse_heat_count, parse_start_list_riders,
    parse_start_list_categories and parse_race_distance_km, which would each
    parse the page again.
    """
    soup = BeautifulSoup(html, "html.parser")
    return StartList(
        heat_count=parse_heat_count(html),
        riders=_start_list_riders(soup),
        categories=_start_list_categories(soup),
        race_distance_km=_race_distance_km(soup),
    )


def parse_live_results_html(jxn_data: dict) -> str:
    """Return the live results HTML (the ``dynarea`` jxnobj) from fetch_live_results, or "" if absent."""
    for obj in jxn_data.get("jxnobj", []):
        if obj.get("cmd") == "as" and obj.get("id") == "dynarea":
            return str(obj.get("data", ""))
    return ""


def live_results_show_event(html: str, event_name: str) -> bool:
    """True when a live results page's text names the event (its title reads e.g.
    "65-69 Men Pursuit Qualifying - 2km - 8 Laps"), so a page still showing the
    previous event isn't read as this one's heats."""
    text = re.sub(r"(?:&nbsp;|\s)+", " ", re.sub(r"<[^>]+>", " ", html)).lower()
    return " ".join(event_name.split()).lower() in text


def parse_live_heat(html: str) -> int | None:
    """
    Count the number of completed heats on a live results page.

    The caller uses this count as the number of *finished* heats; the active
    heat is then count + 1. Returns None when the page shows no heats at all
    (caller falls back to time-based estimation), and 0 when it shows heats
    but none has finished.

    Two page formats are handled:

    Pursuit and team event format — an explicit header names the running heat:
        "Riders On Track for Heat N of M"
      → returns N - 1 (heats before the current one are done).

    Sprint / keirin / per-heat format — separate "Heat N" sections:
      → counts sections that contain a non-zero timing value (e.g. 12.345)
        or a "Winner" (a sprint bye has one, timed 0.000).
      → the "0.000 km/h" placeholder on upcoming/active heats is excluded
        because it starts with 0.
    """
    match = re.search(r"Riders\s+On\s+Track\s+for\s+Heat\s+(\d+)", html, re.IGNORECASE)
    if match:
        return max(0, int(match.group(1)) - 1)

    # Per-heat format: split on "Heat N" labels and count sections
    # that contain actual timing values (non-zero integer part).
    sections = re.split(r"\bHeat\s+\d+\b", html)
    if len(sections) < 2:
        return None
    return sum(1 for section in sections[1:] if re.search(r"\b[1-9]\d*\.\d{2,}|\bWinner\b", section))


def parse_finish_time(html: str) -> float | None:
    """
    Extract 'Finish Time: MM:SS' from a result page and return the duration
    in minutes, or None if the field is not present.
    """
    match = re.search(r"Finish Time:\s*(\d+):(\d{2})", html)
    if not match:
        return None
    return int(match.group(1)) + int(match.group(2)) / 60.0


def parse_generated_time(html: str) -> datetime | None:
    """
    Extract 'Generated: YYYY-MM-DD HH:MM:SS' from a result page footer and
    return it as a datetime, or None if the line is absent or malformed.

    This timestamp is present on every result page type (bunch races, time
    trials, sprint qualifying, pursuit) and represents approximately when
    the result was published — i.e. when the last result was entered for
    that event.  Consecutive Generated timestamps can be differenced to
    derive actual inter-event slot durations for all disciplines.
    """
    match = re.search(r"Generated:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", html)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        logger.info("Could not parse generated time from matched string: %s", match.group(1))
        return None


def parse_schedule(jxn_data: dict) -> list[Session]:
    """
    Parse the Jaxon response into a list of Session objects,
    each containing an ordered list of Event objects.
    """
    html = _extract_section_html(jxn_data, "scheduleview")
    soup = BeautifulSoup(html, "html.parser")
    sessions: list[Session] = []

    for details in soup.find_all("details"):
        summary_tag = details.find("summary")
        if not summary_tag:
            continue

        try:
            day, scheduled_start = _parse_summary(summary_tag.get_text())
        except ValueError:
            logger.warning("Could not parse schedule summary: %s", summary_tag.get_text())
            continue  # skip non-schedule sessions (e.g. event documents)

        session_id_str = details.get("id", "0")
        try:
            session_id = int(str(session_id_str))
        except (ValueError, TypeError):
            session_id = 0

        events: list[Event] = []
        for position, row in enumerate(details.find_all("tr")):
            h4 = row.find("h4")
            if not h4:
                continue

            name = h4.get_text(strip=True)
            is_special = name.lower() in SPECIAL_EVENT_NAMES
            discipline = detect_discipline(name)
            status, result_url, start_list_url, audit_url, live_url = _parse_row(row)
            if discipline.startswith("pursuit_"):
                discipline = (
                    pursuit_discipline_from_urls(
                        result_url,
                        start_list_url,
                        audit_url,
                        live_url,
                    )
                    or pursuit_discipline_from_band(name)
                    or discipline
                )

            events.append(
                Event(
                    position=position,
                    name=name,
                    discipline=discipline,
                    status=status,
                    is_special=is_special,
                    result_url=result_url,
                    start_list_url=start_list_url,
                    audit_url=audit_url,
                    live_url=live_url,
                )
            )

        # Special events (e.g. Medal Ceremonies) publish their result page
        # incrementally while still in progress. Don't consider one COMPLETED
        # until the event immediately following it has started.
        for i, event in enumerate(events):
            if (
                event.is_special
                and event.status == EventStatus.COMPLETED
                and i + 1 < len(events)
                and events[i + 1].status != EventStatus.COMPLETED
            ):
                events[i] = event.model_copy(update={"status": EventStatus.UPCOMING})

        sessions.append(
            Session(
                session_id=session_id,
                day=day,
                scheduled_start=scheduled_start,
                events=events,
            )
        )

    return sessions
