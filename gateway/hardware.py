"""Aeronex ESP32-S3 node -> data contract v1.0 (pure functions, no I/O: easy to test).

The firmware (`firmware/aeronex_node/aeronex_node.ino`) prints, every 2 s, one machine-readable line:

    CSV_DATA,<millis>,<mq135>,<mq2>,<mq9>,<dht_temp_c>,<dht_hum_pct>,<bmp_temp_c>,<pressure_hpa>,<altitude_m>

(an empty field = that sensor returned NaN). This module turns a window of such lines into ONE contract payload.

What the hardware does NOT have, and how the contract says so honestly (null value + sensor_flags bit, never a made-up number):
  * no PM sensor       -> pm.pm25_ugm3 / pm.pm10_ugm3 = null, FLAG_PM set. The backend therefore never uses this node for AQI,
                          forecasts or source attribution; it contributes gas proxies, temperature, humidity and pressure.
  * no GPS module      -> gps.fix = false, lat/lon null, FLAG_GPS_MODULE set; the backend places a fixed node in the zone
                          encoded in its device ID (e.g. ZONE_HITEC_90 -> HITEC). Optional surveyed coordinates can be supplied.
  * no wall clock      -> timestamp is the host clock when the window closes.
Thermistors (front/rear) are not part of CSV_DATA and have no field in the contract, so they are not forwarded.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from shared.constants import (DEVICE_ID_RE, FLAG_ENV_TH, FLAG_GPS_MODULE, FLAG_MQ2, FLAG_MQ9, FLAG_MQ135, FLAG_PM,
                              FLAG_PRESSURE)
from shared.schema import TelemetryPayload

FIRMWARE_VERSION = "aeronex-node-1.0"
CALIBRATION_VERSION = "hw-prototype-0"
CSV_PREFIX = "CSV_DATA,"
WARNING_ON_AQI, WARNING_OFF_AQI = 300, 270        # hardware "warning" mode: on above 300, off below 270 (hysteresis)


@dataclass(frozen=True)
class Reading:
    uptime_ms: int
    mq135: Optional[int]
    mq2: Optional[int]
    mq9: Optional[int]
    dht_temp_c: Optional[float]
    dht_hum_pct: Optional[float]
    bmp_temp_c: Optional[float]
    pressure_hpa: Optional[float]
    altitude_m: Optional[float]


def _num(s: str) -> Optional[float]:
    s = s.strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def parse_csv_line(line: str) -> Optional[Reading]:
    """A firmware CSV_DATA line -> Reading, or None for any other line (human-readable report, MODE_SET echo, boot text ...)."""
    line = line.strip()
    if not line.startswith(CSV_PREFIX):
        return None
    parts = line[len(CSV_PREFIX):].split(",")
    if len(parts) != 9:
        return None                                   # truncated by a serial glitch: drop, never guess
    ms = _num(parts[0])
    if ms is None or ms < 0:
        return None
    gas = [_num(p) for p in parts[1:4]]
    gas = [None if g is None or not (0 <= g <= 4095) else int(g) for g in gas]
    return Reading(int(ms), gas[0], gas[1], gas[2], *[_num(p) for p in parts[4:9]])


def parse_mode_echo(line: str) -> Optional[str]:
    line = line.strip()
    return line.split(",", 1)[1] if line.startswith("MODE_SET,") else None


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


@dataclass
class Window:
    """Readings collected between two sends. The MQ sensors are noisy, so we send the window mean (like the simulator's cadence)."""
    readings: list[Reading] = field(default_factory=list)

    def add(self, r: Reading) -> None:
        self.readings.append(r)

    def __len__(self) -> int:
        return len(self.readings)

    def col(self, name: str) -> list[float]:
        return [getattr(r, name) for r in self.readings if getattr(r, name) is not None]


@dataclass(frozen=True)
class NodeConfig:
    device_id: str = "ZONE_HITEC_90"
    warmup_s: int = 300                        # MQ heaters need time; the contract's warm-up flag covers this
    lat: Optional[float] = None                # surveyed install position (optional; the node has no GPS module)
    lon: Optional[float] = None

    def __post_init__(self):
        if not DEVICE_ID_RE.match(self.device_id):
            raise ValueError("device_id must look like ZONE_<ZONECODE>_<NN>, e.g. ZONE_HITEC_90 (see docs/device_ids.md)")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("give both --lat and --lon, or neither")


def build_payload(w: Window, cfg: NodeConfig, sequence: int, now: Optional[datetime] = None) -> Optional[dict]:
    """One contract payload (wire dict) for the window, or None if the window is empty."""
    if not len(w):
        return None
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    flags = 0

    def gas(name: str, bit: int) -> Optional[int]:
        nonlocal flags
        m = _mean(w.col(name))
        if m is None:
            flags |= bit
            return None
        return int(round(m))

    mq2, mq9, mq135 = gas("mq2", FLAG_MQ2), gas("mq9", FLAG_MQ9), gas("mq135", FLAG_MQ135)

    # temperature: the BMP180 (+/-0.5 C) is the better thermometer; the DHT11 (+/-2 C) is the fallback
    bmp_t, dht_t, hum = _mean(w.col("bmp_temp_c")), _mean(w.col("dht_temp_c")), _mean(w.col("dht_hum_pct"))
    temp = bmp_t if bmp_t is not None else dht_t
    if temp is None or hum is None:
        flags |= FLAG_ENV_TH
    pres = _mean(w.col("pressure_hpa"))
    if pres is None:
        flags |= FLAG_PRESSURE

    flags |= FLAG_PM                              # no particulate sensor fitted: PM stays null, the flag says why
    surveyed = cfg.lat is not None
    if not surveyed:
        flags |= FLAG_GPS_MODULE                  # no GPS module either

    uptime_s = w.readings[-1].uptime_ms / 1000.0
    wire = TelemetryPayload.model_validate({
        "schema_version": "1.0",
        "device_id": cfg.device_id,
        "sequence": sequence,
        "timestamp_utc": now,
        "gps": {"lat": cfg.lat, "lon": cfg.lon, "fix": surveyed, "hdop": None, "speed_kmh": 0.0 if surveyed else None},
        "env": {"temperature_c": None if temp is None else round(temp, 1),
                "humidity_pct": None if hum is None else round(hum, 1),
                "pressure_hpa": None if pres is None else round(pres, 1)},
        "pm": {"pm25_ugm3": None, "pm10_ugm3": None},
        "gas_proxy": {"mq2_adc": mq2, "mq9_adc": mq9, "mq135_adc": mq135},
        "quality": {"warmup": uptime_s < cfg.warmup_s, "gps_valid": surveyed, "sensor_flags": flags},
        "firmware_version": FIRMWARE_VERSION,
        "calibration_version": CALIBRATION_VERSION,
    }).to_wire()
    return wire


# ---- hardware "mode" (the firmware accepts MODE,normal|warning|silent over serial) --------------------------------------
def next_mode(current: str, zone_aqi: Optional[float], forced: str = "auto") -> str:
    """auto: 'warning' while the node's zone AQI is above 300 (leaves below 270, so it cannot flicker); otherwise 'normal'."""
    if forced in ("normal", "warning", "silent"):
        return forced
    if zone_aqi is None:
        return current
    if current != "warning" and zone_aqi > WARNING_ON_AQI:
        return "warning"
    if current == "warning" and zone_aqi < WARNING_OFF_AQI:
        return "normal"
    return current
