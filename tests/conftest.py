"""Shared pytest fixtures: recorded payloads from shared/fixtures/."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "shared" / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def valid_payload() -> dict:
    return _load("valid_payload.json")


@pytest.fixture
def missing_fields_payload() -> dict:
    return _load("missing_required_fields.json")


@pytest.fixture
def invalid_gps_payload() -> dict:
    return _load("invalid_gps_fix.json")


@pytest.fixture
def duplicate_sequence_payloads() -> list[dict]:
    return _load("duplicate_sequence.json")


@pytest.fixture
def bus_payload() -> dict:
    return _load("bus_payload.json")


@pytest.fixture
def warmup_payload() -> dict:
    return _load("warmup_payload.json")


@pytest.fixture
def clone():
    return copy.deepcopy


# ---- backend fixtures ---------------------------------------------------------
API_KEY = "test-key"


@pytest.fixture
def make_client(tmp_path):
    """Factory: TestClient on a fresh SQLite file; keyword args override Settings."""
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.config import Settings

    made = []

    def _make(**overrides):
        settings = Settings(api_key=API_KEY, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
                            **overrides)
        c = TestClient(create_app(settings))
        made.append(c)
        return c

    yield _make
    for c in made:
        c.app.state.engine.dispose()


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def auth():
    return {"X-API-Key": API_KEY}
