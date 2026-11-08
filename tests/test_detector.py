"""Exception rules and the detector's open/refresh/resolve cycle. Needs Postgres."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db.events import book_shipment, record_event
from core.db.models import (
    EventType,
    ExceptionRule,
    ExceptionStatus,
    Route,
    RouteMode,
    ServiceLevel,
    Shipment,
    ShipmentException,
)
from core.detector.rules import load_rules
from core.detector.run import replay_times, run_detector
from tests.conftest import ROOT

pytestmark = pytest.mark.postgres

RULES = load_rules(ROOT / "config" / "rules.yaml")
T0 = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)  # 09:30 IST
H = timedelta(hours=1)


def _book(session: Session, hub_ids: dict[str, int], n: int = 1, origin: str = "BLR-DC1", dest: str = "DEL-DC1") -> int:
    shipment = Shipment(
        tracking_number=f"CO{n:010d}",
        service_level=ServiceLevel.STANDARD,
        origin_hub_id=hub_ids[origin],
        destination_hub_id=hub_ids[dest],
        origin_pincode="560034",
        destination_pincode="110001",
        weight_grams=800,
        booked_at=T0,
        promised_by=T0 + timedelta(days=5),
    )
    return book_shipment(session, shipment).id


def _route(session: Session, hub_ids: dict[str, int], origin: str, dest: str) -> int:
    return session.execute(
        select(Route.id).where(
            Route.origin_hub_id == hub_ids[origin],
            Route.destination_hub_id == hub_ids[dest],
            Route.mode == RouteMode.ROAD,
        )
    ).scalar_one()


def _open(session: Session) -> dict[int, set[ExceptionRule]]:
    rows = session.execute(
        select(ShipmentException.shipment_id, ShipmentException.rule).where(
            ShipmentException.status == ExceptionStatus.OPEN
        )
    ).all()
    found: dict[int, set[ExceptionRule]] = {}
    for shipment_id, rule in rows:
        found.setdefault(shipment_id, set()).add(rule)
    return found


def _detect(session: Session, as_of: datetime) -> dict[int, set[ExceptionRule]]:
    run_detector(session, as_of, RULES)
    return _open(session)


def test_healthy_shipment_raises_nothing(session: Session, hub_ids: dict[str, int]) -> None:
    s = _book(session, hub_ids)
    record_event(session, s, EventType.PICKED_UP, T0 + 2 * H, hub_id=hub_ids["BLR-DC1"])
    assert _detect(session, T0 + 4 * H) == {}


def test_pickup_overdue(session: Session, hub_ids: dict[str, int]) -> None:
    s = _book(session, hub_ids)
    assert _detect(session, T0 + 17 * H) == {}
    assert _detect(session, T0 + 19 * H) == {s: {ExceptionRule.PICKUP_OVERDUE}}


def test_stuck_at_hub_then_resolved_when_it_moves(session: Session, hub_ids: dict[str, int]) -> None:
    s = _book(session, hub_ids)
    record_event(session, s, EventType.PICKED_UP, T0 + 1 * H, hub_id=hub_ids["BLR-DC1"])
    assert _detect(session, T0 + 14 * H) == {s: {ExceptionRule.STUCK_AT_HUB}}

    record_event(
        session,
        s,
        EventType.DEPARTED_HUB,
        T0 + 15 * H,
        hub_id=hub_ids["BLR-DC1"],
        route_id=_route(session, hub_ids, "BLR-DC1", "BLR-GW"),
    )
    assert _detect(session, T0 + 16 * H) == {}
    exc = session.scalars(select(ShipmentException)).one()
    assert exc.status is ExceptionStatus.RESOLVED
    assert exc.resolved_by == "detector"
    assert exc.resolved_at == T0 + 16 * H
    assert exc.detected_at == T0 + 14 * H


def test_waiting_at_the_destination_has_a_longer_threshold(session: Session, hub_ids: dict[str, int]) -> None:
    s = _book(session, hub_ids, origin="BLR-DC1", dest="BLR-DC1")
    record_event(session, s, EventType.PICKED_UP, T0 + 1 * H, hub_id=hub_ids["BLR-DC1"])
    assert _detect(session, T0 + 25 * H) == {}
    assert _detect(session, T0 + 32 * H) == {s: {ExceptionRule.STUCK_AT_DELIVERY_CENTER}}


def test_in_transit_overdue(session: Session, hub_ids: dict[str, int]) -> None:
    s = _book(session, hub_ids)
    route = _route(session, hub_ids, "BLR-DC1", "BLR-GW")  # 120 min planned -> overdue after 240 min
    record_event(session, s, EventType.PICKED_UP, T0 + 1 * H, hub_id=hub_ids["BLR-DC1"])
    record_event(session, s, EventType.DEPARTED_HUB, T0 + 2 * H, hub_id=hub_ids["BLR-DC1"], route_id=route)
    assert _detect(session, T0 + 5 * H) == {}
    assert _detect(session, T0 + 7 * H) == {s: {ExceptionRule.IN_TRANSIT_OVERDUE}}


def test_failed_delivery_and_sla_breach(session: Session, hub_ids: dict[str, int]) -> None:
    s = _book(session, hub_ids)
    record_event(session, s, EventType.DELIVERY_ATTEMPT_FAILED, T0 + 3 * H, hub_id=hub_ids["DEL-DC1"])
    assert _detect(session, T0 + 4 * H) == {s: {ExceptionRule.DELIVERY_FAILED}}
    assert _detect(session, T0 + timedelta(days=5, hours=1)) == {
        s: {ExceptionRule.DELIVERY_FAILED, ExceptionRule.SLA_BREACH}
    }


def test_misrouted_to_another_delivery_center(session: Session, hub_ids: dict[str, int]) -> None:
    s = _book(session, hub_ids)
    record_event(session, s, EventType.ARRIVED_AT_HUB, T0 + 3 * H, hub_id=hub_ids["PNQ-DC1"])
    assert ExceptionRule.MISROUTED in _detect(session, T0 + 4 * H)[s]


def test_delivered_shipments_are_never_flagged(session: Session, hub_ids: dict[str, int]) -> None:
    s = _book(session, hub_ids)
    record_event(session, s, EventType.DELIVERED, T0 + 3 * H, hub_id=hub_ids["DEL-DC1"])
    assert _detect(session, T0 + timedelta(days=10)) == {}


def test_the_detector_sees_only_events_up_to_as_of(session: Session, hub_ids: dict[str, int]) -> None:
    s = _book(session, hub_ids)
    record_event(session, s, EventType.PICKED_UP, T0 + 30 * H, hub_id=hub_ids["BLR-DC1"])
    # At T0 + 20 h the pickup hasn't happened yet, even though it's already in the table.
    assert _detect(session, T0 + 20 * H) == {s: {ExceptionRule.PICKUP_OVERDUE}}


def test_running_twice_is_idempotent(session: Session, hub_ids: dict[str, int]) -> None:
    _book(session, hub_ids)
    first = run_detector(session, T0 + 20 * H, RULES)
    second = run_detector(session, T0 + 20 * H, RULES)
    assert (first.opened, second.opened, second.still_open) == (1, 0, 1)
    assert len(session.scalars(select(ShipmentException)).all()) == 1


def test_a_cleared_problem_that_returns_opens_a_new_exception(session: Session, hub_ids: dict[str, int]) -> None:
    s = _book(session, hub_ids)
    record_event(session, s, EventType.DELIVERY_ATTEMPT_FAILED, T0 + 1 * H, hub_id=hub_ids["DEL-DC1"])
    _detect(session, T0 + 2 * H)
    record_event(session, s, EventType.OUT_FOR_DELIVERY, T0 + 20 * H, hub_id=hub_ids["DEL-DC1"])
    _detect(session, T0 + 21 * H)
    record_event(session, s, EventType.DELIVERY_ATTEMPT_FAILED, T0 + 25 * H, hub_id=hub_ids["DEL-DC1"])
    _detect(session, T0 + 26 * H)
    statuses = sorted(e.status for e in session.scalars(select(ShipmentException)))
    assert statuses == [ExceptionStatus.OPEN, ExceptionStatus.RESOLVED]


def test_disabled_rules_are_skipped(session: Session, hub_ids: dict[str, int]) -> None:
    _book(session, hub_ids)
    config = RULES.model_copy(update={"pickup_overdue": RULES.pickup_overdue.model_copy(update={"enabled": False})})
    assert run_detector(session, T0 + 20 * H, config).opened == 0


def test_naive_as_of_is_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        run_detector(session, datetime(2026, 11, 2), RULES)


def test_replay_times_include_the_end() -> None:
    times = list(replay_times(T0, T0 + 5 * H, 2 * H))
    assert times == [T0, T0 + 2 * H, T0 + 4 * H, T0 + 5 * H]
