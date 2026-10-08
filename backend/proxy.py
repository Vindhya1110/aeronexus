"""Serve the Streamlit dashboard under /app/ on the SAME origin/port as the website and API.

One website, one port: the browser only ever talks to the backend. HTTP requests for /app/* are streamed to the
dashboard process (default http://127.0.0.1:8501, started with --server.baseUrlPath=app) and its websocket
(/app/_stcore/stream) is bridged both ways. If the dashboard is down the visitor gets a friendly 503 page.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

log = logging.getLogger("airdna.proxy")

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}
DOWN = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Aeronex</title><body style=\"margin:0;min-height:100vh;display:grid;place-items:center;"
        "background:#06202b;color:#e6f6fb;font-family:Segoe UI,system-ui,sans-serif;text-align:center\">"
        "<div><h1>Dashboard is starting&hellip;</h1><p style=color:#9fc3cf>It will be back in a few seconds."
        "</p><p><a style=color:#5eead4 href=/app/>Retry</a> &middot; <a style=color:#5eead4 href=/>Home</a></p></div>"
        "<script>setTimeout(()=>location.reload(),4000)</script>")


def install(app: FastAPI, upstream: str) -> None:
    upstream = upstream.rstrip("/")
    ws_upstream = upstream.replace("http://", "ws://").replace("https://", "wss://")
    client = httpx.AsyncClient(base_url=upstream, timeout=httpx.Timeout(30.0, read=None), follow_redirects=False)

    app.router.on_shutdown.append(client.aclose)

    @app.get("/app", include_in_schema=False)
    def _slash():
        return RedirectResponse("/app/")

    @app.api_route("/app/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                   include_in_schema=False)
    async def http_proxy(request: Request, path: str):
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        headers["host"] = upstream.split("://", 1)[1]
        try:
            req = client.build_request(request.method, f"/app/{path}", params=request.query_params,
                                       headers=headers, content=request.stream())
            r = await client.send(req, stream=True)
        except httpx.HTTPError:
            return HTMLResponse(DOWN, status_code=503)
        out = {k: v for k, v in r.headers.items() if k.lower() not in HOP}
        return StreamingResponse(r.aiter_raw(), status_code=r.status_code, headers=out,
                                 background=BackgroundTask(r.aclose))

    @app.websocket("/app/{path:path}")
    async def ws_proxy(ws: WebSocket, path: str):
        subs = ws.scope.get("subprotocols") or []
        query = ws.scope.get("query_string", b"").decode()
        url = f"{ws_upstream}/app/{path}" + (f"?{query}" if query else "")
        try:
            hdrs = {"Origin": upstream}
            if ws.headers.get("cookie"):
                hdrs["Cookie"] = ws.headers["cookie"]            # the dashboard reads the shared login cookie from its websocket
            up = await websockets.connect(url, subprotocols=subs or None, max_size=None, additional_headers=hdrs)
        except Exception:  # noqa: BLE001 - upstream down: refuse the socket, the page shows its own error
            await ws.close(code=1011)
            return
        await ws.accept(subprotocol=up.subprotocol)

        async def to_up():
            try:
                while True:
                    m = await ws.receive()
                    if m["type"] == "websocket.disconnect":
                        break
                    await up.send(m["text"] if m.get("text") is not None else m["bytes"])
            finally:
                await up.close()

        async def to_client():
            try:
                async for msg in up:
                    if isinstance(msg, str):
                        await ws.send_text(msg)
                    else:
                        await ws.send_bytes(msg)
            finally:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass

        tasks = [asyncio.create_task(to_up()), asyncio.create_task(to_client())]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
