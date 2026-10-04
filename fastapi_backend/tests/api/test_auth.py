"""Signing in, first-run setup, refresh rotation, API tokens, throttling, password reset and the Host check."""

from __future__ import annotations

from app.domain import auth
from tests.helpers import login, make_user


def test_first_run_setup(app, client):
    r = client.get("/api/v1/auth/status")
    assert r.json() == {"setup_required": True, "wizard_pending": True, "passwords": True}
    assert client.get("/api/v1/auth/me").status_code == 401
    bad = {"code": "nope", "email": "ada@x.io", "password": "admin password 1"}
    assert client.post("/api/v1/auth/setup", json=bad).status_code == 403
    code = app.state.archive.setup_code
    r = client.post("/api/v1/auth/setup", json={"code": code, "email": "ada@x.io", "password": "admin password 1", "name": "Ada"})
    assert r.status_code == 200, r.text
    pair = r.json()
    assert pair["user"]["admin"] and pair["token_type"] == "bearer" and pair["expires_in"] > 0
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"}).json()
    assert me["user"]["email"] == "ada@x.io" and me["via"] == "access" and set(me["roles"]) == {"pods", "calls"}
    assert client.post("/api/v1/auth/setup", json={"code": code, "email": "b@x.io", "password": "another password"}).status_code == 403
    assert client.get("/api/v1/auth/status").json() == {"setup_required": False, "wizard_pending": True, "passwords": True}


def test_login_refresh_logout(client, db):
    make_user(db, "ada@x.io", "admin password 1", admin=True)
    assert client.post("/api/v1/auth/login", json={"email": "ada@x.io", "password": "wrong password!"}).status_code == 401
    pair = client.post("/api/v1/auth/login", json={"email": "ADA@x.io ", "password": "admin password 1"}).json()
    h = {"Authorization": f"Bearer {pair['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200

    # refresh rotates: the new token works, the old one keeps working only inside the grace window
    new = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert new.status_code == 200
    new = new.json()
    assert new["refresh_token"] != pair["refresh_token"]
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new['access_token']}"}).status_code == 200

    # a rotated token replayed after the grace window ends the whole session, including its access tokens
    old = auth.REUSE_GRACE_SECONDS
    auth.REUSE_GRACE_SECONDS = -1
    try:
        assert client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401
    finally:
        auth.REUSE_GRACE_SECONDS = old
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": new["refresh_token"]}).status_code == 401
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401

    # logout ends the session
    pair = client.post("/api/v1/auth/login", json={"email": "ada@x.io", "password": "admin password 1"}).json()
    h = {"Authorization": f"Bearer {pair['access_token']}"}
    assert client.post("/api/v1/auth/logout", json={"refresh_token": pair["refresh_token"]}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_garbage_and_disabled(client, db):
    uid = make_user(db, "ed@x.io", "editor password 1")
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401
    h = login(client, "ed@x.io", "editor password 1")
    auth.update_account(db, uid, disabled=True)
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "ed@x.io", "password": "editor password 1"}).status_code == 401


def test_api_tokens(client, db):
    make_user(db, "ada@x.io", "admin password 1", admin=True)
    h = login(client, "ada@x.io", "admin password 1")
    tok = client.post("/api/v1/tokens", json={"name": "ci", "scope": "read"}, headers=h).json()
    bearer = {"Authorization": f"Bearer {tok['token']}"}
    me = client.get("/api/v1/auth/me", headers=bearer).json()
    assert (me["via"], me["scope"]) == ("token", "read")
    assert client.post("/api/v1/tokens", json={"name": "x"}, headers=bearer).status_code == 403  # read-only
    listed = client.get("/api/v1/tokens", headers=h).json()
    assert [t["name"] for t in listed] == ["ci"] and "hash" not in listed[0]
    assert client.post("/api/v1/tokens", json={"name": "x", "scope": "admin"}, headers=h).status_code == 422
    assert client.delete(f"/api/v1/tokens/{tok['id']}", headers=h).status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer).status_code == 401


def test_throttling_and_host_check(client, db):
    make_user(db, "ada@x.io", "admin password 1", admin=True)
    for _ in range(8):
        client.post("/api/v1/auth/login", json={"email": "ada@x.io", "password": "wrong password!"})
    assert client.post("/api/v1/auth/login", json={"email": "ada@x.io", "password": "admin password 1"}).status_code == 429
    assert client.get("/api/v1/auth/status", headers={"host": "evil.example"}).status_code == 400


def test_password_reset(client, db, caplog):
    make_user(db, "ada@x.io", "admin password 1")
    assert client.post("/api/v1/auth/password/forgot", json={"email": "nobody@x.io"}).status_code == 200
    raw, _ = auth.start_reset(db, "ada@x.io")
    assert client.post("/api/v1/auth/password/reset", json={"token": "bogus", "password": "a new password 1"}).status_code == 400
    assert client.post("/api/v1/auth/password/reset", json={"token": raw, "password": "short"}).status_code == 400
    assert client.post("/api/v1/auth/password/reset", json={"token": raw, "password": "a new password 1"}).status_code == 200
    assert client.post("/api/v1/auth/password/reset", json={"token": raw, "password": "a new password 2"}).status_code == 400  # once
    login(client, "ada@x.io", "a new password 1")
