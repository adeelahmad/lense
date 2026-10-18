"""Speech providers: transcription with speakers, language, events and sentiment by OpenAI-compatible servers,
ElevenLabs, AssemblyAI and Deepgram at custom addresses; their speakers in speaker separation; text to speech."""

from __future__ import annotations

import base64
import http.server
import io
import itertools
import json
import shutil
import subprocess
import sys
import threading
import urllib.parse
import wave

import numpy as np
import pytest

from app.domain import activity, ingest, settings, speakers, speech, store, voice
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
                        {"text": "Hello", "start": 0, "end": 400, "speaker": "A", "confidence": 0.99},
                        {"text": "there.", "start": 400, "end": 800, "speaker": "A", "confidence": 0.52},
                        {"text": "Terrible", "start": 900, "end": 1300, "speaker": "B"},
                        {"text": "news.", "start": 1300, "end": 1700, "speaker": "B"},
                    ],
                    "sentiment_analysis_results": [
                        {"text": "Hello there.", "start": 0, "end": 800, "sentiment": "POSITIVE"},
                        {"text": "Terrible news.", "start": 900, "end": 1700, "sentiment": "NEGATIVE"},
                    ],
                }
            )
        if self.path.startswith("/el/v1/voices"):
            return self._send(
                {"voices": [{"voice_id": "v42", "name": "Ada", "category": "premade", "labels": {"gender": "female", "accent": "british"}}]}
            )
        if self.path == "/dg/v1/models":
            return self._send(
                {
                    "stt": [],
                    "tts": [
                        {"name": "thalia", "canonical_name": "aura-2-thalia-en", "languages": ["en"], "metadata": {"accent": "American"}}
                    ],
                }
            )
        if self.path.startswith("/gm/v1beta/models"):
            return self._send({"models": [{"name": "models/gemini-2.5-flash-preview-tts"}, {"name": "models/gemini-2.5-pro"}]})
        if self.path == "/st/v1/audio/voices":
            return self._send({"voices": ["default", "grandpa"]})
        if self.path == "/oai/v1/audio/voices":
            return self._send({"voices": ["af_heart", "am_adam"]})
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
                        {"text": "Hello.", "start": 0.0, "end": 0.5, "type": "word", "speaker_id": "speaker_0", "logprob": -0.5},
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
                {"word": "great", "punctuated_word": "Great", "start": 0.0, "end": 0.4, "speaker": 0, "confidence": 0.98},
                {"word": "work", "punctuated_word": "work.", "start": 0.4, "end": 0.8, "speaker": 0, "confidence": 0.41},
                {"word": "thanks", "punctuated_word": "Thanks.", "start": 1.0, "end": 1.5, "speaker": 1, "confidence": 0.9},
            ]
            return self._send(
                {
                    "results": {
                        "channels": [{"detected_language": "en", "alternatives": [{"transcript": "Great work. Thanks.", "words": w}]}],
                        "sentiments": {"segments": [{"text": "Great work.", "start_word": 0, "end_word": 2, "sentiment": "positive"}]},
                    }
                }
            )
        if p == "/st/v1/audio/speech":
            return self._send(speech.wav(b"\x00\x00" * 24, 24000), "audio/wav")
        if p.startswith("/gm/v1beta/models/"):
            pcm = base64.b64encode(b"\x00\x01" * 2400).decode()
            return self._send(
                {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000", "data": pcm}}]}}]}
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
            "gemini_base_url": base + "/gm",
            "gemini_api_key": "gm-key",
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


def test_providers_keep_how_sure_they_were_of_each_word(db, cfg, fake, monkeypatch):
    """Deepgram's and AssemblyAI's confidence and ElevenLabs' log probability stay with each word's timing, as a fourth;
    a provider that doesn't say (an OpenAI-compatible server) keeps three, as before."""
    monkeypatch.setattr(speech, "POLL_SECONDS", 0)
    eff = provided(db, cfg, fake)

    def stored(p):
        return [json.loads(s["words"]) for s in speech.Engine(eff, p).transcribe(clip()) if s.get("words")]

    assert stored("deepgram") == [[["Great", 0, 400, 0.98], ["work.", 400, 800, 0.41]], [["Thanks.", 1000, 1500, 0.9]]]
    assert stored("assemblyai")[0] == [["Hello", 0, 400, 0.99], ["there.", 400, 800, 0.52]]
    assert stored("elevenlabs")[0] == [["Hello.", 0, 500, 0.607]]  # e^-0.5
    assert stored("openai") == [[["Book", 0, 300], ["Friday.", 1200, 1900]]]
    # a long recording's pieces move onto its timeline with how sure each word was
    assert speech._shift([{"t0": 0, "t1": 9, "words": json.dumps([["a", 0, 4, 0.5], ["b", 5, 9]])}], 1000, None)[0]["words"] == json.dumps(
        [["a", 1000, 1004, 0.5], ["b", 1005, 1009]]
    )


def test_whisper_keeps_how_sure_it_was_of_each_word():
    """faster-whisper's word probability, and mlx-whisper's, are kept with each word's timing."""

    class W:
        def __init__(self, word, start, end, probability):
            self.word, self.start, self.end, self.probability = word, start, end, probability

    class S:
        start, end, text = 0.0, 1.0, " Book a call."
        words = [W(" Book", 0.0, 0.3, 0.97), W(" a", 0.3, 0.4, 0.88), W(" call.", 0.5, 1.0, 0.345)]

    class Faster:
        def transcribe(self, audio, **kw):
            assert kw["word_timestamps"] is True
            return [S()], None

    class Mlx:
        def transcribe(self, audio, **kw):
            words = [{"word": " Book", "start": 0.0, "end": 0.3, "probability": 0.5}, {"word": " now.", "start": 0.4, "end": 0.9}]
            return {"segments": [{"start": 0.0, "end": 1.0, "text": " Book now.", "words": words}]}

    w = object.__new__(ingest.Whisper)
    w.mlx, w.m, w.lang = False, Faster(), None
    (seg,) = w.transcribe(clip())
    assert json.loads(seg["words"]) == [["Book", 0, 300, 0.97], ["a", 300, 400, 0.88], ["call.", 500, 1000, 0.345]]
    w.mlx, w.m, w.repo = True, Mlx(), "mlx-community/whisper"
    assert json.loads(w.transcribe(clip())[0]["words"]) == [["Book", 0, 300, 0.5], ["now.", 400, 900]]


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


def talk(seconds, pauses):
    """Noise like speech with a second of silence starting at each of `pauses` (seconds)."""
    audio = clip(seconds)
    for at in pauses:
        audio[int(at * speech.SR) : int((at + 1) * speech.SR)] = 0
    return audio


def test_each_provider_has_limits_and_they_can_be_changed(db, cfg):
    eff = settings.effective(db, cfg)
    assert {p: speech.limits(eff, p) for p in speech.PROVIDERS} == {
        "openai": (10 * 60 * speech.SR, 25_000_000),
        "elevenlabs": (600 * 60 * speech.SR, 3_000_000_000),
        "assemblyai": (600 * 60 * speech.SR, 2_200_000_000),
        "deepgram": (120 * 60 * speech.SR, 2_000_000_000),
    }
    settings.save(db, cfg, "speech", {"openai_max_minutes": 20, "openai_max_mb": 100}, "t")
    assert speech.limits(settings.effective(db, cfg), "openai") == (20 * 60 * speech.SR, 100_000_000)
    for bad in ({"openai_max_minutes": 0}, {"deepgram_max_mb": 10_001}, {"elevenlabs_max_minutes": "60"}, {"gemini_max_mb": 5}):
        with pytest.raises(ValueError):
            settings.save(db, cfg, "speech", bad, "t")


def test_a_cut_falls_in_a_pause():
    sr = speech.SR
    audio = talk(150, (48, 103))
    cuts = speech.pieces(audio, 60 * sr)
    assert len(cuts) == 3 and cuts[0][0] == 0 and cuts[-1][1] == len(audio)
    assert all(a == b for (_, a), (b, _) in itertools.pairwise(cuts))  # nothing lost or sent twice
    assert all(e - s <= 60 * sr for s, e in cuts)
    assert 48 * sr < cuts[0][1] < 49 * sr and 103 * sr < cuts[1][1] < 104 * sr
    assert speech.pieces(audio, 150 * sr) == [(0, len(audio))]
    flat = np.zeros(90 * sr, dtype=np.float32)  # no pause stands out: cut as late as it may
    assert speech.pieces(flat, 60 * sr)[0][1] > 59 * sr


def test_a_long_recording_goes_in_pieces_and_comes_back_whole(db, cfg, fake, monkeypatch):
    rows = []
    monkeypatch.setattr(speech, "_ledger", lambda *a: _Ledger(rows, *a))
    settings.save(db, cfg, "speech", {"deepgram_max_minutes": 1}, "t")
    eff = provided(db, cfg, fake)
    segs = speech.Engine(eff, "deepgram").transcribe(talk(150, (48, 103)))
    sent = [b for m, p, h, b in Fake.seen if p.startswith("/dg/v1/listen")]
    assert len(sent) == 3
    starts = sorted({s["t0"] for s in segs if s["text"] == "Great work."})
    assert len(starts) == 3 and starts[0] == 0 and 48_000 < starts[1] < 49_000 and 103_000 < starts[2] < 104_000
    assert [s["provider_speaker"] for s in segs][:2] == ["c0:S0", "c0:S1"]
    assert {s["provider_speaker"] for s in segs} == {f"c{k}:S{n}" for k in range(3) for n in range(2)}
    second = next(s for s in segs if s["provider_speaker"] == "c1:S1")
    assert json.loads(second["words"]) == [["Thanks.", starts[1] + 1000, starts[1] + 1500, 0.9]]  # with how sure, on the timeline
    assert [s["t0"] for s in segs] == sorted(s["t0"] for s in segs)
    assert rows == [("speech.transcribe", "deepgram:nova-3", {"seconds": 150.0, "pieces": 3})]


def test_a_piece_too_large_to_send_is_halved(db, cfg, fake, monkeypatch):
    monkeypatch.setattr(speech, "encode", lambda a: (b"\0" * (len(a) // speech.SR * 10_000), "audio.ogg", "audio/ogg"))
    settings.save(db, cfg, "speech", {"openai_max_mb": 1}, "t")  # 10 KB a second: 100 seconds a request
    eff = provided(db, cfg, fake)
    segs = speech.Engine(eff, "openai").transcribe(talk(250, (40, 110, 165)))  # in half (at 110 s), then each half again
    sent = [b for m, p, h, b in Fake.seen if p == "/oai/v1/audio/transcriptions"]
    assert len(sent) == 4 and all(len(b) < 1_000_000 for b in sent)
    assert [s["text"] for s in segs] == ["Book a call for Friday."] * 4
    assert [s["t0"] // 1000 for s in segs] == [0, 40, 110, 165]
    monkeypatch.setattr(speech, "encode", lambda a: (b"\0" * 2_000_000, "audio.ogg", "audio/ogg"))
    with pytest.raises(speech.ProviderError, match="raise the limit in Settings"):
        speech.Engine(eff, "openai").transcribe(talk(10, ()))


class _Ledger:
    def __init__(self, rows, action, cfg, model_, detail):
        self.rows, self.row = rows, (action, model_, detail)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.rows.append(self.row)


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


def test_answers_are_read_by_gemini_in_a_voice_picked(db, cfg, fake):
    provided(db, cfg, fake)
    settings.save(db, cfg, "voice", {"tts_provider": "gemini"}, "t")
    eff = settings.effective(db, cfg)
    assert voice.can_speak(eff) and speech.tts_voice(eff) == "Kore"
    data, ctype = voice.speak(eff, "Hello", voice="Puck")
    assert ctype == "audio/wav" and data[:4] == b"RIFF"
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getframerate(), w.getnframes()) == (24000, 2400)
    method, path, headers, body = Fake.seen[-1]
    assert (
        path == "/gm/v1beta/models/gemini-2.5-flash-preview-tts:generateContent"
        and {k.lower(): v for k, v in headers.items()}["x-goog-api-key"] == "gm-key"
    )
    cfg_ = json.loads(body)["generationConfig"]
    assert cfg_["responseModalities"] == ["AUDIO"]
    assert cfg_["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Puck"
    found, err = speech.voices(eff)
    assert err is None and {"id": "Kore", "name": "Kore", "detail": "Firm"} in found and len(found) == 30
    assert speech.check(eff, "gemini") == (True, "signed in; 1 speech model(s)")


def test_each_provider_lists_its_voices(db, cfg, fake):
    assert speech.voices(cfg, "elevenlabs") == ([], "ElevenLabs has no API key yet: add it in Settings → Speech providers")
    assert [v["id"] for v in speech.voices(cfg, "openai")[0]][:2] == ["alloy", "ash"]  # OpenAI's own
    eff = provided(db, cfg, fake)
    assert speech.voices(eff, "elevenlabs") == ([{"id": "v42", "name": "Ada", "detail": "female, british"}], None)
    assert speech.voices(eff, "deepgram") == ([{"id": "aura-2-thalia-en", "name": "Thalia", "detail": "American, en"}], None)
    assert Fake.seen[-1][2]["Authorization"] == "Token dg-key"
    settings.save(db, cfg, "voice", {"tts_base_url": fake + "/oai/v1", "tts_model": "kokoro"}, "t")
    found, _ = speech.voices(settings.effective(db, cfg), "openai")  # a server that lists its own
    assert [v["id"] for v in found] == ["af_heart", "am_adam"]
    settings.save(db, cfg, "speech", {"elevenlabs_base_url": "http://127.0.0.1:9/el"}, "t")
    found, err = speech.voices(settings.effective(db, cfg), "elevenlabs")
    assert found == [] and err.startswith("can't reach 127.0.0.1:9")
    with pytest.raises(ValueError):
        speech.voices(eff, "nope")


def test_styletts2_reads_aloud_from_its_own_service(db, cfg, fake):
    settings.save(db, cfg, "voice", {"tts_provider": "styletts2"}, "t")
    eff = settings.effective(db, cfg)
    assert eff["voice"]["styletts2_url"] == "http://styletts2:8880" and voice.can_speak(eff)  # the compose service
    settings.save(db, cfg, "voice", {"styletts2_url": fake + "/st/"}, "t")
    eff = settings.effective(db, cfg)
    data, ctype = voice.speak(eff, "Hello", voice="grandpa")
    assert ctype == "audio/wav" and data[:4] == b"RIFF"
    method, path, headers, body = Fake.seen[-1]
    assert path == "/st/v1/audio/speech" and json.loads(body) == {
        "model": "styletts2",
        "input": "Hello",
        "voice": "grandpa",
        "response_format": "wav",
    }
    voice.speak(eff, "Hi")
    assert json.loads(Fake.seen[-1][3])["voice"] == "default"
    assert speech.voices(eff) == (
        [
            {"id": "default", "name": "default", "detail": "its own reader"},
            {"id": "grandpa", "name": "grandpa", "detail": "from your clip"},
        ],
        None,
    )
    settings.save(db, cfg, "voice", {"styletts2_url": "http://127.0.0.1:9"}, "t")
    eff = settings.effective(db, cfg)
    found, err = speech.voices(eff)
    assert found == [] and err.startswith("StyleTTS2 isn't running") and "--profile styletts2" in err
    assert voice.speak(eff, "Hello") is None  # not running: the browser reads it
    with pytest.raises(ValueError):
        settings.save(db, cfg, "voice", {"styletts2_url": "styletts2:8880"}, "t")


SAY_VOICES = """Albert              en_US    # Hello! My name is Albert.
Eddy (English (UK)) en_GB    # Hello! My name is Eddy.
Bad News            en_US    # Hello! My name is Bad News.
"""


def test_macos_say_reads_aloud_on_a_mac(db, cfg, monkeypatch):
    settings.save(db, cfg, "voice", {"tts_provider": "say"}, "t")
    eff = settings.effective(db, cfg)
    monkeypatch.setattr(speech, "say_available", lambda: False)
    assert not voice.can_speak(eff)
    assert speech.voices(eff) == ([], "macOS say reads aloud only when Lens runs on a Mac itself, not in Docker")
    with pytest.raises(speech.ProviderError, match="only when Lens runs on a Mac"):
        speech.speak(eff, "Hi")
    monkeypatch.setattr(speech, "say_available", lambda: True)
    ran = []

    def run(cmd, input=None, **kw):
        ran.append((cmd, input))
        if cmd[1:] == ["-v", "?"]:
            return subprocess.CompletedProcess(cmd, 0, SAY_VOICES.encode(), b"")
        out = cmd[cmd.index("-o") + 1]
        with open(out, "wb") as f:
            f.write(speech.wav(b"\x00\x00" * 10, 22050))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(speech.subprocess, "run", run)
    assert voice.can_speak(eff) and speech.tts_voice(eff) is None
    data, ctype = voice.speak(eff, "-v Bad News? no, text", voice="Eddy (English (UK))")
    assert ctype == "audio/wav" and data[:4] == b"RIFF"
    cmd, sent = ran[-1]
    assert cmd[-2:] == ["-v", "Eddy (English (UK))"] and sent == b"-v Bad News? no, text"  # text on stdin, never an option
    voice.speak(eff, "Hi")
    assert "-v" not in ran[-1][0]  # the Mac's own voice
    assert speech.voices(eff)[0] == [
        {"id": "Albert", "name": "Albert", "detail": "en_US"},
        {"id": "Eddy (English (UK))", "name": "Eddy (English (UK))", "detail": "en_GB"},
        {"id": "Bad News", "name": "Bad News", "detail": "en_US"},
    ]
    with pytest.raises(speech.ProviderError, match="isn't a voice"):
        speech.speak(eff, "Hi", voice="--help")


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


def test_stuck_converters_are_stopped(tmp_path, monkeypatch):
    """ffmpeg, antiword and pdftotext that never finish are given up on, so one bad file can't hold a worker."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ffmpeg", "antiword", "pdftotext"):
        (bin_dir / name).write_text(f"#!/bin/sh\nexec {shutil.which('sleep')} 60\n")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))  # nothing else: no real catdoc or LibreOffice
    for mod, name in ((ingest, "DECODE_SECONDS"), (ingest, "READ_SECONDS"), (speech, "ENCODE_SECONDS")):
        monkeypatch.setattr(mod, name, 0.5)
    with pytest.raises(RuntimeError, match="longer than"):
        ingest.decode(tmp_path / "a.wav")
    with pytest.raises(speech.ProviderError, match="longer than"):
        speech.encode(np.zeros(1600, dtype=np.float32))
    monkeypatch.setitem(sys.modules, "pypdf", None)  # pdftotext is the fallback
    with pytest.raises(SystemExit, match="longer than"):
        ingest.read_pdf(tmp_path / "a.pdf")
    (tmp_path / "a.doc").write_bytes(b"x")
    with pytest.raises(SystemExit):  # antiword gave up; no catdoc or LibreOffice here
        ingest.read_doc(tmp_path / "a.doc")


def test_the_namespaces_own_names_go_to_the_engine(db, cfg, fake, monkeypatch):
    """With transcribe.vocabulary on, each recording is transcribed knowing its namespace's custom vocabulary, named
    people, defined entities and the names people fixed in its lines; off (the default), nothing is sent."""
    from app.domain import vocabulary

    monkeypatch.setattr(speech, "POLL_SECONDS", 0)
    nid = store.ns_id(db, "family")
    db.q("CREATE speaker CONTENT {space: $s, name: 'Siobhan'}; CREATE speaker CONTENT {space: $s, name: 'Speaker 2'}", s=nid)
    db.q("CREATE entity CONTENT {space: $s, name: 'Llanfair', key: 'llanfair', type: 'PLACE', defined: true}", s=nid)
    rid = db.next_id("recording")
    db.q("CREATE $r CONTENT {space: $s, title: 'call', status: 'transcribed'}", r=R("recording", rid), s=nid)
    db.q(
        "CREATE segment_edit CONTENT {recording: $r, idx: 0, before: {text: 'ask shiv on'}, after: {text: 'Ask Siobhan and Aoife on'},"
        " by: 'a@example.com', at: time::now(), n: 1};"
        "CREATE segment_edit CONTENT {recording: $r, idx: 1, before: {text: 'um hello'}, after: {text: 'Hello Cleaned'},"
        " by: $c, at: time::now(), n: 2}",
        r=rid,
        c="text cleanup",
    )
    assert vocabulary.terms(db, cfg, nid) == []  # off by default
    settings.save(db, cfg, "transcribe", {"vocabulary": True}, "t")
    settings.save(db, cfg, "analysis", {"gazetteer": ["Ffion | PERSON"]}, "t")
    eff = provided(db, cfg, fake)
    words = vocabulary.terms(db, eff, nid)
    assert words == ["Ffion", "Siobhan", "Llanfair", "Aoife"]  # not "Speaker 2", not the cleanup's "Cleaned"
    assert vocabulary.prompt(words) == "Ffion, Siobhan, Llanfair, Aoife."

    speech.Engine(eff, "openai").transcribe(clip(), terms=words)
    assert b'name="prompt"\r\n\r\nFfion, Siobhan, Llanfair, Aoife.' in Fake.seen[-1][3]
    speech.Engine(eff, "deepgram").transcribe(clip(), terms=words)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(Fake.seen[-1][1]).query)
    assert q["keyterm"] == words and "keywords" not in q
    Fake.seen.clear()
    speech.Engine(eff, "assemblyai").transcribe(clip(), terms=words)
    asked = next(json.loads(b) for m, p, h, b in Fake.seen if p == "/aai/v2/transcript" and m == "POST")
    assert asked["word_boost"] == words

    class Hearing:
        name = "fake"
        heard = None

        def transcribe(self, audio, terms=()):
            Hearing.heard = list(terms)
            return [{"t0": 0, "t1": 900, "text": "Siobhan rang.", "lang": "en"}]

    monkeypatch.setattr(ingest, "audio_path", lambda *a, **k: "unused")
    monkeypatch.setattr(ingest, "decode", lambda p: clip())
    ingest.transcribe_one(db, eff, rid, log=lambda *a: None, engine=Hearing())
    assert Hearing.heard == words


def test_whisper_keeps_the_language_it_heard():
    """With no language set, the recording's language is the one Whisper detected; a language set in Settings wins."""
    from types import SimpleNamespace as NS

    from app.domain import ingest

    class Faster:
        def transcribe(self, audio, **kw):
            seg = NS(start=0.0, end=1.5, text=" Bonjour à tous.", words=[NS(word=" Bonjour", start=0.0, end=0.6)])
            return iter([seg]), NS(language="fr", language_probability=0.97)

    class Mlx:
        def transcribe(self, audio, **kw):
            return {"language": "de", "segments": [{"start": 0.0, "end": 1.0, "text": " Guten Tag.", "words": []}]}

    def engine(m, mlx, lang):
        w = object.__new__(ingest.Whisper)
        w.m, w.mlx, w.repo, w.lang, w.name = m, mlx, "repo", lang, "whisper"
        return w

    segs = engine(Faster(), False, None).transcribe(None)
    assert ([s["lang"] for s in segs], ingest._language(segs)) == (["fr"], "fr")
    assert [s["lang"] for s in engine(Mlx(), True, None).transcribe(None)] == ["de"]
    assert [s["lang"] for s in engine(Faster(), False, "en").transcribe(None)] == ["en"]  # the setting, as before


def test_speech_calls_are_costed_per_minute_heard_and_per_character_read(db, cfg, fake):
    provided(db, cfg, fake)
    prices = {
        "deepgram:nova-3": {"unit": "minute", "per_minute": 0.0043},
        "elevenlabs:eleven_multilingual_v2": {"unit": "characters", "per_million": 100},
    }
    settings.save(db, cfg, "telemetry", {"prices": prices}, "t")
    settings.save(db, cfg, "voice", {"tts_provider": "elevenlabs", "tts_voice": "v42"}, "t")
    eff = settings.effective(db, cfg)
    with activity.scope(db, "asset:1", cfg=eff):
        speech.Engine(eff, "deepgram").transcribe(talk(90, (40,)))
        voice.speak(eff, "Hello there")
    rows = {r["action"]: r for r in db.rows("SELECT * FROM activity") if r["action"] in ("speech.transcribe", "model.speech")}
    heard, read = rows["speech.transcribe"], rows["model.speech"]
    assert heard["detail"]["seconds"] == 90.0 and heard["cost_usd"] == pytest.approx(1.5 * 0.0043)
    assert read["detail"]["chars"] == 11 and read["cost_usd"] == pytest.approx(11 / 1e6 * 100)
    assert not heard.get("unpriced") and not read.get("unpriced")
    # a provider without a price isn't costed, as before
    settings.save(db, cfg, "telemetry", {"prices": {}}, "t")
    eff = settings.effective(db, cfg)
    with activity.scope(db, "asset:2", cfg=eff):
        voice.speak(eff, "Hello again")
    again = [r for r in db.rows("SELECT * FROM activity") if r["action"] == "model.speech" and r["resources"] == ["asset:2"]]
    assert [r.get("cost_usd") for r in again] == [None]
