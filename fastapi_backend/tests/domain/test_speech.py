"""Speech providers: transcription with speakers, language, events and sentiment by OpenAI-compatible servers,
ElevenLabs, AssemblyAI and Deepgram at custom addresses; their speakers in speaker separation; text to speech."""

from __future__ import annotations

import http.server
import json
import threading
import urllib.parse

import numpy as np
import pytest

from app.domain import ingest, settings, speakers, speech, store, voice
from tests.helpers import login, make_user, write_wav

R = store.R


def words(*items):
    return items


class Fake(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Fake.seen.append(("GET", self.path, dict(self.headers), b""))
        if self.path.startswith("/aai/v2/transcript/t1"):
            return self._send(
                {
                    "id": "t1",
                    "status": "completed",
                    "language_code": "en_us",
                    "words": [
                        {"text": "Hello", "start": 0, "end": 400, "speaker": "A"},
                        {"text": "there.", "start": 400, "end": 800, "speaker": "A"},
                        {"text": "Terrible", "start": 900, "end": 1300, "speaker": "B"},
                        {"text": "news.", "start": 1300, "end": 1700, "speaker": "B"},
                    ],
                    "sentiment_analysis_results": [
                        {"text": "Hello there.", "start": 0, "end": 800, "sentiment": "POSITIVE"},
                        {"text": "Terrible news.", "start": 900, "end": 1700, "sentiment": "NEGATIVE"},
                    ],
                }
            )
        if self.path.endswith("/models") or self.path.endswith("/v1/models"):
            return self._send({"data": [{"id": "whisper-1"}]} if "/oai/" in self.path else [{"model_id": "scribe_v1"}])
        if "/v2/transcript" in self.path or self.path.endswith("/v1/projects"):
            return self._send({"transcripts": [], "projects": []})
        self.send_error(404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Fake.seen.append(("POST", self.path, dict(self.headers), body))
        p = self.path
        if p == "/oai/v1/audio/transcriptions":
            if b"diarize" in body:
                return self._send(
                    {
                        "text": "Hi. Bye.",
                        "segments": [
                            {"start": 0, "end": 0.8, "text": "Hi.", "speaker": "A"},
                            {"start": 1.0, "end": 1.8, "text": "Bye.", "speaker": "B"},
                        ],
                    }  # fmt: skip
                )
            return self._send(
                {
                    "text": "Book a call for Friday.",
                    "language": "english",
                    "segments": [{"start": 0.0, "end": 1.9, "text": " Book a call for Friday."}],
                    "words": [{"word": "Book", "start": 0.0, "end": 0.3}, {"word": "Friday.", "start": 1.2, "end": 1.9}],
                }
            )
        if p == "/el/v1/speech-to-text":
            return self._send(
                {
                    "language_code": "eng",
                    "text": "Hello. (laughter) Hi.",
                    "words": [
                        {"text": "Hello.", "start": 0.0, "end": 0.5, "type": "word", "speaker_id": "speaker_0"},
                        {"text": " ", "start": 0.5, "end": 0.6, "type": "spacing", "speaker_id": "speaker_0"},
                        {"text": "(laughter)", "start": 0.6, "end": 0.9, "type": "audio_event", "speaker_id": "speaker_0"},
                        {"text": "Hi.", "start": 1.0, "end": 1.4, "type": "word", "speaker_id": "speaker_1"},
                    ],
                }
            )
        if p == "/aai/v2/upload":
            return self._send({"upload_url": "https://cdn.example/clip"})
        if p == "/aai/v2/transcript":
            return self._send({"id": "t1", "status": "queued"})
        if p.startswith("/dg/v1/listen"):
            w = [
                {"word": "great", "punctuated_word": "Great", "start": 0.0, "end": 0.4, "speaker": 0},
                {"word": "work", "punctuated_word": "work.", "start": 0.4, "end": 0.8, "speaker": 0},
                {"word": "thanks", "punctuated_word": "Thanks.", "start": 1.0, "end": 1.5, "speaker": 1},
            ]
            return self._send(
                {
                    "results": {
                        "channels": [{"detected_language": "en", "alternatives": [{"transcript": "Great work. Thanks.", "words": w}]}],
                        "sentiments": {"segments": [{"text": "Great work.", "start_word": 0, "end_word": 2, "sentiment": "positive"}]},
                    }
                }
            )
        if p.startswith("/el/v1/text-to-speech/") or p.startswith("/dg/v1/speak"):
            return self._send(b"ID3", "audio/mpeg")
        self.send_error(404)


@pytest.fixture
def fake():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Fake.seen.clear()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def provided(db, cfg, base):
    settings.save(
        db,
        cfg,
        "speech",
        {
            "openai_base_url": base + "/oai/v1",
            "openai_api_key": "sk-o",
            "elevenlabs_base_url": base + "/el",
            "elevenlabs_api_key": "el-key",
            "assemblyai_base_url": base + "/aai",
            "assemblyai_api_key": "aai-key",
            "deepgram_base_url": base + "/dg",
            "deepgram_api_key": "dg-key",
        },
        "t",
    )
    return settings.effective(db, cfg)


def clip(seconds=2.0):
    rng = np.random.default_rng(1)
    return (rng.normal(scale=0.05, size=int(seconds * speech.SR))).astype(np.float32)


def test_keys_are_sealed_and_addresses_checked(db, cfg):
    settings.save(db, cfg, "speech", {"deepgram_api_key": "dg-secret", "deepgram_base_url": "https://proxy.example/dg/"}, "t")
    row = db.one("SELECT data, sealed FROM $r", r=R("app_setting", "speech"))
    assert "dg-secret" not in json.dumps(row) and "deepgram_api_key" in row["sealed"]
    eff = settings.effective(db, cfg)
    assert eff["speech"]["deepgram_api_key"] == "dg-secret"
    assert speech.base_url(eff, "deepgram") == "https://proxy.example/dg"
    assert settings.view(db, cfg)["speech"]["values"]["deepgram_api_key"] == {"secret": True, "set": True}
    assert settings.view(db, cfg)["speech"]["values"]["elevenlabs_api_key"] == {"secret": True, "set": False}
    with pytest.raises(ValueError):
        settings.save(db, cfg, "speech", {"openai_base_url": "ftp://x"}, "t")
    with pytest.raises(ValueError):
        settings.save(db, cfg, "transcribe", {"engine": "nope"}, "t")
    settings.save(db, cfg, "transcribe", {"engine": "deepgram"}, "t")
    settings.save(db, cfg, "diarize", {"engine": "provider"}, "t")
    settings.save(db, cfg, "voice", {"stt": "elevenlabs", "tts_provider": "deepgram"}, "t")
    assert speech.ready(eff, "deepgram") and not speech.ready(eff, "assemblyai")
    with pytest.raises(ingest.EngineMissing, match="AssemblyAI has no API key"):
        ingest.get_engine({**eff, "transcribe": {**eff["transcribe"], "engine": "assemblyai"}})


def test_local_engines_stay_the_default(cfg):
    assert cfg["transcribe"]["engine"] == "sensevoice"
    assert cfg["diarize"]["engine"] == "auto"
    assert cfg["voice"]["stt"] == "same" and cfg["voice"]["tts_provider"] == "openai"
    assert not voice.can_speak(cfg)


def test_an_openai_compatible_server_transcribes(db, cfg, fake):
    eff = provided(db, cfg, fake)
    segs = speech.Engine(eff, "openai").transcribe(clip())
    assert segs == [
        {
            "t0": 0,
            "t1": 1900,
            "text": "Book a call for Friday.",
            "raw_text": None,
            "emotion": None,
            "event": None,
            "lang": "en",
            "words": json.dumps([["Book", 0, 300], ["Friday.", 1200, 1900]]),
            "provider_speaker": None,
        }  # fmt: skip
    ]
    method, path, headers, body = Fake.seen[-1]
    assert headers["Authorization"] == "Bearer sk-o"
    assert b'name="model"\r\n\r\nwhisper-1' in body and b'filename="audio.ogg"' in body and b"OggS" in body
    settings.save(db, cfg, "speech", {"openai_model": "gpt-4o-transcribe-diarize"}, "t")
    segs = speech.Engine(settings.effective(db, cfg), "openai").transcribe(clip())
    assert [(s["text"], s["provider_speaker"]) for s in segs] == [("Hi.", "A"), ("Bye.", "B")]
    assert b"diarized_json" in Fake.seen[-1][3]


def test_elevenlabs_transcribes_with_speakers_and_events(db, cfg, fake):
    eff = provided(db, cfg, fake)
    segs = ingest.get_engine({**eff, "transcribe": {**eff["transcribe"], "engine": "elevenlabs"}}).transcribe(clip())
    assert [(s["text"], s["provider_speaker"], s["event"], s["lang"]) for s in segs] == [
        ("Hello. (laughter)", "speaker_0", "Laughter", "eng"),
        ("Hi.", "speaker_1", None, "eng"),
    ]
    method, path, headers, body = Fake.seen[-1]
    assert headers["Xi-Api-Key"] == "el-key" and b"scribe_v1" in body and b'name="diarize"\r\n\r\ntrue' in body


def test_assemblyai_transcribes_with_speakers_and_sentiment(db, cfg, fake, monkeypatch):
    monkeypatch.setattr(speech, "POLL_SECONDS", 0)
    eff = provided(db, cfg, fake)
    segs = speech.Engine(eff, "assemblyai").transcribe(clip())
    assert [(s["text"], s["provider_speaker"], s["emotion"], s["lang"]) for s in segs] == [
        ("Hello there.", "A", "Happy", "en"),
        ("Terrible news.", "B", "Sad", "en"),
    ]
    asked = next(json.loads(b) for m, p, h, b in Fake.seen if p == "/aai/v2/transcript" and m == "POST")
    assert asked["speaker_labels"] and asked["sentiment_analysis"] and asked["language_detection"]
    assert asked["audio_url"] == "https://cdn.example/clip"
    assert all(h["Authorization"] == "aai-key" for m, p, h, b in Fake.seen)


def test_deepgram_transcribes_with_speakers_and_sentiment(db, cfg, fake):
    settings.save(db, cfg, "transcribe", {"language": "en"}, "t")
    eff = provided(db, cfg, fake)
    segs = speech.Engine(eff, "deepgram").transcribe(clip())
    assert [(s["text"], s["provider_speaker"], s["emotion"]) for s in segs] == [("Great work.", "S0", "Happy"), ("Thanks.", "S1", None)]
    method, path, headers, body = Fake.seen[-1]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(path).query))
    assert q["model"] == "nova-3" and q["diarize"] == "true" and q["sentiment"] == "true" and q["language"] == "en"
    assert headers["Authorization"] == "Token dg-key" and headers["Content-Type"] == "audio/ogg"


def test_speaker_separation_uses_the_providers_speakers(db, cfg, folder, fake):
    eff = provided(db, cfg, fake)
    eff["speakers"]["embedder"] = "none"
    wav = folder / "call.wav"
    write_wav(wav, seconds=2.0)
    nid = store.ns_id(db, "pods")
    rid = db.next_id("recording")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("recording", rid),
        d={"space": nid, "fingerprint": "fp", "status": "new", "source": "audio", "path": str(wav), "title": "call"},
    )
    segs = speech.Engine(eff, "deepgram").transcribe(clip())
    ingest.write_transcript(db, rid, nid, segs, {"status": "transcribed", "engine": "deepgram"})
    assert speakers.diarize_one(db, eff, rid, log=lambda *a: None) == 2
    rec = db.one("SELECT diarizer FROM $r", r=R("recording", rid))
    assert rec["diarizer"] == "provider"
    rows = db.rows("SELECT idx, local_speaker, provider_speaker FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    assert [(r["local_speaker"], r["provider_speaker"]) for r in rows] == [("S0", "S0"), ("S1", "S1")]


def test_answers_are_read_by_elevenlabs_or_deepgram(db, cfg, fake):
    eff = provided(db, cfg, fake)
    settings.save(db, cfg, "voice", {"tts_provider": "elevenlabs", "tts_voice": "v42"}, "t")
    eff = settings.effective(db, cfg)
    assert voice.can_speak(eff)
    assert voice.speak(eff, "Hello") == (b"ID3", "audio/mpeg")
    method, path, headers, body = Fake.seen[-1]
    assert path.startswith("/el/v1/text-to-speech/v42") and json.loads(body)["model_id"] == "eleven_multilingual_v2"
    settings.save(db, cfg, "voice", {"tts_provider": "deepgram", "tts_voice": None}, "t")
    assert voice.speak(settings.effective(db, cfg), "Hello") == (b"ID3", "audio/mpeg")
    method, path, headers, body = Fake.seen[-1]
    assert "model=aura-2-thalia-en" in path and headers["Authorization"] == "Token dg-key"
    settings.save(db, cfg, "speech", {"deepgram_base_url": "http://127.0.0.1:9/dg"}, "t")
    assert voice.speak(settings.effective(db, cfg), "Hello") is None  # unreachable: the browser reads it


def test_voice_chat_can_use_its_own_engine(db, cfg, fake):
    eff = provided(db, cfg, fake)
    settings.save(db, cfg, "voice", {"stt": "elevenlabs"}, "t")
    eff = settings.effective(db, cfg)
    assert voice.can_transcribe(eff) == "elevenlabs"
    voice._ENGINE.update(engine=None, key=None)
    try:
        assert voice.engine(eff).name == "elevenlabs"
    finally:
        voice._ENGINE.update(engine=None, key=None)
    settings.save(db, cfg, "voice", {"stt": "assemblyai"}, "t")
    settings.save(db, cfg, "speech", {"assemblyai_api_key": None}, "t")
    assert voice.can_transcribe(settings.effective(db, cfg)) is None


def test_checking_a_provider(db, cfg, fake, client):
    assert speech.check(cfg, "deepgram") == (False, "Deepgram has no API key yet")
    eff = provided(db, cfg, fake)
    assert speech.check(eff, "openai") == (True, f"reached {fake[7:]}; it lists 1 model(s)")
    assert speech.check(eff, "elevenlabs") == (True, "signed in; 1 model(s)")
    assert speech.check(eff, "assemblyai") == (True, "signed in")
    assert speech.check(eff, "deepgram") == (True, "signed in")
    make_user(db, "ad@x.io", "admin password 1", admin=True)
    h = login(client, "ad@x.io", "admin password 1")
    r = client.post("/api/v1/settings/speech/test?provider=deepgram", headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["detail"] == "signed in"
    assert client.post("/api/v1/settings/speech/test?provider=nope", headers=h).status_code == 422


def test_words_become_segments():
    w = [{"text": f"w{i}.", "t0": i * 100, "t1": i * 100 + 90, "speaker": "A"} for i in range(45)]
    w.append({"text": "late", "t0": 9000, "t1": 9200, "speaker": "A"})
    segs = speech.group_words(w)
    assert [len(json.loads(s["words"])) for s in segs] == [40, 5, 1]  # long sentence breaks, then the pause
    assert speech._short("en-US") == "en" and speech._short("english") == "en"
