"""Talking to Lens: what someone said into the mic, as text, by the server's own speech-to-text engine; and answers
read aloud by a text-to-speech model when one is set up (else the browser reads them).

Speech stays on the server by default: the clip is decoded with ffmpeg and transcribed by the engine that transcribes
recordings (SenseVoice, Whisper), kept loaded while people are talking and let go after VOICE_IDLE_SECONDS. voice.stt
can pick another engine for talking, including a speech provider (domain/speech.py), and voice.tts_provider who reads
answers aloud.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import subprocess
from collections import deque
from contextlib import contextmanager

import numpy as np
import threading
import time

from . import ingest, speech

log = logging.getLogger(__name__)
IDLE_SECONDS = 600
MAX_BYTES = 25 * 1024 * 1024
MAX_SECONDS = 300
DECODE_SECONDS = 30
# Deliberately per process. Deployments with multiple workers need an edge/global budget too.
_JOBS = threading.BoundedSemaphore(2)
_UPLOADS = threading.BoundedSemaphore(2)
_RATE_LOCK = threading.Lock()
_PUBLIC_JOBS = deque()
PUBLIC_PER_MINUTE = 10


class Busy(RuntimeError):
    pass


class ClipTooLong(ValueError):
    pass


class InvalidClip(ValueError):
    pass


@contextmanager
def admission(anonymous=False):
    """No waiting queue; anonymous callers share a budget, never trust forwarding headers."""
    if not _JOBS.acquire(blocking=False):
        raise Busy("voice service is busy; try again shortly")
    try:
        if anonymous:
            with _RATE_LOCK:
                now = time.monotonic()
                while _PUBLIC_JOBS and _PUBLIC_JOBS[0] <= now - 60:
                    _PUBLIC_JOBS.popleft()
                if len(_PUBLIC_JOBS) >= PUBLIC_PER_MINUTE:
                    raise Busy("public voice request limit reached; try again in a minute")
                _PUBLIC_JOBS.append(now)
        yield
    finally:
        _JOBS.release()


@contextmanager
def upload_admission():
    """Bound in-memory request bodies separately from running provider jobs."""
    if not _UPLOADS.acquire(blocking=False):
        raise Busy("voice service is busy; try again shortly")
    try:
        yield
    finally:
        _UPLOADS.release()


def run_admitted(function, *args, anonymous=False):
    """The worker owns its slot until it finishes, even if its HTTP waiter is cancelled."""
    with admission(anonymous=anonymous):
        return function(*args)


def decode_clip(path):
    """Decode at most 301 seconds, not an entire potentially hours-long compressed upload.

    Only an uploaded local media file is accepted; playlists and network protocols cannot fetch other resources.
    """
    try:
        out = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
             "-format_whitelist", ingest.MEDIA_DEMUXERS, "-i", str(path),
             "-t", str(MAX_SECONDS + 1), "-f", "f32le", "-acodec", "pcm_f32le",
             "-ac", "1", "-ar", str(ingest.SR), "-"],
            capture_output=True, timeout=DECODE_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise InvalidClip("audio decoding timed out") from None
    except OSError:
        raise RuntimeError("audio decoder is unavailable") from None
    if out.returncode or len(out.stdout) % 4:
        raise InvalidClip("could not decode audio")
    audio = np.frombuffer(out.stdout, dtype=np.float32)
    if len(audio) > MAX_SECONDS * ingest.SR:
        raise ClipTooLong("clips must be no longer than 300 seconds")
    return audio

_LOCK = threading.Lock()
_ENGINE = {"key": None, "engine": None, "used": 0.0}


def _talk(cfg):
    """The settings with the engine voice chat uses (voice.stt, else the transcription engine)."""
    stt = (cfg.get("voice") or {}).get("stt") or "same"
    if stt == "same" or stt == cfg["transcribe"]["engine"]:
        return cfg
    return {**cfg, "transcribe": {**cfg["transcribe"], "engine": stt}}


def _key(cfg):
    """What the loaded engine depends on: the transcription settings, except how the transcript marks unsure words."""
    t = {k: v for k, v in cfg["transcribe"].items() if k != "confidence_highlight"}
    return json.dumps([t, cfg.get("speech")], sort_keys=True, default=str)


def can_transcribe(cfg):
    """The engine that would transcribe what's said here, or None when none is installed."""
    if (cfg.get("voice") or {}).get("input") == "browser":
        return None
    cfg = _talk(cfg)
    e = cfg["transcribe"]["engine"]
    if e in speech.PROVIDERS:
        return e if speech.ready(cfg, e) else None
    for x in [e] + [x for x in ingest.ENGINE_MODULES if x != e]:
        try:
            if x in ingest.ENGINE_MODULES and ingest.installed(x):
                return x
        except (ValueError, ImportError):
            continue
    return None


def engine(cfg):
    """The loaded engine, loaded on first use (or when the transcription settings changed)."""
    cfg = _talk(cfg)
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
        raise ClipTooLong("clips must be no larger than 25 MiB")
    fd, path = tempfile.mkstemp(prefix="lens-voice-", suffix=".clip")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        audio = decode_clip(path)
    finally:
        os.unlink(path)
    seconds = len(audio) / ingest.SR
    if seconds < 0.3:
        return {"text": "", "language": None, "engine": None, "seconds": round(seconds, 2)}
    try:
        e = engine(cfg)
    except ingest.EngineMissing:
        raise RuntimeError("speech-to-text engine is unavailable") from None
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
    return speech.tts_ready(cfg)


def speak(cfg, text, voice=None):
    """The text read aloud by the text-to-speech provider (an OpenAI-compatible /audio/speech, ElevenLabs, Deepgram,
    Gemini or macOS say), in `voice` (else the settings'), as (bytes, media type); None when there's none or it failed,
    so the browser reads it instead."""
    if not can_speak(cfg) or not text.strip():
        return None
    try:
        return speech.speak(cfg, text, voice=voice)  # it writes its own row in the activity ledger
    except speech.ProviderError as e:
        log.info("voice: text-to-speech failed, the browser reads it: %s", e)
        return None
