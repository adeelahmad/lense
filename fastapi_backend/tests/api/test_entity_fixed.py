"""The fixed mode: defined entities, Unknown and Unlabeled, and re-analysing to apply a setup."""

from __future__ import annotations

from app.domain import analyze, entity_map, hierarchy, ingest, store
from tests.helpers import drain, login, make_user, quiet

TEXT = (
    "Alice|N|We met Northwind Labs in Paris on 3 March 2025.\n"
    "Bob|N|The acme rollout went well, Dyno Therapeutics paid.\n"
    "Alice|N|Acme Corp signed and Globex too."
)


def _env(db, cfg, folder, client):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    p = folder / "x.txt"
    p.write_text(TEXT)
    rid = ingest.import_transcript(db, cfg, "pods", p, log=quiet)
    analyze.analyze_recording(db, cfg, rid)
    return rid, login(client, "ed@x.io", "editor password 1"), login(client, "vi@x.io", "viewer password 1")


def _said(db, rid):
    names = {e["id"]: e["name"] for e in db.rows("SELECT record::id(id) AS id, name FROM entity")}
    return {m["text"]: names[m["entity"]] for m in db.rows("SELECT text, entity FROM mentions WHERE recording = $r", r=rid)}


def test_fixed_list(db, cfg, folder, client):
    rid, ed, vi = _env(db, cfg, folder, client)
    assert _said(db, rid)["Globex"] == "Globex"  # self-organising: every name is an entity
    base = "/api/v1/namespaces/pods/entities"
    assert client.post(base, headers=vi, json={"name": "Acme Corp", "type": "ORG"}).status_code == 403
    acme = client.post(
        base, headers=ed, json={"name": "Acme Corp", "type": "ORG", "aliases": ["Acme", "ACME Inc"], "description": "Client"}
    )
    assert acme.status_code == 201
    a = acme.json()
    assert (a["defined"], a["description"], sorted(a["aliases"])) == (True, "Client", ["acme", "acme inc"])
    assert client.post(base, headers=ed, json={"name": "Nope", "type": "WHAT"}).status_code == 400
    dyno = client.post(base, headers=ed, json={"name": "Dyno Therapeutics", "type": "ORG"}).json()

    r = client.put("/api/v1/namespaces/pods/entity-setup", headers=ed, json={"mode": "fixed", "types": ["ORG"]})
    assert r.status_code == 200 and r.json()["mode"] == "fixed"
    lst = {e["name"]: e for e in client.get("/api/v1/entities", headers=vi).json()["items"]}
    assert {"Unknown", "Unlabeled"} <= set(lst) and lst["Unknown"]["builtin"] == "unknown"

    applied = client.post("/api/v1/namespaces/pods/entity-setup/apply", headers=ed, json={}).json()
    assert applied["recordings"] == 1
    drain(db, cfg)
    said = _said(db, rid)
    assert said["acme"] == "Acme Corp" and said["Acme Corp"] == "Acme Corp"  # found lower-case too
    assert said["Dyno Therapeutics"] == "Dyno Therapeutics"
    assert said["Northwind Labs"] == "Unlabeled"  # an organisation: it belongs
    assert said["Globex"] == "Unknown"  # a lone word is a topic to the rules: not a type kept
    assert said["Paris"] == "Unknown"  # not a type kept
    assert "3 March 2025" in said and said["3 March 2025"] == "Unknown"
    lst = {e["name"]: e for e in client.get("/api/v1/entities", headers=vi).json()["items"]}
    assert lst["Acme Corp"]["defined"] and lst["Acme Corp"]["mentions"] == 2

    # people's corrections still win, and a new name given in the fixed mode joins the list
    m = [x for x in db.rows("SELECT record::id(id) AS id, text FROM mentions WHERE recording = $r", r=rid) if x["text"] == "Globex"][0]
    moved = client.post(f"/api/v1/mentions/{m['id']}/move", headers=ed, json={"new_name": "Globex", "new_type": "ORG"}).json()
    assert client.get(f"/api/v1/entities/{moved['entity']}", headers=vi).json()["defined"] is True
    analyze.analyze_recording(db, cfg, rid)
    assert _said(db, rid)["Globex"] == "Globex"

    unknown = lst["Unknown"]["id"]
    assert client.post(f"/api/v1/entities/{unknown}/rename", headers=ed, json={"name": "X"}).status_code == 400
    assert client.post("/api/v1/entities/merge", headers=ed, json={"keep": a["id"], "others": [unknown]}).status_code == 400
    assert client.post(f"/api/v1/entities/{unknown}/hide", headers=ed).status_code == 400
    assert client.delete(f"/api/v1/entities/{unknown}", headers=ed).status_code == 400
    assert client.delete(f"/api/v1/entities/{a['id']}", headers=ed).status_code == 400  # it's mentioned
    spare = client.post(base, headers=ed, json={"name": "Initech", "type": "ORG"}).json()
    assert client.delete(f"/api/v1/entities/{spare['id']}", headers=vi).status_code == 403
    assert client.delete(f"/api/v1/entities/{spare['id']}", headers=ed).status_code == 200

    # aliases: replaced, and never another entity's name
    up = client.patch(f"/api/v1/entities/{a['id']}", headers=ed, json={"aliases": ["Acme"]}).json()
    assert up["aliases"] == ["acme"]
    clash = client.patch(f"/api/v1/entities/{a['id']}", headers=ed, json={"aliases": ["Dyno Therapeutics"]})
    assert clash.status_code == 400
    off = client.patch(f"/api/v1/entities/{dyno['id']}", headers=ed, json={"defined": False}).json()
    assert off["defined"] is False
    analyze.analyze_recording(db, cfg, rid)
    assert _said(db, rid)["Dyno Therapeutics"] == "Unlabeled"


def test_a_collection_of_its_own(db, cfg, folder, client):
    rid, ed, _ = _env(db, cfg, folder, client)
    sid = store.ns_id(db, "pods")
    col = hierarchy.create(db, sid, "Clients")
    hierarchy.place(db, [rid], col)
    client.post("/api/v1/namespaces/pods/entities", headers=ed, json={"name": "Globex", "type": "ORG", "collection": col})
    other = hierarchy.create(db, sid, "Elsewhere")
    client.post("/api/v1/namespaces/pods/entities", headers=ed, json={"name": "Acme Corp", "type": "ORG", "collection": other})
    client.put("/api/v1/namespaces/pods/entity-setup", headers=ed, json={"collection": col, "mode": "fixed"})
    assert client.post("/api/v1/namespaces/pods/entity-setup/apply", headers=ed, json={"collection": col}).json()["recordings"] == 1
    drain(db, cfg)
    said = _said(db, rid)
    assert said["Globex"] == "Globex"
    assert said["Acme Corp"] == "Unlabeled"  # defined for another collection
    assert said["Paris"] == "Unlabeled"  # every type kept (but dates)
    assert "3 March 2025" not in said or said["3 March 2025"] == "Unknown"
    assert entity_map.builtins(db, sid).keys() == {"unknown", "unlabeled"}
