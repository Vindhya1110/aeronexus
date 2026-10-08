"""Scripted demo narrative, layered on top of the simulator's own (seeded) output.

The generator's episodes are random per seed/zone/day. For a demo we want the same visible story every
time, so this overlays two *scripted* episodes onto emitted payloads (wire + ground truth), using the same
source signatures the generator already uses (see signals.py):

  traffic jam   PM2.5+PM10 up, MQ-9 up                       (MGBSEC, morning rush)
  smoke episode PM2.5 spike, MQ-2 and MQ-135 up sharply      (HITEC, just after the rush)

Everything else is the normal seeded baseline (calm night -> rush-hour build-up). Deterministic: depends only on
the payload's own timestamp and device, never on wall-clock or RNG.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

ADC_MAX = 4095

# Narrative clock (UTC; IST = UTC+5:30). Dataset 2026-12-09 03:00 IST (2 days of history) .. 2026-12-10 10:50 IST.
DEMO_START = datetime(2026, 12, 8, 21, 30, tzinfo=timezone.utc)      # 03:00 IST, 2 days before the story day (history for the ML 24 h baselines)
DEMO_END = datetime(2026, 12, 10, 5, 20, tzinfo=timezone.utc)        # 10:50 IST: the smoke event is still open
DEMO_LIVE_SPLIT = datetime(2026, 12, 10, 4, 0, tzinfo=timezone.utc)  # 09:30 IST: history ends, live play begins


@dataclass(frozen=True)
class Episode:
    zone: str
    kind: str          # "traffic" | "smoke"
    start: datetime
    magnitude: float   # PM2.5 ug/m3 at peak
    rise_s: float
    decay_s: float

    def envelope(self, t: datetime) -> float:
        dt = (t - self.start).total_seconds()
        if dt < 0:
            return 0.0
        if dt < self.rise_s:
            return dt / self.rise_s
        return math.exp(-(dt - self.rise_s) / self.decay_s)


EPISODES = (
    Episode("MGBSEC", "traffic", datetime(2026, 12, 10, 3, 15, tzinfo=timezone.utc), 38, 1200, 1500),  # 08:45 IST
    Episode("HITEC", "smoke", datetime(2026, 12, 10, 4, 30, tzinfo=timezone.utc), 150, 600, 3600),     # 10:00 IST
)
# per unit of PM2.5 contribution: (pm10, mq2, mq9, mq135) - mirrors signals.NodeSensors.pm_and_gas
_COEF = {
    "traffic": (1.35, 1.2, 8.0, 2.5),
    "smoke": (1.25, 10.0, 1.0, 7.0),   # scripted open-burning plume: MQ-2/MQ-135-dominated (MQ-9 rise kept small)
}


def zone_of_device(device_id: str) -> str | None:
    parts = device_id.split("_")
    if len(parts) == 3 and parts[1] in {"CHNAM", "HITEC", "IDAPASH", "MGBSEC", "PGPT", "SANATH"}:
        return parts[1]
    return None          # PGHTC bus crosses zones; leave it alone


def apply(wire: dict, truth: dict | None = None) -> None:
    """Mutate one payload in place (no-op outside the scripted episodes or for other zones)."""
    zone = zone_of_device(wire.get("device_id", ""))
    if zone is None or wire.get("pm") is None:
        return
    t = datetime.fromisoformat(wire["timestamp_utc"].replace("Z", "+00:00"))
    for ep in EPISODES:
        if ep.zone != zone:
            continue
        env = ep.envelope(t)
        if env < 1e-3:
            continue
        add = ep.magnitude * env
        c10, c2, c9, c135 = _COEF[ep.kind]
        pm, gas = wire["pm"], wire["gas_proxy"]
        if pm["pm25_ugm3"] is not None:
            pm["pm25_ugm3"] = round(pm["pm25_ugm3"] + add, 1)
        if pm["pm10_ugm3"] is not None:
            pm["pm10_ugm3"] = round(pm["pm10_ugm3"] + c10 * add, 1)
        for key, coef in (("mq2_adc", c2), ("mq9_adc", c9), ("mq135_adc", c135)):
            if gas[key] is not None:
                gas[key] = int(min(ADC_MAX, round(gas[key] + coef * add)))
        if truth is not None and env > 0.3:
            truth["dominant_source"] = "traffic" if ep.kind == "traffic" else "smoke"
            truth["dominant_strength"] = round(env, 2)
            truth["scripted_episode"] = ep.kind
