"""A namespace's own words for transcription (`transcribe.vocabulary`, off by default).

The names speech-to-text gets wrong are the ones only you use: family, colleagues, places, projects. With the setting
on, each recording's transcription is given the words its namespace already knows, so the same mistakes stop coming
back:
- the custom vocabulary (`analysis.gazetteer`, Settings › Analysis), first, as you wrote it there;
- the names of the namespace's named speakers;
- its defined entities (entity_setup.py), however the setup writes them;
- words people put in when they corrected its transcripts (segment_edit, newest first), the ones with a capital
  letter that weren't there before: the names they had to fix.

How an engine takes them (`hints`): Whisper (faster-whisper, mlx-whisper) and OpenAI-compatible servers as the
prompt, a line of the names; Deepgram as key terms (Nova-3) or keywords; AssemblyAI as key terms (Universal-3 and
Slam-1) or boosted words. SenseVoice and ElevenLabs take no hints, so they transcribe as before. Nothing is sent
anywhere it wasn't going already: the words go only to the engine the recording goes to.
"""

from __future__ import annotations

import re
from collections import Counter

from . import store

MOST = 60  # terms per recording: Deepgram's key terms and Whisper's prompt both have room for about this many
PROMPT_CHARS = 600  # Whisper keeps the last 224 tokens of a prompt; a little more than that in characters
EDITS = 500  # the corrections read, newest first
WORD = re.compile(r"[^\W\d_][\w'’\-]*[^\W_]|[^\W\d_]", re.UNICODE)


def on(cfg):
    return bool((cfg.get("transcribe") or {}).get("vocabulary"))


def _corrected(db, nid):
    """Words people put in when they corrected the namespace's lines, most often first: the capitalised ones a line
    didn't have before (a name the engine got wrong), not the text cleanup's."""
    from .cleanup import CLEANER

    rows = db.rows(
        "SELECT before.text AS before, after.text AS after, at FROM segment_edit"
        " WHERE recording IN (SELECT VALUE record::id(id) FROM recording WHERE space = $s)"
        " AND after.text != NONE AND by != $c ORDER BY at DESC LIMIT $n",
        s=int(nid),
        c=CLEANER,
        n=EDITS,
    )
    seen: Counter[str] = Counter()
    for r in rows:
        had = {w.lower() for w in WORD.findall(str(r.get("before") or ""))}
        for w in WORD.findall(str(r.get("after") or "")):
            if len(w) >= 3 and any(ch.isupper() for ch in w) and w.lower() not in had:
                seen[w] += 1
    return [w for w, _ in seen.most_common()]


def terms(db, cfg, nid):
    """The words a recording of namespace `nid` is transcribed with, most wanted first; [] with the setting off."""
    if not on(cfg) or nid is None:
        return []
    gaz = [str(line).partition("|")[0] for line in (cfg.get("analysis") or {}).get("gazetteer") or []]  # "Name | TYPE"
    spoken = db.values("SELECT VALUE name FROM speaker WHERE space = $s AND name != NONE AND name != '' ORDER BY name", s=int(nid))
    named = [str(n) for n in spoken if not str(n).strip().lower().startswith("speaker")]  # not "Speaker 2"
    defined = db.values("SELECT VALUE name FROM entity WHERE space = $s AND defined = true AND hidden != true ORDER BY name", s=int(nid))
    out, keys = [], set()
    for t in [*gaz, *named, *(str(x) for x in defined), *_corrected(db, nid)]:
        t = re.sub(r"\s+", " ", t).strip()
        k = t.lower()
        if t and k not in keys and len(t) <= 50:
            keys.add(k)
            out.append(t)
        if len(out) >= MOST:
            break
    return out


def prompt(words):
    """The words as a line for a Whisper-style prompt, cut at a whole word to fit PROMPT_CHARS; '' for none."""
    line = ""
    for w in words:
        nxt = f"{line}, {w}" if line else w
        if len(nxt) + 1 > PROMPT_CHARS:
            break
        line = nxt
    return f"{line}." if line else ""


def for_recording(db, cfg, rid):
    """The words for recording `rid` (by its namespace)."""
    if not on(cfg):
        return []
    row = db.one("SELECT space FROM $r", r=store.R("recording", rid))
    return terms(db, cfg, (row or {}).get("space"))
