"""Comments on a resource (docs/api.md#comments): a conversation everyone who can read the resource reads and joins.

A comment is about a moment or a passage (a time range in ms, with the words quoted when they were picked in the text)
or about the whole resource, and starts a thread; a reply goes on the thread (a reply to a reply too). A thread can be
resolved, by its writer or an editor, and reopened. Only its writer changes a comment's text; its writer, or an owner
of the resource, deletes it, and a thread goes with its replies. Comments move with their resource and go when it is
deleted.
"""

from __future__ import annotations

from . import notes, store

R = store.R
MAX_COMMENTS = 1000  # per person, on one resource
TEXT_MAX = 5000
FIELDS = (
    "record::id(id) AS id, recording, space, account, parent, text, t0, t1, quote, resolved ?? false AS resolved, "
    "resolved_by, resolved_at, created_at, updated_at, edited_at, flag"
)


def _text(text):
    text = str(text or "").strip()
    if not text:
        raise ValueError("A comment needs some text.")
    if len(text) > TEXT_MAX:
        raise ValueError(f"A comment can have up to {TEXT_MAX} characters.")
    return text


def get(db, cid):
    """The comment, or KeyError."""
    c = db.one(f"SELECT {FIELDS} FROM $r", r=R("comment", int(cid)))
    if not c:
        raise KeyError(cid)
    return c


def _order(rows):
    """Threads about the whole resource first, then by moment, the earliest started first; each thread's replies
    after it, the earliest first."""
    roots = sorted(
        (c for c in rows if c.get("parent") is None),
        key=lambda c: (c.get("t0") is not None, c.get("t0") or 0, c.get("created_at") or "", c["id"]),
    )
    replies = {}
    for c in rows:
        if c.get("parent") is not None:
            replies.setdefault(c["parent"], []).append(c)
    out = []
    for root in roots:
        out.append(root)
        out.extend(sorted(replies.get(root["id"], []), key=lambda c: (c.get("created_at") or "", c["id"])))
    return out


def on(db, rid):
    """The resource's comments, threaded: each thread (whole-resource ones first, then by moment) followed by its
    replies."""
    return _order(db.rows(f"SELECT {FIELDS} FROM comment WHERE recording = $r", r=int(rid)))


def create(db, rid, space, account, text, t0=None, t1=None, quote=None, parent=None, duration_ms=None):
    """A new comment, or with `parent` a reply on that comment's thread; its id. ValueError for no text, a moment that
    doesn't fit the resource, a parent that isn't a comment on it, or too many comments."""
    text = _text(text)
    if parent is not None:
        try:
            p = get(db, parent)
        except KeyError:
            raise ValueError("The comment replied to isn't on this resource.") from None
        if p["recording"] != int(rid):
            raise ValueError("The comment replied to isn't on this resource.")
        parent = p["parent"] if p.get("parent") is not None else p["id"]  # a reply to a reply goes on the thread
        t0 = t1 = quote = None  # a reply is about what its thread is about
    else:
        t0, t1 = notes._moment(t0, t1, duration_ms)
        quote = " ".join(str(quote or "").split())[: notes.QUOTE_MAX] or None
    if len(db.values("SELECT VALUE id FROM comment WHERE recording = $r AND account = $a", r=int(rid), a=account)) >= MAX_COMMENTS:
        raise ValueError(f"You have {MAX_COMMENTS} comments on this resource already; delete one first.")
    cid = db.next_id("comment")
    t = store.now()
    row = {
        "recording": int(rid),
        "space": space,
        "account": account,
        "parent": parent,
        "text": text,
        "t0": t0,
        "t1": t1,
        "quote": quote,
        "resolved": False,
        "created_at": t,
        "updated_at": t,
    }
    db.q("CREATE $r CONTENT $d", r=R("comment", cid), d=store.clean(row))
    return cid


def update(db, cid, text):
    """Change its text (kept as `edited_at`). ValueError for no text."""
    t = store.now()
    db.q("UPDATE $r SET text = $x, edited_at = $t, updated_at = $t", r=R("comment", int(cid)), x=_text(text), t=t)


def resolve(db, cid, account, resolved=True):
    """Resolve the thread (by `account`), or reopen it. ValueError for a reply: a thread is resolved whole."""
    c = get(db, cid)
    if c.get("parent") is not None:
        raise ValueError("A reply can't be resolved on its own: resolve its thread.")
    t = store.now()
    if resolved:
        db.q("UPDATE $r SET resolved = true, resolved_by = $a, resolved_at = $t, updated_at = $t", r=R("comment", int(cid)), a=account, t=t)
    else:
        db.q("UPDATE $r SET resolved = false, resolved_by = NONE, resolved_at = NONE, updated_at = $t", r=R("comment", int(cid)), t=t)


def delete(db, cid):
    """Delete it, and for a thread its replies; how many replies went."""
    gone = db.values("SELECT VALUE id FROM comment WHERE parent = $p", p=int(cid))
    db.run(["DELETE comment WHERE parent = $p", "DELETE $r"], p=int(cid), r=R("comment", int(cid)))
    return len(gone)
