"""What each approved action does. Runs only from core.actions.service.approve.

This is a simulated network, so most actions record what would be sent (a message to the hub, a customer
notification) rather than calling another system. Initiating a return changes the shipment itself.
Executors run in the approval transaction: if one raises, nothing it did is kept.
"""

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db.events import record_event
from core.db.models import Action, ActionType, EventType, Hub, Shipment, ShipmentException

Result = dict[str, str | int]
Executor = Callable[[Session, Action, ShipmentException, Shipment], Result]

NOTIFICATIONS = {
    "delay": "Your parcel {tracking} is delayed. We're working on it and will update you soon.",
    "failed_attempt": "We couldn't deliver {tracking} today. We'll try again on the next delivery round.",
    "returning": "Parcel {tracking} is being returned to the sender.",
    "lost": "We can't currently locate parcel {tracking} and have opened an investigation.",
}


def _hub_code(session: Session, hub_id: int | None) -> str:
    code = session.scalar(select(Hub.code).where(Hub.id == hub_id)) if hub_id else None
    return code or "unknown"


def _message(session: Session, action: Action, exc: ShipmentException, shipment: Shipment) -> Result:
    hub = _hub_code(session, exc.hub_id)
    texts = {
        ActionType.PRIORITY_PICKUP: f"Pickup for {shipment.tracking_number} escalated to the first round at {hub}.",
        ActionType.ESCALATE_TO_HUB: f"Escalated to {hub} operations: {action.params.get('note', '')}",
        ActionType.REROUTE: f"{hub} instructed to send {shipment.tracking_number} via {action.params.get('via_hub')}.",
        ActionType.OPEN_TRACE: f"Trace opened for {shipment.tracking_number}, last seen at {hub}.",
        ActionType.RESCHEDULE_DELIVERY: (
            f"Delivery of {shipment.tracking_number} moved to the {action.params.get('slot')} round."
        ),
    }
    return {"sent_to": hub, "message": texts[action.action_type]}


def _notify(session: Session, action: Action, exc: ShipmentException, shipment: Shipment) -> Result:
    template = action.params["template"]
    return {"sent_to": "customer", "message": NOTIFICATIONS[template].format(tracking=shipment.tracking_number)}


def _initiate_rto(session: Session, action: Action, exc: ShipmentException, shipment: Shipment) -> Result:
    # Simulated time: the return starts when the detector last saw the problem, not on the wall clock.
    event = record_event(
        session, shipment.id, EventType.RTO_INITIATED, exc.last_seen_at, hub_id=shipment.last_scan_hub_id
    )
    return {"tracking_event_id": event.id, "message": f"Return to origin started for {shipment.tracking_number}."}


EXECUTORS: dict[ActionType, Executor] = {
    ActionType.PRIORITY_PICKUP: _message,
    ActionType.ESCALATE_TO_HUB: _message,
    ActionType.REROUTE: _message,
    ActionType.OPEN_TRACE: _message,
    ActionType.RESCHEDULE_DELIVERY: _message,
    ActionType.NOTIFY_CUSTOMER: _notify,
    ActionType.INITIATE_RTO: _initiate_rto,
}
