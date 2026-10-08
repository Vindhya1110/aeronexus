"""Feature 1: city-wide AQI map by spatial interpolation (Gaussian-process regression = kriging with learned kernel).

Input: the latest point readings from every GPS-located node (fixed nodes + buses along their routes). Output: a regular grid
of estimated AQI with a per-cell uncertainty (1 sigma), so the map says how much to trust each cell - far from any sensor the
uncertainty grows and the estimate reverts to the city mean. The kernel (smoothness length-scale, signal and noise variance) is
fitted to each snapshot by maximum likelihood.

Validation (`python -m ml.interp evaluate`): leave-one-device-out on hundreds of snapshots from the held-out test blocks,
against inverse-distance weighting, nearest neighbour and the city mean.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel

from . import data as mldata
from .features import STEP_MIN, aqi_vec
from .splits import role

ART = Path(__file__).resolve().parent / "artifacts"
KM_PER_DEG_LAT = 111.0


def _xy(lat, lon, lat0):
    return np.column_stack([(np.asarray(lon) - 78.4) * KM_PER_DEG_LAT * np.cos(np.radians(lat0)), (np.asarray(lat) - lat0) * KM_PER_DEG_LAT])


def _kernel():
    return ConstantKernel(400.0, (1.0, 1e5)) * RBF(length_scale=3.0, length_scale_bounds=(0.3, 30.0)) + WhiteKernel(25.0, (0.5, 5e3))


def fit_gp(lat, lon, aqi) -> tuple[GaussianProcessRegressor, float, float]:
    lat0 = float(np.mean(lat))
    X, y = _xy(lat, lon, lat0), np.asarray(aqi, dtype=float)
    mu = float(y.mean())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gp = GaussianProcessRegressor(_kernel(), normalize_y=False, n_restarts_optimizer=1, random_state=7).fit(X, y - mu)
    return gp, mu, lat0


def _idw(xy_tr, y_tr, xy_te, power=2.0):
    d = np.linalg.norm(xy_tr[None, :, :] - xy_te[:, None, :], axis=2)
    w = 1.0 / np.maximum(d, 1e-3) ** power
    return (w * y_tr[None, :]).sum(1) / w.sum(1)


def _loo(p: pd.DataFrame) -> dict:
    """Leave-one-node-out error of each method on THIS snapshot: lets the service pick whichever interpolates better right now."""
    lat0 = float(p["lat"].mean())
    XY, y = _xy(p["lat"], p["lon"], lat0), p["aqi"].to_numpy(dtype=float)
    e_gp, e_idw = [], []
    for i in range(len(p)):
        tr = np.arange(len(p)) != i
        gp, mu, _ = fit_gp(p["lat"][tr], p["lon"][tr], y[tr])
        e_gp.append(gp.predict(XY[[i]])[0] + mu - y[i])
        e_idw.append(_idw(XY[tr], y[tr], XY[[i]])[0] - y[i])
    return {"gp": float(np.mean(np.abs(e_gp))), "idw": float(np.mean(np.abs(e_idw))),
            "gp_rmse": float(np.sqrt(np.mean(np.square(e_gp)))), "idw_rmse": float(np.sqrt(np.mean(np.square(e_idw))))}


def predict_grid(points: pd.DataFrame, n: int = 40, pad_km: float = 2.0, method: str = "auto") -> dict | None:
    """points: columns lat, lon, aqi (one row per device = its latest mean). method: 'gp' | 'idw' | 'auto' (default: whichever has the
    lower leave-one-node-out error on the current snapshot - on our simulated field IDW usually wins, see ml/artifacts/interp.metrics.json).
    Returns grid + per-cell 1-sigma uncertainty, or None if fewer than 3 nodes."""
    p = points.dropna(subset=["lat", "lon", "aqi"]).reset_index(drop=True)
    if len(p) < 3:
        return None
    lat0 = float(p["lat"].mean())
    pad_lat, pad_lon = pad_km / KM_PER_DEG_LAT, pad_km / (KM_PER_DEG_LAT * np.cos(np.radians(lat0)))
    lats = np.linspace(p["lat"].min() - pad_lat, p["lat"].max() + pad_lat, n)
    lons = np.linspace(p["lon"].min() - pad_lon, p["lon"].max() + pad_lon, n)
    LON, LAT = np.meshgrid(lons, lats)
    G = _xy(LAT.ravel(), LON.ravel(), lat0)
    loo = _loo(p) if len(p) >= 4 else {"gp": 0.0, "idw": 1.0, "gp_rmse": 1.0, "idw_rmse": 1.0}
    use = method if method in ("gp", "idw") else ("gp" if loo["gp"] < loo["idw"] else "idw")
    XY, y = _xy(p["lat"], p["lon"], lat0), p["aqi"].to_numpy(dtype=float)
    ls = None
    if use == "gp":
        gp, mu, _ = fit_gp(p["lat"], p["lon"], y)
        mean, std = gp.predict(G, return_std=True)
        mean, ls = mean + mu, float(gp.kernel_.k1.k2.length_scale)
    else:
        mean = _idw(XY, y, G)
        dmin = np.min(np.linalg.norm(G[:, None, :] - XY[None, :, :], axis=2), axis=1)       # km to the nearest node
        std = loo["idw_rmse"] * np.sqrt(1.0 + (dmin / 2.0) ** 2)                             # grows away from every sensor
    return {"lats": lats.round(5).tolist(), "lons": lons.round(5).tolist(),
            "aqi": np.clip(mean, 0, 500).reshape(n, n).round(1).tolist(), "std": np.asarray(std).reshape(n, n).round(1).tolist(),
            "points": int(len(p)), "length_scale_km": ls if ls is not None else 0.0, "city_mean": float(y.mean()),
            "method_used": use, "leave_one_out_mae": loo,
            "method": ("Gaussian-process regression (kriging), RBF + noise kernel fitted per snapshot" if use == "gp"
                       else "inverse-distance weighting (power 2); uncertainty from leave-one-out error, widening with distance to the nearest node")}


# ------------------------------------------------------------------------------------------------ evaluation
def evaluate(df: pd.DataFrame | None = None, snapshots: int = 300, auto_snapshots: int = 80, seed: int = 7) -> dict:
    df = mldata.load() if df is None else df
    d = df[df["gps_valid"] & df["lat"].notna() & df["pm25"].notna()].copy()
    d["aqi"] = aqi_vec(d["pm25"], d["pm10"])
    d["slot"] = d["ts"].dt.floor(f"{STEP_MIN}min")
    test_slots = d.loc[role(d["slot"]) .values == "test", "slot"].drop_duplicates()
    rng = np.random.default_rng(seed)
    slots = rng.choice(test_slots.to_numpy(), size=min(snapshots, len(test_slots)), replace=False)
    err = {"gp": [], "idw": [], "nearest": [], "city_mean": []}
    cover, n_cov = 0, 0
    # also leave one whole ZONE out: how well does it extrapolate to an area with no sensor at all?
    zerr = {"gp": [], "idw": [], "city_mean": []}
    for s in slots:
        g = d[d["slot"] == s].groupby("device_id").agg(lat=("lat", "mean"), lon=("lon", "mean"), aqi=("aqi", "mean"), zone=("zone", "first")).reset_index()
        if len(g) < 6:
            continue
        lat0 = g["lat"].mean()
        XY = _xy(g["lat"], g["lon"], lat0)
        for i in range(len(g)):
            tr = np.arange(len(g)) != i
            gp, mu, _ = fit_gp(g["lat"][tr], g["lon"][tr], g["aqi"][tr])
            m, sd = gp.predict(XY[[i]], return_std=True)
            truth = g["aqi"].iloc[i]
            err["gp"].append(abs(m[0] + mu - truth))
            err["idw"].append(abs(_idw(XY[tr], g["aqi"].to_numpy()[tr], XY[[i]])[0] - truth))
            err["nearest"].append(abs(g["aqi"].to_numpy()[tr][np.argmin(np.linalg.norm(XY[tr] - XY[i], axis=1))] - truth))
            err["city_mean"].append(abs(g["aqi"].to_numpy()[tr].mean() - truth))
            cover += abs(m[0] + mu - truth) <= 1.96 * sd[0]
            n_cov += 1
        for z, gz in g.groupby("zone"):
            if z is None or len(gz) == len(g):
                continue
            tr = (g["zone"] != z).to_numpy()
            if tr.sum() < 3:
                continue
            gp, mu, _ = fit_gp(g["lat"][tr], g["lon"][tr], g["aqi"][tr])
            for j in np.where(~tr)[0]:
                truth = g["aqi"].iloc[j]
                zerr["gp"].append(abs(gp.predict(XY[[j]])[0] + mu - truth))
                zerr["idw"].append(abs(_idw(XY[tr], g["aqi"].to_numpy()[tr], XY[[j]])[0] - truth))
                zerr["city_mean"].append(abs(g["aqi"].to_numpy()[tr].mean() - truth))
    # the deployed 'auto' selector (nested leave-one-out): held-out node never seen by the selection either
    aerr = []
    for s in slots[:auto_snapshots]:
        g = d[d["slot"] == s].groupby("device_id").agg(lat=("lat", "mean"), lon=("lon", "mean"), aqi=("aqi", "mean")).reset_index()
        if len(g) < 6:
            continue
        lat0 = g["lat"].mean()
        XY = _xy(g["lat"], g["lon"], lat0)
        for i in range(len(g)):
            tr = np.arange(len(g)) != i
            sub = g[tr].reset_index(drop=True)
            loo = _loo(sub)
            if loo["gp"] < loo["idw"]:
                gp, mu, _ = fit_gp(sub["lat"], sub["lon"], sub["aqi"])
                pred = gp.predict(XY[[i]])[0] + mu
            else:
                pred = _idw(XY[tr], sub["aqi"].to_numpy(), XY[[i]])[0]
            aerr.append(abs(pred - g["aqi"].iloc[i]))
    res = {"snapshots": int(len(slots)), "auto_selector_mae": float(np.mean(aerr)) if aerr else None, "auto_predictions": len(aerr), "leave_one_device_out_mae": {k: float(np.mean(v)) for k, v in err.items()},
           "leave_one_zone_out_mae": {k: float(np.mean(v)) for k, v in zerr.items()},
           "gp_95pct_interval_coverage": float(cover / max(1, n_cov)), "predictions": int(n_cov)}
    return res


def main() -> None:
    res = evaluate()
    print(json.dumps(res, indent=1))
    ART.mkdir(exist_ok=True)
    (ART / "interp.metrics.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if len(sys.argv) > 1 and sys.argv[1] == "evaluate":
        main()
