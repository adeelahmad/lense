"""The graph as folders and files (docs/file-tree.md): what the S3, WebDAV, SMB and NFS servers serve, and the API browses.

Graph nodes are folders, nested along the graph's links, with assets' files inside them:

    /<namespace>/
        Collections/<collection>/<collection inside it>/…   every asset in its collection's folder
        Topics/<topic>/<narrower topic>/…                   the assets about each topic (accepted ones)
        Entities/<type>/<name>/                             the assets that mention an entity

An asset (a recording, document or image) shows as up to four files named after it, `<title> [<id>]`: its own file
(`.mp3`, `.pdf`, …), `.json` beside it with its metadata (Dublin Core terms, its collection, topics and entities, the
description saved on it), `.txt` with its text (the transcript, or what was read off its pages), and `.pdf` for a
document converted to one. An own file that is itself `.txt` or `.json` (an imported transcript) is
`<title> [<id>].original.txt`. The same asset shows in every folder it belongs to; a topic with several broader
topics shows under each.

What someone sees is what the web app shows them: the namespaces they have a role in (admins: all of them), and in a
namespace where they only have roles on some collections, those collections and the topics and entities of the
assets in them. The tree only reads; names are made safe for every file system (no `/ \\ : * ? " < > |`) and unique
in their folder, ignoring case.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import pathlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from . import auth, convert, documents, entity_setup, hierarchy, keyring, metadata, render, store, video

R = store.R
NAME_MAX = 120
CHUNK = 1 << 16
ASSET_ID = re.compile(r" \[(\d+)\](?:\.[^.\[\]]+)+$")
COLLECTIONS, TOPICS, ENTITIES = "Collections", "Topics", "Entities"
TOPIC_DEPTH = 32  # topics can't loop (topics._would_loop), but a path stops here anyway
UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
REC_FIELDS = (
    "record::id(id) AS id, space, collection, title, path, source, remote, size, media, status, duration_ms, created_at, "
    "updated_at, analyzed_at, recorded_at, meta_json, access, access_parts, featured, fields"
)


class NotFound(KeyError):
    """No such file or folder, or not one this person may see."""


class NotAFolder(ValueError):
    """A file where a folder was asked for."""


@dataclass(frozen=True)
class Viewer:
    """Who is looking: an admin sees every namespace; others their namespaces (`roles`) and, in namespaces they have
    no role in, the collections they were given a role on, with the ones inside them (`collections`)."""

    admin: bool = False
    roles: dict[int, str] = field(default_factory=dict)
    collections: dict[int, dict[int, str]] = field(default_factory=dict)

    @classmethod
    def of(cls, db, account: dict[str, Any]) -> Viewer:
        """For an account row ({id, admin})."""
        admin = bool(account.get("admin"))
        return cls(admin, auth.roles(db, account), {} if admin else hierarchy.roles_of(db, account["id"]))

    def spaces(self, db) -> dict[int, set[int] | None]:
        """{namespace: None (all of it) or the collections seen}."""
        out: dict[int, set[int] | None] = {}
        for sid, cols in self.collections.items():
            if cols:
                out[sid] = set(cols)
        for sid in self.roles:
            out[sid] = None
        return out


@dataclass
class Entry:
    """A file or a folder. A file's bytes come from `data` (made here) or `path` (stored, maybe encrypted) or a storage
    source (`remote`: {source, path, space})."""

    name: str
    folder: bool
    size: int = 0
    modified: str | None = None
    content_type: str | None = None
    data: bytes | None = None
    path: str | None = None
    encrypted: bool = False
    remote: dict[str, Any] | None = None

    @property
    def version(self) -> str:
        """Changes when the file does (for ETags): its name, size and time."""
        return hashlib.sha256(f"{self.name}\0{self.size}\0{self.modified}".encode()).hexdigest()[:24]

    def out(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "folder": self.folder, "modified": self.modified}
        if not self.folder:
            d.update(size=self.size, content_type=self.content_type)
        return d

    def read(self, db, cfg, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        """The bytes from `start` to `end` (inclusive; None: to the end). A locked vault raises keyring.Locked."""
        if self.folder:
            raise NotAFolder(self.name)
        end = self.size - 1 if end is None else min(end, self.size - 1)
        if end < start:
            return iter(())
        if self.data is not None:
            return iter([self.data[start : end + 1]])
        if self.remote is not None:
            from . import sources

            return sources.stream(db, cfg, self.remote["source"], self.remote["path"], start, end - start + 1, self.remote.get("space"))
        return self._file(db, cfg, start, end)

    def _file(self, db, cfg, start: int, end: int) -> Iterator[bytes]:
        assert self.path is not None
        f = keyring.Reader(db, cfg, self.path) if self.encrypted else open(self.path, "rb")  # noqa: SIM115 - closed below
        with f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk


def clean(name: Any) -> str:
    """A name any file system takes: no separators or reserved characters, no trailing dots or spaces, not too long."""
    s = UNSAFE.sub("_", " ".join(str(name or "").split()))[:NAME_MAX].rstrip(". ")
    return s if s and s not in (".", "..") else "_"


def _unique(items: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """Names made unique in their folder, ignoring case: a repeat gets its key added, ` [key]`."""
    seen: set[str] = set()
    out = []
    for name, key in sorted(items, key=lambda x: (x[0].casefold(), str(x[1]))):
        n = name
        if n.casefold() in seen:
            n = f"{name} [{key}]"
        seen.add(n.casefold())
        out.append((n, key))
    return out


def _json(d: Any) -> bytes:
    return (json.dumps(d, ensure_ascii=False, indent=2, default=str) + "\n").encode()


def _when(rec: dict[str, Any]) -> str | None:
    return rec.get("updated_at") or rec.get("analyzed_at") or rec.get("created_at")


def _folder(name: str, modified: str | None = None) -> Entry:
    return Entry(name, True, modified=modified)


def _type_of(name: str, kind: str) -> str:
    ext = pathlib.PurePosixPath(name).suffix.lower()
    return (
        {"video": video.VIDEO_TYPES.get(ext), "audio": render.AUDIO_TYPES.get(ext)}.get(kind)
        or documents.content_type(name)
        or mimetypes.guess_type(name)[0]
        or "application/octet-stream"
    )


class Tree:
    """One person's view of the tree, for one request: list folders, look files up, read them."""

    def __init__(self, db, cfg, viewer: Viewer):
        self.db, self.cfg, self.viewer = db, cfg, viewer
        self._spaces = viewer.spaces(db)
        self._names = {sid: n for sid, n in store.space_names(db).items() if sid in self._spaces}
        self._cols: dict[int, list[dict[str, Any]]] = {}
        self._only: int | None = None
        self._seen: dict[int, set[int]] = {}
        self._topic_rows: dict[int, list[dict[str, Any]]] = {}

    # ---------- walking ----------
    @staticmethod
    def split(path: str) -> list[str]:
        return [p for p in str(path or "").split("/") if p]

    def list(self, path: str) -> list[Entry]:
        """A folder's files and folders, by name; NotFound, or NotAFolder for a file."""
        try:
            return self._list(self.split(path))
        except NotFound:
            if not self.stat(path).folder:  # NotFound again when there's nothing there at all
                raise NotAFolder(path) from None
            raise

    def _list(self, parts: list[str]) -> list[Entry]:
        if not parts:
            return [_folder(n) for n in sorted(self._names.values(), key=str.casefold)]
        sid = self._space(parts[0])
        rest = parts[1:]
        if not rest:
            return [_folder(COLLECTIONS), _folder(ENTITIES), _folder(TOPICS)]
        head, rest = rest[0], rest[1:]
        if head == COLLECTIONS:
            return self._list_collection(sid, rest)
        if head == TOPICS:
            return self._list_topic(sid, rest)
        if head == ENTITIES:
            types = self._types(sid)
            if not rest:
                return [_folder(n) for n in types]
            if rest[0] in types:
                ents = self._entities(sid, types[rest[0]])
                if len(rest) == 1:
                    return [_folder(n) for n in ents]
                if len(rest) == 2 and rest[1] in ents:
                    return self._assets(self._entity_recordings(sid, ents[rest[1]]))
        raise NotFound("/".join(parts))

    def stat(self, path: str) -> Entry:
        """One file or folder; NotFound when there's none (or it isn't this person's to see)."""
        parts = self.split(path)
        if not parts:
            return _folder("")
        self._space(parts[0])
        if len(parts) == 1:
            return _folder(parts[0])
        *up, name = parts
        m = ASSET_ID.search(name)
        self._only = int(m.group(1)) if m else None  # one asset's files: don't make the whole folder's
        try:
            listing = self._list(up)
        except NotFound:
            raise NotFound(path) from None
        finally:
            self._only = None
        for e in listing:
            if e.name == name:
                return e
        raise NotFound(path)

    def walk(self, path: str = "") -> Iterator[tuple[str, Entry]]:
        """Every file under a folder, depth first: (its path from the root, the file)."""
        base = "/".join(self.split(path))
        for e in self.list(base):
            p = f"{base}/{e.name}" if base else e.name
            if e.folder:
                yield from self.walk(p)
            else:
                yield p, e

    def _space(self, name: str) -> int:
        for sid, n in self._names.items():
            if n == name:
                return sid
        raise NotFound(name)

    # ---------- namespaces and collections ----------
    def _collections(self, sid: int) -> list[dict[str, Any]]:
        if sid not in self._cols:
            rows = hierarchy.of_space(self.db, sid)
            seen = self._spaces[sid]
            self._cols[sid] = [c for c in rows if seen is None or c["id"] in seen]
        return self._cols[sid]

    def _children(self, sid: int, parent: int | None) -> dict[str, dict[str, Any]]:
        """The collections in a collection (None: at the top of what this person sees), by their names here."""
        rows = self._collections(sid)
        known = {c["id"] for c in rows}
        if parent is None:
            kids = [c for c in rows if c.get("parent") is None or c["parent"] not in known]
        else:
            kids = [c for c in rows if c.get("parent") == parent]
        by_id = {c["id"]: c for c in kids}
        return {n: by_id[cid] for n, cid in _unique([(clean(c["name"]), c["id"]) for c in kids])}

    def _list_collection(self, sid: int, names: list[str]) -> list[Entry]:
        cid = None
        for n in names:
            kids = self._children(sid, cid)
            if n not in kids:
                raise NotFound(n)
            cid = kids[n]["id"]
        out = [_folder(n, c.get("updated_at") or c.get("created_at")) for n, c in self._children(sid, cid).items()]
        if cid is not None:
            out += self._assets(self.db.rows(f"SELECT {REC_FIELDS} FROM recording WHERE collection = $c", c=cid))
        return sorted(out, key=lambda e: e.name.casefold())

    def _visible(self, sid: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = self._spaces.get(sid)
        return [r for r in rows if r.get("space") == sid and (seen is None or r.get("collection") in seen)]

    def _recordings(self, sid: int, rids: list[int]) -> list[dict[str, Any]]:
        if not rids:
            return []
        rows = self.db.rows(f"SELECT {REC_FIELDS} FROM recording WHERE id IN $ids", ids=[R("recording", int(r)) for r in set(rids)])
        return self._visible(sid, rows)

    # ---------- topics and entities ----------
    def _topics(self, sid: int) -> list[dict[str, Any]]:
        """The namespace's topics this person sees: all of them, or (seen in part) the topics of the assets they see and
        the topics broader than those."""
        if sid not in self._topic_rows:
            rows = self.db.rows("SELECT record::id(id) AS id, label, broader FROM topic WHERE space = $s", s=sid)
            if self._spaces[sid] is not None:
                keep = {
                    a["topic"]
                    for a in self.db.rows("SELECT topic, recording FROM topic_about WHERE space = $s AND status = 'accepted'", s=sid)
                    if a["recording"] in self._seen_recordings(sid)
                }
                up = {t["id"]: t.get("broader") or [] for t in rows}
                todo = list(keep)
                while todo:
                    for b in up.get(todo.pop(), []):
                        if b not in keep:
                            keep.add(b)
                            todo.append(b)
                rows = [t for t in rows if t["id"] in keep]
            self._topic_rows[sid] = rows
        return self._topic_rows[sid]

    def _narrower(self, sid: int, parent: int | None) -> dict[str, int]:
        """The topics just under a topic (None: the ones with no broader topic this person sees), by their names here."""
        rows = self._topics(sid)
        known = {t["id"] for t in rows}
        if parent is None:
            kids = [t for t in rows if not any(b in known for b in t.get("broader") or [])]
        else:
            kids = [t for t in rows if parent in (t.get("broader") or [])]
        return dict(_unique([(clean(t["label"]), t["id"]) for t in kids]))

    def _list_topic(self, sid: int, names: list[str]) -> list[Entry]:
        if len(names) > TOPIC_DEPTH:
            raise NotFound("/".join(names))
        tid = None
        for n in names:
            kids = self._narrower(sid, tid)
            if n not in kids:
                raise NotFound(n)
            tid = kids[n]
        out = [_folder(n) for n in self._narrower(sid, tid)]
        if tid is not None:
            out += self._assets(self._topic_recordings(sid, tid))
        return sorted(out, key=lambda e: e.name.casefold())

    def _topic_recordings(self, sid: int, tid: int) -> list[dict[str, Any]]:
        rids = self.db.values("SELECT VALUE recording FROM topic_about WHERE topic = $t AND status = 'accepted'", t=tid)
        return self._recordings(sid, rids)

    def _seen_recordings(self, sid: int) -> set[int]:
        if sid not in self._seen:
            self._seen[sid] = hierarchy.recordings_in(self.db, self._spaces[sid] or [])
        return self._seen[sid]

    def _entity_rows(self, sid: int) -> list[dict[str, Any]]:
        from .entities import QUIET

        rows = [
            e
            for e in self.db.rows("SELECT record::id(id) AS id, name, type, hidden FROM entity WHERE space = $s", s=sid)
            if not e.get("hidden") and e.get("type") not in QUIET
        ]
        if self._spaces[sid] is not None:
            seen = self._seen_recordings(sid)
            have = {
                m["entity"]
                for m in self.db.rows("SELECT entity, recording FROM mentions WHERE space = $s GROUP BY entity, recording", s=sid)
                if m["recording"] in seen
            }
            rows = [e for e in rows if e["id"] in have]
        return rows

    def _types(self, sid: int) -> dict[str, str]:
        """{folder name: entity type} for the types this namespace's entities have."""
        from .entities import TYPES

        own = entity_setup.labels(self.db, {sid}).get(sid, {})
        codes = sorted({e["type"] for e in self._entity_rows(sid)})
        return dict(_unique([(clean(TYPES.get(c) or own.get(c) or c.title()), c) for c in codes]))

    def _entities(self, sid: int, code: str) -> dict[str, int]:
        return dict(_unique([(clean(e["name"]), e["id"]) for e in self._entity_rows(sid) if e["type"] == code]))

    def _entity_recordings(self, sid: int, eid: int) -> list[dict[str, Any]]:
        rids = [m["recording"] for m in self.db.rows("SELECT recording FROM mentions WHERE entity = $e GROUP BY recording", e=eid)]
        return self._recordings(sid, rids)

    # ---------- assets ----------
    def _primary(self, rec: dict[str, Any]) -> Entry | None:
        """The asset's own file: stored here (maybe encrypted), cached from a storage source, or on one."""
        kind = render.kind(rec)
        rm = rec.get("remote") or None
        original = (rm or {}).get("path") or rec.get("path") or ""
        name = pathlib.PurePosixPath(str(original)).name
        if not name:
            return None
        ctype = _type_of(name, kind)
        local = store.resolve_path(self.cfg, rec.get("path")) if not rm else None
        if rm:
            from . import sources

            for cached in (
                sources.cache_file(self.cfg, rm["source"], rm["path"], rec.get("space")),
                sources.cache_file(self.cfg, rm["source"], rm["path"]),
            ):
                if cached.is_file():
                    local = str(cached)
                    break
        if local and os.path.isfile(local):
            enc = keyring.is_encrypted(local)
            try:
                size = keyring.plain_size(self.db, self.cfg, local) if enc else os.path.getsize(local)
            except (keyring.Locked, keyring.Damaged):
                if not rec.get("size"):
                    return None
                size = int(rec["size"])
            return Entry(name, False, size, _when(rec), ctype, path=local, encrypted=enc)
        if rm and rec.get("size"):
            return Entry(name, False, int(rec["size"]), _when(rec), ctype, remote={**rm, "space": rec.get("space")})
        return None

    def _texts(self, rids: list[int]) -> dict[int, bytes]:
        """Each asset's text, a line per segment ("Speaker: …" when it has one)."""
        if not rids:
            return {}
        segs = self.db.rows(
            "SELECT recording, idx, t0, text, local_speaker, speaker FROM segment WHERE recording IN $r", r=sorted(set(rids))
        )
        spk = {
            s["id"]: s.get("name") or s.get("label")
            for s in (
                self.db.rows(
                    "SELECT record::id(id) AS id, name, label FROM speaker WHERE id IN $ids",
                    ids=[R("speaker", x) for x in {s["speaker"] for s in segs if s.get("speaker")}],
                )
                if segs
                else []
            )
        }
        lines: dict[int, list[tuple[Any, str]]] = {}
        for s in segs:
            text = " ".join(str(s.get("text") or "").split())
            if not text:
                continue
            who = s.get("local_speaker") or spk.get(s.get("speaker"))
            lines.setdefault(s["recording"], []).append(((s.get("idx") or 0, s.get("t0") or 0), f"{who}: {text}" if who else text))
        return {rid: ("\n".join(t for _, t in sorted(ls)) + "\n").encode() for rid, ls in lines.items()}

    def _about(self, rids: list[int]) -> tuple[dict[int, list[str]], dict[int, list[dict[str, str]]]]:
        """Each asset's accepted topics, and the entities it mentions."""
        from .entities import QUIET, TYPES

        if not rids:
            return {}, {}
        ids = sorted(set(rids))
        about = self.db.rows("SELECT topic, recording FROM topic_about WHERE recording IN $r AND status = 'accepted'", r=ids)
        labels = {
            t["id"]: t["label"]
            for t in (
                self.db.rows("SELECT record::id(id) AS id, label FROM topic WHERE id IN $t", t=[R("topic", a["topic"]) for a in about])
                if about
                else []
            )
        }
        topics: dict[int, list[str]] = {}
        for a in about:
            if a["topic"] in labels:
                topics.setdefault(a["recording"], []).append(labels[a["topic"]])
        ments = self.db.rows("SELECT recording, entity FROM mentions WHERE recording IN $r GROUP BY recording, entity", r=ids)
        ents = {
            e["id"]: e
            for e in (
                self.db.rows(
                    "SELECT record::id(id) AS id, space, name, type, hidden FROM entity WHERE id IN $e",
                    e=[R("entity", m["entity"]) for m in ments],
                )
                if ments
                else []
            )
            if not e.get("hidden") and e.get("type") not in QUIET
        }
        own = entity_setup.labels(self.db, {e["space"] for e in ents.values()})
        named: dict[int, list[dict[str, str]]] = {}
        for m in ments:
            e = ents.get(m["entity"])
            if e:
                kind = TYPES.get(e["type"]) or own.get(e["space"], {}).get(e["type"]) or e["type"].title()
                named.setdefault(m["recording"], []).append({"name": e["name"], "type": kind})
        return (
            {k: sorted(v, key=str.casefold) for k, v in topics.items()},
            {k: sorted(v, key=lambda x: (x["type"], x["name"].casefold())) for k, v in named.items()},
        )

    def _assets(self, rows: list[dict[str, Any]]) -> list[Entry]:
        """The files of these assets: own file, metadata, text and PDF, each named `<title> [<id>]`."""
        if self._only is not None:
            rows = [r for r in rows if r["id"] == self._only]
        rids = [r["id"] for r in rows]
        texts, (topics, ents) = self._texts(rids), self._about(rids)
        cols = {c["id"]: c for sid in {r["space"] for r in rows} for c in hierarchy.of_space(self.db, sid)} if rows else {}
        out: list[Entry] = []
        for rec in sorted(rows, key=lambda r: r["id"]):
            rid, when = rec["id"], _when(rec)
            base = f"{clean(rec.get('title') or 'Untitled')[: NAME_MAX - 20]} [{rid}]"
            own, own_name = self._primary(rec), None
            files = []
            if own:
                ext = pathlib.PurePosixPath(own.name).suffix.lower()
                own_name = own.name
                own.name = base + (ext if ext not in (".json", ".txt") else f".original{ext}")
                files.append(own)
            text = texts.get(rid)
            if text is not None:
                files.append(Entry(f"{base}.txt", False, len(text), when, "text/plain; charset=utf-8", data=text))
            pdf = convert.rendition_path(self.cfg, rid)
            if rec.get("source") == "document" and pdf.is_file() and not (own and own.name.endswith(".pdf")):
                enc = keyring.is_encrypted(pdf)
                try:
                    size = keyring.plain_size(self.db, self.cfg, pdf) if enc else pdf.stat().st_size
                    files.append(Entry(f"{base}.pdf", False, size, when, "application/pdf", path=str(pdf), encrypted=enc))
                except (keyring.Locked, keyring.Damaged):
                    pass
            col = []
            c = cols.get(rec.get("collection"))
            while c and len(col) < hierarchy.MAX_DEPTH + 1:
                col.append(c["name"])
                c = cols.get(c.get("parent")) if c.get("parent") is not None else None
            meta = {
                "identifier": f"{store.API}/recordings/{rid}",
                "id": rid,
                "title": rec.get("title"),
                "type": render.kind(rec),
                "format": own.content_type if own else None,
                "file": own.name if own else None,
                "original_name": own_name if own else None,
                "size": own.size if own else None,
                "namespace": self._names.get(rec["space"]),
                "collection": "/".join(col[::-1]) or None,
                "created": rec.get("created_at"),
                "date": rec.get("recorded_at"),
                "modified": when,
                "duration_seconds": round(rec["duration_ms"] / 1000, 3) if rec.get("duration_ms") else None,
                "status": rec.get("status"),
                "subject": topics.get(rid, []),
                "entities": ents.get(rid, []),
                "text": f"{base}.txt" if text is not None else None,
                "metadata": metadata._split(rec),
            }
            data = _json({k: v for k, v in meta.items() if v not in (None, {}, [])})
            files.append(Entry(f"{base}.json", False, len(data), when, "application/json", data=data))
            out += files
        return sorted(out, key=lambda e: e.name.casefold())
