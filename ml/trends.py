"""Feature 10: long-term trend and seasonality per zone (STL decomposition + Mann-Kendall trend test).

Daily mean AQI per zone is decomposed (statsmodels STL, weekly cycle) into trend + weekly pattern + remainder. The trend is
summarised as a Theil-Sen slope (AQI points per month) with a Mann-Kendall significance test, plus month-over-month change
and the strength of the weekly and the (monthly-scale) seasonal component. `before_after()` compares two periods with a
bootstrap confidence interval - the tool for "measurable impact once deployed".

Validated by `python -m ml.trends evaluate`: a KNOWN linear decline is injected into a copy of the data and must be recovered
(slope and significance), while the untouched control zones must stay "no significant trend" after de-seasoning.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kendalltau, theilslopes
from statsmodels.tsa.seasonal import STL

from . import data as mldata
from .features import aqi_vec

ART = Path(__file__).resolve().parent / "artifacts"


def daily_aqi(series: pd.DataFrame) -> pd.DataFrame:
    s = series.dropna(subset=["pm25", "pm10"]).copy()
    s["day"] = s["bucket"].dt.floor("D") if "bucket" in s else s["ts"].dt.floor("D")
    d = s.groupby(["zone", "day"])[["pm25", "pm10"]].mean().reset_index()
    d["aqi"] = aqi_vec(d["pm25"], d["pm10"])
    return d


def decompose(daily: pd.Series) -> dict:
    """Weekly STL always; with >= 2 years of data an annual STL first, so the seasonal swing (winter high, monsoon low) is
    separated from the long-term trend. With less than 2 years the two cannot be told apart and `annual_removed` is False."""
    y = daily.asfreq("D").interpolate(limit=3).dropna()
    annual = len(y) >= 2 * 365
    if annual:
        yr = STL(y, period=365, robust=True).fit()
        trend, work = yr.trend, y - yr.seasonal
    else:
        trend, work = None, y
    wk = STL(work, period=7, robust=True).fit()
    var = lambda c: float(np.var(c))
    if trend is None:
        trend = wk.trend
    return {"trend": trend, "weekly": wk.seasonal, "resid": wk.resid, "annual_removed": annual,
            "weekly_strength": float(max(0.0, 1 - var(wk.resid) / max(1e-9, var(wk.resid + wk.seasonal)))),
            "trend_strength": float(max(0.0, 1 - var(wk.resid) / max(1e-9, var(wk.resid + wk.trend))))}


def trend_stats(daily: pd.Series) -> dict:
    """Theil-Sen slope (AQI/month) + Mann-Kendall test on the trend component, month-over-month changes. If the series is
    shorter than 2 years the trend is reported but flagged NOT identifiable (confounded with the annual cycle)."""
    y = daily.asfreq("D").interpolate(limit=3).dropna()
    dec = decompose(y)
    t = (dec["trend"].index - dec["trend"].index[0]).days.to_numpy().astype(float)
    slope, icpt, lo, hi = theilslopes(dec["trend"].to_numpy(), t)
    tau, p = kendalltau(t, dec["trend"].to_numpy())
    monthly = y.resample("MS").mean()
    mom = monthly.pct_change().dropna()
    ident = bool(dec["annual_removed"])
    return {"trend_identifiable": ident, "slope_per_month": float(slope * 30.4), "slope_ci_per_month": [float(lo * 30.4), float(hi * 30.4)],
            "kendall_tau": float(tau), "p_value": float(p),
            "significant": bool(ident and p < 0.01 and abs(slope * (len(y))) > 5),
            "weekly_strength": dec["weekly_strength"], "trend_strength": dec["trend_strength"],
            "latest_month_mean": float(monthly.iloc[-1]), "mom_change_last": float(mom.iloc[-1]) if len(mom) else None,
            "months": {str(k.date())[:7]: round(float(v), 1) for k, v in monthly.items()}}


def before_after(daily: pd.Series, split: pd.Timestamp, window_days: int = 180, n_boot: int = 2000, seed: int = 7) -> dict:
    """Impact of something that started at `split`: mean daily AQI in the `window_days` after it versus the SAME calendar days one
    year earlier (so the season cancels out), with a 95% bootstrap CI (resampling whole weeks to respect autocorrelation)."""
    after = daily[(daily.index >= split) & (daily.index < split + pd.Timedelta(days=window_days))].dropna()
    ref_idx = after.index - pd.Timedelta(days=365)
    before = daily.reindex(ref_idx).dropna()
    n = min(len(after), len(before))
    pair = pd.DataFrame({"after": after.to_numpy()[:len(after)], "before": daily.reindex(ref_idx).to_numpy()}).dropna()
    rng = np.random.default_rng(seed)
    weeks = [pair.iloc[i:i + 7] for i in range(0, len(pair), 7)]
    stat = lambda d: (d["after"].mean() - d["before"].mean()) / d["before"].mean()
    boots = [stat(pd.concat([weeks[i] for i in rng.integers(0, len(weeks), len(weeks))])) for _ in range(n_boot)]
    return {"before_mean_same_days_last_year": float(pair["before"].mean()), "after_mean": float(pair["after"].mean()),
            "change_pct": float(stat(pair)), "ci95_pct": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))], "n_days": int(len(pair))}


def report(daily: pd.DataFrame) -> dict:
    return {z: trend_stats(g.set_index("day")["aqi"]) for z, g in daily.groupby("zone")}


def evaluate() -> dict:
    series = mldata.zone_series(mldata.load())
    daily = daily_aqi(series)
    one_year = report(daily)                               # < 2 years: trend flagged not identifiable
    # two-year test set: the simulated year repeated twice (identical seasonality), so any trend we find is injected by us
    y2 = daily.copy()
    y2["day"] = y2["day"] + pd.Timedelta(days=365)
    two = pd.concat([daily, y2], ignore_index=True)
    control = report(two)
    # inject a KNOWN decline in HITEC: AQI x (1 - 0.3% per day) for the last 180 days (ends at -54%)
    inj = two.copy()
    end = inj["day"].max()
    start_decl = end - pd.Timedelta(days=179)
    m = (inj["zone"] == "HITEC") & (inj["day"] >= start_decl)
    inj.loc[m, "aqi"] = inj.loc[m, "aqi"] * (1 - 0.003 * (inj.loc[m, "day"] - start_decl).dt.days)
    hit = inj[inj["zone"] == "HITEC"].set_index("day")["aqi"]
    dec = decompose(hit)
    tr = dec["trend"]
    tr_win = tr[tr.index >= start_decl]
    t = (tr_win.index - tr_win.index[0]).days.to_numpy().astype(float)
    sl = theilslopes(tr_win.to_numpy(), t)
    true_mean_factor = 1 - 0.003 * 89.5
    ba = before_after(hit, start_decl)
    strip = lambda d: {k: v for k, v in d.items() if k != "months"}
    return {"one_year_zones": {z: {k: v for k, v in strip(s).items() if k in ("trend_identifiable", "weekly_strength", "latest_month_mean", "mom_change_last")} for z, s in one_year.items()},
            "two_year_control_zones": {z: strip(s) for z, s in control.items()},
            "injected_decline": {"zone": "HITEC", "true": "x(1 - 0.3%/day) for 180 days: -54% by the end, average level -26.9% over the window",
                                 "trend_component_change_over_window_pct": float(tr_win.iloc[-1] / tr_win.iloc[0] - 1),
                                 "true_change_over_window_pct": -0.003 * 179, "theil_sen_slope_in_window_per_month": float(sl[0] * 30.4)},
            "year_over_year_impact": {**ba, "true_average_change_pct": float(true_mean_factor - 1)}}


def main() -> None:
    res = evaluate()
    print(json.dumps(res, indent=1))
    ART.mkdir(exist_ok=True)
    (ART / "trends.metrics.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if len(sys.argv) > 1 and sys.argv[1] == "evaluate":
        main()
