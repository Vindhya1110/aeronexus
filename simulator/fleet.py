"""Default simulated fleet and the time-ordered emission loop."""
from __future__ import annotations

import heapq
from datetime import datetime, timedelta
from typing import Iterator

from shared.zones import ZONES_BY_ID

from .faults import FaultConfig
from .nodes import BusNode, Emission, FixedNode, Node, ZoneField
from .routes import load_route

# (device_id, zone_id, index of the zone path vertex to sit at, offset deg lat/lon ~ tens of m)
FIXED_SITES = [
    ("ZONE_PGPT_01", "PGPT", 1, (0.0002, -0.0001)),     # Ameerpet end
    ("ZONE_PGPT_02", "PGPT", 3, (-0.0001, 0.0002)),     # Miyapur end
    ("ZONE_CHNAM_01", "CHNAM", 1, (0.0001, 0.0001)),
    ("ZONE_HITEC_01", "HITEC", 0, (0.0002, 0.0002)),
    ("ZONE_MGBSEC_01", "MGBSEC", 2, (0.0001, -0.0002)),
    ("ZONE_SANATH_01", "SANATH", 0, (0.0002, 0.0001)),
    ("ZONE_IDAPASH_01", "IDAPASH", 0, (-0.0002, 0.0001)),
]
# (device_id, route code, start fraction along route)
BUS_SITES = [
    ("BUS_CHNAM_01", "CHNAM", 0.1),
    ("BUS_CHNAM_02", "CHNAM", 0.6),
    ("BUS_MGBSEC_01", "MGBSEC", 0.3),
    ("BUS_PGHTC_01", "PGHTC", 0.5),
]


class Fleet:
    def __init__(self, seed: int, start: datetime, faults: FaultConfig | None = None,
                 fixed_interval_s: int = 60, bus_interval_s: int = 30,
                 allow_route_download: bool = True):
        self.faults = faults or FaultConfig()
        field = ZoneField(seed)
        self.nodes: list[Node] = []
        for dev, zid, vi, (dlat, dlon) in FIXED_SITES:
            lat, lon = ZONES_BY_ID[zid].path[vi]
            self.nodes.append(FixedNode(dev, lat + dlat, lon + dlon, field, seed, self.faults,
                                        start, fixed_interval_s))
        for dev, rcode, frac in BUS_SITES:
            route = load_route(rcode, allow_download=allow_route_download)
            self.nodes.append(BusNode(dev, route, frac, field, seed, self.faults,
                                      start, bus_interval_s))

    # -- sequence persistence (live mode survives restarts without duplicate seqs) --
    def sequences(self) -> dict[str, int]:
        return {n.device_id: n.sequence for n in self.nodes}

    def restore_sequences(self, seqs: dict[str, int]) -> None:
        for n in self.nodes:
            n.sequence = int(seqs.get(n.device_id, n.sequence))

    # -- emission ------------------------------------------------------------
    def next_due(self) -> datetime:
        return min(n.next_due for n in self.nodes)

    def emit_due(self, now: datetime) -> list[tuple[datetime, Emission]]:
        """All emissions for nodes due at or before `now`, oldest first."""
        heap = [(n.next_due, i) for i, n in enumerate(self.nodes) if n.next_due <= now]
        heapq.heapify(heap)
        out: list[tuple[datetime, Emission]] = []
        while heap:
            t, i = heapq.heappop(heap)
            for em in self.nodes[i].read(t):
                out.append((t, em))
            if self.nodes[i].next_due <= now:
                heapq.heappush(heap, (self.nodes[i].next_due, i))
        return out

    def run_until(self, end: datetime) -> Iterator[Emission]:
        """Replay mode: chronological emissions up to `end`, as fast as possible."""
        while self.next_due() <= end:
            step_to = self.next_due() + timedelta(seconds=1)
            for _, em in self.emit_due(min(step_to, end)):
                yield em
