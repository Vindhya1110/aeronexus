"""Evaluate the worker's source hypothesis against the simulator's hidden ground truth.

EVALUATION ONLY. The ground-truth file (`python -m simulator dump --with-ground-truth`) is never
sent to the backend and the worker never reads it; this tool joins the two after the fact.

Setup:  load a replay into the DB, backfill the worker over it, start the backend, then run this.
    python tools/load_replay.py data/replay.jsonl
    python -m worker.run_once --backfill
    uvicorn backend.main:app --port 8001
    python tools/evaluate_source_hypothesis.py data/replay.ground_truth.jsonl --url http://localhost:8001

Matching (default --mode window): for each stored worker output (zone, window_end), the truth is the
majority `dominant_source` among ground-truth records (device_id, sequence, timestamp_utc) of that zone
inside the output's evidence window. --mode record instead scores every ground-truth record against the
first worker output at or after its timestamp.

Truth -> expected worker label:
    traffic -> traffic/combustion      dust -> dust-dominant
    smoke, industrial -> smoke/combustion   (the worker has no separate 'industrial' label)
    background -> insufficient evidence      (no source stands out)
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from worker.source import DUST, INSUFFICIENT, SMOKE, TRAFFIC  # noqa: E402

SHORT = {TRAFFIC: "traffic", DUST: "dust", SMOKE: "smoke", INSUFFICIENT: "insufficient"}
TRUTH_TO_CLASS = {"traffic": "traffic", "dust": "dust", "smoke": "smoke", "industrial": "smoke",
                  "background": "insufficient"}
CLASSES = ["traffic", "dust", "smoke", "insufficient"]


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_truth(path: str, min_strength: float = 0.0) -> dict[str, list[tuple[datetime, str, str, int]]]:
    by_zone = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["zone_id"] and (r["dominant_source"] == "background" or r.get("dominant_strength", 9) >= min_strength):
                by_zone[r["zone_id"]].append((ts(r["timestamp_utc"]), TRUTH_TO_CLASS[r["dominant_source"]],
                                              r["device_id"], r["sequence"]))
    for v in by_zone.values():
        v.sort()
    return by_zone


def fetch_outputs(url: str, zone: str) -> list[dict]:
    r = requests.get(f"{url.rstrip('/')}/v1/zones/{zone}/derived", params={"limit": 5000}, timeout=60)
    r.raise_for_status()
    return [x for x in r.json()["rows"] if x["source_label"]]


def pairs_window(truth, outputs, window_min, min_records):
    out = []
    for zone, rows in outputs.items():
        recs = truth.get(zone, [])
        times = [r[0] for r in recs]
        for o in rows:
            end = ts(o["window_end_utc"])
            lo, hi = bisect.bisect_right(times, end - timedelta(minutes=window_min)), bisect.bisect_right(times, end)
            seg = recs[lo:hi]
            if len(seg) < min_records:
                continue
            true = Counter(r[1] for r in seg).most_common(1)[0][0]
            out.append((true, SHORT[o["source_label"]], o["source_confidence"], zone))
    return out


def pairs_record(truth, outputs, max_gap_min):
    out = []
    for zone, rows in outputs.items():
        ends = [ts(o["window_end_utc"]) for o in rows]
        for t, cls, _dev, _seq in truth.get(zone, []):
            i = bisect.bisect_left(ends, t)
            if i < len(ends) and ends[i] - t <= timedelta(minutes=max_gap_min):
                out.append((cls, SHORT[rows[i]["source_label"]], rows[i]["source_confidence"], zone))
    return out


def report(pairs) -> None:
    if not pairs:
        print("no comparable (truth, output) pairs - did you backfill the worker over this time range?")
        return
    cm = {t: Counter() for t in CLASSES}
    for t, p, *_ in pairs:
        cm[t][p] += 1
    w = 13
    print(f"\nConfusion matrix  (rows = ground truth, columns = worker output, n={len(pairs)})\n")
    print(" " * w + "".join(f"{c:>{w}}" for c in CLASSES) + f"{'total':>{w}}{'recall':>{w}}")
    for t in CLASSES:
        tot = sum(cm[t].values())
        rec = f"{cm[t][t] / tot:.1%}" if tot else "-"
        print(f"{t:<{w}}" + "".join(f"{cm[t][c]:>{w}}" for c in CLASSES) + f"{tot:>{w}}{rec:>{w}}")
    print("\nPer-class (recall = of truly X, how many labelled X; precision = of labelled X, how many truly X)")
    for c in CLASSES:
        tp = cm[c][c]
        n_true, n_pred = sum(cm[c].values()), sum(cm[t][c] for t in CLASSES)
        rec = f"{tp / n_true:6.1%}" if n_true else "   n/a"
        prec = f"{tp / n_pred:6.1%}" if n_pred else "   n/a"
        print(f"  {c:<13} recall {rec} ({tp}/{n_true})   precision {prec} ({tp}/{n_pred})")
    correct = sum(cm[c][c] for c in CLASSES)
    print(f"\nOverall accuracy: {correct / len(pairs):.1%}")
    committed = [p for p in pairs if p[1] != "insufficient" and p[2] is not None]
    if committed:
        right = [p[2] for p in committed if p[0] == p[1]]
        wrong = [p[2] for p in committed if p[0] != p[1]]
        print(f"\nWhen the worker commits to a source ({len(committed)}/{len(pairs)} outputs): "
              f"precision {len(right) / len(committed):.1%}")
        if right and wrong:
            print(f"  mean confidence when correct {sum(right) / len(right):.2f} vs when wrong "
                  f"{sum(wrong) / len(wrong):.2f} (abstentions excluded: their confidence means 'no rule fits')")
    src = [(t, p) for t, p, *_ in pairs if t != "insufficient"]
    if src:
        ab = sum(1 for t, p in src if p == "insufficient")
        print(f"Where a real source was present: abstained ('insufficient evidence') {ab / len(src):.1%}, "
              f"wrong source {sum(1 for t, p in src if p not in (t, 'insufficient')) / len(src):.1%}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ground_truth", nargs="?", help="ground-truth JSONL (or use --ground-truth)")
    ap.add_argument("--ground-truth", dest="gt_opt", help="same as the positional argument")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--mode", choices=["window", "record"], default="window")
    ap.add_argument("--evidence-window-min", type=int, default=15, help="must match the worker's setting")
    ap.add_argument("--min-records", type=int, default=5)
    ap.add_argument("--min-strength", type=float, default=0.0,
                    help="ignore ground-truth records whose dominant source adds less than this fraction of the "
                         "background PM10 (0.35 = everything the generator calls a source; 1.0 = clear events only)")
    ap.add_argument("--zones", nargs="*", help="default: all zones present in the ground truth")
    a = ap.parse_args()

    gt_path = a.gt_opt or a.ground_truth
    if not gt_path:
        ap.error("ground-truth file required")
    truth = load_truth(gt_path, a.min_strength)
    zones = a.zones or sorted(truth)
    outputs = {z: fetch_outputs(a.url, z) for z in zones}
    print(f"ground truth: {sum(len(v) for v in truth.values())} records; worker outputs: "
          f"{sum(len(v) for v in outputs.values())} across {len(zones)} zones; mode={a.mode}")
    pairs = (pairs_window(truth, outputs, a.evidence_window_min, a.min_records) if a.mode == "window"
             else pairs_record(truth, outputs, max_gap_min=10))
    report(pairs)


if __name__ == "__main__":
    main()
