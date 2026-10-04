"""Talking to Lens: what someone said into the mic, as text, by the server's own speech-to-text engine; and answers
read aloud by a text-to-speech model when one is set up (else the browser reads them).

Speech stays on the server: the clip is decoded with ffmpeg and transcribed by the engine that transcribes recordings
(SenseVoice, Whisper), kept loaded while people are talking and let go after VOICE_IDLE_SECONDS.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request

from . import activity, ingest

log = logging.getLogger(__name__)
IDLE_SECONDS = 600
MAX_BYTES = 25 * 1024 * 1024
_LOCK = threading.Lock()
_ENGINE = {"key": None, "engine": None, "used": 0.0}


def _key(cfg):
    return json.dumps(cfg["transcribe"], sort_keys=True, default=str)


def can_transcribe(cfg):
    """The engine that would transcribe what's said here, or None when none is installed."""
    if (cfg.get("voice") or {}).get("input") == "browser":
        return None
    e = cfg["transcribe"]["engine"]
    for x in [e] + [x for x in ingest.ENGINE_MODULES if x != e]:
        try:
            if x in ingest.ENGINE_MODULES and ingest.installed(x):
                return x
        except (ValueError, ImportError):
            continue
    return None


def engine(cfg):
    """The loaded engine, loaded on first use (or when the transcription settings changed)."""
    with _LOCK:
        if _ENGINE["engine"] is None or _ENGINE["key"] != _key(cfg):
            _ENGINE["engine"] = None
            _ENGINE["engine"] = ingest.get_engine(cfg, log=log.info)
            _ENGINE["key"] = _key(cfg)
        _ENGINE["used"] = time.time()
        return _ENGINE["engine"]


def warm(cfg):
    """Load the engine in the background, so the first thing said isn't kept waiting."""
    if _ENGINE["engine"] is not None or not can_transcribe(cfg):
        return

    def go():
        try:
            engine(cfg)
        except Exception as e:  # noqa: BLE001 - the first clip will say what's wrong
            log.info("voice: couldn't load the speech-to-text engine: %s", e)

    threading.Thread(target=go, daemon=True, name="voice-warm").start()


def let_go():
    """Free the engine's memory when nobody has talked for a while."""
    with _LOCK:
        if _ENGINE["engine"] is not None and time.time() - _ENGINE["used"] > IDLE_SECONDS:
            _ENGINE.update(engine=None, key=None)


def transcribe(cfg, data):
    """What was said in a clip (any format ffmpeg reads: webm, ogg, mp4, wav). Returns {text, language, engine,
    seconds}."""
    if not data:
        return {"text": "", "language": None, "engine": None, "seconds": 0.0}
    if len(data) > MAX_BYTES:
        raise ValueError("that clip is too long; say it in shorter parts")
    fd, path = tempfile.mkstemp(prefix="lens-voice-", suffix=".clip")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        audio = ingest.decode(path)
    finally:
        os.unlink(path)
    seconds = len(audio) / ingest.SR
    if seconds < 0.3:
        return {"text": "", "language": None, "engine": None, "seconds": round(seconds, 2)}
    e = engine(cfg)
    t = threading.Timer(IDLE_SECONDS + 5, let_go)
    t.daemon = True
    t.start()
    segs = e.transcribe(audio)
    text = " ".join(s["text"].strip() for s in segs if s.get("text")).strip()
    langs = [s.get("lang") for s in segs if s.get("lang")]
    return {
        "text": text,
        "language": langs[0] if langs else None,
        "engine": getattr(e, "name", type(e).__name__.lower()),
        "seconds": round(seconds, 2),
    }


def can_speak(cfg):
    v = cfg.get("voice") or {}
    return bool(v.get("tts_model") and (v.get("tts_base_url") or cfg["llm"].get("base_url")))


def speak(cfg, text):
    """The text read aloud by the text-to-speech model (an OpenAI-compatible /audio/speech), as (bytes, media type);
    None when there's none or it failed, so the browser reads it instead."""
    if not can_speak(cfg) or not text.strip():
        return None
    v = cfg["voice"]
    base = (v.get("tts_base_url") or cfg["llm"]["base_url"]).rstrip("/")
    key = v.get("tts_api_key") or (cfg["llm"].get("api_key") if not v.get("tts_base_url") else None)
    body = {"model": v["tts_model"], "input": text[:4000], "voice": v.get("tts_voice") or "alloy", "response_format": "mp3"}
    req = urllib.request.Request(
        base + "/audio/speech",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})},
        method="POST",
    )
    ledger = activity.call("model.speech", cfg, v["tts_model"], detail={"chars": len(body["input"])})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = r.read(), r.headers.get_content_type() or "audio/mpeg"
        ledger.end()
        return out
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        ledger.end(e)
        log.info("voice: text-to-speech failed, the browser reads it: %s", e)
        return None
