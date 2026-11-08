"""The HTTP API end to end: login, roles, exceptions, the propose/approve flow, the audit log. Needs Postgres."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.events import book_shipment, record_event
from core.db.models import EventType, Hub, ServiceLevel, Shipment, ShipmentStatus
from core.detector.rules import load_rules
from core.detector.run import run_detector
from tests.conftest import ROOT, login

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)


def _exception(engine: Engine, *, failed: bool = True, n: int = 1) -> int:
    """A shipment with a failed delivery (or never picked up), detected. Returns the exception id."""
    with Session(engine) as s, s.begin():
        hubs = dict(s.execute(select(Hub.code, Hub.id)).all())
        shipment = book_shipment(
            s,
            Shipment(
                tracking_number=f"CO{n:010d}",
                service_level=ServiceLevel.STANDARD,
                origin_hub_id=hubs["BLR-DC1"],
                destination_hub_id=hubs["DEL-DC1"],
                origin_pincode="560034",
                destination_pincode="110001",
                weight_grams=500,
                booked_at=T0,
                promised_by=T0 + timedelta(days=5),
            ),
        )
        if failed:
            record_event(
                s, shipment.id, EventType.DELIVERY_ATTEMPT_FAILED, T0 + timedelta(hours=2), hub_id=hubs["DEL-DC1"]
            )
        run_detector(s, T0 + timedelta(hours=20), load_rules(ROOT / "config" / "rules.yaml"))
        return s.scalar(text("SELECT max(id) FROM exceptions WHERE shipment_id = :id"), {"id": shipment.id}) or 0


# --- auth -----------------------------------------------------------------------------------------


def test_login_and_me(api: TestClient) -> None:
    r = api.get("/auth/me", headers=login(api, "sam"))
    assert r.status_code == 200
    assert r.json()["username"] == "sam" and r.json()["role"] == "supervisor"


def test_wrong_password_and_missing_token(api: TestClient) -> None:
    assert api.post("/auth/token", data={"username": "sam", "password": "nope"}).status_code == 401
    assert api.post("/auth/token", data={"username": "nobody", "password": "nope"}).status_code == 401
    assert api.get("/exceptions").status_code == 401
    assert api.get("/exceptions", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_deactivated_users_lose_access_immediately(api: TestClient, api_engine: Engine) -> None:
    headers = login(api, "olga")
    with api_engine.begin() as conn:
        conn.execute(text("UPDATE users SET is_active = false WHERE username = 'olga'"))
    assert api.get("/auth/me", headers=headers).status_code == 401


def test_roles_are_enforced(api: TestClient, api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    body = {"action_type": "notify_customer", "params": {"template": "failed_attempt"}}
    assert api.post(f"/exceptions/{exc_id}/actions", json=body, headers=login(api, "vera")).status_code == 403
    assert api.post("/sim/detect", headers=login(api, "sam")).status_code == 403
    assert api.get("/audit", headers=login(api, "sam")).status_code == 403
    assert api.get("/audit", headers=login(api, "ada")).status_code == 200


# --- exceptions -----------------------------------------------------------------------------------


def test_list_and_detail(api: TestClient, api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    headers = login(api, "vera")
    page = api.get("/exceptions", headers=headers).json()
    assert page["total"] == 1
    assert page["items"][0]["rule"] == "delivery_failed"
    assert page["items"][0]["tracking_number"] == "CO0000000001"

    assert api.get("/exceptions?rule=sla_breach", headers=headers).json()["total"] == 0
    assert api.get("/exceptions?hub=DEL-DC1", headers=headers).json()["total"] == 1

    detail = api.get(f"/exceptions/{exc_id}", headers=headers).json()
    assert [e["event_type"] for e in detail["timeline"]] == ["booked", "delivery_attempt_failed"]
    assert detail["shipment"]["destination_hub"] == "DEL-DC1"
    assert set(detail["allowed_actions"]) == {"reschedule_delivery", "notify_customer", "initiate_rto"}
    assert api.get("/exceptions/999999", headers=headers).status_code == 404


def test_resolve_by_hand(api: TestClient, api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    r = api.post(f"/exceptions/{exc_id}/resolve", json={"note": "customer collected it"}, headers=login(api, "olga"))
    assert r.status_code == 200
    assert r.json()["status"] == "resolved" and r.json()["resolved_by"] == "olga"
    again = api.post(f"/exceptions/{exc_id}/resolve", json={"note": "x"}, headers=login(api, "olga"))
    assert again.status_code == 409


# --- propose / approve / reject -------------------------------------------------------------------


def _propose(api: TestClient, exc_id: int, user: str = "olga", **body: object) -> dict[str, object]:
    body = {"action_type": "notify_customer", "params": {"template": "failed_attempt"}, **body}
    r = api.post(f"/exceptions/{exc_id}/actions", json=body, headers=login(api, user))
    assert r.status_code == 201, r.text
    result: dict[str, object] = r.json()
    return result


def test_propose_then_approve_executes(api: TestClient, api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    action = _propose(api, exc_id, rationale="customer asked for an update")
    assert action["status"] == "proposed"
    assert [a["id"] for a in api.get("/actions", headers=login(api, "vera")).json()] == [action["id"]]

    r = api.post(f"/actions/{action['id']}/approve", json={"note": "ok"}, headers=login(api, "sam"))
    assert r.status_code == 200
    done = r.json()
    assert done["status"] == "executed"
    assert done["decided_by"] == "sam"
    assert "CO0000000001" in done["result"]["message"]
    assert api.get("/actions", headers=login(api, "vera")).json() == []


def test_initiate_rto_changes_the_shipment(api: TestClient, api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    action = _propose(api, exc_id, action_type="initiate_rto", params={})
    assert (
        api.post(f"/actions/{action['id']}/approve", json={}, headers=login(api, "sam")).json()["status"] == "executed"
    )
    with Session(api_engine) as s:
        shipment = s.scalars(select(Shipment)).one()
        assert shipment.status is ShipmentStatus.RETURNING


def test_actions_run_at_most_once(api: TestClient, api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    action = _propose(api, exc_id, action_type="initiate_rto", params={})
    headers = login(api, "sam")
    assert api.post(f"/actions/{action['id']}/approve", json={}, headers=headers).status_code == 200
    assert api.post(f"/actions/{action['id']}/approve", json={}, headers=headers).status_code == 409
    assert api.post(f"/actions/{action['id']}/reject", json={}, headers=headers).status_code == 409
    with api_engine.connect() as conn:
        rto = conn.scalar(text("SELECT count(*) FROM tracking_events WHERE event_type = 'rto_initiated'"))
    assert rto == 1


def test_nobody_approves_their_own_proposal(api: TestClient, api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    action = _propose(api, exc_id, user="sam")  # supervisors can propose too
    r = api.post(f"/actions/{action['id']}/approve", json={}, headers=login(api, "sam"))
    assert r.status_code == 403
    assert "you proposed" in r.json()["detail"]


def test_operators_cannot_approve(api: TestClient, api_engine: Engine) -> None:
    action = _propose(api, _exception(api_engine))
    assert api.post(f"/actions/{action['id']}/approve", json={}, headers=login(api, "otto")).status_code == 403


def test_reject(api: TestClient, api_engine: Engine) -> None:
    action = _propose(api, _exception(api_engine))
    r = api.post(f"/actions/{action['id']}/reject", json={"note": "not needed"}, headers=login(api, "sam"))
    assert r.json()["status"] == "rejected" and r.json()["decision_note"] == "not needed"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"action_type": "open_trace", "params": {}}, "not allowed for delivery_failed"),
        ({"action_type": "notify_customer", "params": {}}, "template"),
        ({"action_type": "notify_customer", "params": {"template": "spam"}}, "template"),
        ({"action_type": "notify_customer", "params": {"template": "delay", "x": "1"}}, "x"),
    ],
)
def test_policy_is_enforced(api: TestClient, api_engine: Engine, body: dict[str, object], message: str) -> None:
    r = api.post(f"/exceptions/{_exception(api_engine)}/actions", json=body, headers=login(api, "olga"))
    assert r.status_code == 422
    assert message in r.json()["detail"]


def test_one_pending_proposal_per_type(api: TestClient, api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    _propose(api, exc_id)
    body = {"action_type": "notify_customer", "params": {"template": "delay"}}
    assert api.post(f"/exceptions/{exc_id}/actions", json=body, headers=login(api, "otto")).status_code == 409


def test_idempotency_key_returns_the_same_action(api: TestClient, api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    headers = {**login(api, "olga"), "Idempotency-Key": "click-123"}
    body = {"action_type": "notify_customer", "params": {"template": "delay"}}
    first = api.post(f"/exceptions/{exc_id}/actions", json=body, headers=headers).json()
    second = api.post(f"/exceptions/{exc_id}/actions", json=body, headers=headers).json()
    assert first["id"] == second["id"]


def test_resolved_exceptions_take_no_proposals(api: TestClient, api_engine: Engine) -> None:
    exc_id = _exception(api_engine)
    api.post(f"/exceptions/{exc_id}/resolve", json={"note": "fixed"}, headers=login(api, "olga"))
    body = {"action_type": "notify_customer", "params": {"template": "delay"}}
    assert api.post(f"/exceptions/{exc_id}/actions", json=body, headers=login(api, "olga")).status_code == 409


# --- audit, metrics, hubs, sim --------------------------------------------------------------------


def test_everything_is_audited_and_the_log_is_append_only(api: TestClient, api_engine: Engine) -> None:
    action = _propose(api, _exception(api_engine))
    api.post(f"/actions/{action['id']}/approve", json={}, headers=login(api, "sam"))
    log = api.get(f"/audit?entity_type=action&entity_id={action['id']}", headers=login(api, "ada")).json()
    assert [e["event"] for e in reversed(log)] == ["action.proposed", "action.approved", "action.executed"]
    assert [e["actor"] for e in reversed(log)] == ["olga", "sam", "system"]

    with pytest.raises(IntegrityError, match="append-only"), api_engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_log"))


def test_metrics_hubs_and_detector(api: TestClient, api_engine: Engine) -> None:
    _exception(api_engine)
    _exception(api_engine, failed=False, n=2)
    headers = login(api, "ada")
    summary = api.get("/metrics/summary", headers=headers).json()
    assert summary["shipments"] == 2
    assert summary["open_by_rule"] == {"delivery_failed": 1, "pickup_overdue": 1}

    hubs = {h["code"]: h for h in api.get("/hubs", headers=headers).json()}
    assert hubs["DEL-DC1"]["open_exceptions"] == 1
    assert hubs["BLR-DC1"]["shipments_here"] == 1

    r = api.post("/sim/detect", headers=headers)
    assert r.status_code == 200
    assert datetime.fromisoformat(r.json()["as_of"]) == T0 + timedelta(hours=2)  # the latest event, not the wall clock
