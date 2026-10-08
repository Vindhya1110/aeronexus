"""Feature 7: sensor-drift correction by co-location ("rendezvous") calibration.

Whenever a bus passes within 400 m of a fixed node (same 5-minute slot) the two should read the same air. For each mobile
sensor we keep a rolling window of such pairs and fit a robust linear map  reference ~ a * reading + b  (Huber regression), refit
daily from the PREVIOUS 30 days only. The map is the learned drift-correction function; applying it keeps the bus aligned with
the fixed network as its sensor ages.

Honest set-up: the simulated sensors have a fixed unit-to-unit gain and no drift, so the experiment INJECTS a known slow drift
into two buses of an evaluation copy of the data (and none into the others, as a control). That lets us check both that the
method removes the disagreement and that it does not "correct" healthy sensors. The fixed nodes are low-cost units too, not
reference-grade; with a real reference station the same code gives absolute accuracy, here it gives network consistency.

    python -m ml.calibration evaluate
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor

from . import data as mldata
from .features import STEP_MIN

ART = Path(__file__).resolve().parent / "artifacts"
PAIR_KM, WINDOW_DAYS, MIN_PAIRS = 0.4, 30, 40
# evaluation-only drift injected into the data copy (fraction per day, relative to the sensor's own reading)
INJECTED_DRIFT = {"BUS_CHNAM_01": +0.0015, "BUS_MGBSEC_01": -0.0010}      # +0.15 % / day  and  -0.10 % / day


def inject_drift(df: pd.DataFrame, drift: dict[str, float] = INJECTED_DRIFT) -> pd.DataFrame:
    d = df.copy()
    day = (d["ts"] - d["ts"].min()).dt.total_seconds() / 86400
    for dev, rate in drift.items():
        m = d["device_id"] == dev
        f = 1.0 + rate * day[m]
        d.loc[m, ["pm25", "pm10"]] = d.loc[m, ["pm25", "pm10"]].mul(f, axis=0)
    return d


def colocation_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """Rows: slot, bus, fixed, bus_pm25, ref_pm25 (+pm10) for every bus sample within PAIR_KM of a fixed node in the same slot."""
    d = df[df["gps_valid"] & df["pm25"].notna() & df["lat"].notna()].copy()
    d["slot"] = d["ts"].dt.floor(f"{STEP_MIN}min")
    fixed = d[d["device_id"].str.startswith("ZONE_")]
    bus = d[d["device_id"].str.startswith("BUS_")]
    m = bus.merge(fixed[["slot", "device_id", "lat", "lon", "pm25", "pm10"]], on="slot", suffixes=("", "_ref"))
    km = np.hypot((m["lat"] - m["lat_ref"]) * 111.0, (m["lon"] - m["lon_ref"]) * 111.0 * 0.96)
    m = m[km <= PAIR_KM]
    return m.rename(columns={"device_id": "bus", "device_id_ref": "fixed", "pm25": "bus_pm25", "pm25_ref": "ref_pm25",
                             "pm10": "bus_pm10", "pm10_ref": "ref_pm10"})[["slot", "bus", "fixed", "bus_pm25", "ref_pm25", "bus_pm10", "ref_pm10"]]


def fit_correction(pairs: pd.DataFrame, col: str = "pm25") -> dict | None:
    if len(pairs) < MIN_PAIRS:
        return None
    xs, ys = pairs[f"bus_{col}"].to_numpy(), pairs[f"ref_{col}"].to_numpy()
    # Huber is robust to outliers in the reference but NOT to high-leverage ones in the bus reading (a passing diesel truck),
    # so first trim pairs whose ratio ref/bus is far from the median ratio, then fit.
    ok = xs > 1.0
    ratio = np.where(ok, ys / np.where(ok, xs, 1.0), np.nan)
    med = np.nanmedian(ratio)
    mad = np.nanmedian(np.abs(ratio - med)) * 1.4826
    keep = ok & (np.abs(ratio - med) <= 4.0 * max(mad, 0.02 * med))
    if keep.sum() < MIN_PAIRS:
        return None
    h = HuberRegressor(epsilon=1.35, alpha=1e-4, max_iter=200).fit(xs[keep].reshape(-1, 1), ys[keep])
    return {"gain": float(h.coef_[0]), "offset": float(h.intercept_), "pairs": int(keep.sum())}


def rolling_corrections(pairs: pd.DataFrame, col: str = "pm25", every_days: int = 1) -> pd.DataFrame:
    """For every day D and bus: the correction fitted on pairs from [D-30, D) only (no peeking)."""
    rows = []
    pairs = pairs.copy()
    pairs["day"] = pairs["slot"].dt.floor("D")
    for bus, g in pairs.groupby("bus"):
        days = sorted(g["day"].unique())
        for D in days[::every_days]:
            w = g[(g["day"] < D) & (g["day"] >= D - pd.Timedelta(days=WINDOW_DAYS))]
            c = fit_correction(w, col)
            if c:
                rows.append({"bus": bus, "day": D, **c})
    return pd.DataFrame(rows)


def evaluate(df: pd.DataFrame | None = None) -> dict:
    df = mldata.load() if df is None else df
    pairs = colocation_pairs(inject_drift(df))
    corr = rolling_corrections(pairs)
    p = pairs.copy()
    p["day"] = p["slot"].dt.floor("D")
    p = p.merge(corr, on=["bus", "day"], how="inner")
    p["corrected"] = p["gain"] * p["bus_pm25"] + p["offset"]
    p["raw_err"], p["cor_err"] = (p["bus_pm25"] - p["ref_pm25"]).abs() / p["ref_pm25"], (p["corrected"] - p["ref_pm25"]).abs() / p["ref_pm25"]
    last = p["slot"] >= p["slot"].max() - pd.Timedelta(days=90)           # the last 3 months: where drift has accumulated
    per = {}
    for bus, g in p[last].groupby("bus"):
        per[bus] = {"raw_mean_rel_error": float(g["raw_err"].mean()), "corrected_mean_rel_error": float(g["cor_err"].mean()),
                    "improvement": float(1 - g["cor_err"].mean() / g["raw_err"].mean()), "pairs": int(len(g)),
                    "injected_drift_per_day": INJECTED_DRIFT.get(bus, 0.0)}
    # does the learned gain track the injected drift? compare with the gain a drifting sensor "should" have: 1/(1+rate*day) x base
    track = {}
    for bus, rate in INJECTED_DRIFT.items():
        g = corr[corr["bus"] == bus].copy()
        base = g["gain"].iloc[: max(5, len(g) // 10)].mean()
        day = (g["day"] - df["ts"].min().floor("D")).dt.total_seconds() / 86400
        ideal = base * (1 + rate * day.iloc[: max(5, len(g) // 10)].mean()) / (1 + rate * day)
        track[bus] = {"rmse_gain_vs_ideal": float(np.sqrt(np.mean((g["gain"] - ideal) ** 2))), "final_gain": float(g["gain"].iloc[-1]),
                      "initial_gain": float(base), "ideal_final_gain": float(ideal.iloc[-1])}
    return {"pairs_total": int(len(pairs)), "pairs_per_bus": pairs.groupby("bus").size().to_dict(), "per_bus_last_90_days": per,
            "gain_tracking": track, "settings": {"pair_km": PAIR_KM, "window_days": WINDOW_DAYS, "min_pairs": MIN_PAIRS}}


def main() -> None:
    res = evaluate()
    print(json.dumps(res, indent=1))
    ART.mkdir(exist_ok=True)
    (ART / "calibration.metrics.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if len(sys.argv) > 1 and sys.argv[1] == "evaluate":
        main()
