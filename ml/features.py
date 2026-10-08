"""Shared feature engineering for the forecasting and early-warning models.

The SAME function builds features for training (a year of zone series) and for inference (the worker's recent
zone series), so there is no train/serve skew. Everything is computed on a regular 5-minute grid per zone, using only
information available at the forecast origin t, plus *calendar* facts about the target time (hour, weekday), which are
legitimately known in advance. Future weather is NOT known, so no weather at the target time is used.

Weather inputs are the environment sensors on the nodes (temperature, humidity, pressure). There is no wind sensor and
no traffic-volume feed in this prototype; traffic is represented by a time-of-day rush-hour profile only.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

STEP_MIN = 5
ZONE_CODES = {"CHNAM": 0, "HITEC": 1, "IDAPASH": 2, "MGBSEC": 3, "PGPT": 4, "SANATH": 5}
IST_OFFSET = pd.Timedelta(hours=5, minutes=30)

# Approximate dates of major Indian festivals that affect air quality (firecrackers, crowds). Placeholder calendar:
# the simulator does not model festival effects, so the model cannot learn from it here; it is wired in for real data.
FESTIVALS = [date(2025, 10, 2), date(2025, 10, 20), date(2026, 1, 14), date(2026, 3, 4),
             date(2026, 10, 20), date(2026, 11, 8), date(2027, 1, 14), date(2027, 3, 22)]

_PM25_X = [0, 30, 31, 60, 61, 90, 91, 120, 121, 250, 251, 500]
_PM25_Y = [0, 50, 51, 100, 101, 200, 201, 300, 301, 400, 401, 500]
_PM10_X = [0, 50, 51, 100, 101, 250, 251, 350, 351, 430, 431, 600]
_PM10_Y = _PM25_Y


def aqi_vec(pm25, pm10) -> np.ndarray:
    """Vectorised CPCB PM-only AQI (same bands as worker/aqi.py): max of the PM2.5 and PM10 sub-indices."""
    a = np.interp(np.asarray(pm25, dtype=float), _PM25_X, _PM25_Y, right=500)
    b = np.interp(np.asarray(pm10, dtype=float), _PM10_X, _PM10_Y, right=500)
    out = np.fmax(a, b)                                  # fmax ignores a NaN on one side
    return np.where(np.isnan(np.asarray(pm25, dtype=float)) & np.isnan(np.asarray(pm10, dtype=float)), np.nan, out)


def category_of(aqi: float) -> str:
    for top, name in ((50, "Good"), (100, "Satisfactory"), (200, "Moderate"), (300, "Poor"), (400, "Very Poor"), (500, "Severe")):
        if aqi <= top:
            return name
    return "Severe"


def _rush(hour_ist: np.ndarray) -> np.ndarray:
    """Generic two-peak traffic-hours profile (morning ~9, evening ~18:30). A proxy; no traffic data exists."""
    g = lambda h, mu, s: np.exp(-0.5 * ((h - mu) / s) ** 2)
    return np.maximum(g(hour_ist, 9.0, 1.4), g(hour_ist, 18.5, 1.8))


def _festival_days(ts: pd.DatetimeIndex) -> np.ndarray:
    days = np.array([(pd.Timestamp(f) - pd.Timestamp("1970-01-01")).days for f in FESTIVALS])
    d = (ts.tz_convert("UTC").tz_localize(None).floor("D") - pd.Timestamp("1970-01-01")).days.to_numpy()
    diff = d[:, None] - days[None, :]
    nearest = diff[np.arange(len(d)), np.abs(diff).argmin(axis=1)]
    return np.clip(nearest, -7, 7)                       # signed days since/until the nearest festival, clipped


def regular_grid(series: pd.DataFrame) -> pd.DataFrame:
    """series columns: zone, bucket, pm25, pm10, temp, hum, pres. -> complete 5-min grid per zone (NaN where missing)."""
    out = []
    for zone, g in series.groupby("zone"):
        g = g.set_index("bucket").sort_index()
        idx = pd.date_range(g.index.min(), g.index.max(), freq=f"{STEP_MIN}min", tz="UTC")
        g = g.reindex(idx)
        g["zone"] = zone
        g.index.name = "bucket"
        out.append(g.reset_index())
    return pd.concat(out, ignore_index=True)


def base_features(series: pd.DataFrame) -> pd.DataFrame:
    """One row per (zone, bucket) with everything known at that origin. Returns a frame with `zone`, `bucket`,
    `aqi` (30-min smoothed AQI = the quantity we forecast) and the feature columns."""
    grid = regular_grid(series)
    parts = []
    for zone, g in grid.groupby("zone", sort=False):
        g = g.sort_values("bucket").reset_index(drop=True)
        pm25s = g["pm25"].rolling(6, min_periods=3).mean()
        pm10s = g["pm10"].rolling(6, min_periods=3).mean()
        aqi = pd.Series(aqi_vec(pm25s, pm10s), index=g.index)
        f = pd.DataFrame({"zone": zone, "bucket": g["bucket"], "aqi": aqi,
                          "pm25": pm25s, "pm10": pm10s,
                          "pm_ratio": pm10s / pm25s.replace(0, np.nan)})
        for lag, name in ((3, "15m"), (6, "30m"), (12, "1h"), (36, "3h")):
            f[f"d_aqi_{name}"] = aqi - aqi.shift(lag)
        f["aqi_mean_3h"] = aqi.rolling(36, min_periods=12).mean()
        f["aqi_std_3h"] = aqi.rolling(36, min_periods=12).std()
        f["aqi_max_6h"] = aqi.rolling(72, min_periods=12).max()
        f["aqi_mean_24h"] = aqi.rolling(288, min_periods=100).mean()
        f["aqi_lag_24h"] = aqi.shift(288)
        f["aqi_lag_7d"] = aqi.shift(2016)
        temp, hum, pres = (g[c].rolling(6, min_periods=2).mean() for c in ("temp", "hum", "pres"))
        f["temp"], f["hum"], f["pres"] = temp, hum, pres
        f["pres_d3h"] = pres - pres.shift(36)
        f["hum_d3h"] = hum - hum.shift(36)
        f["aqi_series"] = aqi                                # kept for building targets / climatology; not a feature
        parts.append(f)
    f = pd.concat(parts, ignore_index=True)
    ist = f["bucket"] + IST_OFFSET
    hour = ist.dt.hour + ist.dt.minute / 60
    f["hour_sin"], f["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    f["dow"] = ist.dt.dayofweek
    f["month_sin"], f["month_cos"] = np.sin(2 * np.pi * (ist.dt.month - 1) / 12), np.cos(2 * np.pi * (ist.dt.month - 1) / 12)
    f["rush"] = _rush(hour.to_numpy())
    f["festival_d"] = _festival_days(pd.DatetimeIndex(f["bucket"]))
    f["zone_code"] = f["zone"].map(ZONE_CODES).astype(float)
    return f


def with_horizon(f: pd.DataFrame, h_min: int) -> pd.DataFrame:
    """Add the columns that depend on the forecast horizon: calendar facts of the TARGET time and the
    climatological value for the target time-of-day (mean of the same clock time on earlier days)."""
    steps = h_min // STEP_MIN
    out = f.copy()
    tgt = out["bucket"] + pd.Timedelta(minutes=h_min)
    ist = tgt + IST_OFFSET
    hour = ist.dt.hour + ist.dt.minute / 60
    out["t_hour_sin"], out["t_hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    out["t_dow"] = ist.dt.dayofweek
    out["t_rush"] = _rush(hour.to_numpy())
    clim = []
    for k in range(1, 8):                                    # same clock time k days before the target
        lag = 288 * k - steps
        if lag >= 0:
            clim.append(out.groupby("zone")["aqi_series"].shift(lag))
    out["clim"] = pd.concat(clim, axis=1).mean(axis=1) if clim else np.nan
    out["anom_vs_clim"] = out["aqi"] - out["clim"]
    return out


BASE_FEATURES = ["aqi", "pm25", "pm10", "pm_ratio", "d_aqi_15m", "d_aqi_30m", "d_aqi_1h", "d_aqi_3h", "aqi_mean_3h",
                 "aqi_std_3h", "aqi_max_6h", "aqi_mean_24h", "aqi_lag_24h", "aqi_lag_7d", "temp", "hum", "pres",
                 "pres_d3h", "hum_d3h", "hour_sin", "hour_cos", "dow", "month_sin", "month_cos", "rush", "festival_d",
                 "zone_code"]
HORIZON_FEATURES = ["t_hour_sin", "t_hour_cos", "t_dow", "t_rush", "clim", "anom_vs_clim"]
ALL_FEATURES = BASE_FEATURES + HORIZON_FEATURES
