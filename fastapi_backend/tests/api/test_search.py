"""Search, the knowledge graph (and its cache) and entity mentions, limited to readable namespaces."""

from __future__ import annotations

from app.domain import store
from tests.helpers import login, make_user, seed


def test_search_scopes(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "root@x.io", "root password 1", admin=True)
    hv, hr = login(client, "vi@x.io", "viewer password 1"), login(client, "root@x.io", "root password 1")
    res = client.get("/api/v1/search", params={"q": "capsid", "ns": "pods"}, headers=hv).json()
    assert res["total"] >= 3 and "<mark>" in res["hits"][0]["snippet"]
    assert client.get("/api/v1/search", params={"q": "shipment"}, headers=hv).json()["total"] == 0  # calls is not readable
    assert client.get("/api/v1/search", params={"q": "shipment"}, headers=hr).json()["total"] >= 1
    assert client.get("/api/v1/search", params={"q": "capsid", "ns": "calls"}, headers=hv).status_code == 404
    assert client.get("/api/v1/search", params={"q": "capsid", "limit": 500}, headers=hv).status_code == 422
    assert client.get("/api/v1/search", headers=hv).status_code == 422  # q is required
    one = client.get("/api/v1/search", params={"q": "capsid", "limit": 1, "offset": 1}, headers=hv).json()
    assert len(one["hits"]) == 1
    assert client.get("/api/v1/search", params={"q": "capsid"}).status_code == 401


def test_graph_cache_and_mentions(app, client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "own@x.io", "owner password 1", roles={"calls": "owner"})
    hr, hv, ho = (
        login(client, "root@x.io", "root password 1"),
        login(client, "vi@x.io", "viewer password 1"),
        login(client, "own@x.io", "owner password 1"),
    )
    g = client.get("/api/v1/graph", headers=hr).json()
    assert g["namespaces"] == ["pods"]  # calls is isolated
    assert len(app.state.graph_cache) == 1
    assert client.get("/api/v1/graph", headers=hr).json() == g  # served from the cache
    assert len(app.state.graph_cache) == 1
    calls = client.get("/api/v1/graph", params={"scope": "ns:calls"}, headers=ho).json()
    assert {n["label"] for n in calls["nodes"] if n["kind"] == "speaker"} == {"Alice", "Dave"}
    # making calls shared (a change that clears the cache) brings it into the global graph
    assert client.patch("/api/v1/namespaces/calls", json={"graph": "shared"}, headers=ho).status_code == 200
    assert app.state.graph_cache == {}
    assert client.get("/api/v1/graph", headers=hr).json()["namespaces"] == ["calls", "pods"]
    assert client.get("/api/v1/graph", headers=hv).json()["namespaces"] == ["pods"]
    assert client.get("/api/v1/graph", params={"scope": "ns:nope"}, headers=hr).status_code == 404

    dyno = db.values("SELECT VALUE record::id(id) FROM entity WHERE name = 'Dyno Therapeutics'")
    ids = ",".join(str(i) for i in dyno)
    mine = client.get("/api/v1/mentions", params={"entities": ids}, headers=hv).json()
    everything = client.get("/api/v1/mentions", params={"entities": ids}, headers=hr).json()
    assert mine and {m["namespace"] for m in mine} == {"pods"}
    assert {m["namespace"] for m in everything} == {"pods", "calls"}
    assert all("Dyno" in m["text"] for m in everything)
    assert client.get("/api/v1/mentions", params={"entities": "x,y"}, headers=hv).json() == []
    pods = store.ns_id(db, "pods")
    assert all(m["recording_id"] in db.values("SELECT VALUE record::id(id) FROM recording WHERE space = $s", s=pods) for m in mine)
