"""Accounts: login, roles, sign-up, admin management, optional read-API enforcement."""
from __future__ import annotations

import time

import pytest

from backend.auth import hash_password, make_token, read_token, verify_password
from backend.models import User

PW = "correct-horse-9"


def add_user(client, username, role, password=PW, active=True):
    with client.app.state.session_factory() as db:
        db.add(User(username=username, display_name=username.title(), role=role,
                    password_hash=hash_password(password), active=active))
        db.commit()


def login(client, username, password=PW):
    return client.post("/v1/auth/login", json={"username": username, "password": password})


def bearer(client, username, password=PW):
    return {"Authorization": f"Bearer {login(client, username, password).json()['token']}"}


def test_password_hash_roundtrip_and_salting():
    h1, h2 = hash_password(PW), hash_password(PW)
    assert h1 != h2 and PW not in h1
    assert verify_password(PW, h1) and not verify_password("wrong-password", h1)
    assert not verify_password(PW, "garbage")


def test_token_signature_expiry_and_tamper():
    tok, _ = make_token("s3cret", "ann", "user", 60)
    assert read_token("s3cret", tok)["sub"] == "ann"
    assert read_token("other-secret", tok) is None
    body, sig = tok.split(".")
    assert read_token("s3cret", body + "." + sig[:-2] + "AA") is None
    expired, _ = make_token("s3cret", "ann", "user", -5)
    assert read_token("s3cret", expired) is None


def test_login_success_and_me(client):
    add_user(client, "asha", "user")
    r = login(client, "asha")
    assert r.status_code == 200 and r.json()["user"]["role"] == "user"
    assert "password" not in r.text and "scrypt" not in r.text
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.json()["username"] == "asha"


def test_login_failures_are_uniform(client):
    add_user(client, "asha", "user")
    wrong_pw, unknown = login(client, "asha", "nope-nope-1"), login(client, "ghost")
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()                      # no username enumeration


def test_lockout_after_repeated_failures(client):
    add_user(client, "asha", "user")
    for _ in range(5):
        assert login(client, "asha", "bad-password-1").status_code == 401
    assert login(client, "asha").status_code == 429               # even the right password, until the window passes


def test_inactive_account_cannot_login_or_use_old_token(client):
    add_user(client, "asha", "user")
    h = bearer(client, "asha")
    with client.app.state.session_factory() as db:
        db.query(User).filter_by(username="asha").update({"active": False})
        db.commit()
    assert login(client, "asha").status_code == 401
    assert client.get("/v1/auth/me", headers=h).status_code == 401


def test_me_requires_token(client):
    assert client.get("/v1/auth/me").status_code == 401
    assert client.get("/v1/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_signup_creates_plain_user_only(client):
    r = client.post("/v1/auth/register", json={"username": "newbie", "password": PW, "display_name": "N"})
    assert r.status_code == 201 and r.json()["role"] == "user"
    r = client.post("/v1/auth/register", json={"username": "sneaky", "password": PW, "role": "admin"})
    assert r.status_code == 201 and r.json()["role"] == "user"    # extra role field is ignored
    assert client.post("/v1/auth/register", json={"username": "NEWBIE", "password": PW}).status_code == 409
    assert client.post("/v1/auth/register", json={"username": "x", "password": PW}).status_code == 422
    assert client.post("/v1/auth/register", json={"username": "okname", "password": "short"}).status_code == 422


def test_signup_can_be_disabled(make_client):
    c = make_client(allow_signup=False)
    assert c.post("/v1/auth/register", json={"username": "newbie", "password": PW}).status_code == 403


def test_admin_manages_accounts_users_cannot(client):
    add_user(client, "boss", "admin")
    add_user(client, "asha", "user")
    admin, user = bearer(client, "boss"), bearer(client, "asha")
    assert client.get("/v1/auth/users", headers=user).status_code == 403
    assert client.post("/v1/auth/users", headers=user, json={"username": "evil1", "password": PW,
                                                              "role": "admin"}).status_code == 403
    r = client.post("/v1/auth/users", headers=admin, json={"username": "officer2", "password": PW, "role": "admin"})
    assert r.status_code == 201 and r.json()["role"] == "admin"
    assert {u["username"] for u in client.get("/v1/auth/users", headers=admin).json()["users"]} == {"boss", "asha", "officer2"}
    assert client.patch("/v1/auth/users/asha", headers=admin, json={"password": "brand-new-pw1"}).status_code == 200
    assert login(client, "asha", "brand-new-pw1").status_code == 200
    assert client.patch("/v1/auth/users/asha", headers=admin, json={"active": False}).json()["active"] is False
    assert client.patch("/v1/auth/users/boss", headers=admin, json={"active": False}).status_code == 409
    assert client.patch("/v1/auth/users/boss", headers=admin, json={"role": "user"}).status_code == 409


def test_read_api_open_by_default(client):
    assert client.get("/v1/zones").status_code == 200 and client.get("/v1/events").status_code == 200


@pytest.fixture
def locked(make_client):
    c = make_client(require_auth=True)
    add_user(c, "boss", "admin")
    add_user(c, "asha", "user")
    return c


def test_enforcement_by_role(locked):
    assert locked.get("/healthz").status_code == 200              # liveness stays open
    for path in ("/v1/zones", "/v1/devices", "/v1/events"):
        assert locked.get(path).status_code == 401
    user, admin = bearer(locked, "asha"), bearer(locked, "boss")
    assert locked.get("/v1/zones", headers=user).status_code == 200
    assert locked.get("/v1/devices", headers=user).status_code == 403
    assert locked.get("/v1/events", headers=user).status_code == 403
    assert locked.get("/v1/devices", headers=admin).status_code == 200
    assert locked.get("/v1/events", headers=admin).status_code == 200


def test_ingest_still_uses_api_key_not_login(locked, auth, valid_payload):
    assert locked.post("/v1/telemetry", json=valid_payload, headers=auth).status_code == 202


def test_websocket_requires_token_when_enforced(locked):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with locked.websocket_connect("/v1/live"):
            pass
    tok = login(locked, "asha").json()["token"]
    with locked.websocket_connect(f"/v1/live?token={tok}") as ws:
        assert ws.receive_json()["type"] == "hello"


def test_create_user_cli_generate(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "AIRDNA_DATABASE_URL": f"sqlite:///{(tmp_path / 'c.db').as_posix()}"}
    r = subprocess.run([sys.executable, str(root / "tools" / "create_user.py"), "--username", "officer9",
                        "--role", "admin", "--generate", "--quiet"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    pw = r.stdout.strip()
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.config import Settings
    c = TestClient(create_app(Settings(database_url=env["AIRDNA_DATABASE_URL"])))
    ok = c.post("/v1/auth/login", json={"username": "officer9", "password": pw})
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "admin"
    c.app.state.engine.dispose()


# ---------------------------------------------------------------------------- shared browser session (cookie)
def test_login_sets_an_httponly_cookie_and_it_alone_authenticates(client):
    add_user(client, "asha", "user")
    r = login(client, "asha")
    cookie = r.headers["set-cookie"].lower()
    assert "aeronex_token=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert client.get("/v1/auth/me").json()["username"] == "asha"          # TestClient keeps the cookie: no Authorization header at all


def test_logout_clears_the_session(client):
    add_user(client, "asha", "user")
    login(client, "asha")
    assert client.get("/v1/auth/me").status_code == 200
    assert client.post("/v1/auth/logout").status_code == 200
    assert client.get("/v1/auth/me").status_code == 401


def test_logout_link_redirects_only_within_the_site(client):
    add_user(client, "asha", "user")
    login(client, "asha")
    r = client.get("/v1/auth/logout?next=/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    r = client.get("/v1/auth/logout?next=https://evil.example/", follow_redirects=False)
    assert r.headers["location"] == "/"                                    # no open redirect
    r = client.get("/v1/auth/logout?next=//evil.example", follow_redirects=False)
    assert r.headers["location"] == "/"
    assert client.get("/v1/auth/me").status_code == 401


def test_cookie_session_opens_the_enforced_read_api_by_role(make_client):
    c = make_client(require_auth=True)
    add_user(c, "boss", "admin")
    add_user(c, "asha", "user")
    assert c.get("/v1/zones").status_code == 401
    login(c, "asha")
    assert c.get("/v1/zones").status_code == 200 and c.get("/v1/devices").status_code == 403
    c.post("/v1/auth/logout")
    login(c, "boss")
    assert c.get("/v1/devices").status_code == 200


def test_signing_in_as_someone_else_replaces_the_session(client):
    add_user(client, "asha", "user")
    add_user(client, "boss", "admin")
    login(client, "asha")
    login(client, "boss")
    assert client.get("/v1/auth/me").json()["role"] == "admin"
