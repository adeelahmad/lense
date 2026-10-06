"""Find recordings, transcribe them, and import transcripts made elsewhere."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import html
import json
import pathlib
import re
import subprocess
import time
from difflib import SequenceMatcher

import numpy as np

from . import speech, store

TAG_RX = re.compile(r"<\|([^|]*)\|>")
SR = 16000


def parse_sv(raw):
    """Split SenseVoice rich output into (language, emotion, event, text)."""
    tags = TAG_RX.findall(raw or "")
    lang = emo = ev = None
    for t in tags:
        if t.upper() in store.SV_EMOTION:
            emo = store.SV_EMOTION[t.upper()]
        elif t in store.SV_EVENTS:
            ev = t
        elif re.fullmatch(r"[a-z]{2,3}", t):
            lang = t
    return lang, emo, ev, TAG_RX.sub("", raw or "").strip()


# ---------- files ----------
def probe(path):
    ch = 0
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=channels,codec_type", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        j = json.loads(out.stdout or "{}")
        dur = float(j.get("format", {}).get("duration") or 0)
        ch = max([s.get("channels", 0) for s in j.get("streams", []) if s.get("codec_type") == "audio"] or [0])
        if dur:
            return int(dur * 1000) or None, int(ch) or None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    dur, wav_ch = _probe_wav(path)
    return dur, (int(ch) or None) or wav_ch


def _probe_wav(path):
    """Duration and channels of a PCM WAV file without ffprobe (else the duration falls back to the transcript's end)."""
    import wave

    try:
        with wave.open(str(path), "rb") as w:
            rate, frames, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        return (int(frames * 1000 / rate) or None) if rate else None, ch or None
    except (OSError, EOFError, wave.Error):
        return None, None


def fingerprint(path, block=65536, db=None, cfg=None):
    """What tells the same file apart from others: its size and its first and last 64 KB. An encrypted file (pass the
    database and configuration) is fingerprinted by its plain bytes, so it matches the file it was."""
    p = pathlib.Path(path)
    if db is not None:
        from . import keyring

        if keyring.is_encrypted(p):
            with keyring.Reader(db, cfg, p) as f:
                return _fingerprint(f, f.size, block)
    with open(p, "rb") as f:
        return _fingerprint(f, p.stat().st_size, block)


def _fingerprint(f, size, block):
    h = hashlib.sha1(str(size).encode())
    h.update(f.read(block))
    if size > 2 * block:
        f.seek(size - block)
        h.update(f.read(block))
    return h.hexdigest()[:20]


DATE_RX = re.compile(r"((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?:[ T_-]?(\d{2})[-_.:h]?(\d{2})(?:[-_.:m]?(\d{2}))?)?")


def recorded_at(path, mtime):
    for m in DATE_RX.finditer(pathlib.Path(path).stem):
        y, mo, d, h, mi, s = (int(x) if x else 0 for x in m.groups())
        try:
            return dt.datetime(y, mo, d, h, mi, s).isoformat(timespec="seconds")
        except ValueError:
            continue
    return dt.datetime.fromtimestamp(mtime).isoformat(timespec="seconds")


DECODE_SECONDS = 6 * 3600  # a recording is decoded far faster than it plays; past this ffmpeg is stuck
READ_SECONDS = 300  # antiword, catdoc, pdftotext


def decode(path, channels=1):
    try:
        out = _decode(path, channels)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg: decoding took longer than {DECODE_SECONDS} s") from None
    if out.returncode != 0:
        raise RuntimeError("ffmpeg: " + out.stderr.decode(errors="replace")[-300:])
    x = np.frombuffer(out.stdout, dtype=np.float32)
    return x.reshape(-1, channels) if channels > 1 else x


def _decode(path, channels):
    return subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            str(channels),
            "-ar",
            str(SR),
            "-",
        ],
        capture_output=True,
        timeout=DECODE_SECONDS,
    )


def envelope(x, bins=1600):
    """Loudness per time bin (0..255), drawn behind the player's rail."""
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = len(x)
    bins = max(1, min(bins, n // 160))
    edges = np.linspace(0, n, bins + 1).astype(np.int64)
    rms = np.array([np.sqrt(np.mean(np.square(x[a:b]))) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])])
    peak = float(np.percentile(rms, 99)) or 1e-9
    return bytes((np.clip(np.sqrt(rms / peak), 0, 1) * 255).astype(np.uint8))


def scan(db, cfg, only=None, log=print):
    from . import deletion

    exts = {e.lower() for e in cfg["audio"]["extensions"]}
    stats = {"new": 0, "known": 0, "changed": 0, "duplicate": 0, "deleted": 0}
    for name, spec in cfg["namespaces"].items():
        if only and name != only:
            continue
        nid = store.ns_id(db, name)
        home = store.default_collection(db, nid)  # where new files go
        gone_paths, gone_fps = deletion.gone(db, nid)  # recordings someone deleted stay deleted
        for root in spec["paths"]:
            rootp = pathlib.Path(root)
            if not rootp.exists():
                log(f"  {name}: {root} is not reachable, skipped")
                continue
            for p in sorted(rootp.rglob("*")):
                if not p.is_file() or p.suffix.lower() not in exts or p.name.startswith("."):
                    continue
                st = p.stat()
                row = db.one(
                    "SELECT record::id(id) AS id, size, mtime FROM recording WHERE space = $s AND path = $p LIMIT 1", s=nid, p=str(p)
                )
                if row and row.get("size") == st.st_size and abs((row.get("mtime") or 0) - st.st_mtime) < 1:
                    stats["known"] += 1
                    continue
                if not row and str(p) in gone_paths:
                    stats["deleted"] += 1
                    continue
                fp = fingerprint(p)
                if not row and fp in gone_fps:
                    stats["deleted"] += 1
                    continue
                dup = db.one("SELECT record::id(id) AS id, path FROM recording WHERE space = $s AND fingerprint = $f LIMIT 1", s=nid, f=fp)
                if dup and dup["path"] != str(p) and pathlib.Path(store.resolve_path(cfg, dup["path"])).exists():
                    stats["duplicate"] += 1
                    continue
                dur, ch = probe(p)
                if dup or row:
                    rid = (dup or row)["id"]
                    if row and not dup:
                        store.reset_downstream(db, rid)
                        db.q(
                            "UPDATE $r SET status = 'new', fingerprint = $f, fp_key = $k",
                            r=store.R("recording", rid),
                            f=fp,
                            k=f"{nid}:{fp}",
                        )
                        stats["changed"] += 1
                    else:
                        stats["known"] += 1
                    db.q(
                        "UPDATE $r MERGE $d",
                        r=store.R("recording", rid),
                        d=store.clean({"path": str(p), "size": st.st_size, "mtime": st.st_mtime, "duration_ms": dur, "channels": ch}),
                    )
                    continue
                rid = db.next_id("recording")
                db.q(
                    "CREATE $r CONTENT $d",
                    r=store.R("recording", rid),
                    d=store.clean(
                        {
                            "space": nid,
                            "collection": home,
                            "path": str(p),
                            "source": "audio",
                            "fingerprint": fp,
                            "fp_key": f"{nid}:{fp}",
                            "title": p.stem,
                            "recorded_at": recorded_at(p, st.st_mtime),
                            "duration_ms": dur,
                            "channels": ch,
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                            "status": "new",
                            "created_at": store.now(),
                        }
                    ),
                )
                stats["new"] += 1
    return stats


# ---------- engines ----------
def pick_device(pref):
    if pref and pref != "auto":
        return pref
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


class SenseVoice:
    """fsmn-vad finds speech, SenseVoice transcribes each span with emotion and event tags."""

    name = "sensevoice"

    def __init__(self, cfg):
        c, t = cfg["transcribe"]["sensevoice"], cfg["transcribe"]
        try:
            from funasr import AutoModel
        except ImportError as e:
            raise EngineMissing("SenseVoice needs FunASR: uv sync --extra sensevoice, or EXTRAS=sensevoice for the Docker images") from e
        dev = pick_device(t["device"])
        kw = {"disable_update": True, "device": "cpu" if dev == "mps" else dev}
        if c.get("hub") == "hf":
            kw["hub"] = "hf"
        self.max_ms = int(c.get("vad_max_segment_ms", 30000))
        self.vad = AutoModel(model=c.get("vad_model", "fsmn-vad"), max_single_segment_time=self.max_ms, **kw)
        self.asr = AutoModel(model=c["model"], **kw)
        self.lang = t.get("language") or "auto"
        self.batch = int(c.get("batch_size", 16))

    def _run(self, clips):
        try:
            return self.asr.generate(input=clips, cache={}, language=self.lang, use_itn=True, batch_size=len(clips))
        except Exception:  # noqa: BLE001 - older FunASR builds only take one input at a time
            return [self.asr.generate(input=c, cache={}, language=self.lang, use_itn=True)[0] for c in clips]

    def transcribe(self, audio):
        spans = self.vad.generate(input=audio)[0]["value"]
        pieces = [(s, min(int(e), s + self.max_ms)) for b, e in spans for s in range(int(b), int(e), self.max_ms)]
        out = []
        for i in range(0, len(pieces), self.batch):
            batch = [(s, e) for s, e in pieces[i : i + self.batch] if e - s >= 200]
            if not batch:
                continue
            res = self._run([audio[s * SR // 1000 : e * SR // 1000] for s, e in batch])
            for (s, e), r in zip(batch, res):
                lang, emo, ev, text = parse_sv(r.get("text", ""))
                if any(ch.isalnum() for ch in text):
                    out.append(
                        {
                            "t0": s,
                            "t1": e,
                            "text": text,
                            "raw_text": r.get("text"),
                            "emotion": emo,
                            "event": ev,
                            "lang": lang,
                            "words": None,
                        }
                    )
        return out


class Whisper:
    """faster-whisper (CUDA or CPU) or mlx-whisper (Apple Silicon), with word timestamps."""

    def __init__(self, cfg, mlx=False):
        t = cfg["transcribe"]
        self.mlx, self.name = mlx, ("mlx-whisper" if mlx else "whisper")
        self.lang = None if t.get("language") in (None, "auto") else t["language"]
        try:
            if mlx:
                import mlx_whisper

                self.m, self.repo = mlx_whisper, t["mlx_whisper"]["model"]
            else:
                from faster_whisper import WhisperModel

                dev = pick_device(t["device"])
                self.m = WhisperModel(
                    t["whisper"]["model"], device="cpu" if dev == "mps" else dev, compute_type=t["whisper"]["compute_type"]
                )
        except ImportError as e:
            name = "mlx-whisper" if mlx else "faster-whisper"
            raise EngineMissing(f"{name} isn't installed: uv sync --extra {'mlx' if mlx else 'whisper'}") from e

    def transcribe(self, audio):
        if self.mlx:
            segs = self.m.transcribe(audio, path_or_hf_repo=self.repo, word_timestamps=True, language=self.lang)["segments"]
            rows = [(s["start"], s["end"], s["text"], [(w["word"], w["start"], w["end"]) for w in s.get("words") or []]) for s in segs]
        else:
            segs, _ = self.m.transcribe(audio, vad_filter=True, word_timestamps=True, language=self.lang)
            rows = [(s.start, s.end, s.text, [(w.word, w.start, w.end) for w in (s.words or [])]) for s in segs]
        return [
            {
                "t0": int(a * 1000),
                "t1": int(b * 1000),
                "text": x.strip(),
                "raw_text": None,
                "emotion": None,
                "event": None,
                "lang": self.lang,
                "words": json.dumps([[w.strip(), int(p * 1000), int(q * 1000)] for w, p, q in ws]),
            }
            for a, b, x, ws in rows
            if x.strip()
        ]


ENGINE_MODULES = {"sensevoice": "funasr", "mlx-whisper": "mlx_whisper", "whisper": "faster_whisper"}  # the order to fall back in


class EngineMissing(ValueError):
    """A speech-to-text engine's packages aren't installed (or don't import) on this worker."""


def installed(engine):
    return importlib.util.find_spec(ENGINE_MODULES[engine]) is not None


def _make_engine(cfg, e):
    if e == "sensevoice":
        return SenseVoice(cfg)
    if e in ("whisper", "mlx-whisper"):
        return Whisper(cfg, mlx=e == "mlx-whisper")
    if e in speech.PROVIDERS:
        try:
            return speech.Engine(cfg, e)
        except speech.ProviderError as err:
            raise EngineMissing(str(err)) from None
    raise SystemExit(f"unknown transcribe.engine {e!r}")


def get_engine(cfg, log=None):
    """The configured engine; when it isn't installed here (or its packages are there but don't import, e.g. FunASR
    without PyTorch), the first one that is (the Docker images and packages carry faster-whisper, not SenseVoice, the
    default), so an import is transcribed rather than failing. With none at all, says how to add one."""
    e = cfg["transcribe"]["engine"]
    if e not in ENGINE_MODULES:
        return _make_engine(cfg, e)
    why = {}
    for x in [e] + [x for x in ENGINE_MODULES if x != e]:
        if x != e and not installed(x):
            continue
        try:
            engine = _make_engine(cfg, x)
        except EngineMissing as err:
            why[x] = str(err)
            continue
        if x != e and log:
            log(f"  {e} isn't installed on this worker ({why[e]}); transcribing with {x}")
        return engine
    raise EngineMissing(
        f"no speech-to-text engine is installed on this worker ({why[e]}). In Docker, rebuild the images "
        "(make dev, or docker compose up --build --renew-anon-volumes): they carry faster-whisper, and "
        "EXTRAS=sensevoice adds SenseVoice. Elsewhere: uv sync --extra whisper (or --extra sensevoice)"
    )


def segment_rows(rid, nid, segs):
    out = []
    for i, s in enumerate(segs):
        t0 = int(s["t0"])
        t1 = int(max(s["t1"], t0 + 1))
        out.append(
            store.clean(
                {
                    "id": store.R("segment", rid * store.SEG + i),
                    "recording": rid,
                    "space": nid,
                    "idx": i,
                    "t0": t0,
                    "t1": t1,
                    "dur": t1 - t0,
                    "local_speaker": s.get("speaker"),
                    "provider_speaker": s.get("provider_speaker"),  # a speech provider's label (domain/speech.py)
                    "text": s["text"],
                    "raw_text": s.get("raw_text"),
                    "emotion": store.norm_emotion(s.get("emotion")),
                    "event": s.get("event"),
                    "lang": s.get("lang"),
                    "words": s.get("words"),
                    "page": s.get("page"),  # a document's or an image's page (domain/documents.py)
                    "box": s.get("box"),
                }
            )
        )
    return out


def _language(segs):
    langs = [s.get("lang") for s in segs if s.get("lang") and s.get("lang") != "nospeech"]
    return max(set(langs), key=langs.count) if langs else None


def write_transcript(db, rid, nid, segs, patch):
    """Replace a recording's transcript and everything derived from it, atomically."""
    rows = segment_rows(rid, nid, segs)  # overwrite segments in place and drop the extra ones (see store.DOWNSTREAM)
    db.run(
        store.DOWNSTREAM + ["FOR $s IN $segs { UPSERT $s.id CONTENT $s; }", "UPDATE $rec MERGE $patch", "UPDATE $rec SET embedded = NONE"],
        rid=rid,
        keep=len(rows),
        segs=rows,
        rec=store.R("recording", rid),
        patch=patch,
    )
    db.q("UPDATE $rec SET error = NONE, diarized_at = NONE, analyzed_at = NONE", rec=store.R("recording", rid))


def audio_path(db, cfg, rec, plain=True):
    """A local file for a recording's audio: the file itself, or a cached copy of one on a storage source. An
    encrypted file comes as a plain working copy for the tools to read, unless `plain` is False."""
    from . import keyring

    if rec.get("remote"):
        from . import sources

        path = str(sources.cached_copy(db, cfg, rec["remote"]["source"], rec["remote"]["path"], rec.get("space")))
    else:
        path = store.resolve_path(cfg, rec.get("path"))
    return keyring.working_copy(db, cfg, path) if plain else path


def add_envelope(db, cfg, rid):
    """The waveform of media attached to a recording whose transcript was imported before."""
    rec = db.one("SELECT path, source, remote FROM $r", r=store.R("recording", rid))
    db.q("UPDATE $r SET envelope = $e", r=store.R("recording", rid), e=envelope(decode(audio_path(db, cfg, rec))))


def transcribe_one(db, cfg, rid, log=print, engine=None):
    r = db.one("SELECT record::id(id) AS id, space, path, title, remote FROM $r", r=store.R("recording", rid))
    engine, t = engine or get_engine(cfg, log), time.time()
    audio = decode(audio_path(db, cfg, r))
    segs = engine.transcribe(audio)
    env = envelope(audio)
    del audio
    write_transcript(
        db,
        rid,
        r["space"],
        segs,
        store.clean(
            {"status": "transcribed", "engine": engine.name, "language": _language(segs), "envelope": env, "transcribed_at": store.now()}
        ),
    )
    log(f"  {r['title']}: {len(segs)} segments in {time.time() - t:.0f}s")
    return len(segs)


def transcribe_pending(db, cfg, ns=None, limit=0, force=False, log=print):
    where = "source = 'audio' AND " + ("true" if force else "status IN ['new', 'error']")
    if ns:
        where += " AND space = $s"
    rows = db.rows(
        f"SELECT record::id(id) AS id, title, recorded_at FROM recording WHERE {where} ORDER BY recorded_at, id",
        s=store.ns_id(db, ns, create=False) if ns else None,
    )[: limit or None]
    if not rows:
        return 0
    engine, done = get_engine(cfg, log), 0
    from . import keyring

    for r in rows:
        try:
            with keyring.work(cfg):
                transcribe_one(db, cfg, r["id"], log, engine)
            done += 1
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the batch
            db.q("UPDATE $r SET status = 'error', error = $e", r=store.R("recording", r["id"]), e=f"{type(e).__name__}: {e}"[:500])
            log(f"  {r['title']}: failed ({type(e).__name__}: {e})")
    return done


# ---------- transcripts made elsewhere ----------
def _norm(w):
    return re.sub(r"[^\w']", "", w.lower())


def _split_timed(seg, max_words=40):
    words = seg["text"].split()
    if len(words) <= max_words:
        return [seg]
    groups, cur = [], []
    for s in re.split(r"(?<=[.!?…])\s+", seg["text"]):
        w = s.split()
        if cur and len(cur) + len(w) > max_words:
            groups.append(cur)
            cur = []
        cur += w
        while len(cur) > max_words * 1.5:
            groups.append(cur[:max_words])
            cur = cur[max_words:]
    if cur:
        groups.append(cur)
    out, acc, span = [], 0, seg["t1"] - seg["t0"]
    for g in groups:
        a = seg["t0"] + span * acc / len(words)
        acc += len(g)
        out.append({**seg, "t0": int(a), "t1": int(seg["t0"] + span * acc / len(words)), "text": " ".join(g)})
    return out


def stitch_chunks(rows):
    """Your pipeline's overlapping SenseVoice windows -> one continuous, de-duplicated transcript.

    Each window repeats the tail of the previous one. The repeat is found by matching the last
    words of the previous window against the first words of this one; if no match is found,
    the share of words inside the overlap is dropped instead.
    """
    rows = sorted(rows, key=lambda r: (r["start_ms"], r.get("index", 0)))
    out, prev_words, prev_end = [], [], None
    for r in rows:
        lang, emo, ev, tagged = parse_sv(r.get("raw_text") or "")
        text = (r.get("text") or tagged or "").strip()
        words = text.split()
        t0, t1 = int(r["start_ms"]), int(r["end_ms"])
        if not any(ch.isalnum() for ch in text):
            prev_end = max(prev_end or 0, t1)
            continue
        cut = 0
        if prev_words and prev_end is not None and t0 < prev_end:
            tail, head = [_norm(w) for w in prev_words[-40:]], [_norm(w) for w in words[:40]]
            best = None
            for b in SequenceMatcher(None, tail, head, autojunk=False).get_matching_blocks():
                if b.size >= 2 and b.a + b.size >= len(tail) - 4 and b.b <= 15:
                    best = b
            cut = best.b + best.size if best else int(len(words) * (prev_end - t0) / max(1, t1 - t0) * 0.8)
            t0 = prev_end
        prev_words, prev_end = words, t1
        kept = words[cut:]
        if kept:
            out.append(
                {
                    "t0": t0,
                    "t1": max(t1, t0 + 1),
                    "text": " ".join(kept),
                    "raw_text": r.get("raw_text"),
                    "emotion": emo,
                    "event": ev,
                    "lang": lang,
                }
            )
    return [s for seg in out for s in _split_timed(seg)]


def _ms(v, scale):
    return None if v is None else int(round(float(v) * scale))


def _generic(s):
    if "start_ms" in s or "t0" in s:
        a, b, k = s.get("start_ms", s.get("t0")), s.get("end_ms", s.get("t1")), 1
    else:
        a, b, k = s.get("start"), s.get("end"), 1000
    words = s.get("words")
    if words and isinstance(words[0], dict):
        words = json.dumps([[w.get("word", "").strip(), _ms(w.get("start"), k), _ms(w.get("end"), k)] for w in words])
    tags = s.get("tags") or {}
    return {
        "t0": _ms(a, k),
        "t1": _ms(b, k),
        "text": str(s.get("text", "")).strip(),
        "speaker": s.get("speaker") or s.get("spk"),
        "emotion": s.get("emotion") or tags.get("emotion"),
        "event": s.get("event"),
        "lang": s.get("lang") or s.get("language"),
        "words": words if isinstance(words, str) else None,
    }


PIPE = re.compile(r"^([^|]{1,40})\|([^|]{0,24})\|(.+)$")
SPK = re.compile(r"^(?:\[?((?:\d{1,2}:)?\d{1,2}:\d{2})\]?\s+)?([A-Z][\w .'’-]{0,30}?)\s*:\s+(\S.*)$")
VTT_TIME = re.compile(r"((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})")


def _clock(s):
    parts = s.replace(",", ".").split(":")
    return int(round(sum(float(p) * 60**i for i, p in enumerate(reversed(parts))) * 1000))


def _cues(raw):
    segs = []
    for block in re.split(r"\n\s*\n", raw.replace("\r", "")):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        for i, l in enumerate(lines):
            m = VTT_TIME.search(l)
            if m:
                text = " ".join(lines[i + 1 :]).strip()
                sm = SPK.match(text) or re.match(r"^<v\s+([^>]+)>(.*)$", text)
                spk, text = (sm.group(sm.lastindex - 1).strip(), sm.group(sm.lastindex).strip()) if sm else (None, text)
                if text:
                    segs.append({"t0": _clock(m.group(1)), "t1": _clock(m.group(2)), "text": re.sub(r"<[^>]+>", "", text), "speaker": spk})
                break
    return segs


HEADER = re.compile(r"^([A-Z][\w .'’-]{0,40}?)\s*[\(\[]?((?:\d{1,2}:)?\d{1,2}:\d{2})(?:\.\d+)?[\)\]]?\s*:?\s*$")
PAREN = re.compile(r"^([A-Z][\w .'’-]{0,40}?)\s*[\(\[]((?:\d{1,2}:)?\d{1,2}:\d{2})[\)\]]\s*:\s*(\S.*)$")


def _speaker_names(matches):
    names = {}
    for m in matches:
        names[m] = names.get(m, 0) + 1
    return names


def _text(raw):
    raw = raw.replace("\r", "")
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    if not lines:
        return []
    # Otter, Zoom and Teams exports: a "Name  12:34" line, then what they said
    heads = [HEADER.match(l) for l in lines]
    if (
        sum(1 for h in heads if h) >= 2
        and len({h.group(1).strip() for h in heads if h}) <= 12
        and sum(1 for h in heads if h) >= 0.15 * len(lines)
    ):
        segs = []
        for l, h in zip(lines, heads):
            if h:
                segs.append({"speaker": h.group(1).strip(), "text": "", "t0": _clock(h.group(2))})
            elif segs:
                segs[-1]["text"] = (segs[-1]["text"] + " " + l).strip()
        return _timed([s for s in segs if s["text"]])
    paren = [PAREN.match(l) for l in lines]
    if sum(1 for m in paren if m) >= max(2, 0.3 * len(lines)):
        segs = []
        for l, m in zip(lines, paren):
            if m:
                segs.append({"speaker": m.group(1).strip(), "text": m.group(3).strip(), "t0": _clock(m.group(2))})
            elif segs:
                segs[-1]["text"] += " " + l
        return _timed(segs)
    segs = []
    pipe = sum(1 for l in lines if PIPE.match(l)) >= max(3, 0.6 * len(lines))
    spk_hits = [SPK.match(l) for l in lines]
    names = _speaker_names([m.group(2).strip() for m in spk_hits if m])
    spk = not pipe and sum(1 for m in spk_hits if m) >= max(3, 0.3 * len(lines)) and len(names) >= 2 and max(names.values()) >= 2
    if not pipe and not spk:  # prose: paragraphs, then sentence groups of about 40 words
        for block in re.split(r"\n\s*\n", raw):
            words = " ".join(block.split())
            if words:
                segs += [{"speaker": None, "text": g["text"], "t0": None} for g in _split_timed({"t0": 0, "t1": 1, "text": words})]
        return _timed(segs)
    for l in lines:
        m = PIPE.match(l) if pipe else SPK.match(l) if spk else None
        if m and pipe:
            segs.append({"speaker": m.group(1).strip(), "emotion": m.group(2).strip(), "text": m.group(3).strip(), "t0": None})
        elif m:
            segs.append({"speaker": m.group(2).strip(), "text": m.group(3).strip(), "t0": _clock(m.group(1)) if m.group(1) else None})
        elif segs and (pipe or spk):
            segs[-1]["text"] += " " + l
        elif not (pipe or spk):
            segs.append({"speaker": None, "text": l, "t0": None})
    return _timed(segs)


def _timed(segs):
    t = 0
    for i, s in enumerate(segs):  # untimed lines get a speaking-rate estimate
        n = len(s["text"].split())
        if s["t0"] is None:
            s["t0"], s["guessed"] = t, True
        nxt = next((x["t0"] for x in segs[i + 1 :] if x["t0"] is not None), None)
        s["t1"] = nxt if nxt is not None and nxt > s["t0"] else s["t0"] + n * 385
        t = s["t1"] + 250
    return segs


# ---------- documents: txt, md, mdx, docx, doc, pdf ----------
def strip_markdown(text, mdx=False):
    """Markdown or MDX to plain text, keeping 'Name: text' and 'speaker|label|text' lines intact."""
    t = text.replace("\r", "")
    t = re.sub(r"\A---\n.*?\n---\n", "", t, flags=re.S)
    t = re.sub(r"```.*?```", "", t, flags=re.S)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    if mdx:
        t = re.sub(r"^(?:import|export)\s.*$", "", t, flags=re.M)
        t = re.sub(r"\{/\*.*?\*/\}", "", t, flags=re.S)
    t = re.sub(r"<[A-Za-z][^<>]*/>", "", t)
    t = re.sub(r"</?[A-Za-z][^<>]*>", "", t)
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s+", "", t, flags=re.M)
    t = re.sub(r"^\s{0,3}>\s?", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*+]\s+(?=\S)", "", t, flags=re.M)
    t = re.sub(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$", "", t, flags=re.M)
    t = re.sub(r"^\|(.*)\|\s*$", lambda m: "  ".join(c.strip() for c in m.group(1).split("|")), t, flags=re.M)
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    return t


def read_docx(path):
    """Paragraph text from a .docx (tables included), without extra dependencies."""
    import xml.etree.ElementTree as ET
    import zipfile

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    lines = []
    for p in root.iter(W + "p"):
        parts = []
        for el in p.iter():
            if el.tag == W + "t" and el.text:
                parts.append(el.text)
            elif el.tag == W + "tab":
                parts.append("\t")
            elif el.tag in (W + "br", W + "cr"):
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


def read_doc(path):
    """Legacy Word files need antiword, catdoc or LibreOffice (the Docker image has antiword)."""
    import shutil
    import tempfile

    for cmd in (["antiword", "-w", "0", str(path)], ["catdoc", "-w", str(path)]):
        if shutil.which(cmd[0]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=READ_SECONDS)
            except subprocess.TimeoutExpired:
                continue
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if office:
        from .convert import _run

        with tempfile.TemporaryDirectory() as d:
            # a profile of its own, so it neither waits on nor disturbs a LibreOffice the user has open
            argv = [office, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard"]
            _run(argv + [f"-env:UserInstallation=file://{d}/profile", "--convert-to", "txt:Text", "--outdir", d, str(path)], 300, cwd=d)
            for f in pathlib.Path(d).glob("*.txt"):
                return f.read_text(encoding="utf-8", errors="replace")
    raise SystemExit("reading .doc needs antiword, catdoc or LibreOffice installed (or save it as .docx)")


def read_pdf(path):
    try:
        from pypdf import PdfReader

        text = "\n\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    except ImportError:
        import shutil

        if not shutil.which("pdftotext"):
            raise SystemExit("reading PDFs needs pypdf (pip install pypdf) or pdftotext") from None
        try:
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=READ_SECONDS).stdout
        except subprocess.TimeoutExpired:
            raise SystemExit(f"reading {path} took longer than {READ_SECONDS} s") from None
    if not text.strip():
        raise SystemExit(f"{path} has no text layer (a scan?); run OCR first")
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


def _html_text(markup):
    t = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", "", markup or "")
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(t)).strip()


def read_email(path):
    """An .eml email as Markdown: its subject as the heading, who sent it to whom and when, its attachments by name,
    then its text (the details are 'Label — value' lines, which the transcript reader doesn't take for speakers)."""
    from . import convert

    e = convert.read_eml(path)
    who = [re.sub(r"<([^<>\s]+@[^<>\s]+)>", r"(\1)", e[k] or "") for k in ("from", "to", "cc")]  # Markdown drops <mail>
    rows = [("From", who[0]), ("To", who[1]), ("Cc", who[2]), ("Date", e["date"])]
    rows.append(("Attachments", ", ".join(p["name"] for p in e["parts"] if p["attached"])))
    body = e.get("text") if e.get("text") is not None else _html_text(e.get("html"))
    head = [f"{k} — {v}" for k, v in rows if v]
    return "\n".join([f"# {e['subject'] or '(no subject)'}", "", *head, "", (body or "").strip()]) + "\n"


def read_calendar(path):
    """An .ics calendar as Markdown, one section per event (calendars.py)."""
    from . import calendars

    return calendars.text(pathlib.Path(path).read_text(encoding="utf-8-sig", errors="replace"))


DOC_READERS = {".docx": read_docx, ".doc": read_doc, ".pdf": read_pdf}
MARKDOWN_READERS = {".eml": read_email, ".ics": read_calendar}  # their subject or event title is the title
FORMATS = ("auto", "text", "markdown", "mdx", "json", "jsonl", "srt", "vtt")


def read_text_transcript(raw, fmt="auto", name=None):
    """Transcript text in any supported shape: pasted, or read from a file. {segments, speakers, title, timed}; lines
    that don't say when they are get a speaking-rate estimate, and `timed` is false when every line got one."""
    raw = raw.lstrip("\ufeff")
    fmt = fmt or "auto"
    if fmt == "auto":
        ext = pathlib.Path(name or "").suffix.lower()
        fmt = {
            ".md": "markdown",
            ".markdown": "markdown",
            ".mdx": "mdx",
            ".json": "json",
            ".jsonl": "jsonl",
            ".srt": "srt",
            ".vtt": "vtt",
            ".txt": "text",
            ".text": "text",
        }.get(ext) or sniff(raw)
    speakers, title = {}, None
    if fmt in ("markdown", "mdx"):
        fm = re.match(r"\A---\n(.*?)\n---\n", raw, re.S)
        tm = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", fm.group(1), re.M) if fm else None
        hm = re.search(r"^#\s+(.+)$", raw, re.M)
        title = (tm or hm).group(1).strip() if (tm or hm) else None
    if fmt in ("json", "jsonl"):
        if fmt == "jsonl" or (raw.lstrip()[:1] == "{" and "\n{" in raw.strip()):
            rows = [json.loads(l) for l in raw.splitlines() if l.strip()]
            if rows and "start_ms" in rows[0] and ("raw_text" in rows[0] or "index" in rows[0]):
                return {"segments": stitch_chunks(rows), "speakers": {}, "title": None, "timed": True}
            segs = [_generic(r) for r in rows]
        else:
            j = json.loads(raw)
            if isinstance(j, dict) and str(j.get("lens", "")).startswith("lens/"):
                speakers, title = (j.get("doc") or {}).get("speakers") or {}, (j.get("doc") or {}).get("title")
            segs = [_generic(s) for s in (j.get("segments", []) if isinstance(j, dict) else j)]
    elif fmt in ("srt", "vtt"):
        segs = _cues(raw)
    elif fmt in ("markdown", "mdx"):
        segs = _text(strip_markdown(raw, mdx=fmt == "mdx"))
    else:
        segs = _text(strip_markdown(raw, mdx=fmt == "mdx") if fmt in ("markdown", "mdx") else raw)
    segs = [s for s in segs if s.get("text")]
    t = 0
    for s in segs:
        if s.get("t0") is None:
            s["t0"], s["guessed"] = t, True
        if s.get("t1") is None:
            s["t1"] = s["t0"] + 385 * len(s["text"].split())
        t = s["t1"]
    guessed = [s.pop("guessed", False) for s in segs]
    # timed: whether it says when its lines are, rather than every time being a speaking-rate estimate
    return {"segments": segs, "speakers": speakers, "title": title, "timed": bool(segs) and not all(guessed)}


def sniff(raw):
    """Guess a pasted transcript's format from its content."""
    head = raw.lstrip()
    if head[:1] in "{[":
        try:
            json.loads(head)
            return "json"
        except ValueError:
            lines = [l for l in head.splitlines() if l.strip()][:50]
            try:
                for l in lines:
                    json.loads(l)
                return "jsonl"
            except ValueError:
                pass
    if head.startswith("WEBVTT") or VTT_TIME.search(raw[:2000]):
        return "vtt"
    if re.search(r"(^|\n)\s{0,3}(#{1,6}\s|[-*]\s)|\*\*\S|^---\n", raw[:4000]):
        return "markdown"
    return "text"


def read_transcript(path, fmt="auto"):
    p = pathlib.Path(path)
    ext = p.suffix.lower()
    if ext in DOC_READERS:
        return read_text_transcript(DOC_READERS[ext](p), "text" if fmt == "auto" else fmt, p.name)
    if ext in MARKDOWN_READERS:
        return read_text_transcript(MARKDOWN_READERS[ext](p), "markdown" if fmt == "auto" else fmt, p.name)
    return read_text_transcript(p.read_text(encoding="utf-8-sig", errors="replace"), fmt, p.name)


def _store_import(db, cfg, ns, t, title, fp, src, st, audio, speaker_names, engine, collection=None):
    from . import deletion, speakers as spk

    segs = t["segments"]
    if not segs:
        raise SystemExit("no transcript text found")
    nid = store.ns_id(db, ns)
    home = store.home(db, nid, collection)  # KeyError for a collection of another namespace
    deletion.forget(db, nid, fp, src)  # imported on purpose: a deleted recording may come back
    dur, ch, env = (None, None, None)
    if audio:
        dur, ch = probe(audio)
        try:
            env = envelope(decode(audio))
        except (RuntimeError, OSError):
            env = None
    row = db.one("SELECT record::id(id) AS id FROM recording WHERE space = $s AND fingerprint = $f LIMIT 1", s=nid, f=fp)
    rid = row["id"] if row else db.next_id("recording")
    if not row:
        db.q(
            "CREATE $r CONTENT $d",
            r=store.R("recording", rid),
            d={"space": nid, "collection": home, "fingerprint": fp, "fp_key": f"{nid}:{fp}", "status": "new", "created_at": store.now()},
        )
    write_transcript(
        db,
        rid,
        nid,
        segs,
        store.clean(
            {
                "path": src,
                "source": "audio" if audio else "transcript",
                "title": title or t.get("title") or "Untitled",
                "recorded_at": st,
                "duration_ms": dur or max(s["t1"] for s in segs),
                "channels": ch,
                "language": _language(segs),
                "engine": engine,
                "envelope": env,
                "status": "transcribed",
                "transcribed_at": store.now(),
            }
        ),
    )
    names = {**(t.get("speakers") or {}), **(speaker_names or {})}
    locals_ = {s["speaker"] for s in segs if s.get("speaker")}
    if locals_:
        spk.assign_labels(db, nid, rid, {l: names.get(l, l) for l in locals_})
    return rid


def import_transcript(db, cfg, ns, tpath, audio=None, title=None, speaker_names=None, fmt="auto", log=print, collection=None):
    """A transcript file (txt, md, mdx, docx, doc, pdf, json, jsonl, srt, vtt, eml, ics), optionally with its audio, into a
    collection of the namespace (default: its default collection)."""
    src = pathlib.Path(audio or tpath)
    t = read_transcript(tpath, fmt)
    st = src.stat()
    return _store_import(
        db,
        cfg,
        ns,
        t,
        title or pathlib.Path(tpath).stem,
        fingerprint(src),
        str(src.resolve()),
        recorded_at(src, st.st_mtime),
        audio,
        speaker_names,
        "import:" + pathlib.Path(tpath).suffix.lstrip("."),
        collection,
    )


def import_text(db, cfg, ns, text, title=None, fmt="auto", speaker_names=None, name=None, collection=None):
    """Pasted text, or text piped in on the command line, into a collection of the namespace (default: its default)."""
    if not (text or "").strip():
        raise SystemExit("nothing to import")
    t = read_text_transcript(text, fmt, name)
    fp = "paste-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]
    first = next((l.strip() for l in text.splitlines() if l.strip()), "Pasted transcript")
    return _store_import(
        db,
        cfg,
        ns,
        t,
        title or re.sub(r"^#+\s*", "", first)[:80],
        fp,
        "paste:" + fp[6:],
        dt.datetime.now().isoformat(timespec="seconds"),
        None,
        speaker_names,
        "import:paste",
        collection,
    )
