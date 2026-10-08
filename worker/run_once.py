"""Run the analytics once, synchronously.

  python -m worker.run_once                      # one live pass (each zone at its newest sample)
  python -m worker.run_once --backfill           # replay history: passes every --step-min over all stored data
  python -m worker.run_once --backfill --from 2026-12-10T06:00:00Z --to 2026-12-11T06:00:00Z --step-min 5
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from backend.db import make_engine, make_session_factory
from backend.models import Measurement

from .config import WorkerSettings
from .runner import Worker


def parse_ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def print_summary(rows: list[dict]) -> None:
    for r in rows:
        if r.get("status") == "no data":
            print(f"{r['zone']:8} no data")
            continue
        print(f"{r['zone']:8} {r['window_end']:%Y-%m-%d %H:%MZ}  AQI {r['aqi']} ({r['category']}) "
              f"PM2.5~{r['pm25_smoothed']}  source: {r['source']} ({r['confidence']})  "
              f"events={r['events_touched']} forecasts={r['forecast_rows']}"
              f"{'  BLIND SPOT' if r['blind_spot'] else ''}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--from", dest="start", type=parse_ts)
    ap.add_argument("--to", dest="end", type=parse_ts)
    ap.add_argument("--step-min", type=int, default=5)
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = WorkerSettings.from_env()
    engine = make_engine(cfg.backend.database_url)
    factory = make_session_factory(engine)
    worker = Worker(factory, cfg)

    if not a.backfill:
        print_summary(worker.run_pass())
        return
    with factory() as db:
        lo, hi = db.execute(select(func.min(Measurement.timestamp_utc), func.max(Measurement.timestamp_utc))).one()
    if lo is None:
        print("no measurements stored yet")
        return
    lo, hi = lo.replace(tzinfo=timezone.utc), hi.replace(tzinfo=timezone.utc)
    t = a.start or (lo + timedelta(hours=1))             # need ~1 h of history before the first judgement
    end = a.end or hi
    n = 0
    last = []
    while t <= end:
        last = worker.run_pass(as_of=t)
        n += 1
        if n % 24 == 0:
            logging.info("backfill at %s (%d passes)", t.strftime("%Y-%m-%d %H:%MZ"), n)
        t += timedelta(minutes=a.step_min)
    print(f"backfilled {n} passes; last pass:")
    print_summary(last)


if __name__ == "__main__":
    main()
