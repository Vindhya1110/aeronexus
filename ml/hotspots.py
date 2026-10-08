"""Feature 3: persistent hotspots (DBSCAN) and learned anomaly detection (Isolation Forest).

Hotspots: GPS-valid samples (fixed nodes + buses) are binned to ~220 m cells; a cell is a *persistent hotspot candidate* if
its AQI is high relative to the city at the same hour on most of the days it was observed. DBSCAN (haversine, 450 m) joins
neighbouring candidate cells into hotspot clusters, ranked by severity x persistence x evidence.

Anomalies: an Isolation Forest over each zone's deviation features (PM relative to its 24 h baseline, MQ proxy rises, PM
ratio, short-term AQI change) flags unusual combinations, compared on the held-out final 60 days with the statistical
detector the worker already runs (3 h rolling median + MAD, ratio/rise/z gates).

    python -m ml.hotspots evaluate
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.cluster import DBSCAN
from sklearn.ensemble import IsolationForest

from . import data as mldata
from .features import STEP_MIN, aqi_vec, regular_grid
from .source import source_features
from .splits import role

ART = Path(__file__).resolve().parent / "artifacts"
CELL_DEG = 0.002                                         # ~220 m
EARTH_KM = 6371.0088


# ------------------------------------------------------------------------------------------------ hotspots
def find_hotspots(df: pd.DataFrame, min_days: int = 5, eps_m: float = 450.0) -> pd.DataFrame:
    """df: sample rows with ts, lat, lon, pm25, pm10, gps_valid. Returns ranked hotspot clusters."""
    d = df[df["gps_valid"] & df["lat"].notna() & df["pm25"].notna()].copy()
    if d.empty:
        return pd.DataFrame()
    d["aqi"] = aqi_vec(d["pm25"], d["pm10"])
    d["hour"] = d["ts"].dt.floor("h")
    city = d.groupby("hour")["aqi"].median().rename("city")      # what "normal" is at that hour, city-wide
    d = d.join(city, on="hour")
    d["rel"] = d["aqi"] / d["city"].clip(lower=1)
    d["day"] = d["ts"].dt.floor("D")
    d["clat"], d["clon"] = (d["lat"] / CELL_DEG).round(), (d["lon"] / CELL_DEG).round()
    daily = d.groupby(["clat", "clon", "day"]).agg(rel=("rel", "mean"), aqi=("aqi", "mean"), n=("aqi", "size")).reset_index()
    day_cut = daily.groupby("day")["rel"].transform(lambda s: s.quantile(0.75))
    daily["high"] = daily["rel"] >= np.maximum(day_cut, 1.05)
    cells = daily.groupby(["clat", "clon"]).agg(days=("day", "nunique"), persistence=("high", "mean"), aqi=("aqi", "mean"),
                                                rel=("rel", "mean"), n=("n", "sum")).reset_index()
    cand = cells[(cells["days"] >= min_days) & (cells["persistence"] >= 0.5) & (cells["rel"] > 1.0)].copy()
    if cand.empty:
        return pd.DataFrame()
    cand["lat"], cand["lon"] = cand["clat"] * CELL_DEG, cand["clon"] * CELL_DEG
    coords = np.radians(cand[["lat", "lon"]].to_numpy())
    cand["cluster"] = DBSCAN(eps=eps_m / 1000 / EARTH_KM, min_samples=1, metric="haversine").fit_predict(coords)
    out = []
    for cid, g in cand.groupby("cluster"):
        w = g["n"].to_numpy()
        lat, lon = np.average(g["lat"], weights=w), np.average(g["lon"], weights=w)
        radius_m = float(np.max(np.hypot((g["lat"] - lat) * 111_000, (g["lon"] - lon) * 111_000 * np.cos(np.radians(lat))))) + CELL_DEG * 55_000
        mean_aqi, pers = float(np.average(g["aqi"], weights=w)), float(np.average(g["persistence"], weights=w))
        out.append({"lat": lat, "lon": lon, "radius_m": radius_m, "mean_aqi": mean_aqi, "vs_city": float(np.average(g["rel"], weights=w)),
                    "persistence": pers, "cells": int(len(g)), "samples": int(w.sum()), "days_observed": int(g["days"].max()),
                    "score": mean_aqi * pers * np.log1p(len(g))})
    res = pd.DataFrame(out).sort_values("score", ascending=False).reset_index(drop=True)
    res.insert(0, "rank", np.arange(1, len(res) + 1))
    return res


def evaluate_hotspots(df: pd.DataFrame) -> dict:
    hs = find_hotspots(df)
    if hs.empty:
        return {"clusters": 0}
    from shared.zones import ZONES
    # truth proxy: each zone's mean PM2.5 over the year (the simulator's real emission level, which hotspots should rank)
    zmean = df.dropna(subset=["zone", "pm25"]).groupby("zone")["pm25"].mean()
    def nearest_zone(r):
        best = min(ZONES, key=lambda z: min(np.hypot((p[0] - r["lat"]) * 111, (p[1] - r["lon"]) * 111 * 0.96) for p in z.path))
        return best.zone_id
    hs["zone"] = hs.apply(nearest_zone, axis=1)
    per_zone = hs.groupby("zone")["score"].max()
    common = sorted(set(per_zone.index) & set(zmean.index))
    rho = float(spearmanr(per_zone[common], zmean[common])[0]) if len(common) >= 3 else None
    top2_truth = set(zmean.sort_values(ascending=False).index[:2])
    top2_found = list(hs.drop_duplicates("zone").head(2)["zone"])
    return {"clusters": int(len(hs)), "zones_with_hotspots": sorted(set(hs["zone"])), "spearman_vs_zone_mean_pm25": rho,
            "top2_found": top2_found, "top2_truth": sorted(top2_truth), "top2_overlap": len(top2_truth & set(top2_found)),
            "table": hs.head(10).round(3).to_dict("records")}


# ------------------------------------------------------------------------------------------------ anomalies
IF_FEATURES = ["pm25_rel", "pm10_rel", "ratio", "d_mq2", "d_mq9", "d_mq135", "d_aqi_30m"]


def anomaly_frame(series: pd.DataFrame) -> pd.DataFrame:
    f = source_features(series.drop(columns=[c for c in ("truth_source", "truth_strength", "n") if c in series.columns]))
    aq = aqi_vec(f["pm25"], f["pm10"])
    f["aqi"] = aq
    f["d_aqi_30m"] = f["aqi"] - f.groupby("zone")["aqi"].shift(6)
    return f


def statistical_detector(series: pd.DataFrame) -> pd.Series:
    """Vectorised version of the worker's gate: PM2.5 vs 3 h rolling median, ratio >= 1.5 AND rise >= 15 AND robust z >= 3."""
    out = []
    for zone, g in regular_grid(series.drop(columns=[c for c in ("truth_source", "truth_strength", "n") if c in series.columns])).groupby("zone", sort=False):
        g = g.sort_values("bucket").reset_index(drop=True)
        cur = g["pm25"].rolling(3, min_periods=2).mean()
        med = g["pm25"].rolling(36, min_periods=12).median().shift(3)
        mad = (g["pm25"] - g["pm25"].rolling(36, min_periods=12).median()).abs().rolling(36, min_periods=12).median().shift(3)
        z = (cur - med) / (1.4826 * mad.clip(lower=1.0))
        flag = ((cur / med) >= 1.5) & ((cur - med) >= 15) & (z >= 3)
        out.append(pd.Series(flag.fillna(False).to_numpy(), index=pd.MultiIndex.from_arrays([g["zone"], g["bucket"]])))
    return pd.concat(out)


def _episodes(flag: pd.Series) -> list[tuple[int, int]]:
    a = flag.to_numpy().astype(int)
    d = np.diff(np.r_[0, a, 0])
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def evaluate_anomalies(df: pd.DataFrame, save: Path | None = ART / "anomaly_if.joblib") -> dict:
    series = mldata.zone_series(df)
    f = anomaly_frame(series)
    truth = series.set_index(["zone", "bucket"])["truth_source"]
    stat = statistical_detector(series)
    f = f.set_index(["zone", "bucket"]).sort_index()
    f["truth_smoke"] = (truth.reindex(f.index) == "smoke")
    f["stat"] = stat.reindex(f.index).fillna(False)
    f = f.dropna(subset=IF_FEATURES)
    r = role(pd.Series(f.index.get_level_values("bucket")))
    r.index = f.index
    train, test = f[r == "train"], f[r == "test"].copy()
    iso = IsolationForest(n_estimators=200, contamination=0.01, random_state=7, n_jobs=-1).fit(train[IF_FEATURES])
    test["if_score"] = -iso.score_samples(test[IF_FEATURES])            # higher = more anomalous
    thr = float(np.quantile(-iso.score_samples(train[IF_FEATURES]), 0.99))
    test["if"] = test["if_score"] > thr
    res = {"threshold": thr}
    weeks = (test.index.get_level_values('bucket').nunique() * STEP_MIN / 60 / 24) / 7
    for name in ("stat", "if"):
        hit_eps = total_eps = 0
        for z, g in test.groupby(level="zone"):
            for s, e in _episodes(g["truth_smoke"]):
                total_eps += 1
                hit_eps += bool(g[name].iloc[s:e].any())
        alert_eps = sum(len(_episodes(g[name])) for _, g in test.groupby(level="zone"))
        tp_alert = sum(1 for z, g in test.groupby(level="zone") for s, e in _episodes(g[name]) if g["truth_smoke"].iloc[max(0, s - 6):e + 6].any())
        res[name] = {"episodes_total": total_eps, "episodes_detected": hit_eps, "episode_recall": hit_eps / max(1, total_eps),
                     "alerts": alert_eps, "alert_precision": tp_alert / max(1, alert_eps), "false_alerts_per_week": (alert_eps - tp_alert) / weeks}
    both = test["stat"] | test["if"]
    hit = tot = 0
    for z, g in test.groupby(level="zone"):
        gb = both.loc[g.index]
        for s, e in _episodes(g["truth_smoke"]):
            tot += 1
            hit += bool(gb.iloc[s:e].any())
    res["either"] = {"episode_recall": hit / max(1, tot)}
    if save is not None:
        save.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"iso": iso, "threshold": thr, "features": IF_FEATURES}, save)
    return res


_CACHE: dict = {}


def load_if(path: Path = ART / "anomaly_if.joblib"):
    if path not in _CACHE:
        _CACHE[path] = joblib.load(path) if path.exists() else None
    return _CACHE[path]


def anomaly_score(series: pd.DataFrame, zone: str, bundle=None) -> dict | None:
    bundle = bundle or load_if()
    if bundle is None or series.empty:
        return None
    f = anomaly_frame(series[series["zone"] == zone]).dropna(subset=IF_FEATURES)
    if f.empty:
        return None
    s = float(-bundle["iso"].score_samples(f[IF_FEATURES].iloc[[-1]])[0])
    return {"score": s, "threshold": bundle["threshold"], "anomalous": bool(s > bundle["threshold"]), "origin": f["bucket"].iloc[-1]}


def main() -> None:
    t0 = time.time()
    df = mldata.load()
    hs = evaluate_hotspots(df)
    print("HOTSPOTS:", {k: v for k, v in hs.items() if k != "table"})
    for r in hs.get("table", [])[:6]:
        print("  ", r)
    an = evaluate_anomalies(df)
    print("ANOMALY (smoke episodes, held-out test blocks):")
    for k in ("stat", "if", "either"):
        print("  ", k, {a: (round(b, 3) if isinstance(b, float) else b) for a, b in an[k].items()})
    ART.mkdir(exist_ok=True)
    (ART / "hotspots.metrics.json").write_text(json.dumps({"hotspots": hs, "anomaly": an}, indent=1, default=float))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if len(sys.argv) > 1 and sys.argv[1] == "evaluate":
        main()
