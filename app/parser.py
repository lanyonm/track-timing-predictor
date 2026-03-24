import logging
import re
from datetime import datetime, time

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

from app.disciplines import detect_discipline, SPECIAL_EVENT_NAMES
from app.models import Event, EventStatus, RiderEntry, Session, normalize_rider_name


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
        classes = " ".join(btn.get("class", []))
        if "btn-success" in classes and "disabled" not in classes:
            result_url = btn.get("href")
        if "btn-primary" in classes and "disabled" not in classes:
            start_list_url = btn.get("href")
        if "btn-info" in classes and "disabled" not in classes:
            audit_url = btn.get("href")
        if "btn-danger" in classes and "disabled" not in classes:
            live_url = btn.get("href")

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
    if (not text
            or re.match(r"^Heat\s+\d+$", text)
            or re.match(r"^\d+$", text)
            or text == "\xa0"
            or re.match(r"^Number of Riders", text)):
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

    Returns an empty list if no riders are found.
    """
    soup = BeautifulSoup(html, "html.parser")
    riders: list[RiderEntry] = []
    current_heat = 0

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue

        # Check if this row contains a "Heat N" label
        row_text = row.get_text(" ", strip=True)
        heat_match = re.search(r"\bHeat\s+(\d+)\b", row_text)

        if heat_match:
            current_heat = int(heat_match.group(1))

            # Extract names from h4 tags in this row (handles both
            # sprint qualifying single-rider and team multi-rider formats)
            for h4 in row.find_all("h4"):
                for name, team in _extract_names_from_h4(h4):
                    if _is_rider_name(name):
                        tokens = normalize_rider_name(name)
                        riders.append(RiderEntry(
                            name=name, heat=current_heat,
                            normalized_tokens=tokens, team_name=team,
                        ))
        else:
            # Rider row: either within a multi-rider heat (current_heat > 0)
            # or a bunch race with no heat labels (current_heat == 0 → heat 1)
            heat = current_heat or 1
            for h4 in row.find_all("h4"):
                for name, team in _extract_names_from_h4(h4):
                    if _is_rider_name(name):
                        tokens = normalize_rider_name(name)
                        riders.append(RiderEntry(
                            name=name, heat=heat,
                            normalized_tokens=tokens, team_name=team,
                        ))

    if not riders and soup.find("tr"):
        logger.warning("parse_start_list_riders found 0 riders in HTML with %d rows", len(soup.find_all("tr")))

    return riders


def parse_heat_count(html: str) -> int | None:
    """
    Count the number of heats in a start list page.

    Each sequential time slot is labeled 'Heat N' in the page text.
    Returns None if no heats are found (e.g., page unavailable or format changed).
    """
    heats = re.findall(r"\bHeat\s+\d+\b", html)
    return len(heats) if heats else None


def parse_live_heat(html: str) -> int | None:
    """
    Count the number of completed heats on a live results page.

    The caller uses this count as the number of *finished* heats; the active
    heat is then count + 1. Returns None if no completed heats are found
    (caller falls back to time-based estimation).

    Two page formats are handled:

    Team event format (team sprint, team pursuit) — an explicit header names
    the running heat:
        "Riders On Track for Heat N of M"
      → returns N - 1 (heats before the current one are done).
      → returns None when N == 1 (nothing completed yet).

    Keirin / per-heat format — separate "Heat N" sections:
      → counts sections that contain a non-zero timing value (e.g. 12.345).
      → the "0.000 km/h" placeholder on upcoming/active heats is excluded
        because it starts with 0.
    """
    # Team event format (team sprint, team pursuit): the page explicitly says
    # which heat is on track.
    match = re.search(r"Riders\s+On\s+Track\s+for\s+Heat\s+(\d+)", html, re.IGNORECASE)
    if match:
        active = int(match.group(1))
        return active - 1 if active > 1 else None

    # Keirin / per-heat format: split on "Heat N" labels and count sections
    # that contain actual timing values (non-zero integer part).
    sections = re.split(r"\bHeat\s+\d+\b", html)
    count = sum(1 for section in sections[1:] if re.search(r"\b[1-9]\d*\.\d{2,}", section))
    return count if count > 0 else None


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
            session_id = int(session_id_str)
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

            events.append(Event(
                position=position,
                name=name,
                discipline=discipline,
                status=status,
                is_special=is_special,
                result_url=result_url,
                start_list_url=start_list_url,
                audit_url=audit_url,
                live_url=live_url,
            ))

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

        sessions.append(Session(
            session_id=session_id,
            day=day,
            scheduled_start=scheduled_start,
            events=events,
        ))

    return sessions
