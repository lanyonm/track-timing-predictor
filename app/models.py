from __future__ import annotations

import unicodedata
from datetime import datetime, time
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DurationSource = Literal["finish_time", "generated_diff", "heat_count"]


class EventCategory(BaseModel):
    """Structured decomposition of an event name into component dimensions."""
    model_config = ConfigDict(frozen=True)

    discipline: str = Field(min_length=1)
    classification: str | None = None
    gender: Literal["men", "women", "open"] = "open"
    round: str | None = None
    ride_number: int | None = Field(default=None, ge=1)
    omnium_part: int | None = Field(default=None, ge=1)


class EventStatus(str, Enum):
    NOT_READY = "not_ready"
    UPCOMING = "upcoming"
    COMPLETED = "completed"


class Event(BaseModel):
    position: int  # 0-based sequence index within the session; not a finishing position
    name: str
    discipline: str
    status: EventStatus
    is_special: bool
    result_url: str | None = None
    start_list_url: str | None = None
    audit_url: str | None = None
    live_url: str | None = None


class Session(BaseModel):
    session_id: int
    day: str
    scheduled_start: time
    events: list[Event]


def normalize_rider_name(raw_name: str) -> frozenset[str]:
    """Normalize a rider name to a frozenset of lowercase ASCII tokens.

    Applies Unicode NFKD decomposition, strips non-ASCII characters,
    removes apostrophes/hyphens/periods, then splits on whitespace.
    """
    normalized = unicodedata.normalize("NFKD", raw_name).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.replace("'", "").replace("-", "").replace(".", "")
    return frozenset(t.lower() for t in normalized.split())


class RiderEntry(BaseModel):
    name: str
    heat: int = Field(ge=1)
    normalized_tokens: frozenset[str] = frozenset()
    team_name: str | None = None

    @model_validator(mode="after")
    def _compute_tokens(self) -> "RiderEntry":
        if not self.normalized_tokens:
            tokens = normalize_rider_name(self.name)
            object.__setattr__(self, "normalized_tokens", tokens)
        return self


class RiderMatch(BaseModel):
    heat: int = Field(ge=1)
    heat_count: int = Field(ge=1)
    heat_predicted_start: datetime | None = None
    team_name: str | None = None


class Prediction(BaseModel):
    event: Event
    predicted_start: time
    estimated_duration_minutes: float
    is_adjusted: bool
    cumulative_delay_minutes: float
    is_observed: bool = False    # True when duration comes from a result-page Finish Time
    heat_count: int | None = None  # Set when duration is derived from start-list heat count
    is_active: bool = False      # True for the first non-COMPLETED event in an in-progress session
    active_heat: int | None = None  # Estimated current heat (1-based) for an active multi-heat event
    rider_match: RiderMatch | None = None


class SessionPrediction(BaseModel):
    session: Session
    event_predictions: list[Prediction]
    observed_delay_minutes: float
    has_racer_match: bool = False
    has_pending_racer_match: bool = False
    events_without_start_lists: int = 0

    @property
    def is_complete(self) -> bool:
        """True when every non-special event in the session is completed.

        Special events (Break, End of Session, Medal Ceremonies) are excluded
        because they often never transition to COMPLETED in the source data.
        """
        races = [e for e in self.session.events if not e.is_special]
        return bool(races) and all(e.status == EventStatus.COMPLETED for e in races)


class NextRace(BaseModel):
    event_name: str
    heat: int = Field(ge=1)
    heat_count: int = Field(ge=1)
    predicted_start: datetime | None = None
    is_active: bool = False


class SchedulePrediction(BaseModel):
    competition_id: int
    sessions: list[SessionPrediction]
    racer_name: str | None = None
    match_count: int = 0
    events_without_start_lists: int = 0
    total_events: int = 0
    next_race: NextRace | None = None


# ---------------------------------------------------------------------------
# Palmares models
# ---------------------------------------------------------------------------

class PalmaresEntry(BaseModel):
    """A single timed event in a racer's palmares."""
    racer_name: str
    competition_id: int
    competition_name: str
    competition_date: str | None = None
    session_id: int
    session_name: str
    event_position: int
    event_name: str
    team_name: str | None = None  # Team name for team events (team pursuit, team sprint)
    audit_url: str


class PalmaresCompetition(BaseModel):
    """Groups palmares entries by competition for template rendering."""
    competition_id: int
    competition_name: str
    competition_date: str | None = None
    entries: list[PalmaresEntry]


# ---------------------------------------------------------------------------
# Duration data import models
# ---------------------------------------------------------------------------

class DurationRecord(BaseModel):
    """A single observation of how long an event took."""
    category: EventCategory
    event_name: str
    heat_count: int | None = Field(default=None, ge=1)
    duration_minutes: float = Field(gt=0)
    per_heat_duration_minutes: float | None = Field(default=None, gt=0)
    duration_source: DurationSource
    competition_id: int = Field(gt=0)
    session_id: int = Field(ge=1)
    event_position: int = Field(ge=0)

    @model_validator(mode="after")
    def _heat_count_required_for_heat_source(self) -> "DurationRecord":
        if self.duration_source == "heat_count" and self.heat_count is None:
            raise ValueError("heat_count must be set when duration_source is 'heat_count'")
        if self.per_heat_duration_minutes is not None and self.heat_count is None:
            raise ValueError("per_heat_duration_minutes requires heat_count to be set")
        return self


class UncategorizedEntry(BaseModel):
    """Summary of an event name that couldn't be fully categorized."""
    event_name: str
    partial_category: EventCategory
    unresolved_text: str = Field(min_length=1)
    frequency: int = Field(ge=1)
    avg_duration_minutes: float | None = Field(default=None, gt=0)
    has_heats: bool


class CompetitionMeta(BaseModel):
    """Metadata for a competition."""
    competition_id: int = Field(gt=0)
    name: str | None = None
    url: str = Field(min_length=1)


class EventReport(BaseModel):
    """Per-event data in a competition report."""
    position: int = Field(ge=0)
    name: str
    category: EventCategory
    status: EventStatus
    is_special: bool
    heat_count: int | None = Field(default=None, ge=1)
    duration_minutes: float | None = Field(default=None, gt=0)
    duration_source: DurationSource | None = None

    @model_validator(mode="after")
    def _duration_fields_co_present(self) -> "EventReport":
        has_minutes = self.duration_minutes is not None
        has_source = self.duration_source is not None
        if has_minutes != has_source:
            raise ValueError("duration_minutes and duration_source must both be set or both be None")
        return self


class SessionReport(BaseModel):
    """Per-session data in a competition report."""
    session_id: int = Field(ge=1)
    day: str = Field(min_length=1)
    scheduled_start: str = Field(pattern=r"^\d{2}:\d{2}$")
    events: list[EventReport]


class CompetitionReport(BaseModel):
    """Top-level JSON output file structure."""
    version: Literal["1.0"] = "1.0"
    extracted_at: datetime
    competition: CompetitionMeta
    sessions: list[SessionReport]
    duration_observations: list[DurationRecord]
    uncategorized_summary: list[UncategorizedEntry]
