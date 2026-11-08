"""Triage an exception with LangGraph: gather -> diagnose -> validate (-> diagnose again) -> propose.

The agent only ever proposes. It has no route to approval or execution: proposals land in the same
queue as an operator's, as `agent:triage`, and a supervisor decides (core.actions.service.approve).
"""

import logging
from typing import Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from core.actions import service
from core.actions.policy import ActionPolicy, PolicyViolation
from core.db.models import TriageRun
from core.llm import LLM, LLMError
from core.triage import tools
from core.triage.diagnose import diagnose_with_llm, diagnose_with_rules
from core.triage.schema import Context, Diagnosis, ProposedAction

log = logging.getLogger(__name__)

AGENT = "agent:triage"
MAX_LLM_ATTEMPTS = 2


class State(TypedDict, total=False):
    exception_id: int
    context: Context
    diagnosis: Diagnosis
    diagnoser: str
    attempts: int
    feedback: str | None
    rejected: list[str]  # actions dropped by validation, with the reason
    triage_run_id: int
    action_ids: list[int]


class Triage:
    """Holds what the graph's nodes need (session, policy, model) and builds the graph once."""

    def __init__(self, session: Session, policy: ActionPolicy, llm: LLM | None, *, persist: bool = True) -> None:
        self.session, self.policy, self.llm, self.persist = session, policy, llm, persist
        graph = StateGraph(State)
        graph.add_node("gather", self.gather)
        graph.add_node("diagnose", self.diagnose)
        graph.add_node("validate", self.validate)
        graph.add_node("propose", self.propose)
        graph.add_edge(START, "gather")
        graph.add_edge("gather", "diagnose")
        graph.add_edge("diagnose", "validate")
        graph.add_conditional_edges("validate", self.after_validate, {"retry": "diagnose", "propose": "propose"})
        graph.add_edge("propose", END)
        self.graph = graph.compile()

    def run(self, exception_id: int) -> State:
        result = self.graph.invoke({"exception_id": exception_id, "attempts": 0, "rejected": []})
        return cast(State, result)

    # --- nodes ---

    def gather(self, state: State) -> State:
        return {"context": tools.gather(self.session, state["exception_id"], self.policy)}

    def diagnose(self, state: State) -> State:
        ctx, attempts = state["context"], state.get("attempts", 0) + 1
        if self.llm is None:
            return {"diagnosis": diagnose_with_rules(ctx), "diagnoser": "rules", "attempts": attempts}
        try:
            diagnosis = diagnose_with_llm(self.llm, ctx, state.get("feedback"))
        except LLMError as exc:
            log.warning("triage of exception %s: model failed (%s)", ctx.exception_id, exc)
            if attempts < MAX_LLM_ATTEMPTS:
                return {"attempts": attempts, "feedback": str(exc)}
            return {"diagnosis": diagnose_with_rules(ctx), "diagnoser": f"rules (llm failed: {exc})"[:64],
                    "attempts": attempts}  # fmt: skip
        return {"diagnosis": diagnosis, "diagnoser": f"llm:{self.llm.name}"[:64], "attempts": attempts,
                "feedback": None}  # fmt: skip

    def validate(self, state: State) -> State:
        """Drop actions the policy doesn't allow. If the model's were all invalid, ask it once more."""
        if "diagnosis" not in state:  # the model call failed; retry
            return {}
        diagnosis, ctx = state["diagnosis"], state["context"]
        kept: list[ProposedAction] = []
        rejected: list[str] = []
        for action in diagnosis.actions:
            try:
                params = self.policy.check(ctx.rule, action.action_type, action.params)
            except PolicyViolation as exc:
                rejected.append(f"{action.action_type}: {exc}")
                continue
            kept.append(action.model_copy(update={"params": params}))
        update: State = {"diagnosis": diagnosis.model_copy(update={"actions": kept}), "rejected": rejected}
        if rejected and not kept and state["diagnoser"].startswith("llm:"):
            update["feedback"] = "these actions were rejected by policy: " + "; ".join(rejected)
        return update

    def after_validate(self, state: State) -> Literal["retry", "propose"]:
        needs_retry = "diagnosis" not in state or bool(state.get("feedback"))
        return "retry" if needs_retry and state.get("attempts", 0) < MAX_LLM_ATTEMPTS else "propose"

    def propose(self, state: State) -> State:
        diagnosis, ctx = state["diagnosis"], state["context"]
        if not self.persist:
            return {"action_ids": []}
        action_ids: list[int] = []
        rejected = list(state.get("rejected", []))
        for action in diagnosis.actions:
            try:
                created = service.propose(
                    self.session,
                    self.policy,
                    ctx.exception_id,
                    action.action_type,
                    action.params,
                    proposed_by=AGENT,
                    rationale=action.rationale,
                )
            except (service.Conflict, PolicyViolation) as exc:  # e.g. the same action is already awaiting approval
                rejected.append(f"{action.action_type}: {exc}")
                continue
            action_ids.append(created.id)
        run = TriageRun(
            exception_id=ctx.exception_id,
            cause=diagnosis.cause,
            confidence=diagnosis.confidence,
            summary=diagnosis.summary,
            evidence=diagnosis.evidence,
            diagnoser=state["diagnoser"],
            action_ids=action_ids,
        )
        self.session.add(run)
        self.session.flush()
        service.audit(self.session, AGENT, "triage.completed", "exception", ctx.exception_id,
                      cause=diagnosis.cause.value, diagnoser=state["diagnoser"], triage_run=run.id)  # fmt: skip
        return {"triage_run_id": run.id, "action_ids": action_ids, "rejected": rejected}
