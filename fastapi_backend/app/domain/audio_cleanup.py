"""Cleaning up a recording's sound before it's transcribed: the clean_audio step (Settings → Transcription → Audio cleanup).

A phone on a table, a fan, a quiet speaker across the room, a call recorded with the other side's voice coming back
through the speakers: speech-to-text hears all of it. The clean_audio step runs the recording through WebRTC's audio
processing, the same code a browser runs on a call, through pywebrtc-audio (Apache-2.0, about 0.5 MB, numpy only,
fetched the first time the step runs). Every option is in the settings:

- noise: noise suppression, off or low, moderate, high or very high (6, 12, 18 or 21 dB).
- gain: automatic gain control, so a quiet voice is as loud as a near one.
- high_pass: takes out the rumble below speech (traffic, handling noise, a desk being knocked).
- echo: echo cancellation, for a two-channel recording with what the speakers played on one channel (left or right)
  and the microphone on the other, as call recorders keep them: the far side's voice is taken out of the microphone,
  then both sides are mixed for transcription.
- trim_silence: the stretches with nobody speaking (min_silence_ms or longer, by WebRTC's voice detection) are left
  out of what's transcribed, so a cloud engine bills fewer minutes, a local one finishes sooner and Whisper has no
  silence to imagine words in. The transcript's times are put back on the recording's own timeline.

The step itself measures how much of the recording is speech and keeps the options it ran with on the recording
(`audio_cleanup`); from then on transcribing it hears the audio cleaned up that way. The recording's file is never
changed and no second copy is stored: the cleaning takes a fraction of a second per minute, so it's done again each
time. Speaker separation and voice IDs still listen to the original. Running the step with every option off puts the
recording back to being transcribed as it is.
"""

from __future__ import annotations

import importlib.metadata
import math

import numpy as np

from . import components, ingest, store

R = store.R
SR = ingest.SR
FRAME = SR // 100  # WebRTC works in 10 ms frames
WINDOW = 10  # frames: voice detection is read per tenth of a second
THRESHOLD = 0.4  # a tenth of a second whose mean speech probability reaches this is speech
PAD_MS = 300  # kept either side of speech, so a word's first and last sounds aren't cut
NOISE = {"off": None, "low": 0, "moderate": 1, "high": 2, "very_high": 3}  # WebRTC's levels: 6, 12, 18 and 21 dB
ECHO = ("off", "left", "right")  # which channel holds what the speakers played
DEFAULTS = store.DEFAULTS["transcribe"]["cleanup"]
KEYS = ("noise", "gain", "high_pass", "echo", "trim_silence", "min_silence_ms", "only_if_poor", "snr_floor_db")
PACKAGE = "pywebrtc-audio"
QUIET_PCT = 10  # the loudness at this percentile of the 10 ms frames is taken as the noise floor
LOUD_PCT = 95  # the loudness at this percentile is taken as the speech level
CLIP = 0.99  # a sample at least this loud (of 1.0) counts as clipped
CLIP_MAX = 0.5  # more than this percent of samples clipped is poor quality worth cleaning


def check(value):
    """transcribe.cleanup as it's saved: ValueError naming the first thing wrong with it."""
    if not isinstance(value, dict):
        raise ValueError("transcribe.cleanup is {noise, gain, high_pass, echo, trim_silence, min_silence_ms}")
    extra = sorted(set(value) - set(KEYS))
    if extra:
        raise ValueError(f"transcribe.cleanup has no setting {extra[0]}")
    out = {**DEFAULTS, **value}
    if out["noise"] not in NOISE:
        raise ValueError(f"transcribe.cleanup.noise is one of: {', '.join(NOISE)}")
    if out["echo"] not in ECHO:
        raise ValueError(f"transcribe.cleanup.echo is one of: {', '.join(ECHO)}")
    for k in ("gain", "high_pass", "trim_silence", "only_if_poor"):
        if not isinstance(out[k], bool):
            raise ValueError(f"transcribe.cleanup.{k} is true or false")
    ms = out["min_silence_ms"]
    if not (isinstance(ms, int) and not isinstance(ms, bool) and 500 <= ms <= 60000):
        raise ValueError("transcribe.cleanup.min_silence_ms is a whole number of milliseconds from 500 to 60000")
    snr = out["snr_floor_db"]
    if not (isinstance(snr, (int, float)) and not isinstance(snr, bool) and 0 <= snr <= 60):
        raise ValueError("transcribe.cleanup.snr_floor_db is a number of decibels from 0 to 60")
    return out


def options(cfg):
    """The audio cleanup settings, with defaults for what isn't set (a bad saved value falls back to its default)."""
    mine = dict(((cfg.get("transcribe") or {}).get("cleanup")) or {})
    out = dict(DEFAULTS)
    for k in KEYS:
        if k in mine:
            try:
                out = check({**out, k: mine[k]})
            except ValueError:
                pass
    return out


def does_anything(o):
    return o["noise"] != "off" or o["gain"] or o["high_pass"] or o["echo"] != "off" or o["trim_silence"]


def quality(mono):
    """A cheap read of a recording's sound before it's cleaned: (estimated SNR in dB or None, percent of samples
    clipped). The noise floor and the speech level are the loudness at two percentiles of the 10 ms frames; numpy
    only, no model. SNR is None when the recording is too short or too quiet to judge."""
    if not len(mono):
        return None, 0.0
    x = np.abs(np.asarray(mono, dtype=np.float32))
    clip_pct = round(100 * float(np.mean(x >= CLIP)), 2)
    n = len(x) // FRAME
    if n < 2:
        return None, clip_pct
    rms = np.sqrt(np.square(x[: n * FRAME].reshape(n, FRAME)).mean(axis=1))
    rms = rms[rms > 0]
    if len(rms) < 2:
        return None, clip_pct
    noise = float(np.percentile(rms, QUIET_PCT))
    speech = float(np.percentile(rms, LOUD_PCT))
    if noise <= 0 or speech <= 0:
        return None, clip_pct
    return round(20 * math.log10(speech / noise), 1), clip_pct


def is_poor(o, snr_db, clip_pct):
    """Whether a recording measures as low quality, so the quality gate still cleans it. Clipping is poor; an SNR that
    couldn't be judged counts as poor too, so a doubtful recording is cleaned rather than skipped."""
    if clip_pct > CLIP_MAX:
        return True
    if snr_db is None:
        return True
    return snr_db < o["snr_floor_db"]


def ready():
    return components.importable("pywebrtc_audio")


def ensure(cfg, say=None):
    """pywebrtc-audio, fetched now if this worker hasn't got it (it's small); RuntimeError when it can't be had."""
    if ready():
        return
    try:
        components.pip_install(cfg, "webrtc", say)
    except (components.Unavailable, RuntimeError, OSError) as e:
        raise RuntimeError(f"{PACKAGE} isn't installed and couldn't be fetched ({e})") from None
    if not ready():
        raise RuntimeError(f"{PACKAGE} was fetched but doesn't load on this machine")


def version():
    try:
        return importlib.metadata.version(PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return None


def process(near, o, far=None):
    """Samples (16 kHz mono float) through WebRTC's processing with these options: (the cleaned samples, each 10 ms
    frame's speech probability, the gain applied at the end in dB or None). With `far` (what the speakers played,
    the same length), the echo of it is taken out of `near`."""
    from pywebrtc_audio import AudioProcessor

    level = NOISE[o["noise"]]
    ap = AudioProcessor(
        sample_rate=SR,
        noise_suppression=level is not None,
        ns_level=level or 0,
        auto_gain_control=bool(o["gain"]),
        high_pass_filter=bool(o["high_pass"]),
        echo_cancellation=far is not None,
    )
    x = np.ascontiguousarray(near, dtype=np.float32)
    f = None if far is None else np.ascontiguousarray(far, dtype=np.float32)
    out = np.empty_like(x)
    probs = np.empty((len(x) + FRAME - 1) // FRAME, dtype=np.float32)
    for k, i in enumerate(range(0, len(x), FRAME)):
        out[i : i + FRAME] = ap.process(x[i : i + FRAME]) if f is None else ap.process(x[i : i + FRAME], f[i : i + FRAME])
        probs[k] = ap.speech_probability
    gain = round(float(ap.gain_db), 1) if o["gain"] and len(x) else None
    return out, probs, gain


def _loud(probs):
    """Whether each tenth of a second is speech."""
    k = -(-len(probs) // WINDOW)
    padded = np.pad(probs, (0, k * WINDOW - len(probs)), constant_values=0)
    return padded.reshape(k, WINDOW).mean(axis=1) >= THRESHOLD


def speech_ms(probs):
    return int(_loud(probs).sum()) * WINDOW * 10 if len(probs) else 0


def speech_spans(probs, min_silence_ms, total_ms):
    """Where there's speech, from the frames' speech probabilities: [(start, end)] in ms, each padded by PAD_MS, with
    pauses shorter than min_silence_ms kept in."""
    if not len(probs):
        return []
    loud = _loud(probs)
    k = len(loud)
    step = WINDOW * 10  # ms per window
    out = []
    i = 0
    while i < k:
        if not loud[i]:
            i += 1
            continue
        j = i
        while j < k and loud[j]:
            j += 1
        a, b = max(0, i * step - PAD_MS), min(total_ms, j * step + PAD_MS)
        if out and a - out[-1][1] < min_silence_ms:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
        i = j
    if out and out[0][0] < min_silence_ms:  # a short silence at either end isn't worth a join
        out[0] = (0, out[0][1])
    if out and total_ms - out[-1][1] < min_silence_ms:
        out[-1] = (out[-1][0], total_ms)
    return out


def cut(audio, spans):
    """Only the samples in these spans (ms), one after another: (samples, joins), a join being (where it starts in
    what's kept, where it started in the recording, how long it is), all in samples."""
    parts, joins, at = [], [], 0
    for a, b in spans:
        s, e = a * SR // 1000, min(len(audio), b * SR // 1000)
        if e <= s:
            continue
        parts.append(audio[s:e])
        joins.append((at, s, e - s))
        at += e - s
    return (np.concatenate(parts) if parts else audio[:0]), joins


def to_recording(ms, joins, end=False):
    """A time in what was kept (ms) on the recording's own timeline. An end that falls on a join stays with what came
    before it."""
    t = ms * SR / 1000
    pick = joins[0]
    for j in joins:
        if j[0] < t or (j[0] == t and not end):
            pick = j
        else:
            break
    at, start, n = pick
    return int(round((start + min(max(t - at, 0), n)) * 1000 / SR))


def remap(segs, joins):
    """Segments transcribed from what was kept, on the recording's timeline (their words too)."""
    import json

    for s in segs:
        s["t0"], s["t1"] = to_recording(s["t0"], joins), to_recording(s["t1"], joins, end=True)
        if s.get("words"):
            words = json.loads(s["words"])
            s["words"] = json.dumps([[w, to_recording(a, joins), to_recording(b, joins, end=True)] for w, a, b in words])
    return segs


def _channels(path, o, channels):
    """(microphone, speakers) for echo cancellation, or None when it's off or the recording has one channel."""
    if o["echo"] == "off" or (channels or 1) < 2:
        return None
    st = ingest.decode(path, channels=2)
    far = 0 if o["echo"] == "left" else 1
    return np.ascontiguousarray(st[:, 1 - far]), np.ascontiguousarray(st[:, far])


def clean(o, path, mono, channels=1):
    """A recording's audio cleaned up with options o: (samples, frame speech probabilities, gain in dB, how echo was
    handled). `mono` is the recording decoded as it is; a two-channel one is decoded again for echo cancellation."""
    pair = _channels(path, o, channels)
    if pair is None:
        out, probs, gain = process(mono, o)
        echo = "no second channel" if o["echo"] != "off" else None
    else:
        near, far = pair
        out, probs, gain = process(near, o, far)
        out = np.clip((out + far) / 2, -1, 1).astype(np.float32)  # both sides of the call, without the echo
        echo = o["echo"]
    return out, probs, gain, echo


def hear(cfg, rec, path, mono, log=print):
    """What transcribing a recording hears: its audio cleaned up as its clean_audio step set, silence left out when it
    asked. Returns (samples, joins or None); the original when this worker can't run the cleanup."""
    o = {**DEFAULTS, **{k: v for k, v in (rec.get("audio_cleanup") or {}).items() if k in KEYS}}
    try:
        ensure(cfg, log)
    except RuntimeError as e:
        log(f"  transcribing the original audio: {e}")
        return mono, None
    out, probs, _, _ = clean(o, path, mono, rec.get("channels"))
    if not o["trim_silence"]:
        return out, None
    total = len(out) * 1000 // SR
    spans = speech_spans(probs, o["min_silence_ms"], total)
    if not spans:
        return out, None  # it heard no speech: the engine has the last word
    if spans == [(0, total)]:
        return out, None
    kept, joins = cut(out, spans)
    log(f"  {round((total - len(kept) * 1000 / SR) / 1000)} s of silence left out of what's transcribed")
    return kept, joins


def step(db, cfg, rid, say):
    """The clean_audio step: measure the recording through the cleanup and keep its options on it, or clear them when
    every option is off. Returns what it kept, or None."""
    o = options(cfg)
    r = R("recording", rid)
    if not does_anything(o):
        db.q("UPDATE $r SET audio_cleanup = NONE", r=r)
        return None
    rec = db.one("SELECT path, remote, space, channels, web FROM $r", r=r) or {}
    if (rec.get("web") or {}).get("media"):  # a video or episode given by its address: its media downloaded first
        from . import mediaurl

        mediaurl.ensure(db, cfg, rid, rec, say)
        rec = db.one("SELECT path, remote, space, channels FROM $r", r=r) or {}
    ensure(cfg, say)
    path = ingest.audio_path(db, cfg, rec)
    mono = ingest.decode(path)
    snr_db, clip_pct = quality(mono)
    if o["only_if_poor"] and not is_poor(o, snr_db, clip_pct):
        del mono
        db.q("UPDATE $r SET audio_cleanup = NONE", r=r)
        return {"skipped": True, "snr_db": snr_db, "clip_pct": clip_pct, "snr_floor_db": o["snr_floor_db"]}
    out, probs, gain, echo = clean(o, path, mono, rec.get("channels"))
    del mono
    total = len(out) * 1000 // SR
    spans = speech_spans(probs, o["min_silence_ms"], total)
    speech = speech_ms(probs)
    kept = sum(b - a for a, b in spans) if o["trim_silence"] and spans else total
    saved = store.clean(
        {
            **o,
            "duration_ms": total,
            "speech_ms": speech,
            "kept_ms": kept,
            "gain_db": gain,
            "snr_db": snr_db,
            "clip_pct": clip_pct,
            "echo_note": echo if echo and echo not in ECHO else None,
            "engine": f"{PACKAGE} {version() or ''}".strip(),
            "at": store.now(),
        }
    )
    db.q("UPDATE $r SET audio_cleanup = $a", r=r, a=saved)
    return saved


def summary(saved):
    """One line for the step's log: what it found and what transcribing will hear."""
    if not saved:
        return "every audio cleanup option is off: it's transcribed as it is"
    if saved.get("skipped"):
        snr = "its noise couldn't be judged" if saved.get("snr_db") is None else f"{saved['snr_db']} dB signal-to-noise"
        return f"good quality ({snr}, over the {saved['snr_floor_db']} dB floor): left as it is, transcribed from the original"
    total = max(saved["duration_ms"], 1)
    parts = [f"speech in {round(100 * saved['speech_ms'] / total)}% of {round(total / 60000, 1)} min"]
    if saved.get("snr_db") is not None:
        parts.append(f"{saved['snr_db']} dB signal-to-noise")
    if saved["noise"] != "off":
        parts.append(f"noise suppressed ({saved['noise'].replace('_', ' ')})")
    if saved.get("gain_db") is not None:
        parts.append(f"gain {saved['gain_db']:+.1f} dB")
    if saved.get("echo_note"):
        parts.append(f"echo not cancelled: {saved['echo_note']}")
    elif saved["echo"] != "off":
        parts.append(f"echo of the {saved['echo']} channel cancelled")
    if saved["trim_silence"] and saved["kept_ms"] < saved["duration_ms"]:
        parts.append(f"{round((saved['duration_ms'] - saved['kept_ms']) / 60000, 1)} min of silence left out of transcription")
    return "; ".join(parts)
