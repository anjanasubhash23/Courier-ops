"""List and inspect exceptions; propose actions for them; resolve them by hand."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Row, Select, func, select
from sqlalchemy.orm import Session, aliased

from app.auth import Operator, Viewer
from app.deps import DbSession, Policy, get_llm_dep
from app.errors import service_errors
from app.schemas import (
    ActionOut,
    EventOut,
    ExceptionDetail,
    ExceptionOut,
    Page,
    ProposeAction,
    Resolve,
    ShipmentOut,
    TriageOut,
)
from core.actions import service
from core.db.clock import sim_now
from core.db.models import (
    Action,
    ActionStatus,
    ExceptionRule,
    ExceptionStatus,
    Hub,
    Severity,
    Shipment,
    ShipmentException,
    TrackingEvent,
    TriageRun,
)
from core.llm import LLM
from core.triage.graph import Triage

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

_pending = (
    select(func.count())
    .where(Action.exception_id == ShipmentException.id, Action.status == ActionStatus.PROPOSED)
    .correlate(ShipmentException)
    .scalar_subquery()
)


def _query() -> Select[ShipmentException, str, str | None, int]:
    return (
        select(ShipmentException, Shipment.tracking_number, Hub.code, _pending)
        .join(Shipment, Shipment.id == ShipmentException.shipment_id)
        .outerjoin(Hub, Hub.id == ShipmentException.hub_id)
    )


ExceptionRow = Row[ShipmentException, str, str | None, int]


def _fields(obj: object, model: type[BaseModel]) -> dict[str, object]:
    """The attributes of `obj` that `model` has fields for."""
    return {name: getattr(obj, name) for name in model.model_fields if hasattr(obj, name)}


def _out(row: ExceptionRow) -> ExceptionOut:
    exc, tracking, hub, pending = row
    return ExceptionOut.model_validate(
        {**_fields(exc, ExceptionOut), "tracking_number": tracking, "hub": hub, "pending_actions": pending}
    )


@router.get("")
def list_exceptions(
    session: DbSession,
    _: Viewer,
    status_: Annotated[ExceptionStatus | None, Query(alias="status")] = ExceptionStatus.OPEN,
    rule: ExceptionRule | None = None,
    severity: Severity | None = None,
    hub: str | None = None,
    tracking_number: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ExceptionOut]:
    q = _query()
    if status_:
        q = q.where(ShipmentException.status == status_)
    if rule:
        q = q.where(ShipmentException.rule == rule)
    if severity:
        q = q.where(ShipmentException.severity == severity)
    if hub:
        q = q.where(Hub.code == hub)
    if tracking_number:
        q = q.where(Shipment.tracking_number == tracking_number)
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
    rows = session.execute(
        q.order_by(
            func.array_position([s.value for s in severity_order], ShipmentException.severity),
            ShipmentException.detected_at,
            ShipmentException.id,
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(items=[_out(row) for row in rows], total=total, limit=limit, offset=offset)


def load_detail(session: Session, policy: Policy, exception_id: int) -> ExceptionDetail:
    row = session.execute(_query().where(ShipmentException.id == exception_id)).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"exception {exception_id} not found")
    exc = row[0]

    origin, destination, last = aliased(Hub), aliased(Hub), aliased(Hub)
    s, origin_code, destination_code, last_code = session.execute(
        select(Shipment, origin.code, destination.code, last.code)
        .join(origin, origin.id == Shipment.origin_hub_id)
        .join(destination, destination.id == Shipment.destination_hub_id)
        .outerjoin(last, last.id == Shipment.last_scan_hub_id)
        .where(Shipment.id == exc.shipment_id)
    ).one()
    shipment = ShipmentOut.model_validate(
        {
            **_fields(s, ShipmentOut),
            "origin_hub": origin_code,
            "destination_hub": destination_code,
            "last_scan_hub": last_code,
        }
    )
    timeline = [
        EventOut(event_type=e.event_type, occurred_at=e.occurred_at, hub=code, reason=e.reason)
        for e, code in session.execute(
            select(TrackingEvent, Hub.code)
            .outerjoin(Hub, Hub.id == TrackingEvent.hub_id)
            .where(TrackingEvent.shipment_id == exc.shipment_id)
            .order_by(TrackingEvent.occurred_at, TrackingEvent.id)
        )
    ]
    actions = session.scalars(select(Action).where(Action.exception_id == exc.id).order_by(Action.id)).all()
    triage = [
        TriageOut.model_validate(t)
        for t in session.scalars(select(TriageRun).where(TriageRun.exception_id == exc.id).order_by(TriageRun.id))
    ]
    return ExceptionDetail(
        **_out(row).model_dump(),
        shipment=shipment,
        timeline=timeline,
        actions=[ActionOut.model_validate(a) for a in actions],
        allowed_actions=list(policy.allowed_for(exc.rule)),
        triage=triage,
    )


@router.get("/{exception_id}")
def get_exception(exception_id: int, session: DbSession, policy: Policy, _: Viewer) -> ExceptionDetail:
    return load_detail(session, policy, exception_id)


@router.post("/{exception_id}/actions", status_code=status.HTTP_201_CREATED)
def propose_action(
    exception_id: int,
    body: ProposeAction,
    session: DbSession,
    policy: Policy,
    user: Operator,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> ActionOut:
    with service_errors():
        action = service.propose(
            session,
            policy,
            exception_id,
            body.action_type,
            body.params,
            proposed_by=user.username,
            rationale=body.rationale,
            idempotency_key=idempotency_key,
        )
    return ActionOut.model_validate(action)


@router.post("/{exception_id}/resolve")
def resolve(exception_id: int, body: Resolve, session: DbSession, policy: Policy, user: Operator) -> ExceptionDetail:
    as_of = sim_now(session)
    if as_of is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no tracking events yet")
    with service_errors():
        service.resolve_exception(session, exception_id, username=user.username, as_of=as_of, note=body.note)
    return load_detail(session, policy, exception_id)


@router.post("/{exception_id}/triage")
def triage(
    exception_id: int,
    session: DbSession,
    policy: Policy,
    _: Operator,
    llm: Annotated[LLM | None, Depends(get_llm_dep)],
) -> ExceptionDetail:
    """Diagnose the exception and propose actions (as `agent:triage`). Uses rules when no LLM is configured."""
    exc = session.get(ShipmentException, exception_id)
    if exc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"exception {exception_id} not found")
    if exc.status is not ExceptionStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"exception {exception_id} is {exc.status}")
    Triage(session, policy, llm).run(exception_id)
    return load_detail(session, policy, exception_id)
