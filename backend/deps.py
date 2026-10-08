"""FastAPI dependencies: DB session, settings, API-key check."""
from __future__ import annotations

import secrets
from typing import Iterator, Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from shared.constants import API_KEY_HEADER

from .config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


def require_api_key(settings: Settings = Depends(get_settings),
                    x_api_key: Optional[str] = Header(default=None, alias=API_KEY_HEADER)) -> None:
    """401 if the X-API-Key header is absent, 403 if present but wrong."""
    if x_api_key is None:
        raise HTTPException(401, detail=f"missing {API_KEY_HEADER} header",
                            headers={"WWW-Authenticate": "ApiKey"})
    if not secrets.compare_digest(x_api_key.encode(), settings.api_key.encode()):
        raise HTTPException(403, detail="invalid API key")
