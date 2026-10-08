"""Read endpoints, WebSocket, and a simulator -> backend end-to-end check."""
from datetime import datetime, timedelta, timezone

from simulator.faults import FaultConfig
from simulator.fleet import Fleet

ZONE_IDS = {"PGPT", "CHNAM", "HITEC", "MGBSEC", "SANATH", "IDAPASH"}
START = datetime(2026, 12, 10, 3, 30, tzinfo=timezone.utc)


def feed(client, auth, minutes=30, faults=None, seed=5):
    """Drive the real simulator fleet through POST /v1/telemetry; return {status_code: count}."""
    fleet = Fleet(seed, START, faults if faults is not None else FaultConfig.none(),
                  allow_route_download=False)
    codes: dict[int, int] = {}
    for em in fleet.run_until(START + timedelta(minutes=minutes)):
        c = client.post("/v1/telemetry", json=em.wire, headers=auth).status_code
        codes[c] = codes.get(c, 0) + 1
    return codes


def test_zones_seeded_from_shared_definitions(client):
    zones = client.get("/v1/zones").json()["zones"]
    assert {z["zone_id"] for z in zones} == ZONE_IDS
    z = zones[0]
    assert z["current"] is None and z["aqi"] is None and z["source_hypothesis"] is None
    assert z["last_seen_utc"] is None and z["stale"] is True
    assert z["source_context"]


def test_zone_current_after_ingest(client, auth, valid_payload):
    client.post("/v1/telemetry", json=valid_payload, headers=auth)
    zones = {z["zone_id"]: z for z in client.get("/v1/zones").json()["zones"]}
    s = zones["SANATH"]
    assert s["current"]["pm25_ugm3"] == valid_payload["pm"]["pm25_ugm3"]
    assert s["current"]["n_samples"] == 1
    assert s["aqi"] is None                                  # nothing faked before the worker exists
    assert s["devices"]["online"] == 1 and s["stale"] is False
    assert zones["HITEC"]["current"] is None


def test_devices_online_with_versions(client, auth, valid_payload):
    online = client
    online.post("/v1/telemetry", json=valid_payload, headers=auth)
    d = online.get("/v1/devices").json()["devices"][0]
    assert d["status"] == "online" and d["firmware_version"] == "0.1.0"
    assert d["calibration_version"] == "prototype-0" and d["last_packet_utc"] == valid_payload["timestamp_utc"]


def test_device_offline_when_threshold_exceeded(tmp_path, auth, valid_payload):
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.config import Settings
    c = TestClient(create_app(Settings(api_key="test-key", offline_after_s=-1,
                                       database_url=f"sqlite:///{(tmp_path / 'o.db').as_posix()}")))
    c.post("/v1/telemetry", json=valid_payload, headers=auth)
    assert c.get("/v1/devices").json()["devices"][0]["status"] == "offline"
    c.app.state.engine.dispose()


def test_history_downsamples_and_selects_fields(client, auth):
    assert feed(client, auth, minutes=60).keys() == {202}
    h = client.get("/v1/zones/HITEC/history", params={"fields": "pm25_ugm3,mq9_adc", "max_points": 10}).json()
    assert h["fields"] == ["pm25_ugm3", "mq9_adc"]
    assert 1 <= len(h["points"]) <= 12
    assert set(h["points"][0]["values"]) == {"pm25_ugm3", "mq9_adc"}
    assert "proxy" in h["meta"]["field_notes"]["mq9_adc"]     # MQ always labelled as proxy
    assert h["meta"]["n_raw_samples"] > len(h["points"])      # actually downsampled
    assert h["points"][0]["flags"]["warmup_samples"] >= 0


def test_history_rejects_unknown_field_and_zone(client):
    r = client.get("/v1/zones/HITEC/history", params={"fields": "pm25_ugm3,bogus"})
    assert r.status_code == 422 and r.json()["detail"]["unknown"] == ["bogus"]
    assert client.get("/v1/zones/NOPE/history").status_code == 404


def test_history_time_range(client, auth):
    feed(client, auth, minutes=60)
    t0 = (START + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t1 = (START + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    h = client.get("/v1/zones/HITEC/history", params={"from": t0, "to": t1, "bucket_s": 60}).json()
    assert h["bucket_s"] == 60 and 9 <= len(h["points"]) <= 11
    assert all(t0 <= p["t"] <= t1 for p in h["points"])


def test_history_empty_zone(client):
    h = client.get("/v1/zones/PGPT/history").json()
    assert h["points"] == []


def test_forecast_stub_shape(client):
    f = client.get("/v1/zones/HITEC/forecast").json()
    assert f["status"] == "unavailable" and f["forecasts"] == []
    assert f["horizons_min"] == [15, 30, 60, 360, 1440, 4320] and f["methods"] == ["persistence", "linear_trend", "gbt"]
    assert client.get("/v1/zones/NOPE/forecast").status_code == 404


def test_events_empty(client):
    assert client.get("/v1/events").json() == {"count": 0, "events": []}


def test_websocket_pushes_on_accepted_telemetry(client, auth, valid_payload):
    with client.websocket_connect("/v1/live") as ws:
        assert ws.receive_json()["type"] == "hello"
        assert client.post("/v1/telemetry", json=valid_payload, headers=auth).status_code == 202
        msg = ws.receive_json()
        assert msg["type"] == "telemetry_accepted"
        assert msg["device_id"] == valid_payload["device_id"] and msg["zone_id"] == "SANATH"
        assert "pm" not in msg                                # summaries, not raw samples


def test_simulator_end_to_end_with_faults(client, auth):
    """Everything the simulator can emit gets exactly the status code its delivery logic expects."""
    codes = feed(client, auth, minutes=90,
                 faults=FaultConfig(gps_no_fix_rate=0.05, gps_poor_hdop_rate=0.05, sensor_dropout_rate=0.05,
                                    duplicate_rate=0.05, malformed_rate=0.05, reboot_per_day=20))
    assert set(codes) <= {202, 409, 422}
    assert codes.get(409) and codes.get(422) and codes[202] > 100
    devices = client.get("/v1/devices").json()["devices"]
    assert len(devices) == 11 and {d["kind"] for d in devices} == {"fixed", "mobile"}
    zones = client.get("/v1/zones").json()["zones"]
    assert all(z["current"] for z in zones if z["zone_id"] in ("HITEC", "IDAPASH", "SANATH"))
