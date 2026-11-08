from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db.models import Hub, HubServiceArea, Route
from core.db.seed import NetworkConfig, load_network, seed_network
from tests.conftest import ROOT

CONFIG = ROOT / "config" / "hubs.yaml"


def _network(**changes: Any) -> dict[str, Any]:
    hub = {"name": "x", "city": "x", "state": "x", "lat": 12.9, "lon": 77.6}
    raw: dict[str, Any] = {
        "hubs": [
            {**hub, "code": "BLR-GW", "type": "gateway"},
            {**hub, "code": "BLR-DC1", "type": "delivery_center", "pincode_prefixes": ["560"]},
        ],
        "routes": [{"from": "BLR-GW", "to": "BLR-DC1", "mode": "road", "distance_km": 40, "transit_minutes": 120}],
    }
    raw.update(changes)
    return raw


def test_shipped_config_is_valid() -> None:
    network = load_network(CONFIG)
    assert len(network.hubs) == 13
    assert sum(len(network.legs(r)) for r in network.routes) == 36


def test_minimal_network_is_valid() -> None:
    NetworkConfig.model_validate(_network())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"routes": [{"from": "BLR-GW", "to": "XXX-GW", "mode": "road", "distance_km": 1, "transit_minutes": 1}]},
         "unknown hub"),
        ({"routes": [{"from": "BLR-GW", "to": "BLR-GW", "mode": "air", "distance_km": 1, "transit_minutes": 1}]},
         "same hub"),
        ({"routes": [{"from": "BLR-GW", "to": "BLR-DC1", "mode": "road", "distance_km": 1, "transit_minutes": 1},
                     {"from": "BLR-DC1", "to": "BLR-GW", "mode": "road", "distance_km": 1, "transit_minutes": 1}]},
         "duplicate route legs"),
    ],
)  # fmt: skip
def test_invalid_routes(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        NetworkConfig.model_validate(_network(**changes))


def test_invalid_hubs() -> None:
    raw = _network()
    raw["hubs"][0]["pincode_prefixes"] = ["560"]
    with pytest.raises(ValidationError, match="only delivery centers"):
        NetworkConfig.model_validate(raw)

    raw = _network()
    del raw["hubs"][1]["pincode_prefixes"]
    with pytest.raises(ValidationError, match="needs pincode_prefixes"):
        NetworkConfig.model_validate(raw)

    raw = _network()
    raw["hubs"].append({**raw["hubs"][1], "code": "BLR-DC2"})
    with pytest.raises(ValidationError, match="more than one hub"):
        NetworkConfig.model_validate(raw)


@pytest.mark.postgres
def test_seeding_is_idempotent(session: Session) -> None:
    network = load_network(CONFIG)
    first = seed_network(session, network)
    ids = session.execute(select(Hub.code, Hub.id)).all()

    second = seed_network(session, network)

    assert first == second == {"hubs": 13, "service_areas": 8, "routes": 36}
    assert session.execute(select(Hub.code, Hub.id)).all() == ids
    assert len(session.scalars(select(Route.id)).all()) == 36
    assert len(session.scalars(select(HubServiceArea.pincode_prefix)).all()) == 8


@pytest.mark.postgres
def test_reseeding_updates_and_deactivates(session: Session) -> None:
    seed_network(session, NetworkConfig.model_validate(_network()))
    changed = _network()
    changed["routes"][0]["transit_minutes"] = 150
    changed["routes"][0]["bidirectional"] = False
    changed["hubs"].append({**changed["hubs"][0], "code": "MAA-SC", "type": "sort_center"})

    seed_network(session, NetworkConfig.model_validate(changed))

    routes = {(r.transit_minutes, r.is_active) for r in session.scalars(select(Route))}
    assert routes == {(150, True), (120, False)}  # reverse leg dropped from config -> deactivated

    seed_network(session, NetworkConfig.model_validate(_network()))
    active = dict(session.execute(select(Hub.code, Hub.is_active)).all())
    assert active == {"BLR-GW": True, "BLR-DC1": True, "MAA-SC": False}
