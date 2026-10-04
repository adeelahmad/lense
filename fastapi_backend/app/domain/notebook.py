"""Notes as pages (docs/notes.md): free notes in a tree, and a page for every resource, entity and topic.

A page belongs to one namespace. It has a title, a one-line summary (the context the assistant reads first), a date,
a place in PARA (project, area, resource, archive), a parent page for the tree, a body in Markdown and who wrote it
(a person or the assistant). A page about something (`about`, like "recording:12" or "entity:5") is that thing's own
page: there is at most one per thing, made the first time someone writes on it; free notes have no `about`.

The body links to anything with mention tokens, which the editor and the assistant both write:

    @[Weekly call](recording:12)   @[Ada Lovelace](entity:5)   @[Plans](page:3)   #[Capsids](entity:9)

@ is for resources, people and other pages; # is for topics (entities of type TERM until topics become a SKOS
vocabulary of their own). Links are kept as note_link rows, so a page shows its backlinks and the graph sees them.

The editor may also keep its own document state (`doc`, opaque: a BlockSuite/Yjs snapshot) next to the Markdown. A
change to the Markdown alone (the assistant's, say) drops that state so the editor rebuilds it from the Markdown.

Refine later: pages for partial collection members, page history and undo, attachments, real-time co-editing.
"""

from __future__ import annotations

import datetime as dt
import re

from . import store

R = store.R
PLACES = ("project", "area", "resource", "archive")
AUTHORS = ("person", "assistant")
KINDS = ("recording", "entity", "collection", "speaker", "page")  # what a page can link to
ABOUT = ("recording", "entity", "collection", "speaker")  # what can have a page of its own
TITLE_MAX = 200
SUMMARY_MAX = 300
BODY_MAX = 200_000
DOC_MAX = 2_000_000
PAGES_MAX = 20_000  # per namespace
DEPTH_MAX = 12
MENTION = re.compile(r"([@#])\[([^\]\n]{1,200})\]\(([a-z]+):(\d{1,18})\)")
FIELDS = (
    "record::id(id) AS id, space, title, summary, summary_by, date, place, parent, position, about, author, "
    "created_by, updated_by, created_at, updated_at"
)


def _title(title):
    title = " ".join(str(title or "").split())
    if not title:
        raise ValueError("A page needs a title.")
    if len(title) > TITLE_MAX:
        raise ValueError(f"A title can have up to {TITLE_MAX} characters.")
    return title


def _summary(summary):
    summary = " ".join(str(summary or "").split())
    if len(summary) > SUMMARY_MAX:
        raise ValueError(f"A summary is one line of up to {SUMMARY_MAX} characters.")
    return summary or None


def _date(date):
    if date in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(date)[:10]).isoformat()
    except ValueError:
        raise ValueError("A page's date looks like 2026-10-04.") from None


def _place(place):
    if place in (None, ""):
        return None
    if place not in PLACES:
        raise ValueError(f"A page's place is one of {', '.join(PLACES)}.")
    return place


def _body(body):
    body = str(body or "")
    if len(body) > BODY_MAX:
        raise ValueError(f"A page can have up to {BODY_MAX} characters.")
    return body


def _about(about):
    """('recording', 12) from 'recording:12', or ValueError."""
    kind, _, key = str(about or "").partition(":")
    if kind not in ABOUT or not key.isdigit():
        raise ValueError(f"A page can be about a {', '.join(ABOUT[:-1])} or {ABOUT[-1]}, like recording:12.")
    return kind, int(key)


def mentions(body):
    """The links in a body, in order and once each: [(sign, kind, id, label)]."""
    out, seen = [], set()
    for sign, label, kind, key in MENTION.findall(body or ""):
        if kind not in KINDS or (sign, kind, key) in seen:
            continue
        seen.add((sign, kind, key))
        out.append((sign, kind, int(key), label.strip()))
    return out


def plain(body):
    """The body as plain text: mentions become their labels, Markdown marks are dropped."""
    text = MENTION.sub(lambda m: m.group(2), body or "")
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^[ \t]{0,3}(#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+(\[[ xX]\][ \t]+)?|\d+[.)][ \t]+)", "", text, flags=re.M)
    text = re.sub(r"[*_`~]{1,3}", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def owner(db, kind, key):
    """The namespace a thing belongs to, or KeyError."""
    if kind == "page":
        row = db.one("SELECT space FROM $r", r=R("note_page", int(key)))
    else:
        row = db.one("SELECT space FROM $r", r=R(kind, int(key)))
    if not row or row.get("space") is None:
        raise KeyError(f"{kind}:{key}")
    return row["space"]


def get(db, pid):
    """The page with its body, or KeyError."""
    p = db.one(f"SELECT {FIELDS}, body, doc FROM $r", r=R("note_page", int(pid)))
    if not p:
        raise KeyError(pid)
    return p


def about(db, sid, thing):
    """The page about this thing in namespace sid (its own page), or None while nobody has written one."""
    kind, key = _about(thing)
    return db.one(f"SELECT {FIELDS}, body, doc FROM note_page WHERE about_key = $k LIMIT 1", k=f"{sid}:{kind}:{key}")


def tree(db, sid, everything=False):
    """The namespace's pages without their bodies, for the explorer: free notes only, or every page."""
    where = "space = $s" if everything else "space = $s AND about = NONE"
    rows = db.rows(f"SELECT {FIELDS} FROM note_page WHERE {where}", s=sid)
    return sorted(rows, key=lambda p: (p.get("position") or 0, p["id"]))


def _check_parent(db, sid, pid, parent):
    """ValueError unless `parent` is a page of the namespace that isn't `pid` or inside it."""
    if parent is None:
        return None
    seen, at = set(), int(parent)
    while at is not None:
        if at == pid:
            raise ValueError("A page can't go inside itself.")
        row = db.one("SELECT space, parent FROM $r", r=R("note_page", at))
        if not row or row["space"] != sid:
            raise ValueError("Its parent has to be a page in the same namespace.")
        seen.add(at)
        if len(seen) > DEPTH_MAX:
            raise ValueError(f"Pages go up to {DEPTH_MAX} levels deep.")
        at = row.get("parent")
    return int(parent)


def _last_position(db, sid, parent):
    rows = db.values(
        "SELECT VALUE position FROM note_page WHERE space = $s AND parent = $p AND about = NONE",
        s=sid,
        p=parent if parent is not None else None,
    )
    return max([r for r in rows if r is not None], default=0) + 1


def _links(db, pid, sid, body):
    """Keep the body's links as note_link rows, in place of the old ones."""
    db.q("DELETE note_link WHERE page = $p", p=pid)
    for sign, kind, key, label in mentions(body):
        db.q(
            "CREATE note_link CONTENT $d",
            d={"page": pid, "space": sid, "sign": sign, "target": f"{kind}:{key}", "label": label[:200]},
        )


def create(db, sid, account, title, body="", summary=None, date=None, place=None, parent=None, about_thing=None, author="person", doc=None):
    """A new page; its id. ValueError for bad input, a page that already exists about that thing, or too many."""
    if author not in AUTHORS:
        raise ValueError("A page is written by a person or the assistant.")
    title, body, summary = _title(title), _body(body), _summary(summary)
    key = None
    if about_thing:
        kind, ref = _about(about_thing)
        if owner(db, kind, ref) != sid:
            raise ValueError("That isn't in this namespace.")
        key = f"{sid}:{kind}:{ref}"
        if db.one("SELECT VALUE id FROM note_page WHERE about_key = $k LIMIT 1", k=key):
            raise ValueError("That already has a page: change it instead.")
        about_thing, parent = f"{kind}:{ref}", None
    if len(db.values("SELECT VALUE id FROM note_page WHERE space = $s", s=sid)) >= PAGES_MAX:
        raise ValueError(f"A namespace can have up to {PAGES_MAX} pages.")
    pid = db.next_id("note_page")
    parent = _check_parent(db, sid, pid, parent)
    t = store.now()
    page = {
        "space": sid,
        "title": title,
        "summary": summary,
        "summary_by": author if summary else None,
        "date": _date(date) or t[:10],
        "place": _place(place),
        "parent": parent,
        "position": None if about_thing else _last_position(db, sid, parent),
        "about": about_thing,
        "about_key": key,
        "body": body,
        "text": plain(body),
        "doc": _doc(doc),
        "author": author,
        "created_by": account,
        "updated_by": account,
        "created_at": t,
        "updated_at": t,
    }
    db.q("CREATE $r CONTENT $d", r=R("note_page", pid), d=store.clean(page))
    _links(db, pid, sid, body)
    return pid


def _doc(doc):
    if doc is None:
        return None
    if not isinstance(doc, str) or len(doc) > DOC_MAX:
        raise ValueError("The editor's document is too large.")
    return doc


UNSET = object()


def update(db, pid, account, title=None, body=None, summary=UNSET, date=UNSET, place=UNSET, author="person", doc=UNSET):
    """Change what's given. A new body without `doc` drops the editor's state; the summary records who wrote it."""
    p = get(db, pid)
    sets, args = ["updated_at = $t", "updated_by = $a"], {"t": store.now(), "a": account}
    if title is not None:
        sets.append("title = $title")
        args["title"] = _title(title)
    if summary is not UNSET:
        sets += ["summary = $summary", "summary_by = $by"]
        args["summary"] = _summary(summary)
        args["by"] = author if args["summary"] else None
    if date is not UNSET:
        sets.append("date = $date")
        args["date"] = _date(date)
    if place is not UNSET:
        sets.append("place = $place")
        args["place"] = _place(place)
    if body is not None:
        sets += ["body = $body", "text = $text"]
        args["body"] = _body(body)
        args["text"] = plain(args["body"])
        if doc is UNSET:
            doc = None
    if doc is not UNSET:
        sets.append("doc = $doc")
        args["doc"] = _doc(doc)
    db.q(f"UPDATE $r SET {', '.join(sets)}", r=R("note_page", int(pid)), **args)
    if body is not None:
        _links(db, int(pid), p["space"], args["body"])


def move(db, pid, parent=None, before=None):
    """Put a free note inside `parent` (None: the top), before the page `before` (None: at the end)."""
    p = get(db, pid)
    if p.get("about"):
        raise ValueError("A page about something stays with it; only free notes move in the tree.")
    parent = _check_parent(db, p["space"], int(pid), parent)
    if before is None:
        position = _last_position(db, p["space"], parent)
    else:
        sib = db.rows(
            "SELECT record::id(id) AS id, position FROM note_page WHERE space = $s AND parent = $p AND about = NONE",
            s=p["space"],
            p=parent,
        )
        sib = sorted((s for s in sib if s["id"] != int(pid)), key=lambda s: (s.get("position") or 0, s["id"]))
        at = next((i for i, s in enumerate(sib) if s["id"] == int(before)), None)
        if at is None:
            raise ValueError("Put it before a page with the same parent.")
        low = (sib[at - 1].get("position") or 0) if at else (sib[at].get("position") or 0) - 1
        position = (low + (sib[at].get("position") or 0)) / 2
    db.q("UPDATE $r SET parent = $p, position = $pos, updated_at = $t", r=R("note_page", int(pid)), p=parent, pos=position, t=store.now())


def delete(db, pid):
    """Delete a page; the pages inside it move up to its parent. Links to it stay as text."""
    p = get(db, pid)
    for child in db.values("SELECT VALUE record::id(id) FROM note_page WHERE parent = $p", p=int(pid)):
        db.q("UPDATE $r SET parent = $up", r=R("note_page", child), up=p.get("parent"))
    db.q("DELETE note_link WHERE page = $p", p=int(pid))
    db.q("DELETE $r", r=R("note_page", int(pid)))


def links(db, pid):
    """The page's links, in the order its body has them."""
    p = get(db, pid)
    return [{"sign": s, "target": f"{k}:{i}", "label": label} for s, k, i, label in mentions(p.get("body"))]


def backlinks(db, sid, targets):
    """The pages of namespace `sid` that link to any of these targets ("page:3", "recording:12"): [{page, title}]."""
    rows = db.rows(
        "SELECT page, sign, target FROM note_link WHERE space = $s AND target IN $t",
        s=sid,
        t=list(targets),
    )
    ids = sorted({r["page"] for r in rows})
    titles = (
        {
            p["id"]: p
            for p in db.rows(
                "SELECT record::id(id) AS id, title, about, updated_at FROM note_page WHERE id IN $ids",
                ids=[R("note_page", i) for i in ids],
            )
        }
        if ids
        else {}
    )
    return [
        {"page": i, "title": titles[i]["title"], "about": titles[i].get("about"), "updated_at": titles[i].get("updated_at")}
        for i in ids
        if i in titles
    ]


def labels(db, targets):
    """Current names for link targets: {"recording:12": "Weekly call"}; the ones that are gone are left out."""
    by_kind = {}
    for t in targets:
        kind, _, key = t.partition(":")
        if kind in KINDS and key.isdigit():
            by_kind.setdefault(kind, []).append(int(key))
    out = {}
    for kind, keys in by_kind.items():
        table = "note_page" if kind == "page" else kind
        name = {"recording": "title ?? path", "page": "title", "speaker": "name ?? label"}.get(kind, "name")
        for r in db.rows(
            f"SELECT record::id(id) AS id, space, {name} AS name FROM {table} WHERE id IN $ids", ids=[R(table, k) for k in keys]
        ):
            out[f"{kind}:{r['id']}"] = {"name": r.get("name"), "space": r.get("space")}
    return out


def search_targets(db, sid, q="", sign="@", limit=20):
    """What a mention can link to in namespace sid, best matches first: for #, topics; for @, pages, recordings,
    people and other entities, collections and speakers."""
    q = " ".join(str(q or "").split()).casefold()
    hit = (lambda s: q in str(s or "").casefold()) if q else (lambda s: True)
    out = []
    if sign == "#":
        for e in db.rows("SELECT record::id(id) AS id, name FROM entity WHERE space = $s AND type = 'TERM' AND hidden != true", s=sid):
            if hit(e["name"]):
                out.append({"target": f"entity:{e['id']}", "label": e["name"], "kind": "topic"})
        return sorted(out, key=lambda x: (not x["label"].casefold().startswith(q), x["label"].casefold()))[:limit]
    for p in db.rows("SELECT record::id(id) AS id, title FROM note_page WHERE space = $s AND about = NONE", s=sid):
        if hit(p["title"]):
            out.append({"target": f"page:{p['id']}", "label": p["title"], "kind": "page"})
    for r in db.rows("SELECT record::id(id) AS id, title ?? path AS name FROM recording WHERE space = $s", s=sid):
        if hit(r["name"]):
            out.append({"target": f"recording:{r['id']}", "label": str(r["name"]).rsplit("/", 1)[-1], "kind": "recording"})
    for e in db.rows(
        "SELECT record::id(id) AS id, name, type FROM entity WHERE space = $s AND type NOT IN ['TERM', 'DATE', 'NUMBER'] AND hidden != true",
        s=sid,
    ):
        if hit(e["name"]):
            out.append({"target": f"entity:{e['id']}", "label": e["name"], "kind": "entity"})
    for c in db.rows("SELECT record::id(id) AS id, name FROM collection WHERE space = $s", s=sid):
        if hit(c["name"]):
            out.append({"target": f"collection:{c['id']}", "label": c["name"], "kind": "collection"})
    for s in db.rows("SELECT record::id(id) AS id, name ?? label AS name FROM speaker WHERE space = $s", s=sid):
        if s.get("name") and hit(s["name"]):
            out.append({"target": f"speaker:{s['id']}", "label": s["name"], "kind": "speaker"})
    return sorted(out, key=lambda x: (not x["label"].casefold().startswith(q), x["label"].casefold()))[:limit]


def release(db, thing):
    """The thing is gone (deleted, merged away, moved out of the namespace): its page stays, as a free note at the top
    of the tree, so nothing written on it is lost."""
    for p in db.rows("SELECT record::id(id) AS id, space FROM note_page WHERE about = $a", a=thing):
        db.q(
            "UPDATE $r SET about = NONE, about_key = NONE, parent = NONE, position = $pos",
            r=R("note_page", p["id"]),
            pos=_last_position(db, p["space"], None),
        )


def follow(db, thing, dst):
    """The thing moved to namespace dst: its page goes with it, with its links."""
    kind, key = _about(thing)
    for p in db.rows("SELECT record::id(id) AS id FROM note_page WHERE about = $a", a=thing):
        db.q("UPDATE $r SET space = $s, about_key = $k", r=R("note_page", p["id"]), s=dst, k=f"{dst}:{kind}:{key}")
        db.q("UPDATE note_link SET space = $s WHERE page = $p", s=dst, p=p["id"])
