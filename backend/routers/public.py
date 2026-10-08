"""Public, no-login endpoints for the Aeronex landing page: a small air-quality teaser and site config.

Deliberately coarse: per-zone AQI + category only. Source attribution, forecasts, events, device data and
history stay behind login (see /v1/auth).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..deps import get_db, get_settings
from ..models import Device, DerivedMeasurement, Zone
from .devices import iso, is_online

router = APIRouter(prefix="/v1/public", tags=["public"])


@router.get("/config")
def public_config(settings: Settings = Depends(get_settings)):
    return {"product": "Aeronex", "dashboard_url": settings.dashboard_url}


@router.get("/summary")
def public_summary(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    now = datetime.now(timezone.utc)
    zones = []
    for z in db.scalars(select(Zone).order_by(Zone.zone_id)):
        d = db.scalars(select(DerivedMeasurement).where(DerivedMeasurement.zone_id == z.zone_id,
                                                        DerivedMeasurement.aqi.is_not(None))
                       .order_by(DerivedMeasurement.window_end.desc()).limit(1)).first()
        zones.append({"zone_id": z.zone_id, "name": z.name,
                      "aqi": None if d is None else d.aqi,
                      "category": None if d is None else d.aqi_category,
                      "pm25": None if d is None else d.pm25_smoothed})
    with_aqi = [z for z in zones if z["aqi"] is not None]
    devices = list(db.scalars(select(Device)))
    worst = max(with_aqi, key=lambda z: z["aqi"]) if with_aqi else None
    return {
        "generated_at": iso(now),
        "zones": zones,
        "city": {"aqi_avg": round(sum(z["aqi"] for z in with_aqi) / len(with_aqi)) if with_aqi else None,
                 "worst_zone": worst["name"] if worst else None, "worst_aqi": worst["aqi"] if worst else None},
        "network": {"zones": len(zones), "devices": len(devices),
                    "online": sum(is_online(x, now, settings) for x in devices)},
        "note": "Indicative PM-based AQI (CPCB breakpoints) from prototype sensors. Sign in for full detail.",
    }
