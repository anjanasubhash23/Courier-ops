"""The network as the simulator sees it: hubs, routes and pincodes by code, plus shortest paths.

Everything is sorted by code so iteration order, and therefore the simulation, is deterministic.
"""

import heapq
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from core.db.models import Hub, HubServiceArea, HubType, Route, RouteMode, ServiceLevel
from core.db.seed import NetworkConfig

ROAD_ONLY = frozenset({RouteMode.ROAD})
ANY_MODE = frozenset(RouteMode)

# Added per hop when choosing paths, so a path with fewer handovers wins when transit times are close.
HOP_PENALTY_MINUTES = 120

RouteKey = tuple[str, str, RouteMode]  # (origin code, destination code, mode)


@dataclass(frozen=True)
class NetRoute:
    origin: str
    destination: str
    mode: RouteMode
    transit_minutes: int

    @property
    def key(self) -> RouteKey:
        return (self.origin, self.destination, self.mode)


class Network:
    def __init__(self, hubs: dict[str, HubType], routes: Iterable[NetRoute], service_areas: dict[str, str]) -> None:
        self.hubs = dict(sorted(hubs.items()))
        self.service_areas = dict(sorted(service_areas.items()))
        self._out: dict[str, list[NetRoute]] = {code: [] for code in self.hubs}
        for r in sorted(routes, key=lambda r: r.key):
            self._out[r.origin].append(r)
        self.delivery_centers = [c for c, t in self.hubs.items() if t is HubType.DELIVERY_CENTER]

    @classmethod
    def from_config(cls, config: NetworkConfig) -> "Network":
        routes = [NetRoute(o, d, r.mode, r.transit_minutes) for r in config.routes for o, d in NetworkConfig.legs(r)]
        areas = {p: h.code for h in config.hubs for p in h.pincode_prefixes}
        return cls({h.code: h.type for h in config.hubs}, routes, areas)

    @classmethod
    def from_db(cls, session: Session) -> "Network":
        """Active hubs and routes only."""
        hubs = dict(session.execute(select(Hub.code, Hub.hub_type).where(Hub.is_active)).all())
        origin, destination = aliased(Hub), aliased(Hub)
        rows = session.execute(
            select(origin.code, destination.code, Route.mode, Route.transit_minutes)
            .join(origin, Route.origin_hub_id == origin.id)
            .join(destination, Route.destination_hub_id == destination.id)
            .where(Route.is_active, origin.is_active, destination.is_active)
        ).all()
        areas = dict(
            session.execute(select(HubServiceArea.pincode_prefix, Hub.code).join(Hub).where(Hub.is_active)).all()
        )
        return cls(hubs, [NetRoute(*row) for row in rows], areas)

    def prefixes_of(self, hub: str) -> list[str]:
        return [p for p, h in self.service_areas.items() if h == hub]

    def hub_for_pincode(self, pincode: str) -> str | None:
        """The delivery center whose prefix matches the most leading digits."""
        for length in range(len(pincode), 0, -1):
            if hub := self.service_areas.get(pincode[:length]):
                return hub
        return None

    def routes_from(self, hub: str, modes: frozenset[RouteMode] = ANY_MODE) -> list[NetRoute]:
        return [r for r in self._out[hub] if r.mode in modes]

    def path(self, origin: str, destination: str, modes: frozenset[RouteMode] = ANY_MODE) -> list[NetRoute] | None:
        """Fastest sequence of routes (Dijkstra), or None if unreachable. `[]` when origin == destination."""
        best: dict[str, int] = {origin: 0}
        previous: dict[str, NetRoute] = {}
        queue: list[tuple[int, str]] = [(0, origin)]
        while queue:
            cost, hub = heapq.heappop(queue)
            if hub == destination:
                legs: list[NetRoute] = []
                while hub != origin:
                    legs.append(previous[hub])
                    hub = previous[hub].origin
                return legs[::-1]
            if cost > best[hub]:
                continue
            for r in self.routes_from(hub, modes):
                new_cost = cost + r.transit_minutes + HOP_PENALTY_MINUTES
                if new_cost < best.get(r.destination, new_cost + 1):
                    best[r.destination] = new_cost
                    previous[r.destination] = r
                    heapq.heappush(queue, (new_cost, r.destination))
        return None

    def service_path(self, origin: str, destination: str, level: ServiceLevel) -> list[NetRoute] | None:
        """Express may fly; standard goes by road. Express falls back to road where there's no air option."""
        if level is ServiceLevel.EXPRESS and (legs := self.path(origin, destination, ANY_MODE)) is not None:
            return legs
        return self.path(origin, destination, ROAD_ONLY)

    @staticmethod
    def modes_for(level: ServiceLevel) -> frozenset[RouteMode]:
        return ANY_MODE if level is ServiceLevel.EXPRESS else ROAD_ONLY
