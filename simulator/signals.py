"""Physical signal model: PM, MQ proxy ADC, and environment.

Design: a *zone-level* field (deterministic given seed + time, so two nodes in
the same zone see the same smoke episode) plus *node-level* noise/drift
(per-device state). Source signatures are intentionally distinct:

  traffic      -> PM2.5 & PM10 up in rush hours, MQ-9 up (CO/combustion proxy)
  industrial   -> steady PM2.5 + puffs, MQ-135 and MQ-2 up
  construction -> PM10 up (dust) with PM2.5 barely moved, MQ proxies ~ baseline
  smoke        -> PM2.5 spike, MQ-2 and MQ-135 up sharply (rare episodes)
"""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from shared.constants import ADC_MAX

from .profiles import ZoneProfile

IST = timezone(timedelta(hours=5, minutes=30))

# Seasonal PM multipliers (month 1..12): Nov-Jan elevated (winter inversion, stubble/
# biomass burning, low mixing height); monsoon (Jun-Sep) washed out.
SEASON_PM = {1: 1.55, 2: 1.30, 3: 1.00, 4: 0.90, 5: 0.85, 6: 0.60,
             7: 0.45, 8: 0.45, 9: 0.55, 10: 0.85, 11: 1.40, 12: 1.60}
# Road/construction dust is worst in the dry months, suppressed by monsoon rain.
SEASON_DUST = {1: 1.1, 2: 1.2, 3: 1.4, 4: 1.5, 5: 1.4, 6: 0.8,
               7: 0.4, 8: 0.4, 9: 0.5, 10: 0.9, 11: 1.0, 12: 1.1}
# Hyderabad monthly mean temperature (deg C) and relative humidity (%), approximate.
TEMP_MEAN = {1: 22, 2: 25, 3: 29, 4: 32, 5: 34, 6: 30, 7: 27, 8: 26, 9: 27, 10: 26, 11: 23, 12: 21}
RH_MEAN = {1: 55, 2: 45, 3: 38, 4: 38, 5: 40, 6: 65, 7: 76, 8: 78, 9: 75, 10: 68, 11: 60, 12: 57}

# Baselines for the MQ proxy ADC channels in clean-ish air (12-bit counts).
MQ_BASE = {"mq2": 700.0, "mq9": 900.0, "mq135": 1000.0}


def _gauss(x: float, mu: float, sigma: float) -> float:
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2)


def hour_ist(t: datetime) -> float:
    li = t.astimezone(IST)
    return li.hour + li.minute / 60 + li.second / 3600


def diurnal_pm(h: float) -> float:
    """Background PM modulation: morning + evening rush peaks, shallow night plateau."""
    return 0.78 + 0.30 * _gauss(h, 9.0, 1.8) + 0.42 * _gauss(h, 19.5, 2.3) + 0.12 * _gauss(h, 6.0, 1.5)


def rush_index(t: datetime) -> float:
    """0..1 traffic intensity; double-peaked, lower on Sundays."""
    h = hour_ist(t)
    base = 0.22 + 0.35 * _gauss(h, 13.0, 3.5)           # daytime plateau
    peak = max(_gauss(h, 9.0, 1.3), _gauss(h, 18.5, 1.7))
    idx = min(1.0, base + 0.75 * peak)
    if h < 5 or h > 23:
        idx *= 0.4
    if t.astimezone(IST).weekday() == 6:
        idx *= 0.65
    return idx


def is_rush_hour(t: datetime) -> bool:
    return rush_index(t) > 0.7


def dust_activity(t: datetime) -> float:
    """Construction/road-dust activity: daytime, Mon-Sat."""
    li = t.astimezone(IST)
    if li.weekday() == 6:
        return 0.15
    return _gauss(li.hour + li.minute / 60, 13.0, 3.2)


# --------------------------------------------------------------------------
# Zone-level episodic events (deterministic per seed/zone/UTC-day)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Event:
    kind: str        # smoke | puff | jam | dust
    start: datetime
    magnitude: float
    rise_s: float
    decay_s: float

    def envelope(self, t: datetime) -> float:
        dt = (t - self.start).total_seconds()
        if dt < 0:
            return 0.0
        if dt < self.rise_s:
            return dt / self.rise_s
        return math.exp(-(dt - self.rise_s) / self.decay_s)


class EventSchedule:
    """Lazily generates a zone's events one UTC day at a time."""

    def __init__(self, seed: int, zone_key: str, profile: ZoneProfile):
        self.seed, self.zone_key, self.profile = seed, zone_key, profile
        self._cache: dict[tuple, list[Event]] = {}

    def _day_events(self, day: datetime) -> list[Event]:
        key = day.date()
        if key in self._cache:
            return self._cache[key]
        rng = random.Random(zlib.crc32(f"{self.seed}:{self.zone_key}:{key}".encode()))
        p = self.profile
        midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        evs: list[Event] = []

        def poisson(rate: float) -> int:
            # Knuth; rates here are small
            L, k, prod = math.exp(-rate), 0, 1.0
            while True:
                prod *= rng.random()
                if prod <= L:
                    return k
                k += 1

        def at(hours_lo: float, hours_hi: float) -> datetime:
            return midnight + timedelta(hours=rng.uniform(hours_lo, hours_hi))

        # (IST day windows converted to UTC hours: IST = UTC + 5.5)
        for _ in range(poisson(p.smoke_per_day)):   # evening/night burning
            evs.append(Event("smoke", at(11.5, 24), rng.uniform(40, 130), 600, rng.uniform(1500, 3600)))
        for _ in range(poisson(p.puff_per_day)):
            evs.append(Event("puff", at(0, 24), rng.uniform(25, 70), 300, rng.uniform(900, 2400)))
        for _ in range(poisson(p.jam_per_day)):     # around rush hours
            evs.append(Event("jam", at(2.5, 5.0) if rng.random() < 0.5 else at(11.5, 14.5),
                             rng.uniform(0.5, 1.0), 600, rng.uniform(1200, 2400)))
        for _ in range(poisson(p.dust_burst_per_day)):   # daytime IST
            evs.append(Event("dust", at(3.0, 12.0), rng.uniform(40, 120), 240, rng.uniform(600, 1500)))
        self._cache[key] = evs
        return evs

    def active(self, t: datetime) -> list[tuple[Event, float]]:
        out = []
        for d in (t - timedelta(days=1), t):
            for ev in self._day_events(d):
                e = ev.envelope(t)
                if e > 0.01:
                    out.append((ev, e))
        return out


# --------------------------------------------------------------------------
# Zone field: turn (profile, time) into emission components
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Components:
    bg: float          # PM2.5 background
    traffic: float     # PM2.5 from traffic
    industrial: float  # PM2.5 from industry
    smoke: float       # PM2.5 from smoke episode
    dust: float        # PM10-only coarse dust


def zone_components(profile: ZoneProfile, sched: EventSchedule, t: datetime) -> Components:
    m = t.astimezone(IST).month
    season = SEASON_PM[m]
    bg = profile.bg_pm25 * season * diurnal_pm(hour_ist(t))

    traffic = profile.traffic_pm25 * rush_index(t) * (1 + 0.5 * (season - 1))
    industrial = profile.industrial_pm25 * season * (0.7 + 0.3 * _gauss(hour_ist(t), 14, 6))
    dust = profile.dust_pm10 * SEASON_DUST[m] * dust_activity(t)
    smoke = 0.0

    for ev, env in sched.active(t):
        if ev.kind == "smoke":
            smoke += ev.magnitude * env * (0.7 + 0.3 * season)
        elif ev.kind == "puff":
            industrial += ev.magnitude * env
        elif ev.kind == "jam":
            traffic *= 1 + ev.magnitude * env
        elif ev.kind == "dust":
            dust += ev.magnitude * env * SEASON_DUST[m]
    return Components(bg, traffic, industrial, smoke, dust)


# --------------------------------------------------------------------------
# Node-level sensor model
# --------------------------------------------------------------------------
class NodeSensors:
    """Per-device state: AR(1) multiplicative PM noise and slow MQ baseline drift."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self._ar = 0.0
        self._drift = {k: rng.uniform(-40, 40) for k in MQ_BASE}
        self._cal_pm = rng.uniform(0.94, 1.06)   # unit-to-unit gain spread

    def env(self, t: datetime) -> dict:
        r, m, h = self.rng, t.astimezone(IST).month, hour_ist(t)
        amp = 3.5 if m in (6, 7, 8, 9) else 5.5
        temp = TEMP_MEAN[m] + amp * math.sin(2 * math.pi * (h - 9.0) / 24.0) + r.gauss(0, 0.25)
        rh = RH_MEAN[m] - 3.4 * (temp - TEMP_MEAN[m]) + r.gauss(0, 1.2)
        # station (not sea-level) pressure: Hyderabad sits at roughly 540 m
        pres = 952.0 + 1.2 * math.sin(2 * math.pi * (h - 10.0) / 12.0) + r.gauss(0, 0.2)
        return {"temperature_c": round(temp, 1),
                "humidity_pct": round(max(5.0, min(99.0, rh)), 1),
                "pressure_hpa": round(pres, 1)}

    def pm_and_gas(self, comp: Components, temp_c: float, rh: float,
                   traffic_boost: float = 1.0, warmup_age_s: float | None = None) -> dict:
        r = self.rng
        self._ar = 0.85 * self._ar + r.gauss(0, 0.045)           # slow, correlated wobble
        mult = max(0.5, (1 + self._ar + r.gauss(0, 0.025))) * self._cal_pm

        traffic = comp.traffic * traffic_boost
        pm25 = (comp.bg + traffic + comp.industrial + comp.smoke + 0.12 * comp.dust) * mult
        pm10 = (1.75 * comp.bg + 1.35 * traffic + 1.3 * comp.industrial
                + 1.25 * comp.smoke + comp.dust) * mult
        pm25 = max(1.0, pm25)
        pm10 = max(pm25, pm10)

        # MQ proxies respond to *combustion* terms, not to dust.
        tf = 1 + 0.004 * (temp_c - 25) - 0.0015 * (rh - 55)       # MQ temp/humidity cross-sensitivity
        raw = {
            "mq2": MQ_BASE["mq2"] + 1.2 * traffic + 10.0 * comp.smoke + 3.5 * comp.industrial,
            "mq9": MQ_BASE["mq9"] + 8.0 * traffic + 3.5 * comp.smoke + 2.0 * comp.industrial,
            "mq135": MQ_BASE["mq135"] + 2.5 * traffic + 7.0 * comp.smoke + 8.0 * comp.industrial,
        }
        gas = {}
        for k, v in raw.items():
            v = (v + self._drift[k]) * tf + r.gauss(0, 18)
            if warmup_age_s is not None:       # MQ heaters read high and settle while warming
                v *= 1 + 0.5 * math.exp(-warmup_age_s / 90.0)
            gas[f"{k}_adc"] = int(max(0, min(ADC_MAX, round(v))))
        return {"pm25_ugm3": round(pm25, 1), "pm10_ugm3": round(pm10, 1), **gas}
