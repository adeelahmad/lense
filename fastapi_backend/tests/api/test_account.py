"""Your own account (docs/api.md): change your name, and your password with your current one."""

from __future__ import annotations

from tests.helpers import make_user

PW = "viewer password 1"


def _sign_in(client, password=PW):
    r = client.post("/api/v1/auth/login", json={"email": "vic@x.io", "password": password})
    assert r.status_code == 200, r.text
    pair = r.json()
    return {"Authorization": f"Bearer {pair['access_token']}"}, pair["refresh_token"]


def test_changing_your_name(client, db):
    make_user(db, "vic@x.io", PW, roles={"pods": "viewer"})
    h, _ = _sign_in(client)
    r = client.patch("/api/v1/auth/me", headers=h, json={"name": "  Vic   Viewer "})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["name"] == "Vic Viewer"
    assert client.get("/api/v1/auth/me", headers=h).json()["user"]["name"] == "Vic Viewer"
    assert client.patch("/api/v1/auth/me", headers=h, json={"name": "   "}).status_code == 400
    assert client.patch("/api/v1/auth/me", headers=h, json={"name": "x" * 81}).status_code == 422
    read = client.post("/api/v1/tokens", headers=h, json={"name": "ro", "scope": "read"}).json()["token"]
    assert client.patch("/api/v1/auth/me", headers={"Authorization": f"Bearer {read}"}, json={"name": "X"}).status_code == 403
    assert client.patch("/api/v1/auth/me", json={"name": "X"}).status_code == 401


def test_changing_your_password(client, db):
    make_user(db, "vic@x.io", PW, roles={"pods": "viewer"})
    h, mine = _sign_in(client)
    _, elsewhere = _sign_in(client)  # the same person on another device
    write = client.post("/api/v1/tokens", headers=h, json={"name": "rw", "scope": "write"}).json()["token"]
    url = "/api/v1/auth/password"
    good = {"current_password": PW, "new_password": "a brand new password"}
    # API tokens can't; the current password must be right; the new one long enough and new
    assert client.post(url, headers={"Authorization": f"Bearer {write}"}, json=good).status_code == 403
    r = client.post(url, headers=h, json={**good, "current_password": "not it at all"})
    assert (r.status_code, r.json()["detail"]) == (400, "Your current password is wrong.")
    assert client.post(url, headers=h, json={**good, "new_password": "short"}).status_code == 400
    assert client.post(url, headers=h, json={**good, "new_password": PW}).status_code == 400
    client.post("/api/v1/auth/password/forgot", json={"email": "vic@x.io"})
    assert db.values("SELECT VALUE id FROM password_reset")

    assert client.post(url, headers=h, json=good).status_code == 200
    # this session stays; the other device is signed out; reset links are void; API tokens keep working
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": mine}).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": elsewhere}).status_code == 401
    assert not db.values("SELECT VALUE id FROM password_reset")
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {write}"}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "vic@x.io", "password": PW}).status_code == 401
    _sign_in(client, "a brand new password")
    audit = db.rows("SELECT action, target FROM audit_log WHERE action = 'password.change'")
    assert len(audit) == 1 and audit[0]["target"].startswith("account:")

    # guessing is throttled like signing in
    h, _ = _sign_in(client, "a brand new password")
    for _ in range(8):
        client.post(url, headers=h, json={"current_password": "a guess, wrong", "new_password": "whatever it is 1"})
    assert client.post(url, headers=h, json={"current_password": "a brand new password", "new_password": "x" * 12}).status_code == 429
