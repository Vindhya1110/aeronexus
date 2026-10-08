"""Blocked time splits shared by every model.

Training on the first months and testing on the last would test on a SEASON the model never saw (monsoon vs winter), which
measures extrapolation, not forecasting skill. Instead the year is cut into repeating 40-day cycles:

    days 0-21 train | 22-25 gap | 26-28 validation | 29-32 gap | 33-36 test | 37-39 gap

so every season appears in train, validation and test, while the gaps (each >= 3 days, the longest 72 h target window) guarantee that no
row's label window overlaps another role's data in either direction. Features may look back across blocks (they only use the past, as in
production); labels never cross a block boundary.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CYCLE = 40


def role(bucket: pd.Series, start: pd.Timestamp | None = None) -> pd.Series:
    start = start if start is not None else bucket.min().floor("D")
    day = ((bucket - start).dt.total_seconds() // 86400).astype(int) % CYCLE
    r = np.full(len(day), "gap", dtype=object)
    r[(day <= 21).to_numpy()] = "train"
    r[((day >= 26) & (day <= 28)).to_numpy()] = "val"
    r[((day >= 33) & (day <= 36)).to_numpy()] = "test"
    return pd.Series(r, index=bucket.index)
