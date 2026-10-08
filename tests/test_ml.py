"""ML features: leakage guards, splits, helpers, and the API/worker wiring (fast: no training here)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml import calibration, features, interp, risk, splits, trends
from worker.aqi import compute_aqi


def synthetic_series(days=10, zone="HITEC", seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=days * 288, freq="5min", tz="UTC")
    hour = (idx.hour + idx.minute / 60).to_numpy()
    pm25 = 40 + 25 * np.sin(2 * np.pi * (hour - 6) / 24) + rng.normal(0, 2, len(idx))
    return pd.DataFrame({"zone": zone, "bucket": idx, "pm25": pm25, "pm10": pm25 * 1.7, "temp": 25.0, "hum": 55.0, "pres": 952.0})


def test_vectorised_aqi_matches_the_worker_formula():
    for pm25, pm10 in [(12, 30), (45, 80), (75, 160), (110, 300), (200, 380), (30.4, 60)]:
        v = float(features.aqi_vec([pm25], [pm10])[0])
        assert abs(v - compute_aqi(pm25, pm10)["aqi"]) <= 1.01
    assert np.isnan(features.aqi_vec([np.nan], [np.nan])[0])


def test_features_use_only_the_past():
    """Changing data AFTER the origin must not change the features at the origin (no look-ahead)."""
    s = synthetic_series()
    f1 = features.with_horizon(features.base_features(s), 60)
    origin = s["bucket"].iloc[1500]
    s2 = s.copy()
    s2.loc[s2["bucket"] > origin, "pm25"] *= 5
    s2.loc[s2["bucket"] > origin, "pm10"] *= 5
    f2 = features.with_horizon(features.base_features(s2), 60)
    a = f1[f1["bucket"] == origin][features.ALL_FEATURES].iloc[0]
    b = f2[f2["bucket"] == origin][features.ALL_FEATURES].iloc[0]
    pd.testing.assert_series_equal(a.astype(float), b.astype(float))


def test_blocked_splits_keep_a_three_day_gap_between_roles():
    """Between any two blocks of different roles (either order, across cycles) there are at least 3 unused days."""
    b = pd.Series(pd.date_range("2025-10-01", periods=365 * 24, freq="h", tz="UTC"))
    r = splits.role(b)
    assert {"train", "val", "test"} <= set(r)
    day = ((b - b.min()).dt.total_seconds() // 86400).astype(int).to_numpy()
    roles = r.to_numpy()
    for i in range(len(day) - 1):
        if roles[i] != roles[i + 1] and "gap" not in (roles[i], roles[i + 1]):
            pytest.fail(f"roles {roles[i]} and {roles[i + 1]} touch at day {day[i]}")
    # a non-gap block followed (after the gap) by a different role must be >= 3 days later
    last_role, last_day = None, None
    for rl, dy in zip(roles, day):
        if rl == "gap":
            continue
        if last_role is not None and rl != last_role:
            assert dy - last_day >= 4, (last_role, rl, last_day, dy)       # >= 3 whole empty days in between
        last_role, last_day = rl, dy


def test_interpolation_grid_and_uncertainty_growth():
    pts = pd.DataFrame({"lat": [17.36, 17.40, 17.44, 17.38, 17.42], "lon": [78.44, 78.47, 78.50, 78.40, 78.46], "aqi": [120, 180, 150, 110, 170]})
    for method in ("idw", "gp", "auto"):
        g = interp.predict_grid(pts, n=12, method=method)
        a, sd = np.array(g["aqi"]), np.array(g["std"])
        assert a.shape == (12, 12) and 0 <= a.min() and a.max() <= 500 and g["method_used"] in ("idw", "gp")
        assert sd[0, 0] >= sd.min()                                 # corners (far from nodes) are no more certain than the best cell
    assert interp.predict_grid(pts.head(2)) is None                 # not enough nodes -> refuses instead of inventing a map


def test_risk_ranking_combines_air_quality_and_exposure():
    exposure = {"A": {"sites": {"school": 10, "hospital": 5}, "buildings": 9000}, "B": {"sites": {"school": 0, "hospital": 0}, "buildings": 500}}
    rows = risk.rank([{"zone_id": "A", "name": "A", "aqi": 150, "p_very_poor": 0.1}, {"zone_id": "B", "name": "B", "aqi": 150, "p_very_poor": 0.1}], exposure)
    assert [r["zone_id"] for r in rows] == ["A", "B"] and rows[0]["risk"] > rows[1]["risk"]      # same air, more people exposed -> higher
    worse = risk.rank([{"zone_id": "A", "name": "A", "aqi": 100, "p_very_poor": 0}, {"zone_id": "B", "name": "B", "aqi": 400, "p_very_poor": 0.9}], exposure)
    assert worse[0]["zone_id"] == "B"                                                         # much worse air can outrank exposure
    none = risk.rank([{"zone_id": "A", "name": "A", "aqi": 150}], {})
    assert none[0]["exposure_index"] is None and "no exposure data" in none[0]["note"]       # says so, never invents


def test_calibration_recovers_a_known_gain_and_ignores_outliers():
    rng = np.random.default_rng(1)
    ref = rng.uniform(30, 120, 400)
    bus = ref / 1.25 + rng.normal(0, 1.5, 400)                      # sensor reads 20% low
    bus[:20] = 400                                                  # gross outliers (e.g. a passing diesel truck)
    pairs = pd.DataFrame({"bus_pm25": bus, "ref_pm25": ref})
    c = calibration.fit_correction(pairs)
    assert abs(c["gain"] - 1.25) < 0.05
    assert calibration.fit_correction(pairs.head(5)) is None        # too few pairs -> no correction


def test_year_over_year_impact_recovers_a_known_change():
    idx = pd.date_range("2025-01-01", periods=730, freq="D")
    base = pd.Series(80 + 20 * np.sin(2 * np.pi * idx.dayofyear / 365), index=idx)
    after = base.copy()
    after[after.index >= "2026-03-01"] *= 0.8                       # a real 20% cut after deployment
    r = trends.before_after(after, pd.Timestamp("2026-03-01"), window_days=120, n_boot=300)
    assert r["ci95_pct"][0] <= -0.2 <= r["ci95_pct"][1] + 0.02
    assert abs(r["change_pct"] + 0.2) < 0.02


def test_stl_trend_flagged_not_identifiable_under_two_years():
    idx = pd.date_range("2025-10-01", periods=365, freq="D")
    s = pd.Series(70 + 10 * np.sin(2 * np.pi * np.arange(365) / 365) + np.random.default_rng(0).normal(0, 2, 365), index=idx)
    t = trends.trend_stats(s)
    assert t["trend_identifiable"] is False and t["significant"] is False


# ---------------------------------------------------------------------------- wiring
def test_ml_runtime_is_failsafe_without_artifacts(tmp_path, monkeypatch):
    from worker.ml_runtime import MLRuntime
    import ml.forecast, ml.hotspots, ml.source, ml.warning
    for m, name in ((ml.forecast, "load"), (ml.warning, "load"), (ml.source, "load"), (ml.hotspots, "load_if")):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    rt = MLRuntime()
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.db import init_db
    eng = create_engine(f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    fac = sessionmaker(eng)
    init_db(eng, fac, True)
    with fac() as db:
        assert rt.run_zone(db, "HITEC", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc)) == {}


def test_insight_endpoints_degrade_gracefully_and_respect_roles(make_client):
    c = make_client()
    assert c.get("/v1/insights/early-warning").json()["status"] == "unavailable"
    assert c.get("/v1/insights/hotspots").json()["status"] == "unavailable"
    assert c.get("/v1/heatmap").json()["status"] == "unavailable"          # no data yet: says so
    assert c.get("/v1/insights/risk").json()["status"] == "unavailable"
    locked = make_client(require_auth=True)
    for path in ("/v1/insights/early-warning", "/v1/heatmap", "/v1/insights/trends"):
        assert locked.get(path).status_code == 401
    for path in ("/v1/insights/risk", "/v1/insights/hotspots", "/v1/insights/calibration"):
        assert locked.get(path).status_code == 401


def test_worker_still_never_reads_ground_truth():
    from pathlib import Path
    for f in (Path(__file__).resolve().parent.parent / "worker").glob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert "ground_truth" not in src and "dominant_source" not in src and "truth_" not in src, f.name
