"""Putting spoken clips together into one programme with ffmpeg: decode each clip, join them with short pauses (the
exact time each starts and ends comes out too), even out the loudness and encode the result."""

from __future__ import annotations

import subprocess

import numpy as np

from .ingest import MEDIA_DEMUXERS

SR = 24000  # speech models speak at 22–24 kHz
LOUDNESS = "loudnorm=I=-16:TP=-1.5:LRA=11"  # the usual target for spoken-word podcasts
SECONDS = 600  # a clip or an episode encodes in seconds; past this ffmpeg is stuck


def _ffmpeg(args, data, what):
    try:
        out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *args], input=data, capture_output=True, timeout=SECONDS)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg: {what} took longer than {SECONDS} s") from None
    except FileNotFoundError:
        raise RuntimeError("ffmpeg isn't installed") from None
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg ({what}): " + out.stderr.decode(errors="replace")[-300:])
    return out.stdout


def decode(data: bytes) -> np.ndarray:
    """An audio file's bytes (mp3, wav, ogg, …) as mono float samples at SR."""
    raw = _ffmpeg(
        ["-format_whitelist", MEDIA_DEMUXERS, "-i", "pipe:0", "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1", "-ar", str(SR), "-"],
        data,
        "decoding a clip",
    )
    return np.frombuffer(raw, dtype=np.float32)


def join(clips, pauses_ms):
    """The clips one after another, with pauses_ms[k] of silence before clip k (none before the first). Returns
    (samples, [(t0, t1)] in ms for each clip)."""
    parts, spans, n = [], [], 0
    for k, x in enumerate(clips):
        gap = int(SR * (pauses_ms[k] if k else 0) / 1000)
        if gap:
            parts.append(np.zeros(gap, dtype=np.float32))
            n += gap
        spans.append((round(n * 1000 / SR), round((n + len(x)) * 1000 / SR)))
        parts.append(np.asarray(x, dtype=np.float32))
        n += len(x)
    return (np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)), spans


def encode(samples) -> tuple[bytes, str, str]:
    """Samples at SR as one file with its loudness evened out: MP3, or Ogg Opus where ffmpeg has no MP3 encoder.
    Returns (bytes, extension, media type)."""
    pcm = np.ascontiguousarray(samples, dtype=np.float32).tobytes()
    last = None
    for codec, fmt, ext, ctype, extra in (
        ("libmp3lame", "mp3", "mp3", "audio/mpeg", ["-b:a", "96k"]),
        ("libopus", "ogg", "ogg", "audio/ogg", ["-b:a", "48k"]),
    ):
        try:
            data = _ffmpeg(
                [
                    "-f",
                    "f32le",
                    "-ar",
                    str(SR),
                    "-ac",
                    "1",
                    "-i",
                    "-",
                    "-af",
                    LOUDNESS,
                    "-ar",
                    str(SR),
                    "-c:a",
                    codec,
                    *extra,
                    "-f",
                    fmt,
                    "-",
                ],
                pcm,
                "encoding the episode",
            )
            return data, ext, ctype
        except RuntimeError as e:
            last = e
    raise last or RuntimeError("ffmpeg: no encoder")
