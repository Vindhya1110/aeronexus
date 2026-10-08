import pytest
from pydantic import ValidationError

from shared.schema import TelemetryPayload, plausibility_flags, quality_summary


def test_valid_payload_parses(valid_payload):
    p = TelemetryPayload.model_validate(valid_payload)
    assert p.device_id.startswith("ZONE_")
    assert p.timestamp_utc.tzinfo is not None
    assert p.to_wire() == valid_payload          # lossless round trip, 'Z' timestamp


def test_missing_required_fields_rejected(missing_fields_payload):
    with pytest.raises(ValidationError) as e:
        TelemetryPayload.model_validate(missing_fields_payload)
    missing = {tuple(err["loc"]) for err in e.value.errors() if err["type"] == "missing"}
    assert {("pm",), ("quality",), ("gps", "fix")} <= missing


def test_invalid_gps_fix_is_accepted_and_flagged(invalid_gps_payload):
    """No GPS fix is a *quality* problem, not a malformed payload: nulls kept, flag visible."""
    p = TelemetryPayload.model_validate(invalid_gps_payload)
    assert p.gps.fix is False and p.gps.lat is None and p.gps.lon is None
    assert p.quality.gps_valid is False
    assert "gps_invalid" in quality_summary(p)


def test_fix_true_with_null_position_rejected(valid_payload, clone):
    bad = clone(valid_payload)
    bad["gps"]["lat"] = None
    with pytest.raises(ValidationError):
        TelemetryPayload.model_validate(bad)


def test_gps_valid_without_fix_rejected(invalid_gps_payload, clone):
    bad = clone(invalid_gps_payload)
    bad["quality"]["gps_valid"] = True
    with pytest.raises(ValidationError):
        TelemetryPayload.model_validate(bad)


def test_duplicate_sequence_fixture(duplicate_sequence_payloads):
    a, b = (TelemetryPayload.model_validate(x) for x in duplicate_sequence_payloads)
    # schema-level both are valid; de-duplication is the backend's job on (device_id, sequence)
    assert (a.device_id, a.sequence) == (b.device_id, b.sequence)


def test_naive_timestamp_rejected(valid_payload, clone):
    bad = clone(valid_payload)
    bad["timestamp_utc"] = "2026-12-10T03:30:00"
    with pytest.raises(ValidationError):
        TelemetryPayload.model_validate(bad)


@pytest.mark.parametrize("dev", ["hitec_01", "ZONE_HITEC", "CAR_A_01", "ZONE__01", "ZONE_hi_01"])
def test_bad_device_id_rejected(valid_payload, clone, dev):
    bad = clone(valid_payload)
    bad["device_id"] = dev
    with pytest.raises(ValidationError):
        TelemetryPayload.model_validate(bad)


def test_unknown_key_rejected(valid_payload, clone):
    bad = clone(valid_payload)
    bad["extra_field"] = 1
    with pytest.raises(ValidationError):
        TelemetryPayload.model_validate(bad)


def test_null_measurements_allowed_and_not_zeroed(valid_payload, clone):
    d = clone(valid_payload)
    d["pm"]["pm25_ugm3"] = None
    p = TelemetryPayload.model_validate(d)
    assert p.pm.pm25_ugm3 is None


def test_out_of_range_kept_and_flagged(valid_payload, clone):
    d = clone(valid_payload)
    d["env"]["humidity_pct"] = 140.0
    d["gas_proxy"]["mq9_adc"] = 5000
    p = TelemetryPayload.model_validate(d)
    assert p.env.humidity_pct == 140.0                      # not dropped, not clamped
    assert set(plausibility_flags(p)) == {"humidity_pct", "mq9_adc"}


def test_warmup_flag_visible(warmup_payload):
    p = TelemetryPayload.model_validate(warmup_payload)
    assert p.quality.warmup is True
    assert "warmup" in quality_summary(p)
