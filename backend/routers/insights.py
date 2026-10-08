"""ML-backed read endpoints: early warnings, live city heatmap (kriging), risk ranking, hotspots, trends, calibration.

Everything here is derived data. Early warnings come from the worker's classifier; the heatmap is fitted on request from the
latest GPS-located readings; the risk ranking is computed on request from the latest AQI + early-warning outlook + cached OSM
exposure; hotspots/trends/calibration are long-horizon analyses stored by the worker (or seeded from the simulated year).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import read_access
from ..deps import get_db
from ..models import DerivedMeasurement, Insight, Measurement, Zone
from .devices import iso

router = APIRouter(prefix="/v1", tags=["insights"])
_USER, _ADMIN = [Depends(read_access("user"))], [Depends(read_access("admin"))]


def _insight(db: Session, kind: str, key: str) -> Optional[Insight]:
    return db.scalar(select(Insight).where(Insight.kind == kind, Insight.key == key))


def _row(i: Insight) -> dict:
    return {"computed_at": iso(i.computed_at), "source": i.source, **i.payload}


@router.get("/insights/early-warning", dependencies=_USER)
def early_warning(db: Session = Depends(get_db)):
    """Probability that each zone's AQI crosses 'Poor' (>200) / 'Very Poor' (>300) within the next 3 hours."""
    rows = {i.key: _row(i) for i in db.scalars(select(Insight).where(Insight.kind == "early_warning"))}
    names = {z.zone_id: z.name for z in db.scalars(select(Zone))}
    out = [{"name": names.get(k), **v} for k, v in sorted(rows.items())]
    return {"status": "ok" if out else "unavailable", "zones": out,
            "disclaimer": "Learned early-warning model trained on simulated history; probabilities are not calibrated for real hardware."}


@router.get("/insights/risk", dependencies=_ADMIN)
def risk(db: Session = Depends(get_db)):
    """Zones ranked by health-risk priority: air quality now + outlook, weighted by exposure of vulnerable groups (OSM)."""
    from ml import risk as mrisk
    zones = []
    for z in db.scalars(select(Zone).order_by(Zone.zone_id)):
        d = db.scalars(select(DerivedMeasurement).where(DerivedMeasurement.zone_id == z.zone_id, DerivedMeasurement.aqi.is_not(None))
                       .order_by(DerivedMeasurement.window_end.desc()).limit(1)).first()
        ew = _insight(db, "early_warning", z.zone_id)
        zones.append({"zone_id": z.zone_id, "name": z.name, "aqi": None if d is None else d.aqi,
                      "p_very_poor": None if ew is None else ew.payload.get("very_poor", {}).get("p")})
    rows = mrisk.rank(zones)
    return {"status": "ok" if rows else "unavailable", "ranking": rows,
            "method": "risk = (0.7 x AQI/300 + 0.3 x P(very poor in 3 h)) x (0.5 + 0.5 x exposure index); exposure from OpenStreetMap "
                      "(schools, hospitals, clinics, nursing homes, building density) within 1.5 km of the zone centre",
            "caveat": "transparent scoring, not a trained model; building count is a proxy for residential density (no census data)"}


@router.get("/heatmap", dependencies=_USER)
def heatmap(n: int = 36, db: Session = Depends(get_db)):
    """Estimated AQI on a regular grid over the monitored area (Gaussian-process interpolation of the latest node readings)."""
    from ml import interp
    from ml.features import aqi_vec
    last = db.scalar(select(func.max(Measurement.timestamp_utc)))
    if last is None:
        return {"status": "unavailable", "reason": "no data yet"}
    since = last - timedelta(minutes=15)
    rows = db.execute(select(Measurement.device_id, Measurement.lat, Measurement.lon, Measurement.pm25_ugm3, Measurement.pm10_ugm3)
                      .where(Measurement.timestamp_utc > since, Measurement.timestamp_utc <= last, Measurement.gps_valid.is_(True),
                             Measurement.warmup.is_(False), Measurement.sensor_flags == 0, Measurement.pm25_ugm3.is_not(None))).all()
    d = pd.DataFrame(rows, columns=["device", "lat", "lon", "pm25", "pm10"]).dropna()
    if d.empty:
        return {"status": "unavailable", "reason": "no GPS-located readings in the last 15 minutes of data"}
    d["aqi"] = aqi_vec(d["pm25"], d["pm10"])
    pts = d.groupby("device").agg(lat=("lat", "mean"), lon=("lon", "mean"), aqi=("aqi", "mean")).reset_index()
    grid = interp.predict_grid(pts, n=max(10, min(n, 80)))
    if grid is None:
        return {"status": "unavailable", "reason": "need readings from at least 3 located nodes"}
    return {"status": "ok", "as_of": iso(last), "grid": grid,
            "nodes": pts.round(5).to_dict("records"),
            "caveat": "an ESTIMATE between nodes (kriging); uncertainty (std) is returned per cell and grows away from sensors"}


@router.get("/insights/hotspots", dependencies=_ADMIN)
def hotspots(db: Session = Depends(get_db)):
    i = _insight(db, "hotspots", "city")
    return {"status": "unavailable", "reason": "not computed yet"} if i is None else {"status": "ok", **_row(i)}


@router.get("/insights/trends", dependencies=_USER)
def trends(db: Session = Depends(get_db)):
    i = _insight(db, "trends", "city")
    return {"status": "unavailable", "reason": "not computed yet"} if i is None else {"status": "ok", **_row(i)}


@router.get("/insights/calibration", dependencies=_ADMIN)
def calibration(db: Session = Depends(get_db)):
    i = _insight(db, "calibration", "city")
    return {"status": "unavailable", "reason": "not computed yet"} if i is None else {"status": "ok", **_row(i)}
