# worker/ - analytics (Phase-1 methods only: rules, rolling statistics, linear regression)

Reads `measurements`, writes `derived_measurements`, `events`, `forecasts`. Never touches raw.
The backend then serves the results through `GET /v1/zones`, `/v1/zones/{id}/forecast`, `/v1/events`
(no backend logic changes needed per result; only wiring added: `aqi.detail`, `GET /v1/zones/{id}/derived`).

```bash
python -m worker                 # scheduler: one pass now, then every AIRDNA_WORKER_INTERVAL_S (default 60)
python -m worker.run_once        # ONE synchronous live pass, prints a per-zone summary
python -m worker.run_once --backfill [--from ISO --to ISO --step-min 5]   # replay history, pass by pass
```
Uses the same `AIRDNA_DATABASE_URL` / `AIRDNA_OFFLINE_AFTER_S` / `AIRDNA_CURRENT_WINDOW_MIN` as the backend.
For a quick demo on replayed data:
`python tools/load_replay.py data/replay.jsonl && python -m worker.run_once --backfill`.

## What each module does

| module | output | method |
|---|---|---|
| `aqi.py` | `aqi`, `aqi_category`, `aqi_detail` | CPCB breakpoints (bands exactly as published, e.g. PM2.5 31-60 <-> AQI 51-100) on **raw PM2.5 / PM10 only**; overall = max sub-index. Input is the rolling mean (30 min of 5-min zone means) stored as `pm25_smoothed` / `pm10_smoothed`. Labelled *indicative*: the official AQI uses 24-h averages and >= 3 pollutants |
| `anomaly.py`, `lifecycle.py` | `events` rows (`event_type=anomaly`) | current-window PM vs time-of-day baseline (same IST clock window, previous <= 7 days, needs >= 2 days) else rolling median/MAD of the last 3 h. Breach = ratio >= 1.5 AND rise >= 15 (PM2.5) / 30 (PM10) ug/m3 AND robust z >= 3. Lifecycle: detected -> confirmed (breach persists 15 min) -> recommended (action text attached) -> rechecked (clear 10 min) -> closed. One open event per zone+metric |
| `source.py` | `source_label`, `source_confidence`, `source_features` | transparent rules, see `docs/source_hypothesis.md`. Always label + confidence, "probable" wording, MQ treated as raw proxy indices |
| `forecast.py` | `forecasts` rows: 15/30/60 min x {persistence, linear trend} | persistence = current-window mean; trend = OLS line over the last 45 min. `confidence` = 1 - MAPE of that method on the zone's own recent history (null if too little history). `model_version`: `persistence-1.0` / `linear-ols-1.0` |
| `coverage.py` | `coverage` JSON | last-sample age, online/offline device counts, blind-spot flag, flagged-sample counts. Reuses `backend.routers.devices.is_online` and `backend.stats.zone_devices` (no second definition of "online") |
| `runner.py` | one pass | per zone: load 3 h of samples -> bucket -> AQI -> anomaly -> source -> (events, forecasts if this window is new) -> append one derived row |

## Design decisions to know

* **Windowing is shared, not duplicated.** The persistence "current" value and the API's `current` block use the
  same `backend.stats.window_stats` and the same `AIRDNA_CURRENT_WINDOW_MIN`. The worker's smoothed PM uses its own,
  longer, explicitly named window (30 min) because smoothing is a different quantity.
* **Which samples count.** For PM-derived values: not warm-up, no sensor-fault bit, present, plausible. Per MQ channel:
  not warm-up, that channel's fault bit clear. Nothing is imputed.
* **Data time, not wall time.** Lifecycle durations and windows use the samples' timestamps, so live runs, replays and
  backfills behave the same, and re-running a pass on unchanged data does not duplicate events/forecasts.
  `derived_measurements` appends one row per zone per pass (history of states + fresh coverage); the API shows the latest.
* **Zone with no data**: a coverage-only derived row (`blind_spot: true`); AQI and hypothesis stay null.
* **Backfill coverage** has `mode: "backfill"` and null online/offline counts (device liveness is a server-clock notion).

## Known limitations (measured, see below)

* A **steady** source (e.g. an industrial estate emitting all day) is part of each device's own 24-h quiet baseline, so
  "rise over own baseline" cannot see it; the rules then abstain.
* Mixed-source periods (dust during rush hour) are usually answered "insufficient evidence", not guessed.
* MQ baselines are per device (10th percentile of its last 24 h) and need ~30 samples; no temperature compensation.

## Evaluation against simulator ground truth

```bash
python -m simulator dump --start 2026-12-10T00:00:00Z --hours 48 --with-ground-truth   # data/replay{,.ground_truth}.jsonl
python tools/load_replay.py data/replay.jsonl
python -m worker.run_once --backfill
uvicorn backend.main:app --port 8001
python tools/evaluate_source_hypothesis.py --ground-truth data/replay.ground_truth.jsonl --url http://localhost:8001 [--min-strength 0.8]
```
Reference run (seed 42, 48 h winter data, 3,384 worker outputs, window matching): overall 50.4% (61.7% on clear
events, `--min-strength 0.8`). Traffic recall 68% / precision 90%; dust recall 0%; smoke+industrial recall 4%;
"insufficient evidence" on a real source 59%, wrong source only 4%. Confidence does not separate right from wrong yet
(0.83 vs 0.84): treat it as ordinal evidence strength, not a calibrated probability.
The ground-truth file is never sent to `POST /v1/telemetry` and nothing under `worker/` reads it (a test enforces this).
