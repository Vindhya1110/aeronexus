"""ESP32 node integration: firmware line -> data contract -> backend -> worker, without needing the hardware."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.models import DerivedMeasurement, Device, Measurement
from gateway import serial_bridge
from gateway.hardware import (CALIBRATION_VERSION, FIRMWARE_VERSION, NodeConfig, Window, build_payload, next_mode, parse_csv_line,
                              parse_mode_echo)
from shared.constants import FLAG_ENV_TH, FLAG_GPS_MODULE, FLAG_MQ2, FLAG_PM, FLAG_PRESSURE, decode_sensor_flags
from shared.schema import TelemetryPayload, quality_summary
from simulator.faults import FaultConfig
from simulator.fleet import Fleet
from worker.config import WorkerSettings
from worker.runner import Worker

#                CSV_DATA,millis,mq135,mq2,mq9,dhtT,dhtRH,bmpT,hPa,alt   (the firmware's exact field order)
LINE = "CSV_DATA,60000,2010,1100,1300,31.0,54.0,30.4,951.80,541.2"


def lines(n=20, start_ms=2000, **over):
    """n firmware lines, 2 s apart, as the node would print them."""
    out = []
    for i in range(n):
        v = dict(mq135=2000 + i, mq2=1100, mq9=1300, dht="31.0", rh="54.0", bmp="30.4", p="951.80", alt="541.2")
        v.update(over)
        out.append(f"CSV_DATA,{start_ms + 2000 * i},{v['mq135']},{v['mq2']},{v['mq9']},{v['dht']},{v['rh']},{v['bmp']},{v['p']},{v['alt']}")
    return out


def window(ls):
    w = Window()
    for ln in ls:
        w.add(parse_csv_line(ln))
    return w


# ---------------------------------------------------------------------------- parsing
def test_parse_follows_the_firmware_field_order():
    r = parse_csv_line(LINE)
    assert (r.uptime_ms, r.mq135, r.mq2, r.mq9) == (60000, 2010, 1100, 1300)      # firmware order is MQ135, MQ2, MQ9
    assert (r.dht_temp_c, r.dht_hum_pct, r.bmp_temp_c, r.pressure_hpa, r.altitude_m) == (31.0, 54.0, 30.4, 951.8, 541.2)


def test_parse_empty_fields_are_none_not_zero():
    r = parse_csv_line("CSV_DATA,4000,1500,1000,1200,,,,,")                          # DHT + BMP failed: firmware prints empty fields
    assert r.dht_temp_c is None and r.dht_hum_pct is None and r.bmp_temp_c is None and r.pressure_hpa is None
    assert (r.mq135, r.mq2, r.mq9) == (1500, 1000, 1200)


@pytest.mark.parametrize("bad", ["", "========", "MQ-135 raw ADC: 1500", "CSV_DATA,1,2,3", "CSV_DATA,x,1,2,3,4,5,6,7,8",
                                 "CSV_DATA,-5,1,2,3,4,5,6,7,8", "Starting AERONEX...", "MODE_SET,warning"])
def test_parse_ignores_everything_that_is_not_a_complete_data_line(bad):
    assert parse_csv_line(bad) is None


def test_parse_rejects_adc_values_outside_12_bit_range_per_field():
    r = parse_csv_line("CSV_DATA,4000,9999,1000,-3,30,50,30,950,500")
    assert r.mq135 is None and r.mq9 is None and r.mq2 == 1000


def test_mode_echo_parse():
    assert parse_mode_echo("MODE_SET,warning") == "warning" and parse_mode_echo("CSV_DATA,1") is None


# ---------------------------------------------------------------------------- mapping to the contract
def test_payload_is_a_valid_contract_packet_and_honest_about_what_is_missing():
    cfg = NodeConfig(device_id="ZONE_HITEC_90", warmup_s=10)
    wire = build_payload(window(lines()), cfg, 7, datetime(2026, 12, 10, 4, 0, 5, 999, tzinfo=timezone.utc))
    p = TelemetryPayload.model_validate(wire)                                        # the SAME validation the backend applies
    assert p.sequence == 7 and p.timestamp_utc == datetime(2026, 12, 10, 4, 0, 5, tzinfo=timezone.utc)
    assert p.pm.pm25_ugm3 is None and p.pm.pm10_ugm3 is None                         # no PM sensor: null, never 0 or invented
    assert p.gas_proxy.mq135_adc == round(sum(2000 + i for i in range(20)) / 20) and p.gas_proxy.mq2_adc == 1100   # window MEAN
    assert p.gps.fix is False and p.gps.lat is None and p.quality.gps_valid is False
    assert p.quality.sensor_flags & FLAG_PM and p.quality.sensor_flags & FLAG_GPS_MODULE
    assert not (p.quality.sensor_flags & (FLAG_MQ2 | FLAG_ENV_TH | FLAG_PRESSURE))   # everything that IS fitted reported fine
    assert p.firmware_version == FIRMWARE_VERSION and p.calibration_version == CALIBRATION_VERSION
    assert set(decode_sensor_flags(p.quality.sensor_flags)) == {"pm_sensor", "gps_module"}
    assert p.env.pressure_hpa == 951.8 and p.env.humidity_pct == 54.0


def test_temperature_prefers_the_bmp180_and_falls_back_to_the_dht11():
    cfg = NodeConfig()
    assert build_payload(window(lines(dht="33.0", bmp="30.4")), cfg, 1)["env"]["temperature_c"] == 30.4
    w = build_payload(window(lines(dht="33.0", bmp="")), cfg, 1)
    assert w["env"]["temperature_c"] == 33.0 and w["quality"]["sensor_flags"] & FLAG_PRESSURE == 0


def test_failed_sensors_become_null_plus_the_matching_flag():
    w = build_payload(window(lines(dht="", rh="", bmp="", p="")), NodeConfig(), 1)
    assert w["env"] == {"temperature_c": None, "humidity_pct": None, "pressure_hpa": None}
    assert w["quality"]["sensor_flags"] & FLAG_ENV_TH and w["quality"]["sensor_flags"] & FLAG_PRESSURE
    TelemetryPayload.model_validate(w)
    only_mq135 = [f"CSV_DATA,{2000 * i},1500,,,30,50,30,950,500" for i in range(1, 5)]
    w = build_payload(window(only_mq135), NodeConfig(), 1)
    assert w["gas_proxy"] == {"mq2_adc": None, "mq9_adc": None, "mq135_adc": 1500} and w["quality"]["sensor_flags"] & FLAG_MQ2


def test_warmup_follows_node_uptime_and_survives_a_reboot():
    cfg = NodeConfig(warmup_s=300)
    assert build_payload(window(lines(5, start_ms=2000)), cfg, 1)["quality"]["warmup"] is True
    assert build_payload(window(lines(5, start_ms=400_000)), cfg, 1)["quality"]["warmup"] is False


def test_empty_window_sends_nothing():
    assert build_payload(Window(), NodeConfig(), 1) is None


def test_surveyed_position_is_reported_as_a_fix_only_when_supplied():
    w = build_payload(window(lines()), NodeConfig(lat=17.45, lon=78.38), 1)
    assert w["gps"]["fix"] is True and w["quality"]["gps_valid"] is True and not (w["quality"]["sensor_flags"] & FLAG_GPS_MODULE)
    with pytest.raises(ValueError):
        NodeConfig(lat=17.45)


@pytest.mark.parametrize("bad", ["hitec", "ZONE_HITEC", "NODE_HITEC_01", "ZONE_hitec_01", "ZONE_HITEC_1"])
def test_device_id_must_follow_the_convention(bad):
    with pytest.raises(ValueError):
        NodeConfig(device_id=bad)


def test_hardware_mode_has_hysteresis_and_can_be_forced():
    assert next_mode("normal", 250) == "normal" and next_mode("normal", 310) == "warning"
    assert next_mode("warning", 290) == "warning"                  # 270-300: stays on, so it cannot flicker
    assert next_mode("warning", 260) == "normal" and next_mode("normal", None) == "normal"
    assert next_mode("normal", 400, forced="silent") == "silent" and next_mode("warning", 100, forced="normal") == "normal"


# ---------------------------------------------------------------------------- the bridge loop
class FakeClient:
    def __init__(self):
        self.sent, self.buffer, self.stats = [], [], {"delivered": 0}

    def send(self, wire):
        self.sent.append(wire)
        self.stats["delivered"] += 1


class Args:
    device_id, interval_s, warmup_s, lat, lon, mode, fresh, max_packets, url = "ZONE_HITEC_90", 10, 300, None, None, "normal", False, 0, "http://x"


def run_bridge(src_lines, monkeypatch, tmp_path, **over):
    monkeypatch.setattr(serial_bridge, "STATE", tmp_path / "hw_state.json")
    a = Args()
    for k, v in over.items():
        setattr(a, k, v)
    client = FakeClient()
    serial_bridge.run(a, serial_bridge.ReplaySource(src_lines), client)
    return client.sent


def test_bridge_windows_by_node_time_and_numbers_packets_continuously(monkeypatch, tmp_path):
    sent = run_bridge(lines(31), monkeypatch, tmp_path)                 # 31 lines x 2 s, 10 s windows
    assert [w["sequence"] for w in sent] == list(range(1, len(sent) + 1)) and len(sent) == 5      # 6 readings span 10 s -> 5 full windows
    assert all(TelemetryPayload.model_validate(w) for w in sent)


def test_bridge_resumes_sequence_numbers_after_a_restart(monkeypatch, tmp_path):
    first = run_bridge(lines(11), monkeypatch, tmp_path)
    second = run_bridge(lines(11), monkeypatch, tmp_path)
    assert second[0]["sequence"] == first[-1]["sequence"] + 1            # no reuse: the backend would answer 409 for a repeat
    third = run_bridge(lines(11), monkeypatch, tmp_path, fresh=True)
    assert third[0]["sequence"] == 1                                     # --fresh after wiping the database


def test_bridge_closes_the_window_when_the_node_reboots(monkeypatch, tmp_path):
    ls = lines(3, start_ms=500_000) + lines(3, start_ms=2000)             # uptime goes backwards = reboot
    sent = run_bridge(ls, monkeypatch, tmp_path)
    assert len(sent) == 1 and sent[0]["quality"]["warmup"] is False       # the pre-reboot readings are flushed, not mixed with warm-up ones


def test_bridge_ignores_noise_between_data_lines(monkeypatch, tmp_path):
    noisy = []
    for ln in lines(12):
        noisy += ["========================================", "       AERONEX FLEET SENSOR REPORT", "MQ-135 raw ADC: 2000", ln, "MODE_SET,normal"]
    assert len(run_bridge(noisy, monkeypatch, tmp_path)) == 2


def test_serial_source_reads_data_lines_and_writes_mode_commands_over_a_real_pyserial_port():
    """pyserial loop:// port echoes what is written: exercises the real SerialSource code path (open, readline, decode, write)."""
    src = serial_bridge.SerialSource("loop://")
    src._open()
    src.ser.write((LINE + chr(13) + chr(10)).encode())
    src.write("MODE,warning" + chr(10))
    got = []
    for ln in src.lines():
        got.append(ln)
        if len(got) == 2:
            break
    assert parse_csv_line(got[0]).mq135 == 2010 and got[1] == "MODE,warning"
    src.ser.close()


# ---------------------------------------------------------------------------- backend + worker with the node in the fleet
START = datetime(2026, 12, 10, 2, 0, tzinfo=timezone.utc)


def feed_sim(client, auth, minutes=100):
    for em in Fleet(5, START, FaultConfig.none(), allow_route_download=False).run_until(START + timedelta(minutes=minutes)):
        assert client.post("/v1/telemetry", json=em.wire, headers=auth).status_code == 202


def feed_node(client, auth, minutes=100, device_id="ZONE_HITEC_90"):
    cfg, seq, n = NodeConfig(device_id=device_id, warmup_s=60), 0, 0
    while n < minutes * 2:                                             # one packet per 30 s
        w = Window()
        for i in range(15):
            w.add(parse_csv_line(f"CSV_DATA,{(n * 15 + i) * 2000 + 2000},{2000 + (n % 7)},1100,1300,31.0,54.0,30.4,951.8,541.2"))
        seq += 1
        r = client.post("/v1/telemetry", json=build_payload(w, cfg, seq, START + timedelta(seconds=30 * n)), headers=auth)
        assert r.status_code == 202, r.text
        n += 1


def test_node_is_accepted_and_placed_in_its_zone(client, auth):
    feed_node(client, auth, minutes=5)
    devs = {d["device_id"]: d for d in client.get("/v1/devices").json()["devices"]}
    d = devs["ZONE_HITEC_90"]
    assert d["kind"] == "fixed" and d["zone_id"] == "HITEC" and d["gps_valid"] is False
    assert set(d["sensor_faults"]) == {"pm_sensor", "gps_module"}        # shown on Node health: no PM sensor, no GPS module
    z = {x["zone_id"]: x for x in client.get("/v1/zones").json()["zones"]}["HITEC"]
    assert z["devices"]["total"] == 1 and z["current"]["pm25_ugm3"] is None and z["current"]["n_samples"] == 0
    with client.app.state.session_factory() as db:
        m = db.scalars(select(Measurement).where(Measurement.device_id == "ZONE_HITEC_90")).first()
        assert m.zone_id == "HITEC" and m.zone_source == "device_home" and m.pm25_ugm3 is None and m.mq135_adc is not None


def test_resending_a_packet_is_safe_after_a_dropped_connection(client, auth):
    w = window(lines())
    wire = build_payload(w, NodeConfig(), 1, START)
    assert client.post("/v1/telemetry", json=wire, headers=auth).status_code == 202
    assert client.post("/v1/telemetry", json=wire, headers=auth).status_code == 409      # store-and-forward resend: harmless


def test_worker_with_the_node_joining_a_running_fleet_leaves_air_quality_unchanged(client, auth):
    feed_sim(client, auth)
    worker = Worker(client.app.state.session_factory, WorkerSettings(backend=client.app.state.settings))
    worker.run_pass()
    before = {z["zone_id"]: z for z in client.get("/v1/zones").json()["zones"]}
    feed_node(client, auth)                                              # the ESP32 node joins HITEC while everything is running
    worker.run_pass()
    after = {z["zone_id"]: z for z in client.get("/v1/zones").json()["zones"]}
    for zid in before:
        assert before[zid]["aqi"]["value"] == after[zid]["aqi"]["value"], zid        # the PM-less node never changes AQI, anywhere
        assert before[zid]["source_hypothesis"]["label"] == after[zid]["source_hypothesis"]["label"], zid
    assert after["HITEC"]["devices"]["total"] == before["HITEC"]["devices"]["total"] + 1     # but it IS counted as a device of its zone
    assert after["HITEC"]["coverage"]["devices"]["total"] == after["HITEC"]["devices"]["total"]
    assert after["HITEC"]["current"]["n_samples"] == before["HITEC"]["current"]["n_samples"]  # PM samples only


def test_worker_survives_a_zone_that_has_only_the_pm_less_node(client, auth):
    feed_node(client, auth, minutes=40, device_id="ZONE_SANATH_90")
    rows = Worker(client.app.state.session_factory, WorkerSettings(backend=client.app.state.settings)).run_pass()
    assert any(r.get("zone") == "SANATH" for r in rows)                  # no crash, an honest "no AQI" row
    z = {x["zone_id"]: x for x in client.get("/v1/zones").json()["zones"]}["SANATH"]
    assert z["aqi"] is None or z["aqi"]["value"] is None
    with client.app.state.session_factory() as db:
        assert db.scalar(select(Device).where(Device.device_id == "ZONE_SANATH_90")) is not None
