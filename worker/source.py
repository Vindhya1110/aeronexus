"""Transparent rule-based source hypothesis. See docs/source_hypothesis.md for the rule table.

Honesty rules:
  * Output is always (label, confidence, features); labels use "probable" wording.
  * MQ values are raw ADC proxy indices. They are only compared with the SAME device's own
    quiet baseline (10th percentile of its last 24 h), never converted to concentrations.
  * The zone's public source_context/source_tags act as a mild prior (x1.1 if consistent, x0.9 if
    not). They can nudge a score; they cannot create evidence.
  * Nothing here can see the simulator's hidden ground truth.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.constants import GAS_PROXY_LABEL
from backend.models import Measurement

from .config import WorkerSettings
from .data import MQ_COLS
from .anomaly import tod_baselines  # noqa: F401  (re-exported for convenience)

TRAFFIC = "probable traffic/combustion signature"
DUST = "probable dust-dominant signature"
SMOKE = "probable smoke/combustion signature"
INSUFFICIENT = "insufficient evidence"
# `confidence` has a different meaning per outcome; spelled out in features so no consumer has to guess.
MEANING_LABEL = "evidence strength for the named label (best rule score x sample-quality factor, capped)"
MEANING_ABSTAIN = "confidence that NO rule fits (1 - best rule score, floored 0.1, capped): high = clearly no signature"
MEANING_NODATA = "placeholder floor: too few samples to judge either way (not a measured confidence)"

# which public zone tags make each signature more/less plausible a priori
PRIOR_TAGS = {TRAFFIC: {"traffic", "dense_market"}, DUST: {"construction"},
              SMOKE: {"industrial", "dense_market"}}

MIN_SCORE, MIN_MARGIN, MAX_CONF = 0.35, 0.10, 0.90      # a heuristic never reports certainty
RULES = {
    DUST: "PM10/PM2.5 ratio high AND PM10 elevated AND all MQ proxies near their own baseline",
    TRAFFIC: "PM elevated AND MQ-9 proxy risen AND MQ-9 rising faster than MQ-2/MQ-135 (rush hour/traffic context adds)",
    SMOKE: "PM2.5 elevated AND MQ-2 and MQ-135 proxies both risen AND rising faster than MQ-9",
}


def ramp(x: float, lo: float, hi: float) -> float:
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def is_rush_hour(as_of: datetime) -> bool:
    h = (as_of + timedelta(hours=5, minutes=30)).hour          # IST
    return 7 <= h < 11 or 17 <= h < 21


class MqBaselines:
    """Per-device quiet baseline of each MQ proxy: p10 of its own last 24 h (cached 30 min of data time)."""

    def __init__(self, cfg: WorkerSettings):
        self.cfg = cfg
        self._cache: dict[tuple, dict] = {}

    def get(self, db: Session, device_id: str, as_of: datetime) -> dict[str, Optional[float]]:
        slot = int(as_of.timestamp() // 1800)
        key = (device_id, slot)
        if key not in self._cache:
            start = as_of - timedelta(hours=self.cfg.mq_baseline_hours)
            rows = db.execute(select(Measurement.mq2_adc, Measurement.mq9_adc, Measurement.mq135_adc,
                                     Measurement.sensor_flags).where(
                Measurement.device_id == device_id, Measurement.timestamp_utc >= start,
                Measurement.timestamp_utc <= as_of, Measurement.warmup.is_(False))).all()
            res: dict[str, Optional[float]] = {}
            for i, col in enumerate(MQ_COLS):
                bit = MQ_COLS[col]
                v = np.array([r[i] for r in rows if r[i] is not None and not (r[3] & bit)], dtype=float)
                res[col] = float(np.percentile(v, 10)) if len(v) >= self.cfg.mq_baseline_min_samples else None
            self._cache[key] = res
            if len(self._cache) > 2000:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[key]


def hypothesize(db: Session, frame: pd.DataFrame, as_of: datetime, tags: tuple[str, ...],
                pm_baseline: Optional[dict], baselines: MqBaselines, cfg: WorkerSettings) -> dict:
    """Returns {label, confidence, features}. `frame` = recent usable-or-not samples for the zone."""
    start = as_of - timedelta(minutes=cfg.evidence_window_min)
    ev = frame[(frame["timestamp_utc"] > start) & frame["pm_ok"]] if not frame.empty else frame
    if len(ev) < 3:
        return {"label": INSUFFICIENT, "confidence": 0.1, "features": {
            "reason": f"only {len(ev)} usable samples in the last {cfg.evidence_window_min} min",
            "confidence_meaning": MEANING_NODATA,
            "n_samples": int(len(ev))}}

    pm25, pm10 = float(ev["pm25_ugm3"].mean()), float(ev["pm10_ugm3"].mean())
    ratio = pm10 / pm25 if pm25 > 0 else None

    # MQ proxy rise over each device's own baseline, averaged over devices with a baseline
    rises: dict[str, list[float]] = {c: [] for c in MQ_COLS}
    for dev, g in ev.groupby("device_id"):
        base = baselines.get(db, dev, as_of)
        for col in MQ_COLS:
            gg = g[g[col + "_ok"]]
            if base[col] and len(gg):
                rises[col].append(float(gg[col].mean()) / base[col] - 1.0)
    d = {c: (float(np.mean(v)) if v else None) for c, v in rises.items()}
    gas_known = all(v is not None for v in d.values())

    b25 = (pm_baseline or {}).get("pm25")
    b10 = (pm_baseline or {}).get("pm10")
    pm_elev25 = max(ramp(pm25, 40, 90), ramp(pm25 / b25, 1.1, 1.6) if b25 else 0.0)
    pm_elev10 = max(ramp(pm10, 80, 200), ramp(pm10 / b10, 1.1, 1.8) if b10 else 0.0)
    rush = is_rush_hour(as_of)

    scores = {TRAFFIC: 0.0, DUST: 0.0, SMOKE: 0.0}
    if gas_known:
        d2, d9, d135 = d["mq2_adc"], d["mq9_adc"], d["mq135_adc"]
        eps = 0.02
        scores[TRAFFIC] = pm_elev25 * ramp(d9, 0.08, 0.35) * ramp(d9 / (max(d2, d135, 0) + eps), 1.0, 2.0)
        ctx = 1.0 if (rush or "traffic" in tags) else 0.0
        scores[TRAFFIC] *= 0.75 + 0.25 * ctx
        scores[SMOKE] = (pm_elev25 * ramp(d2, 0.10, 0.40) * ramp(d135, 0.08, 0.30)
                         * ramp(((d2 + d135) / 2) / (max(d9, 0) + eps), 1.0, 1.8))
        no_gas = 1 - ramp(max(d2, d9, d135), 0.05, 0.20)
    else:
        no_gas = 0.5                                    # cannot confirm 'MQ near baseline'
    scores[DUST] = ramp(ratio or 0, 1.9, 2.8) * pm_elev10 * no_gas

    for lab in scores:                                  # mild public-context prior
        if tags:
            scores[lab] = min(1.0, scores[lab] * (1.1 if PRIOR_TAGS[lab] & set(tags) else 0.9))

    quality = min(1.0, len(ev) / 20) * (1.0 if gas_known else 0.7)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, s1), (second, s2) = ranked[0], ranked[1]
    if s1 >= MIN_SCORE and s1 - s2 >= MIN_MARGIN:
        label, conf = best, min(MAX_CONF, s1 * quality)
    else:
        label, conf = INSUFFICIENT, min(MAX_CONF, max(0.1, 1.0 - s1))

    return {"label": label, "confidence": round(float(conf), 3), "features": {
        "pm25_ugm3": round(pm25, 1), "pm10_ugm3": round(pm10, 1),
        "pm10_pm25_ratio": None if ratio is None else round(ratio, 2),
        "mq_rise_over_own_baseline": {k: None if v is None else round(v, 3) for k, v in d.items()},
        "mq_note": GAS_PROXY_LABEL + "; compared only with each device's own quiet baseline (p10 of last 24 h)",
        "pm_elevation": {"pm25": round(pm_elev25, 2), "pm10": round(pm_elev10, 2)},
        "rush_hour_ist": rush, "zone_source_tags": list(tags),
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "runner_up": second if label != INSUFFICIENT else best,
        "rule_fired": RULES.get(label),
        "n_samples": int(len(ev)), "n_devices": int(ev["device_id"].nunique()),
        "gas_baseline_available": gas_known,
        "evidence_window_min": cfg.evidence_window_min,
        "confidence_meaning": MEANING_LABEL if label != INSUFFICIENT else MEANING_ABSTAIN,
        "method": "rule-based heuristic; 'probable' attribution, not a lab measurement",
    }}
