"""Run the exception rules and reconcile the exceptions table with what they find."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.db.models import ExceptionRule, ExceptionStatus, ShipmentException
from core.detector.rules import RulesConfig, detection_params, detection_sql

DETECTOR = "detector"


@dataclass(frozen=True)
class DetectionResult:
    as_of: datetime
    opened: int
    still_open: int
    resolved: int


def run_detector(session: Session, as_of: datetime, config: RulesConfig) -> DetectionResult:
    """Open an exception for each new rule hit, refresh ones still hitting, resolve ones that cleared.

    Idempotent for a given as_of, and safe to run concurrently: the partial unique index allows only one
    open exception per shipment and rule.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    rules = config.enabled()
    hits: dict[tuple[int, ExceptionRule], tuple[int | None, dict[str, int | float | str]]] = {}
    if rules:
        rows = session.execute(text(detection_sql(rules)), {"as_of": as_of, **detection_params(config)})
        for shipment_id, hub_id, details, rule in rows:
            hits[(shipment_id, ExceptionRule(rule))] = (hub_id, details)

    open_rows = session.execute(
        select(ShipmentException.id, ShipmentException.shipment_id, ShipmentException.rule)
        .where(ShipmentException.status == ExceptionStatus.OPEN)
        .with_for_update()
    ).all()
    open_by_key = {(shipment_id, rule): exc_id for exc_id, shipment_id, rule in open_rows}

    new = [key for key in hits if key not in open_by_key]
    if new:
        stmt = insert(ShipmentException).values(
            [
                {
                    "shipment_id": shipment_id,
                    "rule": rule,
                    "severity": config.rule(rule).severity,
                    "hub_id": hits[(shipment_id, rule)][0],
                    "details": hits[(shipment_id, rule)][1],
                    "detected_at": as_of,
                    "last_seen_at": as_of,
                }
                for shipment_id, rule in new
            ]
        )
        session.execute(
            stmt.on_conflict_do_nothing(index_elements=["shipment_id", "rule"], index_where=text("status = 'open'"))
        )

    still = [(open_by_key[key], *hits[key]) for key in hits if key in open_by_key]
    for exc_id, hub_id, details in still:
        session.execute(
            update(ShipmentException)
            .where(ShipmentException.id == exc_id)
            .values(last_seen_at=as_of, hub_id=hub_id, details=details)
        )

    cleared = [exc_id for key, exc_id in open_by_key.items() if key not in hits]
    if cleared:
        session.execute(
            update(ShipmentException)
            .where(ShipmentException.id.in_(cleared))
            .values(status=ExceptionStatus.RESOLVED, resolved_at=as_of, resolved_by=DETECTOR)
        )
    session.flush()
    return DetectionResult(as_of, opened=len(new), still_open=len(still), resolved=len(cleared))


def replay_times(start: datetime, end: datetime, every: timedelta) -> Iterator[datetime]:
    """start, start + every, ... up to and including end."""
    t = start
    while t < end:
        yield t
        t += every
    yield end
