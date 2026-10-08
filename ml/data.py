"""Training / evaluation data for the Aeronex ML features.

The only data we have is simulated, so the ML datasets are generated directly from the simulator fleet
(no HTTP, no database) at a coarser cadence (5 min per node) over a full year, so seasons, weekdays and episodes
are all represented. Output is one parquet table of per-sample rows; the hidden ground truth is kept in separate
`truth_*` columns and is ONLY ever read by training/evaluation code under ml/, never by the worker.

    python -m ml.data generate --days 365 --start 2025-10-01 --out data/ml/year.parquet

Zone-level 5-minute series (what the worker also sees) come from `zone_series()`.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "data" / "ml" / "year.parquet"
BUCKET = "5min"


def generate(days: int, start: datetime, out: Path, seed: int = 42, interval_s: int = 300) -> Path:
    from simulator.faults import FaultConfig
    from simulator.fleet import Fleet

    fleet = Fleet(seed, start, FaultConfig.none(), interval_s, interval_s, allow_route_download=False)
    rows = []
    t0 = time.time()
    for em in fleet.run_until(start + timedelta(days=days)):
        w, tr = em.wire, em.truth or {}
        if w["quality"]["warmup"]:
            continue
        rows.append((
            w["timestamp_utc"], w["device_id"], w["gps"]["lat"], w["gps"]["lon"], w["quality"]["gps_valid"],
            w["pm"]["pm25_ugm3"], w["pm"]["pm10_ugm3"],
            w["gas_proxy"]["mq2_adc"], w["gas_proxy"]["mq9_adc"], w["gas_proxy"]["mq135_adc"],
            w["env"]["temperature_c"], w["env"]["humidity_pct"], w["env"]["pressure_hpa"],
            tr.get("zone_id"), tr.get("dominant_source"), tr.get("dominant_strength"),
        ))
    df = pd.DataFrame(rows, columns=[
        "ts", "device_id", "lat", "lon", "gps_valid", "pm25", "pm10", "mq2", "mq9", "mq135", "temp", "hum", "pres",
        "zone", "truth_source", "truth_strength"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"wrote {len(df):,} samples ({days} days from {start:%Y-%m-%d}) -> {out}  [{time.time() - t0:.0f}s]")
    return out


def load(path: Path = DEFAULT) -> pd.DataFrame:
    return pd.read_parquet(path)


def zone_series(df: pd.DataFrame, rule: str = BUCKET) -> pd.DataFrame:
    """One row per (zone, 5-min bucket): mean of the samples in that bucket (all devices of the zone)."""
    d = df.dropna(subset=["zone", "pm25", "pm10"]).copy()
    d["bucket"] = d["ts"].dt.floor(rule)
    g = d.groupby(["zone", "bucket"])
    out = g[["pm25", "pm10", "mq2", "mq9", "mq135", "temp", "hum", "pres"]].mean()
    out["n"] = g.size()
    # the dominant ground-truth source of the bucket (evaluation only)
    out["truth_source"] = g["truth_source"].agg(lambda s: s.mode().iat[0] if len(s.mode()) else None)
    out["truth_strength"] = g["truth_strength"].mean()
    return out.reset_index().sort_values(["zone", "bucket"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--days", type=int, default=365)
    g.add_argument("--start", default="2025-10-01")
    g.add_argument("--out", type=Path, default=DEFAULT)
    g.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    start = datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc)
    generate(a.days, start, a.out, a.seed)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
