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


def test_llm_test_tries_what_is_typed(client, db, cfg):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    saved = cfg["llm"]["base_url"]
    assert client.put("/api/v1/settings/llm", json={"base_url": "http://127.0.0.1:9/v1"}, headers=h).status_code == 200
    assert not client.post("/api/v1/settings/llm/test", headers=h).json()["ok"]  # nothing typed: the saved address
    r = client.post("/api/v1/settings/llm/test", json={"base_url": saved, "model": "fake"}, headers=h).json()
    assert r["ok"] and r["model"] == "fake"  # the typed address, not saved yet
    assert client.get("/api/v1/settings", headers=h).json()["llm"]["values"]["base_url"] == "http://127.0.0.1:9/v1"


def test_llm_trying_keeps_the_saved_key_at_the_saved_address():
    from app.domain import llm

    saved = {"base_url": "http://a/v1", "model": "m", "api_key": "k", "api_key_env": "E"}
    assert llm.trying(saved, "http://a/v1/", "n")["api_key"] == "k"
    other = llm.trying(saved, "http://b/v1")
    assert other["api_key"] is None and other["api_key_env"] is None and other["model"] == "m"
    assert llm.trying(saved, "http://b/v1", None, "typed")["api_key"] == "typed"
    assert llm.trying(saved) == saved and saved["api_key"] == "k"


def test_admin_pages_need_an_admin(client, db):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "ed@x.io", "editor password 1")
    assert client.get("/api/v1/admin/health", headers=h).status_code == 403
    assert client.post("/api/v1/admin/reindex", headers=h).status_code == 403
    assert client.post("/api/v1/settings/llm/test", headers=h).status_code == 403
