"""Roles on collections: on top of namespace roles, for the recordings of a collection and the ones inside it. Someone
without a role in the namespace sees just those collections (the Library, search, the recordings); an admin of a
collection runs it like an owner; namespace-wide pages stay with namespace roles."""

from __future__ import annotations

import pytest

from app.domain import store
from tests.helpers import login, make_user, seed

R = store.R
URL = "/api/v1/namespaces/pods/collections"


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    make_user(db, "guest@x.io", "guest password 1")
    e = {
        "a": a,
        "b": b,
        "call": call,
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "ho": login(client, "own@x.io", "owner password 1"),
        "hx": login(client, "out@x.io", "outsider password 1"),
        "hg": login(client, "guest@x.io", "guest password 1"),
    }
    # pods: General (b) · Talks › 2024 (a)
    e["talks"] = client.post(URL, headers=e["he"], json={"name": "Talks"}).json()["id"]
    e["y2024"] = client.post(URL, headers=e["he"], json={"name": "2024", "parent": e["talks"]}).json()["id"]
    assert (
        client.post("/api/v1/recordings/collection", headers=e["he"], json={"recordings": [a], "collection": e["y2024"]}).json()["moved"]
        == 1
    )
    e["general"] = next(n["id"] for n in client.get(URL, headers=e["he"]).json() if n["default"])
    return e


def _give(client, h, cid, email, role):
    return client.put(f"{URL}/{cid}/members", headers=h, json={"email": email, "role": role})


def _ids(client, h, **params):
    return sorted(r["id"] for r in client.get("/api/v1/recordings", headers=h, params=params).json())


def test_outsiders_see_just_their_collections(client, env, db):
    hx, ho, he, hv = env["hx"], env["ho"], env["he"], env["hv"]
    # owners of the namespace give roles; editors and viewers can't
    assert _give(client, he, env["y2024"], "out@x.io", "viewer").status_code == 403
    assert _give(client, hv, env["y2024"], "out@x.io", "viewer").status_code == 403
    assert _give(client, ho, env["y2024"], "nobody@x.io", "viewer").status_code == 404
    r = _give(client, ho, env["y2024"], "out@x.io", "viewer")
    assert r.status_code == 200, r.text
    assert [(m["email"], m["role"], m["by"], m.get("inherited_from")) for m in r.json()] == [("out@x.io", "viewer", "own@x.io", None)]
    audit = db.rows("SELECT action, target, detail FROM audit_log WHERE action = 'collection.member'")
    assert [(x["target"], x["detail"]["role"], x["detail"].get("before")) for x in audit] == [
        (f"collection:{env['y2024']}", "viewer", None)
    ]

    # the namespace shows up, in part, with just that collection (at their top) and its recordings
    pods = next(n for n in client.get("/api/v1/namespaces", headers=hx).json() if n["name"] == "pods")
    assert (pods["partial"], pods["role"], pods["recordings"], pods["wordcloud"]) == (True, None, 1, None)
    assert client.get("/api/v1/auth/me", headers=hx).json()["partial"] == ["pods"]
    assert client.get("/api/v1/auth/me", headers=hv).json()["partial"] == []
    tree = client.get(URL, headers=hx).json()
    assert [(n["name"], n["depth"], n["path"], n["role"], n["can_change"], n["can_grant"]) for n in tree] == [
        ("2024", 0, ["2024"], "viewer", False, False)
    ]
    assert client.get(f"{URL}/{env['talks']}", headers=hx).status_code == 404
    assert _ids(client, hx, ns="pods") == [env["a"]]
    assert _ids(client, hx) == sorted([env["a"], env["call"]])  # and the namespace they have a role in
    assert _ids(client, hx, ns="pods", collection=env["y2024"]) == [env["a"]]
    assert client.get("/api/v1/recordings", headers=hx, params={"collection": env["general"]}).status_code == 404
    for what in ("tags", "origins", "languages"):
        assert client.get(f"/api/v1/recordings/{what}", headers=hx, params={"ns": "pods"}).status_code == 200
    assert sum(o["recordings"] for o in client.get("/api/v1/recordings/languages", headers=hx, params={"ns": "pods"}).json()) == 1

    # the recording opens for them, read-only, and its path starts where they see
    rec = client.get(f"/api/v1/recordings/{env['a']}", headers=hx).json()
    assert (rec["role"], [s["name"] for s in rec["collection_path"]]) == ("viewer", ["2024"])
    assert client.get(f"/api/v1/recordings/{env['a']}/player", headers=hx).status_code == 200
    assert client.get(f"/api/v1/recordings/{env['b']}", headers=hx).status_code == 404
    assert client.patch(f"/api/v1/recordings/{env['a']}", headers=hx, json={"title": "Mine now"}).status_code == 403
    assert client.get(f"/api/v1/recordings/{env['a']}", headers=hv).json()["collection_path"] == [
        {"id": env["talks"], "name": "Talks"},
        {"id": env["y2024"], "name": "2024"},
    ]
    # their own notes, but no sharing
    note = client.post(f"/api/v1/recordings/{env['a']}/notes", headers=hx, json={"text": "Ask about this."})
    assert note.status_code == 200, note.text
    shared = client.post(f"/api/v1/recordings/{env['a']}/notes", headers=hx, json={"text": "Look.", "shared": True})
    assert shared.status_code == 403
    # its runs, and nobody else's
    assert client.get("/api/v1/jobs", headers=hx, params={"recording": env["a"]}).status_code == 200
    assert client.get("/api/v1/jobs", headers=hx, params={"recording": env["b"]}).status_code == 404

    # search finds only what they see; namespace-wide pages stay closed
    hits = client.get("/api/v1/search", headers=hx, params={"q": "Dyno", "ns": "pods"}).json()["hits"]
    assert hits and {h["recording_id"] for h in hits} == {env["a"]}
    assert {h["recording_id"] for h in client.get("/api/v1/search", headers=hv, params={"q": "Dyno", "ns": "pods"}).json()["hits"]} == {
        env["a"],
        env["b"],
    }
    assert client.get("/api/v1/speakers", headers=hx, params={"ns": "pods"}).status_code == 404
    assert client.get("/api/v1/namespaces/pods/stats", headers=hx).status_code == 404
    assert client.get("/api/v1/namespaces/pods/metadata", headers=hx).status_code == 200  # how its records are catalogued
    # what's said in their recording, counted over what they see
    seen = client.get("/api/v1/entities", headers=hx, params={"recording": env["a"]}).json()["items"]
    dyno = next(e for e in seen if "dyno" in e["name"].lower())
    assert dyno["recordings"] == 1
    member = client.get("/api/v1/entities", headers=hv, params={"recording": env["a"]}).json()["items"]
    assert next(e for e in member if "dyno" in e["name"].lower())["recordings"] == 2  # a member counts the namespace
    assert client.get("/api/v1/entities", headers=hx, params={"recording": env["b"]}).status_code == 404

    # IIIF: their recording's (private) manifest, not the others'
    assert client.get(f"/iiif/{env['a']}/manifest", headers=hx).status_code == 200
    assert client.get(f"/iiif/{env['b']}/manifest", headers=hx).status_code == 404

    # taking the role away closes it again
    assert _give(client, ho, env["y2024"], "out@x.io", None).status_code == 200
    assert client.get(f"/api/v1/recordings/{env['a']}", headers=hx).status_code == 404
    assert "pods" not in [n["name"] for n in client.get("/api/v1/namespaces", headers=hx).json()]


def test_editors_and_admins_of_a_collection(client, env, db):
    hx, ho, hv, hg = env["hx"], env["ho"], env["hv"], env["hg"]
    # a viewer of the namespace who edits one collection: its recordings, not the others
    assert _give(client, ho, env["y2024"], "vi@x.io", "editor").status_code == 200
    assert client.patch(f"/api/v1/recordings/{env['a']}", headers=hv, json={"title": "Renamed by a collection editor"}).status_code == 200
    assert client.patch(f"/api/v1/recordings/{env['b']}", headers=hv, json={"title": "Not mine"}).status_code == 403
    roles = {r["id"]: r["role"] for r in client.get("/api/v1/recordings", headers=hv, params={"ns": "pods"}).json()}
    assert roles == {env["a"]: "editor", env["b"]: "viewer"}
    tree = {n["name"]: n for n in client.get(URL, headers=hv).json()}
    assert (tree["2024"]["role"], tree["2024"]["can_change"], tree["General"]["role"]) == ("editor", False, "viewer")
    assert client.patch(f"{URL}/{env['y2024']}", headers=hv, json={"name": "Twenty"}).status_code == 403  # editors don't arrange

    # an outsider who is an admin of Talks runs it, and what's inside it, like an owner
    assert _give(client, ho, env["talks"], "out@x.io", "admin").status_code == 200
    tree = client.get(URL, headers=hx).json()
    assert [(n["name"], n["depth"], n["role"], n["can_change"], n["can_grant"]) for n in tree] == [
        ("Talks", 0, "admin", True, True),
        ("2024", 1, "admin", True, True),
    ]
    spring = client.post(URL, headers=hx, json={"name": "Spring", "parent": env["y2024"]})
    assert spring.status_code == 200 and spring.json()["role"] == "admin", spring.text
    assert client.post(URL, headers=hx, json={"name": "Elsewhere"}).status_code == 403  # the top is the namespace's
    assert client.patch(f"{URL}/{env['talks']}", headers=hx, json={"name": "Lectures"}).json()["name"] == "Lectures"
    assert client.patch(f"{URL}/{env['talks']}", headers=hx, json={"default": True}).status_code == 403
    assert client.patch(f"{URL}/{env['y2024']}", headers=hx, json={"parent": None}).status_code == 403
    assert client.patch(f"{URL}/{spring.json()['id']}", headers=hx, json={"parent": env["general"]}).status_code == 404  # unseen
    # owner actions on its recordings
    assert client.put(f"/api/v1/recordings/{env['a']}/access", headers=hx, json={"access": "restricted"}).status_code == 200
    assert client.put(f"/api/v1/recordings/{env['a']}/access", headers=hv, json={"access": "private"}).status_code == 403
    # and gives roles on it: the guest sees Talks with everything inside it
    members = _give(client, hx, env["talks"], "guest@x.io", "viewer")
    assert members.status_code == 200
    assert {(m["email"], m["role"]) for m in members.json()} == {("out@x.io", "admin"), ("guest@x.io", "viewer")}
    assert _ids(client, hg, ns="pods") == [env["a"]]
    inner = client.get(f"{URL}/{env['y2024']}/members", headers=hx).json()
    assert [(m["email"], m["role"], (m.get("inherited_from") or {}).get("name")) for m in inner] == [
        ("vi@x.io", "editor", None),
        ("out@x.io", "admin", "Lectures"),
        ("guest@x.io", "viewer", "Lectures"),
    ]
    assert client.get(f"{URL}/{env['y2024']}/members", headers=hg).status_code == 403

    # deleting a collection takes the roles given on it
    temp = client.post(URL, headers=ho, json={"name": "Temp"}).json()["id"]
    assert _give(client, ho, temp, "guest@x.io", "editor").status_code == 200
    assert client.delete(f"{URL}/{temp}", headers=ho).status_code == 200
    assert db.values("SELECT VALUE id FROM collection_role WHERE collection = $c", c=temp) == []
    assert _give(client, ho, env["talks"], "guest@x.io", "superuser").status_code == 422
