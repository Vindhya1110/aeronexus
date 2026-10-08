"""Worker against a real (temp) database fed by the simulator through POST /v1/telemetry."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.models import DerivedMeasurement, Event, Forecast, RawMeasurement, Zone
from simulator.faults import FaultConfig
from simulator.fleet import Fleet
from worker.config import WorkerSettings
from worker.lifecycle import update_events
from worker.runner import Worker

START = datetime(2026, 12, 10, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def fed(client, auth):
    fleet = Fleet(5, START, FaultConfig.none(), allow_route_download=False)
    for em in fleet.run_until(START + timedelta(minutes=100)):
        assert client.post("/v1/telemetry", json=em.wire, headers=auth).status_code == 202
    return client


def worker_for(client):
    cfg = WorkerSettings(backend=client.app.state.settings)
    return Worker(client.app.state.session_factory, cfg)


def test_zones_endpoint_fields_filled_after_one_pass(fed):
    before = {z["zone_id"]: z for z in fed.get("/v1/zones").json()["zones"]}
    assert all(z["aqi"] is None and z["source_hypothesis"] is None and z["coverage"] is None for z in before.values())

    worker_for(fed).run_pass()
    zones = {z["zone_id"]: z for z in fed.get("/v1/zones").json()["zones"]}
    for zid, z in zones.items():
        assert z["aqi"]["value"] is not None and z["aqi"]["category"]
        assert z["aqi"]["detail"]["standard"].startswith("CPCB") and "indicative" in z["aqi"]["detail"]["caveat"]
        sh = z["source_hypothesis"]
        assert isinstance(sh["confidence"], float) and sh["label"] and "heuristic" in sh["note"]
        assert sh["label"] == "insufficient evidence" or sh["label"].startswith("probable ")
        assert z["coverage"]["devices"]["total"] >= 1 and z["coverage"]["last_sample_age_s"] is not None
        assert "blind_spot" in z["coverage"] and z["coverage"]["mode"] == "live"


def test_aqi_uses_raw_pm_only(fed):
    worker_for(fed).run_pass()
    z = next(x for x in fed.get("/v1/zones").json()["zones"] if x["zone_id"] == "SANATH")
    assert set(z["aqi"]["detail"]["inputs_ugm3"]) == {"pm25", "pm10"}
    assert z["aqi"]["pm25_smoothed"] is not None and z["current"]["pm25_ugm3"] is not None


def test_worker_never_modifies_raw(fed):
    with fed.app.state.session_factory() as db:
        snap = [(r.id, json.dumps(r.payload, sort_keys=True)) for r in db.scalars(select(RawMeasurement))]
    worker_for(fed).run_pass()
    with fed.app.state.session_factory() as db:
        after = [(r.id, json.dumps(r.payload, sort_keys=True)) for r in db.scalars(select(RawMeasurement))]
    assert snap == after


def test_history_has_distinct_raw_and_smoothed_series(fed):
    w = worker_for(fed)
    for m in (60, 70, 80, 90, 100):                       # several passes so derived history exists
        w.run_pass(as_of=START + timedelta(minutes=m))
    h = fed.get("/v1/zones/SANATH/history", params={"fields": "pm25_ugm3,pm10_ugm3", "bucket_s": 300}).json()
    pts = [p for p in h["points"] if p["smoothed"]["pm25_ugm3"] is not None]
    assert pts, "no smoothed values returned"
    assert any(p["smoothed"]["pm25_ugm3"] != p["values"]["pm25_ugm3"] for p in pts)     # not a copy of raw
    assert all(set(p["values"]) == {"pm25_ugm3", "pm10_ugm3"} for p in h["points"])      # raw block untouched
    off = fed.get("/v1/zones/SANATH/history", params={"include_smoothed": "false"}).json()
    assert "smoothed" not in off["points"][0]


def test_forecast_endpoint_has_both_methods_and_versions(fed):
    worker_for(fed).run_pass()
    f = fed.get("/v1/zones/HITEC/forecast").json()
    base = [r for r in f["forecasts"] if r["method"] != "gbt"]            # rule/statistical baselines always exist
    assert f["status"] == "ok" and len(base) == 6
    assert {r["method"] for r in base} == {"persistence", "linear_trend"}
    assert {r["model_version"] for r in base} == {"persistence-1.0", "linear-ols-1.0"}
    assert {r["horizon_min"] for r in base} == {15, 30, 60}
    gbt = [r for r in f["forecasts"] if r["method"] == "gbt"]             # learned model: present only if artifacts are installed
    assert len(gbt) in (0, 6)
    assert all(r["aqi_p10"] <= r["aqi_pred"] <= r["aqi_p90"] for r in gbt)


def test_rerun_on_unchanged_data_is_idempotent(fed):
    w = worker_for(fed)
    w.run_pass()
    with fed.app.state.session_factory() as db:
        n_fc, n_ev = db.scalar(select(func.count(Forecast.id))), db.scalar(select(func.count(Event.id)))
    w.run_pass()
    with fed.app.state.session_factory() as db:
        assert db.scalar(select(func.count(Forecast.id))) == n_fc
        assert db.scalar(select(func.count(Event.id))) == n_ev
        assert db.scalar(select(func.count(DerivedMeasurement.id))) >= 12      # coverage snapshot rows do append


def test_zone_without_data_gets_coverage_only(client):
    Worker(client.app.state.session_factory, WorkerSettings(backend=client.app.state.settings)).run_pass()
    z = client.get("/v1/zones").json()["zones"][0]
    assert z["aqi"] is None and z["source_hypothesis"] is None            # nothing invented
    assert z["coverage"]["blind_spot"] is True and z["coverage"]["last_sample_utc"] is None


def test_backfill_mode_marks_coverage_and_respects_as_of(fed):
    w = worker_for(fed)
    rows = w.run_pass(as_of=START + timedelta(minutes=90))
    assert all(r.get("window_end") is None or r["window_end"] <= START + timedelta(minutes=90) for r in rows)
    z = next(x for x in fed.get("/v1/zones").json()["zones"] if x["zone_id"] == "HITEC")
    assert z["coverage"]["mode"] == "backfill" and z["coverage"]["devices"]["online"] is None


# ---------------------------------------------------------------- event lifecycle
def ev(value, breach, clear, sev="high"):
    return {"pm25": {"metric": "pm25_ugm3", "value": value, "baseline": 60.0, "baseline_method": "rolling_median(3h)",
                     "ratio": round(value / 60, 2), "z": 5.0, "breach": breach, "clear": clear, "severity": sev}}


def test_full_lifecycle_detected_confirmed_recommended_rechecked_closed(client):
    cfg = WorkerSettings(backend=client.app.state.settings)
    src = {"label": "probable traffic/combustion signature", "confidence": 0.7}
    t = START
    seq = [(0, ev(130, True, False)), (5, ev(135, True, False)), (15, ev(140, True, False)),   # detected, still, confirmed
           (20, ev(138, True, False)),                                                         # -> recommended
           (25, ev(100, False, False)),                                                        # between bands: stays
           (30, ev(62, False, True)), (40, ev(61, False, True)),                               # clear 10 min -> rechecked
           (50, ev(60, False, True)), (60, ev(60, False, True))]                               # clear 20 min -> closed
    seen = []
    with client.app.state.session_factory() as db:
        for dt, e in seq:
            update_events(db, "HITEC", e, t + timedelta(minutes=dt), src, cfg)
            db.commit()
            row = db.scalars(select(Event)).one()
            seen.append(row.status)
    assert seen == ["detected", "detected", "confirmed", "recommended", "recommended",
                    "recommended", "rechecked", "closed", "closed"]
    row = client.get("/v1/events").json()["events"][0]
    assert row["details"]["recommended_action"] and "heuristic" in row["details"]["recommended_basis"]
    assert row["details"]["recheck"]["outcome"] == "returned to baseline"
    assert [x["status"] for x in row["details"]["transitions"]] == \
        ["detected", "confirmed", "recommended", "rechecked", "closed"]
    assert row["severity"] and row["zone_id"] == "HITEC" and row["detected_at"] == "2026-12-10T02:00:00Z"
    assert row["details"]["triggered_by"].startswith("pm25_ugm3")


def test_transient_breach_closes_without_confirmation(client):
    cfg = WorkerSettings(backend=client.app.state.settings)
    with client.app.state.session_factory() as db:
        for dt, e in [(0, ev(130, True, False)), (5, ev(62, False, True)), (15, ev(61, False, True))]:
            update_events(db, "HITEC", e, START + timedelta(minutes=dt), None, cfg)
            db.commit()
        row = db.scalars(select(Event)).one()
    assert row.status == "closed" and row.details["outcome"].startswith("transient")


def test_one_open_event_per_metric_not_one_per_pass(client):
    cfg = WorkerSettings(backend=client.app.state.settings)
    with client.app.state.session_factory() as db:
        for dt in range(0, 12, 2):
            update_events(db, "HITEC", ev(130, True, False), START + timedelta(minutes=dt), None, cfg)
            db.commit()
        assert len(db.scalars(select(Event)).all()) == 1
