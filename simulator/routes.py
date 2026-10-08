"""Bus route geometries for mobile nodes.

Each route is a road-following polyline between real landmarks inside the
seeded zones, obtained from OpenStreetMap via osmnx (shortest path on the drive
network, using edge geometry so curves are kept). Results are cached as JSON in
simulator/route_cache/ so the simulator works offline afterwards.

IMPORTANT: these are representative corridor routes, NOT the official TSRTC
route numbers/stop lists. Route codes (CHNAM, ...) are our own labels.

If osmnx / the network is unavailable and no cache exists, we fall back to a
straight-line polyline through the waypoints and mark `source="waypoint-fallback"`.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from shared.geo import haversine_km

log = logging.getLogger(__name__)
CACHE_DIR = Path(__file__).parent / "route_cache"


@dataclass(frozen=True)
class RouteSpec:
    code: str
    name: str
    waypoints: tuple[tuple[float, float], ...]   # (lat, lon) landmarks to route through


ROUTE_SPECS: dict[str, RouteSpec] = {
    "CHNAM": RouteSpec("CHNAM", "Charminar - Nampally (via Madina, Afzalgunj)",
                       ((17.3616, 78.4747), (17.3688, 78.4735), (17.3735, 78.4745), (17.3917, 78.4690))),
    "MGBSEC": RouteSpec("MGBSEC", "MGBS - Secunderabad (via Koti, Musheerabad)",
                        ((17.3786, 78.4803), (17.3850, 78.4860), (17.4120, 78.4960), (17.4337, 78.5013))),
    "PGHTC": RouteSpec("PGHTC", "Punjagutta - Hitec City (via Jubilee Hills)",
                       ((17.4258, 78.4513), (17.4318, 78.4070), (17.4435, 78.3772))),
}


class Route:
    """A polyline with cumulative distance, so a bus can be placed by distance travelled."""

    def __init__(self, code: str, name: str, points: list[tuple[float, float]], source: str):
        self.code, self.name, self.points, self.source = code, name, points, source
        self.cum_m = [0.0]
        for a, b in zip(points, points[1:]):
            self.cum_m.append(self.cum_m[-1] + haversine_km(*a, *b) * 1000)
        self.length_m = self.cum_m[-1]

    def position_at(self, dist_m: float) -> tuple[float, float]:
        """(lat, lon) at `dist_m` along the route (clamped)."""
        d = max(0.0, min(self.length_m, dist_m))
        lo, hi = 0, len(self.cum_m) - 1
        while hi - lo > 1:                      # binary search for the segment
            mid = (lo + hi) // 2
            if self.cum_m[mid] <= d:
                lo = mid
            else:
                hi = mid
        seg = self.cum_m[hi] - self.cum_m[lo]
        f = 0.0 if seg == 0 else (d - self.cum_m[lo]) / seg
        a, b = self.points[lo], self.points[hi]
        return (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))


def _build_with_osmnx(spec: RouteSpec) -> list[tuple[float, float]]:
    import networkx as nx
    import osmnx as ox

    lats = [p[0] for p in spec.waypoints]
    lons = [p[1] for p in spec.waypoints]
    # graph bbox around the waypoints, padded ~1.5 km
    pad = 0.015
    # osmnx >= 2.0 bbox order: (left, bottom, right, top)
    G = ox.graph_from_bbox((min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad),
                           network_type="drive")
    nodes = ox.distance.nearest_nodes(G, X=lons, Y=lats)
    pts: list[tuple[float, float]] = []
    for u0, v0 in zip(nodes, nodes[1:]):
        path = nx.shortest_path(G, u0, v0, weight="length")
        for u, v in zip(path, path[1:]):
            data = min(G.get_edge_data(u, v).values(), key=lambda d: d.get("length", math.inf))
            geom = data.get("geometry")
            if geom is not None:
                coords = [(y, x) for x, y in geom.coords]
                # shapely geometry may be stored in either orientation
                if haversine_km(*coords[0], G.nodes[u]["y"], G.nodes[u]["x"]) > \
                   haversine_km(*coords[-1], G.nodes[u]["y"], G.nodes[u]["x"]):
                    coords.reverse()
            else:
                coords = [(G.nodes[u]["y"], G.nodes[u]["x"]), (G.nodes[v]["y"], G.nodes[v]["x"])]
            pts.extend(coords)
    # drop consecutive duplicates
    out = [pts[0]]
    for p in pts[1:]:
        if p != out[-1]:
            out.append(p)
    return out


def load_route(code: str, allow_download: bool = True) -> Route:
    spec = ROUTE_SPECS[code]
    cache = CACHE_DIR / f"{code}.json"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        return Route(code, spec.name, [tuple(p) for p in d["points"]], d["source"])
    if allow_download:
        try:
            pts = _build_with_osmnx(spec)
            CACHE_DIR.mkdir(exist_ok=True)
            cache.write_text(json.dumps({"code": code, "source": "osmnx-drive-network",
                                         "points": [[round(a, 6), round(b, 6)] for a, b in pts]}),
                             encoding="utf-8")
            log.info("route %s: %d points fetched via osmnx", code, len(pts))
            return Route(code, spec.name, pts, "osmnx-drive-network")
        except Exception as exc:  # network down, osmnx missing, no path...
            log.warning("route %s: osmnx failed (%s); using waypoint fallback", code, exc)
    return Route(code, spec.name, list(spec.waypoints), "waypoint-fallback")


if __name__ == "__main__":      # python -m simulator.routes  -> (re)build the cache
    logging.basicConfig(level=logging.INFO)
    for c in ROUTE_SPECS:
        r = load_route(c)
        print(f"{c}: {r.source}, {len(r.points)} pts, {r.length_m / 1000:.1f} km")
