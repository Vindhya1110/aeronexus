"""One analytics pass over all zones. Idempotent: rerunning on unchanged data adds only a fresh
coverage snapshot; events and forecasts change only when a zone has newer data.

Raw measurements are only ever READ here. Output goes to derived_measurements, events, forecasts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.models import DerivedMeasurement, Device, Forecast, Measurement, Zone
from backend.stats import window_stats
from shared.zones import get_zone

from .anomaly import evaluate, tod_baselines
from .aqi import compute_aqi
from .config import WorkerSettings
from .coverage import zone_coverage
from .data import bucket_series, load_frame
from .forecast import make_forecasts
from .lifecycle import update_events
from .ml_runtime import MLRuntime
from .source import MqBaselines, hypothesize

log = logging.getLogger("airdna.worker")
UTC = timezone.utc


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    return None if dt is None else (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt)


class Worker:
    def __init__(self, factory: sessionmaker[Session], cfg: Optional[WorkerSettings] = None):
        self.factory, self.cfg = factory, cfg or WorkerSettings.from_env()
        self.mq = MqBaselines(self.cfg)
        self._tod: dict[tuple, dict] = {}
        self.ml = MLRuntime(interval_min=self.cfg.ml_interval_min)

    # ------------------------------------------------------------------
    def run_pass(self, as_of: Optional[datetime] = None, now: Optional[datetime] = None) -> list[dict]:
        """as_of=None: live mode (each zone evaluated at its newest sample, coverage vs server clock).
        as_of=T: backfill mode (everything evaluated as if the data ended at T)."""
        now = now or datetime.now(UTC)
        out = []
        with self.factory() as db:
            devices = list(db.scalars(select(Device)))
            for z in list(db.scalars(select(Zone).order_by(Zone.zone_id))):
                try:
                    out.append(self._zone(db, z, devices, as_of, now))
                except Exception:
                    db.rollback()
                    log.exception("zone %s failed", z.zone_id)
        return out

    # ------------------------------------------------------------------
    def _tod_for(self, db: Session, zone_id: str, end: datetime) -> dict:
        key = (zone_id, end.date(), end.hour)
        if key not in self._tod:
            self._tod[key] = tod_baselines(db, zone_id, end, self.cfg)
            if len(self._tod) > 500:
                self._tod.pop(next(iter(self._tod)))
        return self._tod[key]

    def _zone(self, db: Session, z: Zone, devices: list[Device], as_of: Optional[datetime],
              now: datetime) -> dict:
        cfg, mode = self.cfg, ("backfill" if as_of else "live")
        q = select(func.max(Measurement.timestamp_utc), func.max(Measurement.received_at)).where(
            Measurement.zone_id == z.zone_id)
        if as_of:
            q = q.where(Measurement.timestamp_utc <= as_of)
        last_ts, last_rx = (_utc(x) for x in db.execute(q).one())
        clock = as_of or now

        if last_ts is None:                                  # zone never heard from: say so, invent nothing
            cov = zone_coverage(z.zone_id, devices, clock, cfg, None, None, mode)
            db.add(DerivedMeasurement(zone_id=z.zone_id, window_end=clock, computed_at=now,
                                      method_version=cfg.method_version, coverage=cov))
            db.commit()
            return {"zone": z.zone_id, "status": "no data"}

        end = as_of or last_ts
        frame = load_frame(db, z.zone_id, end - timedelta(hours=cfg.anomaly_lookback_h), end)
        series = bucket_series(frame, cfg.bucket_min)

        # --- smoothed PM + AQI (raw PM only) -----------------------------------
        win = series[series.index > end - timedelta(minutes=cfg.smoothing_window_min)]
        pm25_s = None if win.empty else round(float(win["pm25"].mean()), 1)
        pm10_s = None if win.empty else round(float(win["pm10"].mean()), 1)
        aqi = compute_aqi(pm25_s, pm10_s)
        in_win = frame[(frame["timestamp_utc"] > end - timedelta(minutes=cfg.smoothing_window_min))
                       & frame["pm_ok"]] if not frame.empty else frame
        n_win, n_dev = int(len(in_win)), (int(in_win["device_id"].nunique()) if len(in_win) else 0)

        # --- anomaly baseline / source hypothesis ------------------------------------
        evals = evaluate(series, end, cfg, self._tod_for(db, z.zone_id, end))
        pm_base = {m: e["baseline"] for m, e in evals.items()} or None
        tags = tuple(get_zone(z.zone_id).source_tags) if get_zone(z.zone_id) else tuple(z.source_tags or ())
        src = hypothesize(db, frame, end, tags, pm_base, self.mq, cfg)

        # --- events/forecasts only advance when this window_end has not been processed yet -----
        already = db.scalar(select(func.count(DerivedMeasurement.id)).where(
            DerivedMeasurement.zone_id == z.zone_id, DerivedMeasurement.window_end == end,
            DerivedMeasurement.aqi.is_not(None)))
        new_data = not already
        n_events = n_fc = 0
        # learned models (forecast, early warning, source classifier, anomaly score): add-on, never allowed to break the pass
        ml_out = self.ml.run_zone(db, z.zone_id, end, end, write=new_data)
        if ml_out.get("source_ml"):
            src["features"]["ml_attribution"] = ml_out["source_ml"]
        if ml_out.get("anomaly_if"):
            src["features"]["anomaly_if"] = ml_out["anomaly_if"]
        if new_data:
            n_events = len(update_events(db, z.zone_id, evals, end, src, cfg))
            cur = window_stats(db, z.zone_id, end - timedelta(minutes=cfg.backend.current_window_min), end,
                               exclude_flagged=True)
            for row in make_forecasts(series, {"pm25": cur["pm25_ugm3"], "pm10": cur["pm10_ugm3"]}, end, cfg):
                db.add(Forecast(zone_id=z.zone_id, issued_at=end, **row))
                n_fc += 1

        flagged = window_stats(db, z.zone_id, end - timedelta(minutes=cfg.smoothing_window_min), end)
        cov = zone_coverage(z.zone_id, devices, clock, cfg, last_ts, last_rx, mode,
                            flagged["flagged_samples"], n_win)
        db.add(DerivedMeasurement(
            zone_id=z.zone_id, window_end=end, computed_at=now, method_version=cfg.method_version,
            n_samples=n_win, n_devices=n_dev, pm25_smoothed=pm25_s, pm10_smoothed=pm10_s,
            aqi=aqi["aqi"], aqi_category=aqi["category"], aqi_detail=aqi["detail"],
            source_label=src["label"], source_confidence=src["confidence"],
            source_features={**src["features"], "anomaly_checks": evals} if evals else src["features"],
            coverage=cov))
        db.commit()
        return {"zone": z.zone_id, "window_end": end, "aqi": aqi["aqi"], "category": aqi["category"],
                "pm25_smoothed": pm25_s, "source": src["label"], "confidence": src["confidence"],
                "events_touched": n_events, "forecast_rows": n_fc, "blind_spot": cov["blind_spot"]}
