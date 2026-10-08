"""GET /v1/zones, /v1/zones/{id}/history, /v1/zones/{id}/forecast"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.constants import GAS_PROXY_LABEL, decode_sensor_flags

from ..config import Settings
from ..auth import read_access
from ..deps import get_db, get_settings
from ..models import Device, DerivedMeasurement, Forecast, Measurement, Zone
from ..stats import window_stats, zone_devices
from .devices import iso, is_online

router = APIRouter(prefix="/v1", tags=["zones"], dependencies=[Depends(read_access("user"))])

HISTORY_FIELDS = ("pm25_ugm3", "pm10_ugm3", "temperature_c", "humidity_pct", "pressure_hpa",
                  "mq2_adc", "mq9_adc", "mq135_adc", "speed_kmh")
FIELD_NOTES = {f: GAS_PROXY_LABEL for f in ("mq2_adc", "mq9_adc", "mq135_adc")}
FORECAST_HORIZONS = (15, 30, 60, 360, 1440, 4320)   # minutes; 6 h / 24 h / 72 h come from the ML model


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    return None if dt is None else (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt)


def _zone_or_404(db: Session, zone_id: str) -> Zone:
    z = db.get(Zone, zone_id)
    if z is None:
        raise HTTPException(404, detail=f"unknown zone_id '{zone_id}'")
    return z


@router.get("/zones")
def list_zones(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    now = datetime.now(timezone.utc)
    devices = list(db.scalars(select(Device)))
    out = []
    for z in db.scalars(select(Zone).order_by(Zone.zone_id)):
        zdev = zone_devices(devices, z.zone_id)
        last_ts, last_rx = db.execute(select(func.max(Measurement.timestamp_utc),
                                             func.max(Measurement.received_at))
                                      .where(Measurement.zone_id == z.zone_id)).one()
        current = None
        if last_ts is not None:
            last_ts = _utc(last_ts)
            since = last_ts - timedelta(minutes=settings.current_window_min)
            st = window_stats(db, z.zone_id, since)
            current = {
                "pm25_ugm3": None if st["pm25_ugm3"] is None else round(st["pm25_ugm3"], 1),
                "pm10_ugm3": None if st["pm10_ugm3"] is None else round(st["pm10_ugm3"], 1),
                "window_min": settings.current_window_min,
                "n_samples": st["n_samples"], "n_devices": st["n_devices"],
                "basis": "plain mean of raw PM samples in the window (not smoothed, not an AQI)",
                "flagged_samples": st["flagged_samples"],
            }
        d = db.scalars(select(DerivedMeasurement).where(DerivedMeasurement.zone_id == z.zone_id)
                       .order_by(DerivedMeasurement.window_end.desc()).limit(1)).first()
        out.append({
            "zone_id": z.zone_id, "name": z.name,
            "centroid": {"lat": z.centroid_lat, "lon": z.centroid_lon},
            "radius_km": z.radius_km, "path": z.path,
            "source_context": z.source_context, "source_tags": z.source_tags,
            "current": current,
            # derived values come from the Phase-3 worker; null until it has written something
            "aqi": None if d is None or d.aqi is None else {
                "value": d.aqi, "category": d.aqi_category, "pm25_smoothed": d.pm25_smoothed,
                "pm10_smoothed": d.pm10_smoothed, "method_version": d.method_version,
                "detail": d.aqi_detail,
                "basis": "PM-only indicative AQI (CPCB breakpoints) from a rolling-mean PM; not the official 24-h average",
                "window_end_utc": iso(_utc(d.window_end))},
            "source_hypothesis": None if d is None or d.source_label is None else {
                "label": d.source_label, "confidence": d.source_confidence,
                "features": d.source_features,
                "note": "heuristic hypothesis (probable), not a lab measurement"},
            "coverage": d.coverage if d is not None else None,
            "last_seen_utc": iso(last_ts), "last_received_utc": iso(_utc(last_rx)),
            "stale": last_rx is None or (now - _utc(last_rx)).total_seconds() > settings.offline_after_s,
            "devices": {"total": len(zdev), "online": sum(is_online(x, now, settings) for x in zdev),
                        "ids": sorted(x.device_id for x in zdev)},
        })
    return {"generated_at": iso(now), "zones": out}


@router.get("/zones/{zone_id}/history")
def zone_history(
    zone_id: str,
    from_: Optional[datetime] = Query(None, alias="from", description="UTC; default = to - 6h"),
    to: Optional[datetime] = Query(None, description="UTC; default = newest sample in the zone"),
    fields: str = Query("pm25_ugm3,pm10_ugm3", description=f"comma list of: {', '.join(HISTORY_FIELDS)}"),
    max_points: int = Query(300, ge=2, le=2000),
    bucket_s: Optional[int] = Query(None, ge=1, description="override bucket width (seconds)"),
    device_id: Optional[str] = None,
    include_smoothed: bool = Query(True, description="add the worker's rolling-mean PM as a separate 'smoothed' block"),
    db: Session = Depends(get_db),
):
    _zone_or_404(db, zone_id)
    want = [f.strip() for f in fields.split(",") if f.strip()]
    bad = [f for f in want if f not in HISTORY_FIELDS]
    if bad or not want:
        raise HTTPException(422, detail={"error": "invalid fields", "unknown": bad,
                                         "allowed": list(HISTORY_FIELDS)})

    base = [Measurement.zone_id == zone_id]
    if device_id:
        base.append(Measurement.device_id == device_id)
    to_, from__ = _utc(to), _utc(from_)
    if to_ is None:
        to_ = _utc(db.scalar(select(func.max(Measurement.timestamp_utc)).where(*base)))
    if to_ is None:                                   # zone has no data at all
        return {"zone_id": zone_id, "from": None, "to": None, "bucket_s": None, "fields": want,
                "points": [], "meta": {"note": "no samples for this zone yet"}}
    from__ = from__ or to_ - timedelta(hours=6)
    if from__ >= to_:
        raise HTTPException(422, detail="'from' must be earlier than 'to'")

    span = (to_ - from__).total_seconds()
    width = bucket_s or max(1, math.ceil(span / max_points))
    cols = [Measurement.timestamp_utc, Measurement.warmup, Measurement.gps_valid,
            Measurement.sensor_flags] + [getattr(Measurement, f) for f in want]
    rows = db.execute(select(*cols).where(*base, Measurement.timestamp_utc >= from__,
                                          Measurement.timestamp_utc <= to_)
                      .order_by(Measurement.timestamp_utc)).all()

    buckets: dict[int, list] = defaultdict(list)
    for r in rows:
        buckets[int(_utc(r[0]).timestamp() // width)].append(r)
    points = []
    for b in sorted(buckets):
        rs = buckets[b]
        values = {}
        for i, f in enumerate(want):
            vs = [r[4 + i] for r in rs if r[4 + i] is not None]
            values[f] = round(sum(vs) / len(vs), 2) if vs else None      # all-null stays null
        mask = 0
        for r in rs:
            mask |= r[3]
        points.append({
            "t": iso(datetime.fromtimestamp(b * width, tz=timezone.utc)),
            "n": len(rs), "values": values,
            "flags": {"warmup_samples": sum(1 for r in rs if r[1]),
                      "gps_invalid_samples": sum(1 for r in rs if not r[2]),
                      "sensor_fault_samples": sum(1 for r in rs if r[3]),
                      "sensor_fault_mask": mask, "sensor_faults": decode_sensor_flags(mask)},
        })
    smoothed_note = None
    if include_smoothed and not device_id and any(f in ("pm25_ugm3", "pm10_ugm3") for f in want):
        # worker output (derived_measurements), kept strictly separate from the raw values above
        sm: dict[int, list] = defaultdict(list)
        for we, p25, p10 in db.execute(select(DerivedMeasurement.window_end, DerivedMeasurement.pm25_smoothed,
                                              DerivedMeasurement.pm10_smoothed).where(
                DerivedMeasurement.zone_id == zone_id, DerivedMeasurement.window_end >= from__,
                DerivedMeasurement.window_end <= to_)):
            sm[int(_utc(we).timestamp() // width)].append((p25, p10))
        for pt in points:
            b = int(datetime.fromisoformat(pt["t"].replace("Z", "+00:00")).timestamp() // width)
            vals = sm.get(b, [])
            m25 = [v[0] for v in vals if v[0] is not None]
            m10 = [v[1] for v in vals if v[1] is not None]
            pt["smoothed"] = {"pm25_ugm3": round(sum(m25) / len(m25), 2) if m25 else None,
                              "pm10_ugm3": round(sum(m10) / len(m10), 2) if m10 else None}
        smoothed_note = (f"'smoothed' = worker's rolling mean of the last 30 min of zone PM (derived_measurements); "
                         "null where the worker has not run. Raw 'values' are never altered.")
    return {"zone_id": zone_id, "from": iso(from__), "to": iso(to_), "bucket_s": width, "fields": want,
            "points": points,
            "meta": {"aggregation": "mean of non-null samples per bucket", "smoothed": smoothed_note,
                     "n_raw_samples": len(rows),
                     "field_notes": {f: FIELD_NOTES[f] for f in want if f in FIELD_NOTES}}}


@router.get("/zones/{zone_id}/forecast")
def zone_forecast(zone_id: str, db: Session = Depends(get_db)):
    """Latest forecast set written by the worker. Until the worker exists: status='unavailable'."""
    _zone_or_404(db, zone_id)
    issued = db.scalar(select(func.max(Forecast.issued_at)).where(Forecast.zone_id == zone_id))
    base = {"zone_id": zone_id, "horizons_min": list(FORECAST_HORIZONS),
            "methods": ["persistence", "linear_trend", "gbt"],
            "disclaimer": "Short-horizon projection from locally available sensor data only; "
                          "the method that produced each value is listed with it."}
    if issued is None:
        return {**base, "status": "unavailable", "issued_at": None, "forecasts": [],
                "reason": "forecast worker has not produced any forecast yet"}
    # each method has its own cadence (the learned model runs every few minutes, the baselines every window): take the newest set of EACH
    per_method = dict(db.execute(select(Forecast.method, func.max(Forecast.issued_at)).where(Forecast.zone_id == zone_id)
                                 .group_by(Forecast.method)).all())
    rows = []
    for m, when in per_method.items():
        rows += list(db.scalars(select(Forecast).where(Forecast.zone_id == zone_id, Forecast.method == m, Forecast.issued_at == when)))
    rows.sort(key=lambda f: (f.horizon_min, f.method))
    return {**base, "status": "ok", "issued_at": iso(_utc(issued)),
            "issued_at_by_method": {m: iso(_utc(t)) for m, t in per_method.items()}, "forecasts": [{
        "horizon_min": f.horizon_min, "method": f.method, "model_version": f.model_version,
        "pm25_pred": f.pm25_pred, "pm10_pred": f.pm10_pred, "confidence": f.confidence,
        "aqi_pred": (f.basis or {}).get("aqi"), "aqi_p10": (f.basis or {}).get("aqi_p10"), "aqi_p90": (f.basis or {}).get("aqi_p90"),
        "basis": f.basis} for f in rows]}


@router.get("/zones/{zone_id}/derived")
def zone_derived_history(zone_id: str, from_: Optional[datetime] = Query(None, alias="from"),
                         to: Optional[datetime] = None, limit: int = Query(500, ge=1, le=5000),
                         db: Session = Depends(get_db)):
    """History of worker outputs (AQI, smoothed PM, source hypothesis) by `window_end`, oldest first.
    Used for evaluation and for plotting hypothesis changes over time."""
    _zone_or_404(db, zone_id)
    q = select(DerivedMeasurement).where(DerivedMeasurement.zone_id == zone_id)
    if from_:
        q = q.where(DerivedMeasurement.window_end >= _utc(from_))
    if to:
        q = q.where(DerivedMeasurement.window_end <= _utc(to))
    rows = db.scalars(q.order_by(DerivedMeasurement.window_end).limit(limit))
    return {"zone_id": zone_id, "rows": [{
        "window_end_utc": iso(_utc(r.window_end)), "computed_at_utc": iso(_utc(r.computed_at)),
        "method_version": r.method_version, "n_samples": r.n_samples,
        "pm25_smoothed": r.pm25_smoothed, "pm10_smoothed": r.pm10_smoothed,
        "aqi": r.aqi, "aqi_category": r.aqi_category,
        "source_label": r.source_label, "source_confidence": r.source_confidence,
    } for r in rows]}
