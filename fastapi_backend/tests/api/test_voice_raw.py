"""The standalone voice API has a narrow, opt-in anonymous boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.deps import current_user, get_cfg, optional_user
from app.api.v1.routes.voice import router
from app.domain import voice


@pytest.fixture
def raw(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    cfg = {"voice": {"public_enabled": False}}
    app.dependency_overrides[get_cfg] = lambda: cfg
    app.dependency_overrides[optional_user] = lambda: None
    monkeypatch.setattr(voice, "can_transcribe", lambda cfg: "test")
    monkeypatch.setattr(voice, "can_speak", lambda cfg: True)
    monkeypatch.setattr(voice, "warm", lambda cfg: pytest.fail("anonymous GET must not warm models"))
    voice._PUBLIC_JOBS.clear()
    with TestClient(app) as client:
        yield client, app, cfg
    voice._PUBLIC_JOBS.clear()


def test_anonymous_off_by_default_and_narrow_when_enabled(raw):
    client, app, cfg = raw
    for method, path, kw in [("get", "", {}), ("post", "/speak", {"json": {"text": "hi"}}),
                             ("post", "/transcribe", {"content": b"clip"})]:
        assert getattr(client, method)("/api/v1/voice" + path, **kw).status_code == 401
    cfg["voice"]["public_enabled"] = True
    assert client.get("/api/v1/voice").status_code == 200
    # Still delegates voice lists to the existing signed-in dependency, not raw_user.
    def private():
        raise HTTPException(401)

    app.dependency_overrides[current_user] = private
    assert client.get("/api/v1/voice/voices").status_code == 401
    assert client.get("/api/v1/voice", headers={"Authorization": "Bearer invalid"}).status_code == 401
    cfg["voice"]["public_enabled"] = False
    assert client.get("/api/v1/voice").status_code == 401


def test_raw_speech_errors_and_legacy_fallback(raw, monkeypatch):
    client, app, cfg = raw
    cfg["voice"]["public_enabled"] = True
    monkeypatch.setattr(voice, "speak", lambda *a: None)
    assert client.post("/api/v1/voice/speak", json={"text": "hi"}).status_code == 503
    assert client.post("/api/v1/voice/speak", json={"text": "hi", "voice": "private"}).status_code == 403
    app.dependency_overrides[optional_user] = lambda: SimpleNamespace(id=1)
    assert client.post("/api/v1/voice/speak", json={"text": "hi"}).status_code == 204
    assert client.post("/api/v1/voice/speak", json={"text": "hi", "server_only": True}).status_code == 503
    for text in [" ", "x" * 4001]:
        assert client.post("/api/v1/voice/speak", json={"text": text}).status_code == 422
    monkeypatch.setattr(voice, "speak", lambda *a: (b"RIFF", "audio/wav"))
    result = client.post("/api/v1/voice/speak", json={"text": "x" * 4000, "server_only": True})
    assert result.content == b"RIFF" and result.headers["cache-control"] == "no-store"


def test_raw_transcription_limits_and_errors(raw, monkeypatch):
    client, _, cfg = raw
    cfg["voice"]["public_enabled"] = True
    monkeypatch.setattr(voice, "MAX_BYTES", 4)
    assert client.post("/api/v1/voice/transcribe", content=b"12345").status_code == 413
    assert client.post("/api/v1/voice/transcribe", content=b"").status_code == 400
    for error, status in [(voice.ClipTooLong("long"), 413), (voice.InvalidClip("bad"), 400), (RuntimeError("secret"), 503)]:
        def fail(*args, error=error):
            raise error
        monkeypatch.setattr(voice, "transcribe", fail)
        result = client.post("/api/v1/voice/transcribe", content=b"123")
        assert result.status_code == status
        assert "secret" not in result.text


def test_busy_and_global_anonymous_budget_ignore_forwarding_headers(raw, monkeypatch):
    client, _, cfg = raw
    cfg["voice"]["public_enabled"] = True
    monkeypatch.setattr(voice, "speak", lambda *a: (b"audio", "audio/mpeg"))
    with voice.admission(), voice.admission():
        result = client.post("/api/v1/voice/speak", json={"text": "hi"})
        assert result.status_code == 429 and result.headers["retry-after"] == "60"
    for n in range(voice.PUBLIC_PER_MINUTE):
        assert client.post("/api/v1/voice/speak", json={"text": "hi"}, headers={"X-Forwarded-For": str(n)}).status_code == 200
    assert client.post("/api/v1/voice/speak", json={"text": "hi"}, headers={"X-Forwarded-For": "new"}).status_code == 429


def test_only_admin_can_enable_public_voice(client, cfg, db):
    from tests.helpers import login, make_user

    make_user(db, "voice-admin@x.io", "voice password 1", admin=True)
    admin = login(client, "voice-admin@x.io", "voice password 1")
    make_user(db, "voice-viewer@x.io", "voice password 2", roles={"pods": "viewer"})
    viewer = login(client, "voice-viewer@x.io", "voice password 2")
    path = "/api/v1/settings/voice"
    assert cfg["voice"]["public_enabled"] is False
    assert client.put(path, json={"public_enabled": True}).status_code == 401
    assert client.put(path, headers=viewer, json={"public_enabled": True}).status_code == 403
    assert client.put(path, headers=admin, json={"public_enabled": "true"}).status_code == 400
    assert client.put(path, headers=admin, json={"public_enabled": True}).status_code == 200
    assert client.get("/api/v1/voice").status_code == 200
    assert client.get("/api/v1/voice/voices").status_code == 401
    assert client.get("/api/v1/settings").status_code == 401
    assert client.put(path, headers=admin, json={"public_enabled": False}).status_code == 200
    assert client.get("/api/v1/voice").status_code == 401


def test_raw_provider_timeout_and_budget_errors(raw, monkeypatch):
    client, app, cfg = raw
    cfg["voice"]["public_enabled"] = True
    cfg["speech"] = {"timeout": 1800}

    def transcribe(raw_cfg, data):
        assert raw_cfg["speech"]["timeout"] == 120
        return {"text": "hello"}

    monkeypatch.setattr(voice, "transcribe", transcribe)
    result = client.post("/api/v1/voice/transcribe", content=b"audio")
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert cfg["speech"]["timeout"] == 1800  # archive settings are untouched

    def over_budget(*args):
        raise RuntimeError("private budget details")

    monkeypatch.setattr(voice, "speak", over_budget)
    result = client.post("/api/v1/voice/speak", json={"text": "hi", "server_only": True})
    assert result.status_code == 503 and "private" not in result.text
    app.dependency_overrides[optional_user] = lambda: SimpleNamespace(id=1)
    assert client.post("/api/v1/voice/speak", json={"text": "hi"}).status_code == 204
