"""Circles of trust (docs/circles.md): the people someone shares with on this Lens, and what they share with them.

A person makes circles (Immediate family, Friends, ...) of people with an account here, picked on purpose: someone they
share a namespace with, someone who has them in a circle, or anyone when they're an admin. Circles are their own;
nobody else sees them.

They, or their agent with their yes, share a timeline activity or a resource with one or more circles (or some of
their members) and a note. A share keeps a snapshot of what was shared, as it read then: its name, the line the
timeline gave it, the entities it is about and what kind of activity it was. That's what every member sees, in their own
timeline, as `share.received` (`lens.audience` owner: them alone); the sharer sees `share.sent`, so both feeds show it.
"With the item" also gives each member who can't open the resource permission on that one resource, as an owner gives
permission on an asset (it needs the publish permission there). Undo takes the share, both activities and the
permissions it gave back. Nothing from a vault leaves it, and no model is called.

The activities aren't stored twice: two sources read the share rows. `shares_sent` reads circle_share by (at, id);
`shares_received` reads circle_share_to, one row per member, by (at, id). A received share belongs to no namespace.
"""

from __future__ import annotations

import re

from . import access, auth, events, events_content, store
from .events import _s, _sample

R = store.R
SUGGESTED = ("Immediate family", "Close family", "Friends", "Colleagues")
MAX_CIRCLES = 50
MAX_MEMBERS = 200
NAME_MAX = 80
NOTE_MAX = 2000
LETS = ("free_busy", "questions")  # what a circle's people may ask its owner's agent (circles_ask.py); nothing until picked
SHARE_FIELDS = "id AS rid, record::id(id) AS id, by, at, circles, people, object, tag, note, with_item, granted, space, via"
TO_FIELDS = "id AS rid, record::id(id) AS id, share, account, at, opens"
UNTRUSTED = ("object.name", "object.summary", "object.content")


class Problem(ValueError):
    """Something a person can fix: the message says what."""


# ---------- who ----------
def _people(db, ids):
    """{account id: {id, name, email}} for the accounts among `ids` that can still sign in."""
    ids = sorted({int(i) for i in ids if isinstance(i, int) and not isinstance(i, bool)})
    if not ids:
        return {}
    rows = db.rows(
        "SELECT record::id(id) AS id, email, name, disabled, removed_at FROM account WHERE id IN $ids",
        ids=[R("account", i) for i in ids],
    )
    return {
        r["id"]: {"id": r["id"], "name": r.get("name"), "email": r["email"]}
        for r in rows
        if not r.get("disabled") and not r.get("removed_at")
    }


def _label(p):
    return (p or {}).get("name") or (p or {}).get("email") or "Someone"


def known(db, me):
    """The account ids `me` may put in a circle: people who share a namespace with them, and people who have them in
    a circle of theirs; everyone for an admin. Never themselves."""
    if me.get("admin"):
        ids = set(db.values("SELECT VALUE record::id(id) FROM account"))
    else:
        mine = db.values("SELECT VALUE space FROM membership WHERE account = $a", a=me["id"])
        ids = set(db.values("SELECT VALUE account FROM membership WHERE space IN $s", s=mine)) if mine else set()
        ids |= set(db.values("SELECT VALUE owner FROM circle WHERE members CONTAINS $a", a=me["id"]))
    ids.discard(me["id"])
    return ids


def people(db, me):
    """The people `me` may put in a circle, by name: [{id, name, email}]."""
    return sorted(_people(db, known(db, me)).values(), key=lambda p: (_label(p).lower(), p["id"]))


# ---------- circles ----------
def _view(db, row, names=None):
    names = names if names is not None else _people(db, row.get("members") or [])
    members = [names[m] for m in row.get("members") or [] if m in names]
    return {
        "id": row["id"],
        "name": row["name"],
        "members": members,
        "lets": [x for x in row.get("lets") or [] if x in LETS],
        "about": [n for n in (store.space_names(db).get(s) for s in row.get("ask_spaces") or []) if n],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def circles(db, owner):
    """Someone's circles, by name, each with its members who can still sign in."""
    rows = db.rows(
        "SELECT record::id(id) AS id, name, members, lets, ask_spaces, created_at, updated_at FROM circle WHERE owner = $o", o=owner
    )
    names = _people(db, [m for r in rows for m in r.get("members") or []])
    return sorted((_view(db, r, names) for r in rows), key=lambda c: (c["name"].lower(), c["id"]))


def _row(db, cid, owner):
    r = db.one(
        "SELECT record::id(id) AS id, owner, name, members, lets, ask_spaces, created_at, updated_at FROM $r", r=R("circle", int(cid))
    )
    if not r or r.get("owner") != owner:
        raise KeyError(cid)
    return r


def get(db, cid, owner):
    """One of `owner`'s circles; KeyError for anyone else's, or none."""
    return _view(db, _row(db, cid, owner))


def _name(db, owner, name, cid=None):
    name = " ".join(str(name or "").split())
    if not name or len(name) > NAME_MAX:
        raise Problem(f"a circle's name is 1 to {NAME_MAX} characters")
    for r in db.rows("SELECT record::id(id) AS id, name FROM circle WHERE owner = $o", o=owner):
        if r["id"] != cid and r["name"].lower() == name.lower():
            raise Problem(f"you already have a circle called {r['name']}")
    return name


def _members(db, me, members):
    """Member ids from ids or email addresses, each someone `me` may add (Problem naming the first that isn't)."""
    ok = known(db, me)
    out = []
    for m in members or []:
        if isinstance(m, str) and not m.strip().isdigit():
            acct = auth.find_account(db, m)
            mid = acct["id"] if acct else None
        else:
            mid = int(m) if str(m).strip().isdigit() else None
        if mid is None or mid not in ok:
            raise Problem(f"{m} isn't someone you can add: add people you share a namespace with, or who have you in a circle")
        if mid not in out:
            out.append(mid)
    if len(out) > MAX_MEMBERS:
        raise Problem(f"a circle holds at most {MAX_MEMBERS} people")
    gone = set(out) - set(_people(db, out))
    if gone:
        raise Problem("someone you picked can't sign in any more")
    return out


def _lets(lets):
    out = [str(x) for x in dict.fromkeys(lets or [])]
    bad = [x for x in out if x not in LETS]
    if bad:
        raise Problem(f"a circle can let its people ask for {', '.join(LETS)}, not {bad[0]}")
    return out


def _about(db, me, about):
    """Namespace ids, from names or ids, that `me` reads outside vaults: what a circle's agents may ask about."""
    names = {v: k for k, v in store.space_names(db).items()}
    mine = set(auth.roles(db, me))
    out = []
    for a in about or []:
        sid = int(a) if str(a).strip().isdigit() else names.get(str(a).strip())
        if sid is None or sid not in mine:
            raise Problem(f"{a} isn't a namespace you read")
        if _vault(db, sid):
            raise Problem(f"{a} is a vault: what's in it stays in it")
        if sid not in out:
            out.append(sid)
    return out


def create(db, me, name, members=(), lets=(), about=()):
    """A new circle of `me`'s, with members picked by id or email, and what they may ask `me`'s agent (`lets`). Its id."""
    if len(db.values("SELECT VALUE id FROM circle WHERE owner = $o", o=me["id"])) >= MAX_CIRCLES:
        raise Problem(f"you can have at most {MAX_CIRCLES} circles")
    name = _name(db, me["id"], name)
    picked = _members(db, me, members)
    allowed = _lets(lets)
    spaces = _about(db, me, about)
    cid = db.next_id("circle")
    at = store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("circle", cid),
        d={"owner": me["id"], "name": name, "members": picked, "lets": allowed, "ask_spaces": spaces, "created_at": at, "updated_at": at},
    )
    return cid


def update(db, me, cid, name=None, members=None, add=(), remove=(), lets=None, about=None):
    """Rename a circle of `me`'s, set its members, or add and take some away, or set what they may ask `me`'s agent
    (`lets`) and which namespaces of `me`'s its questions are answered from (`about`). KeyError for anyone else's."""
    row = _row(db, cid, me["id"])
    patch = {"updated_at": store.now()}
    if name is not None:
        patch["name"] = _name(db, me["id"], name, row["id"])
    if lets is not None:
        patch["lets"] = _lets(lets)
    if about is not None:
        patch["ask_spaces"] = _about(db, me, about)
    if members is not None or add or remove:
        now = list(row.get("members") or []) if members is None else _members(db, me, members)
        now += [m for m in _members(db, me, add) if m not in now]
        drop = {int(m) for m in remove or [] if str(m).strip().isdigit()}
        drop |= {a["id"] for a in (auth.find_account(db, m) for m in remove or [] if isinstance(m, str) and "@" in m) if a}
        patch["members"] = [m for m in now if m not in drop]
    db.q("UPDATE $r MERGE $d", r=R("circle", row["id"]), d=patch)
    return get(db, row["id"], me["id"])


def delete(db, me, cid):
    """Delete a circle of `me`'s. What was shared with it stays shared."""
    row = _row(db, cid, me["id"])
    db.q("DELETE $r", r=R("circle", row["id"]))


# ---------- sharing ----------
def _vault(db, sid):
    """Whether a namespace is a vault (keyring.py), as podcasts and feeds ask it."""
    return sid is not None and bool((db.one("SELECT vault FROM $r", r=R("data_key", int(sid))) or {}).get("vault"))


def _viewer(db, me):
    if me.get("admin"):
        return events.viewer(me["id"], None, admin=True)
    return events.viewer(me["id"], set(auth.roles(db, me)))


def _may_read(db, me, who, rid, space):
    return (
        bool(who.get("admin"))
        or space in (who.get("spaces") or set())
        or access.has_permission(db, rid, me["id"])
        or access.in_collection(db, rid, me["id"])
    )


def _tags(tags):
    out = []
    for t in tags or []:
        if isinstance(t, dict) and t.get("type") and (t.get("id") or t.get("name")):
            out.append(store.clean({k: t.get(k) for k in ("type", "id", "name")}))
    return out[: events.MAX_TAGS]


def _from_activity(db, cfg, me, who, aid):
    a = events.get(db, cfg, str(aid), who)
    if a is None:
        raise KeyError(aid)
    if a["kind"].startswith("share."):
        raise Problem("share what was shared itself, not the share")
    o = a.get("object") or {}
    rid = events.ref_id(o.get("id"), "recording")
    obj = store.clean(
        {
            "type": o.get("type") or "Note",
            "id": o.get("id") if isinstance(o.get("id"), str) and events.REF_RX.match(o["id"]) else None,
            "name": events_content._text(o.get("name"), 300) or a["summary"],
            "summary": a["summary"],
            "kind": a["kind"],
            "about": a["id"],
            "published": a.get("published"),
        }
    )
    return obj, _tags(a.get("tag")), a.get("space"), rid if isinstance(rid, int) else None


def _from_recording(db, me, who, rid):
    from . import render

    rec = db.one("SELECT * FROM $r", r=R("recording", int(rid)))
    if not rec or not _may_read(db, me, who, int(rid), rec.get("space")):
        raise KeyError(rid)
    media = render.kind(rec)
    title = events_content._text(rec.get("title"), 300) or "Untitled"
    obj = {
        "type": events_content.TYPES.get(media, "Document"),
        "id": f"lens:recording/{int(rid)}",
        "name": title,
        "summary": title,
        "media": media,
    }
    return obj, events_content.entity_tags(db, [int(rid)]).get(int(rid), []), rec.get("space"), int(rid)


def _recipients(db, me, circle_ids, people_ids):
    """(member ids, circle names) for a share: every member of `me`'s circles named (by id, or by name as automations
    name them), and people in any of their circles. Problem when that's nobody."""
    rows = db.rows("SELECT record::id(id) AS id, name, members FROM circle WHERE owner = $o", o=me["id"])
    mine = {r["id"]: r for r in rows}
    out, names = [], []
    by_name = {str(r["name"]).casefold(): r for r in rows}
    for c in circle_ids or []:
        r = mine.get(int(c)) if str(c).strip().isdigit() else by_name.get(str(c).strip().casefold())
        if r is None:
            raise KeyError(f"circle {c}")
        names.append(r["name"])
        out += [m for m in r.get("members") or [] if m not in out]
    every = {m for r in rows for m in r.get("members") or []}
    for p in people_ids or []:
        pid = int(p) if str(p).strip().isdigit() else None
        if pid is None and isinstance(p, str):
            acct = auth.find_account(db, p)
            pid = acct["id"] if acct else None
        if pid is None or pid not in every:
            raise Problem(f"{p} isn't in any of your circles: add them to one first")
        if pid not in out:
            out.append(pid)
    live = _people(db, out)
    out = [m for m in out if m in live and m != me["id"]]
    if not out:
        raise Problem("there's nobody to share with: pick a circle with people in it")
    return out, names


def _prepare(db, cfg, me, activity, recording, circles_, people_, note, with_item, who):
    """What a share would be, checked, without sharing it: (snapshot, tags, namespace, recording id, its row, member
    ids, circle names, note)."""
    if (activity is None) == (recording is None):
        raise Problem("share one thing: an activity or a resource")
    note = (str(note).strip() or None) if note is not None else None
    if note and len(note) > NOTE_MAX:
        raise Problem(f"a note is at most {NOTE_MAX} characters")
    who = who or _viewer(db, me)
    if activity is not None:
        obj, tags, space, rid = _from_activity(db, cfg, me, who, activity)
    else:
        obj, tags, space, rid = _from_recording(db, me, who, recording)
    rec = db.one("SELECT space, collection FROM $r", r=R("recording", rid)) if rid is not None else None
    if _vault(db, space) or (rec is not None and _vault(db, rec.get("space"))):
        raise Problem("what's in a vault stays in it: it can't be shared")
    to, names = _recipients(db, me, circles_, people_)
    if with_item:
        if rec is None:
            raise Problem("only a resource can be shared with the item itself")
        if "publish" not in auth.permissions(db, me).get(rec["space"], frozenset()):
            raise PermissionError("giving people the item needs the publish permission in its namespace")
    return obj, tags, space, rid, rec, to, names, note


def preview(db, cfg, me, *, activity=None, recording=None, circles_=(), people_=(), note=None, with_item=False, who=None):
    """What share() would share and with whom, checked the same way, without sharing it: {object, circles, people}."""
    obj, _, _, _, _, to, names, _ = _prepare(db, cfg, me, activity, recording, circles_, people_, note, with_item, who)
    named = _people(db, to)
    return {"object": obj, "circles": names, "people": [named[m] for m in to if m in named]}


def share(db, cfg, me, *, activity=None, recording=None, circles_=(), people_=(), note=None, with_item=False, via="web", who=None):
    """Share an activity (by id, as the timeline gives it) or a resource (by id) that `me` can see with their circles
    and people in them, with a note. `with_item` also gives members who can't open the resource permission on it (it
    needs the publish permission in its namespace). `who`: the viewer `me` reads the timeline as (their own by
    default). The share as list_shares gives it."""
    obj, tags, space, rid, rec, to, names, note = _prepare(db, cfg, me, activity, recording, circles_, people_, note, with_item, who)
    opens, granted = {}, []
    for m in to:
        how = None
        if rec is not None:
            if auth.allows(auth.roles(db, auth.get_account(db, m)), rec["space"]) or access.in_collection(db, rid, m):
                how = "member"
            elif access.has_permission(db, rid, m):
                how = "permission"
            elif with_item:
                access.give(db, rid, m, me.get("email"))
                granted.append(m)
                how = "permission"
        opens[m] = how
    obj.pop("url", None)
    if note:
        obj["content"] = note
    sid = db.next_id("circle_share")
    at = store.now()
    named = _people(db, to)
    stmts = [
        "CREATE $s CONTENT $d",
        *(f"CREATE $t{i} CONTENT $d{i}" for i in range(len(to))),
    ]
    params = {
        "s": R("circle_share", sid),
        "d": store.clean(
            {
                "by": me["id"],
                "at": at,
                "circles": names,
                "people": to,
                "object": obj,
                "tag": tags,
                "note": note,
                "with_item": bool(with_item),
                "granted": granted,
                "space": space,
                "via": via,
            }
        ),
    }
    for i, m in enumerate(to):
        params[f"t{i}"] = R("circle_share_to", db.next_id("circle_share_to"))
        params[f"d{i}"] = store.clean({"share": sid, "account": m, "at": at, "opens": opens[m]})
    db.run(stmts, **params)
    events.wake()
    return {
        "id": sid,
        "at": at,
        "object": store.clean({**obj, "url": _url(obj, "member")}),
        "circles": names,
        "people": [named[m] for m in to if m in named],
        "with_item": bool(with_item),
        "granted": len(granted),
    }


def undo(db, me, sid):
    """Take back a share of `me`'s: it leaves every member's timeline and the sharer's, and the permissions it gave are
    taken back. KeyError for anyone else's, or none."""
    row = db.one(f"SELECT {SHARE_FIELDS} FROM $r", r=R("circle_share", int(sid)))
    if not row or row.get("by") != me["id"]:
        raise KeyError(sid)
    rid = events.ref_id((row.get("object") or {}).get("id"), "recording")
    for m in row.get("granted") or []:
        if isinstance(rid, int):
            access.take(db, rid, m)
    db.run(["DELETE circle_share_to WHERE share = $n", "DELETE $r"], n=row["id"], r=R("circle_share", row["id"]))
    return {"undone": row["id"], "taken_back": len(row.get("granted") or [])}


def shares(db, me, direction="received", limit=50):
    """`me`'s shares, newest first: those shared with them (received) or by them (sent), each with who, what, the
    note and when."""
    limit = max(1, min(int(limit or 50), 200))
    if direction == "sent":
        rows = db.rows(f"SELECT {SHARE_FIELDS} FROM circle_share WHERE by = $me ORDER BY at DESC, rid DESC LIMIT {limit}", me=me["id"])
        names = _people(db, [m for r in rows for m in r.get("people") or []])
        return [
            {
                "id": r["id"],
                "at": r["at"],
                "object": store.clean({**(r.get("object") or {}), "url": _url(r.get("object") or {}, "member")}),
                "note": r.get("note"),
                "circles": r.get("circles") or [],
                "people": [names[m] for m in r.get("people") or [] if m in names],
                "with_item": bool(r.get("with_item")),
                "activity": f"share-{r['id']}",
            }
            for r in rows
        ]
    got = db.rows(f"SELECT {TO_FIELDS} FROM circle_share_to WHERE account = $me ORDER BY at DESC, rid DESC LIMIT {limit}", me=me["id"])
    rows = _shares(db, [g["share"] for g in got])
    names = _people(db, [r["by"] for r in rows.values()])
    out = []
    for g in got:
        r = rows.get(g["share"])
        if not r:
            continue
        obj = store.clean({**(r.get("object") or {}), "url": _url(r.get("object") or {}, g.get("opens"))})
        out.append(
            {
                "id": r["id"],
                "at": g["at"],
                "by": names.get(r["by"]) or {"id": r["by"], "name": None, "email": None},
                "object": obj,
                "note": r.get("note"),
                "with_item": bool(r.get("with_item")),
                "activity": f"got-share-{g['id']}",
            }
        )
    return out


def _url(obj, opens):
    """Where a member opens what was shared: the workspace for a member of its namespace, the visitors' page for
    someone given permission on it, nowhere else."""
    rid = events.ref_id(obj.get("id"), "recording")
    if not isinstance(rid, int) or opens not in ("member", "permission"):
        return None
    return f"/resources/{rid}" if opens == "member" else f"/explore/recordings/{rid}"


def _shares(db, ids):
    ids = sorted({i for i in ids if isinstance(i, int)})
    if not ids:
        return {}
    return {
        r["id"]: r for r in db.rows(f"SELECT {SHARE_FIELDS} FROM circle_share WHERE id IN $ids", ids=[R("circle_share", i) for i in ids])
    }


# ---------- as activities ----------
def _actor(names, by):
    return {"type": "Person", "id": f"lens:account/{by}", "name": _label(names.get(by)) if names.get(by) else "Someone"}


def _quoted(obj):
    return f"“{events_content._text(obj.get('name'), 120) or 'something'}”"


def _sent(db, rows):
    rows = [r for r in rows if r and r.get("at")]
    if not rows:
        return []
    names = _people(db, [r["by"] for r in rows] + [m for r in rows for m in r.get("people") or []])
    out = []
    for r in rows:
        whom = list(r.get("circles") or []) or [_label(names.get(m)) for m in (r.get("people") or [])[:3]]
        obj = {
            **store.clean({**(r.get("object") or {}), "url": _url(r.get("object") or {}, "member")}),
            "share": r["id"],
            "circles": list(r.get("circles") or []),
            "people": [_label(names.get(m)) for m in r.get("people") or [] if m in names],
            "with_item": bool(r.get("with_item")),
        }
        actor = _actor(names, r["by"])
        out.append(
            events.build(
                f"share-{r['id']}",
                "share.sent",
                actor=actor,
                obj=obj,
                summary=f"{actor['name']} shared {_quoted(obj)} with {', '.join(whom) or 'nobody'}",
                space=r.get("space"),
                source="shares_sent",
                pos=[r["at"], r["id"]],
                published=r["at"],
                tag=r.get("tag"),
                untrusted=UNTRUSTED,
                audience="owner",
                owner=r["by"],
            )
        )
    return out


def _received(db, rows):
    rows = [r for r in rows if r and r.get("at")]
    if not rows:
        return []
    got = _shares(db, [r["share"] for r in rows])
    names = _people(db, [s["by"] for s in got.values()])
    out = []
    for r in rows:
        s = got.get(r["share"])
        if not s:
            continue
        obj = store.clean({**(s.get("object") or {}), "url": _url(s.get("object") or {}, r.get("opens")), "share": s["id"]})
        obj["with_item"] = bool(s.get("with_item"))
        actor = _actor(names, s["by"])
        out.append(
            events.build(
                f"got-share-{r['id']}",
                "share.received",
                actor=actor,
                obj=obj,
                summary=f"{actor['name']} shared {_quoted(obj)} with you",
                space=None,
                source="shares_received",
                pos=[r["at"], r["id"]],
                published=r["at"],
                tag=s.get("tag"),
                untrusted=UNTRUSTED,
                audience="owner",
                owner=r["account"],
            )
        )
    return out


class _ShareSource(events.Source):
    table = "circle_share"
    fields = SHARE_FIELDS
    rx = re.compile(r"^share-(\d+)$")
    mine = "by"

    def _make(self, db, rows):
        return _sent(db, rows)

    def _spaces(self, spaces, p):
        return events.space_sql(spaces, p)

    def read(self, db, cfg, pos, spaces, limit, hint=None):
        p: dict = {}
        where = f"{events.after_sql('at', pos, p, table=self.table)} AND {self._spaces(spaces, p)}"
        rows = db.rows(f"SELECT {self.fields} FROM {self.table} WHERE {where} ORDER BY at, rid LIMIT {int(limit)}", **p)
        return self._make(db, rows)

    def page(self, db, cfg, before, who, flt, limit):
        if who.get("account") is None:
            return []
        p: dict = {"me": who["account"]}
        where = events_content._paging("at", self.table, before, {**who, "admin": True}, flt, p) + [f"{self.mine} = $me"]
        rows = db.rows(
            f"SELECT {self.fields} FROM {self.table} WHERE {' AND '.join(where)} ORDER BY at DESC, rid DESC LIMIT {int(limit)}", **p
        )
        return self._make(db, rows)

    def get(self, db, cfg, activity_id):
        m = self.rx.match(activity_id)
        if not m:
            return None
        r = db.one(f"SELECT {self.fields} FROM $r", r=R(self.table, int(m.group(1))))
        got = self._make(db, [r]) if r else []
        return got[0] if got else None

    def get_many(self, db, cfg, ids):
        ns = sorted({int(m.group(1)) for m in (self.rx.match(i) for i in ids) if m})
        if not ns:
            return []
        return self._make(db, db.rows(f"SELECT {self.fields} FROM {self.table} WHERE id IN $rs", rs=[R(self.table, n) for n in ns]))


class SharesSentSource(_ShareSource):
    """circle_share rows as share.sent, read by (at, id): the sharer alone sees them."""

    name = "shares_sent"


class SharesReceivedSource(_ShareSource):
    """circle_share_to rows (one per member) as share.received, read by (at, id): each member sees their own. They
    belong to no namespace, so a follower of some namespaces doesn't read them."""

    name = "shares_received"
    table = "circle_share_to"
    fields = TO_FIELDS
    rx = re.compile(r"^got-share-(\d+)$")
    mine = "account"

    def _make(self, db, rows):
        return _received(db, rows)

    def _spaces(self, spaces, p):
        return "true" if spaces is None else "false"


events.register_source(SharesSentSource())
events.register_source(SharesReceivedSource())

_OBJECT = {
    "type": "object",
    "properties": {
        "type": _s("Object type"),
        "id": _s("What was shared"),
        "name": _s("Name"),
        "url": _s("Link"),
        "summary": _s("What the timeline said"),
        "media": _s("Media", enum=["audio", "video", "image", "document", "transcript"]),
        "content": _s("Note"),
        "kind": _s("Kind of activity shared"),
        "about": _s("The activity shared"),
        "published": _s("When it happened", format="date-time"),
        "share": _s("Share", "integer"),
        "circles": {"type": "array", "title": "Circles", "items": _s("Circle")},
        "people": {"type": "array", "title": "Shared with", "items": _s("Person")},
        "with_item": _s("With the item", "boolean"),
    },
}
_SHARED = {
    "type": "Document",
    "id": "lens:recording/7",
    "name": "School trip on Friday",
    "url": "/resources/7",
    "summary": "Work mail received “School trip on Friday” from office@stmarys.org",
    "content": "Sam's trip is on Friday: he needs a packed lunch.",
    "kind": "email.received",
    "about": "create-recording-7",
    "published": "2026-10-15T08:12:03.000Z",
    "share": 4,
    "with_item": True,
}
SCHOOL = {"type": "Mention", "id": "lens:entity/12", "name": "St Mary's School"}
ADEEL = {"type": "Person", "id": "lens:account/1", "name": "Adeel"}
events.kind(
    "share.sent",
    label="You shared",
    source="shares_sent",
    as_type="Announce",
    object_type="Document",
    fields={"object": _OBJECT},
    untrusted=UNTRUSTED,
    sample=_sample(
        "share-4",
        "share.sent",
        "Announce",
        "shares_sent",
        ["2026-10-15T16:24:10+00:00", 4],
        ADEEL,
        {**_SHARED, "circles": ["Immediate family"], "people": ["Sara", "Sam"]},
        "Adeel shared “School trip on Friday” with Immediate family",
        UNTRUSTED,
        [SCHOOL],
    ),
)
events.kind(
    "share.received",
    label="Shared with you",
    source="shares_received",
    as_type="Announce",
    object_type="Document",
    fields={"object": _OBJECT},
    untrusted=UNTRUSTED,
    sample={
        **_sample(
            "got-share-9",
            "share.received",
            "Announce",
            "shares_received",
            ["2026-10-15T16:24:10+00:00", 9],
            ADEEL,
            _SHARED,
            "Adeel shared “School trip on Friday” with you",
            UNTRUSTED,
            [SCHOOL],
        ),
        "space": None,
    },
)

# what builds on circles' sources, loaded once they're defined, so that any of these modules can be imported first
from . import circles_ask  # noqa: E402, F401 - what people's agents ask of each other, as agent.asked
