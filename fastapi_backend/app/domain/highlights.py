"""Highlights on a resource (docs/api.md#highlights): passages its editors mark in colour, with a label, for everyone
who can read the resource.

A highlight is a passage: a time range in ms (a document's pages have a reading-pace clock), with the words quoted
when they were picked in the text; its colour is one of a few the web app has tokens for. Any editor of the resource
changes or deletes one. Highlights move with their resource and go when it is deleted.
"""

from __future__ import annotations

from . import notes, store

R = store.R
COLOURS = ("yellow", "green", "blue", "red")
MAX_HIGHLIGHTS = 1000  # on one resource
LABEL_MAX = 200
FIELDS = "record::id(id) AS id, recording, space, account, t0, t1, quote, colour, label, created_at, updated_at"


def _colour(colour):
    colour = str(colour or "").strip().lower()
    if colour not in COLOURS:
        raise ValueError(f"A highlight's colour is one of {', '.join(COLOURS)}.")
    return colour


def _label(label):
    label = " ".join(str(label or "").split())
    if len(label) > LABEL_MAX:
        raise ValueError(f"A highlight's label can have up to {LABEL_MAX} characters.")
    return label or None


def get(db, hid):
    """The highlight, or KeyError."""
    h = db.one(f"SELECT {FIELDS} FROM $r", r=R("highlight", int(hid)))
    if not h:
        raise KeyError(hid)
    return h


def on(db, rid):
    """The resource's highlights, by passage; the earliest made first among those at one moment."""
    rows = db.rows(f"SELECT {FIELDS} FROM highlight WHERE recording = $r", r=int(rid))
    return sorted(rows, key=lambda h: (h.get("t0") or 0, h.get("t1") or 0, h.get("created_at") or "", h["id"]))


def create(db, rid, space, account, t0, t1=None, quote=None, colour="yellow", label=None, duration_ms=None):
    """A new highlight; its id. ValueError for no passage, one past the end of the resource, a colour there's no
    token for, or too many highlights."""
    if t0 is None:
        raise ValueError("A highlight needs the passage it marks (t0 and t1).")
    t0, t1 = notes._moment(t0, t1, duration_ms)
    colour, label = _colour(colour), _label(label)
    if len(db.values("SELECT VALUE id FROM highlight WHERE recording = $r", r=int(rid))) >= MAX_HIGHLIGHTS:
        raise ValueError(f"This resource has {MAX_HIGHLIGHTS} highlights already; delete one first.")
    hid = db.next_id("highlight")
    t = store.now()
    row = {
        "recording": int(rid),
        "space": space,
        "account": account,
        "t0": t0,
        "t1": t1,
        "quote": " ".join(str(quote or "").split())[: notes.QUOTE_MAX] or None,
        "colour": colour,
        "label": label,
        "created_at": t,
        "updated_at": t,
    }
    db.q("CREATE $r CONTENT $d", r=R("highlight", hid), d=store.clean(row))
    return hid


def update(db, hid, colour=None, label=None):
    """Change its colour, or its label (an empty one clears it)."""
    sets, p = ["updated_at = $t"], {"t": store.now()}
    if colour is not None:
        sets.append("colour = $c")
        p["c"] = _colour(colour)
    if label is not None:
        sets.append("label = $l")
        p["l"] = _label(label)
    db.q(f"UPDATE $r SET {', '.join(sets)}", r=R("highlight", int(hid)), **p)


def delete(db, hid):
    db.q("DELETE $r", r=R("highlight", int(hid)))
