"""Files people add through the file shares (docs/file-tree.md#adding-files): a file written into the tree over S3,
WebDAV, SMB or NFS is imported as if it had been uploaded in the web app (uploads.take): the same checks of its type
and size, the namespace's pipeline, and the same file twice isn't added twice.

Where it's written decides where it goes:

    <namespace>/Collections/<collection>/<inside it>/<file>   that collection (made, with any above it, when missing)
    <namespace>/<file>, <namespace>/Collections/<file>        the namespace's default collection
    <namespace>/Topics/…/<file>, <namespace>/Entities/…/<file> the default collection too (topics and entities come
                                                               from the analysis)

It must be the person's to add to: the import permission in the namespace, or an editor's or admin's role on the
collection, as an upload in the web app. Making a collection takes what making one in the web app takes. Files Lens
made (an asset's own file, its .json, .txt and .pdf) can't be written over.

A client that has just written a file looks for it under the name it gave, which the tree doesn't have (the asset
shows as `<title> [<id>]`). So the name stays in its folder, for the person who wrote it, for ARRIVED_SECONDS,
holding the asset's own file. Files an operating system or an app writes for itself (.DS_Store, ._ files, Thumbs.db,
desktop.ini, Office lock files, `.part` and `.tmp` files, anything hidden), and empty files (Finder and Explorer make
one before they write it) are kept aside instead, on the uploads disk for PENDING_SECONDS after they last changed,
and never imported; renaming one to an ordinary name imports it then (an upload under a temporary name).

Each server process keeps these in memory (they last minutes); it's the person's own view, nobody else sees them.
"""

from __future__ import annotations

import datetime as dt
import mimetypes
import os
import pathlib
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from . import auth, filetree, hierarchy, store, uploads

ARRIVED_SECONDS = 600  # how long a file's own name stays, after it was imported
PENDING_SECONDS = 600  # how long a file kept aside stays after it last changed
SWEEP_SECONDS = 60
PROTOCOLS = ("s3", "webdav", "smb", "nfs")
# what an operating system or an app writes for itself, never imported: hidden files (.DS_Store, ._ AppleDouble,
# .~lock files), Office's ~$ owner files, Windows' Thumbs.db and desktop.ini, the Mac's Icon\r, NTFS streams (a:b),
# and files still arriving under a temporary name
JUNK = re.compile(
    r"^(?:\..*|~\$.*|thumbs\.db|ehthumbs(?:_vista)?\.db|desktop\.ini|icon\r|.*:.*"
    r"|.*\.(?:tmp|temp|part|partial|filepart|crdownload|download|swp|swx|lock))$",
    re.IGNORECASE | re.DOTALL,
)


class Refused(PermissionError):
    """Not theirs to add to, or no file can go there; says why."""


class Rejected(ValueError):
    """A file that can't be imported (its type, its size...); says why."""


class TooLarge(Rejected):
    """Over uploads.max_mb."""


def accepting(cfg, protocol):
    """Whether a share takes files people add: shares.add_files for S3 and WebDAV, smb.add_files, nfs.add_files."""
    section = "shares" if protocol in ("s3", "webdav") else protocol
    return bool({**store.DEFAULTS[section], **(cfg.get(section) or {})}.get("add_files"))


def junk(name):
    """Whether a file name is one an operating system or an app writes for itself (JUNK)."""
    return bool(JUNK.match(name or ""))


def spool(cfg):
    """A new file name on the uploads disk for a file to arrive in (not made yet); take() moves it from there."""
    return uploads.incoming(cfg) / f"share-{secrets.token_hex(12)}"


# ---------- what each person has just added ----------
@dataclass
class Item:
    kind: str  # "arrived" (imported: `recording`) or "pending" (kept aside: `path`)
    expires: float
    recording: int | None = None
    path: str | None = None
    modified: str | None = None
    md5: str | None = None  # as the client sent it
    mtime: str | None = None  # the time the client gave it, as it wrote it (seconds since 1970)
    holds: int = 0  # writers that have it open (SMB): it doesn't expire meanwhile


@dataclass
class Registry:
    """{(account, folder's parts): {name: Item}}, in memory, guarded by a lock."""

    items: dict[tuple[int, tuple[str, ...]], dict[str, Item]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    swept: float = 0.0

    def folder(self, account, folder):
        self.prune()
        with self.lock:
            return dict(self.items.get((account, tuple(folder)), {}))

    def get(self, account, folder, name):
        return self.folder(account, folder).get(name)

    def put(self, account, folder, name, item):
        with self.lock:
            old = self.items.setdefault((account, tuple(folder)), {}).get(name)
            self.items[(account, tuple(folder))][name] = item
        if old and old.kind == "pending" and old.path and old.path != item.path:
            _remove(old.path)

    def pop(self, account, folder, name):
        with self.lock:
            got = self.items.get((account, tuple(folder)), {}).pop(name, None)
            if not self.items.get((account, tuple(folder)), True):
                self.items.pop((account, tuple(folder)), None)
        return got

    def hold(self, account, folder, name, by):
        with self.lock:
            it = self.items.get((account, tuple(folder)), {}).get(name)
            if it is not None:
                it.holds = max(0, it.holds + by)
                it.expires = time.monotonic() + PENDING_SECONDS

    def prune(self):
        now = time.monotonic()
        gone = []
        with self.lock:
            for key, names in list(self.items.items()):
                for name, it in list(names.items()):
                    if it.expires < now and not it.holds:
                        gone.append(names.pop(name))
                if not names:
                    self.items.pop(key)
        for it in gone:
            if it.kind == "pending" and it.path:
                _remove(it.path)

    def paths(self):
        with self.lock:
            return {it.path for names in self.items.values() for it in names.values() if it.path}


REGISTRY = Registry()


def _remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def sweep(cfg):
    """Files kept aside that this process no longer knows (it restarted) go from the uploads disk."""
    REGISTRY.prune()
    if time.monotonic() - REGISTRY.swept < SWEEP_SECONDS:
        return
    REGISTRY.swept = time.monotonic()
    keep, cut = REGISTRY.paths(), time.time() - 2 * max(PENDING_SECONDS, ARRIVED_SECONDS)
    for p in uploads.incoming(cfg).glob("share-*"):
        try:
            if str(p) not in keep and p.stat().st_mtime < cut:
                p.unlink()
        except OSError:
            pass


def _seconds(t):
    """A time in seconds since 1970 (a number or its text) that's believable, or None."""
    try:
        t = float(t) if t is not None else None
    except ValueError:
        return None
    return t if t is not None and 0 < t < time.time() + 86400 else None


def _iso(t=None):
    return dt.datetime.fromtimestamp(t if t is not None else time.time(), dt.timezone.utc).isoformat(timespec="seconds")


def overlay(tree, parts, taken):
    """The files someone just added to a folder, as the tree shows them to them: named as they gave them, beside the
    folder's own files (`taken`: their names, which win)."""
    items = REGISTRY.folder(tree.viewer.account, parts)
    if not items:
        return []
    out = []
    arrived = {it.recording for it in items.values() if it.kind == "arrived"}
    recs = (
        {
            r["id"]: r
            for r in tree.db.rows(
                f"SELECT {filetree.REC_FIELDS} FROM recording WHERE id IN $ids", ids=[filetree.R("recording", int(x)) for x in arrived]
            )
        }
        if arrived
        else {}
    )
    for name, it in items.items():
        if name in taken:
            continue
        if it.kind == "arrived":
            rec = recs.get(it.recording)
            own = tree._primary(rec) if rec else None
            if own is None:
                continue
            own.name, own.added, own.md5, own.mtime = name, "arrived", it.md5, it.mtime
            out.append(own)
        elif it.path and os.path.isfile(it.path):
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            out.append(filetree.Entry(name, False, os.path.getsize(it.path), it.modified, ctype, path=it.path, added="pending",
                                      md5=it.md5, mtime=it.mtime))  # fmt: skip
    return out


# ---------- adding ----------
@dataclass
class Place:
    """Where a file is being written: its namespace, the collection it goes into (`collection`; None: the default),
    or the collections still to make for it (`missing`, inside `parent`), its folder's parts and its name."""

    space: int
    namespace: str
    folder: tuple[str, ...]
    name: str
    collection: int | None = None
    parent: int | None = None
    missing: list[str] = field(default_factory=list)


class Adder:
    """One person adding files to the tree over one protocol (PROTOCOLS). `account`: their account row ({id, email,
    admin}); `tree`: their view of the tree, when the server has one already."""

    def __init__(self, db, cfg, account, protocol, tree=None):
        self.db, self.cfg, self.account, self.protocol = db, cfg, account, protocol
        self.tree = tree or filetree.Tree(db, cfg, filetree.Viewer.of(db, account))
        self.viewer: filetree.Viewer = self.tree.viewer
        sweep(cfg)

    # ---------- where ----------
    def place(self, path, folder=False):
        """Where a file (or, `folder`, a folder) at `path` goes. NotFound for a namespace or folder this person doesn't
        see; Refused for a place nothing can be added."""
        parts = filetree.Tree.split(path)
        if len(parts) < 2:
            raise Refused("files go inside a namespace" if not folder else "namespaces are made in the web app")
        tree = self.tree
        sid = tree._space(parts[0])
        *up, name = parts
        if name in (".", "..") or len(name.encode()) > 255:
            raise Refused("that name can't be used")
        top = up[1] if len(up) > 1 else None
        where = Place(sid, parts[0], tuple(up), name)
        if folder:
            if top != filetree.COLLECTIONS:
                raise Refused("folders can be made inside Collections only: each one is a collection")
            self._walk(where, up[2:] + [name])
            return where
        if top == filetree.COLLECTIONS:
            self._walk(where, up[2:])
        elif top is not None and not self._view_folder(sid, top, up[2:]):
            raise filetree.NotFound(path)
        return where

    def _view_folder(self, sid, top, names):
        """Whether Topics/… or Entities/… names a folder this person sees."""
        tree = self.tree
        if top == filetree.TOPICS:
            tid = None
            for n in names[: filetree.TOPIC_DEPTH + 1]:
                kids = tree._narrower(sid, tid)
                if n not in kids:
                    return False
                tid = kids[n]
            return len(names) <= filetree.TOPIC_DEPTH
        if top == filetree.ENTITIES:
            if not names:
                return True
            types = tree._types(sid)
            if names[0] not in types or len(names) > 2:
                return False
            return len(names) == 1 or names[1] in tree._entities(sid, types[names[0]])
        return False

    def _walk(self, where, names):
        """Follow collection folders down from the top: the collection they reach, or the deepest one there is and the
        names still to make inside it."""
        cid = None
        for i, n in enumerate(names):
            kids = self.tree._children(where.space, cid)
            hit = kids.get(n) or next((c for k, c in kids.items() if k.casefold() == n.casefold()), None)
            if hit is None:
                where.parent, where.missing = cid, list(names[i:])
                return
            cid = hit["id"]
        where.collection = cid

    # ---------- may they ----------
    def may_import(self, sid, cid):
        """The import permission in the namespace, or an editor's or admin's role on the collection."""
        if "import" in self.viewer.perms.get(sid, ()):
            return True
        role = self.viewer.collections.get(sid, {}).get(cid) if cid is not None else None
        return hierarchy.ROLES.get(role or "", 0) >= auth.ROLES["editor"]

    def may_make(self, sid, parent):
        """Editors of the namespace make collections anywhere in it; an admin of a collection, inside it."""
        return auth.allows(self.viewer.roles, sid, "editor") or (
            parent is not None and self.viewer.collections.get(sid, {}).get(parent) == "admin"
        )

    def _target(self, where):
        return where.collection if where.collection is not None or where.missing else store.default_collection(self.db, where.space)

    def check(self, path, size=None):
        """Whether this person may write a file at `path` (of `size` bytes, when known), before anything is sent:
        the Place, or NotFound, Refused or Rejected."""
        where = self.place(path)
        self._not_lens(where)
        if where.missing:
            if not self.may_make(where.space, where.parent):
                raise Refused("making a collection needs editor access to the namespace, or admin of the collection it goes in")
            if len(where.missing) + len(hierarchy.path(self.db, where.parent) if where.parent else []) > hierarchy.MAX_DEPTH:
                raise Rejected(f"collections go at most {hierarchy.MAX_DEPTH} deep")
        elif not self.may_import(where.space, self._target(where)):
            raise Refused("adding files here needs the import permission (an editor's role)")
        if not junk(where.name) and size != 0:  # an empty file is kept aside: its bytes may follow (Finder, Explorer)
            try:
                uploads.check(self.cfg, where.name, size if size is not None else 1)  # no size yet: its type
            except uploads.TooLarge as e:
                raise TooLarge(str(e)) from None
            except ValueError as e:
                raise Rejected(str(e)) from None
        return where

    def _not_lens(self, where):
        """Files Lens made, and folders, can't be written over. Lens's files are all named `<title> [<id>].<ext>`."""
        if filetree.ASSET_ID.search(where.name):
            try:
                e = self.tree.stat("/".join([*where.folder, where.name]))
            except filetree.NotFound:
                return
            if not e.added:
                raise Refused("Lens made this file: it can't be written over")
            return
        top = where.folder[1] if len(where.folder) > 1 else None
        if top is None:
            folders = {filetree.COLLECTIONS, filetree.ENTITIES, filetree.TOPICS}
        elif top == filetree.COLLECTIONS and not where.missing:
            folders = set(self.tree._children(where.space, where.collection if len(where.folder) > 2 else None))
        else:
            folders = set()
        if where.name in folders:
            raise Refused("that's a folder")

    def _make(self, where):
        """The collections still missing on the way to `where`; the one it reaches."""
        cid = where.parent
        for n in where.missing:
            try:
                cid = hierarchy.create(self.db, where.space, n, cid, by=self.account.get("email"))
            except ValueError as e:
                kids = self.tree._children(where.space, cid)
                hit = next((c for k, c in kids.items() if k.casefold() == n.casefold()), None)  # made meanwhile
                if hit is None:
                    raise Rejected(str(e)) from None
                cid = hit["id"]
            else:
                auth.audit(self.db, self._by(), "collection.create", f"collection:{cid}", {"name": n, "namespace": where.namespace,
                           "parent": where.parent, "via": self.protocol})  # fmt: skip
            self.tree.forget()
        where.collection, where.missing = cid, []
        return cid

    def _by(self):
        return {"id": self.account["id"], "email": self.account.get("email")}

    # ---------- doing it ----------
    def folder(self, path):
        """Make the collection a folder at `path` stands for (and any above it); whether one was made."""
        where = self.place(path, folder=True)
        if not where.missing:
            return False
        if not self.may_make(where.space, where.parent):
            raise Refused("making a collection needs editor access to the namespace, or admin of the collection it goes in")
        self._make(where)
        return True

    def touch(self, path, modified):
        """Set the time of a file just added (an S3 client copying it onto itself with new metadata, an SMB client
        setting it); False when it isn't one. A file kept aside is imported with it."""
        parts = filetree.Tree.split(path)
        if not parts or _seconds(modified) is None:
            return False
        with REGISTRY.lock:
            it = REGISTRY.items.get((self.account["id"], tuple(parts[:-1])), {}).get(parts[-1])
            if it is None:
                return False
            it.mtime = str(modified)
            if it.kind == "pending":
                it.modified = _iso(_seconds(modified))
        return True

    def put(self, path, src, modified=None, md5=None):
        """The file at `src` (on the uploads disk: spool(); it's moved away) was written at `path`: import it, or keep it
        aside (junk or empty). `modified`: the file's own time in seconds since 1970 (a number, or the text the client
        sent, which is shown back to it as it was), when the client said; `md5`: its MD5 (hex), when the server worked
        it out. Returns {kind: "arrived", recording, duplicate} or {kind: "pending"}."""
        src = pathlib.Path(src)
        stamp = str(modified) if modified is not None else None
        modified = _seconds(modified)
        where = self.check(path, src.stat().st_size)
        key = (self.account["id"], where.folder, where.name)
        if junk(where.name) or src.stat().st_size == 0:
            if modified:
                os.utime(src, (modified, modified))
            item = Item("pending", time.monotonic() + PENDING_SECONDS, path=str(src), modified=_iso(modified), md5=md5, mtime=stamp)
            REGISTRY.put(*key, item)
            return {"kind": "pending"}
        # check() saw they may import there; whoever may make a collection may import into it
        cid = self._make(where) if where.missing else self._target(where)
        try:
            up = uploads.take(self.db, self.cfg, where.namespace, str(src), where.name, self._by(), collection=cid,
                              modified=int(modified * 1000) if modified else None)  # fmt: skip
        except uploads.TooLarge as e:
            raise TooLarge(str(e)) from None
        except ValueError as e:
            raise Rejected(str(e)) from None
        finally:
            _remove(src)  # take() moved it unless it refused it
        self.tree.forget()
        rid = up.get("recording")
        REGISTRY.put(*key, Item("arrived", time.monotonic() + ARRIVED_SECONDS, recording=rid, md5=md5, mtime=stamp))
        auth.audit(self.db, self._by(), "share.import", f"recording:{rid}",
                   {"via": self.protocol, "path": "/".join([*where.folder, where.name]), "duplicate": bool(up.get("duplicate"))})  # fmt: skip
        return {"kind": "arrived", "recording": rid, "duplicate": bool(up.get("duplicate"))}

    def pending(self, path):
        """A file kept aside at `path` for this person: its Item, or None."""
        parts = filetree.Tree.split(path)
        if not parts:
            return None
        it = REGISTRY.get(self.account["id"], tuple(parts[:-1]), parts[-1])
        return it if it and it.kind == "pending" else None

    def delete(self, path):
        """Delete a file kept aside (an app tidying up after itself); everything else is Refused."""
        parts = filetree.Tree.split(path)
        it = self.pending(path)
        if it is None:
            raise Refused("files can be added here, not deleted")
        REGISTRY.pop(self.account["id"], tuple(parts[:-1]), parts[-1])
        if it.path:
            _remove(it.path)

    def move(self, src, dst):
        """Rename a file kept aside: to an ordinary name, it's imported then (once it's closed, when it's still being
        written: finish). Everything else is Refused."""
        it = self.pending(src)
        if it is None or not it.path:
            raise Refused("files can be added here, not moved or renamed")
        parts = filetree.Tree.split(src)
        if it.holds:
            where = self.check(dst)
            REGISTRY.pop(self.account["id"], tuple(parts[:-1]), parts[-1])
            REGISTRY.put(self.account["id"], where.folder, where.name, it)
            return {"kind": "pending"}
        moving = spool(self.cfg)
        os.replace(it.path, moving)
        REGISTRY.pop(self.account["id"], tuple(parts[:-1]), parts[-1])
        try:
            return self.put(dst, moving, os.stat(moving).st_mtime)
        except BaseException:
            if moving.exists():  # back where it was, so the client can try again
                os.replace(moving, it.path)
                REGISTRY.put(self.account["id"], tuple(parts[:-1]), parts[-1], it)
            raise

    # ---------- written in place (SMB) ----------
    def begin(self, path):
        """A file a client makes at `path` and writes in place (SMB), its size not known yet: checked as check()
        checks it, then a new, empty file on the uploads disk (returned) that shows under its name, kept aside and
        held, until finish()."""
        where = self.check(path)
        src = spool(self.cfg)
        src.touch(mode=0o600)
        item = Item("pending", time.monotonic() + PENDING_SECONDS, path=str(src), modified=_iso(), holds=1)
        REGISTRY.put(self.account["id"], where.folder, where.name, item)
        return src

    def reopen(self, path):
        """A file kept aside at `path`, opened to write again: its file on the uploads disk, held until finish()."""
        it = self.pending(path)
        if it is None or not it.path:
            raise Refused("files can be added here, not changed")
        parts = filetree.Tree.split(path)
        REGISTRY.hold(self.account["id"], tuple(parts[:-1]), parts[-1], 1)
        return pathlib.Path(it.path)

    def _held(self, path, src):
        it = self.pending(path)
        if it is None or it.path != str(src):
            return None  # deleted, or written over by another file, meanwhile
        parts = filetree.Tree.split(path)
        REGISTRY.hold(self.account["id"], tuple(parts[:-1]), parts[-1], -1)
        return it

    def finish(self, path, src):
        """A file written in place (begin, reopen) at `path` was closed: it's imported now when it's an ordinary file
        with something in it and nobody else has it open; else it stays kept aside. put()'s answer, or None when it
        was deleted or written over meanwhile."""
        it = self._held(path, src)
        parts = filetree.Tree.split(path)
        if it is None:
            return None
        if it.holds or junk(parts[-1]) or os.path.getsize(src) == 0:
            return {"kind": "pending"}
        REGISTRY.pop(self.account["id"], tuple(parts[:-1]), parts[-1])  # put() files it again
        try:
            return self.put(path, src, it.mtime)
        except BaseException:
            if os.path.exists(src):  # kept aside again: the client can delete it, or rename it and try again
                REGISTRY.put(self.account["id"], tuple(parts[:-1]), parts[-1], it)
            raise

    def abandon(self, path, src):
        """A file written in place that won't be finished (its client went away, or deleted it as it closed it): it
        stays kept aside, not imported, until it expires or is deleted."""
        self._held(path, src)
