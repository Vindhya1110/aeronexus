"""Constants shared by every Aeronex component (simulator, backend, worker, dashboard).

Keep this file dependency-free so firmware-side tooling could also import it.
"""
from __future__ import annotations

import re

SCHEMA_VERSION = "1.0"
TELEMETRY_PATH = "/v1/telemetry"
API_KEY_HEADER = "X-API-Key"

# --- Device ID convention (see docs/device_ids.md) --------------------------
#   ZONE_<ZONECODE>_<NN>   fixed zonal node, e.g. ZONE_HITEC_01
#   BUS_<ROUTECODE>_<NN>   mobile bus node,  e.g. BUS_CHNAM_02
DEVICE_ID_RE = re.compile(r"^(?P<kind>ZONE|BUS)_(?P<code>[A-Z0-9]{1,12})_(?P<seq>\d{2,3})$")

# --- quality.sensor_flags bitmask ------------------------------------------
# A set bit means "this sensor's value is unavailable/untrusted in this packet".
# The matching value is sent as null (never 0).
FLAG_PM = 1 << 0
FLAG_MQ2 = 1 << 1
FLAG_MQ9 = 1 << 2
FLAG_MQ135 = 1 << 3
FLAG_ENV_TH = 1 << 4      # temperature / humidity
FLAG_PRESSURE = 1 << 5
FLAG_GPS_MODULE = 1 << 6

SENSOR_FLAG_NAMES = {
    FLAG_PM: "pm_sensor",
    FLAG_MQ2: "mq2",
    FLAG_MQ9: "mq9",
    FLAG_MQ135: "mq135",
    FLAG_ENV_TH: "temp_humidity",
    FLAG_PRESSURE: "barometer",
    FLAG_GPS_MODULE: "gps_module",
}


def decode_sensor_flags(flags: int) -> list[str]:
    """Human-readable names of the sensors flagged in a sensor_flags bitmask."""
    return [name for bit, name in SENSOR_FLAG_NAMES.items() if flags & bit]


# --- Plausibility ranges -----------------------------------------------------
# Values outside these are NOT rejected by the schema and NOT dropped: they are
# stored as received and surfaced as "out_of_range" flags (truthfulness rule).
ADC_MAX = 4095  # ESP32-S3 12-bit ADC
PLAUSIBLE_RANGES = {
    "temperature_c": (-20.0, 60.0),
    "humidity_pct": (0.0, 100.0),
    "pressure_hpa": (300.0, 1100.0),
    "pm25_ugm3": (0.0, 1000.0),
    "pm10_ugm3": (0.0, 1500.0),
    "mq2_adc": (0, ADC_MAX),
    "mq9_adc": (0, ADC_MAX),
    "mq135_adc": (0, ADC_MAX),
}

# A fix with HDOP above this is reported by firmware as gps_valid=false.
HDOP_MAX_VALID = 5.0

# Bus nodes report 15-25 km/h cruising; anything above this speed means "mobile".
MOBILE_SPEED_KMH = 1.0

# Labelling rule: MQ readings are raw ADC *proxy indices*, never ppm / concentrations.
GAS_PROXY_LABEL = "gas proxy index (raw ADC counts, uncalibrated)"
