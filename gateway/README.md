# gateway/ - ESP32 node -> backend (serial bridge)

The real gateway for the prototype: reads the node's `CSV_DATA` lines from USB serial and POSTs data-contract v1.0 packets to `POST /v1/telemetry`, exactly
as the simulator does (same store-and-forward client: 2xx/409 = delivered, other 4xx = rejected, 5xx / no connection = buffered and resent in order).

```powershell
# one-off
.\venv\Scripts\python.exe -m pip install -r requirements.txt          # includes pyserial
.\venv\Scripts\python.exe -m gateway.serial_bridge --list-ports        # find the node (COM5, /dev/ttyACM0 ...)

# run it against a running backend (start the stack first: tools\run_demo.py)
.\venv\Scripts\python.exe -m gateway.serial_bridge --port COM5 --device-id ZONE_HITEC_90 --url http://localhost:8000

# or let the launcher start everything, bridge included
.\venv\Scripts\python.exe tools\run_demo.py --hardware COM5                         # any OS
powershell -ExecutionPolicy Bypass -File .\infra\run_demo.ps1 -HardwarePort COM5    # Windows background launcher
```

No hardware at hand? Test the whole path with a synthetic node: `--simulate` (or `--hardware sim` / `-HardwarePort sim`). `--dry-run` prints the packets
instead of sending them; `--replay file.txt` replays a captured serial log.

| option | default | meaning |
|---|---|---|
| `--device-id` | `ZONE_HITEC_90` | `ZONE_<ZONECODE>_<NN>`; the zone code (`PGPT CHNAM HITEC MGBSEC SANATH IDAPASH`) decides the zone. Use a number not taken by a simulated node (they use 01-02) |
| `--interval-s` | 30 | send the **mean** of the node's readings every N seconds (measured in node time) |
| `--warmup-s` | 300 | node uptime below which packets carry `quality.warmup=true` (also after a reboot) |
| `--lat --lon` | none | surveyed install position (the node has no GPS); without them the packet says "no GPS" and the backend uses the device-ID zone |
| `--mode` | `auto` | `auto`: send `MODE,warning` to the node while its zone AQI is above 300 (back to normal below 270); or force `normal` / `warning` / `silent` |
| `--fresh` | off | ignore the saved sequence number (use after wiping the database; the launchers do this because the demo database is recreated) |
| `--url`, `--api-key` | `http://localhost:8000`, `dev-key` | backend and the gateway key (`AIRDNA_BACKEND_URL`, `AIRDNA_API_KEY`) |

## Mapping (`gateway/hardware.py`)

| node | contract field |
|---|---|
| MQ-2 / MQ-9 / MQ-135 (window mean, rounded) | `gas_proxy.mq2_adc / mq9_adc / mq135_adc` (raw proxy counts; a failed channel is `null` + its flag) |
| BMP180 temperature (DHT11 as fallback) | `env.temperature_c` |
| DHT11 humidity | `env.humidity_pct` |
| BMP180 pressure | `env.pressure_hpa` |
| *(no PM sensor)* | `pm.pm25_ugm3 / pm10_ugm3 = null`, `sensor_flags` bit "pm_sensor" |
| *(no GPS module)* | `gps.fix=false`, lat/lon null, bit "gps_module" (unless `--lat/--lon`) |
| node `millis` | `quality.warmup` (uptime below `--warmup-s`) |
| PC clock | `timestamp_utc` (window end) |
| thermistors, altitude | not forwarded (no contract field) |

Nothing is ever invented: an unavailable value is `null` plus a flag. Sequence numbers are saved in `data/hw_state.json`, so a restart never reuses one
(the backend would answer 409). If the node goes silent the bridge sends nothing and the backend shows it offline.
Tests: `tests/test_hardware_bridge.py` (parsing, mapping, windows, sequence resume, backend + worker with the node in the fleet).
