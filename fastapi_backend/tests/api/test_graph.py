"""The graph API: schema, related nodes, paths, read-only Cypher and asking for changes, with roles and scopes."""

from __future__ import annotations

import pytest

from tests.api.test_entities import Env


@pytest.fixture
def env(db, cfg, folder, client):
    return Env(db, cfg, folder, client)


def token(c, h, scope):
    t = c.post("/api/v1/tokens", json={"name": scope, "scope": scope}, headers=h).json()
    return {"Authorization": f"Bearer {t['token']}"}


def test_schema_and_scopes(env):
    c, h = env.c, env.h["viewer"]
    assert c.get("/api/v1/graph/schema").status_code == 401
    s = c.get("/api/v1/graph/schema", headers=h).json()
    assert s["namespaces"] == ["pods"] and s["examples"]
    assert {r["type"] for r in s["relationships"]} >= {"CONTAINS", "MENTIONS", "SAID"}
    assert c.get("/api/v1/graph/schema", params={"scope": "ns:calls"}, headers=h).status_code == 404  # not readable
    assert c.get("/api/v1/graph/schema", params={"scope": "nope"}, headers=h).status_code == 400
    admin = c.get("/api/v1/graph/schema", params={"scope": "ns:calls"}, headers=env.h["admin"]).json()
    assert admin["namespaces"] == ["calls"]  # isolated, but in its own scope


def test_query(env):
    c, h = env.c, env.h["viewer"]
    body = {"query": "MATCH (r:Recording)-[m:MENTIONS]->(e:Organisation) RETURN e.name AS name, count(r) AS n ORDER BY n DESC, name"}
    out = c.post("/api/v1/graph/query", json=body, headers=h).json()
    assert out["columns"] == ["name", "n"] and ["Dyno Therapeutics", 3] in out["rows"]
    # the global graph only holds what the viewer can read: calls (isolated, and not theirs) adds nothing
    calls = c.post("/api/v1/graph/query", json={"query": "MATCH (n:Namespace) RETURN n.name"}, headers=h).json()
    assert calls["rows"] == [["pods"]]
    drawn = c.post("/api/v1/graph/query", json={"query": "MATCH p=(s:Speaker)-[:SAID]->(e) RETURN p LIMIT 2"}, headers=h).json()
    assert drawn["nodes"] and drawn["edges"]
    bad = c.post("/api/v1/graph/query", json={"query": "MATCH (n) DETACH DELETE n"}, headers=h)
    assert bad.status_code == 400 and "read-only" in bad.json()["detail"]
    ro = token(c, h, "read")
    assert c.post("/api/v1/graph/query", json=body, headers=ro).status_code == 200  # read-only tokens may query
    p = c.post("/api/v1/graph/query", json={"query": "MATCH (e:Entity {name: $n}) RETURN e.mentions", "params": {"n": "AWS"}}, headers=h)
    assert p.json()["rows"]


def test_related_and_paths(env):
    c, h = env.c, env.h["viewer"]
    dyno = f"e{env.eid('Dyno Therapeutics')}"
    up = c.get("/api/v1/graph/related", params={"node": dyno, "relation": "ancestors", "depth": 4, "scope": "ns:pods"}, headers=h).json()
    assert {"Recording", "Collection", "Namespace"} <= {n["labels"][0] for n in up["nodes"]}
    # in the global scope the same entity is part of a merged node, found by its id
    g = c.get("/api/v1/graph/related", params={"node": dyno, "relation": "parents"}, headers=h).json()
    assert g["start"].startswith("e:")
    assert c.get("/api/v1/graph/related", params={"node": "e999999"}, headers=h).status_code == 404
    assert c.get("/api/v1/graph/related", params={"node": dyno, "types": "NOPE"}, headers=h).status_code == 400
    alice = c.post(
        "/api/v1/graph/query", json={"query": "MATCH (s:Speaker {name: 'Alice'}) RETURN id(s)", "scope": "ns:pods"}, headers=h
    ).json()["rows"][0][0]
    p = c.get("/api/v1/graph/paths", params={"a": alice, "b": dyno, "scope": "ns:pods", "max_depth": 3}, headers=h).json()
    assert p["paths"] and p["paths"][0]["nodes"][0] == alice


def test_changes_need_write_and_editor(env, db):
    c = env.c
    a, b = env.eid("Northwind Labs"), env.eid("North Wind Labs")
    ask = {"kind": "merge", "a": f"e{a}", "b": f"e{b}", "reason": "same company"}
    assert c.post("/api/v1/graph/changes", json=ask, headers=env.h["viewer"]).status_code == 403
    ro = token(c, env.h["editor"], "read")
    assert c.post("/api/v1/graph/changes", json=ask, headers=ro).status_code == 403  # read-only token
    r = c.post("/api/v1/graph/changes", json=ask, headers=env.h["editor"]).json()
    assert r["status"] == "proposed"
    listed = c.get("/api/v1/graph-changes", params={"status": "proposed"}, headers=env.h["editor"]).json()
    assert [x["id"] for x in listed] == [r["id"]]
    assert c.post("/api/v1/graph/changes", json=ask, headers=env.h["editor"]).json()["id"] == r["id"]  # not twice
    # nothing changed until someone accepts; asking to apply makes it now, recorded and undoable
    assert c.post(
        "/api/v1/graph/query", json={"query": "MATCH (e:Entity {name: 'North Wind Labs'}) RETURN e"}, headers=env.h["editor"]
    ).json()["rows"]
    done = c.post("/api/v1/graph/changes", json={**ask, "apply": True}, headers=env.h["editor"]).json()
    assert done["status"] == "applied"
    gone = c.post("/api/v1/graph/query", json={"query": "MATCH (e:Entity {name: 'North Wind Labs'}) RETURN e"}, headers=env.h["editor"])
    assert gone.json()["rows"] == []
    assert c.post(f"/api/v1/graph-changes/{done['id']}/undo", headers=env.h["editor"]).status_code == 200
    bad = c.post("/api/v1/graph/changes", json={**ask, "kind": "link"}, headers=env.h["editor"])
    assert bad.status_code == 400 and "merge them instead" in bad.json()["detail"]
