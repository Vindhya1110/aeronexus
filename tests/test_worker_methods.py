"""Unit tests for the worker's pure methods (no DB, synthetic inputs)."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from worker.anomaly import evaluate
from worker.aqi import compute_aqi, sub_index, PM10_BREAKPOINTS, PM25_BREAKPOINTS
from worker.config import WorkerSettings
from worker.forecast import make_forecasts
from worker.source import DUST, INSUFFICIENT, SMOKE, TRAFFIC, hypothesize

CFG = WorkerSettings()
T0 = datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc)      # 17:30 IST (rush hour)


# ---------------------------------------------------------------- AQI
@pytest.mark.parametrize("pm25,expected", [(0, 0), (30, 50), (60, 100), (90, 200), (120, 300), (250, 400),
                                           (400, 460), (500, 500), (900, 500)])
def test_pm25_breakpoints(pm25, expected):
    assert sub_index(pm25, PM25_BREAKPOINTS) == expected


@pytest.mark.parametrize("pm25,expected", [(31, 51), (45, 75), (75, 149), (105, 249), (180, 346)])
def test_pm25_interior_values_hand_computed(pm25, expected):
    # CPCB formula by hand, e.g. 45: 51 + (45-31)/(60-31)*(100-51) = 74.66 -> 75
    assert sub_index(pm25, PM25_BREAKPOINTS) == expected


@pytest.mark.parametrize("pm10,expected", [(60, 60), (180, 153), (300, 250)])
def test_pm10_interior_values_hand_computed(pm10, expected):
    assert sub_index(pm10, PM10_BREAKPOINTS) == expected


def test_pm10_breakpoints_and_overall_is_max():
    assert sub_index(250, PM10_BREAKPOINTS) == 200
    r = compute_aqi(pm25=45.0, pm10=260.0)
    assert r["detail"]["dominant_pollutant"] == "pm10" and r["aqi"] == r["detail"]["sub_index"]["pm10"]
    assert r["category"] == "Poor"


def test_aqi_none_without_pm_and_no_mq_input():
    assert compute_aqi(None, None)["aqi"] is None
    import inspect
    assert "mq" not in inspect.signature(compute_aqi).parameters       # AQI cannot see gas proxies


# ---------------------------------------------------------------- anomaly
def make_series(values, end=T0, step=5):
    idx = [end - timedelta(minutes=step * (len(values) - 1 - i)) for i in range(len(values))]
    return pd.DataFrame({"pm25": values, "pm10": [v * 1.7 for v in values], "n": 5},
                        index=pd.DatetimeIndex(idx, tz="UTC"))


def test_anomaly_breach_on_spike_and_not_on_flat():
    rng = np.random.default_rng(0)
    flat = list(60 + rng.normal(0, 2, 36))
    assert not evaluate(make_series(flat), T0, CFG)["pm25"]["breach"]
    spike = flat[:-2] + [130.0, 135.0]
    ev = evaluate(make_series(spike), T0, CFG)["pm25"]
    assert ev["breach"] and ev["severity"] in ("high", "critical") and ev["baseline_method"].startswith("rolling")


def test_anomaly_needs_history():
    assert evaluate(make_series([60.0] * 6), T0, CFG) == {}


def test_time_of_day_baseline_preferred_when_available():
    flat = [60.0] * 36
    tod = {"pm25": (40.0, 3.0, 5), "pm10": (68.0, 5.0, 5)}
    ev = evaluate(make_series(flat), T0, CFG, tod)["pm25"]
    assert ev["baseline"] == 40.0 and ev["baseline_method"].startswith("time_of_day") and ev["breach"]


# ---------------------------------------------------------------- source hypothesis
class FakeBaselines:
    BASE = {"mq2_adc": 700.0, "mq9_adc": 900.0, "mq135_adc": 1000.0}

    def get(self, db, device_id, as_of):
        return dict(self.BASE)


def frame(pm25, pm10, mq2=700, mq9=900, mq135=1000, n=30, devices=2, warmup=False):
    rows = []
    for i in range(n):
        rows.append({"timestamp_utc": T0 - timedelta(seconds=30 * i), "device_id": f"D{i % devices}",
                     "pm25_ugm3": pm25, "pm10_ugm3": pm10, "mq2_adc": mq2, "mq9_adc": mq9, "mq135_adc": mq135,
                     "warmup": warmup, "sensor_flags": 0, "pm_ok": not warmup,
                     "mq2_adc_ok": not warmup, "mq9_adc_ok": not warmup, "mq135_adc_ok": not warmup})
    df = pd.DataFrame(rows)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    return df


def hyp(df, tags=(), pm_base=None):
    return hypothesize(None, df, T0, tags, pm_base, FakeBaselines(), CFG)


def test_dust_signature():
    r = hyp(frame(60, 190, mq2=705, mq9=905, mq135=1005), tags=("construction",), pm_base={"pm25": 55, "pm10": 95})
    assert r["label"] == DUST and 0 < r["confidence"] <= 0.9


def test_traffic_signature_mq9_leads():
    r = hyp(frame(95, 160, mq2=760, mq9=1500, mq135=1120), tags=("traffic",), pm_base={"pm25": 55, "pm10": 95})
    assert r["label"] == TRAFFIC
    assert r["features"]["mq_rise_over_own_baseline"]["mq9_adc"] > r["features"]["mq_rise_over_own_baseline"]["mq2_adc"]


def test_smoke_signature_mq2_mq135_lead():
    r = hyp(frame(120, 170, mq2=1300, mq9=1000, mq135=1500), tags=("industrial",), pm_base={"pm25": 55, "pm10": 95})
    assert r["label"] == SMOKE


def test_clean_air_is_insufficient_evidence_with_confidence():
    r = hyp(frame(25, 42), pm_base={"pm25": 25, "pm10": 42})
    assert r["label"] == INSUFFICIENT and r["confidence"] is not None and r["confidence"] > 0


def test_pm_up_but_mq_ambiguous_is_insufficient():
    r = hyp(frame(90, 150, mq2=800, mq9=950, mq135=1050))
    assert r["label"] == INSUFFICIENT


def test_too_few_samples_is_insufficient_and_says_why():
    r = hyp(frame(95, 160, mq9=1500, n=2))
    assert r["label"] == INSUFFICIENT and "usable samples" in r["features"]["reason"]


def test_warmup_samples_are_not_evidence():
    r = hyp(frame(95, 160, mq9=1500, warmup=True))
    assert r["label"] == INSUFFICIENT


def test_every_output_has_label_and_confidence_and_probable_wording():
    for df in (frame(60, 190), frame(95, 160, mq9=1500), frame(25, 42), frame(95, 160, n=1)):
        r = hyp(df)
        assert isinstance(r["label"], str) and isinstance(r["confidence"], float)
        assert r["label"] == INSUFFICIENT or r["label"].startswith("probable ")


def test_prior_cannot_create_evidence():
    r = hyp(frame(25, 42), tags=("traffic", "industrial", "construction"))
    assert r["label"] == INSUFFICIENT


# ---------------------------------------------------------------- forecast
def test_trend_extrapolates_ramp_and_persistence_stays_flat():
    vals = [40 + 2.0 * i for i in range(36)]                  # +2 ug/m3 per 5 min
    s = make_series(vals)
    rows = make_forecasts(s, {"pm25": vals[-1], "pm10": vals[-1] * 1.7}, T0, CFG)
    by = {(r["method"], r["horizon_min"]): r for r in rows}
    assert len(rows) == 6
    assert by[("persistence", 30)]["pm25_pred"] == pytest.approx(vals[-1], abs=0.1)
    # slope 0.4/min: +30 min ~ +12 over the latest bucket centre
    assert by[("linear_trend", 30)]["pm25_pred"] > by[("persistence", 30)]["pm25_pred"] + 8
    assert by[("linear_trend", 15)]["pm25_pred"] < by[("linear_trend", 60)]["pm25_pred"]
    assert by[("linear_trend", 30)]["model_version"] != by[("persistence", 30)]["model_version"]
    assert by[("linear_trend", 30)]["basis"]["r2"] > 0.99


def test_forecast_confidence_null_without_backtest_history():
    rows = make_forecasts(make_series([50.0] * 8), {"pm25": 50.0, "pm10": 85.0}, T0, CFG)
    assert all(r["confidence"] is None for r in rows)


def test_forecast_never_negative_and_empty_without_current():
    vals = [100 - 3.0 * i for i in range(36)]
    rows = make_forecasts(make_series(vals), {"pm25": 5.0, "pm10": 8.0}, T0, CFG)
    assert all(r["pm25_pred"] >= 0 for r in rows if r["pm25_pred"] is not None)
    assert make_forecasts(make_series(vals), {"pm25": None, "pm10": None}, T0, CFG) == []
