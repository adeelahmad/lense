"""Playlists (docs/api.md#playlists): clips, moments of resources kept under a title, gathered in order to play back to
back.

A clip is an item of a playlist: a resource and a time range of it in ms (a document's pages have a reading-pace
clock), with a title and a note. It may come from a highlight, whose passage and label it copies (and keeps the
highlight's id); it stays when the highlight goes. Clips are not highlights: anyone who can read a resource keeps a
clip of it, and a clip is its maker's, where a highlight is made by an editor for everyone.

A playlist is its maker's, private until shared; a shared one is read by everyone signed in, and each reader gets
only the clips of resources they can read, whoever made it. Only its maker changes it. Clips go when their resource
is deleted; the playlist stays.
"""

from __future__ import annotations

from . import notes, store

R = store.R
MAX_PLAYLISTS = 200  # per person
MAX_ITEMS = 500  # in one playlist
NAME_MAX = 120
DESCRIPTION_MAX = 2000
TITLE_MAX = 200
NOTE_MAX = 2000
DEFAULT_NAME = "Clips"  # the playlist a clip goes in when you have none yet
DEFAULT_LENGTH_MS = 30_000  # a clip of a moment (no end given) lasts this long, or to the end of the resource
FIELDS = "record::id(id) AS id, account, name, description, shared ?? false AS shared, used ?? 0 AS used, created_at, updated_at"
ITEM_FIELDS = "record::id(id) AS id, playlist, account, recording, t0, t1, title, note, quote, highlight, position, created_at, updated_at"


def _text(value, most, what):
    value = " ".join(str(value or "").split())
    if len(value) > most:
        raise ValueError(f"A {what} can have up to {most} characters.")
    return value or None


def _name(name):
    name = _text(name, NAME_MAX, "playlist's name")
    if not name:
        raise ValueError("Give the playlist a name.")
    return name


def _note(note):
    note = str(note or "").strip()
    if len(note) > NOTE_MAX:
        raise ValueError(f"A clip's note can have up to {NOTE_MAX} characters.")
    return note or None


def span(t0, t1=None, duration_ms=None):
    """A clip's time range (t0, t1) in ms: t1 is DEFAULT_LENGTH_MS after t0 when not given or not after it, and stops
    at the end of the resource when its length is known. ValueError for one that ends before it starts or starts past
    the end."""
    if t0 is None:
        raise ValueError("A clip needs the moment it starts (t0).")
    t0 = int(t0)
    if t1 is not None and int(t1) < t0:
        raise ValueError("A clip has to end after it starts.")
    if t0 < 0:
        raise ValueError("A clip starts at 0 or later.")
    if duration_ms and t0 > duration_ms:
        raise ValueError("That moment is past the end of the resource.")
    t1 = int(t1) if t1 is not None and int(t1) > t0 else t0 + DEFAULT_LENGTH_MS
    return t0, min(t1, int(duration_ms)) if duration_ms else t1


# ---------- playlists ----------
def get(db, pid):
    """The playlist, or KeyError."""
    p = db.one(f"SELECT {FIELDS} FROM $r", r=R("playlist", int(pid)))
    if not p:
        raise KeyError(pid)
    return p


def visible(db, account):
    """Your playlists, the latest used first (the first is the one a clip goes in by default), then the ones others
    shared, the latest used first."""
    rows = db.rows(f"SELECT {FIELDS} FROM playlist WHERE account = $a OR shared = true", a=account)
    rows.sort(key=lambda p: (p["used"], p["id"]), reverse=True)
    return sorted(rows, key=lambda p: p["account"] != account)


def last_used(db, account):
    """Your latest used playlist, or None."""
    mine = [p for p in visible(db, account) if p["account"] == account]
    return mine[0] if mine else None


def create(db, account, name, description=None, shared=False):
    """A new playlist; its id. ValueError for no name, a name you use already, or too many playlists."""
    name = _name(name)
    mine = db.rows("SELECT name FROM playlist WHERE account = $a", a=account)
    if len(mine) >= MAX_PLAYLISTS:
        raise ValueError(f"You have {MAX_PLAYLISTS} playlists already; delete one first.")
    if any(p["name"].lower() == name.lower() for p in mine):
        raise ValueError(f"You have a playlist called {name} already.")
    pid = db.next_id("playlist")
    t = store.now()
    row = {
        "account": account,
        "name": name,
        "description": _text(description, DESCRIPTION_MAX, "playlist's description"),
        "shared": bool(shared),
        "used": db.next_id("playlist_used"),
        "created_at": t,
        "updated_at": t,
    }
    db.q("CREATE $r CONTENT $d", r=R("playlist", pid), d=store.clean(row))
    return pid


def default_for(db, account):
    """The playlist a clip goes in when none is named: your latest used one, or a new one called Clips."""
    p = last_used(db, account)
    return p["id"] if p else create(db, account, DEFAULT_NAME)


def update(db, pid, name=None, description=None, shared=None):
    """Rename it, change its description (an empty one clears it), or share or unshare it."""
    p = get(db, pid)
    sets, v = ["updated_at = $t"], {"t": store.now()}
    if name is not None:
        name = _name(name)
        others = db.values("SELECT VALUE name FROM playlist WHERE account = $a AND id != $r", a=p["account"], r=R("playlist", int(pid)))
        if any(n.lower() == name.lower() for n in others):
            raise ValueError(f"You have a playlist called {name} already.")
        sets.append("name = $n")
        v["n"] = name
    if description is not None:
        sets.append("description = $d")
        v["d"] = _text(description, DESCRIPTION_MAX, "playlist's description")
    if shared is not None:
        sets.append("shared = $s")
        v["s"] = bool(shared)
    db.q(f"UPDATE $r SET {', '.join(sets)}", r=R("playlist", int(pid)), **v)


def delete(db, pid):
    """The playlist and its clips."""
    db.run(["DELETE playlist_item WHERE playlist = $p", "DELETE $r"], p=int(pid), r=R("playlist", int(pid)))


def _touch(db, pid):
    """It was just used: the next clip goes in it by default (`used` counts, so two changes in a second keep their order)."""
    db.q("UPDATE $r SET updated_at = $t, used = $u", r=R("playlist", int(pid)), t=store.now(), u=db.next_id("playlist_used"))


# ---------- clips ----------
def items(db, pid):
    """The playlist's clips, in order."""
    rows = db.rows(f"SELECT {ITEM_FIELDS} FROM playlist_item WHERE playlist = $p", p=int(pid))
    return sorted(rows, key=lambda i: (i.get("position") or 0, i["id"]))


def items_of(db, pids):
    """{playlist: its clips, in order} for several playlists at once."""
    out: dict[int, list] = {int(p): [] for p in pids}
    if not out:
        return out
    for i in sorted(
        db.rows(f"SELECT {ITEM_FIELDS} FROM playlist_item WHERE playlist IN $p", p=sorted(out)),
        key=lambda i: (i.get("position") or 0, i["id"]),
    ):
        out[i["playlist"]].append(i)
    return out


def get_item(db, iid):
    """The clip, or KeyError."""
    i = db.one(f"SELECT {ITEM_FIELDS} FROM $r", r=R("playlist_item", int(iid)))
    if not i:
        raise KeyError(iid)
    return i


def _renumber(db, ordered):
    """Positions 0, 1, 2… in this order, writing only the ones that change."""
    for n, i in enumerate(ordered):
        if i.get("position") != n:
            db.q("UPDATE $r SET position = $n", r=R("playlist_item", i["id"]), n=n)


def add(db, pid, account, rid, t0, t1=None, title=None, note=None, quote=None, highlight=None, position=None, duration_ms=None):
    """Add a clip of resource `rid` from t0 to t1 (see span) at `position` (from 0; default the end); its id.
    ValueError for a moment that doesn't fit the resource, a title or note too long, or a full playlist."""
    t0, t1 = span(t0, t1, duration_ms)
    title, note = _text(title, TITLE_MAX, "clip's title"), _note(note)
    now = items(db, pid)
    if len(now) >= MAX_ITEMS:
        raise ValueError(f"This playlist has {MAX_ITEMS} clips already; remove one first.")
    at = len(now) if position is None else max(0, min(int(position), len(now)))
    iid = db.next_id("playlist_item")
    t = store.now()
    row = {
        "playlist": int(pid),
        "account": account,
        "recording": int(rid),
        "t0": t0,
        "t1": t1,
        "title": title,
        "note": note,
        "quote": " ".join(str(quote or "").split())[: notes.QUOTE_MAX] or None,
        "highlight": int(highlight) if highlight is not None else None,
        "position": at,
        "created_at": t,
        "updated_at": t,
    }
    db.q("CREATE $r CONTENT $d", r=R("playlist_item", iid), d=store.clean(row))
    _renumber(db, [*now[:at], {"id": iid, "position": at}, *now[at:]])
    _touch(db, pid)
    return iid


def update_item(db, iid, title=None, note=None, t0=None, t1=None, duration_ms=None):
    """Change its title or note (an empty one clears it), or its time range."""
    i = get_item(db, iid)
    sets, v = ["updated_at = $t"], {"t": store.now()}
    if title is not None:
        sets.append("title = $ti")
        v["ti"] = _text(title, TITLE_MAX, "clip's title")
    if note is not None:
        sets.append("note = $no")
        v["no"] = _note(note)
    if t0 is not None or t1 is not None:
        a, b = span(i["t0"] if t0 is None else t0, i["t1"] if t1 is None else t1, duration_ms)
        sets += ["t0 = $a", "t1 = $b"]
        v.update(a=a, b=b)
    db.q(f"UPDATE $r SET {', '.join(sets)}", r=R("playlist_item", int(iid)), **v)
    _touch(db, i["playlist"])


def remove_item(db, iid):
    i = get_item(db, iid)
    db.q("DELETE $r", r=R("playlist_item", int(iid)))
    _renumber(db, items(db, i["playlist"]))
    _touch(db, i["playlist"])


def reorder(db, pid, order):
    """Put the clips in this order (their ids): the ones named first, as named, then any others as they were.
    ValueError for an id that isn't one of the playlist's clips, or one named twice."""
    now = items(db, pid)
    have = {i["id"]: i for i in now}
    order = [int(x) for x in order]
    if len(set(order)) != len(order):
        raise ValueError("Name each clip once.")
    stray = [x for x in order if x not in have]
    if stray:
        raise ValueError(f"Clip {stray[0]} isn't in this playlist.")
    named = set(order)
    _renumber(db, [have[x] for x in order] + [i for i in now if i["id"] not in named])
    _touch(db, pid)


def remove_account(db, account):
    """What goes when someone is removed: their playlists that weren't shared, with the clips in them."""
    gone = db.values("SELECT VALUE record::id(id) FROM playlist WHERE account = $a AND (shared ?? false) = false", a=account)
    if gone:
        db.run(["DELETE playlist_item WHERE playlist IN $p", "DELETE playlist WHERE id IN $r"], p=gone, r=[R("playlist", p) for p in gone])
