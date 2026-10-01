"""Notes on a recording: about a moment (a time range in ms, with the words quoted when they were picked in the
transcript) or about the whole recording.

A note is its writer's, and only they see it until it is shared; then everyone who can read the recording sees it.
Only its writer changes a note; its writer, or for a shared note an owner of the recording's namespace, deletes it.
Notes move with their recording and go when it is deleted.
"""

from __future__ import annotations

from . import store

R = store.R
MAX_NOTES = 500  # per person, on one recording
TEXT_MAX = 5000
QUOTE_MAX = 1000
FIELDS = (
    "record::id(id) AS id, recording, space, account, text, t0, t1, quote, shared ?? false AS shared, created_at, updated_at, edited_at"
)


def _text(text):
    text = str(text or "").strip()
    if not text:
        raise ValueError("A note needs some text.")
    if len(text) > TEXT_MAX:
        raise ValueError(f"A note can have up to {TEXT_MAX} characters.")
    return text


def _moment(t0, t1, duration_ms=None):
    """The moment a note is about: (None, None) for the whole recording, else (t0, t1) in ms; t1 is t0 when not given,
    and stops at the end of the recording when its length is known."""
    if t0 is None:
        if t1 is not None:
            raise ValueError("A note's moment needs its start (t0).")
        return None, None
    t0, t1 = int(t0), int(t0 if t1 is None else t1)
    if t0 < 0 or t1 < t0:
        raise ValueError("A note's moment has to end after it starts.")
    if duration_ms and t0 > duration_ms:
        raise ValueError("That moment is past the end of the recording.")
    return t0, min(t1, int(duration_ms)) if duration_ms else t1


def get(db, nid):
    """The note, or KeyError."""
    n = db.one(f"SELECT {FIELDS} FROM $r", r=R("note", int(nid)))
    if not n:
        raise KeyError(nid)
    return n


def visible(db, rid, account):
    """This person's notes on the recording and the ones shared on it: notes about the whole recording first, then by
    moment; the earliest written first."""
    rows = db.rows(f"SELECT {FIELDS} FROM note WHERE recording = $r AND (account = $a OR shared = true)", r=int(rid), a=account)
    return sorted(rows, key=lambda n: (n.get("t0") is not None, n.get("t0") or 0, n.get("created_at") or "", n["id"]))


def create(db, rid, space, account, text, t0=None, t1=None, quote=None, shared=False, duration_ms=None):
    """A new note; its id. ValueError for no text, a moment that doesn't fit the recording, or too many notes."""
    text = _text(text)
    t0, t1 = _moment(t0, t1, duration_ms)
    if len(db.values("SELECT VALUE id FROM note WHERE recording = $r AND account = $a", r=int(rid), a=account)) >= MAX_NOTES:
        raise ValueError(f"You have {MAX_NOTES} notes on this recording already; delete one first.")
    quote = " ".join(str(quote or "").split())[:QUOTE_MAX] or None
    nid = db.next_id("note")
    t = store.now()
    note = {
        "recording": int(rid),
        "space": space,
        "account": account,
        "text": text,
        "t0": t0,
        "t1": t1,
        "quote": quote,
        "shared": bool(shared),
        "created_at": t,
        "updated_at": t,
    }
    db.q("CREATE $r CONTENT $d", r=R("note", nid), d=store.clean(note))
    return nid


def update(db, nid, text=None, shared=None):
    """Change its text (kept as `edited_at`), or share or unshare it. ValueError for no text."""
    t = store.now()
    sets, p = ["updated_at = $t"], {"t": t}
    if text is not None:
        sets += ["text = $x", "edited_at = $t"]
        p["x"] = _text(text)
    if shared is not None:
        sets.append("shared = $sh")
        p["sh"] = bool(shared)
    db.q(f"UPDATE $r SET {', '.join(sets)}", r=R("note", int(nid)), **p)


def delete(db, nid):
    db.q("DELETE $r", r=R("note", int(nid)))
