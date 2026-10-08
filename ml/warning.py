"""Feature 9: early-warning classifier - probability that a zone's AQI crosses a threshold within the next 3 hours.

Two thresholds (CPCB categories): "Poor or worse" (AQI > 200) and "Very Poor or worse" (AQI > 300).
Scored on a held-out final stretch of the year against two rules a person might write instead:
  * persistence rule: warn if the AQI is ALREADY above the threshold,
  * trend rule: warn if current AQI + 45-min slope x 3 h would exceed it.
The operating point (probability cut-off) is chosen on a validation stretch BEFORE the test period, never on the test.

    python -m ml.warning train
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from . import data as mldata
from .features import ALL_FEATURES, STEP_MIN, base_features, with_horizon
from .splits import role

ART = Path(__file__).resolve().parent / "artifacts"
LOOKAHEAD_MIN = 180
THRESHOLDS = {"poor": 200, "very_poor": 300}


def _future_max(f: pd.DataFrame) -> pd.Series:
    steps = LOOKAHEAD_MIN // STEP_MIN
    return f.groupby("zone")["aqi_series"].transform(lambda s: s[::-1].rolling(steps, min_periods=steps // 2).max()[::-1].shift(-1))


def _prf(y, flag) -> dict:
    tp, fp, fn = int(((flag == 1) & (y == 1)).sum()), int(((flag == 1) & (y == 0)).sum()), int(((flag == 0) & (y == 1)).sum())
    p, r = tp / max(1, tp + fp), tp / max(1, tp + fn)
    return {"precision": p, "recall": r, "f1": 2 * p * r / max(1e-9, p + r), "alerts": tp + fp, "events": tp + fn}


def train(src: Path = mldata.DEFAULT, out: Path = ART / "warning.joblib") -> dict:
    t0 = time.time()
    f = base_features(mldata.zone_series(mldata.load(src)))
    d = with_horizon(f, LOOKAHEAD_MIN)
    d["fmax"] = _future_max(f)
    d["slope45"] = (d["aqi"] - d.groupby("zone")["aqi"].shift(9)) / 9.0
    d = d.dropna(subset=["fmax", "aqi"])
    d["role"] = role(d["bucket"])
    train_df, val_df, test_df = (d[d["role"] == r] for r in ("train", "val", "test"))
    models, report = {}, {}
    for name, thr in THRESHOLDS.items():
        ytr, yva, yte = ((x["fmax"] > thr).astype(int) for x in (train_df, val_df, test_df))
        clf = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.08, max_leaf_nodes=31, min_samples_leaf=60,
                                             l2_regularization=1.0, early_stopping=True, validation_fraction=0.1,
                                             n_iter_no_change=15, random_state=7).fit(train_df[ALL_FEATURES], ytr)
        pva, pte = clf.predict_proba(val_df[ALL_FEATURES])[:, 1], clf.predict_proba(test_df[ALL_FEATURES])[:, 1]
        # operating point: probability cut-off maximising F1 on the VALIDATION stretch
        cuts = np.linspace(0.05, 0.95, 91)
        best = max(cuts, key=lambda c: _prf(yva.to_numpy(), (pva >= c).astype(int))["f1"])
        y = yte.to_numpy()
        onset = (test_df["aqi"] <= thr).to_numpy()           # the hard, useful case: not yet over the line
        rule_p = (test_df["aqi"] > thr).astype(int).to_numpy()
        rule_t = ((test_df["aqi"] + test_df["slope45"].fillna(0) * (LOOKAHEAD_MIN // STEP_MIN)) > thr).astype(int).to_numpy()
        res = {"base_rate_test": float(y.mean()), "cutoff": float(best),
               "roc_auc": float(roc_auc_score(y, pte)), "pr_auc": float(average_precision_score(y, pte)),
               "brier": float(brier_score_loss(y, pte)), "brier_base_rate": float(brier_score_loss(y, np.full(len(y), y.mean()))),
               "model": _prf(y, (pte >= best).astype(int)), "persistence_rule": _prf(y, rule_p), "trend_rule": _prf(y, rule_t),
               "onset_only": {"n": int(onset.sum()), "base_rate": float(y[onset].mean()) if onset.any() else None,
                              "model": _prf(y[onset], (pte[onset] >= best).astype(int)),
                              "persistence_rule": _prf(y[onset], rule_p[onset]),
                              "trend_rule": _prf(y[onset], rule_t[onset]),
                              "roc_auc": float(roc_auc_score(y[onset], pte[onset])) if 0 < y[onset].mean() < 1 else None}}
        report[name] = res
        models[name] = {"clf": clf, "cutoff": float(best), "threshold": thr}
        m, o = res["model"], res["onset_only"]
        print(f"{name:9} AUC {res['roc_auc']:.3f} PR-AUC {res['pr_auc']:.3f} Brier {res['brier']:.3f} (base {res['brier_base_rate']:.3f}) | "
              f"alert F1 model {m['f1']:.2f} vs persist {res['persistence_rule']['f1']:.2f} vs trend {res['trend_rule']['f1']:.2f} | "
              f"ONSET F1 model {o['model']['f1']:.2f} vs persist {o['persistence_rule']['f1']:.2f} vs trend {o['trend_rule']['f1']:.2f} "
              f"(onset n={o['n']}, base {o['base_rate']:.3f})", flush=True)
    meta = {"lookahead_min": LOOKAHEAD_MIN, "thresholds": THRESHOLDS, "features": ALL_FEATURES, "split": "blocked 40-day cycles (ml/splits.py)", "report": report, "seconds": round(time.time() - t0)}
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"models": models, "meta": meta}, out)
    out.with_suffix(".metrics.json").write_text(json.dumps(meta, indent=1, default=float))
    print(f"saved {out} ({meta['seconds']}s)")
    return meta


_CACHE: dict = {}


def load(path: Path = ART / "warning.joblib"):
    if path not in _CACHE:
        _CACHE[path] = joblib.load(path) if path.exists() else None
    return _CACHE[path]


def predict(series: pd.DataFrame, zone: str, bundle=None) -> dict | None:
    """Probabilities for the newest bucket of `zone`: {'poor': {'p','alert'}, 'very_poor': {...}, 'origin':..., 'aqi_now':...}."""
    bundle = bundle or load()
    if bundle is None or series.empty:
        return None
    f = base_features(series[series["zone"] == zone])
    if f.empty:
        return None
    row = with_horizon(f, bundle["meta"]["lookahead_min"]).iloc[[-1]]
    out = {"origin": row["bucket"].iloc[0], "aqi_now": None if pd.isna(row["aqi"].iloc[0]) else float(row["aqi"].iloc[0]),
           "lookahead_min": bundle["meta"]["lookahead_min"], "history_hours": round(len(f) * STEP_MIN / 60, 1)}
    for name, m in bundle["models"].items():
        p = float(m["clf"].predict_proba(row[ALL_FEATURES])[0, 1])
        out[name] = {"p": p, "alert": bool(p >= m["cutoff"]), "cutoff": m["cutoff"], "threshold_aqi": m["threshold"]}
    return out


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if len(sys.argv) > 1 and sys.argv[1] == "train":
        train()
