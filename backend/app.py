"""Aeronex backend application factory (see main.py for the ASGI entry point)."""
from __future__ import annotations

import dataclasses
import logging
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import DEFAULT_API_KEY, Settings
from .db import init_db, make_engine, make_session_factory
from .live import LiveHub
from .auth import LoginLimiter, read_token
from .routers import auth as auth_router
from .routers import devices, events, insights, public, telemetry, zones

log = logging.getLogger("airdna.backend")


class _NoCacheStatic(StaticFiles):
    """The website must never be served stale: a browser holding an old app.js would send Login to the wrong place."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


def _field_errors(exc: RequestValidationError) -> list[dict]:
    """Flatten pydantic errors to {field, message, type}, e.g. field='gps.fix'."""
    out = []
    for e in exc.errors():
        loc = [str(x) for x in e["loc"] if x != "body"]
        out.append({"field": ".".join(loc) or "(payload)", "message": e["msg"], "type": e["type"]})
    return out


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.auth_secret:                 # never sign tokens with an empty key
        settings = dataclasses.replace(settings, auth_secret=secrets.token_urlsafe(32))
    if settings.api_key == DEFAULT_API_KEY:
        log.warning("using the default dev API key; set AIRDNA_API_KEY for anything but local dev")

    app = FastAPI(title="Aeronex backend", version="0.1.0",
                  description="Ingestion + read API for the zonal AQI prototype (data contract v1.0).")
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    init_db(engine, factory, settings.auto_create_db)

    app.state.settings, app.state.engine = settings, engine
    app.state.session_factory, app.state.live_hub = factory, LiveHub()
    app.state.login_limiter = LoginLimiter()

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": "validation_failed",
                                                      "errors": _field_errors(exc)})

    for r in (telemetry, zones, devices, events, auth_router, public, insights):
        app.include_router(r.router)

    @app.get("/healthz", tags=["meta"])
    def healthz():
        return {"status": "ok", "live_clients": app.state.live_hub.count}

    @app.websocket("/v1/live")
    async def live(ws: WebSocket):
        """Pushes {"type": "telemetry_accepted", ...} for each accepted packet. Summaries only,
        never raw samples. (Zone status-change / event pushes arrive with the worker.)"""
        hub: LiveHub = app.state.live_hub
        if settings.require_auth and not read_token(settings.auth_secret, ws.query_params.get("token", "") or ws.cookies.get("aeronex_token", "")):
            await ws.close(code=1008)                # policy violation: login required
            return
        await hub.connect(ws)
        try:
            await ws.send_json({"type": "hello", "schema_version": "1.0"})
            while True:
                await ws.receive_text()          # we ignore client messages; this detects disconnects
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(ws)

    from . import proxy
    proxy.install(app, settings.dashboard_upstream)       # dashboard at /app/ (same origin as the website)

    # Landing website (site/), served at "/" LAST so it never shadows the API, /docs or /healthz.
    site_dir = Path(__file__).resolve().parent.parent / "site"
    if site_dir.is_dir():
        app.mount("/", _NoCacheStatic(directory=site_dir, html=True), name="site")

    return app


