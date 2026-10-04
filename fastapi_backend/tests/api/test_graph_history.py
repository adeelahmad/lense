"""The graph's history over the API: who changed what through what, the graph as of a version, diffs and named
versions, with roles."""

from __future__ import annotations

import pytest

from tests.api.test_entities import Env
from tests.api.test_graph import token


@pytest.fixture
def env(db, cfg, folder, client):
    return Env(db, cfg, folder, client)


def test_changes_record_the_caller(env):
    c, ed, vi = env.c, env.h["editor"], env.h["viewer"]
    assert c.get("/api/v1/graph/history").status_code == 401
    start = c.get("/api/v1/graph/history", headers=vi).json()["head"]
    dyno = env.eid("Dyno Therapeutics")
    assert c.post(f"/api/v1/entities/{dyno}/rename", headers=ed, json={"name": "Dyno"}).status_code == 200
    t = token(c, ed, "write")
    assert c.patch(f"/api/v1/entities/{dyno}", headers=t, json={"description": "Gene therapy"}).status_code == 200
    got = c.get("/api/v1/graph/history", headers=vi, params={"entity": dyno}).json()
    top = got["versions"][:2]
    assert [(v["op"], v["actor"], v["via"]) for v in top] == [
        ("entity.describe", "ed@x.io", "token"),
        ("entity.rename", "ed@x.io", "web"),
    ]
    assert got["head"] == start + 2 and top[0]["names"][str(dyno)] == "Dyno"
    full = c.get(f"/api/v1/graph/history/{top[1]['version']}", headers=vi).json()
    assert [(o["t"], o["b"]["name"], o["a"]["name"]) for o in full["ops"] if o["t"] == "entity"] == [
        ("entity", "Dyno Therapeutics", "Dyno")
    ]
    # the graph as it was, and what changed since
    was = c.get(f"/api/v1/graph/as-of/{start}", headers=vi).json()
    assert "Dyno Therapeutics" in {e["name"] for e in was["entities"]}
    assert {e["space"] for e in was["entities"]} == {env.db.values("SELECT VALUE record::id(id) FROM space WHERE name = 'pods'")[0]}
    d = c.get("/api/v1/graph/diff", headers=vi, params={"from": start}).json()
    assert {f for x in d["entities"]["changed"] for f in x["fields"]} == {"name", "key", "description"}
    assert c.get("/api/v1/graph/diff", headers=vi, params={"from": start, "to": 10**6}).status_code == 400
    assert c.get("/api/v1/graph/as-of/nope", headers=vi).status_code == 404
    assert c.get("/api/v1/graph/history", headers=vi, params={"namespace": "calls"}).status_code in (403, 404)


def test_named_versions(env):
    c, ed, vi = env.c, env.h["editor"], env.h["viewer"]
    assert c.post("/api/v1/graph/tags", headers=vi, json={"name": "before"}).status_code == 403
    v = c.post("/api/v1/graph/tags", headers=ed, json={"name": "before", "note": "clean-up"}).json()["version"]
    c.post(f"/api/v1/entities/{env.eid('Dyno Therapeutics')}/rename", headers=ed, json={"name": "Dyno"})
    assert c.get("/api/v1/graph/tags", headers=vi).json()[0]["name"] == "before"
    d = c.get("/api/v1/graph/diff", headers=vi, params={"from": "before"}).json()
    assert (d["from"], d["to"], d["events"]) == (v, v + 1, 1)
    assert c.delete("/api/v1/graph/tags/before", headers=ed).status_code == 200
    assert c.get("/api/v1/graph/diff", headers=vi, params={"from": "before"}).status_code == 404
