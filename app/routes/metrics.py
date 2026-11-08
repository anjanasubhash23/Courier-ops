"""Network summary for the dashboard."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.auth import Viewer
from app.deps import DbSession
from core.db.clock import sim_now
from core.db.models import Action, ExceptionStatus, Shipment, ShipmentException, ShipmentStatus

router = APIRouter(tags=["metrics"])


class Summary(BaseModel):
    as_of: datetime | None
    shipments: int
    by_status: dict[str, int]
    delivered: int
    delivered_on_time_pct: float | None
    open_exceptions: int
    open_by_rule: dict[str, int]
    open_by_severity: dict[str, int]
    actions_by_status: dict[str, int]


def _counts(session: DbSession, column: object, *where: object) -> dict[str, int]:
    rows = session.execute(select(column, func.count()).where(*where).group_by(column)).all()  # type: ignore[call-overload]
    return {str(k): n for k, n in sorted(rows)}


@router.get("/metrics/summary")
def summary(session: DbSession, _: Viewer) -> Summary:
    by_status = _counts(session, Shipment.status)
    delivered, on_time = session.execute(
        select(func.count(), func.count().filter(Shipment.delivered_at <= Shipment.promised_by)).where(
            Shipment.status == ShipmentStatus.DELIVERED
        )
    ).one()
    open_ = ShipmentException.status == ExceptionStatus.OPEN
    by_rule = _counts(session, ShipmentException.rule, open_)
    return Summary(
        as_of=sim_now(session),
        shipments=sum(by_status.values()),
        by_status=by_status,
        delivered=delivered,
        delivered_on_time_pct=round(100 * on_time / delivered, 1) if delivered else None,
        open_exceptions=sum(by_rule.values()),
        open_by_rule=by_rule,
        open_by_severity=_counts(session, ShipmentException.severity, open_),
        actions_by_status=_counts(session, Action.status),
    )
