"""Anomaly detection: deviation of the current zone PM from a baseline.

Baseline preference: time-of-day baseline (same IST clock window on up to 7 previous days, needs
>= 2 days of history) -> else rolling median of the trailing hours. A breach needs ALL of:
ratio >= 1.5, absolute rise >= min_abs, robust z-score >= 3. (Thresholds are deliberately plain.)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from .config import WorkerSettings
from .data import bucket_series, load_frame, mad_sigma

METRICS = {"pm25": ("pm25_ugm3", 15.0), "pm10": ("pm10_ugm3", 30.0)}   # (field name, min absolute rise)
RATIO_BREACH, RATIO_CLEAR, Z_BREACH = 1.5, 1.25, 3.0


def severity(ratio: float) -> str:
    return "critical" if ratio >= 3.0 else "high" if ratio >= 2.0 else "warning"


def tod_baselines(db: Session, zone_id: str, as_of: datetime, cfg: WorkerSettings) -> dict[str, Optional[tuple]]:
    """{metric: (median, sigma, n_days) | None} from the same time of day on previous days."""
    pools: dict[str, list[np.ndarray]] = {"pm25": [], "pm10": []}
    for k in range(1, cfg.tod_days + 1):
        c = as_of - timedelta(days=k)
        s = bucket_series(load_frame(db, zone_id, c - timedelta(minutes=60), c + timedelta(minutes=60)),
                          cfg.bucket_min)
        if len(s) >= 6:
            for m in pools:
                pools[m].append(s[m].to_numpy())
    out: dict[str, Optional[tuple]] = {}
    for m, arrs in pools.items():
        if len(arrs) >= cfg.tod_min_days:
            med, sig = mad_sigma(np.concatenate(arrs))
            out[m] = (med, sig, len(arrs))
        else:
            out[m] = None
    return out


def evaluate(series: pd.DataFrame, as_of: datetime, cfg: WorkerSettings,
             tod: Optional[dict] = None) -> dict[str, dict]:
    """Per-metric evaluation dicts ({} if there is not enough history to judge)."""
    if series.empty:
        return {}
    cur_from = as_of - timedelta(minutes=cfg.backend.current_window_min)
    cur = series[series.index > cur_from]
    hist = series[(series.index <= as_of - timedelta(minutes=30))
                  & (series.index >= as_of - timedelta(hours=cfg.anomaly_lookback_h))]
    if cur.empty or len(hist) < cfg.anomaly_min_history_buckets:
        return {}
    out = {}
    for m, (field, min_abs) in METRICS.items():
        value = float(cur[m].mean())
        r_med, r_sig = mad_sigma(hist[m].to_numpy())
        if tod and tod.get(m):
            base, sig, method = tod[m][0], tod[m][1], f"time_of_day({tod[m][2]}d)"
        else:
            base, sig, method = r_med, r_sig, f"rolling_median({cfg.anomaly_lookback_h}h)"
        ratio = value / base if base > 0 else float("inf")
        z = (value - base) / sig
        rise = value - base
        out[m] = {"metric": field, "value": round(value, 1), "baseline": round(base, 1),
                  "baseline_method": method, "rolling_median": round(r_med, 1),
                  "ratio": round(ratio, 2), "z": round(z, 2), "rise": round(rise, 1),
                  "breach": bool(ratio >= RATIO_BREACH and rise >= min_abs and z >= Z_BREACH),
                  "clear": bool(ratio < RATIO_CLEAR),
                  "severity": severity(ratio), "n_current_buckets": int(len(cur)),
                  "n_history_buckets": int(len(hist))}
    return out
