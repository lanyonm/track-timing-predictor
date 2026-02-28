import re
from datetime import time

from bs4 import BeautifulSoup, Tag

from app.disciplines import detect_discipline, SPECIAL_EVENT_NAMES
from app.models import EventStatus, Session, TrackEvent


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
    match = re.match(r"Schedule\s*-\s*(\w+)\s*-\s*(\d{1,2}:\d{2})", text.strip())
    if not match:
        raise ValueError(f"Unexpected summary format: {text!r}")
    return match.group(1), _parse_time(match.group(2))


def _determine_status(row: Tag) -> EventStatus:
    """
    Derive event status from button elements in a table row.

    Priority:
      btn-success (no disabled) -> COMPLETED
      btn-primary (no disabled) -> UPCOMING
      otherwise                 -> NOT_READY
    """
    buttons = row.find_all("a", class_="btn")
    classes_list = [" ".join(b.get("class", [])) for b in buttons]

    has_active_success = any(
        "btn-success" in c and "disabled" not in c for c in classes_list
    )
    has_active_primary = any(
        "btn-primary" in c and "disabled" not in c for c in classes_list
    )

    if has_active_success:
        return EventStatus.COMPLETED
    if has_active_primary:
        return EventStatus.UPCOMING
    return EventStatus.NOT_READY


def parse_schedule(jxn_data: dict) -> list[Session]:
    """
    Parse the Jaxon response into a list of Session objects,
    each containing an ordered list of TrackEvent objects.
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
            continue  # skip non-schedule sessions (e.g. event documents)

        session_id_str = details.get("id", "0")
        try:
            session_id = int(session_id_str)
        except (ValueError, TypeError):
            session_id = 0

        events: list[TrackEvent] = []
        for position, row in enumerate(details.find_all("tr")):
            h4 = row.find("h4")
            if not h4:
                continue

            name = h4.get_text(strip=True)
            is_special = name.lower() in SPECIAL_EVENT_NAMES
            discipline = detect_discipline(name)
            status = _determine_status(row)

            events.append(TrackEvent(
                position=position,
                name=name,
                discipline=discipline,
                status=status,
                is_special=is_special,
            ))

        sessions.append(Session(
            session_id=session_id,
            day=day,
            scheduled_start=scheduled_start,
            events=events,
        ))

    return sessions
