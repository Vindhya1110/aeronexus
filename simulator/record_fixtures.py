"""Record fixture payloads from the simulator (seed 7, no random faults), then apply the
single, specific defect each fixture is meant to carry.

    python -m simulator.record_fixtures
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from .faults import FaultConfig
from .fleet import Fleet

OUT = Path(__file__).resolve().parent.parent / "shared" / "fixtures"


def main() -> None:
    start = datetime(2026, 12, 10, 3, 30, tzinfo=timezone.utc)   # 09:00 IST, winter morning rush
    fleet = Fleet(7, start, FaultConfig.none(), allow_route_download=False)
    wires = [e.wire for e in fleet.run_until(datetime(2026, 12, 10, 4, 30, tzinfo=timezone.utc))]

    def pick(dev: str, n: int) -> dict:
        return copy.deepcopy([w for w in wires if w["device_id"] == dev][n])

    def save(name: str, obj) -> None:
        (OUT / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

    OUT.mkdir(parents=True, exist_ok=True)
    valid = pick("ZONE_SANATH_01", 5)
    save("valid_payload.json", valid)

    missing = copy.deepcopy(valid)
    del missing["pm"], missing["quality"], missing["gps"]["fix"]
    save("missing_required_fields.json", missing)

    nofix = pick("BUS_CHNAM_01", 6)
    nofix["gps"].update(lat=None, lon=None, fix=False, hdop=None, speed_kmh=None)
    nofix["quality"]["gps_valid"] = False
    save("invalid_gps_fix.json", nofix)

    save("duplicate_sequence.json", [valid, copy.deepcopy(valid)])
    save("bus_payload.json", pick("BUS_PGHTC_01", 8))
    save("warmup_payload.json", pick("ZONE_HITEC_01", 0))


if __name__ == "__main__":
    main()
