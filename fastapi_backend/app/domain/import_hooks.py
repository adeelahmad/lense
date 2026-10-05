"""Import webhooks (docs/api.md, Import webhooks): an address other services push files, web addresses or text to,
and they land in one namespace as if someone had uploaded or imported them there.

A namespace's owners make a hook in the web app: a name, and optionally the collection its imports go into and the
pipeline they run (else the namespace's). Its token is shown once; only its hash is kept. A new token stops the old
one, and deleting the hook stops it altogether. A paused hook answers 403 and keeps its token.

What a hook takes (see accept_* below):
- a file (the raw body with its name, or multipart form files): audio, video, documents and images become recordings
  and resources exactly as an upload does; transcripts (.srt, .vtt, .json, .jsonl, .ics) are imported as transcripts;
- a web address: captured as a document when its pipeline runs (a PDF link as it is, a page printed by Chromium);
- text: imported as a transcript, as pasted text is.

Everything a hook brings in is audited under the hook's own name (`import.hook`), never a person's.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import secrets
import urllib.parse

from . import convert, ingest, jobs, pipelines, store, uploads, webcapture

R = store.R
FIELDS = "record::id(id) AS id, space, name, collection, pipeline, enabled, created_by, created_at, last_used_at, used, token_tail"
NAME_MAX = 80
TOKEN_PREFIX = "lih_"  # recognisable in logs and secret scanners
TRANSCRIPT_EXT = frozenset({".srt", ".vtt", ".json", ".jsonl", ".ics"})  # read as transcripts, not uploaded as documents
TOKEN_RX = re.compile(r"^lih_[A-Za-z0-9_-]{20,64}$")


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _token():
    return TOKEN_PREFIX + secrets.token_urlsafe(24)


def _view(db, row):
    names = store.space_names(db)
    return {**row, "namespace": names.get(row.get("space")), "enabled": row.get("enabled", True), "used": row.get("used") or 0}


def _check(db, sid, name, collection, pipeline):
    name = (name or "").strip()
    if not name:
        raise ValueError("give the hook a name, like the service that will push to it")
    if len(name) > NAME_MAX:
        raise ValueError(f"a name is at most {NAME_MAX} characters")
    if collection is not None:
        store.home(db, sid, collection)  # KeyError when it isn't the namespace's
    if pipeline is not None:
        try:
            pipelines.get(db, pipeline)
        except (KeyError, TypeError, ValueError):
            raise ValueError("there's no such pipeline") from None
    return name


def hooks(db, sid):
    """A namespace's hooks, oldest first."""
    return [_view(db, r) for r in db.rows(f"SELECT {FIELDS} FROM import_hook WHERE space = $s ORDER BY id", s=sid)]


def get(db, hid, sid=None):
    row = db.one(f"SELECT {FIELDS} FROM $r", r=R("import_hook", int(hid)))
    if not row or (sid is not None and row["space"] != sid):
        raise KeyError(hid)
    return _view(db, row)


def create(db, sid, name, collection=None, pipeline=None, by=None):
    """A new hook for namespace `sid`. Returns (hook, token): the token is shown this once."""
    name = _check(db, sid, name, collection, pipeline)
    hid, token = db.next_id("import_hook"), _token()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("import_hook", hid),
        d=store.clean(
            {
                "space": sid,
                "name": name,
                "collection": collection,
                "pipeline": pipeline,
                "enabled": True,
                "token_hash": _hash(token),
                "token_tail": token[-4:],
                "created_by": by,
                "created_at": store.now(),
            }
        ),
    )
    return get(db, hid), token


def update(db, hid, sid, changes):
    """Rename a hook, pause or resume it, or change where its imports go (null for the namespace's defaults)."""
    before = get(db, hid, sid)
    after = {**before, **changes}
    name = _check(db, sid, after["name"], after.get("collection"), after.get("pipeline"))
    db.q(
        "UPDATE $r SET name = $n, collection = $c, pipeline = $p, enabled = $e",
        r=R("import_hook", int(hid)),
        n=name,
        c=after.get("collection"),
        p=after.get("pipeline"),
        e=bool(after.get("enabled", True)),
    )
    return before, get(db, hid, sid)


def new_token(db, hid, sid):
    """A new token for a hook; the old one stops working at once."""
    get(db, hid, sid)
    token = _token()
    db.q("UPDATE $r SET token_hash = $h, token_tail = $t", r=R("import_hook", int(hid)), h=_hash(token), t=token[-4:])
    return token


def delete(db, hid, sid):
    """Remove a hook: its token stops working. What it imported stays."""
    hook = get(db, hid, sid)
    db.q("DELETE $r", r=R("import_hook", int(hid)))
    return hook


class Paused(PermissionError):
    pass


def by_token(db, token):
    """The hook a token belongs to. KeyError for an unknown token, Paused for a paused hook's."""
    if not token or not TOKEN_RX.match(token):
        raise KeyError("token")
    row = db.one(f"SELECT {FIELDS} FROM import_hook WHERE token_hash = $h LIMIT 1", h=_hash(token))
    if not row:
        raise KeyError("token")
    hook = _view(db, row)
    if not hook["enabled"]:
        raise Paused(f"the import hook {hook['name']} is paused")
    if not hook["namespace"]:
        raise KeyError("token")  # its namespace has gone
    return hook


def actor(hook):
    """Who a hook's imports are by, in jobs, uploads and the audit log."""
    return {"id": f"import_hook:{hook['id']}", "email": f"import hook: {hook['name']}"}


def _used(db, hook, n):
    db.q("UPDATE $r SET last_used_at = $t, used += $n", r=R("import_hook", hook["id"]), t=store.now(), n=n)


def _collection(db, hook):
    """The hook's collection, unless it has gone since (then the namespace's default)."""
    try:
        return store.home(db, hook["space"], hook.get("collection"))
    except KeyError:
        return store.default_collection(db, hook["space"])


def kind_of(cfg, filename):
    """How a pushed file is taken in: 'upload' (audio, video, documents, images) or 'transcript'. ValueError, saying
    what can be sent, for anything else."""
    ext = pathlib.Path(filename).suffix.lower()
    allowed = {e.lower() for e in cfg["uploads"]["extensions"]}
    if ext in TRANSCRIPT_EXT:
        return "transcript"
    if ext in allowed:
        return "upload"
    kinds = ", ".join(e[1:] for e in sorted(allowed | TRANSCRIPT_EXT))
    shown = f"{ext[1:].upper()} files" if ext else "Files without an extension"
    raise ValueError(f"{shown} can't be imported; these can: {kinds}")


def accept_file(db, cfg, hook, path, filename, title=None):
    """A whole file the hook was sent, at `path` (on the uploads disk; it's moved or removed). One result:
    {kind, name, recording, job, duplicate}."""
    by = actor(hook)
    name = uploads.clean_name(filename)
    try:
        how = kind_of(cfg, name)
        if how == "upload":
            up = uploads.take(db, cfg, hook["namespace"], path, name, by, title, hook.get("pipeline"), _collection(db, hook))
            rid, job, dup = up["recording"], up.get("job"), bool(up.get("duplicate"))
        else:
            if path.stat().st_size > cfg["server"]["max_upload_mb"] * uploads.MB:
                raise uploads.TooLarge(f"transcripts up to {cfg['server']['max_upload_mb']} MB")
            named = path.with_name(path.name + pathlib.Path(name).suffix)  # read_transcript goes by the extension
            path.rename(named)
            path = named
            try:
                rid = ingest.import_transcript(
                    db,
                    cfg,
                    hook["namespace"],
                    path,
                    title=title or pathlib.Path(name).stem,
                    log=lambda *_: None,
                    collection=_collection(db, hook),
                )
            except SystemExit as e:
                raise ValueError(str(e)) from None
            db.q("UPDATE $r SET path = $p", r=R("recording", rid), p=f"hook:{hook['id']}:{name}")
            job, dup = jobs.enqueue(db, rid, None, by=by["email"], pipeline=hook.get("pipeline")), False
    finally:
        path.unlink(missing_ok=True)
    _used(db, hook, 1)
    return store.clean({"kind": "file", "name": name, "recording": rid, "job": job, "duplicate": dup})


def accept_url(db, cfg, hook, url, title=None):
    """A web address, kept as a document: captured (a PDF as it is, a page printed by Chromium) when its pipeline
    runs. Only public addresses on the web's ports."""
    url = webcapture.check_url(cfg, url)
    if not convert.chromium(cfg) and not urllib.parse.urlsplit(url).path.lower().endswith(".pdf"):
        raise ValueError("capturing web pages needs Chromium on the server (the lens:full image); a link to a PDF works without it")
    by = actor(hook)
    rid = webcapture.create(db, hook["space"], url, title, _collection(db, hook), by=by["email"])
    job = jobs.enqueue(db, rid, None, by=by["email"], pipeline=hook.get("pipeline"))
    _used(db, hook, 1)
    return store.clean({"kind": "url", "name": url, "recording": rid, "job": job, "duplicate": False})


def accept_text(db, cfg, hook, text, title=None):
    """Text, imported as a transcript the way pasted text is."""
    try:
        rid = ingest.import_text(
            db, cfg, hook["namespace"], text, title=(title or "").strip()[:200] or None, collection=_collection(db, hook)
        )
    except SystemExit as e:
        raise ValueError(str(e)) from None
    job = jobs.enqueue(db, rid, None, by=actor(hook)["email"], pipeline=hook.get("pipeline"))
    _used(db, hook, 1)
    return store.clean({"kind": "text", "name": (title or "text")[:200], "recording": rid, "job": job, "duplicate": False})
