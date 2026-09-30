"""Collections: saved filters or fixed lists, private unless shared, and never showing what the viewer can't read."""

from __future__ import annotations

import pytest

from tests import fake_llm
from tests.api._assist import Assist, start_llm


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def s(app, db, cfg, folder, new_client):
    return Assist(app, db, cfg, folder, new_client)


def test_collections(s):
    c, h = s.cl["editor"]
    cid = c.post("/api/v1/collections", headers=h, json={"name": "Capsid talk", "filter": {"namespaces": ["pods"], "q": "capsid"}}).json()[
        "id"
    ]
    assert [r["id"] for r in c.get(f"/api/v1/collections/{cid}", headers=h).json()["recordings"]] == [s.a]
    fixed = c.post("/api/v1/collections", headers=h, json={"name": "Mixed", "recordings": [s.a, s.call]}).json()["id"]
    assert c.get(f"/api/v1/collections/{fixed}", headers=h).json()["count"] == 1  # the call is in a namespace the editor can't read
    assert c.post("/api/v1/collections", headers=h, json={"name": "x", "filter": {"colour": "red"}}).status_code == 400


def test_collections_sharing_and_changes(s):
    c, h = s.cl["editor"]
    ca, ha = s.cl["admin"]
    cv, hv = s.cl["viewer"]
    cid = c.post("/api/v1/collections", headers=h, json={"name": "Capsid talk", "filter": {"namespaces": ["pods"], "q": "capsid"}}).json()[
        "id"
    ]
    fixed = c.post("/api/v1/collections", headers=h, json={"name": "Mixed", "recordings": [s.a, s.call], "shared": True}).json()["id"]
    assert c.post("/api/v1/collections", headers=h, json={"name": "  "}).status_code == 400
    assert cv.post("/api/v1/collections", headers=hv, json={"name": "mine", "recordings": [s.b]}).status_code == 200  # viewers can save too

    mine = {x["id"]: x for x in c.get("/api/v1/collections", headers=h).json()}
    assert set(mine) == {cid, fixed} and (mine[cid]["kind"], mine[cid]["count"]) == ("filter", 1) and mine[fixed]["count"] == 1
    # others see shared collections only, counted with their own access
    seen = {x["id"]: x for x in ca.get("/api/v1/collections", headers=ha).json()}
    assert cid not in seen and seen[fixed]["count"] == 2
    assert ca.get(f"/api/v1/collections/{cid}", headers=ha).status_code == 404
    assert {r["id"] for r in ca.get(f"/api/v1/collections/{fixed}", headers=ha).json()["recordings"]} == {s.a, s.call}
    assert ca.patch(f"/api/v1/collections/{fixed}", headers=ha, json={"name": "x"}).status_code == 404  # shared is read-only
    assert ca.delete(f"/api/v1/collections/{fixed}", headers=ha).status_code == 404

    assert c.patch(f"/api/v1/collections/{cid}", headers=h, json={"name": "Exploits", "filter": {"q": "exploit"}}).status_code == 200
    got = c.get(f"/api/v1/collections/{cid}", headers=h).json()
    assert (got["name"], [r["id"] for r in got["recordings"]]) == ("Exploits", [s.b])
    assert c.patch(f"/api/v1/collections/{cid}", headers=h, json={"filter": {"colour": "red"}}).status_code == 400
    assert c.patch(f"/api/v1/collections/{fixed}", headers=h, json={"recordings": [s.b], "shared": False}).status_code == 200
    assert c.get(f"/api/v1/collections/{fixed}", headers=h).json()["recordings"][0]["id"] == s.b
    assert ca.get(f"/api/v1/collections/{fixed}", headers=ha).status_code == 404  # no longer shared
    assert c.get("/api/v1/collections/999", headers=h).status_code == 404
    assert c.delete(f"/api/v1/collections/{cid}", headers=h).status_code == 200
    assert c.get(f"/api/v1/collections/{cid}", headers=h).status_code == 404
