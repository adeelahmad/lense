"""Collections: every recording lives in one collection of its namespace; collections nest, editors arrange them, and
IIIF publishes the tree."""

from __future__ import annotations

import pytest

from app.domain import hierarchy, ingest, store
from tests.helpers import login, make_user, manifests, seed

R = store.R


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "editor"})
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner", "calls": "editor"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    return {
        "a": a,
        "b": b,
        "call": call,
        "pods": store.ns_id(db, "pods"),
        "calls": store.ns_id(db, "calls"),
        "hr": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "ho": login(client, "own@x.io", "owner password 1"),
        "hx": login(client, "out@x.io", "outsider password 1"),
    }


URL = "/api/v1/namespaces/pods/collections"


def _tree(client, h, ns="pods"):
    return {n["name"]: n for n in client.get(f"/api/v1/namespaces/{ns}/collections", headers=h).json()}


def test_every_recording_has_a_home(client, env, db):
    # each namespace starts with General, its default, holding everything
    tree = client.get(URL, headers=env["hv"]).json()
    assert [(n["name"], n["default"], n["recordings"], n["total"], n["depth"], n["path"]) for n in tree] == [
        ("General", True, 2, 2, 0, ["General"])
    ]
    general = tree[0]["id"]
    assert {db.one("SELECT collection FROM $r", r=R("recording", r))["collection"] for r in (env["a"], env["b"])} == {general}
    # a recording from before collections gets its namespace's default when the database is migrated
    rid = db.next_id("recording")
    db.q("CREATE $r CONTENT $d", r=R("recording", rid), d={"space": env["pods"], "title": "Old", "fp_key": "old", "status": "new"})
    hierarchy.migrate_homes(db)
    assert db.one("SELECT collection FROM $r", r=R("recording", rid))["collection"] == general
    # a namespace made later gets its own General
    sid = store.ns_id(db, "fresh")
    assert hierarchy.get(db, store.default_collection(db, sid))["name"] == "General"
    assert client.get("/api/v1/namespaces/calls/collections", headers=env["hv"]).status_code == 404  # no role there


def test_editors_arrange_collections(client, env, db):
    he, hv = env["he"], env["hv"]
    assert client.post(URL, headers=hv, json={"name": "Interviews"}).status_code == 403
    assert client.post(URL, headers=env["hx"], json={"name": "Interviews"}).status_code == 404
    r = client.post(URL, headers=he, json={"name": "  Interviews ", "description": "One guest each"})
    assert r.status_code == 200, r.text
    talks = r.json()
    assert (talks["name"], talks["description"], talks["parent"], talks["default"], talks["created_by"]) == (
        "Interviews",
        "One guest each",
        None,
        False,
        "ed@x.io",
    )
    y24 = client.post(URL, headers=he, json={"name": "2024", "parent": talks["id"]}).json()
    assert (y24["depth"], y24["path"]) == (1, ["Interviews", "2024"])
    # names are unique among siblings, ignoring case; the same name elsewhere is fine
    assert client.post(URL, headers=he, json={"name": "interviews"}).status_code == 400
    assert client.post(URL, headers=he, json={"name": "2024"}).status_code == 200
    assert client.post(URL, headers=he, json={"name": "   "}).status_code == 400
    assert client.post(URL, headers=he, json={"name": "x", "colour": "red"}).status_code == 422
    calls_general = _tree(client, he, "calls")["General"]["id"]
    assert client.post(URL, headers=he, json={"name": "Elsewhere", "parent": calls_general}).status_code == 404
    # at most 8 deep
    parent = y24["id"]
    for i in range(6):
        parent = client.post(URL, headers=he, json={"name": f"Level {i + 3}", "parent": parent}).json()["id"]
    r = client.post(URL, headers=he, json={"name": "Too deep", "parent": parent})
    assert r.status_code == 400 and "8 deep" in r.json()["detail"]
    # depth first, by name, with what each holds
    tree = client.get(URL, headers=hv).json()
    assert [(n["name"], n["depth"]) for n in tree][:4] == [("2024", 0), ("General", 0), ("Interviews", 0), ("2024", 1)]
    assert client.get(f"{URL}/{y24['id']}", headers=hv).json()["path"] == ["Interviews", "2024"]
    assert client.get(f"{URL}/{calls_general}", headers=he).status_code == 404  # not one of pods'

    # rename, describe, move; never into itself
    assert client.patch(f"{URL}/{talks['id']}", headers=hv, json={"name": "Talks"}).status_code == 403
    r = client.patch(f"{URL}/{talks['id']}", headers=he, json={"name": "Talks", "description": None})
    assert (r.json()["name"], r.json()["description"]) == ("Talks", None)
    assert client.patch(f"{URL}/{talks['id']}", headers=he, json={"parent": y24["id"]}).status_code == 400
    assert client.patch(f"{URL}/{y24['id']}", headers=he, json={"parent": None}).status_code == 400  # a 2024 is at the top already
    r = client.patch(f"{URL}/{y24['id']}", headers=he, json={"name": "2024 talks", "parent": None})
    assert (r.json()["depth"], r.json()["path"]) == (0, ["2024 talks"])
    audit = db.rows("SELECT action, detail FROM audit_log WHERE string::starts_with(action, 'collection.')")
    assert {x["action"] for x in audit} == {"collection.create", "collection.update"}


def test_recordings_move_between_collections(client, env, db, cfg):
    he, a, b = env["he"], env["a"], env["b"]
    talks = client.post(URL, headers=he, json={"name": "Talks"}).json()["id"]
    y24 = client.post(URL, headers=he, json={"name": "2024", "parent": talks}).json()["id"]
    general = _tree(client, he)["General"]["id"]
    place = lambda body, h=he: client.post("/api/v1/recordings/collection", headers=h, json=body)  # noqa: E731
    assert place({"recordings": [a], "collection": y24}, env["hv"]).status_code == 403
    assert place({"recordings": [a, env["call"]], "collection": y24}).status_code == 400  # a call is in another namespace
    assert place({"recordings": [a], "collection": 99999}).status_code == 404
    r = place({"recordings": [a], "collection": y24})
    assert r.status_code == 200 and r.json()["moved"] == 1
    assert place({"recordings": [a], "collection": y24}).json()["moved"] == 0  # there already
    tree = _tree(client, he)
    assert (tree["Talks"]["recordings"], tree["Talks"]["total"], tree["2024"]["recordings"], tree["General"]["recordings"]) == (0, 1, 1, 1)
    # the Library filters by a collection and the ones inside it
    ids = lambda c: [x["id"] for x in client.get("/api/v1/recordings", headers=he, params={"collection": c}).json()]  # noqa: E731
    assert ids(talks) == [a] and ids(y24) == [a] and ids(general) == [b]
    row = next(x for x in client.get("/api/v1/recordings", headers=he).json() if x["id"] == a)
    assert (row["collection"], row["collection_name"]) == (y24, "2024")
    calls_general = _tree(client, he, "calls")["General"]["id"]
    assert client.get("/api/v1/recordings", headers=env["hv"], params={"collection": calls_general}).status_code == 404
    # the recording page names its place
    d = client.get(f"/api/v1/recordings/{a}", headers=he).json()
    assert d["collection"] == y24 and d["collection_path"] == [{"id": talks, "name": "Talks"}, {"id": y24, "name": "2024"}]
    # IIIF: its manifest is part of 2024, which is part of Talks, part of the namespace
    hr = env["hr"]
    BASE = client.get("/iiif/collection", headers=hr).json()["id"].rsplit("/iiif/", 1)[0]
    assert client.get(f"/iiif/{a}/manifest", headers=hr).json()["partOf"][0]["id"] == f"{BASE}/iiif/collection/pods/{y24}"
    top = client.get("/iiif/collection/pods", headers=hr).json()
    assert [x["label"]["none"][0] for x in top["items"]] == ["General", "Talks"]
    sub = client.get(f"/iiif/collection/pods/{talks}", headers=hr).json()
    assert [x["id"] for x in sub["items"]] == [f"{BASE}/iiif/collection/pods/{y24}"]
    assert sub["partOf"][0]["id"] == f"{BASE}/iiif/collection/pods"
    assert set(manifests(lambda u: client.get(u, headers=hr), "/iiif/collection/pods")) == {
        f"{BASE}/iiif/{a}/manifest",
        f"{BASE}/iiif/{b}/manifest",
    }
    assert client.get(f"/iiif/collection/pods/{calls_general}").status_code == 404
    assert client.get(f"/iiif/{a}/record.json", headers=hr).json()["isPartOf"]["@id"].endswith(f"/pods/{y24}")
    # anyone else sees only what's public: nothing here, so no collections
    assert client.get("/iiif/collection/pods").json().get("items", []) == []
    assert db.rows("SELECT action FROM audit_log WHERE action = 'recording.collection'")


def test_defaults_and_deleting(client, env, db, cfg):
    he = env["he"]
    general = _tree(client, he)["General"]["id"]
    talks = client.post(URL, headers=he, json={"name": "Talks"}).json()["id"]
    inner = client.post(URL, headers=he, json={"name": "Inner", "parent": talks}).json()["id"]
    # only an empty collection that isn't the default goes
    r = client.delete(f"{URL}/{general}", headers=he)
    assert r.status_code == 409 and "default" in r.json()["detail"]
    assert client.delete(f"{URL}/{talks}", headers=he).status_code == 409  # it holds Inner
    assert client.delete(f"{URL}/{inner}", headers=env["hv"]).status_code == 403
    client.post("/api/v1/recordings/collection", headers=he, json={"recordings": [env["a"]], "collection": inner})
    assert client.delete(f"{URL}/{inner}", headers=he).status_code == 409  # it holds a recording
    client.post("/api/v1/recordings/collection", headers=he, json={"recordings": [env["a"]], "collection": general})
    assert client.delete(f"{URL}/{inner}", headers=he).status_code == 200
    # a new default takes what nobody placed
    assert client.patch(f"{URL}/{general}", headers=he, json={"default": False}).status_code == 400
    assert client.patch(f"{URL}/{talks}", headers=he, json={"default": True}).json()["default"] is True
    rid = ingest.import_text(db, cfg, "pods", "[00:00] Ann: Hello there.\n[00:02] Ben: Hi.\n[00:04] Ann: Bye.")
    assert db.one("SELECT collection FROM $r", r=R("recording", rid))["collection"] == talks
    assert client.delete(f"{URL}/{general}", headers=he).status_code == 409  # not the default, but it holds recordings
    client.post("/api/v1/recordings/collection", headers=he, json={"recordings": [env["a"], env["b"]], "collection": talks})
    assert client.delete(f"{URL}/{general}", headers=he).status_code == 200
    acts = sorted(x["action"] for x in db.rows("SELECT action FROM audit_log WHERE string::starts_with(action, 'collection.')"))
    assert acts == ["collection.create", "collection.create", "collection.delete", "collection.delete", "collection.update"]


def test_imports_and_moves_choose_a_collection(client, env, db, cfg):
    he, ho = env["he"], env["ho"]
    talks = client.post(URL, headers=he, json={"name": "Talks"}).json()["id"]
    calls_general = _tree(client, he, "calls")["General"]["id"]
    body = {"namespace": "pods", "text": "[00:00] Ann: Hello there.\n[00:02] Ben: Hi.\n[00:04] Ann: Bye.", "title": "Into talks"}
    r = client.post("/api/v1/import", headers=he, json={**body, "collection": talks})
    assert r.status_code == 200, r.text
    assert db.one("SELECT collection FROM $r", r=R("recording", r.json()["id"]))["collection"] == talks
    assert client.post("/api/v1/import", headers=he, json={**body, "title": "Lost", "collection": calls_general}).status_code == 404
    # moving to another namespace: into its default, or a collection of it
    a = env["a"]
    assert client.post(f"/api/v1/recordings/{a}/move", headers=ho, json={"namespace": "calls", "collection": talks}).status_code == 404
    r = client.post(f"/api/v1/recordings/{a}/move", headers=ho, json={"namespace": "calls"})
    assert r.status_code == 200 and r.json()["collection"] == calls_general
    assert db.one("SELECT collection FROM $r", r=R("recording", a))["collection"] == calls_general
    # saved views keep a collection
    v = client.post("/api/v1/views", headers=he, json={"name": "Talks", "namespace": "pods", "state": {"collection": talks}}).json()
    assert v["state"]["collection"] == talks
