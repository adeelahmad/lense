"""Every test gets a fresh in-memory SurrealDB, its own data folder, and an app wired to both."""

from __future__ import annotations

import os
import pathlib
import uuid

os.environ.setdefault("ACCESS_SECRET_KEY", "test-access-secret-key-0123456789abcdef")
os.environ.pop("SURREAL_URL", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.domain import auth, store  # noqa: E402


def make_cfg(folder: pathlib.Path, url: str = "mem://", **overrides):
    base = {
        "data_dir": str(folder / "data"),
        "database": {"url": url, "database": "t" + uuid.uuid4().hex[:10]},
        "namespaces": {"pods": {"paths": [], "graph": "shared"}, "calls": {"paths": [], "graph": "isolated"}},
        "server": {"allowed_hosts": ["127.0.0.1", "localhost", "testserver"]},
        "sources": {"local_roots": [str(folder / "inbox")]},
    }
    return store.load_config(str(folder / "missing.yaml"), overrides=store._merge(base, overrides))


@pytest.fixture
def folder(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path


@pytest.fixture
def cfg(folder):
    return make_cfg(folder)


@pytest.fixture
def db(cfg):
    conn = store.connect(cfg)
    auth._FAILS.clear()
    yield conn
    conn.close()


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def client(app):
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def new_client(app):
    """A factory for extra, independent clients (e.g. an anonymous one next to a signed-in one)."""
    return lambda: TestClient(app, base_url="http://127.0.0.1")
