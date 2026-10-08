"""Fingerprint of everything the demo narrative shows, to prove two runs are identical.

    python tools/narrative_fingerprint.py [--db data/demo/demo.db] [--json]

Hashes (a) the ingested measurements, (b) the worker's derived rows (AQI, source label + confidence + features),
(c) events including their lifecycle, (d) forecasts. Wall-clock bookkeeping columns (row ids, computed_at,
received_at, issued_at, coverage ages) are excluded: they differ between runs by design. Also prints the HITEC story.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUERIES = {
    "measurements": "SELECT device_id, zone_id, timestamp_utc, pm25_ugm3, pm10_ugm3, mq2_adc, mq9_adc, mq135_adc, quality_flags "
                    "FROM measurements ORDER BY device_id, timestamp_utc",
    "derived": "SELECT zone_id, window_end, method_version, n_samples, n_devices, pm25_smoothed, pm10_smoothed, aqi, aqi_category, "
               "source_label, source_confidence, source_features FROM derived_measurements ORDER BY zone_id, window_end",
    "events": "SELECT zone_id, event_type, status, severity, detected_at, updated_at, details FROM events "
              "ORDER BY zone_id, detected_at, json_extract(details,'$.metric')",
    "forecasts": "SELECT zone_id, horizon_min, method, model_version, pm25_pred, pm10_pred, confidence, basis FROM forecasts "
                 "ORDER BY zone_id, horizon_min, method, issued_at",
}


def fingerprint(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    out = {}
    for name, q in QUERIES.items():
        h, n = hashlib.sha256(), 0
        for row in con.execute(q):
            h.update(json.dumps(row, sort_keys=True, default=str).encode())
            n += 1
        out[name] = {"rows": n, "sha256": h.hexdigest()[:16]}
    story = con.execute("SELECT status, detected_at, json_extract(details,'$.metric'), "
                        "json_extract(details,'$.source_hypothesis_at_detection.label'), json_extract(details,'$.recommended_action') "
                        "FROM events WHERE zone_id='HITEC' ORDER BY detected_at DESC, id LIMIT 2").fetchall()
    last = con.execute("SELECT window_end, aqi, aqi_category, source_label, source_confidence FROM derived_measurements "
                       "WHERE zone_id='HITEC' ORDER BY window_end DESC LIMIT 1").fetchone()
    out["story"] = {"hitec_events": story, "hitec_last_window": last}
    con.close()
    out["overall"] = hashlib.sha256(json.dumps({k: v for k, v in out.items() if k != "story"}, sort_keys=True).encode()).hexdigest()[:16]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "demo" / "demo.db")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    fp = fingerprint(a.db)
    if a.json:
        print(json.dumps(fp, indent=1, default=str))
        return
    for k in ("measurements", "derived", "events", "forecasts"):
        print(f"{k:13} rows={fp[k]['rows']:6}  sha256={fp[k]['sha256']}")
    print(f"OVERALL FINGERPRINT {fp['overall']}")
    print("HITEC last window:", fp["story"]["hitec_last_window"])
    for e in fp["story"]["hitec_events"]:
        print("HITEC event      :", e)


if __name__ == "__main__":
    main()
