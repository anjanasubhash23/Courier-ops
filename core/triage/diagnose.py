"""Two diagnosers with the same contract (Context -> Diagnosis): an LLM, and transparent rules.

The rules are the fallback when no model is configured, and the baseline the LLM is scored against.
"""

from pathlib import Path

from pydantic import ValidationError

from core.db.models import ActionType, ExceptionRule
from core.llm import LLM, LLMError
from core.triage.schema import Cause, Context, Diagnosis, ProposedAction

PROMPT = Path(__file__).resolve().parent.parent.parent / "prompts" / "triage.md"

# Waits longer than these point to a stuck hub rather than a missed connection or the normal overnight wait.
STUCK_WAIT_HOURS = 28
DESTINATION_STUCK_WAIT_HOURS = 30
MISSED_CONNECTION_WAIT_HOURS = 12
PICKUP_DELAY_HOURS = 18
LOST_FACTOR = 1.5


def diagnose_with_rules(ctx: Context) -> Diagnosis:
    s = ctx.signals
    evidence: list[str] = []
    if (
        s.open_departure_hours is not None
        and s.open_departure_planned_hours
        and (s.open_departure_hours > LOST_FACTOR * s.open_departure_planned_hours + 1)
    ):
        cause, confidence = Cause.LOST, 0.8
        evidence.append(f"No scan for {s.open_departure_hours} h after departing; the route plans "
                        f"{s.open_departure_planned_hours} h")  # fmt: skip
    elif s.failed_attempts:
        cause, confidence = Cause.FAILED_DELIVERY, 0.9
        reasons = sorted({e.reason for e in ctx.timeline if e.reason})
        evidence.append(f"{s.failed_attempts} failed delivery attempt(s): {', '.join(reasons) or 'no reason'}")
    elif s.off_path_hubs or s.revisited_hubs:
        cause, confidence = Cause.MISROUTE, 0.85
        evidence.append(f"Visited {', '.join(s.off_path_hubs or s.revisited_hubs)}, off the planned path "
                        f"{' -> '.join(ctx.planned_path)}")  # fmt: skip
    elif s.hours_to_pickup is not None and s.hours_to_pickup > PICKUP_DELAY_HOURS:
        cause, confidence = Cause.PICKUP_DELAY, 0.85
        evidence.append(f"{s.hours_to_pickup} h from booking to pickup")
    elif s.longest_wait_hours > (DESTINATION_STUCK_WAIT_HOURS if s.longest_wait_at_destination else STUCK_WAIT_HOURS):
        cause, confidence = Cause.HUB_STUCK, 0.7
        evidence.append(f"Waited {s.longest_wait_hours} h at {s.longest_wait_hub}")
    elif s.longest_wait_hours > MISSED_CONNECTION_WAIT_HOURS and not s.longest_wait_at_destination:
        cause = Cause.MISSED_CONNECTION
        still_waiting = ctx.timeline[-1].event_type in ("arrived_at_hub", "picked_up")
        if still_waiting:
            # A missed connection leaves after about a day; a stuck parcel doesn't. Until then they look the same.
            confidence = 0.4
            evidence.append(
                f"Waiting {s.longest_wait_hours} h at {s.longest_wait_hub} so far: a missed departure, "
                "or stuck if it's still there after ~28 h"
            )
        else:
            confidence = 0.7
            evidence.append(f"Waited {s.longest_wait_hours} h at {s.longest_wait_hub}, about one departure cycle")
    else:
        cause, confidence = Cause.UNKNOWN, 0.3
        evidence.append("No signal stands out in the timeline")

    evidence.append(f"Exception {ctx.rule} at {ctx.exception_hub or 'unknown hub'}; promised by "
                    f"{ctx.promised_by.isoformat()}")  # fmt: skip
    if ctx.open_exceptions_at_hub and ctx.open_exceptions_at_hub > 5:
        evidence.append(f"{ctx.open_exceptions_at_hub} open exceptions at {ctx.exception_hub}: possible congestion")

    return Diagnosis(
        cause=cause,
        confidence=confidence,
        summary=f"Likely {cause.value.replace('_', ' ')} for {ctx.tracking_number}. {evidence[0]}.",
        evidence=evidence[:6],
        actions=_actions_for(cause, ctx),
    )


def _actions_for(cause: Cause, ctx: Context) -> list[ProposedAction]:
    hub = ctx.signals.longest_wait_hub or ctx.exception_hub or "the hub"
    next_hub = next((h for h in ctx.planned_path[1:] if h not in ctx.signals.off_path_hubs), ctx.destination_hub)
    wanted: dict[Cause, list[ProposedAction]] = {
        Cause.PICKUP_DELAY: [
            ProposedAction(action_type=ActionType.PRIORITY_PICKUP, rationale="Pickup is overdue"),
            ProposedAction(
                action_type=ActionType.NOTIFY_CUSTOMER,
                params={"template": "delay"},
                rationale="Tell the sender the pickup is late",
            ),
        ],
        Cause.HUB_STUCK: [
            ProposedAction(
                action_type=ActionType.ESCALATE_TO_HUB,
                params={"note": f"Parcel {ctx.tracking_number} held at {hub}; locate and dispatch"},
                rationale="The hub needs to find and move it",
            ),
            ProposedAction(
                action_type=ActionType.NOTIFY_CUSTOMER, params={"template": "delay"}, rationale="Delivery will be late"
            ),
        ],
        Cause.MISSED_CONNECTION: [
            ProposedAction(
                action_type=ActionType.ESCALATE_TO_HUB,
                params={"note": f"Put {ctx.tracking_number} on the next departure from {hub}"},
                rationale="It missed its departure",
            ),
        ],
        Cause.MISROUTE: [
            ProposedAction(
                action_type=ActionType.REROUTE,
                params={"via_hub": next_hub},
                rationale="Send it back onto the planned path",
            ),
            ProposedAction(
                action_type=ActionType.ESCALATE_TO_HUB,
                params={"note": f"{ctx.tracking_number} was mis-sorted; check the sort plan"},
                rationale="Find out why it was mis-sorted",
            ),
        ],
        Cause.FAILED_DELIVERY: [
            ProposedAction(
                action_type=ActionType.RESCHEDULE_DELIVERY,
                params={"slot": "evening"},
                rationale="Try when the customer is more likely to be in",
            ),
            ProposedAction(
                action_type=ActionType.NOTIFY_CUSTOMER,
                params={"template": "failed_attempt"},
                rationale="Let the customer arrange the next attempt",
            ),
        ],
        Cause.LOST: [
            ProposedAction(action_type=ActionType.OPEN_TRACE, rationale="No scan since departure"),
            ProposedAction(
                action_type=ActionType.NOTIFY_CUSTOMER,
                params={"template": "lost"},
                rationale="Be upfront that it can't be located",
            ),
        ],
        Cause.UNKNOWN: [],
    }
    allowed = set(ctx.allowed_actions)
    chosen = [a for a in wanted[cause] if a.action_type in allowed]
    if not chosen and ActionType.NOTIFY_CUSTOMER in allowed and ctx.rule is ExceptionRule.SLA_BREACH:
        chosen = [ProposedAction(action_type=ActionType.NOTIFY_CUSTOMER, params={"template": "delay"},
                                 rationale="The promised time has passed")]  # fmt: skip
    return chosen


def diagnose_with_llm(llm: LLM, ctx: Context, feedback: str | None = None) -> Diagnosis:
    """Raises LLMError if the model fails or its reply doesn't match the Diagnosis schema."""
    prompt = "Context:\n" + ctx.model_dump_json(indent=1)
    if feedback:
        prompt += f"\n\nYour previous reply was rejected: {feedback}\nReply again, fixing that."
    reply = llm.complete_json(PROMPT.read_text(), prompt)
    try:
        return Diagnosis.model_validate(reply)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
        raise LLMError(f"reply doesn't match the schema: {problems}") from None
