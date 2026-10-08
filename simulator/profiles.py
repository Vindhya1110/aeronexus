"""Ground-truth emission profiles used ONLY by the simulator.

These numbers are the simulator's hidden "truth". The worker must infer source
signatures from the telemetry alone; it never imports this module.

All PM numbers are ug/m3 contributions at their peak, before the seasonal modifier.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZoneProfile:
    bg_pm25: float          # urban background PM2.5 (diurnal-modulated)
    traffic_pm25: float     # extra PM2.5 at rush-hour peak (carries MQ-9 signal)
    industrial_pm25: float  # steady industrial PM2.5 (carries MQ-135 / MQ-2 signal)
    dust_pm10: float        # coarse dust, daytime construction/road dust (PM10 only, no gas)
    smoke_per_day: float    # expected open-burning/smoke episodes per day (PM + MQ-2 + MQ-135)
    puff_per_day: float     # expected industrial puffs per day
    jam_per_day: float      # expected traffic jams per day (multiplies traffic term)
    dust_burst_per_day: float


PROFILES: dict[str, ZoneProfile] = {
    #                      bg  traf  ind  dust smoke puff  jam  dustburst
    "PGPT":    ZoneProfile(28, 38,    0,   14,  0.15, 0.0,  1.5, 1.0),
    "CHNAM":   ZoneProfile(30, 46,    0,    8,  0.60, 0.0,  2.0, 0.3),
    "HITEC":   ZoneProfile(24, 28,    0,   45,  0.05, 0.0,  1.2, 2.5),   # construction-heavy
    "MGBSEC":  ZoneProfile(28, 52,    0,    6,  0.15, 0.0,  2.0, 0.3),
    "SANATH":  ZoneProfile(30, 24,   30,    6,  0.20, 2.5,  1.0, 0.3),
    "IDAPASH": ZoneProfile(26,  4,   55,    6,  0.80, 3.5,  0.1, 0.3),   # industrial
}

# Used when a node (e.g. a bus) is outside every seeded zone.
BACKGROUND = ZoneProfile(25, 14, 0, 5, 0.05, 0.0, 0.5, 0.2)


def profile_for(zone_id: str | None) -> ZoneProfile:
    return PROFILES.get(zone_id, BACKGROUND) if zone_id else BACKGROUND
