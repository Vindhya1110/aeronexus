"""POST /v1/telemetry against the recorded fixtures (shared/fixtures/*.json)."""
from sqlalchemy import select

from backend.models import Device, Measurement, RawMeasurement


def post(client, auth, body):
    return client.post("/v1/telemetry", json=body, headers=auth)


def test_valid_payload_accepted(client, auth, valid_payload):
    r = post(client, auth, valid_payload)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "accepted"
    assert body["zone_id"] == "SANATH" and body["zone_source"] == "gps"


def test_raw_is_stored_verbatim_and_typed_row_written(client, auth, valid_payload):
    post(client, auth, valid_payload)
    with client.app.state.session_factory() as db:
        raw = db.scalars(select(RawMeasurement)).one()
        m = db.scalars(select(Measurement)).one()
        dev = db.get(Device, valid_payload["device_id"])
    assert raw.payload == valid_payload                      # exactly what was sent
    assert m.raw_id == raw.id and m.zone_id == "SANATH"
    assert m.pm25_ugm3 == valid_payload["pm"]["pm25_ugm3"]
    assert dev.kind == "fixed" and dev.zone_id == "SANATH" and dev.firmware_version == "0.1.0"


def test_duplicate_sequence_returns_409(client, auth, duplicate_sequence_payloads):
    first, second = duplicate_sequence_payloads
    assert post(client, auth, first).status_code == 202
    r = post(client, auth, second)
    assert r.status_code == 409
    assert r.json()["status"] == "duplicate" and r.json()["sequence"] == first["sequence"]
    with client.app.state.session_factory() as db:
        assert len(db.scalars(select(RawMeasurement)).all()) == 1      # nothing overwritten/added


def test_same_sequence_different_device_is_not_duplicate(client, auth, valid_payload, clone):
    other = clone(valid_payload)
    other["device_id"] = "ZONE_HITEC_01"
    assert post(client, auth, valid_payload).status_code == 202
    assert post(client, auth, other).status_code == 202


def test_missing_required_fields_gives_field_level_422(client, auth, missing_fields_payload):
    r = post(client, auth, missing_fields_payload)
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "validation_failed"
    fields = {e["field"]: e["type"] for e in body["errors"]}
    assert fields["pm"] == "missing" and fields["quality"] == "missing" and fields["gps.fix"] == "missing"


def test_rejected_payload_is_not_stored(client, auth, missing_fields_payload):
    post(client, auth, missing_fields_payload)
    with client.app.state.session_factory() as db:
        assert db.scalars(select(RawMeasurement)).all() == []


def test_missing_api_key_401(client, valid_payload):
    assert client.post("/v1/telemetry", json=valid_payload).status_code == 401


def test_wrong_api_key_403(client, valid_payload):
    r = client.post("/v1/telemetry", json=valid_payload, headers={"X-API-Key": "nope"})
    assert r.status_code == 403


def test_auth_is_checked_before_validation(client, missing_fields_payload):
    assert client.post("/v1/telemetry", json=missing_fields_payload).status_code == 401


def test_invalid_gps_accepted_flagged_and_not_zoned_from_gps(client, auth, invalid_gps_payload):
    """Bus with no fix and no known last zone: stored, flagged, zone left unresolved (not guessed)."""
    r = post(client, auth, invalid_gps_payload)
    assert r.status_code == 202
    assert r.json()["zone_id"] is None
    assert "gps_invalid" in r.json()["quality_flags"]
    with client.app.state.session_factory() as db:
        m = db.scalars(select(Measurement)).one()
    assert m.lat is None and m.lon is None and m.gps_valid is False   # null kept, never 0


def test_fixed_node_without_gps_falls_back_to_home_zone(client, auth, valid_payload, clone):
    d = clone(valid_payload)
    d["gps"].update(lat=None, lon=None, fix=False, hdop=None, speed_kmh=None)
    d["quality"]["gps_valid"] = False
    r = post(client, auth, d)
    assert r.json()["zone_id"] == "SANATH" and r.json()["zone_source"] == "device_home"


def test_bus_outside_all_zones_has_no_zone(client, auth, bus_payload):
    r = post(client, auth, bus_payload)                      # fixture position is between zones
    assert r.status_code == 202 and r.json()["zone_id"] is None


def test_bus_without_gps_uses_last_known_zone(client, auth, bus_payload, clone):
    inside = clone(bus_payload)
    inside["gps"].update(lat=17.3688, lon=78.4735)           # Madina, inside CHNAM
    assert post(client, auth, inside).json()["zone_id"] == "CHNAM"
    nofix = clone(bus_payload)
    nofix["sequence"] = inside["sequence"] + 1
    nofix["gps"].update(lat=None, lon=None, fix=False, hdop=None, speed_kmh=None)
    nofix["quality"]["gps_valid"] = False
    r = post(client, auth, nofix)
    assert r.json()["zone_id"] == "CHNAM" and r.json()["zone_source"] == "last_known"


def test_warmup_flag_visible(client, auth, warmup_payload):
    r = post(client, auth, warmup_payload)
    assert r.status_code == 202 and "warmup" in r.json()["quality_flags"]


def test_null_pm_is_stored_as_null(client, auth, valid_payload, clone):
    d = clone(valid_payload)
    d["pm"]["pm25_ugm3"] = None
    d["quality"]["sensor_flags"] = 1
    r = post(client, auth, d)
    assert r.status_code == 202 and "sensor_fault:pm_sensor" in r.json()["quality_flags"]
    with client.app.state.session_factory() as db:
        assert db.scalars(select(Measurement)).one().pm25_ugm3 is None


def test_out_of_range_kept_and_flagged(client, auth, valid_payload, clone):
    d = clone(valid_payload)
    d["env"]["humidity_pct"] = 140.0
    r = post(client, auth, d)
    assert r.status_code == 202 and "out_of_range:humidity_pct" in r.json()["quality_flags"]
    with client.app.state.session_factory() as db:
        assert db.scalars(select(Measurement)).one().humidity_pct == 140.0


def test_malformed_json_is_422(client, auth):
    r = client.post("/v1/telemetry", content=b"{not json", headers={**auth, "Content-Type": "application/json"})
    assert r.status_code == 422


def test_out_of_order_packet_does_not_move_last_packet_backwards(client, auth, valid_payload, clone):
    late = clone(valid_payload)
    early = clone(valid_payload)
    early["sequence"] = late["sequence"] - 1
    early["timestamp_utc"] = "2026-12-10T03:00:00Z"
    post(client, auth, late)
    post(client, auth, early)
    dev = client.get("/v1/devices").json()["devices"][0]
    assert dev["last_sequence"] == late["sequence"]
