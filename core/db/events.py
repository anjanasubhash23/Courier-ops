"""The write path for shipments: every state change goes through a tracking event."""

from datetime import datetime

from sqlalchemy.orm import Session

from core.db.models import STATUS_AFTER_EVENT, EventType, Shipment, TrackingEvent


def book_shipment(session: Session, shipment: Shipment) -> Shipment:
    """Insert a new shipment together with its `booked` event, so every shipment has a timeline."""
    session.add(shipment)
    session.flush()  # assigns shipment.id
    record_event(session, shipment.id, EventType.BOOKED, shipment.booked_at, hub_id=shipment.origin_hub_id)
    return shipment


def record_event(
    session: Session,
    shipment_id: int,
    event_type: EventType,
    occurred_at: datetime,
    *,
    hub_id: int | None = None,
    route_id: int | None = None,
    reason: str | None = None,
) -> TrackingEvent:
    """Append an event and update the shipment's current state in the same transaction."""
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")

    # Row lock: concurrent events for one shipment are applied one at a time.
    shipment = session.get(Shipment, shipment_id, with_for_update=True)
    if shipment is None:
        raise LookupError(f"shipment {shipment_id} does not exist")

    event = TrackingEvent(
        shipment_id=shipment_id,
        event_type=event_type,
        hub_id=hub_id,
        route_id=route_id,
        reason=reason,
        occurred_at=occurred_at,
    )
    session.add(event)
    apply_event(shipment, event_type, occurred_at, hub_id)
    session.flush()
    return event


def apply_event(shipment: Shipment, event_type: EventType, occurred_at: datetime, hub_id: int | None) -> None:
    """Update the shipment's projected state for one event. The only place the projection rule lives.

    Events can arrive late (a delayed scan upload). A late event is stored but doesn't overwrite
    the state set by a newer one, so the projection always reflects the latest `occurred_at`.
    """
    if shipment.last_event_at is not None and occurred_at < shipment.last_event_at:
        return
    shipment.status = STATUS_AFTER_EVENT[event_type]
    shipment.last_event_at = occurred_at
    if hub_id is not None:
        shipment.last_scan_hub_id = hub_id
    if event_type is EventType.DELIVERED:
        shipment.delivered_at = occurred_at
