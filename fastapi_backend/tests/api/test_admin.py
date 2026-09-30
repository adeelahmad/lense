"""The admin pages: testing the model connection, the health page and rebuilding the search index."""

from __future__ import annotations

import pytest

from tests import fake_llm
from tests.helpers import login, make_user, seed


@pytest.fixture
def llm(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def test_llm_test_health_reindex(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    assert client.post("/api/v1/settings/llm/test", headers=h).json()["ok"]
    health = client.get("/api/v1/admin/health", headers=h).json()
    assert health["database"]["ok"]
    assert health["counts"]["accounts"] == 1
    assert health["counts"]["recordings"] == 3 and health["disk"]["total_gb"] > 0
    assert client.post("/api/v1/admin/reindex", headers=h).status_code == 202
    assert any(a["action"] == "search.reindex" for a in client.get("/api/v1/audit", headers=h).json())


def test_llm_test_without_a_model(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    assert client.put("/api/v1/settings/llm", json={"base_url": None}, headers=h).status_code == 200
    assert client.post("/api/v1/settings/llm/test", headers=h).json() == {
        "ok": False,
        "error": "set a base URL and a model first",
        "reply": None,
        "ms": None,
        "model": None,
    }


def test_admin_pages_need_an_admin(client, db):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "ed@x.io", "editor password 1")
    assert client.get("/api/v1/admin/health", headers=h).status_code == 403
    assert client.post("/api/v1/admin/reindex", headers=h).status_code == 403
    assert client.post("/api/v1/settings/llm/test", headers=h).status_code == 403
