"""Exception rules as SQL, evaluated against each shipment's state as of a point in (simulated) time.

State is rebuilt from tracking_events with occurred_at <= :as_of instead of read from the shipments
projection, so the detector can be replayed over history and gives the same answer it would have then.
"""

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field

from core.db.models import ExceptionRule, Severity

PositiveHours = Annotated[float, Field(gt=0)]


class _Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    severity: Severity
    enabled: bool = True


class HoursRule(_Rule):
    hours: PositiveHours


class TransitRule(_Rule):
    transit_factor: float = Field(ge=1)
    grace_minutes: int = Field(ge=0)


class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pickup_overdue: HoursRule
    stuck_at_hub: HoursRule
    stuck_at_delivery_center: HoursRule
    in_transit_overdue: TransitRule
    delivery_failed: _Rule
    misrouted: _Rule
    sla_breach: _Rule

    def rule(self, rule: ExceptionRule) -> _Rule:
        config: _Rule = getattr(self, rule.value)
        return config

    def enabled(self) -> list[ExceptionRule]:
        return [r for r in ExceptionRule if self.rule(r).enabled]


def load_rules(path: Path) -> RulesConfig:
    return RulesConfig.model_validate(yaml.safe_load(path.read_text()))


# Latest event per shipment as of :as_of, for shipments that hadn't finished by then.
#
# Candidates come from the shipments projection: a shipment delivered or returned before :as_of can't hit any
# rule, and a finished shipment stays finished, so only live shipments and ones that finished after :as_of are
# looked at. Each candidate's latest event then comes from ix_tracking_events_shipment_id_occurred_at.
# (Rebuilding state from every event with DISTINCT ON was ~10x slower at 45k shipments: eval/db/results.md.)
_STATE = """
WITH candidates AS (
    SELECT s.id AS shipment_id, s.booked_at, s.promised_by, s.origin_hub_id, s.destination_hub_id
    FROM shipments s
    WHERE s.booked_at <= :as_of AND s.status NOT IN ('delivered', 'returned')
    UNION ALL
    SELECT s.id, s.booked_at, s.promised_by, s.origin_hub_id, s.destination_hub_id
    FROM shipments s
    WHERE s.status IN ('delivered', 'returned') AND s.last_event_at > :as_of AND s.booked_at <= :as_of
),
state AS (
    SELECT c.*, last.event_type, last.hub_id, last.route_id, last.occurred_at AS last_event_at
    FROM candidates c
    CROSS JOIN LATERAL (
        SELECT e.event_type, e.hub_id, e.route_id, e.occurred_at
        FROM tracking_events e
        WHERE e.shipment_id = c.shipment_id AND e.occurred_at <= :as_of
        ORDER BY e.occurred_at DESC, e.id DESC
        LIMIT 1
    ) last
    WHERE last.event_type NOT IN ('delivered', 'returned_to_origin')
)
"""

# Each rule: SELECT shipment_id, hub_id, details (jsonb) FROM state ...
_RULES: dict[ExceptionRule, str] = {
    ExceptionRule.PICKUP_OVERDUE: """
        SELECT shipment_id, origin_hub_id AS hub_id,
               jsonb_build_object(
                   'hours_since_booking',
                   round(extract(epoch FROM :as_of - booked_at) / 3600, 1)) AS details
        FROM state
        WHERE event_type = 'booked' AND :as_of - booked_at > (:pickup_overdue_hours * interval '1 hour')
    """,
    ExceptionRule.STUCK_AT_HUB: """
        SELECT shipment_id, hub_id,
               jsonb_build_object(
                   'hours_since_scan', round(extract(epoch FROM :as_of - last_event_at) / 3600, 1)) AS details
        FROM state
        WHERE event_type IN ('arrived_at_hub', 'picked_up') AND hub_id <> destination_hub_id
          AND :as_of - last_event_at > (:stuck_at_hub_hours * interval '1 hour')
    """,
    ExceptionRule.STUCK_AT_DELIVERY_CENTER: """
        SELECT shipment_id, hub_id,
               jsonb_build_object(
                   'hours_since_scan', round(extract(epoch FROM :as_of - last_event_at) / 3600, 1)) AS details
        FROM state
        WHERE event_type IN ('arrived_at_hub', 'picked_up') AND hub_id = destination_hub_id
          AND :as_of - last_event_at > (:stuck_at_delivery_center_hours * interval '1 hour')
    """,
    ExceptionRule.IN_TRANSIT_OVERDUE: """
        SELECT st.shipment_id, st.hub_id,
               jsonb_build_object(
                   'route_id', r.id,
                   'planned_minutes', r.transit_minutes,
                   'minutes_in_transit', round(extract(epoch FROM :as_of - st.last_event_at) / 60)) AS details
        FROM state st JOIN routes r ON r.id = st.route_id
        WHERE st.event_type = 'departed_hub'
          AND :as_of > st.last_event_at + (r.transit_minutes * :in_transit_overdue_transit_factor
                                   + :in_transit_overdue_grace_minutes) * interval '1 minute'
    """,
    ExceptionRule.DELIVERY_FAILED: """
        SELECT shipment_id, hub_id, '{}'::jsonb AS details
        FROM state
        WHERE event_type = 'delivery_attempt_failed'
    """,
    ExceptionRule.MISROUTED: """
        SELECT st.shipment_id, st.hub_id, jsonb_build_object('wrong_hub_id', wrong.hub_id) AS details
        FROM state st
        JOIN LATERAL (
            SELECT e.hub_id
            FROM tracking_events e JOIN hubs h ON h.id = e.hub_id
            WHERE e.shipment_id = st.shipment_id AND e.event_type = 'arrived_at_hub' AND e.occurred_at <= :as_of
            GROUP BY e.hub_id, h.hub_type
            HAVING count(*) > 1 OR (h.hub_type = 'delivery_center' AND e.hub_id <> st.destination_hub_id)
            LIMIT 1
        ) wrong ON true
    """,
    ExceptionRule.SLA_BREACH: """
        SELECT shipment_id, hub_id,
               jsonb_build_object('hours_late', round(extract(epoch FROM :as_of - promised_by) / 3600, 1)) AS details
        FROM state
        WHERE :as_of > promised_by
    """,
}


def detection_sql(rules: list[ExceptionRule]) -> str:
    """One statement returning (shipment_id, rule, hub_id, details) for every rule hit."""
    selects = [
        f"SELECT q.shipment_id, q.hub_id, q.details, '{rule.value}' AS rule FROM ({_RULES[rule]}) q" for rule in rules
    ]
    return _STATE + "\nUNION ALL\n".join(selects)


def detection_params(config: RulesConfig) -> dict[str, float]:
    """Bind parameters named `<rule>_<field>`, e.g. :stuck_at_hub_hours."""
    params: dict[str, float] = {}
    for rule in ExceptionRule:
        for field, value in config.rule(rule).model_dump(exclude={"severity", "enabled"}).items():
            params[f"{rule.value}_{field}"] = value
    return params
