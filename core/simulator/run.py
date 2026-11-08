"""Run the simulator over a date range and write the result to the database."""

import random
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session, aliased

from core.db.events import apply_event
from core.db.models import Hub, Route, Shipment, SimFault, TrackingEvent
from core.simulator.config import SimulatorConfig
from core.simulator.demand import Booking, bookings_for_day
from core.simulator.faults import SimFault as InjectedFault
from core.simulator.faults import choose_fault
from core.simulator.movement import SimEvent, plan_journey
from core.simulator.network import Network, RouteKey


@dataclass(frozen=True)
class SimShipment:
    booking: Booking
    events: list[SimEvent]
    fault: InjectedFault | None


def default_as_of(start: date, days: int, cfg: SimulatorConfig) -> datetime:
    """Midnight (local) after the last simulated day."""
    return datetime.combine(start + timedelta(days=days), time(), cfg.tz)


def simulate(
    network: Network, cfg: SimulatorConfig, start: date, days: int, as_of: datetime | None = None
) -> list[SimShipment]:
    """Bookings made on `days` local days from `start`, with every event up to `as_of`.

    Pure and deterministic: the same network, config and arguments always give the same result.
    Events after `as_of` haven't happened yet, so recent shipments are still in flight. A fault is
    only recorded once it has begun by `as_of`.
    """
    as_of = as_of or default_as_of(start, days, cfg)
    shipments = []
    for offset in range(days):
        for booking in bookings_for_day(start + timedelta(days=offset), network, cfg):
            if booking.booked_at > as_of:
                continue
            rng = random.Random(f"{cfg.seed}:journey:{booking.tracking_number}")
            events, fault = plan_journey(booking, network, cfg, rng, choose_fault(rng, cfg))
            visible = [e for e in events if e.occurred_at <= as_of]
            shipments.append(SimShipment(booking, visible, fault if fault and fault.injected_at <= as_of else None))
    return shipments


def write(session: Session, shipments: list[SimShipment]) -> dict[str, int]:
    """Bulk-insert shipments, events and faults. The shipment projection uses the same rule as record_event."""
    hub_ids: dict[str, int] = dict(session.execute(select(Hub.code, Hub.id)).all())
    origin, destination = aliased(Hub), aliased(Hub)
    route_ids: dict[RouteKey, int] = {
        (o, d, mode): rid
        for o, d, mode, rid in session.execute(
            select(origin.code, destination.code, Route.mode, Route.id)
            .join(origin, Route.origin_hub_id == origin.id)
            .join(destination, Route.destination_hub_id == destination.id)
        ).all()
    }

    def hub_id(code: str | None) -> int | None:
        return hub_ids[code] if code else None

    def route_id(key: RouteKey | None) -> int | None:
        return route_ids[key] if key else None

    rows = []
    for s in shipments:
        b = s.booking
        row = Shipment(
            tracking_number=b.tracking_number,
            service_level=b.service_level,
            origin_hub_id=hub_ids[b.origin_hub],
            destination_hub_id=hub_ids[b.destination_hub],
            origin_pincode=b.origin_pincode,
            destination_pincode=b.destination_pincode,
            weight_grams=b.weight_grams,
            booked_at=b.booked_at,
            promised_by=b.promised_by,
        )
        for e in s.events:
            apply_event(row, e.event_type, e.occurred_at, hub_id(e.hub))
        rows.append(row)
    session.add_all(rows)
    session.flush()  # assigns ids

    events = [
        {
            "shipment_id": row.id,
            "event_type": e.event_type,
            "hub_id": hub_id(e.hub),
            "route_id": route_id(e.route),
            "reason": e.reason,
            "occurred_at": e.occurred_at,
        }
        for row, s in zip(rows, shipments, strict=True)
        for e in s.events
    ]
    faults = [
        {
            "shipment_id": row.id,
            "fault_type": s.fault.fault_type,
            "hub_id": hub_id(s.fault.hub),
            "route_id": route_id(s.fault.route),
            "injected_at": s.fault.injected_at,
            "details": s.fault.details,
        }
        for row, s in zip(rows, shipments, strict=True)
        if s.fault
    ]
    if events:
        session.execute(insert(TrackingEvent), events)
    if faults:
        session.execute(insert(SimFault), faults)

    by_type = Counter(s.fault.fault_type.value for s in shipments if s.fault)
    return {"shipments": len(rows), "events": len(events), "faults": len(faults), **dict(sorted(by_type.items()))}


def reset(session: Session) -> None:
    """Delete all shipments and everything that refers to them (events, faults, exceptions...). Keeps the network."""
    session.execute(text("TRUNCATE shipments RESTART IDENTITY CASCADE"))


def backfill(
    session: Session,
    cfg: SimulatorConfig,
    start: date,
    days: int,
    as_of: datetime | None = None,
    *,
    replace: bool = False,
) -> dict[str, int]:
    """Simulate and write a date range. Refuses to add to existing shipments unless `replace` is set."""
    if replace:
        reset(session)
    elif session.scalar(select(func.count()).select_from(Shipment)):
        raise RuntimeError("shipments already exist; pass replace=True (--reset) to delete them first")
    network = Network.from_db(session)
    if not network.delivery_centers:
        raise RuntimeError("no active delivery centers; run scripts/seed_hubs.py first")
    return write(session, simulate(network, cfg, start, days, as_of))
