"""Shipments, tracking events and the constraints that protect them. Needs Postgres."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.events import book_shipment, record_event
from core.db.models import EventType, Route, RouteMode, ServiceLevel, Shipment, ShipmentStatus, TrackingEvent

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def _shipment(hub_ids: dict[str, int], **overrides: object) -> Shipment:
    values: dict[str, object] = {
        "tracking_number": "CO0000000001",
        "service_level": ServiceLevel.STANDARD,
        "origin_hub_id": hub_ids["BLR-DC1"],
        "destination_hub_id": hub_ids["DEL-DC1"],
        "origin_pincode": "560034",
        "destination_pincode": "110001",
        "weight_grams": 1200,
        "booked_at": T0,
        "promised_by": T0 + timedelta(days=4),
        **overrides,
    }
    return Shipment(**values)


def _route(session: Session, origin: int, destination: int, mode: RouteMode = RouteMode.ROAD) -> int:
    return session.execute(
        select(Route.id).where(
            Route.origin_hub_id == origin, Route.destination_hub_id == destination, Route.mode == mode
        )
    ).scalar_one()


def test_booking_records_the_first_event(session: Session, hub_ids: dict[str, int]) -> None:
    s = book_shipment(session, _shipment(hub_ids))

    events = session.scalars(select(TrackingEvent).where(TrackingEvent.shipment_id == s.id)).all()
    assert [e.event_type for e in events] == [EventType.BOOKED]
    assert s.status is ShipmentStatus.BOOKED
    assert s.last_event_at == T0
    assert s.last_scan_hub_id == hub_ids["BLR-DC1"]


def test_full_journey_updates_the_projection(session: Session, hub_ids: dict[str, int]) -> None:
    blr_dc, blr_gw, del_gw, del_dc = (hub_ids[c] for c in ("BLR-DC1", "BLR-GW", "DEL-GW", "DEL-DC1"))
    s = book_shipment(session, _shipment(hub_ids))
    h = timedelta(hours=1)
    steps: list[tuple[EventType, datetime, int | None, int | None]] = [
        (EventType.PICKED_UP, T0 + 1 * h, blr_dc, None),
        (EventType.DEPARTED_HUB, T0 + 2 * h, blr_dc, _route(session, blr_dc, blr_gw)),
        (EventType.ARRIVED_AT_HUB, T0 + 4 * h, blr_gw, None),
        (EventType.DEPARTED_HUB, T0 + 6 * h, blr_gw, _route(session, blr_gw, del_gw, RouteMode.AIR)),
        (EventType.ARRIVED_AT_HUB, T0 + 12 * h, del_gw, None),
        (EventType.DEPARTED_HUB, T0 + 14 * h, del_gw, _route(session, del_gw, del_dc)),
        (EventType.ARRIVED_AT_HUB, T0 + 16 * h, del_dc, None),
        (EventType.OUT_FOR_DELIVERY, T0 + 18 * h, del_dc, None),
    ]
    for event_type, at, hub, route in steps:
        record_event(session, s.id, event_type, at, hub_id=hub, route_id=route)
    assert session.scalar(select(Shipment.status).where(Shipment.id == s.id)) is ShipmentStatus.OUT_FOR_DELIVERY

    record_event(session, s.id, EventType.DELIVERED, T0 + 20 * h)

    assert s.status is ShipmentStatus.DELIVERED
    assert s.delivered_at == T0 + 20 * h
    assert s.last_scan_hub_id == del_dc  # a delivery without a hub keeps the last scan
    count = session.scalars(select(TrackingEvent.id).where(TrackingEvent.shipment_id == s.id)).all()
    assert len(count) == 1 + len(steps) + 1


def test_late_event_is_stored_but_does_not_overwrite_newer_state(session: Session, hub_ids: dict[str, int]) -> None:
    s = book_shipment(session, _shipment(hub_ids))
    record_event(session, s.id, EventType.ARRIVED_AT_HUB, T0 + timedelta(hours=5), hub_id=hub_ids["BLR-GW"])

    late = record_event(session, s.id, EventType.PICKED_UP, T0 + timedelta(hours=1), hub_id=hub_ids["BLR-DC1"])

    assert late.id is not None
    assert s.status is ShipmentStatus.AT_HUB
    assert s.last_scan_hub_id == hub_ids["BLR-GW"]
    assert s.last_event_at == T0 + timedelta(hours=5)


def test_late_delivered_event_does_not_set_delivered_at(session: Session, hub_ids: dict[str, int]) -> None:
    s = book_shipment(session, _shipment(hub_ids))
    record_event(session, s.id, EventType.RTO_INITIATED, T0 + timedelta(days=2))

    record_event(session, s.id, EventType.DELIVERED, T0 + timedelta(days=1))

    assert s.status is ShipmentStatus.RETURNING
    assert s.delivered_at is None


def test_naive_timestamps_are_rejected(session: Session, hub_ids: dict[str, int]) -> None:
    s = book_shipment(session, _shipment(hub_ids))
    with pytest.raises(ValueError, match="timezone-aware"):
        record_event(session, s.id, EventType.PICKED_UP, datetime(2026, 11, 2, 10, 0))


def test_unknown_shipment(session: Session) -> None:
    with pytest.raises(LookupError):
        record_event(session, 999_999, EventType.PICKED_UP, T0)


@pytest.mark.parametrize(
    "statement",
    [
        lambda: update(TrackingEvent).values(reason="edited"),
        lambda: delete(TrackingEvent),
    ],
    ids=["update", "delete"],
)
def test_tracking_events_are_append_only(session: Session, hub_ids: dict[str, int], statement) -> None:
    book_shipment(session, _shipment(hub_ids))
    with pytest.raises(IntegrityError, match="append-only"), session.begin_nested():
        session.execute(statement())


@pytest.mark.parametrize(
    ("event_type", "hub", "route", "constraint"),
    [
        (EventType.ARRIVED_AT_HUB, None, None, "hub_scan_has_hub"),
        (EventType.DEPARTED_HUB, "BLR-DC1", None, "departure_has_route"),
    ],
)
def test_hub_scans_need_a_hub_and_departures_a_route(
    session: Session, hub_ids: dict[str, int], event_type: EventType, hub: str | None, route: None, constraint: str
) -> None:
    s = book_shipment(session, _shipment(hub_ids))
    with pytest.raises(IntegrityError, match=constraint), session.begin_nested():
        record_event(session, s.id, event_type, T0 + timedelta(hours=1), hub_id=hub_ids[hub] if hub else None)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"promised_by": T0 - timedelta(hours=1)}, "promise_after_booking"),
        ({"destination_pincode": "01234"}, "destination_pincode_format"),
        ({"weight_grams": 0}, "weight_positive"),
        ({"service_level": "overnight"}, "service_level"),
    ],
)
def test_shipment_constraints(
    session: Session, hub_ids: dict[str, int], overrides: dict[str, object], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint), session.begin_nested():
        session.add(_shipment(hub_ids, **overrides))
        session.flush()


def test_tracking_numbers_are_unique(session: Session, hub_ids: dict[str, int]) -> None:
    book_shipment(session, _shipment(hub_ids))
    with pytest.raises(IntegrityError, match="uq_shipments_tracking_number"), session.begin_nested():
        book_shipment(session, _shipment(hub_ids))
