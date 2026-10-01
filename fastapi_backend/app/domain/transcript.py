"""Splitting and merging transcript lines, and the timings of their words (docs/api.md#recordings).

A line keeps its words' timings from transcription when the engine gave them (``words``, a JSON list of
[word, t0, t1] in ms). The player gets them as character ranges of the line's current text (`align`), so a corrected
line keeps the timings of the words it still has.

Lines are numbered: a segment's id is its recording's id × SEG + its index. Splitting or merging renumbers the lines
after it, and what points at line numbers follows: the edit history, people's entity corrections and the chapters.
Mentions are dropped for Analyze to find again; it runs after every change, as after a correction.
"""

from __future__ import annotations

import difflib
import json
import re

from . import store

R = store.R
SEG = store.SEG
# scripts written without spaces between words: lines join without one, and words are found one after another
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")
TOKEN = re.compile(r"\S+")


def words(seg):
    """The line's timed words, [(word, t0, t1)], or [] when transcription gave none."""
    raw = seg.get("words")
    try:
        ws = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        return []
    out = []
    for w in ws if isinstance(ws, list) else []:
        try:
            text, a, b = str(w[0]).strip(), int(w[1]), int(w[2])
        except (TypeError, ValueError, IndexError):
            continue
        if text:
            out.append((text, a, max(a, b)))
    return out


def _norm(s):
    return "".join(c for c in s.casefold() if c.isalnum())


def _placed(text, ws):
    """[(word index, c0, c1)]: where the text has each timed word, in order."""
    if not ws or not text:
        return []
    if CJK.search(text):
        out, at = [], 0
        for i, (w, _, _) in enumerate(ws):
            c = text.find(w, at)
            if c < 0 or c - at > 2 * len(w) + 8:  # gone from the text: don't jump ahead to a later one
                continue
            out.append((i, c, c + len(w)))
            at = c + len(w)
        return out
    tokens = [(m.start(), m.end(), _norm(m.group())) for m in TOKEN.finditer(text)]
    sm = difflib.SequenceMatcher(None, [_norm(w) for w, _, _ in ws], [t[2] for t in tokens], autojunk=False)
    return [
        (blk.a + k, tokens[blk.b + k][0], tokens[blk.b + k][1])
        for blk in sm.get_matching_blocks()
        for k in range(blk.size)
        if tokens[blk.b + k][2]
    ]


def align(text, ws):
    """Where each timed word is in the text: [[c0, c1, t0, t1]] in order. Words the text no longer has are left out."""
    return [[c0, c1, ws[i][1], ws[i][2]] for i, c0, c1 in _placed(text, ws)]


def _dump(ws):
    return json.dumps([[w, a, b] for w, a, b in ws]) if ws else None


def _join(a, b):
    a, b = a.rstrip(), b.lstrip()
    if not a or not b:
        return a + b
    return a + b if CJK.match(a[-1]) or CJK.match(b[0]) else f"{a} {b}"


def _boundary(text, at):
    """Where a line splits for a position in its text: the start of the word it's in (in scripts with spaces)."""
    at = max(0, min(int(at), len(text)))
    if not CJK.search(text):
        while 0 < at < len(text) and not text[at - 1].isspace() and not text[at].isspace():
            at -= 1
    return at


def _split_time(seg, text, at, ws):
    """When the second part starts: its first timed word, else after the first part's last one, else as far into the
    line's time as `at` is into its text. Always inside the line."""
    placed = _placed(text, ws)
    after = [ws[i][1] for i, c0, _ in placed if c0 >= at]
    before = [ws[i][2] for i, _, c1 in placed if c1 <= at]
    if after:
        t = after[0]
    elif before:
        t = before[-1]
    else:
        t = seg["t0"] + (seg["t1"] - seg["t0"]) * at / max(1, len(text))
    return int(min(max(t, seg["t0"] + 1), seg["t1"] - 1))


def _segments(db, rid):
    return db.rows("SELECT * FROM segment WHERE recording = $r ORDER BY idx", r=rid)


def _row(seg, rid, idx):
    """A line's content at position `idx`: every field it has, renumbered."""
    d = {k: v for k, v in seg.items() if k != "id"}
    d.update(id=R("segment", rid * SEG + idx), idx=idx, dur=d["t1"] - d["t0"])
    return store.clean(d)


def _renumber(db, rid, segs, start, merged=None):
    """Write the lines from `start` on, and move what points at line numbers: after a split of line `start` everything
    after it moves down one; after line `merged` was merged into the one before it, it and everything after move up
    one. One transaction."""
    first = rid * SEG
    split = merged is None
    later = start + 1 if split else merged  # the first line whose number changes
    gone, moved = [], []
    over = db.rows("SELECT segment, key, recording, space, target FROM entity_override WHERE recording = $r", r=rid)
    had = {(o["segment"] - first, o["key"]) for o in over}
    for o in over:
        idx = o["segment"] - first
        if split and idx == start:  # the line's corrections hold for both its parts
            moved.append({**o, "segment": first + idx + 1})
        elif idx >= later:
            gone.append(R("entity_override", f"{o['segment']}:{o['key']}"))
            if split or idx != merged or (start, o["key"]) not in had:  # merged: the first line's correction wins
                moved.append({**o, "segment": first + (idx + 1 if split else idx - 1)})
    moved = [{**o, "id": R("entity_override", f"{o['segment']}:{o['key']}")} for o in moved]
    sign = "+" if split else "-"
    db.run(
        [
            "DELETE mentions WHERE recording = $rid",
            "FOR $s IN $rows { UPSERT $s.id CONTENT $s; }",
            "DELETE segment WHERE recording = $rid AND idx >= $keep",
            f"UPDATE segment_edit SET idx = idx {sign} 1 WHERE recording = $rid AND idx >= $later",
            f"UPDATE section SET seg0 = seg0 {sign} 1 WHERE recording = $rid AND seg0 >= $later",
            f"UPDATE section SET seg1 = seg1 {sign} 1 WHERE recording = $rid AND seg1 >= $later",
            "FOR $o IN $gone { DELETE $o; }",
            "FOR $o IN $moved { UPSERT $o.id CONTENT $o; }",
        ],
        rid=rid,
        rows=[_row(s, rid, start + k) for k, s in enumerate(segs[start:])],
        keep=len(segs),
        later=later,
        gone=gone,
        moved=moved,
    )


def split(db, rid, idx, at, t=None, speaker=None, set_speaker=False):
    """Split line `idx` in two where `at` (a position in its text) falls, at the start of the word it's in. The second
    part starts at `t` (ms) when given, else where its first word was said (or as far into the line as it is into the
    text). Both parts keep the line's speaker unless `set_speaker` gives the second one `speaker`. Returns what the
    edit history keeps. KeyError: no such line; ValueError: a part would be empty, or `t` isn't inside the line."""
    segs = _segments(db, rid)
    if not 0 <= idx < len(segs):
        raise KeyError(idx)
    seg = segs[idx]
    text = seg["text"]
    cut = _boundary(text, at)
    head, tail = text[:cut].rstrip(), text[cut:].lstrip()
    if not head or not tail:
        raise ValueError("Split between two words, not at the start or the end of the line.")
    if seg["t1"] - seg["t0"] < 2:
        raise ValueError("This line is too short to split.")
    ws = words(seg)
    if t is not None and not seg["t0"] < t < seg["t1"]:
        raise ValueError(f"The second part has to start inside the line ({store.tc(seg['t0'])}–{store.tc(seg['t1'])}).")
    ts = int(t) if t is not None else _split_time(seg, text, cut, ws)
    pos = {i: c0 for i, c0, _ in _placed(text, ws)}
    first = [(pos[i] < cut) if i in pos else (w[1] < ts) for i, w in enumerate(ws)]  # unplaced words go by time
    a = {**seg, "text": head, "t1": ts, "words": _dump([w for w, f in zip(ws, first) if f]), "raw_text": None}
    b = {**seg, "text": tail, "t0": ts, "words": _dump([w for w, f in zip(ws, first) if not f]), "raw_text": None}
    if set_speaker:
        b["speaker"] = speaker
    _renumber(db, rid, segs[:idx] + [a, b] + segs[idx + 1 :], idx)
    after = {"at": cut, "t": ts, **({"speaker": speaker} if set_speaker else {})}
    return {"before": {"text": text, "speaker": seg.get("speaker")}, "after": after}


def merge(db, rid, idx):
    """Merge line `idx` with the next one: one line with both texts and their words, from the first's start to the
    second's end, keeping the first's speaker. Returns what the edit history keeps, with where the second line's text
    starts (`at`) and when it started (`t`), to split it there again. KeyError: no such line, or it's the last."""
    segs = _segments(db, rid)
    if not 0 <= idx < len(segs) - 1:
        raise KeyError(idx)
    a, b = segs[idx], segs[idx + 1]
    text = _join(a["text"], b["text"])
    raw = _join(a["raw_text"], b["raw_text"]) if a.get("raw_text") and b.get("raw_text") else None
    m = {**a, "text": text, "t1": max(a["t1"], b["t1"]), "words": _dump(words(a) + words(b)), "raw_text": raw}
    _renumber(db, rid, segs[:idx] + [m] + segs[idx + 2 :], idx, merged=idx + 1)
    return {
        "before": {"text": a["text"], "next": b["text"], "speaker": a.get("speaker"), "next_speaker": b.get("speaker")},
        "after": {"at": len(text) - len(b["text"].lstrip()), "t": b["t0"]},
    }
