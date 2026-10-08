"""GET /v1/devices"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.constants import decode_sensor_flags

from ..config import Settings
from ..auth import read_access
from ..deps import get_db, get_settings
from ..models import Device

router = APIRouter(prefix="/v1", tags=["devices"], dependencies=[Depends(read_access("admin"))])


def is_online(d: Device, now: datetime, settings: Settings) -> bool:
    """Online = a packet was RECEIVED recently (server clock), so replayed old data doesn't fake liveness."""
    return (now - d.last_received_at).total_seconds() <= settings.offline_after_s


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


@router.get("/devices")
def list_devices(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    now = datetime.now(timezone.utc)
    out = []
    for d in db.scalars(select(Device).order_by(Device.device_id)):
        out.append({
            "device_id": d.device_id,
            "kind": d.kind,
            "zone_id": d.last_zone_id or d.zone_id,
            "home_zone_id": d.zone_id,
            "status": "online" if is_online(d, now, settings) else "offline",
            "last_packet_utc": iso(d.last_packet_ts),          # device clock
            "last_received_utc": iso(d.last_received_at),      # server clock
            "last_age_s": round((now - d.last_received_at).total_seconds(), 1),
            "last_sequence": d.last_sequence,
            "firmware_version": d.firmware_version,
            "calibration_version": d.calibration_version,
            "sensor_flags": d.last_sensor_flags,
            "sensor_faults": decode_sensor_flags(d.last_sensor_flags or 0),
            "warmup": d.last_warmup,
            "gps_valid": d.last_gps_valid,
            "last_lat": d.last_lat,
            "last_lon": d.last_lon,
            "battery": None,       # not part of data contract v1.0; reserved for when firmware reports it
        })
    return {"count": len(out), "offline_after_s": settings.offline_after_s, "devices": out}
