"""Entity setups on namespaces and collections, and a namespace's own entity types, with roles."""

from __future__ import annotations

from app.domain import analyze, entity_setup, hierarchy, ingest, store
from tests.helpers import login, make_user, quiet, seed

TEXT = "Alice|N|We met Northwind Labs in Paris on 3 March 2025.\nBob|N|Dyno Therapeutics paid 3 million dollars."


def _env(db, cfg, folder, client):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return login(client, "ed@x.io", "editor password 1"), login(client, "vi@x.io", "viewer password 1")


def test_types_of_a_namespace(db, cfg, folder, client):
    ed, vi = _env(db, cfg, folder, client)
    base = "/api/v1/namespaces/pods/entity-types"
    assert client.post(base, headers=vi, json={"label": "Client"}).status_code == 403
    t = client.post(base, headers=ed, json={"label": "Client team", "description": "A team at a client"})
    assert t.status_code == 201 and t.json()["type"] == "CLIENT_TEAM"
    assert client.post(base, headers=ed, json={"label": "client TEAM"}).status_code == 400  # taken
    assert client.post(base, headers=ed, json={"label": "Organisation"}).status_code == 400  # a built-in one
    types = client.get("/api/v1/entities/types", params={"ns": "pods"}, headers=vi).json()
    assert {"type": "CLIENT_TEAM", "label": "Client team", "builtin": False}.items() <= types[-1].items()
    assert all(t["type"] != "CLIENT_TEAM" for t in client.get("/api/v1/entities/types", headers=vi).json())
    assert client.get("/api/v1/entities/types", params={"ns": "calls"}, headers=vi).status_code == 404
    up = client.patch(f"{base}/CLIENT_TEAM", headers=ed, json={"label": "Client", "description": ""}).json()
    assert (up["label"], up["description"]) == ("Client", None)

    eid = [e["id"] for e in db.rows("SELECT record::id(id) AS id, name FROM entity") if e["name"] == "Dyno Therapeutics"][0]
    assert client.post("/api/v1/entities/retype", headers=ed, json={"ids": [eid], "type": "NOPE"}).status_code == 400
    assert client.post("/api/v1/entities/retype", headers=ed, json={"ids": [eid], "type": "CLIENT_TEAM"}).status_code == 200
    assert client.get(f"/api/v1/entities/{eid}", headers=vi).json()["type_label"] == "Client"
    gone = client.delete(f"{base}/CLIENT_TEAM", headers=ed)
    assert gone.status_code == 400 and "give them another" in gone.json()["detail"]
    client.post("/api/v1/entities/retype", headers=ed, json={"ids": [eid], "type": "ORG"})
    assert client.delete(f"{base}/CLIENT_TEAM", headers=ed).status_code == 200
    assert client.delete(f"{base}/CLIENT_TEAM", headers=ed).status_code == 404


def test_setups_and_what_analysis_keeps(db, cfg, folder, client):
    ed, vi = _env(db, cfg, folder, client)
    sid = store.ns_id(db, "pods")
    v = client.get("/api/v1/namespaces/pods/entity-setup", headers=vi).json()
    assert (v["namespace"]["mode"], v["saved"], v["collections"], v["can_change"]) == ("self", False, [], False)
    url = "/api/v1/namespaces/pods/entity-setup"
    assert client.put(url, headers=vi, json={"mode": "self"}).status_code == 403
    assert client.put(url, headers=ed, json={"mode": "fixed"}).status_code == 422  # not yet
    assert client.put(url, headers=ed, json={"types": ["NOPE"]}).status_code == 400
    ns = client.put(url, headers=ed, json={"types": ["ORG"], "description": "Biotech podcasts"}).json()
    assert (ns["types"], ns["description"], ns["updated_by"]) == (["ORG"], "Biotech podcasts", "ed@x.io")

    calls = hierarchy.create(db, sid, "Calls")
    inner = hierarchy.create(db, sid, "Inner", parent=calls)
    col = client.put(url, headers=ed, json={"collection": calls, "types": ["ORG", "TERM"]}).json()
    assert (col["collection"], col["collection_path"]) == (calls, ["Calls"])
    assert entity_setup.effective(db, sid, inner)["from"] == {"collection": calls}  # inherited
    assert entity_setup.effective(db, sid)["from"] == {"namespace": True}

    p = folder / "x.txt"
    p.write_text(TEXT)
    rid = ingest.import_transcript(db, cfg, "pods", p, log=quiet)
    hierarchy.place(db, [rid], inner)
    analyze.analyze_recording(db, cfg, rid)
    kept = {(m["text"]) for m in db.rows("SELECT text FROM mentions WHERE recording = $r", r=rid)}
    assert kept == {"Northwind Labs", "Paris", "Dyno Therapeutics"}  # no dates or numbers (Paris is a TERM here)

    assert client.delete(f"{url}/collections/{calls}", headers=vi).status_code == 403
    assert client.delete(f"{url}/collections/{calls}", headers=ed).status_code == 200
    analyze.analyze_recording(db, cfg, rid)
    kept = {(m["text"]) for m in db.rows("SELECT text FROM mentions WHERE recording = $r", r=rid)}
    assert kept == {"Northwind Labs", "Dyno Therapeutics"}  # the namespace's: organisations only
    assert client.get(url, headers=vi).json()["collections"] == []
