"""Coverage: how fresh and how well-instrumented each zone is, so blind spots are visible.

Device online/offline is NOT recomputed here: it reuses backend.routers.devices.is_online (the same
function behind GET /v1/devices) and backend.stats.zone_devices (the same zone membership as /v1/zones).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.models import Device
from backend.routers.devices import is_online
from backend.stats import zone_devices

from .config import WorkerSettings


def zone_coverage(zone_id: str, devices: list[Device], now: datetime, cfg: WorkerSettings,
                  last_ts: Optional[datetime], last_rx: Optional[datetime], mode: str,
                  flagged: Optional[dict] = None, n_usable: Optional[int] = None) -> dict:
    zdev = zone_devices(devices, zone_id)
    limit = cfg.backend.offline_after_s
    if mode == "live":
        online = [d for d in zdev if is_online(d, now, cfg.backend)]
        age = None if last_rx is None else round((now - last_rx).total_seconds(), 1)
        dev = {"total": len(zdev), "online": len(online), "offline": len(zdev) - len(online),
               "offline_ids": sorted(d.device_id for d in zdev if d not in online)}
        blind = len(zdev) == 0 or len(online) == 0 or age is None or age > limit
    else:   # backfill: device liveness is a server-clock notion and is meaningless for replayed history
        age = None if last_ts is None else round((now - last_ts).total_seconds(), 1)
        dev = {"total": len(zdev), "online": None, "offline": None, "offline_ids": None}
        blind = len(zdev) == 0 or age is None or age > limit
    return {"mode": mode, "devices": dev, "last_sample_utc": last_ts and last_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_sample_age_s": age, "stale_after_s": limit, "blind_spot": bool(blind),
            "window_flagged_samples": flagged, "usable_pm_samples_in_window": n_usable}
