from __future__ import annotations

from datetime import time
from enum import Enum

from pydantic import BaseModel, ConfigDict


class EventStatus(str, Enum):
    NOT_READY = "not_ready"
    UPCOMING = "upcoming"
    COMPLETED = "completed"


class RiderEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    heat: int
    normalized_tokens: frozenset[str]


class RiderMatch(BaseModel):
    heat: int
    heat_count: int
    heat_predicted_start: time | None = None


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

    @property
    def is_complete(self) -> bool:
        """True when every non-special event in the session is completed.

        Special events (Break, End of Session, Medal Ceremonies) are excluded
        because they often never transition to COMPLETED in the source data.
        """
        races = [e for e in self.session.events if not e.is_special]
        return bool(races) and all(e.status == EventStatus.COMPLETED for e in races)


class SchedulePrediction(BaseModel):
    competition_id: int
    sessions: list[SessionPrediction]
    racer_name: str | None = None
    match_count: int = 0
    events_without_start_lists: int = 0
    total_events: int = 0
