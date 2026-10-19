"""Lining up an imported transcript with its audio (docs/processing.md, Lining up a transcript with its audio).

A transcript imported without times (plain text, Word, PDF, Markdown, `Name: text` lines without times) gets a
speaking-rate estimate for each line, and the asset says so (`times` "estimated"). Attaching its audio keeps those
times. When a person asks (the API, MCP or the asset page), an `align` step runs the audio through the speech engine
transcription uses (ingest.hear: the same engine, fallbacks, vocabulary, ledger and budgets), lines the imported words
up with the words it heard (difflib, rare words first, then the gaps between them), and gives each line the start of
its first matched word and the end of its last; lines that matched nothing share the time between their neighbours by
how many words each has. The text and speakers stay exactly as they are; only the lines' times change, and each line
keeps the times of its matched words (`words`), as transcription's lines do. The asset then says `times` "aligned",
with how many of the words were heard (`aligned`).

It is a heavy step (quiet.py), queued after what the asset's job still has to do and followed by analyze, embed and
report, so chapters, passages and the report follow the new times. Nothing runs on its own: imports keep their
estimated times until someone asks.

Refine later: an undo that puts the estimate back; lining up only the part of a long recording a transcript covers;
a summary's key points and action items taking the new times without summarising again; JSON transcripts without
times (their estimate leaves no gap between lines, so the upgrade step doesn't recognise them).
"""

from __future__ import annotations

import difflib
import json
import re

from . import ingest, store, transcript

R = store.R
STEP = "align"
AFTER = ["analyze", "embed", "report"]  # what reads the lines' times
TIMES = ("estimated", "aligned")  # lines whose times may be lined up again; any other's came with them or from transcription
GAP = 250  # ms the speaking-rate estimate leaves between lines (ingest._timed)
WORD_MS = 385  # ms a word takes at the estimate's speaking rate
TOKEN = re.compile(rf"{transcript.CJK.pattern}|[^\s{transcript.CJK.pattern[1:-1]}]+")


def _words(text):
    """A line's words as written, leaving out what has no letters or digits; a CJK character is a word of its own."""
    return [w for w in TOKEN.findall(text or "") if transcript._norm(w)]


def heard_words(segs):
    """[(word, t0, t1)] the engine heard, normalised (lower case, letters and digits), in order: each line's own
    timed words, or its words spread evenly over it when the engine gave none (SenseVoice)."""
    out = []
    for s in sorted(segs, key=lambda s: s["t0"]):
        timed = [(w[0], w[1], w[2]) for w in transcript.words(s)] or [(s.get("text") or "", int(s["t0"]), int(s["t1"]))]
        for text, t0, t1 in timed:
            ws = _words(text)
            step = (t1 - t0) / max(1, len(ws))
            out += [(transcript._norm(w), int(t0 + k * step), int(t0 + (k + 1) * step)) for k, w in enumerate(ws)]
    return out


def matches(a, b):
    """[(i, j)]: which word of `a` is which of `b`, in order. Rare words anchor first (difflib's autojunk sets the
    common ones aside in long texts), then the gaps between anchors are matched word for word."""
    out = []

    def match(a0, a1, b0, b1, junk):
        sm = difflib.SequenceMatcher(None, a[a0:a1], b[b0:b1], autojunk=junk)
        i = j = 0
        for blk in sm.get_matching_blocks():
            if junk and blk.a > i and blk.b > j:
                match(a0 + i, a0 + blk.a, b0 + j, b0 + blk.b, False)
            out.extend((a0 + blk.a + k, b0 + blk.b + k) for k in range(blk.size))
            i, j = blk.a + blk.size, blk.b + blk.size

    match(0, len(a), 0, len(b), True)
    return out


def line_up(lines, heard, duration_ms=None):
    """Each line's (t0, t1, words) from the words heard (heard_words), the number of the lines' words matched, and
    how many words they have. A line runs from its first matched word's start to its last one's end, and keeps those
    words as [word, t0, t1] in its own spelling; a run of lines that matched nothing shares the time between the
    lines either side (to the end of the audio after the last) by how many words each has, and keeps no words."""
    words = [_words(t) for t in lines]
    flat = [(n, k) for n, ws in enumerate(words) for k in range(len(ws))]
    pairs = matches([transcript._norm(words[n][k]) for n, k in flat], [w for w, _, _ in heard])
    got = [[] for _ in lines]
    for i, j in pairs:
        n, k = flat[i]
        got[n].append([words[n][k], heard[j][1], heard[j][2]])
    out = [(ws[0][1], max(ws[-1][2], ws[0][1] + 1), ws) if ws else None for ws in got]
    n = 0
    while n < len(lines):
        if out[n] is not None:
            n += 1
            continue
        k = next((m for m in range(n, len(lines)) if out[m] is not None), len(lines))
        weight = [max(1, len(words[m])) for m in range(n, k)]
        lo = out[n - 1][1] if n else 0
        hi = out[k][0] if k < len(lines) else max(duration_ms or 0, lo + WORD_MS * sum(weight))
        span, acc = max(0, hi - lo), 0
        for m, w in zip(range(n, k), weight):
            a = lo + span * acc // sum(weight)
            acc += w
            out[m] = (a, max(a + 1, lo + span * acc // sum(weight)), [])
        n = k
    return out, len(pairs), len(flat)


# ---------- asking for it ----------
def latest(db, rid):
    """The latest job to line the asset up: {id, status, error}, or None."""
    for j in db.rows(
        "SELECT record::id(id) AS id, status, steps, error FROM job WHERE recording = $r ORDER BY id DESC LIMIT 20", r=int(rid)
    ):
        if any(isinstance(s, dict) and s.get("type") == STEP for s in j.get("steps") or []):
            return {"id": j["id"], "status": j["status"], "error": j.get("error")}
    return None


def queue(db, rid, by=None):
    """Line the asset's transcript up with its audio, in the background: an `align` step and the steps that read the
    times, after what its job still has to do (jobs.add_steps), or a new job; the job already doing it if there is one.
    ValueError without audio, or when its lines' times came with it or from transcription; KeyError for no such
    asset."""
    from . import jobs

    rec = db.one("SELECT source, times FROM $r", r=R("recording", int(rid)))
    if rec is None:
        raise KeyError(rid)
    if rec.get("source") != "audio":
        raise ValueError("Attach its audio first: there's nothing to line the transcript up with.")
    if rec.get("times") not in TIMES:
        raise ValueError("Its lines already have times: they came with the transcript or from transcription.")
    j = latest(db, rid)
    if j and j["status"] in jobs.ACTIVE:
        return j["id"]
    return jobs.add_steps(db, int(rid), [{"type": STEP}, *AFTER], by=by)


# ---------- the job step ----------
def step(db, cfg, rid, say, spec=None):
    """The `align` step: the audio heard, the lines given the times of their words, the asset marked aligned."""
    from . import jobs

    rec = db.one(f"SELECT {ingest.HEAR_FIELDS}, source, times, duration_ms FROM $r", r=R("recording", int(rid))) or {}
    if rec.get("source") != "audio":
        raise jobs.Skip("there's no audio to line the transcript up with")
    if rec.get("times") not in TIMES:
        raise jobs.Skip("its lines' times came with the transcript or from transcription; kept them")
    lines = db.rows("SELECT record::id(id) AS id, idx, text FROM segment WHERE recording = $r ORDER BY idx", r=int(rid))
    if not lines:
        raise jobs.Skip("there are no lines to line up")
    say(f"listening to the audio to line up {len(lines)} line(s)")
    segs, engine, _ = ingest.hear(db, cfg, rec, say)
    heard = heard_words(segs)
    times, matched, total = line_up([s["text"] for s in lines], heard, rec.get("duration_ms"))
    if not matched:
        raise RuntimeError(
            f"none of the transcript's words were heard in its audio ({len(heard)} word(s) heard): is it the right file? "
            "The estimated times are kept"
        )
    rows = [
        store.clean({"id": R("segment", s["id"]), "t0": t0, "t1": t1, "dur": t1 - t0, "words": json.dumps(ws) if ws else None})
        for s, (t0, t1, ws) in zip(lines, times)
    ]
    share = round(matched / max(1, total), 3)
    db.run(
        [
            "FOR $s IN $rows { UPDATE $s.id SET t0 = $s.t0, t1 = $s.t1, dur = $s.dur, words = $s.words; }",
            "UPDATE $rec SET times = 'aligned', aligned = $a",
        ],
        rows=rows,
        rec=R("recording", int(rid)),
        a={"at": store.now(), "matched": share, "engine": engine.name},
    )
    alone = sum(1 for _, _, ws in times if not ws)
    say(
        f"lined up {len(lines)} line(s) with the audio: {matched} of {total} word(s) heard ({share:.0%})"
        + (f"; {alone} line(s) with none heard placed between their neighbours" if alone else "")
    )


# ---------- what imports say, and older ones ----------
def looks_estimated(lines):
    """Whether these lines' times ({t0, t1} in order) are the speaking-rate estimate a text import without times gets
    (ingest._timed): the first at 0, and each GAP after the one before ends."""
    return bool(lines) and lines[0]["t0"] == 0 and all(b["t0"] == a["t1"] + GAP for a, b in zip(lines, lines[1:]))


def migrate_estimated(db):
    """Transcripts imported before the asset said its times were estimated: those whose lines are spaced as the
    estimate spaces them say so now (`times` "estimated"). Only assets without `times` are looked at, so it's safe to
    run twice."""
    ids = db.values("SELECT VALUE record::id(id) FROM recording WHERE times = NONE AND string::starts_with(engine ?? '', 'import:')")
    for rid in ids:
        lines = db.rows("SELECT idx, t0, t1 FROM segment WHERE recording = $r ORDER BY idx", r=rid)
        if looks_estimated(lines):
            db.q("UPDATE $r SET times = 'estimated' WHERE times = NONE", r=R("recording", rid))
