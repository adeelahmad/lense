"""Saved views of the Library: personal, shareable with a namespace, deleted by their maker or the namespace's owners."""

from __future__ import annotations

import pytest

from app.domain import auth, store, views
from tests.helpers import login, make_user, seed

STATE = {
    "tab": "attention",
    "q": "capsid",
    "statuses": ["analyzed"],
    "speaker": "Alice",
    "date": "30d",
    "tags": ["Interview"],
    "sort": "title",
}
MORE = {"tab": "mine", "origins": ["upload", "source:3"], "languages": ["en", "none"]}


@pytest.fixture
def env(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    return {
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "ho": login(client, "own@x.io", "owner password 1"),
        "hx": login(client, "out@x.io", "outsider password 1"),
    }


def _names(client, h):
    return [v["name"] for v in client.get("/api/v1/views", headers=h).json()]


def test_personal_views(client, env, db):
    hv, he, hx = env["hv"], env["he"], env["hx"]
    r = client.post("/api/v1/views", headers=hv, json={"name": "  Capsid   talk ", "namespace": "pods", "state": STATE})
    assert r.status_code == 200, r.text
    v = r.json()
    assert (v["name"], v["namespace"], v["shared"], v["mine"], v["can_delete"], v["created_by"]) == (
        "Capsid talk",
        "pods",
        False,
        True,
        True,
        "vi@x.io",
    )
    assert v["state"] == {
        **STATE,
        "duration": "any",
        "media": "any",
        "origins": [],
        "languages": [],
        "objects": [],
        "collection": None,
        "field": None,
        "value": None,
    }
    everything = client.post("/api/v1/views", headers=hv, json={"name": "Everything new", "state": {"statuses": ["new"], **MORE}}).json()
    assert {k: everything["state"][k] for k in MORE} == MORE
    # a view can keep to one collection (and the ones inside it)
    inside = client.post("/api/v1/views", headers=hv, json={"name": "General only", "namespace": "pods", "state": {"collection": 7}}).json()
    assert inside["state"]["collection"] == 7
    client.delete(f"/api/v1/views/{inside['id']}", headers=hv)
    # or to the recordings with a custom field's value
    by_field = client.post(
        "/api/v1/views", headers=hv, json={"name": "Lectures", "namespace": "pods", "state": {"field": 3, "value": "Lecture"}}
    )
    assert {k: by_field.json()["state"][k] for k in ("field", "value")} == {"field": 3, "value": "Lecture"}
    client.delete(f"/api/v1/views/{by_field.json()['id']}", headers=hv)
    assert everything["namespace"] is None and everything["state"]["sort"] == "-date"
    # only its maker sees a personal view
    assert set(_names(client, hv)) == {"Capsid talk", "Everything new"}
    assert _names(client, he) == [] and _names(client, hx) == []
    # names are unique per person (ignoring case and spaces), not across people
    assert client.post("/api/v1/views", headers=hv, json={"name": "capsid TALK"}).status_code == 400
    assert client.post("/api/v1/views", headers=he, json={"name": "Capsid talk", "namespace": "pods"}).status_code == 200
    assert client.post("/api/v1/views", headers=hv, json={"name": "   "}).status_code == 400
    # what a view holds is checked
    for bad in (
        {"name": ""},
        {"name": "x" * 61},
        {"name": "Odd", "state": {"date": "2w"}},
        {"name": "Odd", "state": {"sort": "loudness"}},
        {"name": "Odd", "state": {"statuses": ["lost"]}},
        {"name": "Odd", "state": {"tags": ["t"] * 21}},
        {"name": "Odd", "state": {"colour": "red"}},
        {"name": "Odd", "state": {"origins": ["elsewhere"]}},
        {"name": "Odd", "extra": 1},
    ):
        assert client.post("/api/v1/views", headers=hv, json=bad).status_code == 422, bad
    assert client.post("/api/v1/views", headers=hv, json={"name": "Calls", "namespace": "calls"}).status_code == 404
    # its maker renames it and saves other filters into it
    vid = v["id"]
    r = client.patch(f"/api/v1/views/{vid}", headers=hv, json={"name": "Capsid", "state": {"media": "audio"}})
    assert r.status_code == 200 and (r.json()["name"], r.json()["state"]["media"], r.json()["state"]["q"]) == ("Capsid", "audio", "")
    assert client.patch(f"/api/v1/views/{vid}", headers=hv, json={"name": "everything NEW"}).status_code == 400
    assert client.patch(f"/api/v1/views/{vid}", headers=he, json={"name": "Mine now"}).status_code == 404
    # a namespace you can no longer read takes its views out of sight
    auth.set_role(db, db.values("SELECT VALUE record::id(id) FROM account WHERE email = 'vi@x.io'")[0], store.ns_id(db, "pods"), None)
    assert _names(client, hv) == ["Everything new"]
    assert client.delete(f"/api/v1/views/{vid}", headers=hv).status_code == 404
    assert client.delete(f"/api/v1/views/{everything['id']}", headers=hv).status_code == 200
    assert _names(client, hv) == []
    assert not db.values("SELECT VALUE action FROM audit_log WHERE string::starts_with(action, 'view.')")  # personal: not audited


def test_shared_views(client, env, db):
    hv, he, ho, hx = env["hv"], env["he"], env["ho"], env["hx"]
    # sharing needs editor access to the namespace, and a namespace
    assert client.post("/api/v1/views", headers=hv, json={"name": "Team", "namespace": "pods", "shared": True}).status_code == 403
    assert client.post("/api/v1/views", headers=he, json={"name": "Team", "shared": True}).status_code == 400
    r = client.post("/api/v1/views", headers=he, json={"name": "Team", "namespace": "pods", "shared": True, "state": STATE})
    assert r.status_code == 200 and r.json()["shared"]
    team = r.json()["id"]
    # everyone with a role there sees it; others don't
    seen = client.get("/api/v1/views", headers=hv).json()
    assert [(v["name"], v["mine"], v["can_delete"], v["created_by"]) for v in seen] == [("Team", False, False, "ed@x.io")]
    assert _names(client, hx) == []
    # only its maker changes it; the viewer's own views come first
    client.post("/api/v1/views", headers=hv, json={"name": "Mine", "namespace": "pods"})
    assert _names(client, hv) == ["Mine", "Team"]
    assert client.patch(f"/api/v1/views/{team}", headers=hv, json={"name": "Ours"}).status_code == 403
    assert client.patch(f"/api/v1/views/{team}", headers=ho, json={"shared": False}).status_code == 403
    # its maker stops sharing it, then shares it again
    assert client.patch(f"/api/v1/views/{team}", headers=he, json={"shared": False}).status_code == 200
    assert _names(client, hv) == ["Mine"]
    assert client.patch(f"/api/v1/views/{team}", headers=he, json={"shared": True}).json()["shared"]
    every = client.post("/api/v1/views", headers=he, json={"name": "All of it"}).json()["id"]
    assert client.patch(f"/api/v1/views/{every}", headers=he, json={"shared": True}).status_code == 400
    # a viewer can't delete it; an owner of the namespace can
    assert client.delete(f"/api/v1/views/{team}", headers=hv).status_code == 403
    assert client.get("/api/v1/views", headers=ho).json()[0]["can_delete"]
    assert client.delete(f"/api/v1/views/{team}", headers=ho).status_code == 200
    assert _names(client, he) == ["All of it"]
    audit = db.rows("SELECT action, target, detail, email FROM audit_log WHERE string::starts_with(action, 'view.')")
    assert sorted((a["action"], a["email"]) for a in audit) == [
        ("view.delete", "own@x.io"),
        ("view.share", "ed@x.io"),
        ("view.share", "ed@x.io"),
        ("view.unshare", "ed@x.io"),
    ]
    assert {a["target"] for a in audit} == {f"view:{team}"}
    # a read-only token can't save views
    tok = client.post("/api/v1/tokens", json={"name": "ro", "scope": "read"}, headers=he).json()["token"]
    ro = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/views", headers=ro).status_code == 200
    assert client.post("/api/v1/views", headers=ro, json={"name": "No"}).status_code == 403


def test_view_limits(client, env, db, monkeypatch):
    monkeypatch.setattr(views, "MAX_VIEWS", 2)
    hv = env["hv"]
    for name in ("One", "Two"):
        assert client.post("/api/v1/views", headers=hv, json={"name": name}).status_code == 200
    r = client.post("/api/v1/views", headers=hv, json={"name": "Three"})
    assert r.status_code == 400 and "delete one first" in r.json()["detail"]
    assert client.get("/api/v1/views").status_code == 401
