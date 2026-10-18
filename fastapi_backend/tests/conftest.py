"""Every test gets a fresh in-memory SurrealDB, its own data folder, and an app wired to both."""

from __future__ import annotations

import os
import pathlib
import uuid

os.environ.setdefault("ACCESS_SECRET_KEY", "test-access-secret-key-0123456789abcdef")
os.environ.pop("SURREAL_URL", None)
# one OpenMP thread per tesseract: each xdist worker runs its own, and their threads fighting over the cores made the
# video and OCR tests take minutes instead of seconds (539 s against 23 s for them on 4 cores)
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.domain import auth, store  # noqa: E402

# Tests use an in-memory SurrealDB; LENS_TEST_SURREAL_URL=ws://host:8000 runs them against a server instead
# (each test gets its own database there).
TEST_URL = os.environ.get("LENS_TEST_SURREAL_URL", "mem://")

# a cheap password hash: production's costs 32 MB and up to seconds on a busy CI runner, and nearly every test signs in
auth.SCRYPT_N = 2**10


def make_cfg(folder: pathlib.Path, url: str = TEST_URL, **overrides):
    base = {
        "data_dir": str(folder / "data"),
        "database": {"url": url, "database": "t" + uuid.uuid4().hex[:10]},
        "namespaces": {"pods": {"paths": [], "graph": "shared"}, "calls": {"paths": [], "graph": "isolated"}},
        "server": {"allowed_hosts": ["127.0.0.1", "localhost", "testserver"]},
        "sources": {"local_roots": [str(folder / "inbox")]},
        # most tests sign in with passwords; tests/api/test_passkeys.py covers installs without them
        "auth": {"passwords": True},
        "components": {"auto": False},  # tests never fetch packages or models
        # a first answer's model call for the conversation's title would follow every first answer's own: tests that read
        # what the model was sent turn it on (tests/api/test_chat_titles.py)
        "ai": {"auto_title": False},
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
