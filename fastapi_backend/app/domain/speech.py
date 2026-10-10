"""Speech by a provider instead of this server: transcription with speakers, language, audio events and sentiment, and
text to speech (docs/speech-providers.md).

Local engines (SenseVoice, Whisper) stay the default; an admin picks a provider per task in Settings. Each service has
a base URL (a proxy or a compatible server works too), a model and an API key, sealed like every other key:

* openai: any OpenAI-compatible /audio/transcriptions and /audio/speech (OpenAI, Groq, speaches, LocalAI, ...);
  models with "diarize" in their name return speakers.
* elevenlabs: Scribe speech to text (speakers, audio events) and text to speech.
* assemblyai: transcripts with speaker labels, language detection and sentiment.
* deepgram: Nova speech to text (speakers, language, sentiment) and Aura text to speech.

A provider's transcript is the same list of segments a local engine returns, plus `provider_speaker`: the speaker
separation step uses those labels (diarize.engine provider, or auto), and voice IDs still come from local voiceprints.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import numpy as np

from . import store

log = logging.getLogger("lens")
SR = 16000
PROVIDERS = ("openai", "elevenlabs", "assemblyai", "deepgram")
LABELS = {"openai": "OpenAI-compatible", "elevenlabs": "ElevenLabs", "assemblyai": "AssemblyAI", "deepgram": "Deepgram"}
TTS_PROVIDERS = ("openai", "elevenlabs", "deepgram")
CHUNK_SECONDS = 600  # OpenAI-compatible servers take up to 25 MB a request; ten minutes of Opus is a few MB
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
    if p not in PROVIDERS:
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
    with activity.call(action, cfg, model_, detail=detail) as row:
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
    """Timed words [{text, t0, t1, speaker}] (ms) into segments: a new one when the speaker changes, after a pause, or
    at the end of a sentence once it's long enough."""
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
                        "words": json.dumps([[w["text"], w["t0"], w["t1"]] for w in cur if not w.get("event")]),
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


def _label(v):
    return None if v is None else f"S{v}" if isinstance(v, int) or str(v).isdigit() else str(v)


# ---------- providers ----------
def _openai(cfg, audio, seconds):
    base, m, key = base_url(cfg, "openai"), model(cfg, "openai"), _key(cfg, "openai")
    diarize = "diarize" in m
    lang = _language(cfg)
    out, many = [], len(audio) > CHUNK_SECONDS * SR
    for k, start in enumerate(range(0, max(len(audio), 1), CHUNK_SECONDS * SR)):
        data, name, ctype = encode(audio[start : start + CHUNK_SECONDS * SR])
        if diarize:
            fields = [("model", m), ("response_format", "diarized_json"), ("chunking_strategy", "auto")]
        elif m.startswith("gpt-"):
            fields = [("model", m), ("response_format", "json")]
        else:
            fields = [("model", m), ("response_format", "verbose_json"), ("timestamp_granularities[]", "segment"),
                      ("timestamp_granularities[]", "word")]  # fmt: skip
        if lang:
            fields.append(("language", lang))
        body, ctype_ = multipart(fields, [("file", name, data, ctype)])
        headers = {"Content-Type": ctype_, **({"Authorization": f"Bearer {key}"} if key else {})}
        j = _json(base + "/audio/transcriptions", data=body, headers=headers, timeout=section(cfg)["timeout"])
        off = start * 1000 // SR
        got = j.get("language") or lang
        segs = j.get("segments") or []
        words = j.get("words") or []
        if not segs:
            end = min(len(audio), start + CHUNK_SECONDS * SR)
            segs = [{"start": 0, "end": (end - start) / SR, "text": j.get("text") or ""}]
        for s in segs:
            t0, t1 = off + int(float(s.get("start") or 0) * 1000), off + int(float(s.get("end") or 0) * 1000)
            ws = [
                [w["word"].strip(), off + int(w["start"] * 1000), off + int(w["end"] * 1000)]
                for w in words
                if t0 <= off + w["start"] * 1000 < t1
            ]
            spk = _label(s.get("speaker"))
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
                        "provider_speaker": (f"c{k}:{spk}" if many else spk) if spk else None,
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


def _elevenlabs(cfg, audio, seconds):
    data, name, ctype = encode(audio)
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
                      "speaker": _label(w.get("speaker_id")), "event": ev})  # fmt: skip
    return group_words(words, _short(j.get("language_code")))


def _assemblyai(cfg, audio, seconds):
    base, key, t = base_url(cfg, "assemblyai"), _key(cfg, "assemblyai"), section(cfg)["timeout"]
    data, _, ctype = encode(audio)
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
        {"text": w["text"], "t0": int(w["start"]), "t1": int(w["end"]), "speaker": _label(w.get("speaker"))}
        for w in job.get("words") or []
        if (w.get("text") or "").strip()
    ]
    segs = group_words(words, lang)
    spans = [(int(r["start"]), int(r["end"]), r.get("sentiment")) for r in job.get("sentiment_analysis_results") or []]
    return add_sentiment(segs, spans)


def _deepgram(cfg, audio, seconds):
    data, _, ctype = encode(audio)
    q = {"model": model(cfg, "deepgram"), "smart_format": "true", "punctuate": "true", "diarize": "true", "utterances": "true"}
    if _language(cfg):
        q["language"] = _language(cfg)
    else:
        q["detect_language"] = "true"
    if section(cfg)["sentiment"]:
        q["sentiment"] = "true"
    j = _json(
        base_url(cfg, "deepgram") + "/v1/listen?" + urllib.parse.urlencode(q),
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


TRANSCRIBE = {"openai": _openai, "elevenlabs": _elevenlabs, "assemblyai": _assemblyai, "deepgram": _deepgram}


class Engine:
    """A provider as a transcription engine: transcribe(16 kHz mono samples) -> segments, like SenseVoice's."""

    def __init__(self, cfg, provider):
        if provider not in PROVIDERS:
            raise ValueError(f"unknown speech provider {provider!r}")
        _need(cfg, provider)
        self.cfg, self.provider, self.name = cfg, provider, provider

    def transcribe(self, audio):
        seconds = round(len(audio) / SR, 2)
        with _ledger("speech.transcribe", self.cfg, f"{self.provider}:{model(self.cfg, self.provider)}", {"seconds": seconds}):
            segs = TRANSCRIBE[self.provider](self.cfg, audio, seconds)
        return sorted(segs, key=lambda s: s["t0"])


# ---------- text to speech ----------
def tts_ready(cfg):
    v = cfg.get("voice") or {}
    p = v.get("tts_provider") or "openai"
    if p == "openai":
        return bool(v.get("tts_model") and (v.get("tts_base_url") or (cfg.get("llm") or {}).get("base_url")))
    return p in TTS_PROVIDERS and bool(_key(cfg, p))


def speak(cfg, text, voice=None):
    """The text read aloud as (bytes, media type), by the voice settings' provider, in `voice` (else voice.tts_voice;
    for Deepgram the voice is its model). Raises ProviderError."""
    v = cfg.get("voice") or {}
    p = v.get("tts_provider") or "openai"
    text = text[:4000]
    if p == "elevenlabs":
        m = v.get("tts_model") or ELEVENLABS_TTS_MODEL
        url = f"{base_url(cfg, 'elevenlabs')}/v1/text-to-speech/{urllib.parse.quote(voice or v.get('tts_voice') or ELEVENLABS_VOICE, safe='')}?output_format=mp3_44100_128"
        headers = {"Content-Type": "application/json", "xi-api-key": _key(cfg, "elevenlabs"), "Accept": "audio/mpeg"}
        body = {"text": text, "model_id": m}
    elif p == "deepgram":
        m = voice or v.get("tts_model") or DEEPGRAM_TTS_MODEL
        url = f"{base_url(cfg, 'deepgram')}/v1/speak?" + urllib.parse.urlencode({"model": m, "encoding": "mp3"})
        headers = {"Content-Type": "application/json", "Authorization": f"Token {_key(cfg, 'deepgram')}"}
        body = {"text": text}
    else:
        m = v["tts_model"]
        base = (v.get("tts_base_url") or cfg["llm"]["base_url"]).rstrip("/")
        key = v.get("tts_api_key") or (cfg["llm"].get("api_key") if not v.get("tts_base_url") else None)
        url = base + "/audio/speech"
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
        body = {"model": m, "input": text, "voice": voice or v.get("tts_voice") or "alloy", "response_format": "mp3"}
    with _ledger("model.speech", cfg, f"{p}:{m}", {"chars": len(text)}):
        data, ctype = _request(url, data=json.dumps(body).encode(), headers=headers, timeout=60)
    return data, ctype if ctype and ctype.startswith("audio/") else "audio/mpeg"


# ---------- checking a provider from Settings ----------
def check(cfg, provider):
    """Whether the provider answers with these settings: (ok, what it said). Lists models; nothing is billed."""
    if provider not in PROVIDERS:
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
        if provider == "assemblyai":
            _json(base + "/v2/transcript?limit=1", headers={"Authorization": key}, timeout=15)
            return True, "signed in"
        _json(base + "/v1/projects", headers={"Authorization": f"Token {key}"}, timeout=15)
        return True, "signed in"
    except ProviderError as e:
        return False, str(e)
