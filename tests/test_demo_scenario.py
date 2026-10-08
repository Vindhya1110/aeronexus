"""Demo scenario + demo tooling: deterministic, schema-valid, and the scripted episodes are really there."""
from __future__ import annotations

import copy
import sqlite3
import statistics as st
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shared.schema import TelemetryPayload
from simulator import scenario
from simulator.faults import FaultConfig
from simulator.fleet import Fleet

ROOT = Path(__file__).resolve().parent.parent


def _emit(start, end):
    fleet = Fleet(42, start, FaultConfig.none(), allow_route_download=False)
    out = []
    for em in fleet.run_until(end):
        scenario.apply(em.wire, em.truth)
        out.append(em.wire)
    return out


@pytest.fixture(scope="module")
def window():
    # 30 min before the smoke episode through 40 min after its start: small enough to be fast
    t0 = datetime(2026, 12, 10, 4, 0, tzinfo=timezone.utc)
    return _emit(t0, t0 + timedelta(minutes=70))


def test_scenario_output_is_deterministic():
    t0 = datetime(2026, 12, 10, 4, 25, tzinfo=timezone.utc)
    a, b = _emit(t0, t0 + timedelta(minutes=10)), _emit(t0, t0 + timedelta(minutes=10))
    assert a == b and len(a) > 50


def test_scenario_payloads_still_match_the_contract(window):
    for w in window[:300]:
        TelemetryPayload.model_validate(w)


def test_smoke_episode_is_visible_in_hitec_only(window):
    def mean(zone, field, group, lo, hi):
        vals = [w[group][field] for w in window if scenario.zone_of_device(w["device_id"]) == zone
                and lo <= datetime.fromisoformat(w["timestamp_utc"].replace("Z", "+00:00")) < hi]
        return st.mean(vals)
    base = datetime(2026, 12, 10, 4, 0, tzinfo=timezone.utc)
    before = (base, base + timedelta(minutes=20))
    peak = (base + timedelta(minutes=45), base + timedelta(minutes=65))
    # PM2.5 and BOTH combustion proxies (MQ-2, MQ-135) jump in HITEC ...
    assert mean("HITEC", "pm25_ugm3", "pm", *peak) > mean("HITEC", "pm25_ugm3", "pm", *before) + 60
    assert mean("HITEC", "mq2_adc", "gas_proxy", *peak) > 1.5 * mean("HITEC", "mq2_adc", "gas_proxy", *before)
    assert mean("HITEC", "mq135_adc", "gas_proxy", *peak) > 1.3 * mean("HITEC", "mq135_adc", "gas_proxy", *before)
    # ... while a zone with no scripted smoke does not move like that
    assert mean("CHNAM", "pm25_ugm3", "pm", *peak) < mean("CHNAM", "pm25_ugm3", "pm", *before) + 40


def test_apply_ignores_malformed_payloads_and_unscripted_devices():
    assert scenario.zone_of_device("BUS_PGHTC_01") is None
    w = {"device_id": "ZONE_HITEC_01", "timestamp_utc": "2026-12-10T04:50:00Z", "pm": None, "gas_proxy": None}
    before = copy.deepcopy(w)
    scenario.apply(w)                     # pm is None -> untouched, no crash
    assert w == before


def test_integrity_checker_detects_a_gap(tmp_path):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE raw_measurements (device_id TEXT, sequence INT)")
    con.execute("CREATE TABLE measurements (device_id TEXT)")
    for seq in (1, 2, 3, 5):               # 4 is missing = lost packet
        con.execute("INSERT INTO raw_measurements VALUES ('ZONE_X_01', ?)", (seq,))
        con.execute("INSERT INTO measurements VALUES ('ZONE_X_01')")
    con.commit()
    con.close()
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_integrity.py"), "--db", str(db)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "missing=1" in r.stdout and "FAIL" in r.stdout
