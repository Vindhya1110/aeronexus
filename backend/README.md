# backend/ - ingestion + read API (FastAPI, SQLAlchemy, SQLite)

Everything the dashboard and worker need. A real LoRa gateway talks to the same
`POST /v1/telemetry` the simulator uses; nothing here changes when hardware arrives.

## Run (from the repo root)

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000        # docs at http://localhost:8000/docs
python -m simulator live --url http://localhost:8000  # feed it (separate terminal)
python -m pytest tests/test_backend_ingest.py tests/test_backend_api.py
```

## Environment variables

| var | default | meaning |
|---|---|---|
| `AIRDNA_API_KEY` | `dev-key` | required value of the `X-API-Key` header on `POST /v1/telemetry` (same var the simulator reads) |
| `AIRDNA_DATABASE_URL` | `sqlite:///<repo>/data/airdna.db` | any SQLAlchemy URL; e.g. `postgresql+psycopg://user:pw@host/airdna` |
| `AIRDNA_OFFLINE_AFTER_S` | `300` | no packet *received* for this long => device offline / zone stale |
| `AIRDNA_CURRENT_WINDOW_MIN` | `10` | window for the "current" zone PM mean |
| `AIRDNA_AUTO_CREATE_DB` | `1` | create tables at startup; set `0` when using alembic |

Default key is for local dev only (a warning is logged).

## Accounts and roles (login)

Two roles: **user** (public: zones, map, forecasts) and **admin** (official: everything + devices, events, source
insight, account management). Stdlib only: scrypt-hashed passwords, HMAC-signed bearer tokens (8 h), 5-failures/5-min
lockout per username, uniform "invalid username or password" (no username enumeration).

| | |
|---|---|
| `POST /v1/auth/login` | `{username, password}` -> `{token, expires_at, user}` |
| `GET /v1/auth/me` | current account (Bearer token) |
| `POST /v1/auth/register` | public sign-up; **always** creates role `user` (disable: `AIRDNA_ALLOW_SIGNUP=0`) |
| `GET/POST /v1/auth/users`, `PATCH /v1/auth/users/{username}` | admin only: list, create (any role), deactivate / reactivate / change role / reset password; an admin cannot deactivate or demote themselves |

Send `Authorization: Bearer <token>`. **Enforcement is opt-in:** with `AIRDNA_REQUIRE_AUTH=1` the read API requires a
token (`/v1/zones*` any account; `/v1/devices`, `/v1/events` admin only; `WS /v1/live?token=...`); `/healthz` stays open and
`POST /v1/telemetry` keeps using the gateway `X-API-Key`. Default is off so local dev and the older tests stay open.
The dashboard always requires sign-in, and the demo scripts run the backend with enforcement on.

| var | default | meaning |
|---|---|---|
| `AIRDNA_AUTH_SECRET` | random per process | token signing key; set it for stable sessions across restarts |
| `AIRDNA_TOKEN_TTL_S` | `28800` | session length |
| `AIRDNA_REQUIRE_AUTH` | `0` | enforce login on the read API |
| `AIRDNA_ALLOW_SIGNUP` | `1` | public `/register` |

The first admin comes from the CLI (nobody can sign up as one):
`python tools/create_user.py --username officer1 --role admin --name "Duty Officer"` (prompts for the password;
`--generate` prints a random one once). Table `users` (migration `0003`).
Limits: lockout counters are per process; passwords have a length rule only; tokens cannot be revoked individually
(deactivating the account does revoke them, because every request re-checks it).

## Endpoints

| | |
|---|---|
| `POST /v1/telemetry` | needs `X-API-Key`. **202** accepted · **409** duplicate `(device_id, sequence)` · **401** header missing · **403** wrong key · **422** schema violation `{"error":"validation_failed","errors":[{"field":"gps.fix","message":"Field required","type":"missing"}]}` |
| `GET /v1/zones` | per zone: raw PM mean over the current window (+ counts and flagged-sample counts), `aqi` (+`detail`) / `source_hypothesis` / `coverage` (null until `python -m worker` has run), `last_seen_utc`, `stale`, device counts |
| `GET /v1/zones/{id}/history` | `from`, `to` (UTC; default `to` = newest sample, `from` = `to`-6h), `fields=pm25_ugm3,mq9_adc,...`, `max_points` (default 300) or `bucket_s`, optional `device_id`, `include_smoothed` (default true: adds a separate per-bucket `smoothed` block with the worker's rolling-mean PM; raw `values` are never altered). Bucket means; per-bucket `n` and quality flags; MQ fields carry a "proxy index" note |
| `GET /v1/zones/{id}/forecast` | latest worker forecast (15/30/60 min, `method`, `model_version`); `status:"unavailable"` until Phase 3 |
| `GET /v1/zones/{id}/derived` | worker output history (AQI, smoothed PM, source label/confidence) by `window_end`, oldest first; for evaluation and plotting |
| `GET /v1/devices` | last packet (device clock) and last received (server clock), firmware/calibration version, sensor faults, warm-up, GPS validity, `online`/`offline`. `battery` is null (not in contract v1.0) |
| `GET /v1/events` | filters `zone_id`, `status`, `event_type`, `since`, `limit`; empty until Phase 3 |
| `WS /v1/live` | `{"type":"hello"}` on connect, then `{"type":"telemetry_accepted","device_id","sequence","timestamp_utc","zone_id","quality_flags"}` per accepted packet (summaries, never raw samples) |
| `GET /healthz` | liveness |

Read endpoints are unauthenticated for now (auth beyond the ingest key was not specified).

## Behaviour worth knowing

* **Ingest does only validation + one DB transaction**; WebSocket fan-out runs after the 202; no analytics.
* **Zone resolution** (`ingest.resolve_zone`): `gps_valid` -> nearest seeded zone within its radius
  (`zone_source=gps`; a bus outside every zone gets none). Otherwise fixed nodes use the home zone encoded in
  their device ID (`device_home`) and buses use their last known zone (`last_known`). Never guessed beyond that.
* **Raw is immutable**: `raw_measurements` holds the request body verbatim, insert-only, unique on
  `(device_id, sequence)`. `measurements` is the typed flattened copy (nulls stay null). Derived values only go to
  `derived_measurements`, `events`, `forecasts`.
* **Quality is surfaced, not dropped**: warm-up, GPS-invalid, sensor-fault and out-of-range values are accepted
  and listed in `quality_flags`.
* **Online/offline** uses server receive time, so replaying old data does not fake liveness.
* Devices are auto-registered on first packet; zones are seeded from `shared/zones.py` at startup.
* Out-of-order packets are stored but only a newer timestamp moves a device's "last packet".

## Tables

`devices, zones, raw_measurements, measurements, derived_measurements, events, forecasts, calibrations`
(`models.py`; portable types only, so Postgres is a URL change).

Migrations (`0001` initial, `0002` adds `derived_measurements.aqi_detail`; on a DB created by an older `create_all`,
run `python -m alembic -c backend/alembic.ini stamp 0001` first):

 `AIRDNA_DATABASE_URL=... python -m alembic -c backend/alembic.ini upgrade head`
(initial revision `0001`). For Postgres also install a driver such as `psycopg[binary]`.

## Layout

`app.py` factory · `main.py` ASGI entry · `config.py` · `db.py` · `models.py` · `ingest.py` · `deps.py` (API key) ·
`live.py` (WS hub) · `routers/` · `migrations/`
