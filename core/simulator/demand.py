"""Bookings: who sends what, from where, to where, and when."""

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from core.db.models import ServiceLevel
from core.simulator.config import SimulatorConfig
from core.simulator.network import Network


@dataclass(frozen=True)
class Booking:
    tracking_number: str
    service_level: ServiceLevel
    origin_hub: str
    destination_hub: str
    origin_pincode: str
    destination_pincode: str
    weight_grams: int
    booked_at: datetime
    promised_by: datetime

    @property
    def is_local(self) -> bool:
        return self.origin_hub == self.destination_hub


def bookings_for_day(day: date, network: Network, cfg: SimulatorConfig) -> list[Booking]:
    """All bookings made on one local day, in booking order.

    Each day has its own random stream, so a day's bookings don't depend on which other days are simulated.
    """
    rng = random.Random(f"{cfg.seed}:demand:{day.isoformat()}")
    d = cfg.demand
    unknown = set(d.hub_weights) - set(network.delivery_centers)
    if unknown:
        raise ValueError(f"demand.hub_weights names hubs that aren't active delivery centers: {sorted(unknown)}")
    hubs = network.delivery_centers
    weights = [d.hub_weights.get(h, 1.0) for h in hubs]

    volume = d.shipments_per_day * d.weekday_factor[day.weekday()] * rng.uniform(1 - d.daily_noise, 1 + d.daily_noise)
    open_minutes = (d.booking_hours.high - d.booking_hours.low) * 60
    day_start = datetime.combine(day, time(), cfg.tz)

    drafts = []
    for _ in range(round(volume)):
        booked_at = day_start + timedelta(hours=d.booking_hours.low, minutes=round(rng.uniform(0, open_minutes)))
        origin = rng.choices(hubs, weights)[0]
        if rng.random() < d.local_share or len(hubs) == 1:
            destination = origin
        else:
            others = [(h, w) for h, w in zip(hubs, weights, strict=True) if h != origin]
            destination = rng.choices([h for h, _ in others], [w for _, w in others])[0]
        express = rng.random() < d.express_share
        weight = rng.lognormvariate(math.log(d.weight_grams.median), d.weight_grams.sigma)
        drafts.append(
            (
                booked_at,
                origin,
                destination,
                ServiceLevel.EXPRESS if express else ServiceLevel.STANDARD,
                _pincode(rng, network.prefixes_of(origin)),
                _pincode(rng, network.prefixes_of(destination)),
                min(max(round(weight), d.weight_grams.min), d.weight_grams.max),
            )
        )

    drafts.sort(key=lambda b: b[0])  # stable: equal times keep generation order
    bookings = []
    for n, (booked_at, origin, destination, level, from_pin, to_pin, grams) in enumerate(drafts, start=1):
        sla = getattr(cfg.sla_days, level.value)
        sla_day = day + timedelta(days=sla.local if origin == destination else sla.intercity)
        promised_by = max(
            datetime.combine(sla_day, time(cfg.promise_hour), cfg.tz),
            _achievable_promise(booked_at, origin, destination, level, network, cfg),
        )
        bookings.append(
            Booking(
                tracking_number=f"CO{day:%y%m%d}{n:05d}",
                service_level=level,
                origin_hub=origin,
                destination_hub=destination,
                origin_pincode=from_pin,
                destination_pincode=to_pin,
                weight_grams=grams,
                booked_at=booked_at,
                promised_by=promised_by,
            )
        )
    return bookings


def next_local_hour(after: datetime, hour: int, cfg: SimulatorConfig) -> datetime:
    """The first `hour`:00 local time at or after `after`."""
    local = after.astimezone(cfg.tz)
    at = datetime.combine(local.date(), time(hour), cfg.tz)
    return at if at >= local else at + timedelta(days=1)


def after_pickup_cutoff(pickup: datetime, booked_at: datetime, cfg: SimulatorConfig) -> bool:
    """Whether a pickup falls after the last pickup time on the booking day, so it moves to the next morning."""
    cutoff = datetime.combine(booked_at.astimezone(cfg.tz).date(), time(cfg.timing.last_pickup_hour), cfg.tz)
    return pickup >= cutoff


def _achievable_promise(
    booked_at: datetime, origin: str, destination: str, level: ServiceLevel, network: Network, cfg: SimulatorConfig
) -> datetime:
    """The latest a fault-free journey can take, using the slow end of every timing range.

    Pushes the promise out where the configured SLA can't be met (e.g. express on a lane with no flights),
    so a late delivery always means something went wrong.
    """
    t = cfg.timing
    legs = network.service_path(origin, destination, level)
    if legs is None:
        raise ValueError(f"no route from {origin} to {destination}")
    pickup = booked_at + timedelta(hours=t.pickup_hours.high)
    if after_pickup_cutoff(pickup, booked_at, cfg):
        pickup = next_local_hour(pickup, t.delivery_start_hour, cfg) + timedelta(hours=3)  # loading + pickup round
    travel = sum(t.hub_dwell_minutes.high + r.transit_minutes * t.transit_jitter.high for r in legs)
    ready = pickup + timedelta(minutes=travel + t.hub_dwell_minutes.high + 1)  # +1: rounding to whole minutes
    attempt = next_local_hour(ready, t.delivery_start_hour, cfg) + timedelta(hours=1 + t.delivery_hours.high)
    return next_local_hour(attempt, cfg.promise_hour, cfg)


def _pincode(rng: random.Random, prefixes: list[str]) -> str:
    prefix = rng.choice(prefixes)
    return prefix + "".join(str(rng.randrange(10)) for _ in range(6 - len(prefix)))
