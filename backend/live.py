"""WebSocket fan-out for /v1/live."""
from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

log = logging.getLogger(__name__)


class LiveHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    @property
    def count(self) -> int:
        return len(self._clients)

    async def broadcast(self, message: dict) -> None:
        """Send to every client; drop clients that error or stall (never block ingestion)."""
        async def _send(ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2.0)
            except Exception:
                self.disconnect(ws)
        if self._clients:
            await asyncio.gather(*(_send(ws) for ws in list(self._clients)))
