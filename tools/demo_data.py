"""Prepare + load the seeded demo dataset (deterministic: same seed -> byte-identical files).

    python tools/demo_data.py prepare            # (re)generate data/demo/demo_full.jsonl (+ ground truth) and the live split
    python tools/demo_data.py load --mode replay # fresh DB <- whole narrative (anomaly in progress at the end)
    python tools/demo_data.py load --mode live   # fresh DB <- history up to 09:30 IST; simulator continues from there

`load` always starts from an EMPTY database (the DB file is deleted first; stop the backend before calling it),
runs the shared ingest path via tools/load_replay.py, then `worker.run_once --backfill` so zone AQI / source
labels / events / forecasts exist before the dashboard opens. Writes data/sim_state.json for live mode so the
simulator continues the sequence numbers (no duplicate or colliding packets).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.auth import hash_password  # noqa: E402
from backend.db import init_db, make_engine, make_session_factory  # noqa: E402
from backend.models import User  # noqa: E402
from simulator import scenario  # noqa: E402

DEMO = ROOT / "data" / "demo"
FULL = DEMO / "demo_full.jsonl"
HISTORY = DEMO / "demo_history.jsonl"
STATE = ROOT / "data" / "sim_state.json"
DEFAULT_DB = DEMO / "demo.db"
PY = sys.executable


def _run(*args: str, env: dict | None = None) -> None:
    subprocess.run([PY, *args], cwd=ROOT, check=True, env={**os.environ, **(env or {})})


def prepare() -> None:
    DEMO.mkdir(parents=True, exist_ok=True)
    _run("-m", "simulator", "dump", "--scenario", "demo", "--no-faults", "--offline-routes",
         "--with-ground-truth", "--out", str(FULL))
    split = scenario.DEMO_LIVE_SPLIT.strftime("%Y-%m-%dT%H:%M:%SZ")
    last: dict[str, int] = {}
    n = 0
    with FULL.open(encoding="utf-8") as src, HISTORY.open("w", encoding="utf-8", newline="\n") as out:
        for line in src:
            rec = json.loads(line)
            if rec["timestamp_utc"] >= split:        # ISO-Z strings sort chronologically
                continue
            out.write(line)
            last[rec["device_id"]] = max(last.get(rec["device_id"], 0), rec["sequence"])
            n += 1
    (DEMO / "demo_sim_state.json").write_text(json.dumps(last))
    digest = hashlib.sha256(FULL.read_bytes()).hexdigest()[:16]
    print(f"history: {n} payloads before {split}; dataset sha256[:16]={digest}")


CREDS = DEMO / "credentials.json"
DEMO_ACCOUNTS = (("official.demo", "admin", "Demo Official"), ("public.demo", "user", "Demo Citizen"))


def credentials() -> dict:
    """Demo logins + the token-signing secret. Generated once, then reused so restarts keep passwords and sessions."""
    old = json.loads(CREDS.read_text()) if CREDS.exists() else None
    if old and all(u in old.get("accounts", {}) for u, _, _ in DEMO_ACCOUNTS):
        return old
    c = {"auth_secret": (old or {}).get("auth_secret") or secrets.token_urlsafe(32),     # keep the signing key when only the accounts change
         "accounts": {u: {"role": r, "password": secrets.token_urlsafe(9)} for u, r, _ in DEMO_ACCOUNTS}}
    CREDS.write_text(json.dumps(c, indent=1))
    lines = ["Aeronex demo logins (generated; delete data/demo/credentials.json to regenerate)", "",
             "account        role    password", "-------------  ------  ------------"]
    lines += [f"{u:13}  {('official' if v['role'] == 'admin' else 'public'):6}  {v['password']}" for u, v in c["accounts"].items()]
    (DEMO / "credentials.txt").write_text(chr(10).join(lines) + chr(10))
    return c


def ensure_accounts(db: Path) -> None:
    """Accounts live in the DB, which is recreated on every start: re-insert the demo logins after each load."""
    creds = credentials()["accounts"]
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    factory = make_session_factory(engine)
    init_db(engine, factory, True)
    with factory() as s:
        for username, role, name in DEMO_ACCOUNTS:
            if not s.query(User).filter_by(username=username).first():
                s.add(User(username=username, display_name=name, role=role,
                           password_hash=hash_password(creds[username]["password"])))
        s.commit()
    engine.dispose()
    print(f"demo accounts ready (logins in {DEMO / 'credentials.txt'})")


def ensure_insights(db: Path) -> None:
    """Store the long-horizon analyses (hotspots, trends, drift correction) computed on the simulated year, if available."""
    seed = ROOT / "ml" / "artifacts" / "insights_seed.json"
    if not seed.exists():
        print("no ml/artifacts/insights_seed.json (run: python -m ml.export): hotspots / trends / drift panels will be empty")
        return
    from backend.models import Insight, utcnow
    payload = json.loads(seed.read_text())
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    factory = make_session_factory(engine)
    init_db(engine, factory, True)
    with factory() as s:
        for kind, body in payload.items():
            s.query(Insight).filter_by(kind=kind, key="city").delete()
            s.add(Insight(kind=kind, key="city", source="simulated-year-dataset", computed_at=utcnow(), payload=body))
        s.commit()
    engine.dispose()
    print("long-horizon insights stored (hotspots, trends, calibration)")


def _fingerprint() -> str:
    """Hash of the dataset + every source file that shapes the DB, so a stale snapshot is never reused."""
    h = hashlib.sha256(FULL.read_bytes())
    for sub in ("backend", "worker", "shared", "simulator", "ml"):
        for f in sorted((ROOT / sub).rglob("*.py")):
            h.update(f.read_bytes())
    for f in sorted((ROOT / "ml" / "artifacts").glob("*")):          # trained models / seeds: size + mtime is enough
        h.update(f"{f.name}:{f.stat().st_size}:{int(f.stat().st_mtime)}".encode())
    return h.hexdigest()


def _build(mode: str, db: Path) -> None:
    env = {"AIRDNA_DATABASE_URL": f"sqlite:///{db.as_posix()}", "AIRDNA_ML_INTERVAL_MIN": "30"}   # coarser ML cadence while backfilling
    t0 = time.time()
    _run("tools/load_replay.py", str(FULL if mode == "replay" else HISTORY), env=env)
    t1 = time.time()
    # rule-based analytics are replayed pass by pass only over the story day (the ML models read the 3 days of history from the DB)
    _run("-m", "worker.run_once", "--backfill", "--from", "2026-12-09T22:30:00Z", env=env)
    print(f"built from scratch: ingest {t1 - t0:.1f}s + worker backfill {time.time() - t1:.1f}s")


def load(mode: str, db: Path, rebuild: bool) -> None:
    """Fresh DB at `db`. The first run (or --rebuild) ingests + backfills (~1 min) and keeps a snapshot;
    later resets just copy the snapshot (~1 s). Same result either way (determinism is tested)."""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    snap, stamp = DEMO / f"snapshot_{mode}.db", DEMO / f"snapshot_{mode}.fingerprint"
    fp = _fingerprint()
    if not rebuild and snap.exists() and stamp.exists() and stamp.read_text() == fp:
        shutil.copyfile(snap, db)
        print(f"restored from snapshot ({snap.name}) in <1s")
    else:
        _build(mode, db)
        src = sqlite3.connect(db)                       # backup API = consistent copy without the WAL file
        dst = sqlite3.connect(snap)
        src.backup(dst)
        src.close()
        dst.close()
        stamp.write_text(fp)
    ensure_accounts(db)
    ensure_insights(db)
    if mode == "live":
        STATE.write_text((DEMO / "demo_sim_state.json").read_text())
    elif STATE.exists():
        STATE.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    ld = sub.add_parser("load")
    ld.add_argument("--mode", choices=["replay", "live"], default="replay")
    ld.add_argument("--db", type=Path, default=DEFAULT_DB)
    ld.add_argument("--rebuild", action="store_true", help="ignore the snapshot and re-ingest from the JSONL")
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare()
    else:
        if not FULL.exists() or not HISTORY.exists():
            prepare()
        load(a.mode, a.db, a.rebuild)


if __name__ == "__main__":
    main()
