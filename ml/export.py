"""Long-horizon analyses stored as `insights` rows so the dashboard can show them.

Hotspots, trends and drift correction need weeks-to-months of history. The demo database only holds hours, so these are computed
on the 12-month SIMULATED dataset (data/ml/year.parquet) and stored with source='simulated-year-dataset'. A deployment with
>= 30 days of real history would call the same functions on the database instead (source='database').

    python -m ml.export                  # computes and writes ml/artifacts/insights_seed.json (used by tools/demo_data.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import calibration, data as mldata, hotspots, trends

ART = Path(__file__).resolve().parent / "artifacts"
SEED = ART / "insights_seed.json"


def build() -> dict:
    df = mldata.load()
    series = mldata.zone_series(df)
    hs = hotspots.find_hotspots(df)
    zone_of = lambda r: min(__import__("shared.zones", fromlist=["ZONES"]).ZONES,
                            key=lambda z: min(np.hypot((p[0] - r["lat"]) * 111, (p[1] - r["lon"]) * 111 * 0.96) for p in z.path)).zone_id
    if not hs.empty:
        hs["zone"] = hs.apply(zone_of, axis=1)
    daily = trends.daily_aqi(series)
    tr = trends.report(daily)
    pairs = calibration.colocation_pairs(calibration.inject_drift(df))
    corr = calibration.rolling_corrections(pairs, every_days=1)
    last = corr.sort_values("day").groupby("bus").tail(1)
    first = corr.sort_values("day").groupby("bus").head(1).set_index("bus")
    buses = []
    for _, r in last.iterrows():
        drift = calibration.INJECTED_DRIFT.get(r["bus"], 0.0)
        buses.append({"device": r["bus"], "gain": round(float(r["gain"]), 3), "offset": round(float(r["offset"]), 2),
                      "gain_at_start": round(float(first.loc[r["bus"], "gain"]), 3), "pairs_in_window": int(r["pairs"]),
                      "status": "drift corrected" if abs(r["gain"] / first.loc[r["bus"], "gain"] - 1) > 0.15 else "stable",
                      "injected_drift_per_day": drift})
    return {
        "hotspots": {"clusters": hs.round(4).to_dict("records"), "days": 365,
                     "note": "Persistent hotspots found by DBSCAN over 12 months of simulated GPS-located readings (the demo database only holds hours)."},
        "trends": {"zones": {z: v for z, v in tr.items()},
                   "note": "Computed on the 12-month simulated history. With under two years the long-term trend cannot be separated from the annual cycle."},
        "calibration": {"buses": buses,
                        "note": "Rolling 30-day co-location calibration of each bus against nearby fixed nodes. Drift was INJECTED into two buses of a copy of the simulated data to validate the method."},
    }


def main() -> None:
    out = build()
    ART.mkdir(exist_ok=True)
    SEED.write_text(json.dumps(out, default=float))
    print(f"wrote {SEED} ({SEED.stat().st_size // 1024} KB): {len(out['hotspots']['clusters'])} hotspots, {len(out['trends']['zones'])} zones, {len(out['calibration']['buses'])} buses")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    main()
