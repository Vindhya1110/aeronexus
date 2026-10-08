# shared/ - contract used by every component

| file | purpose |
|---|---|
| `schema.py` | Pydantic model of the data contract v1.0 (`TelemetryPayload`), plausibility/quality helpers |
| `zones.py` | Seed Hyderabad zones (polyline + radius + `source_context`), `nearest_zone()` |
| `constants.py` | API header/path, device-ID regex, `sensor_flags` bits, plausible ranges |
| `geo.py` | haversine and point-to-segment distance |
| `fixtures/` | recorded payloads: valid, missing fields, invalid GPS fix, duplicate sequence, bus, warm-up |

Rules: UTC everywhere; missing values are `null`, never 0; out-of-range values are kept and flagged;
MQ values are proxy indices. Changing the contract means updating `schema.py` and all consumers.

Zone coordinates are rough landmark positions - verify before real deployment. See `docs/device_ids.md`.

```bash
python -m pytest tests/test_schema.py tests/test_zones.py
```
