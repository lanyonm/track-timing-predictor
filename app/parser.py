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


def _parse_row(row: Tag) -> tuple[EventStatus, str | None, str | None, str | None]:
    """
    Return (status, result_url, start_list_url, audit_url) from a schedule table row.

    Status priority:
      btn-success (no disabled) -> COMPLETED  (result_url = that button's href)
      btn-primary (no disabled) -> UPCOMING   (start_list_url = that button's href)
      otherwise                 -> NOT_READY

    btn-info (no disabled) = audit URL, captured independently of status.
    All URLs are captured when present; completed events typically have all three.
    """
    buttons = row.find_all("a", class_="btn")
    result_url: str | None = None
    start_list_url: str | None = None
    audit_url: str | None = None

    for btn in buttons:
        classes = " ".join(btn.get("class", []))
        if "btn-success" in classes and "disabled" not in classes:
            result_url = btn.get("href")
        if "btn-primary" in classes and "disabled" not in classes:
            start_list_url = btn.get("href")
        if "btn-info" in classes and "disabled" not in classes:
            audit_url = btn.get("href")

    if result_url:
        return EventStatus.COMPLETED, result_url, start_list_url, audit_url
    if start_list_url:
        return EventStatus.UPCOMING, None, start_list_url, audit_url
    return EventStatus.NOT_READY, None, None, audit_url


def parse_heat_count(html: str) -> int | None:
    """
    Count the number of heats in a start list page.

    Each sequential time slot is labeled 'Heat N' in the page text.
    Returns None if no heats are found (e.g., page unavailable or format changed).
    """
    heats = re.findall(r"\bHeat\s+\d+\b", html)
    return len(heats) if heats else None


def parse_finish_time(html: str) -> float | None:
    """
    Extract 'Finish Time: MM:SS' from a result page and return the duration
    in minutes, or None if the field is not present.
    """
    match = re.search(r"Finish Time:\s*(\d+):(\d{2})", html)
    if not match:
        return None
    return int(match.group(1)) + int(match.group(2)) / 60.0


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
            status, result_url, start_list_url, audit_url = _parse_row(row)

            events.append(TrackEvent(
                position=position,
                name=name,
                discipline=discipline,
                status=status,
                is_special=is_special,
                result_url=result_url,
                start_list_url=start_list_url,
                audit_url=audit_url,
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
