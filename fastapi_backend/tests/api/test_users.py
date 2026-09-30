"""Accounts (admins) and namespace members (owners)."""

from __future__ import annotations

from tests.helpers import login, make_user


def test_admins_manage_accounts(client, new_client, db):
    root = make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "root@x.io", "root password 1")
    people = {p["email"]: p for p in client.get("/api/v1/users", headers=h).json()}
    assert people["ed@x.io"]["roles"] == {"pods": "editor"} and people["root@x.io"]["admin"]
    assert "pw" not in people["ed@x.io"]

    r = client.post("/api/v1/users", json={"email": "New@x.io", "password": "new password 12", "name": "New"}, headers=h)
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    assert client.post("/api/v1/users", json={"email": "new@x.io", "password": "new password 12"}, headers=h).status_code == 400  # taken
    assert client.post("/api/v1/users", json={"email": "not an email", "password": "new password 12"}, headers=h).status_code == 400
    other = new_client()
    hn = login(other, "new@x.io", "new password 12")

    # you can't lock yourself out
    assert client.patch(f"/api/v1/users/{root}", json={"admin": False}, headers=h).status_code == 400
    assert client.patch(f"/api/v1/users/{root}", json={"disabled": True}, headers=h).status_code == 400
    assert client.patch("/api/v1/users/9999", json={"name": "x"}, headers=h).status_code == 404
    assert client.patch(f"/api/v1/users/{uid}", json={"name": "Renamed", "password": "another password 1"}, headers=h).status_code == 200
    assert other.get("/api/v1/auth/me", headers=hn).status_code == 401  # a new password signs them out everywhere
    login(other, "new@x.io", "another password 1")
    audit = client.get("/api/v1/audit", headers=h).json()
    upd = next(a for a in audit if a["action"] == "user.update")
    assert upd["target"] == f"account:{uid}" and upd["detail"] == {"name": "Renamed"}  # never the password
    assert any(a["action"] == "user.create" for a in audit)

    he = login(new_client(), "ed@x.io", "editor password 1")
    assert client.get("/api/v1/users", headers=he).status_code == 403
    assert client.post("/api/v1/users", json={"email": "z@x.io", "password": "zzz password 1"}, headers=he).status_code == 403


def test_owners_manage_members(client, new_client, db):
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    ed = make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    vi = make_user(db, "vi@x.io", "viewer password 1")
    h = login(client, "own@x.io", "owner password 1")
    he = login(new_client(), "ed@x.io", "editor password 1")
    members = {m["email"]: m["role"] for m in client.get("/api/v1/namespaces/pods/members", headers=h).json()}
    assert members == {"own@x.io": "owner", "ed@x.io": "editor"}
    assert client.get("/api/v1/namespaces/pods/members", headers=he).status_code == 403  # editors can't
    assert client.get("/api/v1/namespaces/calls/members", headers=h).status_code == 404  # invisible namespace
    assert client.get("/api/v1/namespaces/nope/members", headers=h).status_code == 404

    assert client.put("/api/v1/namespaces/pods/members", json={"email": "VI@x.io", "role": "viewer"}, headers=h).status_code == 200
    assert client.put("/api/v1/namespaces/pods/members", json={"account": ed, "role": "owner"}, headers=h).status_code == 200
    assert client.put("/api/v1/namespaces/pods/members", json={"email": "ghost@x.io", "role": "viewer"}, headers=h).status_code == 404
    assert client.put("/api/v1/namespaces/pods/members", json={"account": vi, "role": "admin"}, headers=h).status_code == 422
    assert client.put("/api/v1/namespaces/calls/members", json={"account": vi, "role": "viewer"}, headers=h).status_code == 404
    members = {m["email"]: m["role"] for m in client.get("/api/v1/namespaces/pods/members", headers=h).json()}
    assert members == {"own@x.io": "owner", "ed@x.io": "owner", "vi@x.io": "viewer"}
    hv = login(new_client(), "vi@x.io", "viewer password 1")
    assert client.get("/api/v1/auth/me", headers=hv).json()["roles"] == {"pods": "viewer"}

    assert client.put("/api/v1/namespaces/pods/members", json={"account": vi, "role": None}, headers=h).status_code == 200  # removed
    assert "vi@x.io" not in {m["email"] for m in client.get("/api/v1/namespaces/pods/members", headers=h).json()}
    assert client.get("/api/v1/auth/me", headers=hv).json()["roles"] == {}


def test_members_changes_need_write_access(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    vi = make_user(db, "vi@x.io", "viewer password 1")
    h = login(client, "root@x.io", "root password 1")
    tok = client.post("/api/v1/tokens", json={"name": "ci", "scope": "read"}, headers=h).json()["token"]
    bearer = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/namespaces/calls/members", headers=bearer).status_code == 200  # admins own every namespace
    body = {"account": vi, "role": "viewer"}
    assert client.put("/api/v1/namespaces/calls/members", json=body, headers=bearer).status_code == 403  # read-only token
    assert client.put("/api/v1/namespaces/calls/members", json=body, headers=h).status_code == 200
