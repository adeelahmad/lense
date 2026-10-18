"""Comments on a resource (docs/api.md#comments): a conversation everyone who can read the resource reads and joins.

A comment is about a moment or a passage (a time range in ms, with the words quoted when they were picked in the text)
or about the whole resource, and starts a thread; a reply goes on the thread (a reply to a reply too). A thread can be
resolved, by its writer or an editor, and reopened. Only its writer changes a comment's text; its writer, or an owner
of the resource, deletes it, and a thread goes with its replies. Comments move with their resource and go when it is
deleted.

A comment can be on an activity of the timeline instead (docs/timeline.md, Reactions and comments): `activity` holds
the activity's id and `about` its summary at the time, and `recording` is the asset the activity brought in when it is
one's arrival (`create-recording-<id>`), so the same thread shows on the asset's page and on the timeline; otherwise
`recording` is null. `audience` and `owner` are the activity's, so the comment's own activity (comment.added) is seen
by the same people. A comment that tags @lens gets the agent's answer on the thread, a comment with `author`
assistant (comment_agent.py); the thread's first comment keeps that conversation's id in `chat`.
"""

from __future__ import annotations

import re

from . import notes, store

R = store.R
MAX_COMMENTS = 1000  # per person, on one resource
TEXT_MAX = 5000
ABOUT_MAX = 300
FIELDS = (
    "record::id(id) AS id, recording, activity, about, space, account, author, chat, parent, text, t0, t1, quote, "
    "resolved ?? false AS resolved, resolved_by, resolved_at, created_at, updated_at, edited_at"
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


def recording_of(activity_id):
    """The asset an activity is the arrival of (`create-recording-<id>`), or None: comments on that activity are the
    asset's own."""
    m = re.match(r"^create-recording-(\d+)$", activity_id or "")
    return int(m.group(1)) if m else None


def on_activity(db, activity_id):
    """An activity's comments, threaded, the earliest thread first: those made on it, and for an asset's arrival the
    asset's own comments too."""
    rid = recording_of(activity_id)
    if rid is not None:
        return on(db, rid)
    return _order(db.rows(f"SELECT {FIELDS} FROM comment WHERE activity = $a", a=activity_id))


def counts(db, ids):
    """{activity id: how many comments it has}, for a page of activities in two queries; one with none isn't in it."""
    ids = [i for i in dict.fromkeys(ids) if isinstance(i, str)]
    by_rec = {i: recording_of(i) for i in ids if recording_of(i) is not None}
    out = {}
    if by_rec:
        rows = db.rows(
            "SELECT recording, count() AS n FROM comment WHERE recording IN $r GROUP BY recording", r=sorted(set(by_rec.values()))
        )
        n = {r["recording"]: r["n"] for r in rows}
        out |= {i: n[rid] for i, rid in by_rec.items() if n.get(rid)}
    rest = [i for i in ids if i not in by_rec]
    if rest:
        rows = db.rows("SELECT activity, count() AS n FROM comment WHERE activity IN $a GROUP BY activity", a=rest)
        out |= {r["activity"]: r["n"] for r in rows if r.get("n")}
    return out


def _parent(db, parent, rid, activity):
    """The thread a reply goes on: the parent's thread, when the parent is on the same resource or activity."""
    try:
        p = get(db, parent)
    except KeyError:
        raise ValueError("The comment replied to isn't on this resource.") from None
    same = p["recording"] == rid if rid is not None else p.get("activity") == activity
    if not same:
        raise ValueError("The comment replied to isn't on this resource.")
    return p["parent"] if p.get("parent") is not None else p["id"]  # a reply to a reply goes on the thread


def create(
    db,
    rid,
    space,
    account,
    text,
    t0=None,
    t1=None,
    quote=None,
    parent=None,
    duration_ms=None,
    activity=None,
    about=None,
    audience=None,
    owner=None,
    author=None,
):
    """A new comment, or with `parent` a reply on that comment's thread; its id. On a resource (`rid`), or on an
    activity of the timeline (`activity`, with `about`, its summary; `rid` then is the asset it is the arrival of, or
    None). `author` assistant: the agent wrote it, answering `account` (comment_agent.py). ValueError for no text, a
    moment that doesn't fit the resource, a parent that isn't a comment on it, or too many comments."""
    text = _text(text)
    rid = int(rid) if rid is not None else None
    if rid is None and not activity:
        raise ValueError("A comment is on a resource or on an activity.")
    if parent is not None:
        parent = _parent(db, parent, rid, activity)
        t0 = t1 = quote = None  # a reply is about what its thread is about
    elif rid is not None and activity is None:
        t0, t1 = notes._moment(t0, t1, duration_ms)
        quote = " ".join(str(quote or "").split())[: notes.QUOTE_MAX] or None
    else:
        t0 = t1 = quote = None  # a comment on an activity is about the whole of it
    if rid is not None:
        mine = db.values("SELECT VALUE id FROM comment WHERE recording = $r AND account = $a", r=rid, a=account)
    else:
        mine = db.values("SELECT VALUE id FROM comment WHERE activity = $x AND account = $a", x=activity, a=account)
    if len(mine) >= MAX_COMMENTS:
        raise ValueError(f"You have {MAX_COMMENTS} comments on this resource already; delete one first.")
    cid = db.next_id("comment")
    t = store.now()
    row = {
        "recording": rid,
        "activity": activity,
        "about": " ".join(str(about or "").split())[:ABOUT_MAX] or None,
        "space": space,
        "account": account,
        "author": "assistant" if author == "assistant" else None,
        "parent": parent,
        "text": text,
        "t0": t0,
        "t1": t1,
        "quote": quote,
        "resolved": False,
        "audience": audience if audience in ("owner", "admins") else None,
        "owner": owner if audience == "owner" else None,
        "created_at": t,
        "updated_at": t,
    }
    db.q("CREATE $r CONTENT $d", r=R("comment", cid), d=store.clean(row))
    return cid


def set_chat(db, cid, chat_id):
    """Keep the conversation the agent answers this thread in, on its first comment."""
    db.q("UPDATE $r SET chat = $c", r=R("comment", int(cid)), c=int(chat_id))


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
