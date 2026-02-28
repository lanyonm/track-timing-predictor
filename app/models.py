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


class SessionPrediction(BaseModel):
    session: Session
    event_predictions: list[Prediction]
    observed_delay_minutes: float


class SchedulePrediction(BaseModel):
    event_id: int
    sessions: list[SessionPrediction]
