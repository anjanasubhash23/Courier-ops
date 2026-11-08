"""What may be proposed for which exception, with which parameters, and who may approve it."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.db.models import ActionType, ExceptionRule, Role


class PolicyViolation(ValueError):
    """The proposal isn't allowed. The message says why and is safe to show to the proposer."""


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoParams(_Params):
    pass


class EscalateParams(_Params):
    note: str = Field(min_length=1, max_length=500)


class RerouteParams(_Params):
    via_hub: str = Field(pattern=r"^[A-Z]{3}-[A-Z]+[0-9]*$")


class RescheduleParams(_Params):
    slot: Literal["morning", "evening"]


class NotifyParams(_Params):
    template: Literal["delay", "failed_attempt", "returning", "lost"]


PARAMS: dict[ActionType, type[_Params]] = {
    ActionType.PRIORITY_PICKUP: NoParams,
    ActionType.ESCALATE_TO_HUB: EscalateParams,
    ActionType.REROUTE: RerouteParams,
    ActionType.OPEN_TRACE: NoParams,
    ActionType.RESCHEDULE_DELIVERY: RescheduleParams,
    ActionType.NOTIFY_CUSTOMER: NotifyParams,
    ActionType.INITIATE_RTO: NoParams,
}


class ActionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approver_role: Role
    allowed: dict[ExceptionRule, tuple[ActionType, ...]]

    def allowed_for(self, rule: ExceptionRule) -> tuple[ActionType, ...]:
        return self.allowed.get(rule, ())

    def check(self, rule: ExceptionRule, action_type: ActionType, params: dict[str, str]) -> dict[str, str]:
        """Validate a proposal; returns the normalised params or raises PolicyViolation."""
        if action_type not in self.allowed_for(rule):
            allowed = ", ".join(self.allowed_for(rule)) or "none"
            raise PolicyViolation(f"{action_type} is not allowed for {rule} (allowed: {allowed})")
        try:
            validated = PARAMS[action_type].model_validate(params)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'params'}: {e['msg']}" for e in exc.errors())
            raise PolicyViolation(f"invalid params for {action_type}: {problems}") from None
        return validated.model_dump()


def load_policy(path: Path) -> ActionPolicy:
    return ActionPolicy.model_validate(yaml.safe_load(path.read_text()))
