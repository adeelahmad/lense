"""StyleTTS2 behind the OpenAI audio API, for Lens (docs/speech-providers.md#styletts2).

POST /v1/audio/speech {input, voice}  the text read aloud, as 24 kHz WAV
GET  /v1/audio/voices                 {"voices": ["default", ...]}: "default" and each clip in /voices
GET  /health                          {"ok": true, "loaded": whether the model is in memory}

A voice is a few seconds of someone speaking (a .wav, .mp3 or .flac in /voices, named after the voice): StyleTTS2
reads in a voice like it. "default" is a public-domain LibriVox reader. The model loads on the first request and
stays loaded; requests are read one at a time.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import re
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VOICES = pathlib.Path(os.environ.get("STYLETTS2_VOICES", "/voices"))
PORT = int(os.environ.get("PORT", "8880"))
MAX_CHARS = 4000
AUDIO = (".wav", ".mp3", ".flac")
NAME = re.compile(r"^[\w .-]{1,80}$")
SR = 24000

_lock = threading.Lock()
_state: dict = {"model": None, "styles": {}}


def voices():
    found = sorted(
        {
            p.stem
            for p in VOICES.glob("*")
            if p.suffix.lower() in AUDIO and NAME.match(p.stem)
        }
    )
    return ["default", *[v for v in found if v != "default"]]


def _clip(name):
    for ext in AUDIO:
        p = VOICES / f"{name}{ext}"
        if p.is_file():
            return p
    return None


def read(text, voice):
    """The text in the voice, as 16-bit WAV. Raises LookupError for a voice there's no clip of."""
    with _lock:
        if _state["model"] is None:
            from styletts2 import tts  # slow: only when first asked

            _state["model"] = tts.StyleTTS2()
        m = _state["model"]
        if voice not in _state["styles"]:
            if voice == "default":
                from cached_path import cached_path
                from styletts2.tts import DEFAULT_TARGET_VOICE_URL

                path = cached_path(DEFAULT_TARGET_VOICE_URL)
            else:
                path = _clip(voice) if NAME.match(voice) else None
                if path is None:
                    raise LookupError(voice)
            _state["styles"][voice] = m.compute_style(str(path))
        samples = m.inference(text, ref_s=_state["styles"][voice])
    import numpy as np

    pcm = (
        (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767)
        .astype("<i2")
        .tobytes()
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(
            f"styletts2: {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}",
            flush=True,
        )

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code, message):
        self._send(code, {"error": {"message": message}})

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path == "/health":
            return self._send(200, {"ok": True, "loaded": _state["model"] is not None})
        if path in ("/v1/audio/voices", "/audio/voices"):
            return self._send(200, {"voices": voices()})
        self._error(404, "not found")

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/v1/audio/speech", "/audio/speech"):
            return self._error(404, "not found")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
        except ValueError:
            return self._error(400, "the body is JSON: {input, voice}")
        text = body.get("input") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            return self._error(400, "input is the text to read")
        voice = body.get("voice") or "default"
        if not isinstance(voice, str):
            return self._error(400, "voice is a voice's name")
        try:
            audio = read(text.strip()[:MAX_CHARS], voice)
        except LookupError:
            return self._error(
                404,
                f"no voice {voice!r}: put a clip named {voice}.wav in the voices folder",
            )
        except Exception as e:  # noqa: BLE001 - the caller gets the reason; the server keeps going
            return self._error(500, f"StyleTTS2 couldn't read it: {e}")
        self._send(200, audio, "audio/wav")


if __name__ == "__main__":
    print(f"styletts2: listening on :{PORT}; voices in {VOICES}", flush=True)
    ThreadingHTTPServer(
        ("0.0.0.0", PORT), Handler
    ).serve_forever()  # inside its container
