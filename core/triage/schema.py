"""What triage works from (Context) and what it must produce (Diagnosis)."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from core.db.models import ActionType, ExceptionRule


class Cause(StrEnum):
    """Root causes triage can name. The values match the simulator's fault types, so evals can score them."""

    PICKUP_DELAY = "pickup_delay"
    HUB_STUCK = "hub_stuck"
    MISSED_CONNECTION = "missed_connection"
    MISROUTE = "misroute"
    FAILED_DELIVERY = "failed_delivery"
    LOST = "lost"
    UNKNOWN = "unknown"


class Event(BaseModel):
    event_type: str
    at: datetime
    hub: str | None = None
    route_to: str | None = None
    route_mode: str | None = None
    reason: str | None = None


class Signals(BaseModel):
    """Measurements derived from the timeline, so neither diagnoser has to do date arithmetic."""

    hours_to_pickup: float | None = Field(description="booking to pickup, or to now if not picked up")
    longest_wait_hours: float = Field(description="longest time at one hub between arriving and leaving (or now)")
    longest_wait_hub: str | None
    longest_wait_at_destination: bool
    failed_attempts: int
    off_path_hubs: list[str] = Field(description="hubs visited that aren't on the planned path")
    revisited_hubs: list[str]
    open_departure_hours: float | None = Field(description="hours since departing, if no arrival since")
    open_departure_planned_hours: float | None


class Context(BaseModel):
    """Everything triage may see. Built from the database as of the exception's last sighting; no ground truth."""

    model_config = ConfigDict(frozen=True)

    exception_id: int
    rule: ExceptionRule
    severity: str
    exception_hub: str | None
    detected_at: datetime
    as_of: datetime
    tracking_number: str
    service_level: str
    origin_hub: str
    destination_hub: str
    booked_at: datetime
    promised_by: datetime
    planned_path: list[str]
    timeline: list[Event]
    signals: Signals
    shipments_at_hub: int | None = Field(description="shipments whose latest scan (as of now) is at the hub")
    open_exceptions_at_hub: int | None
    allowed_actions: list[ActionType]


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    params: dict[str, str] = {}
    rationale: str = Field(min_length=1, max_length=500)


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: Cause
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1, max_length=600)
    evidence: list[str] = Field(min_length=1, max_length=6)
    actions: list[ProposedAction] = Field(default=[], max_length=3)
