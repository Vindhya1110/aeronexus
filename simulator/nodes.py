"""Simulated edge nodes. Fixed and bus nodes emit the IDENTICAL payload schema;
mobility is expressed only through gps.speed_kmh / gps.fix (data contract rule).
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.constants import (FLAG_ENV_TH, FLAG_MQ2, FLAG_MQ9, FLAG_MQ135, FLAG_PM,
                              FLAG_PRESSURE, HDOP_MAX_VALID)
from shared.schema import TelemetryPayload
from shared.zones import nearest_zone

from .faults import FaultConfig
from .profiles import profile_for
from .routes import Route
from .signals import EventSchedule, NodeSensors, zone_components

FIRMWARE_VERSION = "0.1.0"
CALIBRATION_VERSION = "prototype-0"
GPS_JITTER_DEG = 0.00003      # ~3 m 1-sigma

# sensor-dropout menu: (flag bit, wire paths set to null)
_DROPOUTS = [
    (FLAG_PM, [("pm", "pm25_ugm3"), ("pm", "pm10_ugm3")]),
    (FLAG_MQ2, [("gas_proxy", "mq2_adc")]),
    (FLAG_MQ9, [("gas_proxy", "mq9_adc")]),
    (FLAG_MQ135, [("gas_proxy", "mq135_adc")]),
    (FLAG_ENV_TH, [("env", "temperature_c"), ("env", "humidity_pct")]),
    (FLAG_PRESSURE, [("env", "pressure_hpa")]),
]
# keys whose removal makes a payload malformed (top-level and nested)
_REQUIRED_KEYS = [("device_id",), ("sequence",), ("timestamp_utc",), ("gps",), ("pm",),
                  ("gps", "fix"), ("gps", "lat"), ("pm", "pm25_ugm3"), ("quality", "gps_valid"),
                  ("env", "temperature_c"), ("gas_proxy", "mq9_adc"), ("schema_version",)]


@dataclass
class Emission:
    wire: dict
    kind: str | None = None     # None = clean; else "malformed" | "duplicate"
    truth: dict | None = None   # generator's hidden ground truth. EVALUATION ONLY: never sent over HTTP.


def ground_truth(zone_id, comp, boost: float) -> dict:
    """What the generator actually injected at this instant (PM2.5-scale contributions, ug/m3).

    dominant_source = largest PM10-equivalent excess over the urban background, or 'background'
    if no source adds at least 35% of the background PM10. Used only by tools/evaluate_*.
    """
    traffic = comp.traffic * boost
    excess10 = {"traffic": 1.35 * traffic, "industrial": 1.3 * comp.industrial,
                "smoke": 1.25 * comp.smoke, "dust": comp.dust}
    top = max(excess10, key=excess10.get)
    dominant = top if excess10[top] >= 0.35 * 1.75 * comp.bg else "background"
    strength = excess10[top] / (1.75 * comp.bg)      # dominant excess relative to background PM10
    return {"zone_id": zone_id, "dominant_source": dominant, "dominant_strength": round(strength, 2),
            "components_pm25_ugm3": {"background": round(comp.bg, 1), "traffic": round(traffic, 1),
                                     "industrial": round(comp.industrial, 1), "smoke": round(comp.smoke, 1),
                                     "dust_pm10_only": round(comp.dust, 1)}}


class ZoneField:
    """Shared per-zone event schedules, so co-located nodes see the same episodes."""

    def __init__(self, seed: int):
        self.seed = seed
        self._sched: dict[str | None, EventSchedule] = {}

    def components(self, zone_id: str | None, t: datetime):
        if zone_id not in self._sched:
            self._sched[zone_id] = EventSchedule(self.seed, zone_id or "BG", profile_for(zone_id))
        return zone_components(profile_for(zone_id), self._sched[zone_id], t)


class Node:
    mobile = False

    def __init__(self, device_id: str, field: ZoneField, seed: int, faults: FaultConfig,
                 start: datetime, interval_s: int, warmup_s: int = 180):
        self.device_id, self.field, self.faults = device_id, field, faults
        self.interval_s = interval_s
        self.warmup_s = warmup_s
        self.rng = random.Random(f"{seed}:{device_id}")
        self.sensors = NodeSensors(self.rng)
        self.sequence = 0
        self.boot_time = start                      # cold start => initial warm-up
        self.next_due = start + timedelta(seconds=self.rng.uniform(0, interval_s))  # stagger
        self._last_t = start

    # -- subclasses provide position/speed ---------------------------------
    def _motion(self, t: datetime) -> tuple[float, float, float]:
        """(lat, lon, speed_kmh) ground truth at time t."""
        raise NotImplementedError

    def _traffic_boost(self) -> float:
        return 1.0

    # -- payload ------------------------------------------------------------
    def read(self, t: datetime) -> list[Emission]:
        """Produce one sample at sim-time t (and advance this node's schedule)."""
        r, f = self.rng, self.faults
        dt = max(1.0, (t - self._last_t).total_seconds())
        self._last_t = t
        self.next_due = t + timedelta(seconds=self.interval_s)

        if r.random() < f.reboot_per_day * dt / 86400:
            self.boot_time = t
        age = (t - self.boot_time).total_seconds()
        warmup = age < self.warmup_s

        lat, lon, speed = self._motion(t)
        zone = nearest_zone(lat, lon)
        comp = self.field.components(zone.zone_id if zone else None, t)
        env = self.sensors.env(t)
        meas = self.sensors.pm_and_gas(comp, env["temperature_c"], env["humidity_pct"],
                                       self._traffic_boost(), age if warmup else None)

        # ---- GPS block (with injected faults) ----
        gps_mult = f.bus_gps_multiplier if self.mobile else 1.0
        fix, hdop, gps_valid = True, round(r.uniform(0.8, 2.2), 1), True
        out_lat, out_lon, out_speed = round(lat, 6), round(lon, 6), round(speed, 1)
        roll = r.random()
        if roll < f.gps_no_fix_rate * gps_mult:
            fix, gps_valid, hdop, out_lat, out_lon, out_speed = False, False, None, None, None, None
        elif roll < (f.gps_no_fix_rate + f.gps_poor_hdop_rate) * gps_mult:
            hdop = round(r.uniform(HDOP_MAX_VALID + 0.5, 12.0), 1)
            gps_valid = False

        self.sequence += 1
        flags = 0
        wire = TelemetryPayload.model_validate({
            "schema_version": "1.0",
            "device_id": self.device_id,
            "sequence": self.sequence,
            "timestamp_utc": t.replace(microsecond=0),
            "gps": {"lat": out_lat, "lon": out_lon, "fix": fix, "hdop": hdop, "speed_kmh": out_speed},
            "env": env,
            "pm": {"pm25_ugm3": meas["pm25_ugm3"], "pm10_ugm3": meas["pm10_ugm3"]},
            "gas_proxy": {k: meas[k] for k in ("mq2_adc", "mq9_adc", "mq135_adc")},
            "quality": {"warmup": warmup, "gps_valid": gps_valid, "sensor_flags": 0},
            "firmware_version": FIRMWARE_VERSION,
            "calibration_version": CALIBRATION_VERSION,
        }).to_wire()

        # ---- sensor dropout: null the values (never 0) and set the flag bit ----
        if r.random() < f.sensor_dropout_rate:
            bit, paths = r.choice(_DROPOUTS)
            for grp, key in paths:
                wire[grp][key] = None
            flags |= bit
            wire["quality"]["sensor_flags"] = flags

        truth = ground_truth(zone.zone_id if zone else None, comp, self._traffic_boost())
        emissions = [Emission(wire, truth=truth)]
        if r.random() < f.duplicate_rate:
            emissions.append(Emission(copy.deepcopy(wire), "duplicate", truth))
        if r.random() < f.malformed_rate:
            broken = copy.deepcopy(wire)
            path = r.choice(_REQUIRED_KEYS)
            d = broken
            for k in path[:-1]:
                d = d[k]
            del d[path[-1]]
            emissions[0] = Emission(broken, "malformed", truth)
        return emissions


class FixedNode(Node):
    """Zonal install: speed is always exactly 0.0; GPS jitters a few metres around the site."""

    def __init__(self, device_id: str, lat: float, lon: float, *a, **kw):
        super().__init__(device_id, *a, **kw)
        self.lat, self.lon = lat, lon

    def _motion(self, t):
        r = self.rng
        return (self.lat + r.gauss(0, GPS_JITTER_DEG), self.lon + r.gauss(0, GPS_JITTER_DEG), 0.0)


class BusNode(Node):
    """Bus-mounted node moving back and forth along a real road polyline.

    Cruises at ~15-25 km/h with slowdowns (congestion/signals) and stop dwells.
    """
    mobile = True
    STEP_S = 5.0

    def __init__(self, device_id: str, route: Route, start_fraction: float, *a, **kw):
        super().__init__(device_id, *a, **kw)
        self.route = route
        self.dist_m = route.length_m * start_fraction
        self.direction = 1
        self.speed = 0.0                      # km/h
        self.cruise = self.rng.uniform(17, 24)
        self.mode, self.mode_until = "cruise", self._last_t
        self.next_stop_m = self.dist_m + self.direction * self.rng.uniform(300, 800)
        self._sim_t = self._last_t
        self.slow_target = 5.0

    def _traffic_boost(self) -> float:
        return 1.15                              # roadside exposure

    def _step(self, t: datetime, dt: float) -> None:
        r = self.rng
        if self.mode == "dwell" and t >= self.mode_until:
            self.mode = "cruise"
        elif self.mode == "slow" and t >= self.mode_until:
            self.mode = "cruise"
        elif self.mode == "cruise":
            if r.random() < dt / 240:            # ~1 slowdown per 4 min
                self.mode, self.mode_until = "slow", t + timedelta(seconds=r.uniform(30, 150))
                self.slow_target = r.uniform(2, 9)
        target = {"cruise": self.cruise + r.gauss(0, 1.0), "slow": self.slow_target, "dwell": 0.0}[self.mode]
        target = max(0.0, target)
        accel = 3.6 * dt      # +3.6 km/h per s  (~1 m/s^2)
        brake = 6.0 * dt
        self.speed += max(-brake, min(accel, target - self.speed))
        self.dist_m += self.direction * self.speed / 3.6 * dt
        # reverse at terminals
        if (self.direction > 0 and self.dist_m >= self.route.length_m) or \
           (self.direction < 0 and self.dist_m <= 0):
            self.dist_m = max(0.0, min(self.route.length_m, self.dist_m))
            self.direction *= -1
            self.mode, self.mode_until = "dwell", t + timedelta(seconds=r.uniform(40, 120))
            self.next_stop_m = self.dist_m + self.direction * r.uniform(300, 800)
        elif (self.direction > 0 and self.dist_m >= self.next_stop_m) or \
             (self.direction < 0 and self.dist_m <= self.next_stop_m):
            self.mode, self.mode_until = "dwell", t + timedelta(seconds=r.uniform(15, 40))
            self.next_stop_m = self.dist_m + self.direction * r.uniform(300, 800)

    def _motion(self, t):
        while (t - self._sim_t).total_seconds() >= self.STEP_S:
            self._sim_t += timedelta(seconds=self.STEP_S)
            self._step(self._sim_t, self.STEP_S)
        lat, lon = self.route.position_at(self.dist_m)
        speed = 0.0 if self.speed < 0.3 else min(25.0, max(0.0, self.speed + self.rng.gauss(0, 0.6)))
        return (lat + self.rng.gauss(0, GPS_JITTER_DEG), lon + self.rng.gauss(0, GPS_JITTER_DEG), speed)
