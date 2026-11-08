"""Backfill into Postgres. Needs Postgres."""

import hashlib
from datetime import date, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.db.models import Base, EventType, Shipment, SimFault, TrackingEvent
from core.db.seed import load_network
from core.simulator.config import load_simulator_config
from core.simulator.network import Network
from core.simulator.run import backfill
from tests.conftest import ROOT

pytestmark = pytest.mark.postgres

CFG = load_simulator_config(ROOT / "config" / "simulator.yaml")
START = date(2026, 10, 5)
AS_OF = datetime.fromisoformat("2026-10-08T15:00:00+05:30")


def _count(session: Session, model: type[Base]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _digest(session: Session) -> str:
    """Everything the simulator wrote, keyed by tracking number rather than generated ids."""
    rows = session.execute(
        text("""
            SELECT s.tracking_number, s.status, s.promised_by, s.last_event_at, s.delivered_at,
                   e.event_type, eh.code, e.reason, e.occurred_at, f.fault_type, fh.code, f.injected_at, f.details
            FROM shipments s
            JOIN tracking_events e ON e.shipment_id = s.id
            LEFT JOIN hubs eh ON eh.id = e.hub_id
            LEFT JOIN sim_faults f ON f.shipment_id = s.id
            LEFT JOIN hubs fh ON fh.id = f.hub_id
            ORDER BY s.tracking_number, e.occurred_at, e.id
        """)
    ).all()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def test_backfill_writes_shipments_events_and_faults(session: Session, hub_ids: dict[str, int]) -> None:
    counts = backfill(session, CFG, START, 4, AS_OF)

    assert counts["shipments"] == _count(session, Shipment) > 500
    assert counts["events"] == _count(session, TrackingEvent)
    assert counts["faults"] == _count(session, SimFault) > 0
    last_event = session.scalar(select(func.max(TrackingEvent.occurred_at)))
    assert last_event is not None and last_event <= AS_OF


def test_projection_matches_the_latest_event(session: Session, hub_ids: dict[str, int]) -> None:
    backfill(session, CFG, START, 4, AS_OF)
    mismatches = session.execute(
        text("""
            SELECT s.tracking_number FROM shipments s
            JOIN LATERAL (
                SELECT event_type, occurred_at FROM tracking_events e
                WHERE e.shipment_id = s.id ORDER BY occurred_at DESC, id DESC LIMIT 1
            ) last ON true
            WHERE s.last_event_at <> last.occurred_at
               OR (last.event_type = 'delivered') <> (s.status = 'delivered')
        """)
    ).all()
    assert mismatches == []
    booked = session.scalar(select(func.count()).where(TrackingEvent.event_type == EventType.BOOKED))
    assert booked == _count(session, Shipment)


def test_same_seed_writes_identical_data(session: Session, hub_ids: dict[str, int]) -> None:
    backfill(session, CFG, START, 3, AS_OF)
    first = _digest(session)

    backfill(session, CFG, START, 3, AS_OF, replace=True)
    assert _digest(session) == first

    backfill(session, CFG.model_copy(update={"seed": 7}), START, 3, AS_OF, replace=True)
    assert _digest(session) != first


def test_refuses_to_add_to_existing_shipments(session: Session, hub_ids: dict[str, int]) -> None:
    backfill(session, CFG, START, 1, AS_OF)
    with pytest.raises(RuntimeError, match="already exist"):
        backfill(session, CFG, START, 1, AS_OF)


def test_needs_a_seeded_network(session: Session) -> None:
    with pytest.raises(RuntimeError, match="seed_hubs"):
        backfill(session, CFG, START, 1, AS_OF)


def test_network_from_db_matches_config(session: Session, hub_ids: dict[str, int]) -> None:
    from_db, from_config = Network.from_db(session), Network.from_config(load_network(ROOT / "config" / "hubs.yaml"))
    assert from_db.hubs == from_config.hubs
    assert from_db.service_areas == from_config.service_areas
    for hub in from_config.hubs:
        assert from_db.routes_from(hub) == from_config.routes_from(hub)
