"""Accounts and sessions for Aeronex (stdlib only: scrypt password hashes, HMAC-signed bearer tokens).

Roles:  "user"  - public/citizen view (zones, map, forecast)
        "admin" - official / operator view (everything a user sees + source insight, events, node health, accounts)

Token = base64url(json{sub, role, exp}) + "." + base64url(HMAC-SHA256). Stateless; signed with AIRDNA_AUTH_SECRET.
Changing the secret (or deactivating the account) invalidates sessions.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .deps import get_db, get_settings
from .models import User

ROLES = ("user", "admin")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LEN = 8
_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)


# ---- passwords --------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:  # noqa: BLE001 - malformed hash = no match
        return False


def validate_new_credentials(username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise HTTPException(422, detail="username must be 3-32 chars: letters, digits, _ . -")
    if len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(422, detail=f"password must be at least {MIN_PASSWORD_LEN} characters")


# ---- tokens -----------------------------------------------------------------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(secret: str, username: str, role: str, ttl_s: int) -> tuple[str, int]:
    exp = int(time.time()) + ttl_s
    body = _b64(json.dumps({"sub": username, "role": role, "exp": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", exp


def read_token(secret: str, token: str) -> Optional[dict]:
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        claims = json.loads(_unb64(body))
        return claims if claims["exp"] > time.time() else None
    except Exception:  # noqa: BLE001
        return None


# ---- brute-force damping (per process, per username) ------------------------
class LoginLimiter:
    def __init__(self, max_fails: int = 5, window_s: int = 300):
        self.max_fails, self.window_s = max_fails, window_s
        self._fails: dict[str, deque] = defaultdict(deque)

    def _prune(self, key: str) -> deque:
        q, now = self._fails[key], time.time()
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def locked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.max_fails

    def fail(self, key: str) -> None:
        self._prune(key).append(time.time())

    def clear(self, key: str) -> None:
        self._fails.pop(key, None)


# ---- dependencies -----------------------------------------------------------
COOKIE = "aeronex_token"


def _bearer(authorization: Optional[str], cookie: Optional[str] = None) -> Optional[str]:
    """Bearer header first (API clients, the dashboard server), else the browser session cookie (website + dashboard share it)."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return cookie or None


def current_user(request: Request, authorization: Optional[str] = Header(default=None),
                 settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> User:
    """401 unless a valid bearer token (or session cookie) for an active account is presented."""
    token = _bearer(authorization, request.cookies.get(COOKIE))
    claims = read_token(settings.auth_secret, token) if token else None
    if not claims:
        raise HTTPException(401, detail="login required", headers={"WWW-Authenticate": "Bearer"})
    user = db.scalar(select(User).where(User.username == claims["sub"]))
    if user is None or not user.active:
        raise HTTPException(401, detail="account unavailable", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, detail="official/admin account required")
    return user


def read_access(level: str):
    """Router-level guard for the read API. No-op unless AIRDNA_REQUIRE_AUTH=1 (keeps local dev and the
    existing test-suite open). level 'user' = any signed-in account, 'admin' = officials only."""
    def dep(request: Request, authorization: Optional[str] = Header(default=None)) -> None:
        settings: Settings = request.app.state.settings
        if not settings.require_auth:
            return
        token = _bearer(authorization, request.cookies.get(COOKIE))
        claims = read_token(settings.auth_secret, token) if token else None
        if not claims:
            raise HTTPException(401, detail="login required", headers={"WWW-Authenticate": "Bearer"})
        if level == "admin" and claims.get("role") != "admin":
            raise HTTPException(403, detail="official/admin account required")
    return dep
