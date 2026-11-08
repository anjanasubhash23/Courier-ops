"""Load the network from config/hubs.yaml and upsert it into the database."""

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.db.models import Hub, HubServiceArea, HubType, Route, RouteMode


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HubConfig(_Strict):
    code: str = Field(pattern=r"^[A-Z]{3}-[A-Z]+[0-9]*$", max_length=16)
    name: str
    city: str
    state: str
    type: HubType
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    pincode_prefixes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _prefixes_only_on_delivery_centers(self) -> Self:
        if self.type is HubType.DELIVERY_CENTER and not self.pincode_prefixes:
            raise ValueError(f"{self.code}: a delivery_center needs pincode_prefixes")
        if self.type is not HubType.DELIVERY_CENTER and self.pincode_prefixes:
            raise ValueError(f"{self.code}: only delivery centers serve pincodes")
        return self


class RouteConfig(_Strict):
    model_config = ConfigDict(populate_by_name=True)

    origin: str = Field(alias="from")
    destination: str = Field(alias="to")
    mode: RouteMode
    distance_km: int = Field(gt=0)
    transit_minutes: int = Field(gt=0)
    bidirectional: bool = True


class NetworkConfig(_Strict):
    hubs: tuple[HubConfig, ...]
    routes: tuple[RouteConfig, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        codes = [h.code for h in self.hubs]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate hub codes")
        prefixes = [p for h in self.hubs for p in h.pincode_prefixes]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("a pincode prefix is assigned to more than one hub")
        known = set(codes)
        legs = [(o, d, r.mode) for r in self.routes for o, d in self.legs(r)]
        for origin, destination, _ in legs:
            if origin not in known or destination not in known:
                raise ValueError(f"route {origin}->{destination} uses an unknown hub")
            if origin == destination:
                raise ValueError(f"route {origin}->{destination} starts and ends at the same hub")
        if len(legs) != len(set(legs)):
            raise ValueError("duplicate route legs")
        return self

    @staticmethod
    def legs(route: RouteConfig) -> list[tuple[str, str]]:
        forward = (route.origin, route.destination)
        return [forward, forward[::-1]] if route.bidirectional else [forward]


def load_network(path: Path) -> NetworkConfig:
    return NetworkConfig.model_validate(yaml.safe_load(path.read_text()))


def seed_network(session: Session, network: NetworkConfig) -> dict[str, int]:
    """Upsert hubs, service areas and routes. Hubs and routes not in the config are deactivated, not deleted,
    because shipments and events may reference them."""
    for h in network.hubs:
        values = {
            "code": h.code,
            "name": h.name,
            "city": h.city,
            "state": h.state,
            "hub_type": h.type,
            "latitude": h.lat,
            "longitude": h.lon,
            "is_active": True,
        }
        stmt = insert(Hub).values(values)
        updated = {c: stmt.excluded[c] for c in values if c != "code"}
        session.execute(stmt.on_conflict_do_update(index_elements=[Hub.code], set_=updated))
    codes = [h.code for h in network.hubs]
    session.execute(update(Hub).where(Hub.code.not_in(codes)).values(is_active=False))
    hub_ids: dict[str, int] = dict(session.execute(select(Hub.code, Hub.id)).all())

    areas = [{"pincode_prefix": p, "hub_id": hub_ids[h.code]} for h in network.hubs for p in h.pincode_prefixes]
    session.execute(delete(HubServiceArea))
    if areas:
        session.execute(insert(HubServiceArea), areas)

    route_ids: list[int] = []
    for r in network.routes:
        for origin, destination in network.legs(r):
            route = insert(Route).values(
                origin_hub_id=hub_ids[origin],
                destination_hub_id=hub_ids[destination],
                mode=r.mode,
                distance_km=r.distance_km,
                transit_minutes=r.transit_minutes,
                is_active=True,
            )
            upsert = route.on_conflict_do_update(
                constraint="uq_routes_origin_destination_mode",
                set_={c: route.excluded[c] for c in ("distance_km", "transit_minutes", "is_active")},
            ).returning(Route.id)
            route_ids.append(session.execute(upsert).scalar_one())
    session.execute(update(Route).where(Route.id.not_in(route_ids)).values(is_active=False))

    return {"hubs": len(network.hubs), "service_areas": len(areas), "routes": len(route_ids)}
