"""Feature 2: multi-horizon AQI forecasting with gradient-boosted trees (6 horizons, with prediction intervals).

Horizons: 15 / 30 / 60 min and 6 / 24 / 72 h. One direct model per horizon (mean + 10th/90th percentile quantile models).
Evaluated on a held-out final stretch of the year against three baselines: persistence, linear trend (OLS over the last
45 min) and climatology (mean of the same clock time over the previous days).

    python -m ml.forecast train                # trains on data/ml/year.parquet, writes ml/artifacts/forecast.joblib
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from . import data as mldata
from .features import ALL_FEATURES, STEP_MIN, base_features, with_horizon
from .splits import role

ART = Path(__file__).resolve().parent / "artifacts"
HORIZONS = (15, 30, 60, 360, 1440, 4320)               # minutes
TEST_DAYS, VAL_DAYS, GAP_DAYS = 60, 45, 4


def horizon_label(h: int) -> str:
    return f"{h}min" if h < 120 else f"{h // 60}h"


def _targets(f: pd.DataFrame, h: int) -> pd.Series:
    return f.groupby("zone")["aqi_series"].shift(-(h // STEP_MIN))


def _linear_trend_baseline(f: pd.DataFrame, h: int) -> pd.Series:
    """OLS slope of the smoothed AQI over the last 9 buckets (45 min), extrapolated to the target."""
    w = np.arange(9) - 4.0
    out = []
    for _, g in f.groupby("zone", sort=False):
        y = g["aqi_series"].to_numpy()
        slope = np.full(len(y), np.nan)
        ok = len(y) >= 9
        if ok:
            slope[8:] = np.convolve(y, w[::-1], mode="valid") / 60.0
        out.append(pd.Series(np.clip(y + slope * (h // STEP_MIN), 0, 500), index=g.index))
    return pd.concat(out).reindex(f.index)


def metrics(y, p) -> dict:
    m = ~(np.isnan(y) | np.isnan(p))
    e = y[m] - p[m]
    return {"mae": float(np.mean(np.abs(e))), "rmse": float(np.sqrt(np.mean(e ** 2))),
            "r2": float(1 - np.sum(e ** 2) / np.sum((y[m] - y[m].mean()) ** 2)), "n": int(m.sum())}


def _fit(X, y, loss="squared_error", q=None):
    return HistGradientBoostingRegressor(loss=loss, quantile=q, max_iter=250, learning_rate=0.08, max_leaf_nodes=31,
                                         min_samples_leaf=60, l2_regularization=1.0, early_stopping=True,
                                         validation_fraction=0.1, n_iter_no_change=15, random_state=7).fit(X, y)


def train(src: Path = mldata.DEFAULT, out: Path = ART / "forecast.joblib") -> dict:
    """Residual learning: the trees predict (target - anchor) where the anchor is whichever simple baseline (persistence or
    same-clock-time climatology) is better on the validation blocks for that horizon. Intervals are split-conformal: residual
    quantiles measured on the validation blocks, which are neither train nor test."""
    t0 = time.time()
    series = mldata.zone_series(mldata.load(src))
    f = base_features(series)
    f["role"] = role(f["bucket"])
    models, report = {}, {}
    for h in HORIZONS:
        d = with_horizon(f, h)
        d["y"] = _targets(f, h)
        d = d.dropna(subset=["y", "aqi"])
        train, val, test = (d[d["role"] == r] for r in ("train", "val", "test"))
        # anchor = the simple baseline the trees correct; chosen on the VALIDATION blocks (persistence vs same-clock-time climatology)
        mae = lambda x, col: float(np.nanmean(np.abs(x["y"] - x[col].fillna(x["aqi"]))))
        anchor_col = "aqi" if mae(val, "aqi") <= mae(val, "clim") else "clim"
        for x in (train, val, test):
            x["anchor"] = x[anchor_col].fillna(x["aqi"])
        model = _fit(train[ALL_FEATURES], train["y"] - train["anchor"])
        pred = lambda x: np.clip(x["anchor"].to_numpy() + model.predict(x[ALL_FEATURES]), 0, 500)
        res_val = val["y"].to_numpy() - pred(val)
        q10, q90 = (float(np.quantile(res_val, q)) for q in (0.10, 0.90))
        p = pred(test)
        y = test["y"].to_numpy()
        base = {"persistence": test["aqi"].to_numpy(),
                "linear_trend": _linear_trend_baseline(f, h).reindex(test.index).to_numpy(),
                "climatology": test["clim"].to_numpy()}
        res = {"gbt": metrics(y, p), **{k: metrics(y, v) for k, v in base.items()}}
        res["gbt"]["interval_80_coverage"] = float(np.mean((y >= p + q10) & (y <= p + q90)))
        best_base = min(res[k]["mae"] for k in base)
        res["skill_vs_persistence"] = 1 - res["gbt"]["mae"] / res["persistence"]["mae"]
        res["skill_vs_best_baseline"] = 1 - res["gbt"]["mae"] / best_base
        report[horizon_label(h)] = res
        models[h] = {"mean": model, "q10": q10, "q90": q90, "anchor": anchor_col}
        res["anchor"] = anchor_col
        print(f"{horizon_label(h):>6}  MAE gbt {res['gbt']['mae']:6.1f} | persist {res['persistence']['mae']:6.1f} | "
              f"trend {res['linear_trend']['mae']:6.1f} | clim {res['climatology']['mae']:6.1f} | "
              f"skill vs best baseline {res['skill_vs_best_baseline']:+.1%} | 80% interval cover {res['gbt']['interval_80_coverage']:.0%}",
              flush=True)
    meta = {"horizons": list(HORIZONS), "features": ALL_FEATURES, "split": "blocked 40-day cycles (ml/splits.py)", "report": report, "seconds": round(time.time() - t0),
            "method": "gradient-boosted trees on residual vs climatology; split-conformal 80% intervals"}
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"models": models, "meta": meta}, out)
    (out.with_suffix(".metrics.json")).write_text(json.dumps(meta, indent=1, default=float))
    print(f"saved {out}  ({meta['seconds']}s)")
    return meta


# ---------------------------------------------------------------------------- inference
_CACHE: dict = {}


def load(path: Path = ART / "forecast.joblib"):
    if path not in _CACHE:
        _CACHE[path] = joblib.load(path) if path.exists() else None
    return _CACHE[path]


def predict(series: pd.DataFrame, zone: str, bundle=None) -> list[dict]:
    """series: recent 5-min zone series (columns zone, bucket, pm25, pm10, temp, hum, pres) - ideally >= 24 h.
    Returns one dict per horizon for the newest bucket: {horizon_min, aqi, aqi_p10, aqi_p90}. Missing long lags are NaN,
    which the trees handle natively (their predictions are then less certain; see `lags_available`)."""
    bundle = bundle or load()
    if bundle is None or series.empty:
        return []
    f = base_features(series[series["zone"] == zone])
    if f.empty:
        return []
    out = []
    for h in bundle["meta"]["horizons"]:
        row = with_horizon(f, h).iloc[[-1]]
        m = bundle["models"][h]
        anchor = row[m["anchor"]].fillna(row["aqi"]).iloc[0]
        mid = float(np.clip(anchor + m["mean"].predict(row[ALL_FEATURES])[0], 0, 500))
        out.append({"horizon_min": h, "aqi": mid,
                    "aqi_p10": float(np.clip(mid + m["q10"], 0, 500)), "aqi_p90": float(np.clip(mid + m["q90"], 0, 500)),
                    "origin": row["bucket"].iloc[0], "history_hours": round(len(f) * STEP_MIN / 60, 1)})
    return out


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if len(sys.argv) > 1 and sys.argv[1] == "train":
        train()
