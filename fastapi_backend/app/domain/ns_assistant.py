"""Each namespace's own assistant: a name, standing instructions, and a memory that lasts from one conversation to the
next (docs/assistant.md, "A namespace's own assistant").

It is the same chat assistant (ai_tools.Toolbox, its extensions and approvals), not a second one. It is off until an
owner of the namespace turns it on; then every conversation scoped to that namespace alone (picked, or chosen from the
first question) talks to it. It is told its name and instructions and reads what it remembers with every question.

A memory is one short fact with where it came from: a moment in a recording (the [n] of an excerpt it was shown, kept
as the recording and time, so it is cited like any other excerpt), or the conversation it was told in. Remembering is
routine, so the assistant does it at once and says so in its steps; anyone who can edit the namespace can read, change,
pin and forget memories (/namespaces/{name}/assistant/memories). Forgetting from chat waits for the person's approval.
Viewers' conversations read the memory but don't add to it.

Light by design: no model calls of its own, no index. The newest memories (pinned ones first) go into the prompt, up to
PROMPT_MAX of them; `recall` finds older ones by their words.

Refine later: a decision model choosing what is worth remembering, memories merged when they say the same thing, the
assistant's own extensions per namespace, its memory as a page in Notes.
"""

from __future__ import annotations

import re

from . import store

R = store.R
NAME_MAX = 60
INSTRUCTIONS_MAX = 4000
TEXT_MAX = 500
MEMORY_MAX = 2000  # per namespace
PROMPT_MAX = 30  # memories read with every question
DEFAULT_NAME = "Assistant"
FIELDS = "record::id(id) AS id, space, text, recording, t0, chat, author, account, pinned ?? false AS pinned, created_at, updated_at"


def profile(db, sid):
    """The namespace's assistant as stored, with defaults: off, called Assistant, no instructions."""
    row = db.one("SELECT assistant FROM $r", r=R("space", int(sid)))
    if row is None:
        raise KeyError(sid)
    a = row.get("assistant") or {}
    return {
        "enabled": bool(a.get("enabled")),
        "name": a.get("name") or DEFAULT_NAME,
        "instructions": a.get("instructions") or "",
        "updated_at": a.get("updated_at"),
        "updated_by": a.get("updated_by"),
    }


def save_profile(db, sid, enabled=None, name=None, instructions=None, user=None):
    """Change what is given; the rest stays."""
    a = profile(db, sid)
    if name is not None:
        name = " ".join(str(name).split())
        if len(name) > NAME_MAX:
            raise ValueError(f"The assistant's name can have up to {NAME_MAX} characters.")
        a["name"] = name or DEFAULT_NAME
    if instructions is not None:
        instructions = str(instructions).strip()
        if len(instructions) > INSTRUCTIONS_MAX:
            raise ValueError(f"The instructions can have up to {INSTRUCTIONS_MAX} characters.")
        a["instructions"] = instructions
    if enabled is not None:
        a["enabled"] = bool(enabled)
    a.update(updated_at=store.now(), updated_by=user)
    db.q("UPDATE $r SET assistant = $a", r=R("space", int(sid)), a=store.clean(a))
    return profile(db, sid)


def for_scope(db, scope):
    """(namespace id, name, profile) when a conversation's scope is one namespace whose assistant is on; else None."""
    names = (scope or {}).get("namespaces") or []
    if len(names) != 1:
        return None
    sid = {v: k for k, v in store.space_names(db).items()}.get(names[0])
    if sid is None:
        return None
    p = profile(db, sid)
    return (sid, names[0], p) if p["enabled"] else None


# ---------- memory ----------
def _text(text):
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("A memory needs some text.")
    if len(text) > TEXT_MAX:
        raise ValueError(f"A memory is one short fact of up to {TEXT_MAX} characters.")
    return text


def get(db, sid, mid):
    row = db.one(f"SELECT {FIELDS} FROM $r", r=R("assistant_memory", int(mid)))
    if not row or row["space"] != int(sid):
        raise KeyError(mid)
    return row


def remember(db, sid, text, account, recording=None, t0=None, chat=None, author="assistant"):
    """Keep a fact. One the namespace already remembers (same words) isn't kept twice: its id comes back."""
    text = _text(text)
    same = db.one("SELECT VALUE record::id(id) FROM assistant_memory WHERE space = $s AND text = $t LIMIT 1", s=int(sid), t=text)
    if same:
        return same
    if count(db, sid) >= MEMORY_MAX:
        raise ValueError(f"This namespace's assistant remembers {MEMORY_MAX} things already: forget some first.")
    mid = db.next_id("assistant_memory")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("assistant_memory", mid),
        d=store.clean(
            {
                "space": int(sid),
                "text": text,
                "recording": int(recording) if recording is not None else None,
                "t0": t0,
                "chat": chat,
                "author": author,
                "account": account,
                "pinned": False,
                "created_at": store.now(),
            }
        ),
    )
    return mid


def count(db, sid):
    return len(db.values("SELECT VALUE id FROM assistant_memory WHERE space = $s", s=int(sid)))


def update(db, sid, mid, text=None, pinned=None):
    get(db, sid, mid)
    sets = {"updated_at": store.now()}
    if text is not None:
        sets["text"] = _text(text)
    if pinned is not None:
        sets["pinned"] = bool(pinned)
    db.q("UPDATE $r MERGE $d", r=R("assistant_memory", int(mid)), d=sets)
    return get(db, sid, mid)


def forget(db, sid, mid):
    get(db, sid, mid)
    db.q("DELETE $r", r=R("assistant_memory", int(mid)))


def forget_all(db, sid):
    db.q("DELETE assistant_memory WHERE space = $s", s=int(sid))


def memories(db, sid, limit=None):
    """Pinned first, then newest first."""
    rows = db.rows(f"SELECT {FIELDS} FROM assistant_memory WHERE space = $s", s=int(sid))
    rows.sort(key=lambda m: (m.get("created_at") or "", m["id"]), reverse=True)
    rows.sort(key=lambda m: not m["pinned"])
    return rows[:limit] if limit else rows


def _words(text):
    return {w for w in re.findall(r"[\w'-]+", (text or "").lower()) if len(w) >= 3}


def recall(db, sid, query, limit=10):
    """Memories sharing the most words with the query (a word's start counts, so "ship" finds "shipping")."""
    want = _words(query)
    if not want:
        return memories(db, sid, limit)
    scored = []
    for m in memories(db, sid):
        have = _words(m["text"])
        score = sum(1 for w in want if any(h.startswith(w) or w.startswith(h) for h in have))
        if score:
            scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:limit]]


def labelled(db, rows):
    """Memories as people see them: each with its recording's title and time, when it came from one."""
    ids = sorted({m["recording"] for m in rows if m.get("recording") is not None})
    titles = (
        {
            r["id"]: r.get("title")
            for r in db.rows("SELECT record::id(id) AS id, title FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in ids])
        }
        if ids
        else {}
    )
    return [
        {**m, "title": titles.get(m.get("recording")), "time": store.tc(m["t0"]) if m.get("t0") is not None else None}
        if m.get("recording") is not None
        else m
        for m in rows
    ]


def prompt(namespace, p, lines):
    """What the model is told about who it is here, with what it remembers (lines already numbered by the caller)."""
    out = (
        f"\n\nYou are {p['name']}, the assistant of the {namespace} namespace. People come back to you across conversations, "
        "so you keep a memory: short facts with where they came from. When you learn something lasting about this "
        "namespace (a decision, a preference, a fact or plan people will ask about again), keep it with remember, "
        "giving source_ref when it came from an excerpt; don't remember what the archive already says plainly. Use what you "
        "remember like the excerpts: cite [n] when it has a number, else say who told you and when. recall finds older "
        "memories. Anything that changes data or settings still waits for the person's approval."
    )
    if p.get("instructions"):
        out += f"\n\nInstructions for this namespace's assistant, from its owners:\n{p['instructions']}"
    out += "\n\nWhat you remember:\n" + ("\n".join(lines) if lines else "(nothing yet)")
    return out
