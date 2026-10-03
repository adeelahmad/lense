"""The entity index, entity pages, the graph explorer and curation, with roles."""

from __future__ import annotations

import pytest

from app.domain import analyze, entities, graph, ingest, search, store
from tests.helpers import drain, login, make_user, quiet, seed

EXTRA = (
    "Alice|N|We met the Northwind Labs team and the North Wind Labs lawyers.\n"
    "Bob|N|Amazon Web Services hosts it; AWS bills monthly.\nAlice|N|Northwind Labs again, with Dyno Therapeutics."
)


class Env:
    def __init__(self, db, cfg, folder, client):
        self.db, self.cfg, self.c = db, cfg, client
        self.a, self.b, self.call = seed(db, cfg, folder)
        p = folder / "extra.txt"
        p.write_text(EXTRA)
        self.x = ingest.import_transcript(db, cfg, "pods", p, log=quiet)
        analyze.analyze_pending(db, cfg, log=quiet)
        make_user(db, "root@x.io", "root password 1", admin=True)
        make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
        make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
        self.h = {
            "admin": login(client, "root@x.io", "root password 1"),
            "editor": login(client, "ed@x.io", "editor password 1"),
            "viewer": login(client, "vi@x.io", "viewer password 1"),
        }

    def eid(self, name, ns="pods"):
        spaces = {store.ns_id(self.db, n) for n in ("pods", "calls")}
        items = entities.list_entities(self.db, spaces, q=name)["items"]
        return [x["id"] for x in items if x["name"] == name and x["namespace"] == ns][0]


@pytest.fixture
def env(db, cfg, folder, client):
    return Env(db, cfg, folder, client)


def test_browse_and_explore(env):
    c, h = env.c, env.h["viewer"]
    assert c.get("/api/v1/entities").status_code == 401
    lst = c.get("/api/v1/entities", headers=h).json()
    assert {x["namespace"] for x in lst["items"]} == {"pods"}  # calls isn't readable
    names = {x["name"] for x in c.get("/api/v1/entities", params={"q": "northwnd"}, headers=h).json()["items"]}
    assert names == {"Northwind Labs", "North Wind Labs"}
    types = c.get("/api/v1/entities/types", headers=h).json()
    assert {"type": "ORG", "label": "Organisation", "quiet": False, "builtin": True}.items() <= types[1].items()
    dyno = env.eid("Dyno Therapeutics")
    d = c.get(f"/api/v1/entities/{dyno}", headers=h).json()
    assert (d["type"], d["mentions"], d["namespace"]) == ("ORG", 5, "pods")
    m = c.get(f"/api/v1/entities/{dyno}/mentions", params={"limit": 2}, headers=h).json()
    it = m["items"][0]
    assert it["text"][it["highlight"][0] : it["highlight"][1]] == "Dyno Therapeutics"
    assert c.get(f"/api/v1/entities/{env.eid('Dyno Therapeutics', 'calls')}", headers=h).status_code == 404
    assert c.get("/api/v1/entities/999999", headers=h).status_code == 404
    assert c.get("/api/v1/entities/timeline", params={"ids": str(dyno)}, headers=h).json()["series"]["pods"]
    conn = c.get(f"/api/v1/entities/{dyno}/connections", headers=h).json()
    assert "Northwind Labs" in [x["name"] for x in conn["entities"]]
    sug = c.get("/api/v1/entities/suggestions", headers=h).json()
    pairs = [(s["reason"], {s["a"]["name"], s["b"]["name"]}) for s in sug]
    assert ("same letters", {"Northwind Labs", "North Wind Labs"}) in pairs
    assert ("acronym", {"AWS", "Amazon Web Services"}) in pairs
    g = c.get("/api/v1/graph/explore", params={"focus": f"e{dyno}", "scope": "ns:pods"}, headers=h).json()
    assert {"mentions", "mentioned together"} <= {e["kind"] for e in g["edges"]}
    alice = [n["id"] for n in g["nodes"] if n["kind"] == "speaker" and n["label"] == "Alice"][0]
    p = c.get("/api/v1/graph/path", params={"a": alice, "b": f"e{env.eid('Northwind Labs')}", "scope": "ns:pods"}, headers=h).json()
    assert p["found"] and p["links"][0]["evidence"]
    assert c.get("/api/v1/graph/explore", params={"focus": f"e{dyno}", "scope": "ns:calls"}, headers=h).status_code == 404
    assert c.post(f"/api/v1/entities/{dyno}/rename", headers=h, json={"name": "X"}).status_code == 403


def test_curation_survives_reanalysis(env, db, cfg):
    c, h = env.c, env.h["editor"]
    keep, other = env.eid("Northwind Labs"), env.eid("North Wind Labs")
    mid = c.post("/api/v1/entities/merge", headers=h, json={"keep": keep, "others": [other]}).json()["merge"]
    analyze.analyze_pending(db, cfg, force=True, log=quiet)
    names = [x["name"] for x in c.get("/api/v1/entities", params={"q": "north"}, headers=h).json()["items"]]
    assert names == ["Northwind Labs"]  # the alias holds through re-analysis
    assert c.get(f"/api/v1/entities/{keep}", headers=h).json()["aliases"] == ["north wind labs"]
    assert [m["id"] for m in c.get("/api/v1/entities/merges", headers=h).json()] == [mid]
    assert c.post(f"/api/v1/entities/merges/{mid}/undo", headers=h).status_code == 200
    assert len(c.get("/api/v1/entities", params={"q": "north"}, headers=h).json()["items"]) == 2

    aws = env.eid("AWS")
    mention = c.get(f"/api/v1/entities/{aws}/mentions", headers=h).json()["items"][0]["mention"]
    assert c.post(f"/api/v1/mentions/{mention}/move", headers=h, json={"remove": True}).status_code == 200
    analyze.analyze_pending(db, cfg, force=True, log=quiet)
    assert "AWS" not in [x["name"] for x in c.get("/api/v1/entities", headers=h).json()["items"]]  # the override holds too

    dyno = env.eid("Dyno Therapeutics")
    rename = {"name": "Dyno Therapeutics Inc", "correct": True}
    pv = c.post(f"/api/v1/entities/{dyno}/rename", headers=h, json={**rename, "dry_run": True}).json()
    assert pv["lines"] == 5
    out = c.post(f"/api/v1/entities/{dyno}/rename", headers=h, json=rename).json()
    assert len(out["jobs"]) == 3
    drain(db, cfg)
    assert search.search(db, '"Dyno Therapeutics Inc"', ns="pods")["total"] == 5
    assert c.get(f"/api/v1/entities/{dyno}", headers=h).json()["mentions"] == 5
    assert c.post("/api/v1/entities/retype", headers=h, json={"ids": [dyno], "type": "PRODUCT"}).status_code == 200
    assert c.post("/api/v1/entities/retype", headers=h, json={"ids": [dyno], "type": "NOPE"}).status_code == 400
    assert c.post(f"/api/v1/entities/{dyno}/hide", headers=h, json={"reason": "test"}).status_code == 200
    assert dyno not in [x["id"] for x in c.get("/api/v1/entities", headers=h).json()["items"]]
    key = db.one("SELECT key FROM $r", r=store.R("entity", dyno))["key"]
    assert f"e:{key}" not in [n["id"] for n in graph.build(db, cfg, "ns:pods")["nodes"]]
    c.post(f"/api/v1/entities/{dyno}/hide", headers=h, json={"hidden": False})

    calls_dyno = env.eid("Dyno Therapeutics", "calls")
    assert c.post(f"/api/v1/entities/{dyno}/link", headers=h, json={"with": calls_dyno}).status_code == 404  # editor can't see calls
    ha = env.h["admin"]
    assert c.post(f"/api/v1/entities/{dyno}/link", headers=ha, json={"with": calls_dyno}).status_code == 200
    assert c.get(f"/api/v1/entities/{dyno}", headers=ha).json()["links"][0]["namespace"] == "calls"
    assert c.delete(f"/api/v1/entities/{dyno}/link/{calls_dyno}", headers=ha).status_code == 200
    assert c.get(f"/api/v1/entities/{dyno}", headers=ha).json()["links"] == []
    a, b = env.eid("Amazon Web Services"), env.eid("Northwind Labs")
    assert c.post("/api/v1/entities/not-same", headers=h, json={"a": a, "b": b}).status_code == 200
    actions = [x["action"] for x in c.get("/api/v1/audit", headers=ha).json()]
    assert {"entity.merge", "entity.merge.undo", "mention.move", "entity.rename", "entity.hide", "entity.link"} <= set(actions)
    # a read-only API token can't curate
    tok = c.post("/api/v1/tokens", headers=h, json={"name": "ro"}).json()["token"]
    assert c.post(f"/api/v1/entities/{dyno}/hide", headers={"Authorization": f"Bearer {tok}"}, json={}).status_code == 403


def test_describe_and_filter_by_collection(env, db):
    c, ed, vi = env.c, env.h["editor"], env.h["viewer"]
    dyno = env.eid("Dyno Therapeutics")
    assert c.patch(f"/api/v1/entities/{dyno}", headers=vi, json={"description": "x"}).status_code == 403
    d = c.patch(f"/api/v1/entities/{dyno}", headers=ed, json={"description": "  A gene therapy company.  "}).json()
    assert d["description"] == "A gene therapy company."
    lst = c.get("/api/v1/entities", params={"q": "Dyno"}, headers=vi).json()["items"]
    assert lst[0]["description"] == "A gene therapy company."
    long = c.patch(f"/api/v1/entities/{dyno}", headers=ed, json={"description": "x" * 2001})
    assert long.status_code == 400
    assert c.patch(f"/api/v1/entities/{dyno}", headers=ed, json={"description": ""}).json()["description"] is None

    sid = store.ns_id(db, "pods")
    from app.domain import hierarchy

    sub = hierarchy.create(db, sid, "Extra")
    hierarchy.place(db, [env.x], sub)
    inside = {x["name"]: x["mentions"] for x in c.get("/api/v1/entities", params={"collection": sub}, headers=vi).json()["items"]}
    assert inside["Dyno Therapeutics"] == 1  # counted over its recordings only (5 in the namespace)
    assert "Northwind Labs" in inside and "Amazon Web Services" in inside
    assert c.get("/api/v1/entities", params={"collection": 999999}, headers=vi).status_code == 404
