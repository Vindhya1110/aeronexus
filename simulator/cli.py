"""Aeronex simulator CLI (acts as a fake gateway).

  python -m simulator sample              # print example payloads, no network
  python -m simulator dump --hours 6      # write a replay file (JSONL)
  python -m simulator live                # POST continuously to /v1/telemetry
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .faults import FaultConfig
from .fleet import Fleet
from .gateway import GatewayClient
from . import scenario

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("simulator")


def _parse_ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--start", type=_parse_ts, default=None, help="sim start, ISO-8601 UTC (default: now)")
    p.add_argument("--no-faults", action="store_true", help="disable GPS/sensor/duplicate/malformed injection")
    p.add_argument("--fixed-interval-s", type=int, default=60)
    p.add_argument("--bus-interval-s", type=int, default=30)
    p.add_argument("--scenario", choices=["demo"], default=None,
                   help="overlay the scripted demo narrative (simulator/scenario.py); dump defaults to its time range")
    p.add_argument("--offline-routes", action="store_true", help="never call osmnx; use cache/fallback only")


def _fleet(a, start: datetime) -> Fleet:
    return Fleet(a.seed, start, FaultConfig.none() if a.no_faults else FaultConfig(),
                 a.fixed_interval_s, a.bus_interval_s, allow_route_download=not a.offline_routes)


def cmd_sample(a) -> None:
    start = a.start or datetime.now(timezone.utc)
    fleet = _fleet(a, start)
    shown = 0
    for em in fleet.run_until(start + timedelta(minutes=a.minutes)):
        print(json.dumps(em.wire, indent=None if a.compact else 2) + (f"   # {em.kind}" if em.kind else ""))
        shown += 1
        if shown >= a.count:
            break


def cmd_dump(a) -> None:
    if a.scenario == "demo":
        start, end = a.start or scenario.DEMO_START, a.end or scenario.DEMO_END
    else:
        end = a.end or (a.start + timedelta(hours=a.hours) if a.start else datetime.now(timezone.utc))
        start = a.start or (end - timedelta(hours=a.hours))
    out = Path(a.out) if a.out else ROOT / "data" / "replay.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    fleet = _fleet(a, start)
    n = 0
    gt_path = out.with_name(out.stem + ".ground_truth.jsonl") if a.with_ground_truth else None
    gt = gt_path.open("w", encoding="utf-8") if gt_path else None
    with out.open("w", encoding="utf-8") as f:
        for em in fleet.run_until(end):
            if a.scenario and em.kind != "malformed":
                scenario.apply(em.wire, em.truth)
            f.write(json.dumps(em.wire, separators=(",", ":")) + "\n")
            if gt and em.kind != "malformed":       # malformed rows have no valid key to join on
                w = em.wire
                gt.write(json.dumps({"device_id": w["device_id"], "sequence": w["sequence"],
                                     "timestamp_utc": w["timestamp_utc"], **em.truth},
                                    separators=(",", ":")) + "\n")
            n += 1
    if gt:
        gt.close()
        print(f"ground truth (evaluation only, never POST this) -> {gt_path}")
    print(f"wrote {n} payloads ({start:%Y-%m-%d %H:%MZ} .. {end:%Y-%m-%d %H:%MZ}) -> {out}")


def cmd_live(a) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start = a.start or datetime.now(timezone.utc)
    fleet = _fleet(a, start)
    state = ROOT / "data" / "sim_state.json"
    if state.exists() and not a.fresh:
        fleet.restore_sequences(json.loads(state.read_text()))
    client = GatewayClient(a.url, a.api_key)
    t0, last_report = time.monotonic(), time.monotonic()
    log.info("live: %s  speedup=x%s  nodes=%d", client.url, a.speedup, len(fleet.nodes))
    try:
        while True:
            elapsed = time.monotonic() - t0
            if a.duration_s and elapsed >= a.duration_s:
                break
            sim_now = start + timedelta(seconds=elapsed * a.speedup)
            for _, em in fleet.emit_due(sim_now):
                if a.scenario and em.kind != "malformed":
                    scenario.apply(em.wire)          # wire payload only; nothing evaluation-side is passed in
                client.send(em.wire)
            if time.monotonic() - last_report > 15:
                log.info("sim=%s stats=%s buffered=%d", sim_now.strftime("%Y-%m-%d %H:%MZ"),
                         dict(client.stats), len(client.buffer))
                last_report = time.monotonic()
            wait = (fleet.next_due() - sim_now).total_seconds() / a.speedup
            time.sleep(max(0.05, min(wait, 1.0)))
    except KeyboardInterrupt:
        pass
    finally:
        state.parent.mkdir(exist_ok=True)
        state.write_text(json.dumps(fleet.sequences()))
        log.info("final stats=%s buffered=%d", dict(client.stats), len(client.buffer))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m simulator", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="print sample payloads")
    _common(s)
    s.add_argument("--count", type=int, default=6)
    s.add_argument("--minutes", type=int, default=30, help="sim minutes to run while collecting")
    s.add_argument("--compact", action="store_true")
    s.set_defaults(fn=cmd_sample)

    d = sub.add_parser("dump", help="write N hours of payloads to a JSONL replay file")
    _common(d)
    d.add_argument("--hours", type=float, default=6)
    d.add_argument("--end", type=_parse_ts, default=None)
    d.add_argument("--out", default=None, help="default: data/replay.jsonl")
    d.add_argument("--with-ground-truth", action="store_true",
                   help="also write <out>.ground_truth.jsonl (generator's hidden source profile; evaluation only)")
    d.set_defaults(fn=cmd_dump)

    l = sub.add_parser("live", help="continuously POST to the backend")
    _common(l)
    l.add_argument("--url", default=os.getenv("AIRDNA_BACKEND_URL", "http://localhost:8000"))
    l.add_argument("--api-key", default=os.getenv("AIRDNA_API_KEY", "dev-key"))
    l.add_argument("--speedup", type=float, default=1.0, help="sim seconds per wall second (rate knob)")
    l.add_argument("--duration-s", type=float, default=0, help="0 = run until Ctrl-C")
    l.add_argument("--fresh", action="store_true", help="ignore saved sequence numbers")
    l.set_defaults(fn=cmd_live)

    a = ap.parse_args(argv)
    a.fn(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
