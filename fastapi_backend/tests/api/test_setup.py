"""First-run setup: the web wizard after the first admin, what the environment sets (and locks), and a fresh database
with no namespaces in archive.yaml."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.domain import auth, store
from tests.conftest import TEST_URL
from tests.helpers import login, make_user

ENV = ("LENS_ADMIN_EMAIL", "LENS_ADMIN_PASSWORD", "LENS_ADMIN_NAME", "LENS_NAMESPACE", "LENS_SETUP_WIZARD")
LLM_ENV = ("LENS_LLM_BASE_URL", "LENS_LLM_MODEL", "LENS_LLM_API_KEY", "LENS_LLM_VISION_MODEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV + LLM_ENV:
        monkeypatch.delenv(k, raising=False)


def bare_cfg(folder):
    """archive.yaml with no namespaces, like a fresh install."""
    cfg = store.load_config(
        str(folder / "missing.yaml"),
        overrides={
            "data_dir": str(folder / "data"),
            "database": {"url": TEST_URL, "database": "t" + uuid.uuid4().hex[:10]},
            "server": {"allowed_hosts": ["127.0.0.1", "localhost", "testserver"]},
            "sources": {"local_roots": [str(folder / "inbox")]},
        },
    )
    assert cfg["namespaces"] == {}
    return cfg


@pytest.fixture
def fresh(folder):
    """Open a fresh archive (no namespaces) with whatever the environment says, as the API does at startup."""
    from app.main import create_app

    opened = []

    def make():
        cfg = bare_cfg(folder)
        db = store.connect(cfg)
        opened.append(db)
        auth._FAILS.clear()
        app = create_app(cfg, db, background=False)
        return app, TestClient(app, base_url="http://127.0.0.1")

    yield make
    for db in opened:
        db.close()


def test_fresh_database_without_namespaces_starts(folder):
    db = store.connect(bare_cfg(folder))
    try:
        assert db.values("SELECT VALUE n FROM $r", r=store.R("seq", "migrations")) == [2]
        assert store.space_names(db) == {}
    finally:
        db.close()


def test_wizard_after_first_admin(fresh, folder):
    app, client = fresh()
    assert client.get("/api/v1/auth/status").json() == {"setup_required": True, "wizard_pending": True}
    assert client.get("/api/v1/setup").status_code == 401

    code = app.state.archive.setup_code
    r = client.post("/api/v1/auth/setup", json={"code": code, "email": "ada@x.io", "password": "admin password 1"})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/v1/auth/status").json() == {"setup_required": False, "wizard_pending": True}

    view = client.get("/api/v1/setup", headers=h).json()
    assert view["pending"] and not view["admin"]["from_env"]
    assert view["namespace"] == {"existing": [], "locked": False}
    assert view["llm"]["locked"] == [] and view["llm"]["values"]["api_key"] == {"secret": True, "set": False}
    assert view["storage"]["local_roots"] == [str(folder / "inbox")] and view["storage"]["watches"] == 0

    assert client.post("/api/v1/setup/namespace", json={"name": "Bad Name!"}, headers=h).status_code == 400
    assert client.post("/api/v1/setup/namespace", json={"name": "family", "graph": "isolated"}, headers=h).status_code == 200
    ns = client.get("/api/v1/namespaces", headers=h).json()
    assert [(n["name"], n["graph"]) for n in ns] == [("family", "isolated")]

    r = client.put("/api/v1/setup/llm", json={"base_url": "http://ollama:11434/v1", "model": "llama3", "api_key": "sk-1"}, headers=h)
    assert r.json()["saved"] == ["api_key", "base_url", "model"]
    llm = client.get("/api/v1/setup", headers=h).json()["llm"]["values"]
    assert llm["base_url"] == "http://ollama:11434/v1" and llm["model"] == "llama3" and llm["api_key"] == {"secret": True, "set": True}
    # an empty key keeps the saved one
    client.put("/api/v1/setup/llm", json={"base_url": "http://ollama:11434/v1", "model": "qwen3", "api_key": ""}, headers=h)
    assert app.state.settings.current()["llm"]["api_key"] == "sk-1"

    (folder / "inbox" / "tapes").mkdir(parents=True)
    bad = {"folder": "/etc", "namespace": "family"}
    assert client.put("/api/v1/setup/storage", json=bad, headers=h).status_code == 400
    r = client.put(
        "/api/v1/setup/storage", json={"max_upload_mb": 2048, "folder": str(folder / "inbox" / "tapes"), "namespace": "family"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["watch"] and client.get("/api/v1/setup", headers=h).json()["storage"] == {
        **view["storage"],
        "max_upload_mb": 2048,
        "watches": 1,
    }

    assert client.post("/api/v1/setup/finish", json={}, headers=h).status_code == 200
    assert client.get("/api/v1/auth/status").json()["wizard_pending"] is False
    assert "setup.finish" in [e["action"] for e in client.get("/api/v1/audit", headers=h).json()]


def test_only_admins_use_the_wizard(fresh):
    app, client = fresh()
    make_user(app.state.db, "ada@x.io", "admin password 1", admin=True)
    make_user(app.state.db, "ed@x.io", "editor password 1")
    h = login(client, "ed@x.io", "editor password 1")
    assert client.get("/api/v1/setup", headers=h).status_code == 403
    assert client.post("/api/v1/setup/finish", json={}, headers=h).status_code == 403


def test_existing_installs_never_see_the_wizard(app, client):
    # the shared fixture's archive starts with no accounts, so it is fresh; one that had accounts when it started isn't
    assert client.get("/api/v1/auth/status").json()["wizard_pending"] is True
    db = app.state.db
    db.q("DELETE $r", r=store.R("setup", "wizard"))
    make_user(db, "ada@x.io", "admin password 1", admin=True)
    app.state.archive.prepare()
    assert client.get("/api/v1/auth/status").json() == {"setup_required": False, "wizard_pending": False}


def test_environment_sets_and_locks(fresh, monkeypatch):
    monkeypatch.setenv("LENS_ADMIN_EMAIL", "ops@x.io")
    monkeypatch.setenv("LENS_ADMIN_PASSWORD", "ops password 123")
    monkeypatch.setenv("LENS_NAMESPACE", "media")
    monkeypatch.setenv("LENS_LLM_BASE_URL", "http://vllm:8000/v1")
    monkeypatch.setenv("LENS_LLM_API_KEY", "sk-env")
    app, client = fresh()
    # the admin exists, so there is no setup code to find in the log; the wizard still runs for what's left
    assert app.state.archive.setup_code is None
    assert client.get("/api/v1/auth/status").json() == {"setup_required": False, "wizard_pending": True}
    h = login(client, "ops@x.io", "ops password 123")
    view = client.get("/api/v1/setup", headers=h).json()
    assert view["admin"]["from_env"] and view["namespace"] == {"existing": ["media"], "locked": True}
    assert sorted(view["llm"]["locked"]) == ["api_key", "base_url"]
    assert view["llm"]["values"]["base_url"] == "http://vllm:8000/v1"
    assert view["llm"]["values"]["api_key"] == {"secret": True, "set": True}  # never the key itself
    assert "sk-env" not in client.get("/api/v1/settings", headers=h).text

    assert client.post("/api/v1/setup/namespace", json={"name": "other"}, headers=h).status_code == 409
    r = client.put("/api/v1/setup/llm", json={"base_url": "http://elsewhere/v1", "model": "llama3", "api_key": "sk-x"}, headers=h)
    assert r.json()["saved"] == ["model"]
    cfg = app.state.settings.current()["llm"]
    assert (cfg["base_url"], cfg["model"], cfg["api_key"]) == ("http://vllm:8000/v1", "llama3", "sk-env")


def test_env_namespace_only_while_there_is_none(fresh, monkeypatch):
    from app.domain import setup

    app, _ = fresh()
    db = app.state.db
    store.ns_id(db, "family")
    monkeypatch.setenv("LENS_NAMESPACE", "media")
    setup.apply_env(db)  # the next start: a namespace exists, so LENS_NAMESPACE adds nothing
    assert sorted(store.space_names(db).values()) == ["family"]


def test_wizard_off(fresh, monkeypatch):
    monkeypatch.setenv("LENS_SETUP_WIZARD", "off")
    _, client = fresh()
    assert client.get("/api/v1/auth/status").json() == {"setup_required": True, "wizard_pending": False}


def test_finds_model_servers_running_nearby(fresh, monkeypatch):
    from app.domain import setup
    from tests import fake_llm

    srv, url = fake_llm.start()
    port = srv.server_address[1]
    try:
        monkeypatch.setattr(fake_llm.Handler, "models", ["nomic-embed-text", "qwen3:8b", "llama3.1"])
        # one server reached at two addresses is offered once; a port with nothing on it is left out
        found = setup.detect_llm(timeout=2, hosts=("127.0.0.1", "localhost"), servers=(("Ollama", port), ("LM Studio", 9)))
        assert found == [
            {"kind": "Ollama", "base_url": url, "models": ["llama3.1", "qwen3:8b", "nomic-embed-text"], "suggested": "llama3.1"}
        ]
        app, client = fresh()
        make_user(app.state.db, "ada@x.io", "admin password 1", admin=True)
        make_user(app.state.db, "ed@x.io", "editor password 1")
        monkeypatch.setattr(setup, "LOCAL_HOSTS", ("127.0.0.1",))
        monkeypatch.setattr(setup, "LOCAL_SERVERS", (("Ollama", port),))
        h = login(client, "ada@x.io", "admin password 1")
        assert client.get("/api/v1/setup/llm/detect", headers=h).json()[0]["suggested"] == "llama3.1"
        assert client.get("/api/v1/setup/llm/detect", headers=login(client, "ed@x.io", "editor password 1")).status_code == 403
    finally:
        srv.shutdown()
