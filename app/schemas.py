"""Response and request bodies."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from core.db.models import (
    ActionStatus,
    ActionType,
    EventType,
    ExceptionRule,
    ExceptionStatus,
    HubType,
    Role,
    ServiceLevel,
    Severity,
    ShipmentStatus,
)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class Me(_Out):
    id: int
    username: str
    role: Role


class HubOut(_Out):
    code: str
    name: str
    city: str
    hub_type: HubType
    latitude: float
    longitude: float
    shipments_here: int
    open_exceptions: int


class ShipmentOut(_Out):
    id: int
    tracking_number: str
    service_level: ServiceLevel
    status: ShipmentStatus
    origin_hub: str
    destination_hub: str
    origin_pincode: str
    destination_pincode: str
    weight_grams: int
    booked_at: datetime
    promised_by: datetime
    last_event_at: datetime | None
    last_scan_hub: str | None
    delivered_at: datetime | None


class EventOut(_Out):
    event_type: EventType
    occurred_at: datetime
    hub: str | None
    reason: str | None


class ActionOut(_Out):
    id: int
    exception_id: int
    action_type: ActionType
    params: dict[str, str]
    rationale: str | None
    status: ActionStatus
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    executed_at: datetime | None
    result: dict[str, str | int] | None


class ExceptionOut(_Out):
    id: int
    shipment_id: int
    tracking_number: str
    rule: ExceptionRule
    severity: Severity
    status: ExceptionStatus
    hub: str | None
    details: dict[str, int | float | str]
    detected_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    pending_actions: int


class TriageOut(_Out):
    id: int
    cause: str
    confidence: float
    summary: str
    evidence: list[str]
    diagnoser: str
    created_at: datetime
    action_ids: list[int]


class ExceptionDetail(ExceptionOut):
    shipment: ShipmentOut
    timeline: list[EventOut]
    actions: list[ActionOut]
    allowed_actions: list[ActionType]
    triage: list[TriageOut]


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ProposeAction(BaseModel):
    action_type: ActionType
    params: dict[str, str] = {}
    rationale: str | None = Field(default=None, max_length=2000)


class Decision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class Resolve(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class AuditOut(_Out):
    id: int
    at: datetime
    actor: str
    event: str
    entity_type: str
    entity_id: int
    details: dict[str, str | int | None]
