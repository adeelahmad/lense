"""Kokoro reads aloud on this machine (docs/speech-providers.md#kokoro): with voice.tts_provider "kokoro", spoken
answers and podcast hosts are read by Kokoro v1.0 (hexgrad's Kokoro-82M, Apache-2.0) on ONNX Runtime. No text leaves
the machine, nothing is billed, and there's no service to start.

It runs through kokoro-onnx (MIT): espeak-ng turns the text into phonemes, and the model reads them at 24 kHz in one of
its 54 voices. espeak-ng and phonemizer, which kokoro-onnx calls it through, are GPL-3.0, so all of it is the `kokoro`
extra: components.py fetches it, with the model and its voices, only once Kokoro is picked. The files are the ones
kokoro-onnx publishes (its model-files-v1.1 release), checked against their SHA-256: the model, voice.kokoro_model
(fp32, 326 MB, the best and on most CPUs the fastest; fp16, 164 MB; int8, 114 MB, for a small disk), and
voices-v1.0.bin (28 MB), every voice.

A voice's first letter is its language and its second whether it's a woman's or a man's (af_heart: American English,
female); espeak-ng reads the text as that language. The model stays loaded while it's reading and is let go when
nothing has been read aloud for IDLE_SECONDS, so a small machine gets its memory back.
"""

from __future__ import annotations

import threading
import time

from . import components

MODELS = {"fp32": "kokoro-v1.0.onnx", "fp16": "kokoro-v1.0.fp16.onnx", "int8": "kokoro-v1.0.int8.onnx"}
VOICES_FILE = "voices-v1.0.bin"
DEFAULT_VOICE = "af_heart"
# the voice a podcast's second host gets, by language and sex, when there's more than one to pick from
PARTNERS = {"af": "af_heart", "am": "am_michael", "bf": "bf_emma", "bm": "bm_george"}
IDLE_SECONDS = 600
# a voice's first letter: the language espeak-ng reads its text as, and what it's called
LANGS = {
    "a": ("en-us", "American English"),
    "b": ("en-gb", "British English"),
    "e": ("es", "Spanish"),
    "f": ("fr-fr", "French"),
    "h": ("hi", "Hindi"),
    "i": ("it", "Italian"),
    "j": ("ja", "Japanese"),
    "p": ("pt-br", "Brazilian Portuguese"),
    "z": ("cmn", "Mandarin Chinese"),
}
# the voices in voices-v1.0.bin
VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river",
    "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck",
    "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "ef_dora", "em_alex", "em_santa", "ff_siwis", "hf_alpha", "hf_beta", "hm_omega", "hm_psi", "if_sara", "im_nicola",
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo", "pf_dora", "pm_alex", "pm_santa", "zf_xiaobei",
    "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
)  # fmt: skip

_lock = threading.Lock()
_loaded: dict = {}


class Missing(RuntimeError):
    """Kokoro isn't here yet: it's fetched once it's picked."""


def size(cfg):
    s = (cfg.get("voice") or {}).get("kokoro_model") or "fp32"
    return s if s in MODELS else "fp32"


def name(cfg):
    """The model, as the activity ledger names it."""
    s = size(cfg)
    return "kokoro-v1.0" if s == "fp32" else f"kokoro-v1.0-{s}"


def files(cfg):
    """Its files, as components.FILES names them: the model, then the voices."""
    return [MODELS[size(cfg)], VOICES_FILE]


def ready(cfg):
    """Whether kokoro-onnx, the chosen model and the voices are here."""
    components.activate(cfg)
    return components.importable("kokoro_onnx") and all(components.model_file(cfg, n) for n in files(cfg))


def voices():
    """Every voice as (id, name, detail): af_heart is ("af_heart", "Heart", "American English, female")."""
    return [(v, v[3:].title(), f"{LANGS[v[0]][1]}, {'female' if v[1] == 'f' else 'male'}") for v in VOICES]


def partner(voice):
    """A voice for a podcast's second host: the same language, a man's for a woman's and a woman's for a man's when
    there is one (am_michael for af_heart), else another of the same kind."""
    voice = voice if voice in VOICES else DEFAULT_VOICE
    for kind in (voice[0] + ("m" if voice[1] == "f" else "f"), voice[:2]):
        others = [v for v in VOICES if v[:2] == kind and v != voice]
        if others:
            return PARTNERS.get(kind) if PARTNERS.get(kind) in others else others[0]
    return voice


def lang(voice):
    """The language espeak-ng reads a voice's text as (American English for a voice it doesn't know)."""
    return LANGS.get((voice or "")[:1], LANGS["a"])[0]


def _later():
    t = threading.Timer(IDLE_SECONDS + 5, let_go)
    t.daemon = True
    t.start()
    return t


def let_go():
    """Free the model's memory when nothing has been read aloud for a while."""
    with _lock:
        if not _loaded:
            return
        if time.time() - _loaded["used"] > IDLE_SECONDS:
            _loaded.clear()
        else:
            _loaded["timer"] = _later()


def _load(cfg):
    if not ready(cfg):
        raise Missing("Kokoro isn't here yet: it's fetched once it's picked (Settings → Components shows how far it got)")
    model, voices_path = (components.model_file(cfg, n) for n in files(cfg))
    with _lock:  # one per process: loading the model takes longer than reading a sentence
        if _loaded.get("model") != model:
            import onnxruntime
            from kokoro_onnx import Kokoro

            _loaded.clear()  # another size's model goes: one is enough memory
            opts = onnxruntime.SessionOptions()
            opts.log_severity_level = 3  # the fp16 model warns about every node it can't fold
            session = onnxruntime.InferenceSession(model, opts, providers=["CPUExecutionProvider"])
            _loaded.update(model=model, kokoro=Kokoro.from_session(session, voices_path))
        _loaded["used"] = time.time()
        if not _loaded.get("timer"):
            _loaded["timer"] = _later()
        return _loaded["kokoro"]


def read(cfg, text, voice=None):
    """The text read aloud in `voice` (else af_heart) as 16-bit PCM and its sample rate. Raises Missing when Kokoro
    isn't here yet, ValueError for a voice it doesn't have or text it can't read."""
    import numpy as np

    voice = voice or DEFAULT_VOICE
    samples, rate = _load(cfg).create(text, voice=voice, lang=lang(voice))
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes(), rate
