import logging
import re
import unicodedata
from datetime import datetime, time

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

from app.disciplines import detect_discipline, SPECIAL_EVENT_NAMES
from app.models import Event, EventStatus, RiderEntry, Session


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


def parse_heat_count(html: str) -> int | None:
    """
    Count the number of heats in a start list page.

    Each sequential time slot is labeled 'Heat N' in the page text.
    Returns None if no heats are found (e.g., page unavailable or format changed).
    """
    heats = re.findall(r"\bHeat\s+\d+\b", html)
    return len(heats) if heats else None


def _normalize_name(name: str) -> frozenset[str]:
    """Normalize a rider name to a frozenset of lowercase ASCII tokens."""
    # Unicode NFKD decomposition, strip non-ASCII
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    # Remove apostrophes, hyphens, periods
    name = name.replace("'", "").replace("-", "").replace(".", "")
    return frozenset(token.lower() for token in name.split() if token)


def parse_start_list_riders(html: str) -> list[RiderEntry]:
    """
    Parse start list HTML and extract rider entries with heat assignments.

    Each heat section begins with a 'Heat N' header, followed by rider entries.
    The HTML may contain structured tags (tables, h4 elements) or plain text.
    We strip HTML tags first to get clean text, then split on Heat headers.
    Returns empty list if no heats/riders found.
    """
    # Strip HTML tags to get plain text, preserving whitespace between elements
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")

    # Split on Heat N headers, keeping the heat number
    parts = re.split(r"\bHeat\s+(\d+)\b", text)
    # parts[0] is before first Heat header; then alternating: heat_num, section_text
    riders: list[RiderEntry] = []
    for i in range(1, len(parts), 2):
        heat = int(parts[i])
        section = parts[i + 1] if i + 1 < len(parts) else ""
        for line in section.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # Match rider lines: optional bib number (digits) followed by
            # LASTNAME Firstname (at least one uppercase letter)
            m = re.match(r"(?:\d+\s{2,})?(.*)", line)
            if m:
                name_part = m.group(1).strip()
                if not name_part:
                    continue
                # Skip lines that look like non-rider text (no uppercase letter)
                if not any(c.isupper() for c in name_part):
                    continue
                # Extract rider name: "LASTNAME Firstname" pattern.
                # Rider names have at least one mixed-case word (the first name);
                # team/club names are typically ALL CAPS.
                name_match = re.match(r"([A-ZÀ-Ý][A-Za-zÀ-ÿ'-]+(?:\s+[A-Za-zÀ-ÿ'-]+){1,3})", name_part)
                if name_match:
                    rider_name = name_match.group(1).strip()
                    words = rider_name.split()
                    # Require at least 2 words and at least one non-all-caps word
                    if len(words) >= 2 and any(not w.isupper() for w in words):
                        riders.append(RiderEntry(
                            name=rider_name,
                            heat=heat,
                            normalized_tokens=_normalize_name(rider_name),
                        ))
    return riders


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
