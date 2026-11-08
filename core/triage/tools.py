"""Gather the evidence for triage: the shipment, its timeline, derived signals and hub load.

All of it is read as of the exception's last sighting (`last_seen_at`), so triage sees what an operator
would have seen then. sim_faults is never read here.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from core.actions.policy import ActionPolicy
from core.db.models import (
    EventType,
    ExceptionStatus,
    Hub,
    Route,
    Shipment,
    ShipmentException,
    TrackingEvent,
)
from core.simulator.network import Network
from core.triage.schema import Context, Event, Signals


def _hours(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 3600, 1)


def signals(timeline: list[Event], booked_at: datetime, as_of: datetime, planned: list[str], destination: str,
            transit_minutes: dict[tuple[str, str, str], int]) -> Signals:  # fmt: skip
    pickup = next((e.at for e in timeline if e.event_type == EventType.PICKED_UP), None)

    # Waits: from arriving at (or being picked up at) a hub until the next departure or delivery round, or now.
    longest, longest_hub, arrived_at, arrived_hub = 0.0, None, None, None
    for e in [*timeline, Event(event_type="now", at=as_of)]:
        leaving = e.event_type in (EventType.DEPARTED_HUB, EventType.OUT_FOR_DELIVERY, "now")
        if arrived_at is not None and leaving:
            wait = _hours(arrived_at, e.at)
            if wait > longest:
                longest, longest_hub = wait, arrived_hub
            arrived_at = None
        if e.event_type in (EventType.ARRIVED_AT_HUB, EventType.PICKED_UP):
            arrived_at, arrived_hub = e.at, e.hub
        elif e.event_type in (EventType.DELIVERED, EventType.RTO_INITIATED, EventType.RETURNED_TO_ORIGIN):
            arrived_at = None

    arrivals = [e.hub for e in timeline if e.event_type == EventType.ARRIVED_AT_HUB and e.hub]
    open_hours = open_planned = None
    last_move = next((e for e in reversed(timeline) if e.event_type in (EventType.DEPARTED_HUB,
                                                                          EventType.ARRIVED_AT_HUB)), None)  # fmt: skip
    if last_move and last_move.event_type == EventType.DEPARTED_HUB and last_move.route_to and last_move.hub:
        open_hours = _hours(last_move.at, as_of)
        planned_minutes = transit_minutes.get((last_move.hub, last_move.route_to, last_move.route_mode or ""))
        open_planned = round(planned_minutes / 60, 1) if planned_minutes else None

    return Signals(
        hours_to_pickup=_hours(booked_at, pickup or as_of),
        longest_wait_hours=longest,
        longest_wait_hub=longest_hub,
        longest_wait_at_destination=longest_hub == destination,
        failed_attempts=sum(e.event_type == EventType.DELIVERY_ATTEMPT_FAILED for e in timeline),
        off_path_hubs=sorted({h for h in arrivals if h not in planned}),
        revisited_hubs=sorted({h for h in arrivals if arrivals.count(h) > 1}),
        open_departure_hours=open_hours,
        open_departure_planned_hours=open_planned,
    )


def gather(session: Session, exception_id: int, policy: ActionPolicy) -> Context:
    exc = session.get(ShipmentException, exception_id)
    if exc is None:
        raise LookupError(f"exception {exception_id} not found")
    as_of = exc.last_seen_at
    s = session.get(Shipment, exc.shipment_id)
    assert s is not None
    codes: dict[int, str] = dict(session.execute(select(Hub.id, Hub.code)).all())

    route_to, route_from = aliased(Hub), aliased(Hub)
    rows = session.execute(
        select(TrackingEvent, route_to.code, Route.mode)
        .outerjoin(Route, Route.id == TrackingEvent.route_id)
        .outerjoin(route_to, route_to.id == Route.destination_hub_id)
        .where(TrackingEvent.shipment_id == s.id, TrackingEvent.occurred_at <= as_of)
        .order_by(TrackingEvent.occurred_at, TrackingEvent.id)
    ).all()
    timeline = [
        Event(
            event_type=e.event_type,
            at=e.occurred_at,
            hub=codes.get(e.hub_id) if e.hub_id else None,
            route_to=to,
            route_mode=mode,
            reason=e.reason,
        )
        for e, to, mode in rows
    ]

    network = Network.from_db(session)
    origin, destination = codes[s.origin_hub_id], codes[s.destination_hub_id]
    legs = network.service_path(origin, destination, s.service_level) or []
    planned = [origin, *(leg.destination for leg in legs)]
    transit = {
        (o, d, str(mode)): minutes
        for o, d, mode, minutes in session.execute(
            select(route_from.code, route_to.code, Route.mode, Route.transit_minutes)
            .join(route_from, route_from.id == Route.origin_hub_id)
            .join(route_to, route_to.id == Route.destination_hub_id)
        ).all()
    }

    at_hub = open_at_hub = None
    if exc.hub_id is not None:
        at_hub = session.scalar(
            select(func.count()).where(Shipment.last_scan_hub_id == exc.hub_id, Shipment.last_event_at <= as_of)
        )
        open_at_hub = session.scalar(
            select(func.count()).where(
                ShipmentException.hub_id == exc.hub_id,
                ShipmentException.status == ExceptionStatus.OPEN,
                ShipmentException.detected_at <= as_of,
            )
        )

    return Context(
        exception_id=exc.id,
        rule=exc.rule,
        severity=exc.severity,
        exception_hub=codes.get(exc.hub_id) if exc.hub_id else None,
        detected_at=exc.detected_at,
        as_of=as_of,
        tracking_number=s.tracking_number,
        service_level=s.service_level,
        origin_hub=origin,
        destination_hub=destination,
        booked_at=s.booked_at,
        promised_by=s.promised_by,
        planned_path=planned,
        timeline=timeline,
        signals=signals(timeline, s.booked_at, as_of, planned, destination, transit),
        shipments_at_hub=at_hub,
        open_exceptions_at_hub=open_at_hub,
        allowed_actions=list(policy.allowed_for(exc.rule)),
    )
