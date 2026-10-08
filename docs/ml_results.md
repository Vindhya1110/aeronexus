# Aeronex ML results (measured, held-out)

Everything below is scored on **held-out blocks** of a 12-month simulated dataset (`ml/splits.py`: repeating 40-day cycles with train / validation / test blocks and gaps of at least 3 days, so every season is in every role and no label window overlaps another role). Regenerate with `python -m ml.train_all` then `python tools/ml_report.py`.

**Read this first.** The data is simulated, so these numbers show that each method works end to end and how it compares with simple baselines; they are not accuracy claims about real Hyderabad air. Where a result is weak or negative it is said so.

## 2. Multi-horizon AQI forecast (gradient-boosted trees)

| horizon | model MAE | persistence | linear trend | climatology | skill vs best baseline | 80% interval coverage |
|---|---|---|---|---|---|---|
| 15min | 5.2 | 8.2 | 9.4 | 20.0 | +36.0% | 77% |
| 30min | 10.7 | 14.6 | 18.7 | 19.9 | +26.5% | 77% |
| 60min | 15.3 | 23.1 | 33.2 | 19.9 | +22.9% | 75% |
| 6h | 16.8 | 55.9 | 119.1 | 19.3 | +13.3% | 75% |
| 24h | 16.3 | 20.6 | 169.2 | 18.1 | +10.2% | 76% |
| 72h | 16.0 | 20.6 | 211.4 | 16.9 | +5.2% | 81% |

MAE in AQI points. Trees predict the residual to the better of persistence / same-clock-time climatology (chosen on validation blocks); intervals are split-conformal. Inputs: AQI history and lags, node temperature/humidity/pressure, calendar. No wind or traffic feed exists; the festival feature is wired but the simulator has no festival effect.

## 9. Early warning (probability of crossing a threshold within 3 h)

| threshold | ROC-AUC | Brier (base rate) | alert F1: model / persistence rule / trend rule | onset-only F1 (not yet over the line): model / persistence / trend | onset precision, recall |
|---|---|---|---|---|---|
| poor (> 200) | 0.966 | 0.060 (0.208) | 0.85 / 0.70 / 0.66 | 0.64 / 0.00 / 0.41 | 0.80, 0.54 |
| very poor (> 300) | 0.959 | 0.049 (0.122) | 0.75 / 0.55 / 0.54 | 0.58 / 0.00 / 0.37 | 0.69, 0.50 |

The alert cut-off is chosen on the validation blocks, never on test. 'Onset-only' counts only moments when the zone is still below the threshold, i.e. warnings given before the crossing.

## 4. Source attribution (trained classifier vs the rule set)

4-class accuracy: **classifier 98.9%** (macro-F1 0.98) vs **rules 42.9%** (macro-F1 0.29). 5-class (industrial separated): 98.6%.

| class | recall: classifier / rules | precision: classifier / rules | test rows |
|---|---|---|---|
| traffic | 98% / 48% | 100% / 90% | 26978 |
| dust | 98% / 0% | 88% / 100% | 2398 |
| smoke | 100% / 5% | 99% / 100% | 20150 |
| insufficient | 100% / 100% | 99% / 27% | 12682 |

**Caveat:** labels are the simulator's hidden truth. Ablations (see `docs/ml.md`) show ~98% accuracy even with PM only and no zone tags: the classifier mostly learns the simulator's time-of-day / zone / PM-level structure. Leave-one-zone-out accuracy is 78-99%. On real hardware the same code needs field-labelled episodes.

## 3. Hotspots (DBSCAN) and anomalies (Isolation Forest)

Hotspots: 4 persistent clusters; top-2 found ['IDAPASH', 'SANATH'] vs top-2 true ['IDAPASH', 'SANATH'].

| detector | smoke episodes detected | alert precision | false alerts / week |
|---|---|---|---|
| statistical (rolling median/MAD) | 48/67 (71.6%) | 18.3% | 43.4 |
| Isolation Forest | 57/67 (85.1%) | 42.2% | 16.5 |

Either detector: 89.6% of episodes. 'False' = not an open-burning episode (some are real jams/dust events).

## 1. Spatial interpolation

| method | leave-one-node-out MAE | leave-one-zone-out MAE |
|---|---|---|
| Gaussian process (kriging) | 30.1 | 33.9 |
| inverse-distance weighting | 27.1 | 31.2 |
| nearest node | 29.7 | n/a |
| city mean | 31.1 | 32.4 |
| **deployed 'auto' selector** | 26.7 | n/a |

GP 95% interval coverage: 75% (nominal 95%, so over-confident). **Honest reading:** on the simulated field the GP does not beat inverse-distance weighting or even the city mean by much: with 11 nodes in 6 separate zones and a field that is flat inside each zone there is little spatial structure to learn. The service therefore picks per snapshot whichever method has the lower leave-one-out error, and returns an uncertainty that grows away from sensors. Treat the heatmap as an estimate; real spatial correlation (denser nodes, bus tracks) is what would make kriging earn its keep.

## 7. Sensor-drift correction (co-location calibration)

66,375 bus/fixed-node co-location pairs. Drift was injected into two buses; two are controls.

| bus | injected drift / day | error before | error after | pairs |
|---|---|---|---|---|
| BUS_CHNAM_01 | +0.15% | 65.2% | 10.2% | 5655 |
| BUS_CHNAM_02 | +0.00% | 10.7% | 10.1% | 5590 |
| BUS_MGBSEC_01 | -0.10% | 28.0% | 10.1% | 3070 |
| BUS_PGHTC_01 | +0.00% | 10.4% | 10.2% | 2038 |

Mean relative disagreement with the nearest fixed node over the last 90 days; corrections are fitted daily from the previous 30 days only. Fixed nodes are low-cost sensors too: this gives network consistency, not absolute accuracy.

## 10. Trend and seasonality

Validation with a KNOWN injected decline (average -26.8% over 180 days): year-over-year comparison recovered **-22.9%** (95% CI -29.9% to -17.2%). Untouched control zones show slope 0.00 AQI/month. The smoothed STL trend reacted too slowly to the sharp drop (-5% seen vs -54% true), so use the year-over-year measure for interventions. With under 2 years of history the long-term trend is flagged not identifiable.

