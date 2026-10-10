"""Ingest integrity check for the demo DB: no duplicate packets, no sequence gaps (= no data loss).

    python tools/check_integrity.py [--db data/demo/demo.db] [--snapshot out.json] [--compare before.json]

The simulator (with --no-faults) numbers each device's packets 1,2,3,...; so any missing number in
1..max(sequence) is a packet that never reached the database, and the (device_id, sequence) unique key
guarantees duplicates cannot exist (reported anyway as a cross-check on `raw_measurements` vs `measurements`).
Exit code 0 = clean, 1 = loss or duplicates found.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def inspect(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.execute("BEGIN")                 # one read snapshot: a packet committed mid-check must not skew raw vs typed counts
    per_dev: dict[str, dict] = {}
    for dev, n, distinct, mx in con.execute(
            "SELECT device_id, COUNT(*), COUNT(DISTINCT sequence), MAX(sequence) FROM raw_measurements GROUP BY device_id"):
        have = {r[0] for r in con.execute("SELECT sequence FROM raw_measurements WHERE device_id=?", (dev,))}
        # a device that joined mid-stream (e.g. the ESP32 node, whose sequence numbers survive a database reset) starts above 1:
        # loss is then measured inside [first, last]; simulated devices always start at 1 so the whole range is checked
        first = min(have)
        missing = sorted(set(range(first if first > 1 else 1, mx + 1)) - have)
        per_dev[dev] = {"rows": n, "distinct": distinct, "max_seq": mx, "duplicates": n - distinct,
                        "missing": len(missing), "missing_sample": missing[:5]}
    typed = con.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
    raw = con.execute("SELECT COUNT(*) FROM raw_measurements").fetchone()[0]
    con.close()
    return {"raw_rows": raw, "measurement_rows": typed, "devices": per_dev}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "demo" / "demo.db")
    ap.add_argument("--snapshot", type=Path, help="write the result as JSON (for --compare later)")
    ap.add_argument("--compare", type=Path, help="JSON written earlier; require that no device lost rows since")
    a = ap.parse_args()
    res = inspect(a.db)
    bad = 0
    print(f"raw_measurements={res['raw_rows']}  measurements={res['measurement_rows']}")
    for dev, d in sorted(res["devices"].items()):
        flag = "OK " if d["missing"] == 0 and d["duplicates"] == 0 else "BAD"
        bad += flag == "BAD"
        print(f"  {flag} {dev:15} rows={d['rows']:5} max_seq={d['max_seq']:5} missing={d['missing']} dup={d['duplicates']}"
              + (f" e.g. {d['missing_sample']}" if d["missing"] else ""))
    if res["raw_rows"] != res["measurement_rows"]:
        print("  BAD raw vs typed row counts differ"); bad += 1
    if a.compare:
        before = json.loads(a.compare.read_text())
        for dev, d in before["devices"].items():
            now = res["devices"].get(dev, {"rows": 0})
            if now["rows"] < d["rows"]:
                print(f"  BAD {dev} lost rows since baseline ({d['rows']} -> {now['rows']})"); bad += 1
        print(f"  grew by {res['raw_rows'] - before['raw_rows']} packets since baseline")
    if a.snapshot:
        a.snapshot.write_text(json.dumps(res))
    print("INTEGRITY: PASS (no gaps, no duplicates)" if not bad else f"INTEGRITY: FAIL ({bad} problem(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
