"""Render docs/ml_results.md from ml/artifacts/*.metrics.json so the numbers in the docs are the measured ones.

    python tools/ml_report.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "ml" / "artifacts"
OUT = ROOT / "docs" / "ml_results.md"


def load(name):
    p = ART / name
    return json.loads(p.read_text()) if p.exists() else None


def pct(x, d=1):
    return "n/a" if x is None else f"{100 * x:.{d}f}%"


def main() -> None:
    L = ["# Aeronex ML results (measured, held-out)", "",
         "Everything below is scored on **held-out blocks** of a 12-month simulated dataset (`ml/splits.py`: repeating 40-day cycles "
         "with train / validation / test blocks and gaps of at least 3 days, so every season is in every role and no label window "
         "overlaps another role). Regenerate with `python -m ml.train_all` then `python tools/ml_report.py`.", "",
         "**Read this first.** The data is simulated, so these numbers show that each method works end to end and how it compares with "
         "simple baselines; they are not accuracy claims about real Hyderabad air. Where a result is weak or negative it is said so.", ""]
    f = load("forecast.metrics.json")
    if f:
        L += ["## 2. Multi-horizon AQI forecast (gradient-boosted trees)", "",
              "| horizon | model MAE | persistence | linear trend | climatology | skill vs best baseline | 80% interval coverage |", "|---|---|---|---|---|---|---|"]
        for h, r in f["report"].items():
            L.append(f"| {h} | {r['gbt']['mae']:.1f} | {r['persistence']['mae']:.1f} | {r['linear_trend']['mae']:.1f} | {r['climatology']['mae']:.1f} | "
                     f"{r['skill_vs_best_baseline']:+.1%} | {pct(r['gbt']['interval_80_coverage'], 0)} |")
        L += ["", "MAE in AQI points. Trees predict the residual to the better of persistence / same-clock-time climatology (chosen on validation blocks); "
              "intervals are split-conformal. Inputs: AQI history and lags, node temperature/humidity/pressure, calendar. No wind or traffic feed exists; "
              "the festival feature is wired but the simulator has no festival effect.", ""]
    w = load("warning.metrics.json")
    if w:
        L += ["## 9. Early warning (probability of crossing a threshold within 3 h)", "",
              "| threshold | ROC-AUC | Brier (base rate) | alert F1: model / persistence rule / trend rule | onset-only F1 (not yet over the line): model / persistence / trend | onset precision, recall |", "|---|---|---|---|---|---|"]
        for k, r in w["report"].items():
            o = r["onset_only"]
            L.append(f"| {k.replace('_', ' ')} (> {w['meta']['thresholds'][k] if 'meta' in w else w['thresholds'][k]}) | {r['roc_auc']:.3f} | {r['brier']:.3f} ({r['brier_base_rate']:.3f}) | "
                     f"{r['model']['f1']:.2f} / {r['persistence_rule']['f1']:.2f} / {r['trend_rule']['f1']:.2f} | "
                     f"{o['model']['f1']:.2f} / {o['persistence_rule']['f1']:.2f} / {o['trend_rule']['f1']:.2f} | {o['model']['precision']:.2f}, {o['model']['recall']:.2f} |")
        L += ["", "The alert cut-off is chosen on the validation blocks, never on test. 'Onset-only' counts only moments when the zone is still below the "
              "threshold, i.e. warnings given before the crossing.", ""]
    s = load("source.metrics.json")
    if s:
        r = s["report"]
        ml4, ru4 = r["ml_4class"], r["rules_4class"]
        L += ["## 4. Source attribution (trained classifier vs the rule set)", "",
              f"4-class accuracy: **classifier {pct(ml4['accuracy'])}** (macro-F1 {ml4['macro_f1']:.2f}) vs **rules {pct(ru4['accuracy'])}** (macro-F1 {ru4['macro_f1']:.2f}). "
              f"5-class (industrial separated): {pct(r['ml_5class']['accuracy'])}.", "",
              "| class | recall: classifier / rules | precision: classifier / rules | test rows |", "|---|---|---|---|"]
        for c in ml4["labels"]:
            L.append(f"| {c} | {pct(ml4['per_class'][c]['recall'], 0)} / {pct(ru4['per_class'][c]['recall'], 0)} | "
                     f"{pct(ml4['per_class'][c]['precision'], 0)} / {pct(ru4['per_class'][c]['precision'], 0)} | {ml4['per_class'][c]['support']} |")
        L += ["", "**Caveat:** labels are the simulator's hidden truth. Ablations (see `docs/ml.md`) show ~98% accuracy even with PM only and no zone tags: "
              "the classifier mostly learns the simulator's time-of-day / zone / PM-level structure. Leave-one-zone-out accuracy is 78-99%. "
              "On real hardware the same code needs field-labelled episodes.", ""]
    h = load("hotspots.metrics.json")
    if h:
        hs, an = h["hotspots"], h["anomaly"]
        L += ["## 3. Hotspots (DBSCAN) and anomalies (Isolation Forest)", "",
              f"Hotspots: {hs.get('clusters')} persistent clusters; top-2 found {hs.get('top2_found')} vs top-2 true {hs.get('top2_truth')}.", "",
              "| detector | smoke episodes detected | alert precision | false alerts / week |", "|---|---|---|---|"]
        for k, n in (("stat", "statistical (rolling median/MAD)"), ("if", "Isolation Forest")):
            a = an[k]
            L.append(f"| {n} | {a['episodes_detected']}/{a['episodes_total']} ({pct(a['episode_recall'])}) | {pct(a['alert_precision'])} | {a['false_alerts_per_week']:.1f} |")
        L += ["", f"Either detector: {pct(an['either']['episode_recall'])} of episodes. 'False' = not an open-burning episode (some are real jams/dust events).", ""]
    i = load("interp.metrics.json")
    if i:
        a, b = i["leave_one_device_out_mae"], i["leave_one_zone_out_mae"]
        L += ["## 1. Spatial interpolation", "",
              "| method | leave-one-node-out MAE | leave-one-zone-out MAE |", "|---|---|---|",
              f"| Gaussian process (kriging) | {a['gp']:.1f} | {b['gp']:.1f} |", f"| inverse-distance weighting | {a['idw']:.1f} | {b['idw']:.1f} |",
              f"| nearest node | {a['nearest']:.1f} | n/a |", f"| city mean | {a['city_mean']:.1f} | {b['city_mean']:.1f} |"]
        if i.get("auto_selector_mae"):
            L.append(f"| **deployed 'auto' selector** | {i['auto_selector_mae']:.1f} | n/a |")
        L += ["", f"GP 95% interval coverage: {pct(i['gp_95pct_interval_coverage'], 0)} (nominal 95%, so over-confident). **Honest reading:** on the simulated field the GP does not beat "
              "inverse-distance weighting or even the city mean by much: with 11 nodes in 6 separate zones and a field that is flat inside each zone there is little spatial "
              "structure to learn. The service therefore picks per snapshot whichever method has the lower leave-one-out error, and returns an uncertainty that grows "
              "away from sensors. Treat the heatmap as an estimate; real spatial correlation (denser nodes, bus tracks) is what would make kriging earn its keep.", ""]
    c = load("calibration.metrics.json")
    if c:
        L += ["## 7. Sensor-drift correction (co-location calibration)", "", f"{c['pairs_total']:,} bus/fixed-node co-location pairs. Drift was injected into two buses; two are controls.", "",
              "| bus | injected drift / day | error before | error after | pairs |", "|---|---|---|---|---|"]
        for b, v in c["per_bus_last_90_days"].items():
            L.append(f"| {b} | {v['injected_drift_per_day']:+.2%} | {pct(v['raw_mean_rel_error'])} | {pct(v['corrected_mean_rel_error'])} | {v['pairs']} |")
        L += ["", "Mean relative disagreement with the nearest fixed node over the last 90 days; corrections are fitted daily from the previous 30 days only. "
              "Fixed nodes are low-cost sensors too: this gives network consistency, not absolute accuracy.", ""]
    t = load("trends.metrics.json")
    if t:
        y = t["year_over_year_impact"]
        L += ["## 10. Trend and seasonality", "",
              f"Validation with a KNOWN injected decline (average {pct(y['true_average_change_pct'])} over 180 days): year-over-year comparison recovered **{pct(y['change_pct'])}** "
              f"(95% CI {pct(y['ci95_pct'][0])} to {pct(y['ci95_pct'][1])}). Untouched control zones show slope 0.00 AQI/month. "
              f"The smoothed STL trend reacted too slowly to the sharp drop ({pct(t['injected_decline']['trend_component_change_over_window_pct'], 0)} seen vs "
              f"{pct(t['injected_decline']['true_change_over_window_pct'], 0)} true), so use the year-over-year measure for interventions. With under 2 years of history the long-term "
              "trend is flagged not identifiable.", ""]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
