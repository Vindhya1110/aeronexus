"""Windowed zone statistics shared by the API and the analytics worker, so both use ONE
definition of "the current window" instead of two that can drift apart.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from shared.constants import PLAUSIBLE_RANGES

from .models import Device, Measurement


def zone_devices(devices: list[Device], zone_id: str) -> list[Device]:
    """Devices currently attributed to a zone (last known zone, else home zone)."""
    return [d for d in devices if (d.last_zone_id or d.zone_id) == zone_id]


def window_stats(db: Session, zone_id: str, since: datetime, until: Optional[datetime] = None,
                 exclude_flagged: bool = False) -> dict:
    """Plain mean of raw PM in [since, until] plus counts of flagged samples. n_samples / n_devices count samples that CARRY PM
    (a node without a PM sensor, e.g. the ESP32 prototype, is a device of the zone but not a PM sample).

    exclude_flagged=False (API 'current' block): every sample counts; flagged ones are only counted.
    exclude_flagged=True  (worker): drops warm-up samples, any sensor-fault sample and out-of-range PM.
    """
    conds = [Measurement.zone_id == zone_id, Measurement.timestamp_utc >= since]
    if until is not None:
        conds.append(Measurement.timestamp_utc <= until)
    if exclude_flagged:
        conds += [Measurement.warmup.is_(False), Measurement.sensor_flags == 0,
                  Measurement.pm25_ugm3 <= PLAUSIBLE_RANGES["pm25_ugm3"][1],
                  Measurement.pm10_ugm3 <= PLAUSIBLE_RANGES["pm10_ugm3"][1]]
    row = db.execute(select(
        func.avg(Measurement.pm25_ugm3), func.avg(Measurement.pm10_ugm3),
        func.count(Measurement.pm25_ugm3), func.count(func.distinct(case((Measurement.pm25_ugm3.is_not(None), Measurement.device_id)))),
        func.sum(case((Measurement.warmup.is_(True), 1), else_=0)),
        func.sum(case((Measurement.gps_valid.is_(False), 1), else_=0)),
        func.sum(case((Measurement.sensor_flags > 0, 1), else_=0)),
    ).where(*conds)).one()
    return {
        "pm25_ugm3": None if row[0] is None else float(row[0]),
        "pm10_ugm3": None if row[1] is None else float(row[1]),
        "n_samples": int(row[2]), "n_devices": int(row[3]),
        "flagged_samples": {"warmup": int(row[4] or 0), "gps_invalid": int(row[5] or 0),
                            "sensor_fault": int(row[6] or 0)},
    }
