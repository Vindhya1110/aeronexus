# simulator/ - synthetic data generator (fake gateway)

Drop-in stand-in for a real LoRa gateway: it POSTs the exact data-contract JSON
(`shared/schema.py`) to `POST /v1/telemetry` with an `X-API-Key` header. When real hardware
arrives, this is the only component that gets replaced.

```bash
pip install -r requirements.txt            # from the repo root

python -m simulator sample                 # print a few payloads (no network)
python -m simulator sample --start 2026-12-10T03:30:00Z --count 5 --compact

python -m simulator dump --hours 6         # JSONL replay file -> data/replay.jsonl
python -m simulator dump --start 2026-12-01T00:00:00Z --hours 72 --out data/winter.jsonl

python -m simulator live                   # POST continuously to http://localhost:8000
python -m simulator live --url http://localhost:8000 --api-key dev-key --speedup 60
```

`live` options: `--speedup N` (sim seconds per wall second; the rate knob - x1 is real time),
`--fixed-interval-s` (default 60), `--bus-interval-s` (default 30), `--duration-s`,
`--start` (sim start; default now), `--fresh` (ignore saved sequence numbers).
Env: `AIRDNA_BACKEND_URL`, `AIRDNA_API_KEY` (default `dev-key`).
Sequence numbers are saved to `data/sim_state.json` so restarts don't resend old sequences.

The gateway client behaves like a store-and-forward gateway: 2xx/409 = delivered, other 4xx =
rejected (not retried), 5xx / connection errors = buffered and retried in order.

## What it simulates

* **Fleet** (`fleet.py`): 7 fixed nodes across the 6 zones + 4 buses on 3 routes. IDs: `docs/device_ids.md`.
* **Bus routes** (`routes.py`): road-following polylines between real landmarks, fetched once from
  OpenStreetMap with osmnx and cached in `route_cache/`. They are representative corridors, *not*
  official TSRTC route numbers. Buses cruise 15-25 km/h with slowdowns, stop dwells and terminal turnarounds.
* **PM** (`signals.py`, `profiles.py`): diurnal rush-hour peaks (IST), Nov-Jan x1.4-1.6 seasonal
  elevation, monsoon dip, per-zone source profiles, episodic smoke / industrial puffs / jams / dust bursts.
* **Gas proxies**: raw 12-bit ADC counts, *proxy indices only*.

  | source | PM signature | MQ signature |
  |---|---|---|
  | traffic | PM2.5+PM10 up in rush hours | MQ-9 up |
  | industrial | steady PM2.5, puffs | MQ-135 and MQ-2 up |
  | construction | PM10 up, PM2.5 barely | MQ proxies near baseline |
  | smoke episode | sharp PM2.5 spike | MQ-2 and MQ-135 up sharply |
* **Faults** (`faults.py`, disable with `--no-faults`): no GPS fix, poor HDOP, sensor dropout (null + flag bit),
  node reboot / warm-up, duplicate sequence resend, and malformed payloads (a required key removed -> backend must 422).

The generator's source profiles are hidden "ground truth"; the worker must infer signatures from telemetry only.

`dump --with-ground-truth` also writes `<out>.ground_truth.jsonl`: per record `device_id, sequence, timestamp_utc,
zone_id, dominant_source, dominant_strength` and the injected PM components. Evaluation only (`tools/evaluate_source_hypothesis.py`);
it is never part of the wire payload and `live` mode cannot emit it.

`python -m simulator.record_fixtures` regenerates `shared/fixtures/*.json`.
