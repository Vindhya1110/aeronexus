"""Persist one validated telemetry packet. Pure DB work: no analytics, no network - this is the
whole synchronous cost of POST /v1/telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.schema import TelemetryPayload, quality_summary
from shared.zones import nearest_zone, zone_code_from_device_id, get_zone

from .models import Device, Measurement, RawMeasurement


class DuplicatePacket(Exception):
    pass


@dataclass
class Stored:
    device_id: str
    sequence: int
    timestamp_utc: datetime
    zone_id: Optional[str]
    zone_source: Optional[str]
    quality_flags: list[str]


def resolve_zone(p: TelemetryPayload, device: Optional[Device]) -> tuple[Optional[str], Optional[str]]:
    """(zone_id, how). Trust GPS only when quality.gps_valid; otherwise fall back honestly:
    fixed node -> home zone from its device ID; bus -> its last known zone; else unresolved.
    """
    if p.quality.gps_valid and p.gps.lat is not None and p.gps.lon is not None:
        z = nearest_zone(p.gps.lat, p.gps.lon)
        if z:
            return z.zone_id, "gps"
        if p.is_mobile_device:
            return None, None                   # bus currently outside every seeded zone
    code = zone_code_from_device_id(p.device_id)
    if code and get_zone(code):
        return code, "device_home"
    if device is not None and device.last_zone_id:
        return device.last_zone_id, "last_known"
    return None, None


def _insert(db: Session, p: TelemetryPayload, raw: dict, now: datetime) -> Stored:
    device = db.get(Device, p.device_id)
    zone_id, zone_source = resolve_zone(p, device)
    flags = quality_summary(p)

    if device is None:
        code = zone_code_from_device_id(p.device_id)       # home zone is encoded in fixed-node IDs
        device = Device(device_id=p.device_id, kind="mobile" if p.is_mobile_device else "fixed",
                        zone_id=code if code and get_zone(code) else None,
                        first_seen_at=now, last_received_at=now)
        db.add(device)
    device.last_received_at = now
    # packets can arrive out of order (store-and-forward/replay): only newer ones move "last packet"
    if device.last_packet_ts is None or p.timestamp_utc >= device.last_packet_ts:
        device.last_packet_ts, device.last_sequence = p.timestamp_utc, p.sequence
        device.firmware_version, device.calibration_version = p.firmware_version, p.calibration_version
        device.last_warmup, device.last_gps_valid = p.quality.warmup, p.quality.gps_valid
        device.last_sensor_flags = p.quality.sensor_flags
        if p.quality.gps_valid:
            device.last_lat, device.last_lon = p.gps.lat, p.gps.lon
        if zone_id:
            device.last_zone_id = zone_id

    rm = RawMeasurement(device_id=p.device_id, sequence=p.sequence, timestamp_utc=p.timestamp_utc,
                        received_at=now, payload=raw)
    db.add(rm)
    db.flush()                                   # raises IntegrityError on duplicate (device_id, sequence)
    db.add(Measurement(
        raw_id=rm.id, device_id=p.device_id, zone_id=zone_id, zone_source=zone_source,
        timestamp_utc=p.timestamp_utc, received_at=now,
        lat=p.gps.lat, lon=p.gps.lon, gps_fix=p.gps.fix, hdop=p.gps.hdop, speed_kmh=p.gps.speed_kmh,
        temperature_c=p.env.temperature_c, humidity_pct=p.env.humidity_pct, pressure_hpa=p.env.pressure_hpa,
        pm25_ugm3=p.pm.pm25_ugm3, pm10_ugm3=p.pm.pm10_ugm3,
        mq2_adc=p.gas_proxy.mq2_adc, mq9_adc=p.gas_proxy.mq9_adc, mq135_adc=p.gas_proxy.mq135_adc,
        warmup=p.quality.warmup, gps_valid=p.quality.gps_valid, sensor_flags=p.quality.sensor_flags,
        quality_flags=flags))
    db.commit()
    return Stored(p.device_id, p.sequence, p.timestamp_utc, zone_id, zone_source, flags)


def store_telemetry(factory, p: TelemetryPayload, raw: dict) -> Stored:
    """Insert raw + typed rows in one transaction. Raises DuplicatePacket on repeated (device, seq)."""
    for attempt in (1, 2):
        with factory() as db:
            try:
                return _insert(db, p, raw, datetime.now(timezone.utc))
            except IntegrityError:
                db.rollback()
                dup = db.scalar(select(RawMeasurement.id).where(
                    RawMeasurement.device_id == p.device_id, RawMeasurement.sequence == p.sequence))
                if dup is not None:
                    raise DuplicatePacket from None
                if attempt == 2:                # not a duplicate: e.g. lost a first-packet device race twice
                    raise
    raise RuntimeError("unreachable")
