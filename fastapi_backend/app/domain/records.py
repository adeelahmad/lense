"""Structured files: JSON and JSON Lines that aren't transcripts or chats, read as records, one segment per record.

A JSON Lines file is a record per line; a JSON file is its list (a top-level list, or the one list of objects an
object holds, such as {"orders": [...]}), else the object itself. A record's text is its fields, one `name: value`
per line, nested fields named by their path (`address.city: Paris`), so search and the assistant read every value.
Each segment says which record it is (`record`, counted from 0) and, for JSON Lines, where its line starts in the file
(`offset`, in bytes), so an answer can point back at the exact record. Times are a reading pace, as for documents. The
resource holds records (recording.form), which is how the Records content type knows it.
"""

from __future__ import annotations

import json

WORD_MS = 385  # the reading pace segments are given (as for documents)
MAX_RECORDS = 50_000  # past this the rest of the file isn't read
MAX_CHARS = 4000  # a record's text is cut here
MAX_DEPTH = 8


def _lines(value, path, out, depth=0):
    """`path: value` lines for every scalar in value."""
    if len(out) > 400:
        return
    if isinstance(value, dict) and depth < MAX_DEPTH:
        for k, v in value.items():
            _lines(v, f"{path}.{k}" if path else str(k), out, depth + 1)
    elif isinstance(value, list) and depth < MAX_DEPTH:
        if all(not isinstance(v, (dict, list)) for v in value):
            if value:
                out.append(f"{path}: {', '.join(_scalar(v) for v in value)}" if path else ", ".join(_scalar(v) for v in value))
        else:
            for i, v in enumerate(value):
                _lines(v, f"{path}[{i}]", out, depth + 1)
    elif value is not None and value != "":
        s = _scalar(value) if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False)
        out.append(f"{path}: {s}" if path else s)


def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v).strip()


def text_of(record):
    """A record as text: its fields, one `name: value` per line."""
    out: list[str] = []
    _lines(record, "", out)
    return "\n".join(out)[:MAX_CHARS].strip()


def _items(j):
    """(records, the key they were under) of a parsed JSON file."""
    if isinstance(j, list):
        return j, None
    if isinstance(j, dict):
        lists = [(k, v) for k, v in j.items() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
        if len(lists) == 1:
            return lists[0][1], lists[0][0]
        return [j], None
    return [j], None


def read(raw, lines=False):
    """The records in `raw` (a JSON or JSON Lines file's text): {segments, speakers, title, timed, form, notes}, or
    None when it holds nothing to read. ValueError when it isn't JSON."""
    raw = (raw or "").lstrip("﻿")
    found = []  # (record, its offset in bytes or None)
    if lines:
        at = 0
        for n, line in enumerate(raw.splitlines(keepends=True), 1):
            if line.strip():
                try:
                    found.append((json.loads(line), at))
                except ValueError as e:
                    raise ValueError(f"line {n} isn't JSON ({e})") from None
            at += len(line.encode("utf-8"))
    else:
        items, _ = _items(json.loads(raw))
        found = [(x, None) for x in items]
    notes = []
    if len(found) > MAX_RECORDS:
        notes.append(f"only its first {MAX_RECORDS} records were read")
        found = found[:MAX_RECORDS]
    segs, t = [], 0
    for i, (rec, offset) in enumerate(found):
        text = text_of(rec)
        if not text:
            continue
        dur = WORD_MS * max(1, len(text.split()))
        segs.append({"t0": t, "t1": t + dur, "text": text, "record": i, "offset": offset})
        t += dur
    if not segs:
        return None
    return {"segments": segs, "speakers": {}, "title": None, "timed": False, "form": "records", "notes": notes}
