"""Initial Hyderabad zone definitions (seed data).

The first four are the priority corridors named in the GHMC / NCAP action
plan for Hyderabad; the last two are monitoring-station areas commonly
reported among the city's higher-AQI locations.

NOTE: coordinates are hand-placed ROUGH landmark positions (good to a few
hundred metres), good enough for nearest-zone lookup and simulation. Verify
against the official corridor definitions before any real deployment.

A zone is a polyline (`path`) plus a `radius_km` buffer. A point-like zone has
one vertex; a corridor has several. Distance to a zone = distance to its
nearest segment, so GPS fixes anywhere along a long corridor resolve to it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .geo import haversine_km, point_segment_km


@dataclass(frozen=True)
class Zone:
    zone_id: str                       # short code, also used inside device IDs
    name: str
    path: tuple[tuple[float, float], ...]   # (lat, lon) vertices
    radius_km: float                   # buffer around the path that belongs to the zone
    source_context: str                # free text, used by the source-hypothesis heuristic
    source_tags: tuple[str, ...] = field(default_factory=tuple)  # machine-readable subset

    @property
    def centroid(self) -> tuple[float, float]:
        lats = [p[0] for p in self.path]
        lons = [p[1] for p in self.path]
        return (sum(lats) / len(lats), sum(lons) / len(lons))

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(min_lat, min_lon, max_lat, max_lon), padded by ~radius."""
        pad = self.radius_km / 111.0
        lats = [p[0] for p in self.path]
        lons = [p[1] for p in self.path]
        return (min(lats) - pad, min(lons) - pad, max(lats) + pad, max(lons) + pad)

    def distance_km(self, lat: float, lon: float) -> float:
        if len(self.path) == 1:
            return haversine_km(lat, lon, *self.path[0])
        return min(
            point_segment_km(lat, lon, a, b) for a, b in zip(self.path, self.path[1:])
        )

    def contains(self, lat: float, lon: float) -> bool:
        return self.distance_km(lat, lon) <= self.radius_km


ZONES: tuple[Zone, ...] = (
    Zone(
        zone_id="PGPT",
        name="Punjagutta-Patancheru corridor",
        path=((17.4258, 78.4513),   # Punjagutta
              (17.4375, 78.4483),   # Ameerpet
              (17.4948, 78.3996),   # Kukatpally
              (17.4969, 78.3586),   # Miyapur
              (17.5333, 78.2646)),  # Patancheru
        radius_km=1.5,
        source_context="traffic corridor: heavy mixed vehicular traffic (buses, trucks, "
                       "two-wheelers) on a long arterial, with metro/flyover construction sections",
        source_tags=("traffic", "construction"),
    ),
    Zone(
        zone_id="CHNAM",
        name="Charminar-Nampally corridor",
        path=((17.3616, 78.4747),   # Charminar
              (17.3688, 78.4735),   # Madina
              (17.3735, 78.4745),   # Afzalgunj
              (17.3917, 78.4690)),  # Nampally
        radius_km=0.9,
        source_context="dense old-city traffic corridor: congested narrow roads, markets, "
                       "street-level combustion (vehicles, roadside cooking)",
        source_tags=("traffic", "dense_market"),
    ),
    Zone(
        zone_id="HITEC",
        name="Hitec City",
        path=((17.4435, 78.3772), (17.4486, 78.3908)),   # Hitec City stn -> Madhapur
        radius_km=2.0,
        source_context="IT-corridor traffic plus ongoing high-rise and road construction "
                       "(dust-prone), commuter rush-hour peaks",
        source_tags=("traffic", "construction"),
    ),
    Zone(
        zone_id="MGBSEC",
        name="MGBS-Secunderabad corridor",
        path=((17.3786, 78.4803),   # MGBS bus station
              (17.3850, 78.4860),   # Koti
              (17.4120, 78.4960),   # Musheerabad
              (17.4337, 78.5013)),  # Secunderabad
        radius_km=1.2,
        source_context="traffic corridor with major bus terminals (diesel bus idling) "
                       "and heavy through-traffic",
        source_tags=("traffic",),
    ),
    Zone(
        zone_id="SANATH",
        name="Sanathnagar",
        path=((17.4565, 78.4430),),
        radius_km=2.0,
        source_context="industrial estate adjoining a busy road junction: "
                       "industrial emissions mixed with traffic",
        source_tags=("industrial", "traffic"),
    ),
    Zone(
        zone_id="IDAPASH",
        name="IDA Pashamylaram",
        path=((17.5330, 78.1910),),
        radius_km=3.0,
        source_context="industrial development area (pharma/chemical/manufacturing units, "
                       "boilers, open burning episodes); little residential traffic",
        source_tags=("industrial",),
    ),
)

ZONES_BY_ID = {z.zone_id: z for z in ZONES}


def get_zone(zone_id: str) -> Optional[Zone]:
    return ZONES_BY_ID.get(zone_id)


def nearest_zone(lat: float, lon: float, max_km: Optional[float] = None) -> Optional[Zone]:
    """Zone whose path is closest to (lat, lon).

    Returns None if the point is farther than the zone's own radius (default) or
    than `max_km` (if given), so out-of-city points are not silently assigned.
    """
    best, best_d = None, float("inf")
    for z in ZONES:
        d = z.distance_km(lat, lon)
        if d < best_d:
            best, best_d = z, d
    if best is None:
        return None
    limit = max_km if max_km is not None else best.radius_km
    return best if best_d <= limit else None


def zone_code_from_device_id(device_id: str) -> Optional[str]:
    """ZONE_HITEC_01 -> 'HITEC' (fixed nodes only; bus nodes have a route code instead)."""
    parts = device_id.split("_")
    return parts[1] if len(parts) == 3 and parts[0] == "ZONE" else None
