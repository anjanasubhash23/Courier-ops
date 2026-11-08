"""The approval queue: list actions, approve (which executes) or reject them. Plus the audit log."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.auth import Admin, Supervisor, Viewer
from app.deps import DbSession, Policy
from app.errors import service_errors
from app.schemas import ActionOut, AuditOut, Decision
from core.actions import service
from core.db.models import Action, ActionStatus, AuditEntry

router = APIRouter(tags=["actions"])


@router.get("/actions")
def list_actions(
    session: DbSession,
    _: Viewer,
    status_: Annotated[ActionStatus | None, Query(alias="status")] = ActionStatus.PROPOSED,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ActionOut]:
    q = select(Action).order_by(Action.proposed_at, Action.id).limit(limit)
    if status_:
        q = q.where(Action.status == status_)
    return [ActionOut.model_validate(a) for a in session.scalars(q)]


@router.get("/actions/{action_id}")
def get_action(action_id: int, session: DbSession, _: Viewer) -> ActionOut:
    action = session.get(Action, action_id)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"action {action_id} not found")
    return ActionOut.model_validate(action)


@router.post("/actions/{action_id}/approve")
def approve(action_id: int, body: Decision, session: DbSession, policy: Policy, user: Supervisor) -> ActionOut:
    """Approve and execute. Returns 200 with status `failed` (and the error in `result`) if execution failed."""
    with service_errors():
        action = service.approve(session, policy, action_id, username=user.username, role=user.role, note=body.note)
    return ActionOut.model_validate(action)


@router.post("/actions/{action_id}/reject")
def reject(action_id: int, body: Decision, session: DbSession, policy: Policy, user: Supervisor) -> ActionOut:
    with service_errors():
        action = service.reject(session, policy, action_id, username=user.username, role=user.role, note=body.note)
    return ActionOut.model_validate(action)


@router.get("/audit")
def audit_log(
    session: DbSession,
    _: Admin,
    entity_type: str | None = None,
    entity_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditOut]:
    q = select(AuditEntry).order_by(AuditEntry.id.desc()).limit(limit)
    if entity_type:
        q = q.where(AuditEntry.entity_type == entity_type)
    if entity_id is not None:
        q = q.where(AuditEntry.entity_id == entity_id)
    return [AuditOut.model_validate(a) for a in session.scalars(q)]
