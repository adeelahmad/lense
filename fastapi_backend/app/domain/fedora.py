"""Fedora (Fedora Commons, 6.x) as the archive's repository store: every namespace, collection, recording (with its
file), entity and speaker is kept there as an LDP resource described in RDF (rdf.py), for preservation and for the
tools that read Fedora. SurrealDB stays the working database; Fedora is a copy kept in step with it. Off unless
`fedora.url` is set (docs/fedora.md), so installs without it (a Raspberry Pi) run as before.

Layout under `<fedora.url>/<fedora.root>`:

    <namespace>                       a container: the namespace (dcmitype:Collection)
    <namespace>/collections/<id>      its collections
    <namespace>/recordings/<id>       its recordings, each with its file as a binary child, `file`
    <namespace>/entities/<id>         its entities (skos:Concept)
    <namespace>/speakers/<id>         its speakers (foaf:Person)

Each resource's description is what Lens says of it (its triples, blank nodes as hash URIs), plus owl:sameAs its
Lens URI (`/id/...`); links to other resources use their Lens URIs, which stay the same wherever Fedora is.

Keeping in step: a change to a recording's metadata queues it (`touch`), and the queue is sent every
`fedora.sync_seconds`; everything is compared with what was last sent (a hash per resource) every `fedora.full_hours`,
which catches what analysis changed, new recordings and deletions. Only what changed is sent. Refine later: one
syncing process (the API and the worker both run the loop; PUTs are idempotent), transactions, OCFL-level checks,
and reading Fedora back.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import mimetypes
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

from rdflib import BNode, Graph, URIRef
from rdflib.namespace import OWL

from . import keyring, rdf, render, store

R = store.R
LENIENT = 'handling=lenient; received="minimal"'
TIMEOUT = 60


class FedoraError(RuntimeError):
    pass


def enabled(cfg) -> bool:
    f = cfg.get("fedora") or {}
    return bool(f.get("enabled") and f.get("url"))


def touch(db, kind, key):
    """Something changed: send it at the next sync (only when Fedora is in use, but cheap either way)."""
    db.q("UPSERT $r CONTENT $d", r=R("fedora_outbox", f"{kind}-{key}"), d={"kind": kind, "key": str(key), "at": store.now()})


class Client:
    """Fedora's REST API (LDP), with the configured account."""

    def __init__(self, cfg):
        f = cfg["fedora"]
        self.base = f["url"].rstrip("/")
        self.root = (f.get("root") or "lens").strip("/")
        self.auth = None
        if f.get("user"):
            token = base64.b64encode(f"{f['user']}:{f.get('password') or ''}".encode()).decode()
            self.auth = f"Basic {token}"

    def url(self, path=""):
        return f"{self.base}/{self.root}" + (f"/{path}" if path else "")

    def call(self, method, url, body=None, headers=None, length=None):
        h = dict(headers or {})
        if self.auth:
            h["Authorization"] = self.auth
        if length is not None:
            h["Content-Length"] = str(length)
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise FedoraError(f"can't reach Fedora at {self.base}: {e}") from None

    def exists(self, url):
        status, _ = self.call("HEAD", url)
        return status == 200

    def put_rdf(self, url, turtle: bytes):
        status, body = self.call("PUT", url, turtle, {"Content-Type": "text/turtle", "Prefer": LENIENT})
        if status not in (200, 201, 204):
            raise FedoraError(f"Fedora refused {url}: {status} {body[:300].decode(errors='replace')}")

    def put_file(self, url, path: pathlib.Path, mime: str | None, name: str | None = None):
        name = urllib.parse.quote(name or path.name)
        headers = {"Content-Type": mime or "application/octet-stream", "Content-Disposition": f"attachment; filename*=UTF-8''{name}"}
        with path.open("rb") as fh:
            status, body = self.call("PUT", url, fh, headers, length=path.stat().st_size)
        if status not in (200, 201, 204):
            raise FedoraError(f"Fedora refused {url}: {status} {body[:300].decode(errors='replace')}")

    def delete(self, url):
        status, _ = self.call("DELETE", url)
        if status in (204, 200, 410):
            self.call("DELETE", url + "/fcr:tombstone")  # so the path can be used again
        elif status != 404:
            raise FedoraError(f"Fedora refused to delete {url}: {status}")

    def ensure(self, url):
        if not self.exists(url):
            self.put_rdf(url, b"")


# ---------- what each resource says ----------
def own(g: Graph, subject: URIRef, target: str) -> bytes:
    """The triples about `subject` (and the blank nodes it reaches), as Turtle about `target`: the subject becomes the
    Fedora resource, blank nodes become its hash URIs."""
    out = Graph()
    for prefix, ns in g.namespaces():
        out.bind(prefix, ns)
    names: dict[BNode, URIRef] = {}

    def node(n):
        if isinstance(n, BNode):
            if n not in names:
                names[n] = URIRef(f"{target}#b{len(names) + 1}")
                for p, o in g.predicate_objects(n):
                    out.add((names[n], p, node(o)))
            return names[n]
        return n

    me = URIRef(target)
    for p, o in g.predicate_objects(subject):
        out.add((me, p, node(o)))
    out.add((me, OWL.sameAs, subject))
    return out.serialize(format="turtle").encode()


def _resources(db, cfg, base):
    """Every resource Lens keeps in Fedora: (path, Lens URI, graph builder, file to keep with it or None)."""
    u = rdf.Uris(base)
    for sp in db.rows("SELECT record::id(id) AS id, name FROM space ORDER BY name"):
        sid, ns = sp["id"], sp["name"]
        yield ns, u.namespace(ns), lambda sid=sid: _graph(lambda g: rdf.add_namespace(g, db, u, sid), u), None
        for c in db.rows("SELECT record::id(id) AS id FROM collection WHERE space = $s", s=sid):
            yield f"{ns}/collections/{c['id']}", u.collection(c["id"]), lambda cid=c["id"]: rdf.collection_graph(db, base, cid), None
        for r in db.rows("SELECT record::id(id) AS id, path FROM recording WHERE space = $s", s=sid):
            yield (
                f"{ns}/recordings/{r['id']}",
                u.recording(r["id"]),
                lambda rid=r["id"]: rdf.recording_graph(db, cfg, base, rid, member=True),
                r.get("path"),
            )
        for e in db.rows("SELECT record::id(id) AS id FROM entity WHERE space = $s AND hidden != true", s=sid):
            yield f"{ns}/entities/{e['id']}", u.entity(e["id"]), lambda eid=e["id"]: rdf.entity_graph(db, base, eid), None
        for s in db.rows("SELECT record::id(id) AS id FROM speaker WHERE space = $s", s=sid):
            yield f"{ns}/speakers/{s['id']}", u.speaker(s["id"]), lambda spk=s["id"]: rdf.speaker_graph(db, base, spk), None


def _graph(add, u):
    g = rdf.new_graph(u)
    add(g)
    return g


CONTAINERS = ("collections", "recordings", "entities", "speakers")


def _file(cfg, path):
    """A recording's file on disk, when it is one Lens can read (its own, or under the data folder)."""
    if not path:
        return None
    p = pathlib.Path(path)
    if not p.is_absolute():
        p = pathlib.Path(cfg["data_dir"]) / p
    return p if p.is_file() else None


def _state_id(path):
    return hashlib.sha1(path.encode()).hexdigest()


def _base(cfg):
    """The address Lens URIs are made of: iiif.base_url, else the app's address (FRONTEND_URL)."""
    return (cfg["iiif"].get("base_url") or os.environ.get("FRONTEND_URL") or "http://localhost:3000").rstrip("/")


def sync(db, cfg, only=None, log=None):
    """Send what changed since the last sync: everything (`only` None), or the resources whose paths are in `only`.
    Returns counts."""
    c = Client(cfg)
    base = _base(cfg)
    f = cfg["fedora"]
    max_bytes = int(f.get("max_file_mb") or 0) * 1024 * 1024
    counts = {"sent": 0, "files": 0, "unchanged": 0, "deleted": 0, "failed": 0}
    c.ensure(c.url())
    seen_ns = set()
    seen = set()
    errors = []
    for path, subject, build, file_path in _resources(db, cfg, base):
        ns = path.split("/")[0]
        if only is not None and path not in only and ns not in only:
            continue
        seen.add(path)
        try:
            if ns not in seen_ns:
                seen_ns.add(ns)
                c.ensure(c.url(ns))
                for part in CONTAINERS:
                    c.ensure(c.url(f"{ns}/{part}"))
            target = c.url(path)
            ttl = own(build(), subject, target)
            digest = hashlib.sha256(ttl).hexdigest()
            sid = _state_id(path)
            state = db.one("SELECT * FROM $r", r=R("fedora_state", sid)) or {}
            if state.get("hash") != digest:
                c.put_rdf(target, ttl)
                counts["sent"] += 1
            else:
                counts["unchanged"] += 1
            fp = _file(cfg, file_path) if f.get("files", True) else None
            stamp = None
            if fp and (not max_bytes or fp.stat().st_size <= max_bytes):
                st = fp.stat()
                stamp = f"{st.st_size}:{int(st.st_mtime)}"
                if state.get("file") != stamp:
                    mime = render.AUDIO_TYPES.get(fp.suffix.lower()) or mimetypes.guess_type(fp.name)[0]
                    with keyring.plain_path(db, cfg, fp) as plain:  # Fedora keeps the file itself, not Lens's encrypted form
                        c.put_file(f"{target}/file", pathlib.Path(plain), mime, name=fp.name)
                    counts["files"] += 1
            db.q(
                "UPSERT $r CONTENT $d",
                r=R("fedora_state", sid),
                d=store.clean({"path": path, "hash": digest, "file": stamp or state.get("file"), "at": store.now()}),
            )
        except FedoraError as e:
            counts["failed"] += 1
            errors.append(str(e))
            if "can't reach" in str(e):
                break
        except (keyring.Locked, keyring.Damaged) as e:  # a locked vault, or a damaged file: the rest still go
            counts["failed"] += 1
            errors.append(f"{path}: {e.__class__.__name__.lower()}")
    if only is None and not errors:  # what Lens no longer has goes from Fedora too
        for row in db.rows("SELECT record::id(id) AS id, path FROM fedora_state"):
            if row["path"] not in seen:
                try:
                    c.delete(c.url(row["path"]))
                    db.q("DELETE $r", r=R("fedora_state", row["id"]))
                    counts["deleted"] += 1
                except FedoraError as e:
                    errors.append(str(e))
    _note(db, counts, errors, full=only is None)
    if errors and log:
        log(f"fedora: {errors[0]}")
    return {**counts, "errors": errors[:10]}


def _note(db, counts, errors, full):
    cur = status(db)
    now = store.now()
    db.q(
        "UPSERT fedora_status:last CONTENT $d",
        d=store.clean(
            {
                "last_sync": now,
                "last_full": now if full and not errors else cur.get("last_full"),
                "last_counts": counts,
                "last_error": errors[0] if errors else None,
                "error_at": now if errors else None,
            }
        ),
    )


def status(db):
    return db.one("SELECT * OMIT id FROM fedora_status:last") or {}


def _paths_for(db, kind, key):
    """The Fedora paths an outbox entry stands for."""
    if kind == "recording":
        row = db.one("SELECT space FROM $r", r=R("recording", int(key)))
        if not row:
            return set()  # deleted: the next full sync removes it
        ns = (db.one("SELECT name FROM $s", s=R("space", row["space"])) or {}).get("name")
        return {f"{ns}/recordings/{key}"}
    if kind == "namespace":
        return {key}
    return set()


def sync_due(db, cfg, log=None):
    """The background step: the queue every time, everything when a full sync is due."""
    if not enabled(cfg):
        return None
    f = cfg["fedora"]
    st = status(db)
    last_full = st.get("last_full")
    due = not last_full or dt.datetime.fromisoformat(str(last_full).replace("Z", "+00:00")) < dt.datetime.now(
        dt.timezone.utc
    ) - dt.timedelta(hours=float(f.get("full_hours") or 24))
    if st.get("force"):
        due = True
        db.q("UPDATE fedora_status:last SET force = NONE")
    if due:
        db.q("DELETE fedora_outbox")
        return sync(db, cfg, log=log)
    queued = db.rows("SELECT record::id(id) AS id, kind, key FROM fedora_outbox")
    if not queued:
        return None
    paths = set()
    for q in queued:
        paths |= _paths_for(db, q["kind"], q["key"])
    for q in queued:
        db.q("DELETE $r", r=R("fedora_outbox", q["id"]))
    return sync(db, cfg, only=paths, log=log) if paths else None


def request_full(db):
    """Send everything at the next sync."""
    db.q("UPSERT fedora_status:last MERGE {force: true}")


def summary(db, cfg):
    st = status(db)
    f = cfg.get("fedora") or {}
    return {
        "enabled": enabled(cfg),
        "url": f.get("url"),
        "root": f.get("root") or "lens",
        "pending": len(db.rows("SELECT id FROM fedora_outbox")),
        "resources": len(db.rows("SELECT id FROM fedora_state")),
        "last_sync": st.get("last_sync"),
        "last_full": st.get("last_full"),
        "last_counts": st.get("last_counts"),
        "last_error": st.get("last_error"),
        "full_requested": bool(st.get("force")),
    }
