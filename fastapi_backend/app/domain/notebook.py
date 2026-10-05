"""Notes as pages (docs/notes.md): free notes in a tree, and a page for every resource, entity and topic.

A page belongs to one namespace. It has a title, a one-line summary (the context the assistant reads first), a date,
a place in PARA (project, area, resource, archive), a parent page for the tree, a body in Markdown and who wrote it
(a person or the assistant). A page about something (`about`, like "recording:12" or "entity:5") is that thing's own
page: there is at most one per thing, made the first time someone writes on it; free notes have no `about`.

The body links to anything with mention tokens, which the editor and the assistant both write:

    @[Weekly call](recording:12)   @[Ada Lovelace](entity:5)   @[Plans](page:3)   #[Capsid design](topic:9)

@ is for resources, people and other pages; # is for topics, the namespace's vocabulary (topics.py). Links are kept as note_link rows, so a page shows its backlinks and the graph sees them.

The editor may also keep its own document state (`doc`, opaque: a BlockSuite/Yjs snapshot) next to the Markdown, which
holds what Markdown can't, like drawings on the edgeless canvas. A change to the Markdown alone (the assistant's, say)
keeps that state but marks it stale, and the editor brings its text in line with the Markdown.

Every change to a page's title, summary or text keeps what it was before as a version (`note_version`), so a page's
history can be read and any version restored, the assistant's changes included. Edits by one person in one sitting
make one version.

Refine later: pages for partial collection members, attachments, real-time co-editing, diffs between versions.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import re

from . import activity, store

R = store.R
PLACES = ("project", "area", "resource", "archive")
AUTHORS = ("person", "assistant")
VIEWS = ("page", "edgeless")  # how the editor shows it: a document, or the endless canvas (drawings, diagrams)
KINDS = ("recording", "entity", "topic", "collection", "speaker", "page")  # what a page can link to
ABOUT = ("recording", "entity", "topic", "collection", "speaker")  # what can have a page of its own
TITLE_MAX = 200
SUMMARY_MAX = 300
BODY_MAX = 200_000
DOC_MAX = 2_000_000
PAGES_MAX = 20_000  # per namespace
DEPTH_MAX = 12
MENTION = re.compile(r"([@#])\[([^\]\n]{1,200})\]\(([a-z]+):(\d{1,18})\)")
FIELDS = (
    "record::id(id) AS id, space, title, summary, summary_by, date, place, place_by, place_suggestion, parent, position, "
    "about, author, view, doc_stale, created_by, updated_by, created_at, updated_at"
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


def create(
    db,
    sid,
    account,
    title,
    body="",
    summary=None,
    date=None,
    place=None,
    parent=None,
    about_thing=None,
    author="person",
    doc=None,
    view=None,
):
    """A new page; its id. ValueError for bad input, a page that already exists about that thing, or too many."""
    if author not in AUTHORS:
        raise ValueError("A page is written by a person or the assistant.")
    if view is not None and view not in VIEWS:
        raise ValueError("A page is seen as a page or on the edgeless canvas.")
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
        "place_by": author if place else None,
        "filed": bool(place),
        "parent": parent,
        "position": None if about_thing else _last_position(db, sid, parent),
        "about": about_thing,
        "about_key": key,
        "body": body,
        "text": plain(body),
        "doc": _doc(doc),
        "view": view,
        "author": author,
        "refine_pending": True,
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
VERSION_SITTING = 600  # seconds: one person's edits this close together make one version
VERSIONS_MAX = 100  # versions kept per page; the oldest go first
VERSION_FIELDS = "record::id(id) AS id, page, at, by, author, title, summary, place, size, why"


def snapshot(db, p, account, author, why=None):
    """Keep page p (a row with its body) as it is, before a change by account (None: the model) as author. Changes by
    the same person in one sitting share one version, the one from before they started; anyone else's change, or the
    assistant's, starts a new one."""
    last = db.one(
        "SELECT at, by, author FROM note_version WHERE page = $p ORDER BY at DESC LIMIT 1",
        p=int(p["id"]),
    )
    now = dt.datetime.now(dt.timezone.utc)
    if last and why is None and author == "person" and last.get("author") == "person" and last.get("by") == account:
        at = dt.datetime.fromisoformat(str(last["at"]).replace("Z", "+00:00"))
        if (now - at).total_seconds() < VERSION_SITTING and str(p.get("updated_by") or "") == str(account or ""):
            return None
    body = p.get("body") or ""
    vid = db.next_id("note_version")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("note_version", vid),
        d=store.clean(
            {
                "page": int(p["id"]),
                "space": p["space"],
                "at": now.isoformat(timespec="milliseconds"),
                "by": account,
                "author": author,
                "title": p.get("title"),
                "summary": p.get("summary"),
                "place": p.get("place"),
                "body": body,
                "doc": p.get("doc"),
                "size": len(body),
                "why": why,
            }
        ),
    )
    old = db.rows(
        "SELECT record::id(id) AS id, at FROM note_version WHERE page = $p ORDER BY at DESC START $n", p=int(p["id"]), n=VERSIONS_MAX
    )
    if old:
        db.q("DELETE note_version WHERE id IN $ids", ids=[R("note_version", v["id"]) for v in old])
    return vid


def history(db, pid, limit=50):
    """The page's earlier versions, newest first, without their bodies."""
    return db.rows(f"SELECT {VERSION_FIELDS} FROM note_version WHERE page = $p ORDER BY at DESC LIMIT $n", p=int(pid), n=int(limit))


def version(db, pid, vid):
    """One earlier version with its body, or KeyError."""
    v = db.one(f"SELECT {VERSION_FIELDS}, body, doc FROM $r", r=R("note_version", int(vid)))
    if not v or v.get("page") != int(pid):
        raise KeyError(vid)
    return v


def restore(db, pid, vid, account):
    """Put an earlier version back: its title, summary and text (and the editor's state with them). What the page was
    becomes a version too, so a restore can be undone."""
    v = version(db, pid, vid)
    update(
        db,
        pid,
        account,
        title=v.get("title"),
        body=v.get("body") or "",
        summary=v.get("summary"),
        doc=v.get("doc"),
        why=f"restored the version of {str(v['at'])[:16].replace('T', ' ')}",
    )


def update(
    db, pid, account, title=None, body=None, summary=UNSET, date=UNSET, place=UNSET, author="person", doc=UNSET, view=UNSET, why=None
):
    """Change what's given. A new body without `doc` marks the editor's state stale; the summary records who wrote it.
    A change to the title, summary or text keeps what the page was as a version first (snapshot)."""
    p = get(db, pid)
    if (
        (title is not None and _title(title) != p.get("title"))
        or (body is not None and _body(body) != (p.get("body") or ""))
        or (summary is not UNSET and _summary(summary) != p.get("summary"))
    ):
        snapshot(db, p, account, author, why)
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
        # whoever files it decides: the assistant doesn't file it again, and its suggestion is done with
        sets += ["place = $place", "place_by = $place_by", "place_suggestion = NONE", "filed = true"]
        args["place"] = _place(place)
        args["place_by"] = author if args["place"] else None
    if title is not None or body is not None:
        sets.append("refine_pending = true")
    if body is not None:
        sets += ["body = $body", "text = $text"]
        args["body"] = _body(body)
        args["text"] = plain(args["body"])
        # a new body without the editor's state (the assistant's): the state is kept, for what Markdown can't hold
        # (drawings on the edgeless canvas), and marked stale so the editor brings the text in line with the body
        sets.append("doc_stale = $stale")
        args["stale"] = doc is UNSET
    if doc is not UNSET:
        sets.append("doc = $doc")
        args["doc"] = _doc(doc)
    if view is not UNSET:
        if view not in VIEWS:
            raise ValueError("A page is seen as a page or on the edgeless canvas.")
        sets.append("view = $view")
        args["view"] = view
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


def delete(db, pid, cfg=None):
    """Delete a page, with its files (given `cfg`, to reach where they are kept); the pages inside it move up to its
    parent. Links to it stay as text."""
    p = get(db, pid)
    if cfg is not None:
        for f in files(db, int(pid)):
            delete_file(db, cfg, int(pid), f["key"])
    for child in db.values("SELECT VALUE record::id(id) FROM note_page WHERE parent = $p", p=int(pid)):
        db.q("UPDATE $r SET parent = $up", r=R("note_page", child), up=p.get("parent"))
    db.q("DELETE note_link WHERE page = $p", p=int(pid))
    db.q("DELETE note_version WHERE page = $p", p=int(pid))
    db.q("DELETE $r", r=R("note_page", int(pid)))


def links(db, pid):
    """The page's links, in the order its body has them."""
    p = get(db, pid)
    return [{"sign": s, "target": f"{k}:{i}", "label": label} for s, k, i, label in mentions(p.get("body"))]


# ---------- files on a page: images, attachments and other blobs the editor keeps (blobs.py stores them) ----------
FILE_KEY_RX = re.compile(r"^[A-Za-z0-9+/=_-]{8,128}$")  # the editor's own key for a blob: a hash of its bytes
FILES_MAX = 500  # per page
FILE_FIELDS = "record::id(id) AS id, key, name, type, size, author, created_at"


def file_key(path):
    """What the editor names a file by: its bytes' SHA-256 in base64url, as BlockSuite's blob sync does."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while part := f.read(1 << 20):
            h.update(part)
    return base64.urlsafe_b64encode(h.digest()).decode()


def files(db, pid):
    """A page's files, oldest first."""
    rows = db.rows(f"SELECT {FILE_FIELDS} FROM note_file WHERE page = $p", p=int(pid))
    return sorted(rows, key=lambda f: f["id"])


def file_row(db, pid, key):
    row = db.one(f"SELECT {FILE_FIELDS}, where FROM note_file WHERE page = $p AND key = $k LIMIT 1", p=int(pid), k=key)
    if not row:
        raise KeyError(key)
    return row


def add_file(db, cfg, p, key, path, name=None, ctype=None, account=None, author="person"):
    """Keep a file on page p under the editor's `key`, where Settings → Storage says. The same key again is the same
    bytes, so it's kept once. Returns its row."""
    from . import blobs

    if not FILE_KEY_RX.match(key or "") or key != file_key(path):
        raise ValueError("the key isn't the file's: SHA-256 of its bytes, in base64url")
    pid = int(p["id"])
    try:
        return file_row(db, pid, key)
    except KeyError:
        pass
    if len(db.values("SELECT VALUE id FROM note_file WHERE page = $p", p=pid)) >= FILES_MAX:
        raise ValueError(f"A page keeps up to {FILES_MAX} files.")
    fid = db.next_id("note_file")
    where = blobs.put(db, cfg, p["space"], f"notes/{p['space']}/{pid}/{fid}", path)
    name = re.sub(r"[\x00-\x1f/\\]", "_", str(name or "").strip())[:200] or None
    ctype = ctype if ctype and re.match(r"^[\w.+-]+/[\w.+-]+$", ctype) else "application/octet-stream"
    db.q(
        "CREATE $r CONTENT $d",
        r=R("note_file", fid),
        d=store.clean(
            {
                "page": pid,
                "space": p["space"],
                "key": key,
                "name": name,
                "type": ctype,
                "size": os.path.getsize(path),
                "where": where,
                "author": author,
                "created_by": account,
                "created_at": store.now(),
            }
        ),
    )
    return file_row(db, pid, key)


def open_file(db, cfg, pid, key):
    """(row, a context manager giving the file's plain bytes to read). KeyError when there's no such file."""
    from . import blobs

    row = file_row(db, pid, key)
    return row, blobs.open_file(db, cfg, row.get("where"), f"notes/{_space_of(db, pid)}/{int(pid)}/{row['id']}")


def _space_of(db, pid):
    return get(db, pid)["space"]


def delete_file(db, cfg, pid, key):
    """Remove a file from a page and from where it's kept."""
    from . import blobs

    row = file_row(db, pid, key)
    blobs.delete(db, cfg, row.get("where"), f"notes/{_space_of(db, pid)}/{int(pid)}/{row['id']}")
    db.q("DELETE $r", r=R("note_file", row["id"]))


SUGGEST_CHARS = 20_000  # of a page's text looked through for things to link
SUGGEST_MAX = 8
_TOKEN = re.compile(r"[\w][\w'’.-]*")
_NOT_LINKED = ("TERM", "DATE", "NUMBER", "TIME", "MONEY", "PERCENT", "QUANTITY", "ORDINAL", "CARDINAL")


def suggest_links(db, p, limit=SUGGEST_MAX):
    """What page p's text names but doesn't link yet: the namespace's topics (by any of their labels) and its people,
    organisations, places and other named things (by name or alias), in the order the text names them. No model is
    asked: it's the namespace's own vocabulary matched against the words. [{target, label, kind, sign}]"""
    from . import analyze, topics

    text = (p.get("text") or plain(p.get("body") or ""))[:SUGGEST_CHARS]
    if not text.strip():
        return []
    sid = p["space"]
    have = {t for _, k, i, _ in mentions(p.get("body")) for t in [f"{k}:{i}"]} | ({p["about"]} if p.get("about") else set())
    by_key, by_term = topics._vocab(db, sid)
    tlabel = {t["id"]: t["label"] for t in db.rows("SELECT record::id(id) AS id, label FROM topic WHERE space = $s", s=sid)}
    ents = {}
    for e in db.rows(
        "SELECT record::id(id) AS id, name, type FROM entity WHERE space = $s AND type NOT IN $no AND hidden != true",
        s=sid,
        no=list(_NOT_LINKED),
    ):
        ents.setdefault(analyze.ent_key(e["name"]), (e["id"], e["name"]))
    names = {i: n for i, n in ents.values()}
    for a in db.rows("SELECT entity, key FROM entity_alias WHERE space = $s", s=sid):
        eid = int(str(a["entity"]).split(":")[-1]) if a.get("entity") is not None else None
        if eid in names and a.get("key"):
            ents.setdefault(a["key"], (eid, names[eid]))
    words = _TOKEN.findall(text)
    out, seen = [], set(have)
    for i in range(len(words)):
        for n in (4, 3, 2, 1):
            gram = " ".join(words[i : i + n]).strip(".-'’")
            if len(words[i : i + n]) < n or len(gram) < 3:
                continue
            hit = None
            tid = topics._named(by_key, by_term, gram)
            if tid and tid in tlabel:
                hit = {"target": f"topic:{tid}", "label": tlabel[tid], "kind": "topic", "sign": "#"}
            elif n > 1 or gram[:1].isupper():  # a single word names a thing only when written with a capital
                e = ents.get(analyze.ent_key(gram))
                if e:
                    hit = {"target": f"entity:{e[0]}", "label": e[1], "kind": "entity", "sign": "@"}
            if hit and hit["target"] not in seen:
                seen.add(hit["target"])
                out.append(hit)
                break
        if len(out) >= limit:
            break
    return out


HOMES = ("project", "area")  # where a note can be nested: under a page filed as a project or an area
HOMES_MAX = 3
_WORD = re.compile(r"[^\W\d_]{4,}")
_GENERIC = {"note", "notes", "page", "pages", "project", "projects", "area", "areas", "plan", "plans", "idea", "ideas"}


def _words(text):
    from .analyze import STOP

    return {w for w in (x.lower() for x in _WORD.findall(text or "")) if w not in STOP and w not in _GENERIC}


def suggest_homes(db, p, limit=HOMES_MAX):
    """The project or area pages a free note at the top of the tree could go inside, best first: the ones it links to,
    that link to it, that link the same topics and things, or whose title it names. No model is asked, and nothing
    moves until someone (or the assistant) moves it. Only unnested notes that aren't projects, areas or archived.
    [{page, title, place, score, why}]"""
    if p.get("about") or p.get("parent") is not None or p.get("place") in (*HOMES, "archive"):
        return []
    sid, pid = p["space"], int(p["id"])
    pages = db.rows("SELECT record::id(id) AS id, title, place, parent FROM note_page WHERE space = $s AND about = NONE", s=sid)
    up = {x["id"]: x.get("parent") for x in pages}

    def inside(x):  # x is p or somewhere under it
        seen = set()
        while x is not None and x not in seen:
            if x == pid:
                return True
            seen.add(x)
            x = up.get(x)
        return False

    homes = {x["id"]: x for x in pages if x.get("place") in HOMES and not inside(x["id"])}
    if not homes:
        return []
    mine = {f"{k}:{i}" for _, k, i, _ in mentions(p.get("body"))}
    theirs = {}
    for r in db.rows("SELECT page, target, label FROM note_link WHERE space = $s AND page IN $h", s=sid, h=list(homes)):
        theirs.setdefault(r["page"], {})[r["target"]] = r["label"]
    text = _words(f"{p.get('title') or ''} {plain(MENTION.sub(' ', p.get('body') or ''))[:SUGGEST_CHARS]}")  # links count once, as links
    out = []
    for hid, h in homes.items():
        links, score, why = theirs.get(hid, {}), 0, []
        if f"page:{hid}" in mine:
            score, why = score + 3, why + ["it links to this page"]
        if f"page:{pid}" in links:
            score, why = score + 2, why + ["this page links to it"]
        shared = sorted(t for t in mine & set(links) if not t.startswith("page:"))
        if shared:
            score += len(shared)
            why.append("both link " + ", ".join(links[t] for t in shared[:3]))
        named = _words(h["title"]) & text
        if named:
            score += min(len(named), 2)
            why.append("it names " + ", ".join(sorted(named)[:3]))
        if score >= 2:
            out.append({"page": hid, "title": h["title"], "place": h["place"], "score": score, "why": why})
    out.sort(key=lambda x: (-x["score"], HOMES.index(x["place"]), x["page"]))
    return out[:limit]


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
        name = {"recording": "title ?? path", "page": "title", "speaker": "name ?? label", "topic": "label"}.get(kind, "name")
        for r in db.rows(
            f"SELECT record::id(id) AS id, space, {name} AS name FROM {table} WHERE id IN $ids", ids=[R(table, k) for k in keys]
        ):
            out[f"{kind}:{r['id']}"] = {"name": r.get("name"), "space": r.get("space")}
    return out


def find(db, spaces, query="", place=None, limit=10):
    """Pages in these namespaces whose title, summary or text has the words, those with them in the title first, then
    the latest changed. Returns (how many matched, the first `limit` without their bodies)."""
    q = " ".join(str(query or "").split()).casefold()
    rows = db.rows(
        "SELECT record::id(id) AS id, space, title, summary, place, about, updated_at, text FROM note_page WHERE space IN $s",
        s=list(spaces),
    )
    hits = [
        r
        for r in rows
        if (not place or r.get("place") == place)
        and (not q or any(q in str(r.get(k) or "").casefold() for k in ("title", "summary", "text")))
    ]
    hits.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    hits.sort(key=lambda r: q not in str(r["title"]).casefold())
    return len(hits), [{k: v for k, v in r.items() if k != "text"} for r in hits[:limit]]


def search_targets(db, sid, q="", sign="@", limit=20):
    """What a mention can link to in namespace sid, best matches first: for #, topics (by any of their labels); for @,
    pages, recordings, people and other entities, collections and speakers."""
    q = " ".join(str(q or "").split()).casefold()
    hit = (lambda s: q in str(s or "").casefold()) if q else (lambda s: True)
    out = []
    if sign == "#":  # the namespace's vocabulary, found by any of a topic's labels
        for t in db.rows("SELECT record::id(id) AS id, label, alt FROM topic WHERE space = $s", s=sid):
            if any(hit(x) for x in [t["label"], *(t.get("alt") or [])]):
                out.append({"target": f"topic:{t['id']}", "label": t["label"], "kind": "topic"})
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


# ---------- refining titles and summaries (ai.refine_notes) ----------
QUIET_SECONDS = 120  # a page is refined once nobody has changed it for this long
REFINE_BATCH = 5  # pages refined per pass
REFINE_CHARS = 12_000  # of a page's text the model reads
REFINE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "summary": {"type": "string"}},
    "required": ["title", "summary"],
}
REFINE_SYSTEM = (
    "You keep a note's title and its one-line summary true to the whole note. The summary is the context an assistant "
    "reads before deciding whether to open the note, so it says what the note holds, specifically, in one sentence of "
    "at most 200 characters. Keep the title as it is unless it is empty, 'Untitled' or no longer describes the note; "
    "then give a short, specific title of at most 80 characters. Write in the note's language."
    ' Reply with JSON: {"title": ..., "summary": ...}.'
)


def _refs(p):
    """What the model calls made for a page count for in the activity ledger: the page and its namespace."""
    return (f"note_page:{p['id']}", f"space:{p['space']}")


def refine(db, cfg, pid):
    """Ask the model for the page's title and summary; saved unless someone changed the page meanwhile. Returns what
    changed: {"title"?, "summary"?}."""
    from . import llm

    p = db.one("SELECT record::id(id) AS id, space, title, summary, text, updated_at FROM $r", r=R("note_page", int(pid)))
    if not p:
        raise KeyError(pid)
    if not (p.get("text") or "").strip():
        db.q("UPDATE $r SET refine_pending = false, refine_claim = NONE", r=R("note_page", p["id"]))
        return {}
    user = f"Title: {p['title']}\nSummary: {p.get('summary') or '(none)'}\n\nNote:\n{p['text'][:REFINE_CHARS]}"
    with activity.scope(db, *_refs(p), cfg=cfg):
        out = llm.json_out(cfg, REFINE_SYSTEM, user, REFINE_SCHEMA)
    title = " ".join(str(out.get("title") or "").split())[:TITLE_MAX] or p["title"]
    summary = " ".join(str(out.get("summary") or "").split())[:SUMMARY_MAX] or p.get("summary")
    now = db.one("SELECT updated_at FROM $r", r=R("note_page", p["id"]))
    if not now or now.get("updated_at") != p.get("updated_at"):
        db.q("UPDATE $r SET refine_claim = NONE", r=R("note_page", p["id"]))
        return {}  # changed while the model was thinking: it's still pending, so a later pass looks again
    changed = {k: v for k, v in (("title", title), ("summary", summary)) if v and v != p.get(k)}
    sets = ["refine_pending = false", "refine_claim = NONE", "refined_at = $t"] + [f"{k} = ${k}" for k in changed]
    if changed:
        snapshot(db, get(db, p["id"]), None, "assistant", "the model refined the title and summary")
    if "summary" in changed:
        sets.append("summary_by = 'assistant'")
    db.q(f"UPDATE $r SET {', '.join(sets)}", r=R("note_page", p["id"]), t=store.now(), **changed)
    return changed


def refine_due(db, cfg, log=None):
    """Refine the pages whose title or text changed since they were last refined and that nobody has touched for a
    while (so a page being typed isn't sent on every keystroke). Off with ai.refine_notes, or without a model."""
    from . import llm

    if not cfg["ai"].get("refine_notes", True) or not llm.configured(cfg):
        return 0
    t = dt.datetime.now(dt.timezone.utc)
    quiet = (t - dt.timedelta(seconds=QUIET_SECONDS)).isoformat(timespec="seconds")
    rows = db.rows(
        "SELECT record::id(id) AS id, refine_claim FROM note_page WHERE refine_pending = true AND updated_at < $q LIMIT $n",
        q=quiet,
        n=REFINE_BATCH * 4,
    )
    done = 0
    for p in rows:
        if done >= REFINE_BATCH:
            break
        if (p.get("refine_claim") or "") > quiet:
            continue  # another process took it a moment ago
        db.q("UPDATE $r SET refine_claim = $c", r=R("note_page", p["id"]), c=store.now())
        try:
            refine(db, cfg, p["id"])
        except Exception as e:  # noqa: BLE001 - one page failing doesn't stop the others
            if log:
                log(f"notes: couldn't refine page {p['id']}: {type(e).__name__}: {e}")
            # not again until it changes
            db.q("UPDATE $r SET refine_pending = false, refine_claim = NONE", r=R("note_page", p["id"]))
        done += 1
    return done


# ---------- filing notes in PARA (ai.organise_notes) ----------
PARA = {
    "project": "Work toward an outcome with an end: a goal, a deliverable, something with a deadline.",
    "area": "A responsibility kept up over time with no end date: health, a team, a home, finances.",
    "resource": "A topic or interest kept for reference: research, how-tos, ideas, people, things to know.",
    "archive": "Done, cancelled or no longer active: kept only for the record.",
}
FILE_BATCH = 10


def file_due(db, cfg, log=None):
    """File free notes nobody has filed yet in PARA, once their summary is written: a routine decision (a decision
    model when one is set up, else the language model). Sure enough (decisions.act_above), it's filed; otherwise the
    best guess waits on the page as a suggestion. Off with ai.organise_notes."""
    from . import decide

    if not cfg["ai"].get("organise_notes", True) or not decide.engine(cfg):
        return 0
    rows = db.rows(
        "SELECT record::id(id) AS id, space, title, summary, text, updated_at FROM note_page "
        "WHERE about = NONE AND filed != true AND refine_pending != true LIMIT $n",
        n=FILE_BATCH,
    )
    done = 0
    for p in rows:
        state = {"title": p["title"], "summary": p.get("summary"), "text": (p.get("text") or "")[:4000]}
        try:
            with activity.scope(db, *_refs(p), cfg=cfg):
                d = decide.choose(cfg, "Where does this note belong in PARA (projects, areas, resources, archives)?", PARA, state)
        except decide.Undecided as e:
            if log:
                log(f"notes: couldn't file page {p['id']}: {e}")
            return done
        now = db.one("SELECT updated_at, filed FROM $r", r=R("note_page", p["id"]))
        if not now or now.get("filed") or now.get("updated_at") != p.get("updated_at"):
            continue  # someone filed or changed it meanwhile
        if decide.sure(cfg, d):
            db.q(
                "UPDATE $r SET place = $c, place_by = 'assistant', place_suggestion = NONE, filed = true",
                r=R("note_page", p["id"]),
                c=d["choice"],
            )
        else:
            db.q(
                "UPDATE $r SET place_suggestion = $s, filed = true",
                r=R("note_page", p["id"]),
                s={"place": d["choice"], "confidence": round(d["confidence"], 2), "by": d["by"]},
            )
        done += 1
    return done


def organise_due(db, cfg, log=None):
    """The notes pass the scheduler runs: titles and summaries first, then filing."""
    return refine_due(db, cfg, log) + file_due(db, cfg, log)
