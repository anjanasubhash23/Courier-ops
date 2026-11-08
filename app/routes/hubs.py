"""Hubs with how many shipments are there and how many open exceptions."""

from fastapi import APIRouter
from sqlalchemy import func, select

from app.auth import Viewer
from app.deps import DbSession
from app.schemas import HubOut
from core.db.models import ExceptionStatus, Hub, Shipment, ShipmentException, ShipmentStatus

router = APIRouter(tags=["hubs"])

FINISHED = (ShipmentStatus.DELIVERED, ShipmentStatus.RETURNED)


@router.get("/hubs")
def list_hubs(session: DbSession, _: Viewer) -> list[HubOut]:
    here = (
        select(func.count())
        .where(Shipment.last_scan_hub_id == Hub.id, Shipment.status.not_in(FINISHED))
        .correlate(Hub)
        .scalar_subquery()
    )
    open_exceptions = (
        select(func.count())
        .where(ShipmentException.hub_id == Hub.id, ShipmentException.status == ExceptionStatus.OPEN)
        .correlate(Hub)
        .scalar_subquery()
    )
    rows = session.execute(select(Hub, here, open_exceptions).where(Hub.is_active).order_by(Hub.code)).all()
    return [
        HubOut(
            code=h.code,
            name=h.name,
            city=h.city,
            hub_type=h.hub_type,
            latitude=h.latitude,
            longitude=h.longitude,
            shipments_here=n_here,
            open_exceptions=n_open,
        )
        for h, n_here, n_open in rows
    ]
