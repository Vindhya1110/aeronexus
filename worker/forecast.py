"""Forecast: persistence baseline and linear (OLS) trend, side by side, for 15/30/60 min.

Both use only this zone's own recent history. Confidence = 1 - MAPE of that same method on the
zone's own last few hours (backtest at the same horizon); null when there is too little history
to backtest. No ML.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from .config import WorkerSettings

HORIZONS = (15, 30, 60)
PERSISTENCE = ("persistence", "persistence-1.0")
TREND = ("linear_trend", "linear-ols-1.0")
MIN_TREND_POINTS, MIN_BACKTEST = 5, 6


def _centers(series: pd.DataFrame, bucket_min: int) -> np.ndarray:
    """Bucket centres in minutes since the epoch (independent of the index's datetime resolution)."""
    return np.asarray((series.index - pd.Timestamp("1970-01-01", tz="UTC")) / pd.Timedelta(minutes=1),
                      dtype=float) + bucket_min / 2.0


def _fit(x: np.ndarray, y: np.ndarray) -> Optional[tuple[float, float, float]]:
    """OLS line y = a*x + b -> (slope_per_min, intercept, r2)."""
    if len(x) < MIN_TREND_POINTS or np.ptp(x) == 0:
        return None
    a, b = np.polyfit(x, y, 1)
    pred = a * x + b
    ss_res, ss_tot = float(np.sum((y - pred) ** 2)), float(np.sum((y - y.mean()) ** 2))
    return float(a), float(b), (1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def _trend_predict(x: np.ndarray, y: np.ndarray, at_min: float) -> Optional[float]:
    f = _fit(x, y)
    if f is None:
        return None
    a, b, _ = f
    return float(max(0.0, min(a * at_min + b, 3.0 * float(np.max(y)))))      # no negative / absurd values


def _backtest(series: pd.DataFrame, metric: str, cfg: WorkerSettings) -> dict[tuple[str, int], Optional[float]]:
    """MAPE of each (method, horizon) on this zone's recent history, or None if < MIN_BACKTEST points."""
    if len(series) < MIN_TREND_POINTS + 2:
        return {}
    xs = _centers(series, cfg.bucket_min)
    ys = series[metric].to_numpy()
    lookup = {int(round(x)): y for x, y in zip(xs, ys)}
    errs: dict[tuple[str, int], list[float]] = {(m, h): [] for m in ("persistence", "linear_trend") for h in HORIZONS}
    for i in range(MIN_TREND_POINTS - 1, len(xs)):
        origin = xs[i]
        win = (xs <= origin) & (xs > origin - cfg.trend_window_min)
        for h in HORIZONS:
            actual = lookup.get(int(round(origin + h)))
            if actual is None or actual <= 0:
                continue
            errs[("persistence", h)].append(abs(ys[i] - actual) / actual)
            p = _trend_predict(xs[win], ys[win], origin + h)
            if p is not None:
                errs[("linear_trend", h)].append(abs(p - actual) / actual)
    return {k: (float(np.mean(v)) if len(v) >= MIN_BACKTEST else None) for k, v in errs.items()}


def make_forecasts(series: pd.DataFrame, current: dict, as_of: datetime, cfg: WorkerSettings) -> list[dict]:
    """Rows (dicts) for the forecasts table. `current` = {'pm25':..., 'pm10':...} from the shared
    current-window stats (the persistence baseline). Returns [] if there is no current value."""
    if current.get("pm25") is None or series.empty:
        return []
    xs = _centers(series, cfg.bucket_min)
    now_min = as_of.timestamp() / 60.0
    win = (xs <= now_min) & (xs > now_min - cfg.trend_window_min)
    bt25 = _backtest(series, "pm25", cfg)
    fit25 = _fit(xs[win], series["pm25"].to_numpy()[win])

    rows = []
    for h in HORIZONS:
        conf = lambda key: None if bt25.get(key) is None else float(np.clip(1.0 - bt25[key], 0.05, 0.95))
        rows.append({"horizon_min": h, "method": PERSISTENCE[0], "model_version": PERSISTENCE[1],
                     "pm25_pred": round(current["pm25"], 1),
                     "pm10_pred": None if current.get("pm10") is None else round(current["pm10"], 1),
                     "confidence": conf(("persistence", h)),
                     "basis": {"rule": "future value = current-window mean", "window_min": cfg.backend.current_window_min,
                               "backtest_mape_pm25": bt25.get(("persistence", h)),
                               "data": "local zone sensors only"}})
        t25 = _trend_predict(xs[win], series["pm25"].to_numpy()[win], now_min + h)
        t10 = _trend_predict(xs[win], series["pm10"].to_numpy()[win], now_min + h)
        basis = {"rule": f"OLS line over last {cfg.trend_window_min} min of {cfg.bucket_min}-min zone means",
                 "n_points": int(win.sum()), "backtest_mape_pm25": bt25.get(("linear_trend", h)),
                 "data": "local zone sensors only"}
        if fit25:
            basis.update(slope_pm25_per_min=round(fit25[0], 3), r2=round(fit25[2], 3))
        if t25 is None:
            basis["reason"] = f"fewer than {MIN_TREND_POINTS} buckets in the trend window"
        rows.append({"horizon_min": h, "method": TREND[0], "model_version": TREND[1],
                     "pm25_pred": None if t25 is None else round(t25, 1),
                     "pm10_pred": None if t10 is None else round(t10, 1),
                     "confidence": None if t25 is None else conf(("linear_trend", h)), "basis": basis})
    return rows
