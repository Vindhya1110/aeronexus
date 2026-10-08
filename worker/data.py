"""Load a zone's recent samples into a validated pandas frame and derive bucketed series."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.constants import FLAG_MQ2, FLAG_MQ9, FLAG_MQ135, PLAUSIBLE_RANGES
from backend.models import Measurement

MQ_COLS = {"mq2_adc": FLAG_MQ2, "mq9_adc": FLAG_MQ9, "mq135_adc": FLAG_MQ135}
COLS = ["timestamp_utc", "device_id", "pm25_ugm3", "pm10_ugm3", "mq2_adc", "mq9_adc", "mq135_adc",
        "warmup", "sensor_flags"]


def load_frame(db: Session, zone_id: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Raw samples for a zone in [start, end], with `pm_ok` and per-channel `<mq>_ok` columns.

    A sample's PM is usable only if: not warm-up, no sensor-fault bit set, value present and plausible.
    An MQ channel is usable if: not warm-up, that channel's fault bit clear, value present.
    Nothing is imputed; unusable values stay in the frame but are masked by the *_ok columns.
    """
    rows = db.execute(select(*[getattr(Measurement, c) for c in COLS]).where(
        Measurement.zone_id == zone_id, Measurement.timestamp_utc >= start,
        Measurement.timestamp_utc <= end).order_by(Measurement.timestamp_utc)).all()
    df = pd.DataFrame(rows, columns=COLS)
    if df.empty:
        return df
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    hi25, hi10 = PLAUSIBLE_RANGES["pm25_ugm3"][1], PLAUSIBLE_RANGES["pm10_ugm3"][1]
    clean = (~df["warmup"].astype(bool)) & (df["sensor_flags"] == 0)
    df["pm_ok"] = clean & df["pm25_ugm3"].notna() & df["pm10_ugm3"].notna() \
        & (df["pm25_ugm3"] <= hi25) & (df["pm10_ugm3"] <= hi10)
    for col, bit in MQ_COLS.items():
        df[col + "_ok"] = (~df["warmup"].astype(bool)) & ((df["sensor_flags"] & bit) == 0) & df[col].notna()
    return df


def bucket_series(df: pd.DataFrame, bucket_min: int) -> pd.DataFrame:
    """Zone-level mean of usable PM per `bucket_min` bucket (index = bucket start, UTC)."""
    if df.empty or not df["pm_ok"].any():
        return pd.DataFrame(columns=["pm25", "pm10", "n"])
    ok = df[df["pm_ok"]].set_index("timestamp_utc")
    g = ok.resample(f"{bucket_min}min")
    out = pd.DataFrame({"pm25": g["pm25_ugm3"].mean(), "pm10": g["pm10_ugm3"].mean(),
                        "n": g["pm25_ugm3"].count()})
    return out[out["n"] > 0]


def mad_sigma(x: np.ndarray, floor_frac: float = 0.08) -> tuple[float, float]:
    """(median, robust sigma). Sigma = 1.4826*MAD, floored at floor_frac*median so a flat
    history doesn't make every wiggle look like a huge z-score."""
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    return med, max(1.4826 * mad, floor_frac * abs(med), 1e-6)
