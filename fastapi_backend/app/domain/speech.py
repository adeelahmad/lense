"""Speech by a provider instead of this server: transcription with speakers, language, audio events and sentiment, and
text to speech (docs/speech-providers.md).

Local engines (SenseVoice, Whisper) stay the default; an admin picks a provider per task in Settings. Each service has
a base URL (a proxy or a compatible server works too), a model and an API key, sealed like every other key:

* openai: any OpenAI-compatible /audio/transcriptions and /audio/speech (OpenAI, Groq, speaches, LocalAI, ...);
  models with "diarize" in their name return speakers.
* elevenlabs: Scribe speech to text (speakers, audio events) and text to speech.
* assemblyai: transcripts with speaker labels, language detection and sentiment.
* deepgram: Nova speech to text (speakers, language, sentiment) and Aura text to speech.
* gemini: Google Gemini text to speech (its prebuilt voices).

Text to speech can also come from macOS `say` when Lens runs on a Mac itself (not in Docker): nothing to set up, the
Mac's own voices; from Kokoro v1.0 (kokoro.py), which reads on this machine in any of its 54 voices and is fetched once
it's picked; or from StyleTTS2, the optional `styletts2` service (styletts2/), which reads in a voice like a short clip
of someone speaking. voices() lists what each one can read in, for Settings and podcast hosts.

A provider's transcript is the same list of segments a local engine returns, plus `provider_speaker`: the speaker
separation step uses those labels (diarize.engine provider, or auto), and voice IDs still come from local voiceprints.

Each provider takes so much audio in one request ({p}_max_minutes and {p}_max_mb in the speech settings, defaulting to
its published limits): a longer recording is cut into pieces at pauses, each piece is transcribed on its own, and their
transcripts are joined back on the recording's timeline.

How sure a provider was of each word (Deepgram's and AssemblyAI's confidence, ElevenLabs' log probability, the
`probability` an OpenAI-compatible server may give) is kept with the word's timing, for the transcript to mark the ones
it wasn't sure of (transcript.py).
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import logging
import math
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave

import numpy as np

from . import kokoro, llm, store, transcript, vocabulary

log = logging.getLogger("lens")
SR = 16000
PROVIDERS = ("openai", "elevenlabs", "assemblyai", "deepgram")
SERVICES = (*PROVIDERS, "gemini")  # with an address, model and key in the speech settings; gemini only reads aloud
LABELS = {
    "openai": "OpenAI-compatible",
    "elevenlabs": "ElevenLabs",
    "assemblyai": "AssemblyAI",
    "deepgram": "Deepgram",
    "gemini": "Google Gemini",
    "say": "macOS say",
    "kokoro": "Kokoro",
    "styletts2": "StyleTTS2",
}
TTS_PROVIDERS = ("openai", "elevenlabs", "deepgram", "gemini", "say", "kokoro", "styletts2")
QUIET_SECONDS = 30  # a recording too long for one request is cut at the quietest moment in the half minute before a limit
QUIET_WINDOW = SR // 10  # ... measured in tenths of a second
POLL_SECONDS = 3
MAX_WORDS = 40
GAP_MS = 1500
SENTIMENT = {"positive": "Happy", "negative": "Sad", "neutral": "Neutral"}
EVENTS = {"laughter": "Laughter", "laughs": "Laughter", "laughing": "Laughter", "applause": "Applause", "music": "BGM",
          "crying": "Cry", "cries": "Cry", "cough": "Cough", "coughs": "Cough", "coughing": "Cough", "sneeze": "Sneeze",
          "sneezes": "Sneeze", "breath": "Breath", "breathing": "Breath", "sighs": "Breath"}  # fmt: skip
ELEVENLABS_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # a premade voice every account has
ELEVENLABS_TTS_MODEL = "eleven_multilingual_v2"
DEEPGRAM_TTS_MODEL = "aura-2-thalia-en"
GEMINI_VOICE = "Kore"
# what each provider reads in when nothing is picked (say: the Mac's own voice)
DEFAULT_VOICE = {
    "openai": "alloy",
    "elevenlabs": ELEVENLABS_VOICE,
    "deepgram": DEEPGRAM_TTS_MODEL,
    "gemini": GEMINI_VOICE,
    "say": None,
    "kokoro": "af_heart",
    "styletts2": "default",
}
STYLETTS2_SECONDS = 600  # it reads on the CPU, a long answer slowly; the first call also loads the model
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse")
# Gemini's prebuilt voices and how Google describes each
GEMINI_VOICES = {
    "Zephyr": "Bright", "Puck": "Upbeat", "Charon": "Informative", "Kore": "Firm", "Fenrir": "Excitable",
    "Leda": "Youthful", "Orus": "Firm", "Aoede": "Breezy", "Callirrhoe": "Easy-going", "Autonoe": "Bright",
    "Enceladus": "Breathy", "Iapetus": "Clear", "Umbriel": "Easy-going", "Algieba": "Smooth", "Despina": "Smooth",
    "Erinome": "Clear", "Algenib": "Gravelly", "Rasalgethi": "Informative", "Laomedeia": "Upbeat", "Achernar": "Soft",
    "Alnilam": "Firm", "Schedar": "Even", "Gacrux": "Mature", "Pulcherrima": "Forward", "Achird": "Friendly",
    "Zubenelgenubi": "Casual", "Vindemiatrix": "Gentle", "Sadachbia": "Lively", "Sadaltager": "Knowledgeable",
    "Sulafat": "Warm",
}  # fmt: skip


class ProviderError(RuntimeError):
    """The provider couldn't be reached, refused the request, or isn't set up; the message says which."""


# ---------- settings ----------
def section(cfg):
    return {**store.DEFAULTS["speech"], **(cfg.get("speech") or {})}


def _key(cfg, p):
    k = section(cfg).get(f"{p}_api_key")
    return k if isinstance(k, str) and k else None


def base_url(cfg, p):
    return str(section(cfg)[f"{p}_base_url"] or store.DEFAULTS["speech"][f"{p}_base_url"]).rstrip("/")


def model(cfg, p):
    return section(cfg).get(f"{p}_model") or store.DEFAULTS["speech"][f"{p}_model"]


def ready(cfg, p):
    """Whether a provider can be called: it has a key, or (OpenAI-compatible) a server of its own that may need none."""
    if p not in SERVICES:
        return False
    if _key(cfg, p):
        return True
    return p == "openai" and base_url(cfg, p) != store.DEFAULTS["speech"]["openai_base_url"]


def _need(cfg, p):
    if not ready(cfg, p):
        raise ProviderError(f"{LABELS[p]} has no API key: add it in Settings → Speech providers")


def _language(cfg):
    lang = (cfg.get("transcribe") or {}).get("language")
    return None if lang in (None, "", "auto") else lang


# ---------- the ledger ----------
class _NoLedger:
    def usage(self, *a, **k):
        pass

    def end(self, *a, **k):
        pass


@contextlib.contextmanager
def _ledger(action, cfg, model_, detail):
    """A row in the activity ledger (domain/activity.py) when this install has one."""
    try:
        from . import activity  # noqa: PLC0415 - the ledger is optional
    except ImportError:
        yield _NoLedger()
        return
    # costed by the provider's price in telemetry.prices, per minute of audio or per million characters
    with activity.call(action, cfg, model_, detail=detail, price=activity.model_cost(cfg)) as row:
        yield row


# ---------- HTTP ----------
def _request(url, data=None, headers=None, method=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method or ("POST" if data is not None else "GET"))
    host = urllib.parse.urlsplit(url).netloc
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers.get_content_type()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise ProviderError(f"{e.code} from {host}: {body}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError(f"can't reach {host}: {getattr(e, 'reason', e)}") from None


def _json(url, **kw):
    raw, _ = _request(url, **kw)
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise ProviderError(f"{urllib.parse.urlsplit(url).netloc} didn't answer in JSON") from None


def multipart(fields, files):
    """A multipart/form-data body: fields [(name, value)], files [(name, filename, bytes, type)]."""
    b = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields:
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, name, data, ctype in files:
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
        out += data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


ENCODE_SECONDS = 600  # a piece sent to a speech service encodes in seconds; past this ffmpeg is stuck


def encode(audio):
    """16 kHz mono samples as a small file to send: Ogg Opus, or FLAC where ffmpeg has no Opus. (bytes, name, type)."""
    pcm = np.ascontiguousarray(audio, dtype=np.float32).tobytes()
    for codec, fmt, name, ctype, extra in (
        ("libopus", "ogg", "audio.ogg", "audio/ogg", ["-b:a", "32k"]),
        ("flac", "flac", "audio.flac", "audio/flac", []),
    ):
        try:
            out = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-f",
                    "f32le",
                    "-ar",
                    str(SR),
                    "-ac",
                    "1",
                    "-i",
                    "-",
                    "-c:a",
                    codec,
                    *extra,
                    "-f",
                    fmt,
                    "-",
                ],
                input=pcm,
                capture_output=True,
                timeout=ENCODE_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise ProviderError(f"ffmpeg took longer than {ENCODE_SECONDS} s to encode the audio to send") from None
        if out.returncode == 0 and out.stdout:
            return out.stdout, name, ctype
    raise ProviderError("ffmpeg couldn't encode the audio to send: " + out.stderr.decode(errors="replace")[-200:])


# ---------- words into segments ----------
def _event(text):
    m = re.fullmatch(r"[\(\[]\s*([\w ]+?)\s*[\)\]]", (text or "").strip())
    if not m:
        return None
    w = m.group(1).lower()
    return EVENTS.get(w) or EVENTS.get(w.split()[-1]) or EVENTS.get(w.split()[0])


def group_words(words, lang=None):
    """Timed words [{text, t0, t1, speaker, p?}] (ms; p: how sure the provider was of it, 0 to 1) into segments: a new
    one when the speaker changes, after a pause, or at the end of a sentence once it's long enough."""
    out, cur = [], []

    def flush():
        if cur:
            text = re.sub(r"\s+([,.!?;:…])", r"\1", " ".join(w["text"] for w in cur)).strip()
            if any(ch.isalnum() for ch in text):
                out.append(
                    {
                        "t0": cur[0]["t0"],
                        "t1": cur[-1]["t1"],
                        "text": text,
                        "raw_text": None,
                        "emotion": None,
                        "event": next((w["event"] for w in cur if w.get("event")), None),
                        "lang": lang,
                        "words": json.dumps(
                            [transcript.stored(w["text"], w["t0"], w["t1"], w.get("p")) for w in cur if not w.get("event")]
                        ),
                        "provider_speaker": cur[0].get("speaker"),
                    }
                )
            cur.clear()

    for w in words:
        if cur and (w.get("speaker") != cur[-1].get("speaker") or w["t0"] - cur[-1]["t1"] > GAP_MS or len(cur) >= MAX_WORDS * 1.5):
            flush()
        cur.append(w)
        if len(cur) >= MAX_WORDS and re.search(r"[.!?…]$", w["text"]):
            flush()
    flush()
    return out


def add_sentiment(segs, spans):
    """Each segment's emotion from the provider's sentiment spans [(t0, t1, sentiment)] it overlaps most."""
    for s in segs:
        best, lab = 0, None
        for a, b, v in spans:
            ov = min(b, s["t1"]) - max(a, s["t0"])
            if ov > best:
                best, lab = ov, v
        if lab:
            s["emotion"] = SENTIMENT.get(str(lab).lower())
    return segs


def _from_log(v):
    """A log probability as a probability (0 to 1), or None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v > 0:
        return None
    return math.exp(v)


def _label(v):
    return None if v is None else f"S{v}" if isinstance(v, int) or str(v).isdigit() else str(v)


# ---------- providers ----------
def _openai(cfg, clip, seconds, terms=()):
    base, m, key = base_url(cfg, "openai"), model(cfg, "openai"), _key(cfg, "openai")
    data, name, ctype = clip
    lang = _language(cfg)
    if "diarize" in m:
        fields = [("model", m), ("response_format", "diarized_json"), ("chunking_strategy", "auto")]
    elif m.startswith("gpt-"):
        fields = [("model", m), ("response_format", "json")]
    else:
        fields = [("model", m), ("response_format", "verbose_json"), ("timestamp_granularities[]", "segment"),
                  ("timestamp_granularities[]", "word")]  # fmt: skip
    if lang:
        fields.append(("language", lang))
    if terms and "diarize" not in m:  # the diarizing models take no prompt
        fields.append(("prompt", vocabulary.prompt(terms)))
    body, ctype_ = multipart(fields, [("file", name, data, ctype)])
    headers = {"Content-Type": ctype_, **({"Authorization": f"Bearer {key}"} if key else {})}
    j = _json(base + "/audio/transcriptions", data=body, headers=headers, timeout=section(cfg)["timeout"])
    got = j.get("language") or lang
    segs = j.get("segments") or [{"start": 0, "end": seconds, "text": j.get("text") or ""}]
    words = j.get("words") or []
    out = []
    for s in segs:
        t0, t1 = int(float(s.get("start") or 0) * 1000), int(float(s.get("end") or 0) * 1000)
        ws = [
            transcript.stored(w["word"].strip(), int(w["start"] * 1000), int(w["end"] * 1000), w.get("probability"))
            for w in words
            if t0 <= w["start"] * 1000 < t1
        ]
        text = (s.get("text") or "").strip()
        if any(ch.isalnum() for ch in text):
            out.append(
                {
                    "t0": t0,
                    "t1": max(t1, t0 + 1),
                    "text": text,
                    "raw_text": None,
                    "emotion": None,
                    "event": None,
                    "lang": _short(got),
                    "words": json.dumps(ws) if ws else None,
                    "provider_speaker": _label(s.get("speaker")),
                }  # fmt: skip
            )
    return out


def _short(lang):
    """'english' or 'en-US' as 'en'; Lens keeps two- or three-letter codes."""
    if not lang:
        return None
    lang = str(lang).lower()
    names = {"english": "en", "chinese": "zh", "japanese": "ja", "korean": "ko", "cantonese": "yue", "german": "de",
             "french": "fr", "spanish": "es", "italian": "it", "portuguese": "pt", "dutch": "nl", "russian": "ru",
             "arabic": "ar", "urdu": "ur", "hindi": "hi", "turkish": "tr", "polish": "pl", "swedish": "sv"}  # fmt: skip
    return names.get(lang) or lang.split("-")[0].split("_")[0][:3]


def _elevenlabs(cfg, clip, seconds, terms=()):  # Scribe takes no hints (vocabulary.py)
    data, name, ctype = clip
    fields = [("model_id", model(cfg, "elevenlabs")), ("diarize", "true"), ("tag_audio_events", "true"), ("timestamps_granularity", "word")]
    if _language(cfg):
        fields.append(("language_code", _language(cfg)))
    mn, mx = (cfg.get("diarize") or {}).get("min_speakers"), (cfg.get("diarize") or {}).get("max_speakers")
    if mx or mn:
        fields.append(("num_speakers", str(mx or mn)))
    body, ctype_ = multipart(fields, [("file", name, data, ctype)])
    j = _json(
        base_url(cfg, "elevenlabs") + "/v1/speech-to-text",
        data=body,
        headers={"Content-Type": ctype_, "xi-api-key": _key(cfg, "elevenlabs")},
        timeout=section(cfg)["timeout"],
    )
    words = []
    for w in j.get("words") or []:
        if w.get("type") == "spacing":
            continue
        text = (w.get("text") or "").strip()
        if not text:
            continue
        ev = _event(text) if w.get("type") == "audio_event" else None
        if w.get("type") == "audio_event" and words and not ev:
            continue
        words.append({"text": text, "t0": int(float(w.get("start") or 0) * 1000), "t1": int(float(w.get("end") or 0) * 1000),
                      "speaker": _label(w.get("speaker_id")), "event": ev, "p": _from_log(w.get("logprob"))})  # fmt: skip
    return group_words(words, _short(j.get("language_code")))


def _assemblyai(cfg, clip, seconds, terms=()):
    base, key, t = base_url(cfg, "assemblyai"), _key(cfg, "assemblyai"), section(cfg)["timeout"]
    data, _, ctype = clip
    up = _json(base + "/v2/upload", data=data, headers={"Authorization": key, "Content-Type": "application/octet-stream"}, timeout=t)
    ask = {"audio_url": up["upload_url"], "speaker_labels": True, "punctuate": True, "format_text": True}
    m = model(cfg, "assemblyai")
    if m:
        ask["speech_model"] = m
    if _language(cfg):
        ask["language_code"] = _language(cfg)
    else:
        ask["language_detection"] = True
    if section(cfg)["sentiment"]:
        ask["sentiment_analysis"] = True
    if terms:  # key terms for the models that take them, else boosted words (at most six words each)
        if str(m or "").startswith(("slam", "universal-3")):
            ask["keyterms_prompt"] = list(terms)
        else:
            ask["word_boost"] = [t for t in terms if len(t.split()) <= 6]
    d = cfg.get("diarize") or {}
    if d.get("min_speakers") or d.get("max_speakers"):
        ask["speaker_options"] = {
            k: v for k, v in (("min_speakers_expected", d.get("min_speakers")), ("max_speakers_expected", d.get("max_speakers"))) if v
        }
    h = {"Authorization": key, "Content-Type": "application/json"}
    job = _json(base + "/v2/transcript", data=json.dumps(ask).encode(), headers=h, timeout=60)
    deadline = time.time() + t
    while job.get("status") not in ("completed", "error"):
        if time.time() > deadline:
            raise ProviderError(f"AssemblyAI didn't finish within {t} seconds")
        time.sleep(POLL_SECONDS)
        job = _json(f"{base}/v2/transcript/{job['id']}", headers={"Authorization": key}, timeout=60)
    if job["status"] == "error":
        raise ProviderError(f"AssemblyAI: {job.get('error') or 'the transcript failed'}")
    lang = _short(job.get("language_code"))
    words = [
        {"text": w["text"], "t0": int(w["start"]), "t1": int(w["end"]), "speaker": _label(w.get("speaker")), "p": w.get("confidence")}
        for w in job.get("words") or []
        if (w.get("text") or "").strip()
    ]
    segs = group_words(words, lang)
    spans = [(int(r["start"]), int(r["end"]), r.get("sentiment")) for r in job.get("sentiment_analysis_results") or []]
    return add_sentiment(segs, spans)


def _deepgram(cfg, clip, seconds, terms=()):
    data, _, ctype = clip
    q: dict = {"model": model(cfg, "deepgram"), "smart_format": "true", "punctuate": "true", "diarize": "true", "utterances": "true"}
    if _language(cfg):
        q["language"] = _language(cfg)
    else:
        q["detect_language"] = "true"
    if section(cfg)["sentiment"]:
        q["sentiment"] = "true"
    if terms:  # Nova-3 takes key terms; the models before it, keywords
        q["keyterm" if str(q["model"]).startswith("nova-3") else "keywords"] = list(terms)
    j = _json(
        base_url(cfg, "deepgram") + "/v1/listen?" + urllib.parse.urlencode(q, doseq=True),
        data=data,
        headers={"Authorization": f"Token {_key(cfg, 'deepgram')}", "Content-Type": ctype},
        timeout=section(cfg)["timeout"],
    )
    res = j.get("results") or {}
    ch = (res.get("channels") or [{}])[0]
    lang = _short(ch.get("detected_language") or _language(cfg))
    alt = (ch.get("alternatives") or [{}])[0]
    words = [
        {
            "text": w.get("punctuated_word") or w["word"],
            "t0": int(w["start"] * 1000),
            "t1": int(w["end"] * 1000),
            "speaker": _label(w.get("speaker")),
            "p": w.get("confidence"),
        }  # fmt: skip
        for w in alt.get("words") or []
    ]
    segs = group_words(words, lang)
    all_words = alt.get("words") or []
    spans = []
    for s in (res.get("sentiments") or {}).get("segments") or []:
        a, b = s.get("start_word"), s.get("end_word")
        if isinstance(a, int) and isinstance(b, int) and all_words:
            a, b = all_words[min(a, len(all_words) - 1)], all_words[min(max(b - 1, a), len(all_words) - 1)]
            spans.append((int(a["start"] * 1000), int(b["end"] * 1000), s.get("sentiment")))
    return add_sentiment(segs, spans)


# each takes (cfg, an encoded clip as (bytes, file name, media type), its seconds, the words to expect) and returns its
# segments, timed from the clip's start
TRANSCRIBE = {"openai": _openai, "elevenlabs": _elevenlabs, "assemblyai": _assemblyai, "deepgram": _deepgram}


# ---------- long recordings ----------
def limits(cfg, p):
    """The most a provider takes in one request: (samples, bytes) from {p}_max_minutes and {p}_max_mb."""
    s = section(cfg)
    return int(s[f"{p}_max_minutes"]) * 60 * SR, int(s[f"{p}_max_mb"]) * 1_000_000


def quiet_cut(audio, lo, hi):
    """The quietest tenth of a second between samples lo and hi (the latest, of equally quiet ones), so a cut falls in
    a pause rather than in a word; hi when there's too little to look at."""
    k = (hi - lo) // QUIET_WINDOW
    if k < 2:
        return hi
    energy = np.square(audio[lo : lo + k * QUIET_WINDOW].reshape(k, QUIET_WINDOW)).mean(axis=1)
    return lo + (k - 1 - int(np.argmin(energy[::-1]))) * QUIET_WINDOW + QUIET_WINDOW // 2


def pieces(audio, most):
    """Where to cut samples into pieces of at most `most` samples each, each cut at a pause: [(start, end)]."""
    out, start = [], 0
    while len(audio) - start > most:
        hi = start + most
        cut = quiet_cut(audio, max(start + most // 2, hi - QUIET_SECONDS * SR), hi)
        out.append((start, cut))
        start = cut
    out.append((start, len(audio)))
    return out


def clips(audio, start, end, most_bytes):
    """A piece encoded to send, halved at a pause until each part's file fits the provider's limit: (start, end, file)s."""
    clip = encode(audio[start:end])
    if len(clip[0]) <= most_bytes:
        yield start, end, clip
        return
    if end - start < 2 * SR:
        mb = most_bytes // 1_000_000
        raise ProviderError(f"two seconds of audio already make a file larger than {mb} MB: raise the limit in Settings → Speech providers")
    mid = (start + end) // 2
    mid = quiet_cut(audio, max(start + (end - start) // 4, mid - QUIET_SECONDS * SR), mid)
    yield from clips(audio, start, mid, most_bytes)
    yield from clips(audio, mid, end, most_bytes)


def _shift(segs, ms, piece):
    """A piece's segments on the recording's timeline; with more than one piece, its speakers are its own (cN:label),
    as a provider numbers the speakers of each request afresh."""
    for s in segs:
        s["t0"], s["t1"] = s["t0"] + ms, s["t1"] + ms
        if ms and s.get("words"):
            s["words"] = json.dumps([[w[0], w[1] + ms, w[2] + ms, *w[3:]] for w in json.loads(s["words"])])
        if piece is not None and s.get("provider_speaker"):
            s["provider_speaker"] = f"c{piece}:{s['provider_speaker']}"
    return segs


class Engine:
    """A provider as a transcription engine: transcribe(16 kHz mono samples) -> segments, like SenseVoice's."""

    def __init__(self, cfg, provider):
        if provider not in PROVIDERS:
            raise ValueError(f"unknown speech provider {provider!r}")
        _need(cfg, provider)
        self.cfg, self.provider, self.name = cfg, provider, provider

    def transcribe(self, audio, terms=()):
        """The recording's transcript: in one request when it fits the provider's limits, else piece by piece; `terms`
        are the words to expect (vocabulary.py)."""
        most, most_bytes = limits(self.cfg, self.provider)
        detail = {"seconds": round(len(audio) / SR, 2)}  # the ledger row reads it when the call ends
        if terms:
            detail["vocabulary"] = len(terms)
        parts = []
        with _ledger("speech.transcribe", self.cfg, f"{self.provider}:{model(self.cfg, self.provider)}", detail):
            for a, b in pieces(audio, most):
                for start, end, clip in clips(audio, a, b, most_bytes):
                    parts.append((start, TRANSCRIBE[self.provider](self.cfg, clip, round((end - start) / SR, 2), terms)))
            if len(parts) > 1:
                detail["pieces"] = len(parts)
                log.info("speech: %s s of audio went to %s in %d pieces", detail["seconds"], LABELS[self.provider], len(parts))
        many = len(parts) > 1
        segs = [s for k, (start, part) in enumerate(parts) for s in _shift(part, start * 1000 // SR, k if many else None)]
        return sorted(segs, key=lambda s: s["t0"])


# ---------- text to speech ----------
def tts_provider(cfg):
    return (cfg.get("voice") or {}).get("tts_provider") or "openai"


def say_available():
    """macOS `say` is there: Lens runs on a Mac itself, not in a container."""
    return sys.platform == "darwin" and shutil.which("say") is not None


def tts_ready(cfg):
    v = cfg.get("voice") or {}
    p = tts_provider(cfg)
    if p == "openai":
        return bool(v.get("tts_model") and (v.get("tts_base_url") or (cfg.get("llm") or {}).get("base_url")))
    if p == "say":
        return say_available()
    if p == "kokoro":
        return kokoro.ready(cfg)  # fetched once it's picked; until then the browser reads answers
    if p == "styletts2":
        return bool(v.get("styletts2_url"))  # its own service; when it isn't running the browser reads answers
    return p in TTS_PROVIDERS and bool(_key(cfg, p))


def tts_voice(cfg):
    """The voice answers are read in: voice.tts_voice, else the provider's default (for Deepgram, voice.tts_model too,
    where its voice was set before it had a voice of its own)."""
    v = cfg.get("voice") or {}
    p = tts_provider(cfg)
    if p == "deepgram":
        return v.get("tts_voice") or v.get("tts_model") or DEEPGRAM_TTS_MODEL
    return v.get("tts_voice") or DEFAULT_VOICE.get(p)


def wav(pcm, rate, channels=1):
    """16-bit PCM as a WAV file."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def _speak_elevenlabs(cfg, text, voice):
    m = (cfg.get("voice") or {}).get("tts_model") or ELEVENLABS_TTS_MODEL
    url = f"{base_url(cfg, 'elevenlabs')}/v1/text-to-speech/{urllib.parse.quote(voice, safe='')}?output_format=mp3_44100_128"
    headers = {"Content-Type": "application/json", "xi-api-key": _key(cfg, "elevenlabs"), "Accept": "audio/mpeg"}
    return m, lambda: _request(url, data=json.dumps({"text": text, "model_id": m}).encode(), headers=headers, timeout=60)


def _speak_deepgram(cfg, text, voice):
    url = f"{base_url(cfg, 'deepgram')}/v1/speak?" + urllib.parse.urlencode({"model": voice, "encoding": "mp3"})
    headers = {"Content-Type": "application/json", "Authorization": f"Token {_key(cfg, 'deepgram')}"}
    return voice, lambda: _request(url, data=json.dumps({"text": text}).encode(), headers=headers, timeout=60)


def _speak_gemini(cfg, text, voice):
    m = model(cfg, "gemini")
    url = f"{base_url(cfg, 'gemini')}/v1beta/models/{urllib.parse.quote(m, safe='')}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": _key(cfg, "gemini")}
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }

    def call():
        j = _json(url, data=json.dumps(body).encode(), headers=headers, timeout=120)
        parts = (((j.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        blob = next((p["inlineData"] for p in parts if isinstance(p, dict) and p.get("inlineData")), None)
        if not blob or not blob.get("data"):
            raise ProviderError("Gemini answered without audio: " + json.dumps(j)[:200])
        rate = re.search(r"rate=(\d+)", blob.get("mimeType") or "")  # raw 16-bit PCM, audio/L16;codec=pcm;rate=24000
        return wav(base64.b64decode(blob["data"]), int(rate.group(1)) if rate else 24000), "audio/wav"

    return m, call


def _speak_say(cfg, text, voice):
    if not say_available():
        raise ProviderError("macOS say reads aloud only when Lens runs on a Mac itself, not in Docker")
    if voice and (voice.startswith("-") or len(voice) > 100):
        raise ProviderError(f"{voice!r} isn't a voice of say's")

    def call():
        with tempfile.TemporaryDirectory(prefix="lens-say-") as d:
            out = pathlib.Path(d) / "speech.wav"
            cmd = ["say", "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@22050", *(["-v", voice] if voice else [])]
            try:  # the text goes in on stdin, so nothing in it is read as an option
                r = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=120)
            except subprocess.TimeoutExpired:
                raise ProviderError("say took longer than 120 s") from None
            if r.returncode or not out.is_file():
                raise ProviderError("say couldn't read it: " + (r.stderr.decode(errors="replace").strip()[-200:] or "no audio"))
            return out.read_bytes(), "audio/wav"

    return voice or "default", call


def _speak_openai(cfg, text, voice):
    v = cfg.get("voice") or {}
    m = v["tts_model"]
    llm_base, llm_key = llm.shared(cfg)
    if not (v.get("tts_base_url") or llm_base):
        raise ProviderError("the model provider has no speech: give the voice a server of its own (voice.tts_base_url)")
    base = (v.get("tts_base_url") or llm_base).rstrip("/")
    key = v.get("tts_api_key") or (llm_key if not v.get("tts_base_url") else None)
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
    body = {"model": m, "input": text, "voice": voice, "response_format": "mp3"}
    return m, lambda: _request(base + "/audio/speech", data=json.dumps(body).encode(), headers=headers, timeout=60)


def _speak_kokoro(cfg, text, voice):
    def call():
        try:
            pcm, rate = kokoro.read(cfg, text, voice)
        except kokoro.Missing as e:
            raise ProviderError(str(e)) from None
        except ValueError as e:  # a voice it doesn't have, or nothing it could read
            raise ProviderError(f"Kokoro couldn't read it: {e}") from None
        return wav(pcm, rate), "audio/wav"

    return kokoro.name(cfg), call


def _styletts2_url(cfg):
    return str((cfg.get("voice") or {}).get("styletts2_url") or "").rstrip("/")


def _speak_styletts2(cfg, text, voice):
    body = {"model": "styletts2", "input": text, "voice": voice, "response_format": "wav"}
    url = _styletts2_url(cfg) + "/v1/audio/speech"
    headers = {"Content-Type": "application/json"}
    return "styletts2", lambda: _request(url, data=json.dumps(body).encode(), headers=headers, timeout=STYLETTS2_SECONDS)


SPEAK = {
    "styletts2": _speak_styletts2,
    "openai": _speak_openai,
    "elevenlabs": _speak_elevenlabs,
    "deepgram": _speak_deepgram,
    "gemini": _speak_gemini,
    "say": _speak_say,
    "kokoro": _speak_kokoro,
}


def speak(cfg, text, voice=None):
    """The text read aloud as (bytes, media type), by the voice settings' provider, in `voice` (else tts_voice()).
    Raises ProviderError."""
    p = tts_provider(cfg)
    if p not in SPEAK:
        raise ProviderError(f"unknown text-to-speech provider {p!r}")
    text = text[:4000]
    m, call = SPEAK[p](cfg, text, voice or tts_voice(cfg))
    with _ledger("model.speech", cfg, f"{p}:{m}", {"chars": len(text)}):
        data, ctype = call()
    return data, ctype if ctype and ctype.startswith("audio/") else "audio/mpeg"


# ---------- the voices a provider reads in ----------
def _voice(id, name=None, detail=None):
    return {"id": id, "name": name or id, "detail": detail or None}


def _say_voices():
    """`say -v ?` lines: "Name (maybe with spaces)   en_US    # A sample sentence"."""
    out = subprocess.run(["say", "-v", "?"], capture_output=True, timeout=30).stdout.decode(errors="replace")
    found = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s+([a-z]{2,3}[_-][A-Za-z0-9]+)\s+#", line)
        if m:
            found.append(_voice(m.group(1).strip(), detail=m.group(2)))
    return found


def _openai_voices(cfg):
    v = cfg.get("voice") or {}
    llm_base, llm_key = llm.shared(cfg)
    base = (v.get("tts_base_url") or llm_base or "").rstrip("/")
    key = v.get("tts_api_key") or (llm_key if not v.get("tts_base_url") else None)
    if base and "api.openai.com" not in base:  # Kokoro-FastAPI, speaches and others list their own
        with contextlib.suppress(ProviderError, AttributeError, TypeError):
            j = _json(base + "/audio/voices", headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=15)
            got = j.get("voices") if isinstance(j, dict) else j
            names = [x if isinstance(x, str) else (x.get("id") or x.get("name")) for x in got or []]
            if any(names):
                return [_voice(n) for n in names if n]
    return [_voice(n) for n in OPENAI_VOICES]


def _elevenlabs_voices(cfg):
    j = _json(f"{base_url(cfg, 'elevenlabs')}/v1/voices", headers={"xi-api-key": _key(cfg, "elevenlabs")}, timeout=20)
    out = []
    for x in j.get("voices") or []:
        labels = x.get("labels") or {}
        detail = ", ".join(str(labels[k]) for k in ("gender", "accent", "age", "description", "use_case") if labels.get(k))
        out.append(_voice(x["voice_id"], x.get("name"), detail or x.get("category")))
    return out


def _deepgram_voices(cfg):
    j = _json(f"{base_url(cfg, 'deepgram')}/v1/models", headers={"Authorization": f"Token {_key(cfg, 'deepgram')}"}, timeout=20)
    out = []
    for x in j.get("tts") or []:
        meta = x.get("metadata") or {}
        detail = ", ".join(str(d) for d in (meta.get("accent"), *(x.get("languages") or [])[:1]) if d)
        out.append(_voice(x.get("canonical_name") or x["name"], (x.get("name") or "").title() or None, detail))
    return out


def voices(cfg, provider=None):
    """The voices a text-to-speech provider (else the voice settings') reads in: ([{id, name, detail}], error or None).
    Providers that list them are asked; Gemini's and OpenAI's own are known."""
    p = provider or tts_provider(cfg)
    if p not in TTS_PROVIDERS:
        raise ValueError(f"unknown text-to-speech provider {p!r}")
    if p == "gemini":
        return [_voice(n, detail=d) for n, d in GEMINI_VOICES.items()], None
    if p == "openai":
        return _openai_voices(cfg), None
    if p == "say":
        if not say_available():
            return [], "macOS say reads aloud only when Lens runs on a Mac itself, not in Docker"
        try:
            return _say_voices(), None
        except (OSError, subprocess.TimeoutExpired) as e:
            return [], f"say didn't list its voices: {e}"
    if p == "kokoro":
        found = [_voice(*v) for v in kokoro.voices()]
        return found, None if kokoro.ready(cfg) else "Kokoro is fetched once it's saved as the voice (Settings → Components)"
    if p == "styletts2":
        try:
            names = _json(_styletts2_url(cfg) + "/v1/audio/voices", timeout=15).get("voices") or []
        except (ProviderError, AttributeError) as e:
            return [], f"StyleTTS2 isn't running ({e}): docker compose --profile styletts2 up -d"
        return [_voice(n, detail="its own reader" if n == "default" else "from your clip") for n in names if isinstance(n, str)], None
    if not _key(cfg, p):
        return [], f"{LABELS[p]} has no API key yet: add it in Settings → Speech providers"
    try:
        return {"elevenlabs": _elevenlabs_voices, "deepgram": _deepgram_voices}[p](cfg), None
    except (ProviderError, KeyError, AttributeError, TypeError) as e:
        return [], str(e) if isinstance(e, ProviderError) else f"{LABELS[p]} answered in a way Lens doesn't read"


# ---------- checking a provider from Settings ----------
def check(cfg, provider):
    """Whether the provider answers with these settings: (ok, what it said). Lists models; nothing is billed."""
    if provider not in SERVICES:
        raise ValueError(f"unknown speech provider {provider!r}")
    if not ready(cfg, provider):
        return False, f"{LABELS[provider]} has no API key yet"
    key, base = _key(cfg, provider), base_url(cfg, provider)
    try:
        if provider == "openai":
            j = _json(base + "/models", headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=15)
            n = len(j.get("data") or [])
            return True, f"reached {urllib.parse.urlsplit(base).netloc}; it lists {n} model(s)"
        if provider == "elevenlabs":
            j = _json(base + "/v1/models", headers={"xi-api-key": key}, timeout=15)
            return True, f"signed in; {len(j) if isinstance(j, list) else 0} model(s)"
        if provider == "gemini":
            j = _json(base + "/v1beta/models?pageSize=1000", headers={"x-goog-api-key": key}, timeout=15)
            tts = [m for m in j.get("models") or [] if "tts" in (m.get("name") or "")]
            return True, f"signed in; {len(tts)} speech model(s)"
        if provider == "assemblyai":
            _json(base + "/v2/transcript?limit=1", headers={"Authorization": key}, timeout=15)
            return True, "signed in"
        _json(base + "/v1/projects", headers={"Authorization": f"Token {key}"}, timeout=15)
        return True, "signed in"
    except ProviderError as e:
        return False, str(e)
