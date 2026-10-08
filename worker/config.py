"""Worker tunables. DB URL / offline threshold / current window come from backend.config so the
worker and API cannot disagree about them."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from backend.config import Settings


@dataclass(frozen=True)
class WorkerSettings:
    backend: Settings = field(default_factory=Settings.from_env)
    interval_s: int = 60                 # scheduler period
    smoothing_window_min: int = 30       # rolling mean for smoothed PM / AQI
    evidence_window_min: int = 15        # window for source-hypothesis features
    trend_window_min: int = 45           # linear-trend fit window (spec: 30-60)
    bucket_min: int = 5                  # zone series resolution
    anomaly_lookback_h: int = 3          # rolling median/MAD history
    anomaly_min_history_buckets: int = 12
    tod_days: int = 7                    # previous days used for time-of-day baseline
    tod_min_days: int = 2
    mq_baseline_hours: int = 24
    mq_baseline_min_samples: int = 30
    confirm_after_min: int = 15          # breach must persist this long (data time) to be 'confirmed'
    clear_after_min: int = 10            # must stay clear this long before 'rechecked'
    ml_interval_min: int = 5             # ML models re-run at most this often per zone (data time); backfill uses a coarser value
    method_version: str = "worker-1.0"

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        return cls(interval_s=int(os.getenv("AIRDNA_WORKER_INTERVAL_S", 60)),
                   ml_interval_min=int(os.getenv("AIRDNA_ML_INTERVAL_MIN", 5)))
