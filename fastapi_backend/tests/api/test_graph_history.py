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


def test_rollback_previews_then_takes_back(env):
    c, ed, vi = env.c, env.h["editor"], env.h["viewer"]
    start = c.get("/api/v1/graph/history", headers=vi).json()["head"]
    dyno = env.eid("Dyno Therapeutics")
    keep, other = env.eid("Northwind Labs"), env.eid("North Wind Labs")
    c.post(f"/api/v1/entities/{dyno}/rename", headers=ed, json={"name": "Dyno"})
    c.post("/api/v1/entities/merge", headers=ed, json={"keep": keep, "others": [other]})
    assert c.post("/api/v1/graph/rollback", headers=vi, json={"to": str(start)}).status_code == 403
    pv = c.post("/api/v1/graph/rollback", headers=ed, json={"to": str(start)}).json()
    assert pv["done"] is False and [u["op"] for u in pv["undo"]] == ["entity.merge", "entity.rename"]
    assert c.get("/api/v1/graph/history", headers=vi).json()["head"] == start + 2  # nothing changed yet
    done = c.post("/api/v1/graph/rollback", headers=ed, json={"to": str(start), "dry_run": False}).json()
    assert done["done"] and done["version"] == start + 3
    assert c.get(f"/api/v1/entities/{dyno}", headers=vi).json()["name"] == "Dyno Therapeutics"
    assert c.get(f"/api/v1/entities/{other}", headers=vi).status_code == 200  # the merge came undone
    top = c.get("/api/v1/graph/history", headers=vi).json()["versions"][0]
    assert (top["op"], top["actor"], top["via"]) == ("graph.rollback", "ed@x.io", "web")
    # a link to a namespace the editor can't edit blocks a rollback past it
    c.post(f"/api/v1/entities/{dyno}/link", headers=env.h["admin"], json={"with": env.eid("Dyno Therapeutics", "calls")})
    assert c.post("/api/v1/graph/rollback", headers=ed, json={"to": str(start)}).status_code == 403
    assert c.post("/api/v1/graph/rollback", headers=env.h["admin"], json={"to": str(start), "dry_run": False}).json()["done"]


def test_verify_and_checkpoints_are_for_admins(env):
    c, ed, ad = env.c, env.h["editor"], env.h["admin"]
    assert c.get("/api/v1/graph/verify", headers=ed).status_code == 403
    assert c.get("/api/v1/graph/verify", headers=ad).json()["same"] is True
    v = c.post("/api/v1/graph/checkpoints", headers=ad).json()["version"]
    assert v in [x["version"] for x in c.get("/api/v1/graph/checkpoints", headers=ad).json()]
    env.db.q("UPDATE entity SET name = 'X' WHERE name = 'AWS'")
    assert c.get("/api/v1/graph/verify", headers=ad).json()["differences"] == 1
    assert c.post("/api/v1/graph/verify", headers=ad).json()["version"] == v + 1
    assert c.get("/api/v1/graph/verify", headers=ad).json()["same"] is True
