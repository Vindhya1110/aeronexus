"""Fake LoRa gateway: forwards payloads over HTTP exactly as a real gateway would.

Behaviour mirrors a store-and-forward gateway:
  * 2xx            -> delivered
  * 409            -> backend says "already have it"; treated as delivered
  * other 4xx      -> payload is bad; counted as rejected, NOT retried
  * 5xx / no route -> buffered and retried (oldest first) before any new packet
"""
from __future__ import annotations

import logging
from collections import Counter, deque

import requests

from shared.constants import API_KEY_HEADER, TELEMETRY_PATH

log = logging.getLogger(__name__)


class GatewayClient:
    def __init__(self, base_url: str, api_key: str, timeout_s: float = 5.0, buffer_max: int = 10_000):
        self.url = base_url.rstrip("/") + TELEMETRY_PATH
        self.session = requests.Session()
        self.session.headers.update({API_KEY_HEADER: api_key, "Content-Type": "application/json"})
        self.timeout_s = timeout_s
        self.buffer: deque[dict] = deque(maxlen=buffer_max)
        self.stats: Counter = Counter()

    def _try_post(self, wire: dict) -> str:
        """Returns 'ok' | 'rejected' | 'retry'."""
        try:
            r = self.session.post(self.url, json=wire, timeout=self.timeout_s)
        except requests.RequestException as exc:
            log.warning("backend unreachable (%s); buffering", exc.__class__.__name__)
            return "retry"
        if r.status_code in (200, 201, 202, 409):
            return "ok"
        if r.status_code >= 500:
            return "retry"
        log.info("rejected %s seq=%s -> HTTP %s %s", wire.get("device_id"), wire.get("sequence"),
                 r.status_code, r.text[:160])
        self.stats[f"http_{r.status_code}"] += 1
        return "rejected"

    def flush(self) -> None:
        while self.buffer:
            res = self._try_post(self.buffer[0])
            if res == "retry":
                return
            self.buffer.popleft()
            self.stats["delivered" if res == "ok" else "rejected"] += 1

    def send(self, wire: dict) -> None:
        self.flush()
        if self.buffer:                       # backend still down: keep order
            self.buffer.append(wire)
            self.stats["buffered"] += 1
            return
        res = self._try_post(wire)
        if res == "retry":
            self.buffer.append(wire)
            self.stats["buffered"] += 1
        else:
            self.stats["delivered" if res == "ok" else "rejected"] += 1
