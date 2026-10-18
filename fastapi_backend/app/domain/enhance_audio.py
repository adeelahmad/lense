"""Enhancing low quality sound before it's transcribed (docs/enhance.md): the enhance step listens to a recording's
audio (or a video's soundtrack) and, when it's noisy, quiet or clipped, makes a cleaned-up copy that the transcribe
step hears instead. The original stays as it is, and so do its times: every filter keeps the copy sample for sample in
step with it, so the words land where they were said. The copy is a rendition, data_dir/renditions/<resource>-
enhanced.flac, 16 kHz mono like what transcription reads (encrypted where the namespace's files are).

Off by default: Settings → Enhance, `enhance.audio`. enhance.py has the settings, the step and what's kept.

What counts as low quality (each a reason kept in recording.enhanced.low), measured over 30 ms frames:
- `noisy`: speech is less than enhance.min_snr_db above the noise floor (the loudest frames, the 95th percentile,
  against the quietest, the 10th).
- `quiet`: the loud half of it is under enhance.min_level_db (dBFS).
- `clipped`: more than CLIPPED of its samples are at full scale.

The engine (`enhance.audio_engine`):
- `ffmpeg` (the default): ffmpeg's own filters, nothing to fetch. adeclip repairs clipping, a high-pass filter takes
  out rumble and hum below 70 Hz, afftdn takes out steady noise (from the floor measured), a quiet one is turned up
  towards LEVEL (as far as its peak allows), and dynaudnorm evens out the level, quiet parts more.
- `webrtc`: WebRTC's audio processing (webrtc-noise-gain, Apache-2.0; WebRTC itself is BSD-3-Clause): its noise
  suppression, high-pass filter and automatic gain, 10 ms at a time, and its voice detector, which says how much of
  it is speech. components.py fetches it the first time it's chosen (Linux on x86-64 and ARM64).

Both are calibrated once per chain: a probe of clicks and bursts goes through it, and however late it comes out is cut
from the copy's start and added back at its end. Echo cancellation needs what the far end of a call said, which a
recording doesn't have, so neither does that.
"""

from __future__ import annotations

import functools
import subprocess
import tempfile

import numpy as np

from . import ingest

SR = ingest.SR  # what transcription reads: 16 kHz mono
FRAME = SR * 30 // 1000  # 30 ms
CLIPPED = 0.001  # more than this share of samples at full scale is clipped
SILENT = -70.0  # dBFS: the loudest frames under this are silence, with nothing to enhance
LEVEL = -20.0  # dBFS: what a quiet recording's loud half is brought up to, as far as its peak lets it
HIGHPASS = 70  # Hz
NR = 12  # afftdn's noise reduction, dB; less when there's no noise to speak of
ENGINES = ("ffmpeg", "webrtc")
REASONS = {"noisy": "noisy", "quiet": "quiet", "clipped": "clipped"}
CHUNK = SR * 4 * 10  # read 10 s of float samples at a time
TIMEOUT = ingest.DECODE_SECONDS
_FFMPEG = ["ffmpeg", "-nostdin", "-v", "error"]


def _decoder(path, fmt="f32le", af=None):
    """ffmpeg reading a file's sound as 16 kHz mono samples on its stdout (errors to a file, so it never stalls)."""
    err = tempfile.TemporaryFile()
    cmd = [*_FFMPEG, "-format_whitelist", ingest.MEDIA_DEMUXERS, "-i", str(path), "-vn", "-sn", "-dn"]
    cmd += ["-af", af] if af else []
    cmd += ["-f", fmt, "-ac", "1", "-ar", str(SR), "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err), err


def _finish(proc, err):
    try:
        proc.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"ffmpeg took longer than {TIMEOUT} s") from None
    if proc.returncode:
        err.seek(0)
        raise RuntimeError("ffmpeg: " + err.read().decode(errors="replace")[-300:])
    err.close()


# ---------- how good it sounds ----------
def measure(path):
    """How good a file's sound is: its `duration` (s), `snr_db` (the loudest frames over the quietest), `level_db` (the
    loud half, dBFS), `floor_db` (the quietest frames), `peak_db` and `clipped` (the share of samples at full scale).
    Read 10 s at a time, so a long recording needs little memory."""
    proc, err = _decoder(path)
    levels, rest, n, clipped, peak = [], np.empty(0, np.float32), 0, 0, 0.0
    assert proc.stdout
    for chunk in iter(lambda: proc.stdout.read(CHUNK), b""):
        x = np.frombuffer(chunk[: len(chunk) // 4 * 4], np.float32)
        n += len(x)
        a = np.abs(x)
        clipped += int(np.count_nonzero(a >= 0.999))
        peak = max(peak, float(a.max(initial=0)))
        x = np.concatenate([rest, x])
        k = len(x) // FRAME
        if k:
            f = x[: k * FRAME].reshape(k, FRAME).astype(np.float64)
            levels.append(np.sqrt(np.mean(f * f, axis=1)))
        rest = x[k * FRAME :]
    _finish(proc, err)
    return summarize(np.concatenate(levels) if levels else np.empty(0), n, clipped, peak)


def _db(v):
    return 20 * np.log10(np.maximum(v, 1e-5))


def summarize(rms, n, clipped, peak):
    """measure()'s numbers from each frame's RMS level, how many samples, how many at full scale, and the peak."""
    if not len(rms):
        return {"duration": round(n / SR, 2), "snr_db": 0.0, "level_db": -100.0, "floor_db": -100.0, "peak_db": -100.0, "clipped": 0.0}
    db = _db(rms)
    floor, top = float(np.percentile(db, 10)), float(np.percentile(db, 95))
    loud = rms[db >= floor + (top - floor) / 2]
    return {
        "duration": round(n / SR, 2),
        "snr_db": round(top - floor, 1),
        "level_db": round(float(_db(np.sqrt(np.mean(loud * loud)))), 1),
        "floor_db": round(floor, 1),
        "peak_db": round(float(_db(peak)), 1),
        "clipped": round(clipped / max(1, n), 5),
    }


def silent(m):
    return m["level_db"] < SILENT


def assess(m, opts):
    """The reasons a recording's sound is low quality."""
    low = []
    if m["snr_db"] < opts["min_snr_db"]:
        low.append("noisy")
    if m["level_db"] < opts["min_level_db"]:
        low.append("quiet")
    if m["clipped"] > CLIPPED:
        low.append("clipped")
    return low


def said(m):
    return f"signal to noise {m['snr_db']:g} dB, level {m['level_db']:g} dBFS, {m['clipped'] * 100:g}% clipped"


# ---------- engines ----------
def filters(low, m):
    """ffmpeg's filters for these reasons (none: on request, a gentle clean-up of everything)."""
    f = ["adeclip"] if "clipped" in low else []
    f.append(f"highpass=f={HIGHPASS}")
    if "noisy" in low or not low:
        f.append(f"afftdn=nr={NR if 'noisy' in low else NR // 2}:nf={int(min(-20, max(-80, round(m['floor_db']))))}")
    if "quiet" in low:
        f.append(f"volume={max(0.0, min(LEVEL - m['level_db'], -1 - m['peak_db'])):.1f}dB")
    if "quiet" in low or not low:
        f.append("dynaudnorm=f=250:g=31:m=10:p=0.9")
    return f


def _probe():
    """Two seconds of 16 kHz sound that any of these chains lets through: noise bursts of several levels and a few
    clicks, over a faint hiss."""
    rng = np.random.default_rng(7)
    x = rng.normal(0, 0.003, 2 * SR).astype(np.float32)
    for i, at in enumerate((0.15, 0.4, 0.7, 1.05, 1.3, 1.6)):
        a = int(at * SR)
        x[a : a + SR // 20] += rng.normal(0, 0.05 * (1 + i % 3), SR // 20).astype(np.float32)
    for at in (0.55, 1.2, 1.85):
        x[int(at * SR)] = 0.8
    return x


def lag(x, y, most=SR // 4):
    """How many samples late `y` comes out against `x` (from -most to most), by cross-correlation."""
    n = min(len(x), len(y))
    a, b = x[:n] - x[:n].mean(), y[:n] - y[:n].mean()
    c = np.fft.irfft(np.fft.rfft(b, 2 * n) * np.conj(np.fft.rfft(a, 2 * n)))
    c = np.concatenate([c[-most:], c[: most + 1]])
    return int(np.argmax(c)) - most


def _wav(x):
    import io
    import wave

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return out.getvalue()


@functools.lru_cache(maxsize=32)
def ffmpeg_delay(chain):
    """How many samples late this chain of ffmpeg filters puts its output (afftdn's analysis window, say)."""
    x = _probe()
    out = subprocess.run(
        [*_FFMPEG, "-f", "wav", "-i", "-", "-af", chain, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        input=_wav(x),
        capture_output=True,
        timeout=60,
    )
    if out.returncode:
        raise RuntimeError("ffmpeg: " + out.stderr.decode(errors="replace")[-300:])
    return max(0, lag(x, np.frombuffer(out.stdout, np.float32)))


def run_ffmpeg(src, dst, low, m):
    """Write the copy with ffmpeg's filters: FLAC, 16 kHz mono, in step with the original. The filters it ran."""
    chain = filters(low, m)
    af = ",".join(chain)
    late = ffmpeg_delay(af)
    full = f"aresample={SR},aformat=channel_layouts=mono,{af}" + (f",atrim=start_sample={late},apad=pad_len={late}" if late else "")
    out = subprocess.run(
        [*_FFMPEG, "-y", "-format_whitelist", ingest.MEDIA_DEMUXERS, "-i", str(src), "-vn", "-sn", "-dn", "-af", full]
        + ["-ac", "1", "-ar", str(SR), "-c:a", "flac", "-sample_fmt", "s16", "-f", "flac", str(dst)],
        capture_output=True,
        timeout=TIMEOUT,
    )
    if out.returncode:
        raise RuntimeError("ffmpeg: " + out.stderr.decode(errors="replace")[-300:])
    return chain


def webrtc_installed():
    try:
        import webrtc_noise_gain  # noqa: F401
    except ImportError:
        return False
    return True


def _webrtc_settings(low):
    """(automatic gain's target, dB under full scale, or 0 for none; noise suppression from 0 to 4)."""
    return (15 if "quiet" in low else 0), (3 if "noisy" in low or not low else 1)


def _processor(low):
    from webrtc_noise_gain import AudioProcessor

    return AudioProcessor(*_webrtc_settings(low))


def _through(proc, pcm):
    """16-bit samples through a WebRTC processor 10 ms at a time (the last part padded with silence, then cut back):
    (what came out, how many 10 ms frames were speech, how many frames)."""
    out, speech, frames, size = bytearray(), 0, 0, SR // 100 * 2
    for i in range(0, len(pcm), size):
        part = pcm[i : i + size]
        got = proc.Process10ms(part + bytes(size - len(part)))
        out += got.audio[: len(part)]
        speech += bool(got.is_speech)
        frames += 1
    return bytes(out), speech, frames


@functools.lru_cache(maxsize=8)
def webrtc_delay(gain, ns):
    from webrtc_noise_gain import AudioProcessor

    x = _probe()
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()
    y, _, _ = _through(AudioProcessor(gain, ns), pcm)
    return max(0, lag(x, np.frombuffer(y, np.int16).astype(np.float32) / 32768))


def run_webrtc(src, dst, low, m):
    """Write the copy through WebRTC's audio processing (adeclip first, where it's clipped), in step with the original.
    The share of it that's speech."""
    proc, err = _decoder(src, fmt="s16le", af="adeclip" if "clipped" in low else None)
    gain, ns = _webrtc_settings(low)
    late = webrtc_delay(gain, ns) * 2  # bytes
    enc = subprocess.Popen(
        [*_FFMPEG, "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-", "-c:a", "flac", "-f", "flac", str(dst)],
        stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdout and enc.stdin
    wr, speech, frames, skip, carry = _processor(low), 0, 0, late, b""
    try:
        for chunk in iter(lambda: proc.stdout.read(CHUNK // 2), b""):
            chunk = carry + chunk
            whole = len(chunk) // (SR // 100 * 2) * (SR // 100 * 2)
            chunk, carry = chunk[:whole], chunk[whole:]
            y, s, f = _through(wr, chunk)
            speech, frames = speech + s, frames + f
            cut = min(skip, len(y))
            skip -= cut
            enc.stdin.write(y[cut:])
        if carry:
            y, s, f = _through(wr, carry)
            speech, frames = speech + s, frames + f
            enc.stdin.write(y[min(skip, len(y)) :])
        enc.stdin.write(bytes(late - skip))  # what was cut from the start, as silence at the end
        enc.stdin.close()
    except BaseException:
        enc.kill()
        proc.kill()
        raise
    _finish(proc, err)
    if enc.wait(timeout=TIMEOUT):
        raise RuntimeError("ffmpeg couldn't write the enhanced copy")
    return round(speech / max(1, frames), 3)
