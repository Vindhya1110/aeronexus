"""Scheduled worker:  python -m worker   (runs one pass immediately, then every AIRDNA_WORKER_INTERVAL_S)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from backend.db import make_engine, make_session_factory

from .config import WorkerSettings
from .runner import Worker

log = logging.getLogger("airdna.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = WorkerSettings.from_env()
    worker = Worker(make_session_factory(make_engine(cfg.backend.database_url)), cfg)

    def job():
        rows = worker.run_pass()
        log.info("pass done: %s", ", ".join(
            f"{r['zone']}={r.get('aqi')}" for r in rows))

    sched = BlockingScheduler(timezone="UTC")
    # max_instances=1 + coalesce: a slow pass never overlaps or piles up
    sched.add_job(job, "interval", seconds=cfg.interval_s, max_instances=1,
                  coalesce=True, id="analytics")
    job()
    log.info("worker scheduled every %ss (Ctrl-C to stop)", cfg.interval_s)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
