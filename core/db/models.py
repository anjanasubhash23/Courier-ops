"""SQLAlchemy models. Alembic autogenerates migrations from Base.metadata.

`tracking_events` is the append-only source of truth for what happened to a shipment.
The status columns on `shipments` are a projection of it, kept current by `core.db.events.record_event`.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names, so Alembic migrations can drop and alter them reliably.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class HubType(StrEnum):
    GATEWAY = "gateway"  # sort center that also handles air linehaul
    SORT_CENTER = "sort_center"
    DELIVERY_CENTER = "delivery_center"  # last mile; serves pincode prefixes


class RouteMode(StrEnum):
    ROAD = "road"
    AIR = "air"


class ServiceLevel(StrEnum):
    STANDARD = "standard"
    EXPRESS = "express"


class ShipmentStatus(StrEnum):
    BOOKED = "booked"
    PICKED_UP = "picked_up"
    AT_HUB = "at_hub"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERED = "delivered"
    RETURNING = "returning"
    RETURNED = "returned"


class EventType(StrEnum):
    BOOKED = "booked"
    PICKED_UP = "picked_up"
    ARRIVED_AT_HUB = "arrived_at_hub"
    DEPARTED_HUB = "departed_hub"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERY_ATTEMPT_FAILED = "delivery_attempt_failed"
    DELIVERED = "delivered"
    RTO_INITIATED = "rto_initiated"  # return to origin
    RETURNED_TO_ORIGIN = "returned_to_origin"


STATUS_AFTER_EVENT: dict[EventType, ShipmentStatus] = {
    EventType.BOOKED: ShipmentStatus.BOOKED,
    EventType.PICKED_UP: ShipmentStatus.PICKED_UP,
    EventType.ARRIVED_AT_HUB: ShipmentStatus.AT_HUB,
    EventType.DEPARTED_HUB: ShipmentStatus.IN_TRANSIT,
    EventType.OUT_FOR_DELIVERY: ShipmentStatus.OUT_FOR_DELIVERY,
    EventType.DELIVERY_ATTEMPT_FAILED: ShipmentStatus.DELIVERY_FAILED,
    EventType.DELIVERED: ShipmentStatus.DELIVERED,
    EventType.RTO_INITIATED: ShipmentStatus.RETURNING,
    EventType.RETURNED_TO_ORIGIN: ShipmentStatus.RETURNED,
}


class FaultType(StrEnum):
    PICKUP_DELAY = "pickup_delay"
    HUB_STUCK = "hub_stuck"
    MISSED_CONNECTION = "missed_connection"
    MISROUTE = "misroute"
    FAILED_DELIVERY = "failed_delivery"
    LOST = "lost"


class ExceptionRule(StrEnum):
    PICKUP_OVERDUE = "pickup_overdue"
    STUCK_AT_HUB = "stuck_at_hub"
    STUCK_AT_DELIVERY_CENTER = "stuck_at_delivery_center"
    IN_TRANSIT_OVERDUE = "in_transit_overdue"
    DELIVERY_FAILED = "delivery_failed"
    MISROUTED = "misrouted"
    SLA_BREACH = "sla_breach"


class ExceptionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Role(StrEnum):
    """In increasing order of what they may do."""

    VIEWER = "viewer"  # read everything except the audit log, ask questions
    OPERATOR = "operator"  # propose actions, resolve exceptions, run triage
    SUPERVISOR = "supervisor"  # approve or reject actions
    ADMIN = "admin"  # run the detector, read the audit log

    @property
    def rank(self) -> int:
        return list(Role).index(self)


class ActionType(StrEnum):
    PRIORITY_PICKUP = "priority_pickup"
    ESCALATE_TO_HUB = "escalate_to_hub"
    REROUTE = "reroute"
    OPEN_TRACE = "open_trace"
    RESCHEDULE_DELIVERY = "reschedule_delivery"
    NOTIFY_CUSTOMER = "notify_customer"
    INITIATE_RTO = "initiate_rto"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


PINCODE_PATTERN = "^[1-9][0-9]{5}$"


def _enum(enum: type[StrEnum], name: str) -> Enum:
    # VARCHAR + CHECK instead of a native Postgres enum: adding a value later is a simple migration.
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
    )


def _timestamp() -> DateTime:
    return DateTime(timezone=True)


class Hub(Base):
    __tablename__ = "hubs"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    city: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(64))
    hub_type: Mapped[HubType] = mapped_column(_enum(HubType, "hub_type"))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(server_default=true())
    created_at: Mapped[datetime] = mapped_column(_timestamp(), server_default=func.now())


class HubServiceArea(Base):
    """Which delivery center serves a pincode. Match the longest prefix of a 6-digit pincode."""

    __tablename__ = "hub_service_areas"
    __table_args__ = (CheckConstraint("pincode_prefix ~ '^[1-9][0-9]{0,5}$'", name="pincode_prefix_format"),)

    pincode_prefix: Mapped[str] = mapped_column(String(6), primary_key=True)
    hub_id: Mapped[int] = mapped_column(ForeignKey("hubs.id"), index=True)


class Route(Base):
    """A scheduled linehaul or feeder leg between two hubs, one direction."""

    __tablename__ = "routes"
    __table_args__ = (
        UniqueConstraint("origin_hub_id", "destination_hub_id", "mode", name="uq_routes_origin_destination_mode"),
        CheckConstraint("origin_hub_id <> destination_hub_id", name="distinct_hubs"),
        CheckConstraint("distance_km > 0", name="distance_positive"),
        CheckConstraint("transit_minutes > 0", name="transit_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    origin_hub_id: Mapped[int] = mapped_column(ForeignKey("hubs.id"))
    destination_hub_id: Mapped[int] = mapped_column(ForeignKey("hubs.id"), index=True)
    mode: Mapped[RouteMode] = mapped_column(_enum(RouteMode, "route_mode"))
    distance_km: Mapped[int] = mapped_column(Integer)
    transit_minutes: Mapped[int] = mapped_column(Integer)  # planned door-to-door time for the leg
    is_active: Mapped[bool] = mapped_column(server_default=true())


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint(f"origin_pincode ~ '{PINCODE_PATTERN}'", name="origin_pincode_format"),
        CheckConstraint(f"destination_pincode ~ '{PINCODE_PATTERN}'", name="destination_pincode_format"),
        CheckConstraint("weight_grams > 0", name="weight_positive"),
        CheckConstraint("promised_by > booked_at", name="promise_after_booking"),
        # For "what's at this hub now"; only live shipments, so it stays small (eval/db/results.md).
        Index(
            "ix_shipments_live_last_scan_hub_id",
            "last_scan_hub_id",
            postgresql_where=text("status NOT IN ('delivered', 'returned')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tracking_number: Mapped[str] = mapped_column(String(20), unique=True)
    service_level: Mapped[ServiceLevel] = mapped_column(_enum(ServiceLevel, "service_level"))
    origin_hub_id: Mapped[int] = mapped_column(ForeignKey("hubs.id"))
    destination_hub_id: Mapped[int] = mapped_column(ForeignKey("hubs.id"))
    origin_pincode: Mapped[str] = mapped_column(String(6))
    destination_pincode: Mapped[str] = mapped_column(String(6))
    weight_grams: Mapped[int] = mapped_column(Integer)
    booked_at: Mapped[datetime] = mapped_column(_timestamp())
    promised_by: Mapped[datetime] = mapped_column(_timestamp())

    # Projection of tracking_events; written only by record_event.
    status: Mapped[ShipmentStatus] = mapped_column(
        _enum(ShipmentStatus, "shipment_status"), server_default=ShipmentStatus.BOOKED.value
    )
    last_event_at: Mapped[datetime | None] = mapped_column(_timestamp())
    last_scan_hub_id: Mapped[int | None] = mapped_column(ForeignKey("hubs.id"))
    delivered_at: Mapped[datetime | None] = mapped_column(_timestamp())

    created_at: Mapped[datetime] = mapped_column(_timestamp(), server_default=func.now())


class TrackingEvent(Base):
    """Append-only: a database trigger rejects UPDATE and DELETE."""

    __tablename__ = "tracking_events"
    __table_args__ = (
        Index("ix_tracking_events_shipment_id_occurred_at", "shipment_id", "occurred_at"),
        Index("ix_tracking_events_occurred_at", "occurred_at"),  # the simulated clock and time windows
        CheckConstraint(
            "event_type NOT IN ('arrived_at_hub', 'departed_hub') OR hub_id IS NOT NULL", name="hub_scan_has_hub"
        ),
        CheckConstraint("event_type <> 'departed_hub' OR route_id IS NOT NULL", name="departure_has_route"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    event_type: Mapped[EventType] = mapped_column(_enum(EventType, "event_type"))
    hub_id: Mapped[int | None] = mapped_column(ForeignKey("hubs.id"))
    route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id"))
    reason: Mapped[str | None] = mapped_column(String(64))  # e.g. "customer_unavailable" on a failed attempt
    occurred_at: Mapped[datetime] = mapped_column(_timestamp())  # simulated (business) time
    recorded_at: Mapped[datetime] = mapped_column(_timestamp(), server_default=func.now())  # wall-clock insert time


class SimFault(Base):
    """Ground truth: a fault the simulator injected into a shipment's journey.

    Used to score the detector and triage agent, so the agent and Text-to-SQL database roles must never
    be able to read it.
    """

    __tablename__ = "sim_faults"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    fault_type: Mapped[FaultType] = mapped_column(_enum(FaultType, "fault_type"))
    hub_id: Mapped[int | None] = mapped_column(ForeignKey("hubs.id"))
    route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id"))
    injected_at: Mapped[datetime] = mapped_column(_timestamp())  # simulated time the fault began
    details: Mapped[dict[str, int | str]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class ShipmentException(Base):
    """A problem the detector found with a shipment. At most one open exception per shipment and rule."""

    __tablename__ = "exceptions"
    __table_args__ = (
        Index(
            "uq_exceptions_open_shipment_rule",
            "shipment_id",
            "rule",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_exceptions_status_rule", "status", "rule"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    rule: Mapped[ExceptionRule] = mapped_column(_enum(ExceptionRule, "exception_rule"))
    severity: Mapped[Severity] = mapped_column(_enum(Severity, "severity"))
    status: Mapped[ExceptionStatus] = mapped_column(
        _enum(ExceptionStatus, "exception_status"), server_default=ExceptionStatus.OPEN.value
    )
    hub_id: Mapped[int | None] = mapped_column(ForeignKey("hubs.id"))  # where the shipment was when detected
    details: Mapped[dict[str, int | float | str]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Simulated (business) time, like tracking_events.occurred_at.
    detected_at: Mapped[datetime] = mapped_column(_timestamp())
    last_seen_at: Mapped[datetime] = mapped_column(_timestamp())
    resolved_at: Mapped[datetime | None] = mapped_column(_timestamp())
    resolved_by: Mapped[str | None] = mapped_column(String(64))  # "detector" when the condition cleared, else a user


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(_enum(Role, "role"))
    is_active: Mapped[bool] = mapped_column(server_default=true())
    created_at: Mapped[datetime] = mapped_column(_timestamp(), server_default=func.now())


class Action(Base):
    """A corrective action for an exception. Nothing happens until a second person approves it.

    `proposed_by` is a username, or `agent:<name>` when the triage agent proposed it.
    """

    __tablename__ = "actions"
    __table_args__ = (
        # One pending proposal per exception and action type.
        Index(
            "uq_actions_pending_exception_type",
            "exception_id",
            "action_type",
            unique=True,
            postgresql_where=text("status = 'proposed'"),
        ),
        Index("ix_actions_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    exception_id: Mapped[int] = mapped_column(ForeignKey("exceptions.id"), index=True)
    action_type: Mapped[ActionType] = mapped_column(_enum(ActionType, "action_type"))
    params: Mapped[dict[str, str]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    rationale: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[ActionStatus] = mapped_column(
        _enum(ActionStatus, "action_status"), server_default=ActionStatus.PROPOSED.value
    )
    proposed_by: Mapped[str] = mapped_column(String(64))
    proposed_at: Mapped[datetime] = mapped_column(_timestamp(), server_default=func.now())
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(_timestamp())
    decision_note: Mapped[str | None] = mapped_column(String(2000))
    executed_at: Mapped[datetime | None] = mapped_column(_timestamp())
    result: Mapped[dict[str, str | int] | None] = mapped_column(JSONB)


class AuditEntry(Base):
    """Who did what, when. Append-only: a database trigger rejects UPDATE and DELETE."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(_timestamp(), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(64))
    event: Mapped[str] = mapped_column(String(64))  # e.g. "action.approved"
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int] = mapped_column(BigInteger)
    details: Mapped[dict[str, str | int | None]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class TriageRun(Base):
    """One diagnosis of an exception, by the triage agent or the rule-based fallback."""

    __tablename__ = "triage_runs"
    __table_args__ = (CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    exception_id: Mapped[int] = mapped_column(ForeignKey("exceptions.id"), index=True)
    cause: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(String(2000))
    evidence: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    diagnoser: Mapped[str] = mapped_column(String(64))  # "rules", or "llm:<provider>/<model>"
    action_ids: Mapped[list[int]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(_timestamp(), server_default=func.now())
