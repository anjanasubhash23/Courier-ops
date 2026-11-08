"""A shipment's journey: the tracking events from booking to delivery (or loss, or return), with any fault applied."""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.db.models import EventType, FaultType
from core.simulator.config import Range, SimulatorConfig
from core.simulator.demand import Booking, after_pickup_cutoff, next_local_hour
from core.simulator.faults import FaultPlan, SimFault, index_at
from core.simulator.network import ROAD_ONLY, NetRoute, Network, RouteKey

FAILURE_REASONS = ("customer_unavailable", "address_not_found", "customer_refused")


@dataclass(frozen=True)
class SimEvent:
    event_type: EventType
    occurred_at: datetime
    hub: str | None = None
    route: RouteKey | None = None
    reason: str | None = None


def plan_journey(
    booking: Booking, network: Network, cfg: SimulatorConfig, rng: random.Random, plan: FaultPlan | None
) -> tuple[list[SimEvent], SimFault | None]:
    return _Journey(booking, network, cfg, rng, plan).run()


def _minutes(m: float) -> timedelta:
    return timedelta(minutes=round(m))


class _Journey:
    def __init__(
        self, booking: Booking, network: Network, cfg: SimulatorConfig, rng: random.Random, plan: FaultPlan | None
    ) -> None:
        self.b, self.network, self.cfg, self.rng, self.plan = booking, network, cfg, rng, plan
        self.timing = cfg.timing
        self.modes = network.modes_for(booking.service_level)
        self.events: list[SimEvent] = []
        self.fault: SimFault | None = None

    def _wants(self, fault_type: FaultType) -> bool:
        """Whether the plan asks for this fault and it hasn't been injected yet."""
        return self.plan is not None and self.plan.fault_type is fault_type and self.fault is None

    @property
    def _position(self) -> float:
        return self.plan.position if self.plan else 0.0

    @property
    def _fault_minutes(self) -> float:
        return self.plan.hours * 60 if self.plan else 0.0

    def _uniform(self, r: Range) -> float:
        return self.rng.uniform(r.low, r.high)

    def _emit(
        self,
        event_type: EventType,
        at: datetime,
        hub: str | None,
        route: RouteKey | None = None,
        reason: str | None = None,
    ) -> None:
        self.events.append(SimEvent(event_type, at, hub, route, reason))

    def _transit(self, route: NetRoute) -> timedelta:
        return _minutes(route.transit_minutes * self._uniform(self.timing.transit_jitter))

    def _next_delivery_slot(self, after: datetime) -> datetime:
        """The first delivery start (local) at or after `after`, plus up to an hour of loading."""
        return next_local_hour(after, self.timing.delivery_start_hour, self.cfg) + _minutes(self.rng.uniform(0, 60))

    def _path(self, origin: str, destination: str) -> list[NetRoute]:
        legs = self.network.service_path(origin, destination, self.b.service_level)
        if legs is None:
            raise ValueError(f"{self.b.tracking_number}: no route from {origin} to {destination}")
        return legs

    def run(self) -> tuple[list[SimEvent], SimFault | None]:
        b = self.b
        self._emit(EventType.BOOKED, b.booked_at, b.origin_hub)
        t = self._pickup()
        legs = self._path(b.origin_hub, b.destination_hub)
        stuck_site = index_at(self._position, len(legs) + 1)  # a departure hub, or the delivery center at the end
        leg_site = index_at(self._position, len(legs)) if legs else -1  # missed connections and losses hit a leg
        misroute = self._misroute_choice(legs) if self._wants(FaultType.MISROUTE) else None

        hub, i = b.origin_hub, 0
        while i < len(legs):
            leg = legs[i]
            depart = t + _minutes(self._uniform(self.timing.hub_dwell_minutes))
            if self._wants(FaultType.HUB_STUCK) and i == stuck_site:
                depart = self._delay(FaultType.HUB_STUCK, depart, hub, self._fault_minutes)
            if self._wants(FaultType.MISSED_CONNECTION) and i == leg_site:
                wait = self.cfg.faults.missed_connection.hours[leg.mode] * 60
                depart = self._delay(FaultType.MISSED_CONNECTION, depart, hub, wait, route=leg.key)
            if misroute is not None and self.fault is None and i == misroute[0]:
                wrong = misroute[1]
                self._emit(EventType.DEPARTED_HUB, depart, hub, route=wrong.key)
                details: dict[str, int | str] = {"intended_hub": leg.destination}
                self.fault = SimFault(FaultType.MISROUTE, depart, wrong.destination, wrong.key, details)
                t = depart + self._transit(wrong)
                hub = wrong.destination
                self._emit(EventType.ARRIVED_AT_HUB, t, hub)
                legs, i = self._path(hub, b.destination_hub), 0
                continue

            self._emit(EventType.DEPARTED_HUB, depart, hub, route=leg.key)
            if self._wants(FaultType.LOST) and i == leg_site:
                self.fault = SimFault(FaultType.LOST, depart, hub, leg.key)
                return self.events, self.fault
            t = depart + self._transit(leg)
            hub = leg.destination
            self._emit(EventType.ARRIVED_AT_HUB, t, hub)
            i += 1

        ready = t + _minutes(self._uniform(self.timing.hub_dwell_minutes))
        if self._wants(FaultType.HUB_STUCK):  # the remaining site is the delivery center itself
            ready = self._delay(FaultType.HUB_STUCK, ready, hub, self._fault_minutes)
        self._deliver(ready)
        return self.events, self.fault

    def _pickup(self) -> datetime:
        b = self.b
        at = b.booked_at + _minutes(self._uniform(self.timing.pickup_hours) * 60)
        if after_pickup_cutoff(at, b.booked_at, self.cfg):
            at = self._next_delivery_slot(at) + _minutes(self.rng.uniform(0, 120))
        if self._wants(FaultType.PICKUP_DELAY):
            at = self._delay(FaultType.PICKUP_DELAY, at, b.origin_hub, self._fault_minutes)
        self._emit(EventType.PICKED_UP, at, b.origin_hub)
        return at

    def _delay(
        self, fault_type: FaultType, planned: datetime, hub: str, minutes: float, route: RouteKey | None = None
    ) -> datetime:
        delay = _minutes(minutes)
        details: dict[str, int | str] = {"delay_minutes": int(delay.total_seconds() // 60)}
        self.fault = SimFault(fault_type, planned, hub, route, details)
        return planned + delay

    def _misroute_choice(self, legs: list[NetRoute]) -> tuple[int, NetRoute] | None:
        """(leg index, wrong route) pairs where the parcel can go astray and still be recovered; pick one."""
        on_path = {self.b.origin_hub} | {leg.destination for leg in legs}
        options = [
            (i, r)
            for i, leg in enumerate(legs)
            for r in self.network.routes_from(leg.origin, self.modes)
            if r.destination not in on_path and self.network.path(r.destination, self.b.destination_hub, self.modes)
        ]
        return options[index_at(self._position, len(options))] if options else None

    def _deliver(self, ready: datetime) -> None:
        b, hub = self.b, self.b.destination_hub
        ofd = self._next_delivery_slot(ready)
        failures = self.plan.failed_attempts if self.plan and self._wants(FaultType.FAILED_DELIVERY) else 0
        for attempt in range(failures):
            self._emit(EventType.OUT_FOR_DELIVERY, ofd, hub)
            failed_at = ofd + _minutes(self._uniform(self.timing.delivery_hours) * 60)
            if attempt == 0:
                self.fault = SimFault(FaultType.FAILED_DELIVERY, failed_at, hub, details={"failed_attempts": failures})
            self._emit(EventType.DELIVERY_ATTEMPT_FAILED, failed_at, hub, reason=self.rng.choice(FAILURE_REASONS))
            ofd = self._next_delivery_slot(failed_at)

        if failures >= self.cfg.max_delivery_attempts:
            self._emit(EventType.RTO_INITIATED, ofd, hub)
            back = self.network.path(hub, b.origin_hub, ROAD_ONLY)
            if back is not None:
                dwell = self.timing.hub_dwell_minutes
                minutes = sum(r.transit_minutes + (dwell.low + dwell.high) / 2 for r in back)
                returned = ofd + _minutes(minutes * self._uniform(self.timing.transit_jitter))
                self._emit(EventType.RETURNED_TO_ORIGIN, returned, b.origin_hub)
            return

        self._emit(EventType.OUT_FOR_DELIVERY, ofd, hub)
        self._emit(EventType.DELIVERED, ofd + _minutes(self._uniform(self.timing.delivery_hours) * 60), hub)
