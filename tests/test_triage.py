"""Triage: evidence gathering, both diagnosers, policy validation, and that the agent only proposes."""

import json
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.main import create_app
from core.actions.policy import load_policy
from core.db.models import Action, ActionStatus, ExceptionStatus, ShipmentException, TriageRun
from core.detector.rules import load_rules
from core.detector.run import run_detector
from core.llm import ScriptedLLM
from core.simulator.config import load_simulator_config
from core.simulator.network import Network
from core.simulator.run import simulate, write
from core.triage.graph import AGENT, Triage
from core.triage.schema import Cause
from eval.triage import FIRST_EXCEPTIONS
from tests.conftest import ROOT, login, make_settings
from tests.test_api import T0, _exception

pytestmark = pytest.mark.postgres

POLICY = load_policy(ROOT / "config" / "action_policy.yaml")
RULES = load_rules(ROOT / "config" / "rules.yaml")
SIM = load_simulator_config(ROOT / "config" / "simulator.yaml")


def _faulty_month(session: Session, fault: str, *, open_only: bool = True) -> list[int]:
    """Simulate shipments where most get `fault`, and replay the detector.

    Returns the open exception ids, or with open_only=False each faulty shipment's first exception
    (the way the eval picks cases).
    """
    rates = {name: 0.0 for name in type(SIM.faults).model_fields}
    rates[fault] = 0.9
    faults = {n: getattr(SIM.faults, n).model_copy(update={"rate": r}) for n, r in rates.items()}
    cfg = SIM.model_copy(update={"faults": SIM.faults.model_copy(update=faults),
                                 "demand": SIM.demand.model_copy(update={"shipments_per_day": 40})})  # fmt: skip
    start = T0.date()
    # Two days of bookings, followed for a week, so faults play out the way they do in the 30-day eval.
    shipments = simulate(Network.from_db(session), cfg, start, 2, T0 + timedelta(days=7))
    write(session, shipments)
    as_of = max(e.occurred_at for s in shipments for e in s.events)
    t = min(s.booking.booked_at for s in shipments)
    while t < as_of:
        run_detector(session, t, RULES)
        t += timedelta(hours=2)
    run_detector(session, as_of, RULES)
    if open_only:
        return list(
            session.scalars(select(ShipmentException.id).where(ShipmentException.status == ExceptionStatus.OPEN))
        )
    return list(session.scalars(text(FIRST_EXCEPTIONS).columns(ShipmentException.id)))


@pytest.mark.parametrize(
    ("fault", "minimum_accuracy"),
    [
        ("pickup_delay", 0.9),
        ("failed_delivery", 0.9),
        ("lost", 0.9),
        ("hub_stuck", 0.7),
        ("missed_connection", 0.6),
        ("misroute", 0.6),
    ],
)
def test_rules_name_the_injected_fault(session: Session, hub_ids: dict[str, int], fault: str,
                                       minimum_accuracy: float) -> None:  # fmt: skip
    exception_ids = _faulty_month(session, fault, open_only=False)
    assert exception_ids
    triage = Triage(session, POLICY, None, persist=False)
    causes = [triage.run(i)["diagnosis"].cause for i in exception_ids]
    accuracy = sum(c.value == fault for c in causes) / len(causes)
    assert accuracy >= minimum_accuracy, (fault, accuracy, causes[:10])


def test_context_never_includes_ground_truth(session: Session, hub_ids: dict[str, int]) -> None:
    exception_ids = _faulty_month(session, "lost")
    triage = Triage(session, POLICY, None, persist=False)
    context = triage.run(exception_ids[0])["context"].model_dump_json()
    assert "sim_fault" not in context and "injected" not in context


def test_context_is_as_of_the_last_sighting(session: Session, hub_ids: dict[str, int]) -> None:
    exception_ids = _faulty_month(session, "pickup_delay")
    ctx = Triage(session, POLICY, None, persist=False).run(exception_ids[0])["context"]
    assert all(e.at <= ctx.as_of for e in ctx.timeline)


def test_triage_proposes_but_never_executes(session: Session, hub_ids: dict[str, int]) -> None:
    exception_ids = _faulty_month(session, "failed_delivery")
    state = Triage(session, POLICY, None).run(exception_ids[0])
    assert state["action_ids"]
    actions = session.scalars(select(Action).where(Action.id.in_(state["action_ids"]))).all()
    assert {a.status for a in actions} == {ActionStatus.PROPOSED}
    assert {a.proposed_by for a in actions} == {AGENT}
    run = session.get(TriageRun, state["triage_run_id"])
    assert run is not None and run.cause == Cause.FAILED_DELIVERY and run.diagnoser == "rules"


def test_running_twice_does_not_duplicate_pending_actions(session: Session, hub_ids: dict[str, int]) -> None:
    exception_ids = _faulty_month(session, "failed_delivery")
    triage = Triage(session, POLICY, None)
    first = triage.run(exception_ids[0])
    second = triage.run(exception_ids[0])
    assert first["action_ids"] and second["action_ids"] == []
    assert all("already awaiting approval" in r for r in second["rejected"])


def _llm_reply(**overrides: Any) -> str:
    reply = {
        "cause": "failed_delivery",
        "confidence": 0.8,
        "summary": "The customer wasn't in.",
        "evidence": ["one failed attempt"],
        "actions": [{"action_type": "notify_customer", "params": {"template": "failed_attempt"}, "rationale": "x"}],
        **overrides,
    }
    return json.dumps(reply)


def test_llm_diagnosis_is_used_and_policy_checked(session: Session, hub_ids: dict[str, int]) -> None:
    exception_ids = _faulty_month(session, "failed_delivery")
    bad_action = {"action_type": "open_trace", "params": {}, "rationale": "not allowed for this rule"}
    replies = [
        _llm_reply(actions=[bad_action]),  # everything rejected -> asked again
        _llm_reply(),
    ]
    llm = ScriptedLLM(model="test", reply=lambda system, prompt: replies.pop(0))
    state = Triage(session, POLICY, llm, persist=False).run(exception_ids[0])
    assert state["diagnoser"] == "llm:scripted/test"
    assert [a.action_type for a in state["diagnosis"].actions] == ["notify_customer"]
    assert len(llm.calls) == 2


def test_llm_gets_the_rejection_as_feedback(session: Session, hub_ids: dict[str, int]) -> None:
    exception_ids = _faulty_month(session, "failed_delivery")
    prompts: list[str] = []
    bad_action = {"action_type": "open_trace", "params": {}, "rationale": "not allowed"}
    replies = [_llm_reply(actions=[bad_action]), _llm_reply()]

    def reply(system: str, prompt: str) -> str:
        prompts.append(prompt)
        return replies.pop(0)

    Triage(session, POLICY, ScriptedLLM(model="t", reply=reply), persist=False).run(exception_ids[0])
    assert "rejected by policy" in prompts[1] and "open_trace" in prompts[1]


def test_falls_back_to_rules_when_the_model_keeps_failing(session: Session, hub_ids: dict[str, int]) -> None:
    exception_ids = _faulty_month(session, "failed_delivery")
    llm = ScriptedLLM(model="t", reply=lambda system, prompt: '{"cause": "aliens"}')
    state = Triage(session, POLICY, llm, persist=False).run(exception_ids[0])
    assert state["diagnoser"].startswith("rules (llm failed")
    assert state["diagnosis"].cause is Cause.FAILED_DELIVERY
    assert len(llm.calls) == 2


def test_triage_endpoint(api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    with TestClient(create_app(make_settings(), engine=api_engine)) as client:
        assert client.post(f"/exceptions/{exc_id}/triage", headers=login(client, "vera")).status_code == 403
        r = client.post(f"/exceptions/{exc_id}/triage", headers=login(client, "olga"))
        assert r.status_code == 200, r.text
        detail = r.json()
        assert detail["triage"][0]["cause"] == "failed_delivery"
        proposed = [a for a in detail["actions"] if a["proposed_by"] == AGENT]
        assert proposed and all(a["status"] == "proposed" for a in proposed)

        # A supervisor approves the agent's proposal through the normal queue.
        approved = client.post(f"/actions/{proposed[0]['id']}/approve", json={}, headers=login(client, "sam"))
        assert approved.json()["status"] == "executed"
