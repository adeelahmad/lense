"""Talking to Lens: what's said into the mic, as text, by the server's own engine; answers read aloud by a speech
model when one is set up, else by the browser."""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from app.domain import ingest, settings, voice
from tests.helpers import login, make_user, write_wav


class Engine:
    name = "sensevoice"
    heard = []

    def transcribe(self, audio):
        Engine.heard.append(len(audio))
        return [{"text": " book a call ", "lang": "en"}, {"text": "for Friday", "lang": "en"}]


@pytest.fixture
def signed_in(client, db):
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return login(client, "vi@x.io", "viewer password 1")


def test_what_is_said_comes_back_as_text(client, signed_in, cfg, folder, monkeypatch):
    h = signed_in
    assert client.get("/api/v1/voice", headers=h).json()["transcribe"] in (True, False)
    monkeypatch.setattr(voice, "can_transcribe", lambda cfg: None)
    assert client.post("/api/v1/voice/transcribe", headers=h, content=b"x").status_code == 409  # the browser's, then
    monkeypatch.setattr(voice, "can_transcribe", lambda cfg: "sensevoice")
    monkeypatch.setattr(ingest, "get_engine", lambda cfg, log=None: Engine())
    voice._ENGINE.update(engine=None, key=None)
    info = client.get("/api/v1/voice", headers=h).json()
    assert info == {"transcribe": True, "engine": "sensevoice", "speak": False}
    wav = folder / "said.wav"
    write_wav(wav, seconds=1.5)
    r = client.post("/api/v1/voice/transcribe", headers={**h, "Content-Type": "application/octet-stream"}, content=wav.read_bytes())
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "book a call for Friday", "language": "en", "engine": "sensevoice", "seconds": 1.5}
    assert Engine.heard[-1] == 24000  # decoded to 16 kHz mono
    assert client.post("/api/v1/voice/transcribe", headers=h, content=b"not audio").status_code == 400
    assert client.post("/api/v1/voice/transcribe", content=wav.read_bytes()).status_code == 401
    voice._ENGINE["used"] = 0
    voice.let_go()  # nobody talking: the engine is let go
    assert voice._ENGINE["engine"] is None


class Tts(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        Tts.seen.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", "3")
        self.end_headers()
        self.wfile.write(b"ID3")


def test_answers_are_read_aloud_by_a_speech_model(client, signed_in, cfg, db):
    h = signed_in
    assert client.post("/api/v1/voice/speak", headers=h, json={"text": "Hello"}).status_code == 204  # the browser reads it
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Tts)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        settings.save(db, cfg, "llm", {"base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "m", "api_key": "sk-1"}, "t")
        settings.save(db, cfg, "voice", {"tts_model": "kokoro", "tts_voice": "af_heart"}, "t")
        assert client.get("/api/v1/voice", headers=h).json()["speak"] is True
        r = client.post("/api/v1/voice/speak", headers=h, json={"text": "The shipment leaves on Friday."})
        assert (r.status_code, r.headers["content-type"], r.content) == (200, "audio/mpeg", b"ID3")
        path, headers, body = Tts.seen[-1]
        assert path == "/v1/audio/speech" and headers["Authorization"] == "Bearer sk-1"
        assert body == {"model": "kokoro", "input": "The shipment leaves on Friday.", "voice": "af_heart", "response_format": "mp3"}
    finally:
        srv.shutdown()
    settings.save(db, cfg, "llm", {"base_url": "http://127.0.0.1:9/v1"}, "t")  # gone: the browser reads it
    assert client.post("/api/v1/voice/speak", headers=h, json={"text": "Hello"}).status_code == 204


def test_voice_settings_are_checked(client, db, app):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    ok = {"input": "server", "tts_base_url": "http://localhost:8880/v1", "tts_model": "kokoro", "tts_api_key": "k"}
    assert client.put("/api/v1/settings/voice", headers=h, json=ok).status_code == 200
    assert client.get("/api/v1/settings", headers=h).json()["voice"]["values"]["tts_api_key"] == {"secret": True, "set": True}
    assert client.put("/api/v1/settings/voice", headers=h, json={"input": "telepathy"}).status_code == 400
    assert client.put("/api/v1/settings/voice", headers=h, json={"tts_base_url": "file:///x"}).status_code == 400
