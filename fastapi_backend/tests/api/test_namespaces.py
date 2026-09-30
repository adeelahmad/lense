"""Namespaces: listing what you can read, creating (admins), settings (owners) and word clouds."""

from __future__ import annotations

from app.domain import pipelines, store
from tests.helpers import login, make_user, seed


def test_list_create_and_tokens(client, new_client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    listed = {n["name"]: n for n in client.get("/api/v1/namespaces", headers=h).json()}
    assert set(listed) == {"pods", "calls"}
    assert (listed["pods"]["recordings"], listed["pods"]["analyzed"], listed["pods"]["speakers"], listed["pods"]["role"]) == (
        2,
        2,
        3,
        "owner",
    )
    assert listed["calls"]["graph"] == "isolated"
    assert client.post("/api/v1/namespaces", json={"name": "extra"}, headers=h).status_code == 200
    r = client.post("/api/v1/namespaces", json={"name": "iso", "graph": "isolated"}, headers=h)
    assert r.status_code == 200 and r.json()["id"] == store.ns_id(db, "iso", create=False)
    assert db.one("SELECT graph FROM $r", r=store.R("space", r.json()["id"]))["graph"] == "isolated"
    assert client.post("/api/v1/namespaces", json={"name": "Bad Name"}, headers=h).status_code == 400
    assert client.post("/api/v1/namespaces", json={"name": "x", "graph": "odd"}, headers=h).status_code == 422
    assert "namespace.create" in db.values("SELECT VALUE action FROM audit_log")
    # anonymous callers are refused; a read-only token reads but can't create (legacy test_setup_signin_csrf_tokens)
    tok = client.post("/api/v1/tokens", json={"name": "ci", "scope": "read"}, headers=h).json()["token"]
    anon, bearer = new_client(), {"Authorization": f"Bearer {tok}"}
    assert anon.get("/api/v1/namespaces").status_code == 401
    assert anon.get("/api/v1/namespaces", headers=bearer).status_code == 200
    assert anon.post("/api/v1/namespaces", json={"name": "nope"}, headers=bearer).status_code == 403


def test_owners_edit_namespaces(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    he, ho = login(client, "ed@x.io", "editor password 1"), login(client, "own@x.io", "owner password 1")
    assert client.post("/api/v1/namespaces", json={"name": "extra"}, headers=ho).status_code == 403  # admins only
    assert client.patch("/api/v1/namespaces/pods", json={"graph": "isolated"}, headers=he).status_code == 403
    assert client.patch("/api/v1/namespaces/calls", json={"graph": "isolated"}, headers=ho).status_code == 404
    assert client.patch("/api/v1/namespaces/pods", json={"graph": "sideways"}, headers=ho).status_code == 422
    assert client.patch("/api/v1/namespaces/pods", json={"graph": None}, headers=ho).status_code == 400
    assert client.patch("/api/v1/namespaces/pods", json={"graph": "isolated"}, headers=ho).status_code == 200
    pods = store.ns_id(db, "pods")
    assert db.one("SELECT graph FROM $r", r=store.R("space", pods))["graph"] == "isolated"
    assert client.patch("/api/v1/namespaces/pods", json={"pipeline": 9999}, headers=ho).status_code == 400
    pid = pipelines.create(db, "just analyze", ["analyze"])
    assert client.patch("/api/v1/namespaces/pods", json={"pipeline": pid}, headers=ho).status_code == 200
    assert db.one("SELECT pipeline FROM $r", r=store.R("space", pods))["pipeline"] == pid
    assert client.patch("/api/v1/namespaces/pods", json={"pipeline": None}, headers=ho).status_code == 200
    assert db.one("SELECT pipeline FROM $r", r=store.R("space", pods)).get("pipeline") is None


def test_namespace_word_cloud(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    r = client.get("/api/v1/namespaces/pods/wordcloud.svg", headers=h)
    assert r.status_code == 200 and "capsid" in r.text and r.headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/v1/namespaces/calls/wordcloud.svg", headers=h).status_code == 404
    assert client.get("/api/v1/namespaces/nope/wordcloud.svg", headers=h).status_code == 404
