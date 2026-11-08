"""Propose, approve and reject actions. The only code that changes an action's status.

Approval executes the action in the same transaction that marks it executed, under a row lock, so an
action runs at most once however many times approve is called.
"""

import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.actions.executors import EXECUTORS
from core.actions.policy import ActionPolicy, PolicyViolation
from core.db.models import (
    Action,
    ActionStatus,
    ActionType,
    AuditEntry,
    ExceptionStatus,
    Hub,
    Role,
    Shipment,
    ShipmentException,
)

log = logging.getLogger(__name__)


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    """The request clashes with the current state (already decided, already proposed...)."""


class Forbidden(PermissionError):
    pass


def audit(
    session: Session, actor: str, event: str, entity_type: str, entity_id: int, **details: str | int | None
) -> None:
    session.add(AuditEntry(actor=actor, event=event, entity_type=entity_type, entity_id=entity_id, details=details))


def propose(
    session: Session,
    policy: ActionPolicy,
    exception_id: int,
    action_type: ActionType,
    params: dict[str, str],
    *,
    proposed_by: str,
    rationale: str | None = None,
    idempotency_key: str | None = None,
) -> Action:
    if idempotency_key and (
        existing := session.scalar(select(Action).where(Action.idempotency_key == idempotency_key))
    ):
        if (existing.exception_id, existing.action_type) != (exception_id, action_type):
            raise Conflict("idempotency key was already used for a different action")
        return existing

    exc = session.get(ShipmentException, exception_id)
    if exc is None:
        raise NotFound(f"exception {exception_id} not found")
    if exc.status is not ExceptionStatus.OPEN:
        raise Conflict(f"exception {exception_id} is {exc.status}")
    params = policy.check(exc.rule, action_type, params)
    if action_type is ActionType.REROUTE and not session.scalar(
        select(func.count()).select_from(Hub).where(Hub.code == params["via_hub"], Hub.is_active)
    ):
        raise PolicyViolation(f"unknown hub {params['via_hub']}")

    action = Action(
        exception_id=exception_id,
        action_type=action_type,
        params=params,
        rationale=rationale,
        proposed_by=proposed_by,
        idempotency_key=idempotency_key,
    )
    try:
        with session.begin_nested():
            session.add(action)
            session.flush()
    except IntegrityError:
        raise Conflict(f"a {action_type} action is already awaiting approval for exception {exception_id}") from None
    audit(session, proposed_by, "action.proposed", "action", action.id, action_type=action_type, exception=exception_id)
    return action


def _decide(session: Session, policy: ActionPolicy, action_id: int, username: str, role: Role) -> Action:
    if role.rank < policy.approver_role.rank:
        raise Forbidden(f"approving needs the {policy.approver_role} role")
    action = session.get(Action, action_id, with_for_update=True)
    if action is None:
        raise NotFound(f"action {action_id} not found")
    if action.status is not ActionStatus.PROPOSED:
        raise Conflict(f"action {action_id} is already {action.status}")
    if action.proposed_by == username:
        raise Forbidden("you can't decide on an action you proposed")
    return action


def approve(
    session: Session, policy: ActionPolicy, action_id: int, *, username: str, role: Role, note: str | None = None
) -> Action:
    """Approve and execute. If execution fails the action is marked failed; nothing it did is kept."""
    action = _decide(session, policy, action_id, username, role)
    exc = session.get(ShipmentException, action.exception_id)
    shipment = session.get(Shipment, exc.shipment_id) if exc else None
    if exc is None or shipment is None:
        raise NotFound(f"exception {action.exception_id} not found")

    action.decided_by, action.decided_at, action.decision_note = username, func.now(), note
    audit(session, username, "action.approved", "action", action.id, note=note)
    try:
        with session.begin_nested():
            result = EXECUTORS[action.action_type](session, action, exc, shipment)
    except Exception as err:
        log.exception("action %s failed", action.id)
        action.status, action.result = ActionStatus.FAILED, {"error": f"{err.__class__.__name__}: {err}"}
        audit(session, "system", "action.failed", "action", action.id, error=str(err))
    else:
        action.status, action.result, action.executed_at = ActionStatus.EXECUTED, result, func.now()
        audit(session, "system", "action.executed", "action", action.id)
    session.flush()
    session.refresh(action)
    return action


def reject(
    session: Session, policy: ActionPolicy, action_id: int, *, username: str, role: Role, note: str | None = None
) -> Action:
    action = _decide(session, policy, action_id, username, role)
    action.status, action.decided_by, action.decided_at, action.decision_note = (
        ActionStatus.REJECTED,
        username,
        func.now(),
        note,
    )
    audit(session, username, "action.rejected", "action", action.id, note=note)
    session.flush()
    session.refresh(action)
    return action


def resolve_exception(session: Session, exception_id: int, *, username: str, as_of: datetime, note: str) -> None:
    """Close an exception by hand (e.g. the problem was fixed outside the system)."""
    exc = session.get(ShipmentException, exception_id, with_for_update=True)
    if exc is None:
        raise NotFound(f"exception {exception_id} not found")
    if exc.status is not ExceptionStatus.OPEN:
        raise Conflict(f"exception {exception_id} is already {exc.status}")
    exc.status, exc.resolved_at, exc.resolved_by = ExceptionStatus.RESOLVED, as_of, username
    audit(session, username, "exception.resolved", "exception", exc.id, note=note)
    session.flush()
