"""SQLAlchemy models. Only portable types (no SQLite-isms) so Postgres is a drop-in swap.

Immutability rule: `raw_measurements` rows are insert-only. Anything computed lives in
`derived_measurements` / `events` / `forecasts`; raw values are never overwritten.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        TypeDecorator, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator):
    """Always store UTC; always return timezone-aware UTC (SQLite drops tzinfo otherwise)."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; use UTC-aware datetimes")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: Optional[datetime], dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Zone(Base):
    __tablename__ = "zones"
    zone_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    radius_km: Mapped[float] = mapped_column(Float)
    path: Mapped[list] = mapped_column(JSON)                  # [[lat, lon], ...] polyline
    source_context: Mapped[str] = mapped_column(Text)
    source_tags: Mapped[list] = mapped_column(JSON)


class Device(Base):
    __tablename__ = "devices"
    device_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))              # "fixed" | "mobile"
    zone_id: Mapped[Optional[str]] = mapped_column(ForeignKey("zones.zone_id"), nullable=True)  # home zone (fixed)
    last_zone_id: Mapped[Optional[str]] = mapped_column(ForeignKey("zones.zone_id"), nullable=True)
    firmware_version: Mapped[Optional[str]] = mapped_column(String(32))
    calibration_version: Mapped[Optional[str]] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_received_at: Mapped[datetime] = mapped_column(UTCDateTime)   # server clock; drives online/offline
    last_packet_ts: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)  # device clock
    last_sequence: Mapped[Optional[int]] = mapped_column(Integer)
    last_lat: Mapped[Optional[float]] = mapped_column(Float)
    last_lon: Mapped[Optional[float]] = mapped_column(Float)
    last_gps_valid: Mapped[Optional[bool]] = mapped_column(Boolean)
    last_warmup: Mapped[Optional[bool]] = mapped_column(Boolean)
    last_sensor_flags: Mapped[Optional[int]] = mapped_column(Integer)


class RawMeasurement(Base):
    """Verbatim accepted payload. Insert-only."""
    __tablename__ = "raw_measurements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"))
    sequence: Mapped[int] = mapped_column(Integer)
    timestamp_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    payload: Mapped[dict] = mapped_column(JSON)               # the request body exactly as received
    __table_args__ = (UniqueConstraint("device_id", "sequence", name="uq_raw_device_sequence"),
                      Index("ix_raw_ts", "timestamp_utc"))


class Measurement(Base):
    """Typed, flattened view of each raw record (values as received; null stays null)."""
    __tablename__ = "measurements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_id: Mapped[int] = mapped_column(ForeignKey("raw_measurements.id"), unique=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"))
    zone_id: Mapped[Optional[str]] = mapped_column(ForeignKey("zones.zone_id"), nullable=True)
    zone_source: Mapped[Optional[str]] = mapped_column(String(16))   # gps | device_home | last_known
    timestamp_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    lat: Mapped[Optional[float]] = mapped_column(Float)
    lon: Mapped[Optional[float]] = mapped_column(Float)
    gps_fix: Mapped[bool] = mapped_column(Boolean)
    hdop: Mapped[Optional[float]] = mapped_column(Float)
    speed_kmh: Mapped[Optional[float]] = mapped_column(Float)
    temperature_c: Mapped[Optional[float]] = mapped_column(Float)
    humidity_pct: Mapped[Optional[float]] = mapped_column(Float)
    pressure_hpa: Mapped[Optional[float]] = mapped_column(Float)
    pm25_ugm3: Mapped[Optional[float]] = mapped_column(Float)
    pm10_ugm3: Mapped[Optional[float]] = mapped_column(Float)
    mq2_adc: Mapped[Optional[int]] = mapped_column(Integer)          # proxy index, not a concentration
    mq9_adc: Mapped[Optional[int]] = mapped_column(Integer)
    mq135_adc: Mapped[Optional[int]] = mapped_column(Integer)
    warmup: Mapped[bool] = mapped_column(Boolean)
    gps_valid: Mapped[bool] = mapped_column(Boolean)
    sensor_flags: Mapped[int] = mapped_column(Integer)
    quality_flags: Mapped[list] = mapped_column(JSON)                # human-readable, from shared.schema
    __table_args__ = (Index("ix_meas_zone_ts", "zone_id", "timestamp_utc"),
                      Index("ix_meas_device_ts", "device_id", "timestamp_utc"))


class DerivedMeasurement(Base):
    """Worker output per zone (AQI, smoothed PM, source hypothesis). Never touches raw."""
    __tablename__ = "derived_measurements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.zone_id"))
    window_end: Mapped[datetime] = mapped_column(UTCDateTime)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    method_version: Mapped[str] = mapped_column(String(32))
    n_samples: Mapped[Optional[int]] = mapped_column(Integer)
    n_devices: Mapped[Optional[int]] = mapped_column(Integer)
    pm25_smoothed: Mapped[Optional[float]] = mapped_column(Float)
    pm10_smoothed: Mapped[Optional[float]] = mapped_column(Float)
    aqi: Mapped[Optional[int]] = mapped_column(Integer)
    aqi_category: Mapped[Optional[str]] = mapped_column(String(32))
    aqi_detail: Mapped[Optional[dict]] = mapped_column(JSON)         # sub-indices, dominant pollutant, basis
    source_label: Mapped[Optional[str]] = mapped_column(String(64))
    source_confidence: Mapped[Optional[float]] = mapped_column(Float)
    source_features: Mapped[Optional[dict]] = mapped_column(JSON)
    coverage: Mapped[Optional[dict]] = mapped_column(JSON)
    __table_args__ = (Index("ix_derived_zone_end", "zone_id", "window_end"),)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone_id: Mapped[Optional[str]] = mapped_column(ForeignKey("zones.zone_id"), nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(ForeignKey("devices.device_id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32))      # hotspot | anomaly | ...
    status: Mapped[str] = mapped_column(String(24))          # detected | confirmed | recommended | rechecked | closed
    severity: Mapped[Optional[str]] = mapped_column(String(16))
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    details: Mapped[Optional[dict]] = mapped_column(JSON)
    __table_args__ = (Index("ix_events_zone_detected", "zone_id", "detected_at"),)


class Forecast(Base):
    __tablename__ = "forecasts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.zone_id"))
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime)
    horizon_min: Mapped[int] = mapped_column(Integer)         # 15 | 30 | 60
    method: Mapped[str] = mapped_column(String(24))           # persistence | linear_trend
    model_version: Mapped[str] = mapped_column(String(32))
    pm25_pred: Mapped[Optional[float]] = mapped_column(Float)
    pm10_pred: Mapped[Optional[float]] = mapped_column(Float)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    basis: Mapped[Optional[dict]] = mapped_column(JSON)
    __table_args__ = (Index("ix_fc_zone_issued", "zone_id", "issued_at"),)


class Calibration(Base):
    __tablename__ = "calibrations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[Optional[str]] = mapped_column(ForeignKey("devices.device_id"), nullable=True)
    calibration_version: Mapped[str] = mapped_column(String(32))
    sensor: Mapped[str] = mapped_column(String(24))
    params: Mapped[Optional[dict]] = mapped_column(JSON)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class User(Base):
    """Dashboard / API accounts. Passwords are stored only as salted scrypt hashes."""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80), default="")
    role: Mapped[str] = mapped_column(String(8))               # "user" | "admin"
    password_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class Insight(Base):
    """Latest ML / analytics result per (kind, key): early_warning/<zone>, hotspots/city, risk/city, trends/<zone>, calibration/city.
    One row each, overwritten by the worker; `payload` is JSON and `source` says where the numbers came from."""
    __tablename__ = "insights"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(48), default="database")
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("kind", "key", name="uq_insight_kind_key"),)
