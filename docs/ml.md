# Aeronex machine-learning features

Eight ML / analytics features sit on top of the rule-based pipeline. They are an **add-on**: every one is optional, loaded from
`ml/artifacts/`, and a missing model or an exception never affects AQI, events or the rule-based forecast (`worker/ml_runtime.py`).
Measured, held-out scores for each are in [ml_results.md](ml_results.md) (generated from `ml/artifacts/*.metrics.json`).

> **Training data is simulated.** A year of 5-minute readings from the simulator fleet (`ml/data.py`, 1.16 M samples, hidden ground truth
> kept in `truth_*` columns that only training/evaluation code under `ml/` reads; a test enforces that `worker/` never does). So the
> models prove the method and the pipeline and are compared with baselines; they are **not** validated on real Hyderabad air.
> Every dashboard panel says so where it matters.

| # | Question | Method | Where you see it |
|---|---|---|---|
| 1 | What is the AQI between sensors? | Gaussian-process regression (kriging) vs inverse-distance weighting; the service picks the lower leave-one-out error per snapshot, returns per-cell uncertainty | **City heatmap** screen, `GET /v1/heatmap` |
| 2 | What will AQI be later? | Gradient-boosted trees, 6 horizons (15/30/60 min, 6/24/72 h), residual to the best simple baseline, split-conformal 80% intervals | **Forecast** screen, `GET /v1/zones/{id}/forecast` (`method: gbt`) |
| 3 | Where are the recurring problem spots, and what is unusual right now? | DBSCAN over persistent high cells; Isolation Forest anomaly score alongside the statistical detector | **Risk & hotspots** screen; `anomaly_if` in the zone's source features |
| 4 | What is causing it? | Trained classifier (traffic / dust / smoke / industrial / background) next to the rule set | **Source insight** screen (`features.ml_attribution`) |
| 7 | Can I trust this sensor? | Rolling robust (Huber) co-location calibration of buses against fixed nodes | **Node health** screen, `GET /v1/insights/calibration` |
| 8 | Who should be prioritised? | Transparent score: (air now + outlook) x exposure of vulnerable groups from OpenStreetMap | **Risk & hotspots** screen, `GET /v1/insights/risk` |
| 9 | When should we warn? | Gradient-boosted classifier: P(AQI > 200) and P(AQI > 300) within 3 h; creates an `early_warning` event for the second | Overview cards, Forecast screen, Events, `GET /v1/insights/early-warning` |
| 10 | How is the city trending? | STL decomposition + Mann-Kendall / Theil-Sen; year-over-year before/after with bootstrap CI | **Trends** screen, `GET /v1/insights/trends` |

(Items 5 scenario simulation and 6 cleanest-route are not built.)

## Honest limitations (read before presenting)

* **Simulated data.** See above. High scores (especially source attribution) partly reflect how regular the simulator is.
* **Source attribution:** ablations on the held-out blocks - all features 98.8% (5-class), without zone tags 98.7%, without absolute MQ
  98.8%, **PM only (no gas, no tags) 97.8%**. The classifier mostly learns time-of-day / zone / PM-level structure. Leave-one-zone-out
  (4-class, no tags): CHNAM 97.9%, HITEC 89.9%, IDAPASH 97.9%, MGBSEC 98.9%, PGPT 96.1%, SANATH 77.8%. Real use needs field-labelled episodes.
* **Source classifier vs the scripted demo episode:** in the replay demo the HITEC smoke episode is a *scripted overlay* (`simulator/scenario.py`), not one of
  the generator's own episodes. The rule set labels it "probable smoke"; the trained classifier, which never saw this kind of event at HITEC, says
  "traffic". The dashboard flags rule/classifier disagreement. This is a real out-of-distribution weakness of a classifier trained on one generator
  (and a reason to keep the transparent rules alongside it), not something we tuned away.
* **Interpolation:** on the simulated field (flat inside each zone, 11 nodes in 6 separate zones) the GP does not beat IDW; 95% intervals
  from the GP cover ~75%. The heatmap is an *estimate* and says so.
* **Forecast:** no wind, no traffic feed, no festival effect in the simulator; 72 h skill is modest (it is mostly the daily pattern).
* **Early warning:** no push notifications; it raises dashboard events. Probabilities are not calibrated for real hardware.
* **Hotspots / trends / drift correction** need weeks-to-months of history. The demo database holds hours, so these panels show the analysis of
  the 12-month simulated dataset (`source = simulated-year-dataset` in the API) - labelled as such. With >= 30 days of real data the same
  functions run on the database.
* **Drift correction** was validated by *injecting* drift into a copy of the data (the simulator's sensors do not drift); the fixed nodes are
  low-cost units too, so the result is network consistency, not absolute accuracy.
* **Risk ranking** uses OpenStreetMap amenity counts and building counts as a stand-in for population; no census data. Not a trained model.
* **Trend:** under 2 years of data the long-term trend cannot be separated from the annual cycle and is flagged "not identifiable". The
  smoothed STL trend reacts slowly to a sharp change; use the year-over-year comparison for "impact after deployment".

## Reproduce

```powershell
python -m ml.data generate --days 365 --start 2025-10-01   # ~6 min -> data/ml/year.parquet
python -m ml.train_all                                      # ~40 min: trains + evaluates everything, writes ml/artifacts/
python -m ml.risk fetch                                     # OpenStreetMap exposure (needs internet; resumable)
python tools/ml_report.py                                   # docs/ml_results.md from the measured metrics
```

Individual steps: `python -m ml.forecast train`, `ml.warning train`, `ml.source train`, `ml.hotspots evaluate`, `ml.interp evaluate`,
`ml.calibration evaluate`, `ml.trends evaluate`, `ml.export`. Split design (`ml/splits.py`): repeating 40-day cycles - days 0-21 train, 26-28
validation, 33-36 test, gaps >= 3 days - so every season is in every role and no label window crosses a role boundary. Features use only
information available at the forecast origin (`tests/test_ml.py` checks that changing future data does not change today's features).

## How it runs live

`worker/ml_runtime.py` runs once per zone per `AIRDNA_ML_INTERVAL_MIN` (default 5 data-minutes): builds the 5-minute zone series from the
database (last 7 days), calls the same feature code used in training, writes forecast rows (`method gbt`), the early-warning insight/event and
attaches `ml_attribution` / `anomaly_if` to the zone's source features. Models load once from `ml/artifacts/`.
