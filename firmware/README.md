# firmware/ - Aeronex sensor node (ESP32-S3 N16R8)

`aeronex_node/aeronex_node.ino` is the hardware team's sketch (`aeronex_wrong_thermistor_D0.ino`) with **one cosmetic change**: the OLED header says
`AERONEX NODE` instead of `AIR DNA FLEET` and shows the hardware mode set by the gateway (`!WARN` / `(mute)`). Sensor reading, pins, the on-flash CSV log
and the `CSV_DATA` serial line are untouched.

## Hardware (as wired in the sketch)

| Part | Pin(s) | Notes |
|---|---|---|
| MQ-135, MQ-2, MQ-9 gas sensors | GPIO 4, 5, 6 (ADC) | raw 12-bit counts, 11 dB attenuation. Sent as **gas proxy indices**, never as ppm |
| Thermistors (front, rear) | GPIO 7, 8 | shown on the serial report and OLED only; not in `CSV_DATA`, not forwarded to the backend (the data contract has no field) |
| DHT11 | GPIO 9 | temperature + humidity |
| BMP180 | SDA 18, SCL 17 (I2C bus 1) | temperature + station pressure (hPa) |
| SSD1306 OLED | SDA 11, SCL 12 (I2C bus 0), addr 0x3C | 3 rotating pages |
| Flash log | LittleFS `/sensor_log.csv` | one row per 2 s |

Arduino IDE: board **ESP32S3 Dev Module**, flash 16 MB, PSRAM OPI, USB CDC on boot enabled, 115200 baud. Libraries: Adafruit GFX, Adafruit SSD1306,
Adafruit BMP085 Library, DHT sensor library (+ Adafruit Unified Sensor), ESP32 core (provides LittleFS).

## What the node sends over USB serial

Every 2 s, besides a human-readable report, one machine-readable line (empty field = that sensor returned NaN):

```
CSV_DATA,<millis>,<mq135>,<mq2>,<mq9>,<dht_temp_c>,<dht_hum_pct>,<bmp_temp_c>,<pressure_hpa>,<altitude_m>
```
It accepts `MODE,normal`, `MODE,warning` or `MODE,silent` (answers `MODE_SET,<mode>`). The gateway sends `MODE,warning` while the node's zone AQI is above 300.

## How it joins the system

`gateway/serial_bridge.py` (PC or Raspberry Pi with the node on USB) reads `CSV_DATA`, averages a window (default 30 s), maps it to the data contract and
POSTs it to the backend like any gateway - see `gateway/README.md`. The node then appears on **Node health** and in its zone, and takes part in
store-and-forward, de-duplication and the crash-recovery guarantees.

## What this node can and cannot contribute (read before demoing)

* **No particulate (PM2.5 / PM10) sensor is fitted.** The payload therefore carries `pm = null` with the "PM sensor unavailable" flag. AQI, the AQI forecast, the
  early warning, the source attribution, hotspots and the heatmap are all **PM-based**, so they keep running on the PM-equipped (simulated) nodes and are
  **not** changed by this node. What it does add: real MQ-2 / MQ-9 / MQ-135 proxy readings, temperature, humidity and pressure, stored and visible on Node
  health, counted as a device of its zone, with its own online/offline status.
  To make it feed AQI, fit a PM sensor (PMS5003 / SDS011) and extend the sketch to print PM2.5 and PM10 - the bridge and the backend need no other change.
* **No GPS module.** Position is "unavailable" (flag set); a fixed node is placed in the zone named in its device ID (`ZONE_HITEC_90` -> Hitec City).
  If you know the install coordinates, pass `--lat/--lon` to the bridge (reported as a surveyed position).
* **No clock.** Timestamps are the PC's UTC clock when each window closes. (In the demo, the simulated fleet runs on a simulated December clock, so the
  node's real-time readings are stored but not time-aligned with the scripted story; in a real deployment everything shares the real clock.)
* **MQ sensors need warm-up** (the bridge marks packets `warmup` for the first 300 s after the node boots; a proper burn-in is 24-48 h) and are uncalibrated.
* Known firmware notes: the thermistor formula assumes a 3.3 V reference while the ADC at 11 dB saturates near 3.1 V (a few percent of error); the
  `hardwareMode` variable has no actuator (buzzer/LED) yet, only the OLED header marker.
