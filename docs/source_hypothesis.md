# Source-hypothesis heuristic (worker/source.py)

A transparent, rule-based **hypothesis** about what is probably dominating PM in a zone right now.
It is not a lab measurement. Output is always `{label, confidence, features}`.

## Inputs (evidence window: last 15 min of usable samples)

| feature | how |
|---|---|
| PM10/PM2.5 ratio | ratio of window means |
| MQ-2 / MQ-9 / MQ-135 rise | `window mean / device's own quiet baseline - 1`, averaged over devices. Baseline = 10th percentile of that device's last 24 h. These are raw ADC **proxy indices**, never gas concentrations |
| PM elevation | soft score from absolute level and ratio to the zone's PM baseline (from the anomaly module) |
| time of day | rush hour = 07-11 and 17-21 IST |
| zone prior | public `source_tags` from `shared/zones.py` (traffic, industrial, construction, dense_market): x1.1 if the tags support a label, x0.9 if not. Cannot create evidence |

`ramp(x, lo, hi)` = linear 0..1. Scores:

| label | score |
|---|---|
| probable traffic/combustion signature | `pm_elev25 * ramp(d9, .08, .35) * ramp(d9 / max(d2, d135), 1, 2) * (0.75 + 0.25*[rush hour or traffic tag])` |
| probable dust-dominant signature | `ramp(PM10/PM2.5, 1.9, 2.8) * pm_elev10 * (1 - ramp(max(d2, d9, d135), .05, .20))` |
| probable smoke/combustion signature | `pm_elev25 * ramp(d2, .10, .40) * ramp(d135, .08, .30) * ramp(mean(d2, d135) / d9, 1, 1.8)` |

(`d2, d9, d135` = fractional rise of MQ-2, MQ-9, MQ-135 over baseline.)

## Decision

* Best score >= 0.35 and ahead of the runner-up by >= 0.10 -> that label, `confidence = min(0.9, score * quality)`,
  where quality shrinks with fewer than 20 samples or when no gas baseline exists.
* Otherwise `insufficient evidence`, whose confidence means "confidence that no rule fits" = `1 - best score` (0.1-0.9).
* Fewer than 3 usable samples, or no gas baseline for the gas-dependent rules, can only lower the result.
* `features` in the API response lists every number above, all three scores, the runner-up and the rule text.

Industrial emissions have no label of their own (the architecture doc lists three signatures); they surface as
smoke/combustion when MQ-2/MQ-135 rise together.

## What `confidence` means (it depends on the label)

| outcome | formula | reading |
|---|---|---|
| a named label | `min(0.9, best_score x quality)` | evidence strength for that label |
| `insufficient evidence`, rules ran | `min(0.9, max(0.1, 1 - best_score))` | **confidence that no rule fits**. 0.9 = every rule scored near zero, i.e. a confident abstention. It is *not* the positive-label scale reused by mistake, and is not comparable with it |
| `insufficient evidence`, < 3 usable samples | fixed 0.1 | a placeholder floor, not a measurement: with no data we claim nothing either way |

Both are ordinal, uncalibrated scores. The API now states which one applies in `features.confidence_meaning`, and the
dashboard captions an abstention as "confidence that no rule fits". Caveat: a high abstention value also appears when a
real source is present but invisible to the rules (see below), so read it as "the rules see nothing", not "nothing is there".

## Industrial zones (IDAPASH, SANATH): deliberate limitation, not fixed

There is no industrial label; industrial emissions only surface as smoke/combustion when MQ-2 and MQ-135 rise together
over the device's own baseline. A steady industrial source never rises over its own 24 h quiet baseline, so those
zones mostly abstain (demo data: IDAPASH 87/87 passes "insufficient evidence"; SANATH 53 of 87, the rest traffic).

Decision: **not adding an industrial rule now.** The only signal that would separate it is an *absolute* elevated
MQ-135 / MQ-2 level (in the simulator IDAPASH averages ~1580 vs ~1050 elsewhere; SANATH ~1390). That works in synthetic
data, but real MQ sensors are uncalibrated with large unit-to-unit and temperature/humidity drift, so an absolute threshold
could not be defended on hardware, and tuning one to the simulator would just be fitting our own generator. Proper fix,
for after hardware arrives: per-device calibration against a reference monitor (the `calibrations` table already exists),
then a steady-source rule (sustained elevated PM2.5 + calibrated MQ-135/MQ-2 + low short-term variance + zone tag).
State this openly in the Round 2 report: *"steady industrial sources are not attributed by the prototype; zones tagged
industrial report `insufficient evidence` unless a combustion episode occurs."*

## Honest limits

See `worker/README.md`: steady sources are invisible to a baseline-relative rule, and mixed periods abstain.
Confidence is evidence strength, not a calibrated probability.
