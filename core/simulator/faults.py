"""Which fault (if any) a shipment gets, and the record of what was injected."""

import random
from dataclasses import dataclass, field
from datetime import datetime

from core.db.models import FaultType
from core.simulator.config import SimulatorConfig
from core.simulator.network import RouteKey


@dataclass(frozen=True)
class FaultPlan:
    """A fault to inject. `position` in [0, 1) picks which hub or leg of the journey it hits."""

    fault_type: FaultType
    position: float
    hours: float = 0.0
    failed_attempts: int = 0


@dataclass(frozen=True)
class SimFault:
    """What was actually injected; stored in sim_faults as ground truth."""

    fault_type: FaultType
    injected_at: datetime
    hub: str | None
    route: RouteKey | None = None
    details: dict[str, int | str] = field(default_factory=dict)


def choose_fault(rng: random.Random, cfg: SimulatorConfig) -> FaultPlan | None:
    # Always draw the same number of values, so a rate change shifts which shipments get a fault
    # but not the rest of each shipment's random journey.
    roll, position, magnitude, attempts_roll = rng.random(), rng.random(), rng.random(), rng.random()

    cumulative = 0.0
    for fault_type in FaultType:  # fixed order
        cumulative += getattr(cfg.faults, fault_type.value).rate
        if roll < cumulative:
            break
    else:
        return None

    f = cfg.faults
    match fault_type:
        case FaultType.PICKUP_DELAY | FaultType.HUB_STUCK:
            hours = getattr(f, fault_type.value).hours
            return FaultPlan(fault_type, position, hours=hours.low + magnitude * (hours.high - hours.low))
        case FaultType.FAILED_DELIVERY:
            weights = f.failed_delivery.attempts_weights
            threshold, n = attempts_roll * sum(weights), 0
            while threshold >= weights[n] and n < len(weights) - 1:
                threshold -= weights[n]
                n += 1
            return FaultPlan(fault_type, position, failed_attempts=n + 1)
        case _:
            return FaultPlan(fault_type, position)


def index_at(position: float, count: int) -> int:
    """Map a position in [0, 1) to one of `count` slots."""
    return min(int(position * count), count - 1)
