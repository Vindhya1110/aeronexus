"""Simulator output must satisfy the data contract and carry the intended physics."""
from __future__ import annotations

import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.schema import TelemetryPayload
from simulator.faults import FaultConfig
from simulator.fleet import Fleet

WINTER = datetime(2026, 12, 10, 0, 0, tzinfo=timezone.utc)
MONSOON = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)


def run(start, hours, faults=None, seed=3):
    fleet = Fleet(seed, start, faults if faults is not None else FaultConfig.none(),
                  allow_route_download=False)
    from datetime import timedelta
    return list(fleet.run_until(start + timedelta(hours=hours)))


@pytest.fixture(scope="module")
def winter_day():
    return run(WINTER, 24)


def test_every_clean_payload_validates(winter_day):
    for em in winter_day:
        TelemetryPayload.model_validate(em.wire)


def test_fixed_nodes_have_zero_speed_and_buses_move(winter_day):
    speeds = defaultdict(list)
    for em in winter_day:
        s = em.wire["gps"]["speed_kmh"]
        speeds[em.wire["device_id"].split("_")[0]].append(s)
    assert set(speeds["ZONE"]) == {0.0}
    bus = speeds["BUS"]
    assert max(bus) <= 25.0 and min(bus) == 0.0
    assert 15 <= st.median([s for s in bus if s > 8]) <= 25     # cruising speed band


def test_sequences_are_contiguous_per_device(winter_day):
    seqs = defaultdict(list)
    for em in winter_day:
        seqs[em.wire["device_id"]].append(em.wire["sequence"])
    for dev, s in seqs.items():
        assert s == list(range(1, len(s) + 1)), dev


def test_deterministic_for_same_seed():
    a = [e.wire for e in run(WINTER, 1)]
    b = [e.wire for e in run(WINTER, 1)]
    assert a == b


def test_winter_elevated_vs_monsoon():
    def mean_pm(emissions):
        return st.mean(e.wire["pm"]["pm25_ugm3"] for e in emissions if e.wire["device_id"] == "ZONE_HITEC_01")
    assert mean_pm(run(WINTER, 24)) > 1.8 * mean_pm(run(MONSOON, 24))


def test_rush_hour_peak_in_traffic_zone(winter_day):
    by_hour = defaultdict(list)
    for em in winter_day:
        if em.wire["device_id"] == "ZONE_MGBSEC_01":
            h = (datetime.fromisoformat(em.wire["timestamp_utc"].replace("Z", "+00:00")).hour * 60 + 330) // 60 % 24
            by_hour[h].append(em.wire["pm"]["pm25_ugm3"])
    rush = st.mean(by_hour[9] + by_hour[19])
    night = st.mean(by_hour[2] + by_hour[3])
    assert rush > 1.25 * night


def _signature(winter_day, dev):
    rows = [e.wire for e in winter_day if e.wire["device_id"] == dev and not e.wire["quality"]["warmup"]]
    return {
        "pm25": st.mean(r["pm"]["pm25_ugm3"] for r in rows),
        "ratio": st.mean(r["pm"]["pm10_ugm3"] / r["pm"]["pm25_ugm3"] for r in rows),
        "mq2": st.mean(r["gas_proxy"]["mq2_adc"] for r in rows),
        "mq9": st.mean(r["gas_proxy"]["mq9_adc"] for r in rows),
        "mq135": st.mean(r["gas_proxy"]["mq135_adc"] for r in rows),
    }


def test_source_signatures_differ_by_zone_context(winter_day):
    traffic = _signature(winter_day, "ZONE_MGBSEC_01")
    industrial = _signature(winter_day, "ZONE_IDAPASH_01")
    # traffic zone: MQ-9 above MQ-135-relative baseline; industrial: MQ-135 and MQ-2 lifted
    assert traffic["mq9"] > industrial["mq9"] + 80
    assert industrial["mq135"] > traffic["mq135"] + 300
    assert industrial["mq2"] > traffic["mq2"] + 100


def test_construction_dust_raises_pm10_without_gas():
    """During a dust burst in a construction zone, PM10/PM2.5 ratio jumps and MQ stays near baseline."""
    rows = [e.wire for e in run(WINTER, 48) if e.wire["device_id"] == "ZONE_HITEC_01"
            and not e.wire["quality"]["warmup"]]
    ratios = [r["pm"]["pm10_ugm3"] / r["pm"]["pm25_ugm3"] for r in rows]
    assert max(ratios) > 2.4
    dusty = [r for r, q in zip(rows, ratios) if q > 2.4]
    assert dusty
    assert st.mean(r["gas_proxy"]["mq2_adc"] for r in dusty) < 850     # MQ-2 not elevated


def test_fault_injection_produces_all_cases():
    out = run(WINTER, 48, FaultConfig(gps_no_fix_rate=0.05, gps_poor_hdop_rate=0.05, sensor_dropout_rate=0.05,
                                      duplicate_rate=0.05, malformed_rate=0.05, reboot_per_day=3))
    wires = [e for e in out]
    assert any(e.kind == "malformed" for e in wires)
    assert any(e.kind == "duplicate" for e in wires)

    for e in wires:
        if e.kind == "malformed":
            with pytest.raises(ValidationError):
                TelemetryPayload.model_validate(e.wire)
            continue
        p = TelemetryPayload.model_validate(e.wire)         # everything else must still validate
        if p.quality.sensor_flags:                          # flagged sensor => null, never 0
            vals = [p.pm.pm25_ugm3, p.gas_proxy.mq2_adc, p.gas_proxy.mq9_adc, p.gas_proxy.mq135_adc,
                    p.env.temperature_c, p.env.pressure_hpa]
            assert None in vals
    ok = [TelemetryPayload.model_validate(e.wire) for e in wires if e.kind != "malformed"]
    assert any(not p.gps.fix for p in ok)
    assert any(p.gps.fix and not p.quality.gps_valid for p in ok)
    assert any(p.quality.warmup for p in ok if p.timestamp_utc > WINTER.replace(hour=1))
    assert any(p.quality.sensor_flags for p in ok)


def test_duplicates_repeat_exact_sequence():
    out = run(WINTER, 24, FaultConfig(duplicate_rate=0.2, gps_no_fix_rate=0, gps_poor_hdop_rate=0,
                                      sensor_dropout_rate=0, malformed_rate=0, reboot_per_day=0))
    dups = [i for i, e in enumerate(out) if e.kind == "duplicate"]
    assert dups
    for i in dups:
        assert out[i].wire == out[i - 1].wire
