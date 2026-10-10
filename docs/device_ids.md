# Device ID convention

```
ZONE_<ZONECODE>_<NN>    fixed zonal node     e.g. ZONE_HITEC_01, ZONE_PGPT_02
BUS_<ROUTECODE>_<NN>    mobile bus node      e.g. BUS_CHNAM_01
```

* Regex (enforced in `shared/schema.py`): `^(ZONE|BUS)_[A-Z0-9]{1,12}_\d{2,3}$`
  - upper-case letters/digits only, so `_` is an unambiguous separator.
* `<ZONECODE>` is a `zone_id` from `shared/zones.py` (`PGPT`, `CHNAM`, `HITEC`, `MGBSEC`, `SANATH`, `IDAPASH`).
  The backend can therefore resolve a fixed node's home zone from its ID alone, which is
  the fallback when a packet has no GPS fix.
* `<ROUTECODE>` is our own route label (`CHNAM`, `MGBSEC`, `PGHTC`), **not** an official TSRTC route number.
* `<NN>` is a 2-3 digit install counter, unique within the zone / route.
* Fixed vs mobile is expressed *only* by the ID prefix and `gps.speed_kmh`; the payload schema is identical.
* `(device_id, sequence)` is the de-duplication key. `sequence` increments per packet and survives
  reboots (firmware stores it in flash); gateways may resend a packet with the same pair (store-and-forward).

## Hardware node

| device_id | where |
|---|---|
| ZONE_HITEC_90 (default; any `ZONE_<ZONECODE>_<NN>`) | the ESP32-S3 prototype node (`firmware/aeronex_node`), via `gateway/serial_bridge.py`; numbers 90+ are reserved for real hardware so they never clash with the simulated fleet |

## Current simulated fleet

| device_id | where |
|---|---|
| ZONE_PGPT_01 / 02 | Punjagutta-Patancheru corridor (Ameerpet end / Miyapur end) |
| ZONE_CHNAM_01 | Charminar-Nampally corridor (Madina) |
| ZONE_HITEC_01 | Hitec City |
| ZONE_MGBSEC_01 | MGBS-Secunderabad corridor (Musheerabad) |
| ZONE_SANATH_01 | Sanathnagar |
| ZONE_IDAPASH_01 | IDA Pashamylaram |
| BUS_CHNAM_01 / 02 | Charminar-Nampally route (~6 km) |
| BUS_MGBSEC_01 | MGBS-Secunderabad route (~8.5 km) |
| BUS_PGHTC_01 | Punjagutta-Hitec City route (~11 km) |

## `quality.sensor_flags` bitmask

A set bit means that sensor's value is unavailable in this packet and is sent as `null`
(never 0). See `shared/constants.py`.

| bit | value | sensor |
|---|---|---|
| 0 | 1 | PM sensor (pm25 + pm10) |
| 1 | 2 | MQ-2 |
| 2 | 4 | MQ-9 |
| 3 | 8 | MQ-135 |
| 4 | 16 | temperature / humidity |
| 5 | 32 | barometer |
| 6 | 64 | GPS module |

## Null vs missing

* **Key absent** -> malformed payload -> backend returns 422.
* **Key present, value `null`** -> sensor value unavailable (accepted, shown as a flag).
* No GPS fix -> `gps.fix=false`, `lat/lon/hdop/speed_kmh = null`, `quality.gps_valid=false`.
* Fix with HDOP > 5 -> `gps.fix=true`, `quality.gps_valid=false` (position kept but untrusted).
