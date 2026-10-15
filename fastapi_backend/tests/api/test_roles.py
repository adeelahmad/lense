"""Roles with permissions: the built-in viewer, editor and owner, custom roles admins make, and what each lets
people do in a namespace (docs/access.md#roles-and-permissions)."""

from __future__ import annotations

from app.domain import roles, store
from tests.helpers import login, make_user, seed


def test_builtin_roles_keep_their_meaning():
    assert roles.BUILTIN["viewer"] == {"read"}
    assert roles.BUILTIN["editor"] == {"read", "edit", "import", "process", "curate", "share"}
    assert roles.BUILTIN["owner"] == roles.ALL
    assert [roles.rank(roles.BUILTIN[r]) for r in ("viewer", "editor", "owner")] == ["viewer", "editor", "owner"]
    assert roles.rank(frozenset({"read", "import"})) == "viewer"
    assert roles.rank(roles.BUILTIN["editor"] | {"members"}) == "editor"


def test_admins_make_custom_roles(client, new_client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    h = login(client, "root@x.io", "root password 1")
    ho = login(new_client(), "own@x.io", "owner password 1")

    listed = client.get("/api/v1/roles", headers=ho).json()  # anyone signed in sees them, to give them
    assert [r["name"] for r in listed["roles"]] == ["viewer", "editor", "owner"]
    assert {p["name"] for p in listed["permissions"]} == set(roles.PERMISSIONS)

    body = {"name": "importer", "label": "Importer", "permissions": ["import"]}
    assert client.post("/api/v1/roles", json=body, headers=ho).status_code == 403  # admins only
    r = client.post("/api/v1/roles", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() | {"members": 0} == {
        "name": "importer",
        "label": "Importer",
        "description": "",
        "permissions": ["read", "import"],  # every role reads
        "builtin": False,
        "rank": "viewer",
        "members": 0,
    }
    assert client.post("/api/v1/roles", json=body, headers=h).status_code == 400  # taken
    assert client.post("/api/v1/roles", json={"name": "owner"}, headers=h).status_code == 400
    assert client.post("/api/v1/roles", json={"name": "Bad Name"}, headers=h).status_code == 400
    assert client.post("/api/v1/roles", json={"name": "x2", "permissions": ["fly"]}, headers=h).status_code == 400
    assert client.patch("/api/v1/roles/editor", json={"permissions": []}, headers=h).status_code == 400  # built-in
    assert client.patch("/api/v1/roles/nope", json={"label": "x"}, headers=h).status_code == 404
    audit = {a["action"] for a in client.get("/api/v1/audit", headers=h).json()}
    assert "role.create" in audit


def test_custom_roles_allow_exactly_their_permissions(client, new_client, db, cfg, folder):
    a, _, _ = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    imp = make_user(db, "imp@x.io", "importer password 1")
    h = login(client, "root@x.io", "root password 1")
    assert client.post("/api/v1/roles", json={"name": "importer", "permissions": ["import"]}, headers=h).status_code == 200
    assert client.put("/api/v1/namespaces/pods/members", json={"account": imp, "role": "importer"}, headers=h).status_code == 200
    hi = login(new_client(), "imp@x.io", "importer password 1")

    me = client.get("/api/v1/auth/me", headers=hi).json()
    assert me["roles"] == {"pods": "viewer"} and me["permissions"] == {"pods": ["import", "read"]}
    assert client.get(f"/api/v1/recordings/{a}", headers=hi).status_code == 200  # reads
    up = {"filename": "talk.wav", "size": 10, "namespace": "pods"}
    assert client.post("/api/v1/uploads", json=up, headers=hi).status_code == 201  # imports
    assert client.patch(f"/api/v1/recordings/{a}", json={"title": "x"}, headers=hi).status_code == 403  # doesn't edit
    assert client.post(f"/api/v1/recordings/{a}/share", json={}, headers=hi).status_code == 403  # nor share
    assert client.get("/api/v1/namespaces/pods/notifications", headers=hi).status_code == 403  # nor manage
    assert client.get("/api/v1/namespaces/calls/members", headers=hi).status_code == 404  # elsewhere: nothing

    # a change to the role holds for everyone with it, at once
    r = client.patch("/api/v1/roles/importer", json={"permissions": ["import", "edit", "share"]}, headers=h)
    assert r.status_code == 200 and r.json()["members"] == 1
    assert client.patch(f"/api/v1/recordings/{a}", json={"title": "Edited"}, headers=hi).status_code == 200
    assert client.delete(f"/api/v1/recordings/{a}", headers=hi).status_code == 403  # removing is still the owners'

    # a role somebody holds can't go
    assert client.delete("/api/v1/roles/importer", headers=h).status_code == 409
    assert client.put("/api/v1/namespaces/pods/members", json={"account": imp, "role": None}, headers=h).status_code == 200
    assert client.delete("/api/v1/roles/importer", headers=h).status_code == 200
    assert client.put("/api/v1/namespaces/pods/members", json={"account": imp, "role": "importer"}, headers=h).status_code == 400


def test_people_managers_give_only_what_they_have(client, new_client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    mgr = make_user(db, "mgr@x.io", "manager password 1")
    own = make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    vi = make_user(db, "vi@x.io", "viewer password 1")
    h = login(client, "root@x.io", "root password 1")
    assert client.post("/api/v1/roles", json={"name": "people", "permissions": ["members"]}, headers=h).status_code == 200
    assert client.put("/api/v1/namespaces/pods/members", json={"account": mgr, "role": "people"}, headers=h).status_code == 200
    hm = login(new_client(), "mgr@x.io", "manager password 1")

    assert {m["email"]: m["role"] for m in client.get("/api/v1/namespaces/pods/members", headers=hm).json()} == {
        "mgr@x.io": "people",
        "own@x.io": "owner",
    }
    put = "/api/v1/namespaces/pods/members"
    assert client.put(put, json={"account": vi, "role": "viewer"}, headers=hm).status_code == 200
    assert client.put(put, json={"account": vi, "role": "editor"}, headers=hm).status_code == 403  # more than theirs
    assert client.put(put, json={"account": mgr, "role": "owner"}, headers=hm).status_code == 403  # no promoting yourself
    assert client.put(put, json={"account": own, "role": None}, headers=hm).status_code == 403  # nor taking an owner out
    assert client.put(put, json={"account": vi, "role": None}, headers=hm).status_code == 200
    # owners hold everything, so they still give any role, custom ones included
    ho = login(new_client(), "own@x.io", "owner password 1")
    assert client.put(put, json={"account": vi, "role": "people"}, headers=ho).status_code == 200
    people = {p["email"]: p["roles"] for p in client.get("/api/v1/users", headers=h).json()}
    assert people["vi@x.io"] == {"pods": "people"}
    assert set(roles.holders(db, store.ns_id(db, "pods"), "members")) == {mgr, own, vi}
