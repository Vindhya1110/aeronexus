"""Fault injection so the backend's validation / quality-flag paths see real cases."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaultConfig:
    """Per-packet probabilities (reboot is per node-day)."""
    gps_no_fix_rate: float = 0.010       # fix=false, lat/lon/hdop/speed = null
    gps_poor_hdop_rate: float = 0.010    # fix=true but hdop > 5 -> gps_valid=false
    bus_gps_multiplier: float = 3.0      # buses lose GPS more often (flyovers, urban canyons)
    sensor_dropout_rate: float = 0.010   # one sensor group null + sensor_flags bit set
    reboot_per_day: float = 0.3          # node restarts -> quality.warmup=true for a while
    duplicate_rate: float = 0.005        # store-and-forward resend of the same sequence
    malformed_rate: float = 0.005        # a REQUIRED key is missing -> backend must 422

    @classmethod
    def none(cls) -> "FaultConfig":
        return cls(0, 0, 1.0, 0, 0, 0, 0)
