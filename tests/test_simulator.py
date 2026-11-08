"""The simulator without a database: network paths, determinism, and the shape of generated journeys."""

from datetime import date, datetime, timedelta
from functools import cache
from itertools import pairwise
from typing import Any

import pytest
from pydantic import ValidationError

from core.db.models import EventType, FaultType, RouteMode, ServiceLevel
from core.db.seed import load_network
from core.simulator.config import SimulatorConfig, load_simulator_config
from core.simulator.network import Network
from core.simulator.run import SimShipment, simulate
from tests.conftest import ROOT

START = date(2026, 10, 5)  # a Monday
FAR_FUTURE = datetime.fromisoformat("2027-01-01T00:00:00+05:30")
TERMINAL = {EventType.DELIVERED, EventType.RETURNED_TO_ORIGIN}


@cache
def network() -> Network:
    return Network.from_config(load_network(ROOT / "config" / "hubs.yaml"))


@cache
def config() -> SimulatorConfig:
    return load_simulator_config(ROOT / "config" / "simulator.yaml")


def with_faults(**rates: float) -> SimulatorConfig:
    """The shipped config with every fault rate set to 0 except the ones given."""
    cfg = config()
    faults = {name: getattr(cfg.faults, name).model_copy(update={"rate": rates.get(name, 0.0)})
              for name in type(cfg.faults).model_fields}  # fmt: skip
    return cfg.model_copy(update={"faults": cfg.faults.model_copy(update=faults)})


@cache
def week() -> list[SimShipment]:
    """One week, run to completion."""
    return simulate(network(), config(), START, 7, FAR_FUTURE)


# --- config ---------------------------------------------------------------------------------------


def _raw_config() -> dict[str, Any]:
    return config().model_dump(mode="json")


def test_fault_rates_must_leave_room_for_no_fault() -> None:
    raw = _raw_config()
    raw["faults"]["lost"]["rate"] = 0.9
    with pytest.raises(ValidationError, match="sum to less than 1"):
        SimulatorConfig.model_validate(raw)


def test_ranges_must_be_ordered() -> None:
    raw = _raw_config()
    raw["timing"]["pickup_hours"] = [5, 1]
    with pytest.raises(ValidationError, match="above high"):
        SimulatorConfig.model_validate(raw)


def test_unknown_timezone_is_rejected() -> None:
    raw = _raw_config()
    raw["timezone"] = "Mars/Olympus"
    with pytest.raises(ValidationError):
        SimulatorConfig.model_validate(raw)


def test_unknown_hub_weights_are_rejected() -> None:
    cfg = config().model_copy(update={"demand": config().demand.model_copy(update={"hub_weights": {"XXX-DC1": 1}})})
    with pytest.raises(ValueError, match="XXX-DC1"):
        simulate(network(), cfg, START, 1)


# --- network --------------------------------------------------------------------------------------


def test_express_flies_where_it_can_and_standard_stays_on_the_road() -> None:
    express = network().service_path("BLR-DC1", "DEL-DC1", ServiceLevel.EXPRESS)
    standard = network().service_path("BLR-DC1", "DEL-DC1", ServiceLevel.STANDARD)
    assert express is not None and standard is not None
    assert [r.mode for r in express] == [RouteMode.ROAD, RouteMode.AIR, RouteMode.ROAD]
    assert all(r.mode is RouteMode.ROAD for r in standard)
    assert sum(r.transit_minutes for r in express) < sum(r.transit_minutes for r in standard)


def test_express_falls_back_to_road_without_flights() -> None:
    legs = network().service_path("CCU-DC1", "MAA-DC1", ServiceLevel.EXPRESS)
    assert legs is not None
    assert all(r.mode is RouteMode.ROAD for r in legs)


def test_paths_are_connected() -> None:
    for origin in network().delivery_centers:
        for destination in network().delivery_centers:
            legs = network().service_path(origin, destination, ServiceLevel.STANDARD)
            assert legs is not None, f"{origin} -> {destination}"
            hubs = [origin] + [r.destination for r in legs]
            assert hubs[-1] == destination
            assert all(r.origin == h for r, h in zip(legs, hubs, strict=False))
    assert network().path("BLR-DC1", "BLR-DC1") == []


def test_pincode_lookup_uses_the_longest_prefix() -> None:
    net = Network(network().hubs, [], {"56": "BLR-GW", "560": "BLR-DC1"})
    assert net.hub_for_pincode("560034") == "BLR-DC1"
    assert net.hub_for_pincode("561000") == "BLR-GW"
    assert net.hub_for_pincode("110001") is None


# --- determinism ----------------------------------------------------------------------------------


def test_same_seed_gives_identical_output() -> None:
    assert simulate(network(), config(), START, 7, FAR_FUTURE) == week()


def test_different_seed_gives_different_output() -> None:
    other = simulate(network(), config().model_copy(update={"seed": 7}), START, 7, FAR_FUTURE)
    assert other != week()


def test_a_day_is_the_same_whichever_range_it_is_simulated_in() -> None:
    wednesday = START + timedelta(days=2)
    alone = simulate(network(), config(), wednesday, 1, FAR_FUTURE)
    in_week = [s for s in week() if s.booking.booked_at.date() == wednesday]
    assert alone == in_week


# --- journeys -------------------------------------------------------------------------------------


def _check_journey(s: SimShipment) -> None:
    events, b = s.events, s.booking
    assert [e.event_type for e in events[:2]] == [EventType.BOOKED, EventType.PICKED_UP]
    assert all(a.occurred_at <= z.occurred_at for a, z in pairwise(events)), "out of order"
    hub = b.origin_hub
    for e in events:
        if e.event_type is EventType.DEPARTED_HUB:
            assert e.route is not None and e.route[0] == hub == e.hub
            hub = e.route[1]
        elif e.event_type is EventType.ARRIVED_AT_HUB:
            assert e.hub == hub, "arrived somewhere it wasn't heading"


def test_journeys_are_consistent() -> None:
    for s in week():
        _check_journey(s)


def test_journeys_without_faults_are_delivered_on_time() -> None:
    clean = [s for s in week() if s.fault is None]
    assert len(clean) > 1000
    for s in clean:
        last = s.events[-1]
        assert last.event_type is EventType.DELIVERED, s.booking.tracking_number
        assert last.occurred_at <= s.booking.promised_by, s.booking.tracking_number
        assert last.hub == s.booking.destination_hub


def test_every_fault_type_occurs() -> None:
    month = simulate(network(), config(), START, 30, FAR_FUTURE)
    seen = {s.fault.fault_type for s in month if s.fault}
    assert seen == set(FaultType)


@pytest.mark.parametrize("fault_type", list(FaultType))
def test_each_fault_leaves_its_mark(fault_type: FaultType) -> None:
    shipments = simulate(network(), with_faults(**{fault_type.value: 0.9}), START, 2, FAR_FUTURE)
    faulty = [s for s in shipments if s.fault]
    assert len(faulty) > 0.6 * len(shipments)  # misroute needs a detour to exist, so not every shipment gets one
    for s in faulty:
        assert s.fault is not None and s.fault.fault_type is fault_type
        _check_journey(s)
        types = [e.event_type for e in s.events]
        match fault_type:
            case FaultType.PICKUP_DELAY:
                pickup = s.events[1].occurred_at
                assert pickup - s.booking.booked_at >= timedelta(minutes=int(s.fault.details["delay_minutes"]))
            case FaultType.HUB_STUCK | FaultType.MISSED_CONNECTION:
                assert int(s.fault.details["delay_minutes"]) >= 12 * 60
                assert types[-1] is EventType.DELIVERED
            case FaultType.MISROUTE:
                visited = {e.hub for e in s.events if e.event_type is EventType.ARRIVED_AT_HUB}
                assert s.fault.hub in visited
                assert s.fault.hub != s.fault.details["intended_hub"]
                assert types[-1] is EventType.DELIVERED
            case FaultType.FAILED_DELIVERY:
                failures = types.count(EventType.DELIVERY_ATTEMPT_FAILED)
                assert failures == s.fault.details["failed_attempts"]
                expected = EventType.RETURNED_TO_ORIGIN if failures >= 3 else EventType.DELIVERED
                assert types[-1] is expected
            case FaultType.LOST:
                assert types[-1] is EventType.DEPARTED_HUB
                assert not TERMINAL & set(types)


def test_as_of_cuts_off_the_future() -> None:
    as_of = datetime.fromisoformat("2026-10-07T12:00:00+05:30")
    shipments = simulate(network(), config(), START, 7, as_of)
    assert shipments, "bookings before as_of are kept"
    assert all(s.booking.booked_at <= as_of for s in shipments)
    assert all(e.occurred_at <= as_of for s in shipments for e in s.events)
    assert all(s.fault.injected_at <= as_of for s in shipments if s.fault)
    in_flight = [s for s in shipments if s.events[-1].event_type not in TERMINAL]
    assert len(in_flight) > 100
    # The same shipments, just seen earlier.
    complete = {s.booking.tracking_number: s for s in week()}
    for s in shipments:
        assert s.events == complete[s.booking.tracking_number].events[: len(s.events)]
