"""Pydantic model of the Aeronex telemetry data contract (schema_version 1.0).

Rules baked in here:
  * Every key in the contract is REQUIRED. An absent key is a malformed payload
    (backend answers 422).
  * Leaf *measurements* are nullable. `null` means "sensor value unavailable";
    it is never replaced by 0.
  * Sensor value ranges are NOT enforced here - implausible values must be kept
    and flagged downstream (see `plausibility_flags`), not rejected or dropped.
  * Timestamps must be timezone-aware and are normalised to UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .constants import DEVICE_ID_RE, PLAUSIBLE_RANGES, SCHEMA_VERSION, decode_sensor_flags


class _Strict(BaseModel):
    # forbid unknown keys: contract drift must be a conscious, versioned change
    model_config = ConfigDict(extra="forbid")


class GPS(_Strict):
    lat: Optional[float] = Field(..., ge=-90, le=90)
    lon: Optional[float] = Field(..., ge=-180, le=180)
    fix: bool
    hdop: Optional[float] = Field(..., ge=0)
    speed_kmh: Optional[float] = Field(..., ge=0)

    @model_validator(mode="after")
    def _fix_requires_position(self) -> "GPS":
        if self.fix and (self.lat is None or self.lon is None):
            raise ValueError("gps.fix is true but lat/lon is null")
        return self


class Env(_Strict):
    temperature_c: Optional[float]
    humidity_pct: Optional[float]
    pressure_hpa: Optional[float]


class PM(_Strict):
    pm25_ugm3: Optional[float]
    pm10_ugm3: Optional[float]


class GasProxy(_Strict):
    """Raw ADC counts of MQ-series sensors. Proxy indices, NOT gas concentrations."""
    mq2_adc: Optional[int]
    mq9_adc: Optional[int]
    mq135_adc: Optional[int]


class Quality(_Strict):
    warmup: bool
    gps_valid: bool
    sensor_flags: int = Field(..., ge=0)


class TelemetryPayload(_Strict):
    schema_version: Literal["1.0"]
    device_id: str
    sequence: int = Field(..., ge=0)
    timestamp_utc: datetime
    gps: GPS
    env: Env
    pm: PM
    gas_proxy: GasProxy
    quality: Quality
    firmware_version: str = Field(..., min_length=1)
    calibration_version: str = Field(..., min_length=1)

    @field_validator("device_id")
    @classmethod
    def _device_id_convention(cls, v: str) -> str:
        if not DEVICE_ID_RE.match(v):
            raise ValueError("device_id must match ZONE_<CODE>_<NN> or BUS_<ROUTE>_<NN>")
        return v

    @field_validator("timestamp_utc")
    @classmethod
    def _utc_only(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp_utc must include a timezone (use ...Z)")
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _gps_valid_needs_fix(self) -> "TelemetryPayload":
        if self.quality.gps_valid and not self.gps.fix:
            raise ValueError("quality.gps_valid is true but gps.fix is false")
        return self

    # ---- convenience --------------------------------------------------
    @property
    def is_mobile_device(self) -> bool:
        return self.device_id.startswith("BUS_")

    def to_wire(self) -> dict:
        """JSON-ready dict exactly as sent over the wire (UTC 'Z' timestamp, nulls kept)."""
        d = self.model_dump(mode="json")
        d["timestamp_utc"] = self.timestamp_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        return d


assert SCHEMA_VERSION == "1.0"  # keep Literal above and constants in sync


def plausibility_flags(p: TelemetryPayload) -> list[str]:
    """Names of fields whose value is outside its plausible physical range.

    Used by the backend/dashboard to show "out of range" badges. The values
    themselves are kept untouched.
    """
    flags: list[str] = []
    for group in (p.env, p.pm, p.gas_proxy):
        for name, value in group.model_dump().items():
            if value is None or name not in PLAUSIBLE_RANGES:
                continue
            lo, hi = PLAUSIBLE_RANGES[name]
            if not (lo <= value <= hi):
                flags.append(name)
    return flags


def quality_summary(p: TelemetryPayload) -> list[str]:
    """All visible quality flags for a packet (warm-up, GPS, failed sensors, range)."""
    out: list[str] = []
    if p.quality.warmup:
        out.append("warmup")
    if not p.quality.gps_valid:
        out.append("gps_invalid")
    out += [f"sensor_fault:{n}" for n in decode_sensor_flags(p.quality.sensor_flags)]
    out += [f"out_of_range:{n}" for n in plausibility_flags(p)]
    return out
