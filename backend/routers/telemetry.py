"""POST /v1/telemetry - the ingestion endpoint a real LoRa gateway will also use.

Delivery contract (matches simulator/gateway.py):
  202 accepted | 409 duplicate (device_id, sequence) | 401/403 auth | 422 schema violation
  5xx -> sender buffers and retries.
Only validation + one DB transaction happen here; analytics run in the worker.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from shared.schema import TelemetryPayload

from ..deps import require_api_key
from ..ingest import DuplicatePacket, store_telemetry

router = APIRouter(prefix="/v1", tags=["telemetry"])


@router.post("/telemetry", status_code=202, dependencies=[Depends(require_api_key)])
async def post_telemetry(request: Request, payload: TelemetryPayload, background: BackgroundTasks):
    raw = await request.json()                      # verbatim body, kept immutable in raw_measurements
    try:
        stored = await run_in_threadpool(store_telemetry, request.app.state.session_factory, payload, raw)
    except DuplicatePacket:
        return JSONResponse(status_code=409, content={
            "status": "duplicate", "device_id": payload.device_id, "sequence": payload.sequence})

    # fan-out to WebSocket clients happens after the 202 is sent
    background.add_task(request.app.state.live_hub.broadcast, {
        "type": "telemetry_accepted",
        "device_id": stored.device_id,
        "sequence": stored.sequence,
        "timestamp_utc": stored.timestamp_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": stored.zone_id,
        "quality_flags": stored.quality_flags,
    })
    return {"status": "accepted", "device_id": stored.device_id, "sequence": stored.sequence,
            "zone_id": stored.zone_id, "zone_source": stored.zone_source,
            "quality_flags": stored.quality_flags}
