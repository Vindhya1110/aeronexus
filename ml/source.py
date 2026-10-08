"""Feature 4: pollution-source attribution with a trained classifier (traffic / dust / smoke / industrial / background).

Features per zone and 5-minute step: PM2.5, PM10 and their ratio, PM elevation over the zone's own 24 h baseline, the rise of
each MQ gas-proxy over its own 24 h quiet level (10th percentile - the same device-baseline idea the rule set uses, applied at
zone level), absolute MQ levels, hour/weekday/rush-hour context, temperature/humidity, and the zone's public source tags
(traffic / industrial / construction / dense market - the stand-in for "proximity to known sites from OSM").

Labels come from the simulator's hidden ground truth. That makes this a *method demonstration*: on real hardware the same
code needs field-labelled episodes. Evaluation is on the final 60 days (never seen in training) and is compared, on the same
rows, with a faithful vectorised copy of the deployed rule set (worker/source.py scoring).

    python -m ml.source train
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
from sklearn.metrics import confusion_matrix, f1_score

from shared.zones import ZONES_BY_ID

from . import data as mldata
from .features import IST_OFFSET, STEP_MIN, _rush, regular_grid
from .splits import role

ART = Path(__file__).resolve().parent / "artifacts"
CLASSES = ["traffic", "dust", "smoke", "industrial", "background"]
COLLAPSE = {"traffic": "traffic", "dust": "dust", "smoke": "smoke", "industrial": "smoke", "background": "insufficient"}
TAGS = ["traffic", "industrial", "construction", "dense_market"]
MIN_STRENGTH = 0.0                                   # keep every bucket; "background" is its own class


def source_features(series: pd.DataFrame) -> pd.DataFrame:
    grid = regular_grid(series.drop(columns=[c for c in ("truth_source", "truth_strength", "n") if c in series.columns]))
    parts = []
    for zone, g in grid.groupby("zone", sort=False):
        g = g.sort_values("bucket").reset_index(drop=True)
        w = lambda c: g[c].rolling(3, min_periods=2).mean()          # 15-minute evidence window
        pm25, pm10 = w("pm25"), w("pm10")
        f = pd.DataFrame({"zone": zone, "bucket": g["bucket"], "pm25": pm25, "pm10": pm10,
                          "ratio": pm10 / pm25.replace(0, np.nan)})
        base25 = g["pm25"].rolling(288, min_periods=100).median()
        base10 = g["pm10"].rolling(288, min_periods=100).median()
        f["pm25_rel"], f["pm10_rel"] = pm25 / base25, pm10 / base10
        for c in ("mq2", "mq9", "mq135"):
            cur = w(c)
            q = g[c].rolling(288, min_periods=100).quantile(0.10)
            f[f"d_{c}"] = cur / q - 1.0
            f[c] = cur
        f["temp"], f["hum"] = w("temp"), w("hum")
        ist = g["bucket"] + IST_OFFSET
        hr = ist.dt.hour + ist.dt.minute / 60
        f["hour_sin"], f["hour_cos"] = np.sin(2 * np.pi * hr / 24), np.cos(2 * np.pi * hr / 24)
        f["rush"], f["dow"] = _rush(hr.to_numpy()), ist.dt.dayofweek
        tags = set(ZONES_BY_ID[zone].source_tags) if zone in ZONES_BY_ID else set()
        for t in TAGS:
            f[f"tag_{t}"] = float(t in tags)
        parts.append(f)
    return pd.concat(parts, ignore_index=True)


FEATURES = ["pm25", "pm10", "ratio", "pm25_rel", "pm10_rel", "d_mq2", "d_mq9", "d_mq135", "mq2", "mq9", "mq135", "temp",
            "hum", "hour_sin", "hour_cos", "rush", "dow"] + [f"tag_{t}" for t in TAGS]


# --- faithful vectorised copy of the deployed rule set (worker/source.py), for a like-for-like comparison ----------------
def _ramp(x, lo, hi):
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def rules_predict(f: pd.DataFrame) -> pd.Series:
    d2, d9, d135 = (f[c].to_numpy() for c in ("d_mq2", "d_mq9", "d_mq135"))
    eps = 0.02
    pm_elev25 = np.maximum(_ramp(f["pm25"].to_numpy(), 40, 90), _ramp(f["pm25_rel"].to_numpy(), 1.1, 1.6))
    pm_elev10 = np.maximum(_ramp(f["pm10"].to_numpy(), 80, 200), _ramp(f["pm10_rel"].to_numpy(), 1.1, 1.8))
    ctx = np.where((f["rush"].to_numpy() > 0.5) | (f["tag_traffic"].to_numpy() > 0), 1.0, 0.0)
    traffic = pm_elev25 * _ramp(d9, 0.08, 0.35) * _ramp(d9 / (np.maximum(np.maximum(d2, d135), 0) + eps), 1.0, 2.0) * (0.75 + 0.25 * ctx)
    smoke = pm_elev25 * _ramp(d2, 0.10, 0.40) * _ramp(d135, 0.08, 0.30) * _ramp(((d2 + d135) / 2) / (np.maximum(d9, 0) + eps), 1.0, 1.8)
    nogas = 1 - _ramp(np.maximum(np.maximum(d2, d9), d135), 0.05, 0.20)
    dust = _ramp(f["ratio"].to_numpy(), 1.9, 2.8) * pm_elev10 * nogas
    sc = np.vstack([traffic, dust, smoke]).T
    sc = np.nan_to_num(sc, nan=0.0)
    pri = np.ones_like(sc)
    tg = {t: f[f"tag_{t}"].to_numpy() > 0 for t in TAGS}
    pri[:, 0] = np.where(tg["traffic"] | tg["dense_market"], 1.1, 0.9)
    pri[:, 1] = np.where(tg["construction"], 1.1, 0.9)
    pri[:, 2] = np.where(tg["industrial"] | tg["dense_market"], 1.1, 0.9)
    sc = np.minimum(1.0, sc * pri)
    order = np.argsort(-sc, axis=1)
    s1, s2 = sc[np.arange(len(sc)), order[:, 0]], sc[np.arange(len(sc)), order[:, 1]]
    labels = np.array(["traffic", "dust", "smoke"])[order[:, 0]]
    out = np.where((s1 >= 0.35) & (s1 - s2 >= 0.10), labels, "insufficient")
    return pd.Series(out, index=f.index)


def _report(y_true, y_pred, labels) -> dict:
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    per = {}
    for i, l in enumerate(labels):
        tp, fn, fp = cm[i, i], cm[i].sum() - cm[i, i], cm[:, i].sum() - cm[i, i]
        per[l] = {"recall": float(tp / max(1, tp + fn)), "precision": float(tp / max(1, tp + fp)), "support": int(cm[i].sum())}
    return {"accuracy": float(np.trace(cm) / cm.sum()), "macro_f1": float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)),
            "per_class": per, "confusion": cm.tolist(), "labels": list(labels)}


def train(src: Path = mldata.DEFAULT, out: Path = ART / "source.joblib") -> dict:
    t0 = time.time()
    series = mldata.zone_series(mldata.load(src))
    truth = series[["zone", "bucket", "truth_source", "truth_strength"]]
    f = source_features(series).merge(truth, on=["zone", "bucket"], how="left")
    f = f.dropna(subset=["truth_source", "pm25", "d_mq2", "pm25_rel"])
    f = f[f["truth_source"].isin(CLASSES)]
    f["role"] = role(f["bucket"])
    train_df, test_df = f[f["role"] == "train"], f[f["role"] == "test"]
    clf = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.08, max_leaf_nodes=31, min_samples_leaf=60, l2_regularization=1.0,
                                         early_stopping=True, validation_fraction=0.1, n_iter_no_change=15, random_state=7,
                                         class_weight="balanced").fit(train_df[FEATURES], train_df["truth_source"])
    proba = clf.predict_proba(test_df[FEATURES])
    cls = list(clf.classes_)
    pred5 = np.array(cls)[proba.argmax(axis=1)]
    y5 = test_df["truth_source"].to_numpy()
    y4 = np.array([COLLAPSE[v] for v in y5])
    pred4 = np.array([COLLAPSE[v] for v in pred5])
    rules4 = rules_predict(test_df).to_numpy()
    labels4 = ["traffic", "dust", "smoke", "insufficient"]
    rep = {"ml_5class": _report(y5, pred5, CLASSES), "ml_4class": _report(y4, pred4, labels4), "rules_4class": _report(y4, rules4, labels4),
           "test_rows": int(len(test_df)), "class_share_test": {k: float((y5 == k).mean()) for k in CLASSES}}
    # abstention: say "insufficient evidence" when the top probability is low (honest uncertainty, like the rules)
    ab = {}
    for thr in (0.5, 0.6, 0.7, 0.8):
        keep = proba.max(axis=1) >= thr
        ab[str(thr)] = {"coverage": float(keep.mean()), "accuracy_when_answering": float((pred5[keep] == y5[keep]).mean()) if keep.any() else None}
    rep["abstention_5class"] = ab
    ml4, ru4 = rep["ml_4class"], rep["rules_4class"]
    print(f"4-class accuracy: ML {ml4['accuracy']:.1%} (macro-F1 {ml4['macro_f1']:.2f}) vs rules {ru4['accuracy']:.1%} (macro-F1 {ru4['macro_f1']:.2f})")
    for l in labels4:
        print(f"  {l:13} recall ML {ml4['per_class'][l]['recall']:.0%} / rules {ru4['per_class'][l]['recall']:.0%}   "
              f"precision ML {ml4['per_class'][l]['precision']:.0%} / rules {ru4['per_class'][l]['precision']:.0%}   n={ml4['per_class'][l]['support']}")
    m5 = rep["ml_5class"]
    print(f"5-class accuracy {m5['accuracy']:.1%} macro-F1 {m5['macro_f1']:.2f}; per class recall: " +
          ", ".join(f"{l} {m5['per_class'][l]['recall']:.0%}" for l in CLASSES), flush=True)
    meta = {"classes": cls, "features": FEATURES, "split": "blocked 40-day cycles (ml/splits.py)", "report": rep, "seconds": round(time.time() - t0),
            "note": "labels from simulator ground truth; method demonstration, needs field labels on real hardware"}
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"clf": clf, "meta": meta}, out)
    out.with_suffix(".metrics.json").write_text(json.dumps(meta, indent=1, default=float))
    print(f"saved {out} ({meta['seconds']}s)")
    return meta


_CACHE: dict = {}


def load(path: Path = ART / "source.joblib"):
    if path not in _CACHE:
        _CACHE[path] = joblib.load(path) if path.exists() else None
    return _CACHE[path]


def predict(series: pd.DataFrame, zone: str, bundle=None) -> dict | None:
    """Newest-bucket attribution for `zone`: {label, probability, probabilities{...}}; needs ~24 h of history for the
    baselines (NaN features are handled by the trees, but confidence is then lower - see `history_hours`)."""
    bundle = bundle or load()
    if bundle is None or series.empty:
        return None
    f = source_features(series[series["zone"] == zone])
    if f.empty:
        return None
    row = f.iloc[[-1]]
    p = bundle["clf"].predict_proba(row[FEATURES])[0]
    cls = list(bundle["clf"].classes_)
    top = int(p.argmax())
    return {"label": cls[top], "probability": float(p[top]), "probabilities": {c: round(float(v), 3) for c, v in zip(cls, p)},
            "origin": row["bucket"].iloc[0], "history_hours": round(len(f) * STEP_MIN / 60, 1)}


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if len(sys.argv) > 1 and sys.argv[1] == "train":
        train()
