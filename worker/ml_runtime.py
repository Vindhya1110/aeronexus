"""Runs the trained ML models inside the worker pass (forecast, early warning, source classifier, learned anomaly score).

Safety rules:
  * ML is an ADD-ON. Any failure (missing artifact, too little data, exception) is logged and skipped; the rule-based and
    statistical pipeline is never affected.
  * Features are built by the same functions used in training (ml/features.py, ml/source.py): no train/serve skew.
  * Models are only loaded from ml/artifacts/ (trained offline). Nothing here can see simulator ground truth.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Event, Forecast, Insight, Measurement, utcnow

log = logging.getLogger("airdna.worker.ml")
HISTORY_H = 24 * 7 + 2                    # the longest lag feature is 7 days
MIN_HISTORY_H = 24                        # below this the 24 h baselines are missing: warnings are shown but flagged and raise no events


def _isnull(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v))


class MLRuntime:
    def __init__(self, interval_min: int = 5):
        self.interval = timedelta(minutes=interval_min)
        self._last: dict[str, datetime] = {}
        self._cache: dict[str, dict] = {}
        self._available: Optional[bool] = None

    # ------------------------------------------------------------------ data
    @staticmethod
    def zone_series(db: Session, zone_id: str, end: datetime, hours: int = HISTORY_H) -> pd.DataFrame:
        """5-minute zone series (clean samples only) in the same shape the models were trained on."""
        rows = db.execute(select(Measurement.timestamp_utc, Measurement.pm25_ugm3, Measurement.pm10_ugm3, Measurement.mq2_adc,
                                 Measurement.mq9_adc, Measurement.mq135_adc, Measurement.temperature_c, Measurement.humidity_pct,
                                 Measurement.pressure_hpa).where(
            Measurement.zone_id == zone_id, Measurement.timestamp_utc > end - timedelta(hours=hours), Measurement.timestamp_utc <= end,
            Measurement.warmup.is_(False), Measurement.sensor_flags == 0)).all()
        if not rows:
            return pd.DataFrame()
        d = pd.DataFrame(rows, columns=["ts", "pm25", "pm10", "mq2", "mq9", "mq135", "temp", "hum", "pres"])
        d["ts"] = pd.to_datetime(d["ts"], utc=True)
        d = d.dropna(subset=["pm25", "pm10"])
        d["bucket"] = d["ts"].dt.floor("5min")
        s = d.groupby("bucket")[["pm25", "pm10", "mq2", "mq9", "mq135", "temp", "hum", "pres"]].mean().reset_index()
        s["zone"] = zone_id
        return s

    # ------------------------------------------------------------------ main entry
    def run_zone(self, db: Session, zone_id: str, end: datetime, issued_at: datetime, write: bool = True) -> dict:
        """Returns {'source_ml':..., 'anomaly_if':..., 'warning':...} and (if write) stores forecast rows, the early-warning
        insight and early-warning events. Cached: recomputed at most once per `interval` of data time per zone."""
        if self._available is False:
            return {}
        last = self._last.get(zone_id)
        if last is not None and end - last < self.interval:
            return self._cache.get(zone_id, {})
        try:
            from ml import forecast as mf, hotspots as mh, source as ms, warning as mw
        except Exception as e:  # noqa: BLE001 - scikit-learn etc. missing
            log.warning("ML disabled (%s)", e)
            self._available = False
            return {}
        series = self.zone_series(db, zone_id, end)
        out: dict = {}
        if series.empty or len(series) < 6:
            return out
        try:
            fc, wn, sm, an = mf.load(), mw.load(), ms.load(), mh.load_if()
            if sm is None and wn is None and fc is None:
                self._available = False
                log.info("no ML artifacts found in ml/artifacts/ - ML features inactive (run python -m ml.forecast train ...)")
                return {}
            if sm is not None:
                a = ms.predict(series, zone_id, sm)
                if a:
                    a["origin"] = a["origin"].isoformat()
                    out["source_ml"] = a
            if an is not None:
                a = mh.anomaly_score(series, zone_id, an)
                if a:
                    a["origin"] = a["origin"].isoformat()
                    out["anomaly_if"] = a
            if write and fc is not None:
                rows = mf.predict(series, zone_id, fc)
                out["forecast"] = rows
                self._store_forecast(db, zone_id, issued_at, rows, fc)
            if wn is not None:
                w = mw.predict(series, zone_id, wn)
                if w:
                    w["origin"] = w["origin"].isoformat()
                    out["warning"] = w
                    if write:
                        self._store_warning(db, zone_id, end, w)
            self._last[zone_id], self._cache[zone_id] = end, out
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("ML step failed for %s (continuing without it)", zone_id)
        return out

    # ------------------------------------------------------------------ storage
    @staticmethod
    def _store_forecast(db: Session, zone_id: str, issued_at: datetime, rows: list[dict], bundle: dict) -> None:
        rep = bundle["meta"]["report"]
        from ml.forecast import horizon_label
        for r in rows:
            lab = horizon_label(r["horizon_min"])
            skill = rep.get(lab, {})
            mae = skill.get("gbt", {}).get("mae")
            conf = None if _isnull(mae) else float(np.clip(1 - mae / 100.0, 0.05, 0.95))     # crude: smaller held-out error = higher
            db.add(Forecast(zone_id=zone_id, issued_at=issued_at, horizon_min=r["horizon_min"], method="gbt", model_version="gbt-1.0",
                            pm25_pred=None, pm10_pred=None, confidence=conf,
                            basis={"aqi": round(r["aqi"], 1), "aqi_p10": round(r["aqi_p10"], 1), "aqi_p90": round(r["aqi_p90"], 1),
                                   "history_hours": r["history_hours"], "test_mae_aqi": mae,
                                   "test_skill_vs_best_baseline": skill.get("skill_vs_best_baseline"),
                                   "rule": "gradient-boosted trees on residual vs simple baseline; split-conformal 80% interval",
                                   "data": "zone sensors (PM, temperature, humidity, pressure) + calendar; no wind/traffic feed"}))

    @staticmethod
    def _store_warning(db: Session, zone_id: str, end: datetime, w: dict) -> None:
        now = utcnow()
        row = db.scalar(select(Insight).where(Insight.kind == "early_warning", Insight.key == zone_id))
        limited = w.get("history_hours", 0) < MIN_HISTORY_H
        payload = {"zone_id": zone_id, "as_of": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "lookahead_min": w["lookahead_min"],
                   "aqi_now": w["aqi_now"], "history_hours": w.get("history_hours"), "limited_history": limited,
                   **{k: w[k] for k in ("poor", "very_poor") if k in w}}
        if row is None:
            db.add(Insight(kind="early_warning", key=zone_id, source="database", computed_at=now, payload=payload))
        else:
            row.payload, row.computed_at = payload, now
        # events: one open early-warning event per zone while the 'very poor' alert is on (poor alert is shown, not an event)
        vp = w.get("very_poor", {})
        open_ev = db.scalar(select(Event).where(Event.zone_id == zone_id, Event.event_type == "early_warning", Event.status != "closed"))
        # events need >= 24 h of history behind the features, and use hysteresis (open at the cut-off, close below 70% of it)
        # so a probability hovering around the cut-off does not flap open/closed every few minutes
        # a WARNING is about a crossing that has not happened yet: once the AQI is already above the threshold it is a plain
        # exceedance (visible in AQI / events), so the early-warning event closes with a note instead of staying open
        already = w.get("aqi_now") is not None and w["aqi_now"] > vp.get("threshold_aqi", 300)
        alert = bool(vp.get("alert")) and not limited and not already
        keep_open = open_ev is not None and vp.get("p", 0.0) >= 0.7 * vp.get("cutoff", 1.0) and not limited and not already
        alert = alert or keep_open
        if alert and open_ev is None:
            db.add(Event(zone_id=zone_id, event_type="early_warning", status="detected", severity="warning", detected_at=end, updated_at=end,
                         details={"metric": "aqi_forecast", "triggered_by": f"P(AQI > 300 within {w['lookahead_min']} min) = {w['very_poor']['p']:.0%}",
                                  "probability": w["very_poor"]["p"], "cutoff": w["very_poor"]["cutoff"],
                                  "recommended_action": "Issue an advance advisory: sensitive groups limit outdoor activity; consider traffic/dust control before the peak.",
                                  "recommended_basis": "early-warning classifier (learned, uncalibrated for real hardware)",
                                  "transitions": [{"status": "detected", "at": end.strftime("%Y-%m-%dT%H:%M:%SZ")}],
                                  "note": "predictive alert from the learned early-warning model, not a measured exceedance"}))
        elif alert and open_ev is not None:
            d = dict(open_ev.details)
            d.update(probability=w["very_poor"]["p"])
            open_ev.details, open_ev.updated_at = d, end
        elif not alert and open_ev is not None:
            d = dict(open_ev.details)
            d.setdefault("transitions", []).append({"status": "closed", "at": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
            if already:
                d["closed_reason"] = "threshold reached (now a measured exceedance, not a prediction)"
            open_ev.details, open_ev.status, open_ev.updated_at = d, "closed", end
