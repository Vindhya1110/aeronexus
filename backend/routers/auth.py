"""/v1/auth: login, who-am-I, self sign-up (role 'user'), and account management for admins."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import (COOKIE, ROLES, current_user, hash_password, make_token, require_admin, validate_new_credentials,
                    verify_password)
from ..config import Settings
from ..deps import get_db, get_settings
from ..models import User, utcnow
from .devices import iso

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# a throwaway hash so unknown usernames cost the same time as wrong passwords
_DUMMY = hash_password("not-a-real-password")


class Login(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class Register(BaseModel):
    username: str
    password: str = Field(max_length=256)
    display_name: str = Field(default="", max_length=80)


class NewAccount(Register):
    role: str = "user"


class AccountPatch(BaseModel):
    active: Optional[bool] = None
    role: Optional[str] = None
    password: Optional[str] = Field(default=None, max_length=256)
    display_name: Optional[str] = Field(default=None, max_length=80)


def _public(u: User) -> dict:
    return {"username": u.username, "display_name": u.display_name or u.username, "role": u.role,
            "active": u.active, "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at)}


@router.post("/login")
def login(body: Login, request: Request, response: Response, settings: Settings = Depends(get_settings), db: Session = Depends(get_db)):
    key = body.username.lower()
    limiter = request.app.state.login_limiter
    if limiter.locked(key):
        raise HTTPException(429, detail="too many failed attempts; try again in a few minutes")
    user = db.scalar(select(User).where(func.lower(User.username) == key))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY)
    if not (user and ok and user.active):
        limiter.fail(key)
        raise HTTPException(401, detail="invalid username or password")
    limiter.clear(key)
    user.last_login_at = utcnow()
    db.commit()
    token, exp = make_token(settings.auth_secret, user.username, user.role, settings.token_ttl_s)
    # One browser session shared by the website (/) and the dashboard (/app/): HttpOnly so page scripts cannot read it
    response.set_cookie(COOKIE, token, max_age=settings.token_ttl_s, httponly=True, samesite="lax", path="/")
    return {"token": token, "expires_at": exp, "user": _public(user)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/logout", include_in_schema=False)
def logout_redirect(next: str = "/"):
    """Plain link target for the dashboard's Sign out: clears the session cookie then returns to a SAME-SITE path."""
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(target, status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _public(user)


def _create(db: Session, username: str, password: str, display_name: str, role: str) -> dict:
    validate_new_credentials(username, password)
    if role not in ROLES:
        raise HTTPException(422, detail=f"role must be one of {ROLES}")
    if db.scalar(select(User).where(func.lower(User.username) == username.lower())):
        raise HTTPException(409, detail="username already taken")
    u = User(username=username, display_name=display_name, role=role, password_hash=hash_password(password))
    db.add(u)
    db.commit()
    return _public(u)


@router.post("/register", status_code=201)
def register(body: Register, settings: Settings = Depends(get_settings), db: Session = Depends(get_db)):
    """Public sign-up. Always creates a plain 'user'; officials/admins are created by an admin only."""
    if not settings.allow_signup:
        raise HTTPException(403, detail="self sign-up is disabled")
    return _create(db, body.username, body.password, body.display_name, "user")


@router.get("/users")
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {"users": [_public(u) for u in db.scalars(select(User).order_by(User.created_at))]}


@router.post("/users", status_code=201)
def create_user(body: NewAccount, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _create(db, body.username, body.password, body.display_name, body.role)


@router.patch("/users/{username}")
def update_user(username: str, body: AccountPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.username == username))
    if not u:
        raise HTTPException(404, detail="no such user")
    if u.username == admin.username and (body.active is False or (body.role and body.role != "admin")):
        raise HTTPException(409, detail="admins cannot deactivate or demote themselves")
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(422, detail=f"role must be one of {ROLES}")
        u.role = body.role
    if body.active is not None:
        u.active = body.active
    if body.display_name is not None:
        u.display_name = body.display_name
    if body.password is not None:
        validate_new_credentials(u.username, body.password)
        u.password_hash = hash_password(body.password)
    db.commit()
    return _public(u)
