"""Saved searches: every filter kept, personal or shared with a namespace, apart from the Library's saved views."""

from __future__ import annotations

from app.domain import store
from tests.helpers import login, make_user, seed


def test_saved_searches(client, db, cfg, folder):
    a, _b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    hv, he, ho, hx = (
        login(client, "vi@x.io", "viewer password 1"),
        login(client, "ed@x.io", "editor password 1"),
        login(client, "own@x.io", "owner password 1"),
        login(client, "out@x.io", "outsider password 1"),
    )
    alice = next(
        r["id"]
        for r in db.rows("SELECT record::id(id) AS id, name, space FROM speaker")
        if r["name"] == "Alice" and r["space"] == store.ns_id(db, "pods")
    )
    body = {"name": "Capsid, said happily", "q": "capsid", "namespace": "pods", "speaker": alice, "emotion": "Happy", "recording": a}

    # every filter is kept, emotion and recording too, with names you can read
    r = client.post("/api/v1/searches", headers=hv, json=body)
    assert r.status_code == 200, r.text
    s = r.json()
    assert {k: s[k] for k in ("name", "q", "namespace", "speaker", "speaker_name", "emotion", "recording", "shared", "mine")} == {
        "name": "Capsid, said happily",
        "q": "capsid",
        "namespace": "pods",
        "speaker": alice,
        "speaker_name": "Alice",
        "emotion": "Happy",
        "recording": a,
        "shared": False,
        "mine": True,
    }
    assert s["recording_title"]
    # saved searches and saved views are apart: same names allowed, separate lists
    assert client.post("/api/v1/views", headers=hv, json={"name": "Capsid, said happily"}).status_code == 200
    assert [x["name"] for x in client.get("/api/v1/views", headers=hv).json()] == ["Capsid, said happily"]
    assert [x["id"] for x in client.get("/api/v1/searches", headers=hv).json()] == [s["id"]]
    assert client.delete(f"/api/v1/views/{s['id']}", headers=hv).status_code == 404  # not a view
    assert client.post("/api/v1/searches", headers=hv, json={**body, "name": "capsid, SAID happily"}).status_code == 400
    # checked like the search itself
    assert client.post("/api/v1/searches", headers=hv, json={**body, "name": "No words", "q": ""}).status_code == 422
    assert client.post("/api/v1/searches", headers=hv, json={**body, "name": "Calls", "namespace": "calls"}).status_code == 404
    assert client.post("/api/v1/searches", headers=hv, json={**body, "name": "A call", "recording": call}).status_code == 404
    assert client.post("/api/v1/searches", headers=hv, json={**body, "name": "Odd", "colour": "red"}).status_code == 422
    # personal: nobody else sees it
    assert client.get("/api/v1/searches", headers=he).json() == []

    # editors share one with its namespace; everyone there sees it, others don't
    assert client.post("/api/v1/searches", headers=hv, json={**body, "name": "Team", "shared": True}).status_code == 403
    assert (
        client.post("/api/v1/searches", headers=he, json={**body, "name": "Everywhere", "namespace": None, "shared": True}).status_code
        == 400
    )
    team = client.post("/api/v1/searches", headers=he, json={**body, "name": "Team", "shared": True}).json()
    seen = client.get("/api/v1/searches", headers=hv).json()
    assert [(x["name"], x["mine"], x["can_delete"], x["created_by"]) for x in seen] == [
        ("Capsid, said happily", True, True, "vi@x.io"),
        ("Team", False, False, "ed@x.io"),
    ]
    assert client.get("/api/v1/searches", headers=hx).json() == []
    # its maker renames and unshares it; an owner deletes a shared one
    assert client.patch(f"/api/v1/searches/{team['id']}", headers=hv, json={"name": "Mine"}).status_code == 403
    assert client.patch(f"/api/v1/searches/{team['id']}", headers=he, json={"name": "Team capsid"}).json()["name"] == "Team capsid"
    assert client.patch(f"/api/v1/searches/{team['id']}", headers=he, json={"shared": False}).json()["shared"] is False
    assert [x["name"] for x in client.get("/api/v1/searches", headers=hv).json()] == ["Capsid, said happily"]
    client.patch(f"/api/v1/searches/{team['id']}", headers=he, json={"shared": True})
    assert client.delete(f"/api/v1/searches/{team['id']}", headers=hv).status_code == 403
    assert client.delete(f"/api/v1/searches/{team['id']}", headers=ho).status_code == 200
    assert client.delete(f"/api/v1/searches/{s['id']}", headers=hv).status_code == 200
    assert client.get("/api/v1/searches", headers=hv).json() == []
    audit = db.rows("SELECT action, target FROM audit_log WHERE string::starts_with(action, 'search.')")
    assert sorted(x["action"] for x in audit) == ["search.delete", "search.share", "search.share", "search.unshare"]
    assert {x["target"] for x in audit} == {f"search:{team['id']}"}
