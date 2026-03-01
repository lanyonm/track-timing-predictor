from __future__ import annotations

from datetime import time
from enum import Enum

from pydantic import BaseModel


class EventStatus(str, Enum):
    NOT_READY = "not_ready"
    UPCOMING = "upcoming"
    COMPLETED = "completed"


class TrackEvent(BaseModel):
    position: int
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
    events: list[TrackEvent]


class Prediction(BaseModel):
    event: TrackEvent
    predicted_start: time
    estimated_duration_minutes: float
    is_adjusted: bool
    cumulative_delay_minutes: float
    is_observed: bool = False    # True when duration comes from a result-page Finish Time
    heat_count: int | None = None  # Set when duration is derived from start-list heat count
    is_active: bool = False      # True for the first non-COMPLETED event in an in-progress session
    active_heat: int | None = None  # Estimated current heat (1-based) for an active multi-heat event


class SessionPrediction(BaseModel):
    session: Session
    event_predictions: list[Prediction]
    observed_delay_minutes: float

    @property
    def is_complete(self) -> bool:
        """True when every non-special event in the session is completed.

        Special events (Break, End of Session, Medal Ceremonies) are excluded
        because they often never transition to COMPLETED in the source data.
        """
        races = [e for e in self.session.events if not e.is_special]
        return bool(races) and all(e.status == EventStatus.COMPLETED for e in races)


class SchedulePrediction(BaseModel):
    event_id: int
    sessions: list[SessionPrediction]
