"""Train and evaluate every ML feature on the simulated year, then write the artifacts the worker/API load.

    python -m ml.data generate --days 365 --start 2025-10-01      # once (about 6 min)
    python -m ml.train_all                                        # about 40 min on a laptop; steps can also be run one by one
    python -m ml.risk fetch                                       # OpenStreetMap exposure data (needs internet; resumable)

Artifacts (ml/artifacts/): forecast.joblib, warning.joblib, source.joblib, anomaly_if.joblib, exposure.json, insights_seed.json
plus *.metrics.json with every held-out score quoted in the docs.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(only: list[str] | None = None) -> None:
    from . import calibration, export, forecast, hotspots, interp, source, trends, warning
    steps = [("forecast", forecast.train), ("warning", warning.train), ("source", source.train), ("hotspots", hotspots.main),
             ("interp", interp.main), ("calibration", calibration.main), ("trends", trends.main), ("export", export.main)]
    for name, fn in steps:
        if only and name not in only:
            continue
        t0 = time.time()
        print(f"\n=== {name} ===", flush=True)
        fn()
        print(f"=== {name} done in {time.time() - t0:.0f}s ===", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
