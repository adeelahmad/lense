"""Audio, video, documents and images uploaded in pieces (docs/api.md, Uploads).

Someone starts an upload with the file's name and size, then sends it in chunks (the web app sends uploads.chunk_mb
at a time). Each chunk streams straight into a partial file under data_dir/uploads/.partial, so no request holds a
whole file in memory. The partial file's length is how much has arrived: after a dropped connection the client asks
and carries on from there. A chunk that breaks off is cut off again, so the file only ever holds whole chunks.

When the last byte lands, the file moves to data_dir/uploads/<namespace>/<upload id>/<its name> and becomes a
recording (or finds the one it already is), and the namespace's pipeline is queued; or, for an upload started for a
transcript-only recording, it becomes that recording's audio. Uploads nobody has sent a chunk to for
uploads.expire_hours are removed with their partial files.
"""

from __future__ import annotations

import datetime as dt
import errno
import fcntl
import os
import pathlib
import secrets
import shutil
import unicodedata

from . import convert, deletion, documents, ingest, jobs, render, store

R = store.R
MB = 1024 * 1024
ROOM = 512 * MB  # what an upload must leave free on the server's disk
FIELDS = (
    "record::id(id) AS id, account, email, namespace, collection, filename, title, size, modified, attach, pipeline, state, "
    "recording, job, duplicate, created_at, touched_at"
)
# What runs once media is attached to a transcript: its waveform (the transcript is kept), speakers by voice unless the
# transcript named them, shots, text on screen and faces for video, then the analysis and report pages again.
ATTACH_STEPS = ["transcribe", "diarize", "shots", "ocr", "faces", "objects", "analyze", "report"]
TRANSCRIPT_EXT = frozenset({".txt", ".text", ".md", ".markdown", ".mdx", ".docx", ".doc", ".pdf"})  # documents imports read as text too


class TooLarge(ValueError):
    """Over uploads.max_mb."""


class Mismatch(ValueError):
    """A chunk that doesn't start where the upload has got to."""


class Busy(RuntimeError):
    """Another chunk of the same upload is arriving."""


class HasMedia(ValueError):
    """The recording to attach to has audio or video already."""


def has_media(db, cfg, rec):
    """Whether a recording plays something: a file on a storage source, or a local file that's there."""
    return bool(rec.get("remote")) or bool(render.has_audio(db, cfg, rec["id"]))


def has_file(db, cfg, rec):
    """Whether a document or an image still has its file: on a storage source, or a local file that's there."""
    path = store.resolve_path(cfg, rec.get("path"))
    return bool(rec.get("remote")) or bool(path and os.path.exists(path))


def clean_name(name):
    """The file's own name, safe on any disk: no folders, control characters or leading dots; its extension in
    lowercase; at most 150 bytes before it."""
    n = unicodedata.normalize("NFC", str(name or "")).replace("\\", "/").rsplit("/", 1)[-1]
    n = "".join(c for c in n if c.isprintable() and c not in '<>:"|?*').strip().lstrip(". ")
    stem, ext = os.path.splitext(n)
    return (stem.strip().encode()[:150].decode("utf-8", "ignore").strip() or "upload") + ext.lower()


def limits(cfg):
    """What may be uploaded, so the web app can say so before it sends anything."""
    u = cfg["uploads"]
    return {
        "max_mb": u["max_mb"],
        "extensions": sorted({e.lower() for e in u["extensions"]}),
        "chunk_mb": u["chunk_mb"],
        "transcript_mb": cfg["server"]["max_upload_mb"],
        "convert": convert.capabilities(cfg),
    }


def _dir(cfg):
    return pathlib.Path(cfg["data_dir"]) / "uploads"


def _part(cfg, uid):
    return _dir(cfg) / ".partial" / uid


def _ago(hours):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat(timespec="seconds")


def view(cfg, row):
    """An upload as the API shows it. `offset` is how many bytes have arrived: the next chunk starts there."""
    done = row.get("state") == "done"
    part = _part(cfg, row["id"])
    touched = dt.datetime.fromisoformat(row.get("touched_at") or row["created_at"])
    return {
        "id": row["id"],
        "namespace": row["namespace"],
        "filename": row["filename"],
        "title": row.get("title"),
        "size": row["size"],
        "offset": row["size"] if done else (part.stat().st_size if part.exists() else 0),
        "state": "done" if done else "receiving",
        "attach": row.get("attach"),
        "pipeline": row.get("pipeline"),
        "recording": row.get("recording"),
        "job": row.get("job"),
        "duplicate": bool(row.get("duplicate")),
        "created_at": row["created_at"],
        "expires_at": (touched + dt.timedelta(hours=cfg["uploads"]["expire_hours"])).isoformat(timespec="seconds"),
    }


def get(db, uid):
    row = db.one(f"SELECT {FIELDS} FROM $r", r=R("upload", str(uid)))
    if not row:
        raise KeyError(uid)
    return row


def mine(db, account):
    """Someone's uploads that haven't finished, newest first: they carry on where they stopped."""
    return db.rows(f"SELECT {FIELDS} FROM upload WHERE account = $a AND state = 'receiving' ORDER BY created_at DESC", a=account)


def _home(db, sid, collection):
    """Where the upload's recording goes: the collection chosen, unless it has gone since, else the default."""
    try:
        return store.home(db, sid, collection)
    except KeyError:
        return store.default_collection(db, sid)


def start(db, cfg, ns, filename, size, by, title=None, modified=None, attach=None, pipeline=None, collection=None):
    """A new upload into namespace `ns`, by `by` ({id, email}). Its name, type and size are checked, and the disk
    must have room for it; nothing is in the archive until the last byte arrives. `modified` is the file's own time
    (milliseconds since 1970), which dates the recording when its name doesn't. `attach` is a transcript-only
    recording in `ns` the file becomes the audio of, instead of a recording of its own; `pipeline` runs instead of the
    namespace's once a new recording is made; `collection` (one of the namespace's) is where it goes, else the
    namespace's default."""
    sweep(db, cfg)
    u = cfg["uploads"]
    name = clean_name(filename)
    ext = pathlib.Path(name).suffix
    allowed = sorted({e.lower() for e in u["extensions"]})
    if ext not in allowed:
        kind = f"{ext[1:].upper()} files" if ext else "Files without an extension"
        raise ValueError(f"{kind} can't be uploaded here; these can: {', '.join(e[1:] for e in allowed)}")
    if attach and documents.kind_of(name):
        raise ValueError("only audio or video can be attached to a transcript")
    why = convert.unavailable(cfg, name)
    if why:
        instead = "; import it as a transcript instead" if ext in TRANSCRIPT_EXT else ""
        raise ValueError(f"this {convert.word(name)} can't be read here: {why}{instead}")
    if size <= 0:
        raise ValueError("the file is empty")
    if size > u["max_mb"] * MB:
        raise TooLarge(f"files up to {u['max_mb']} MB")
    folder = _dir(cfg) / ".partial"
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < size + ROOM:
        raise OSError(errno.ENOSPC, "the server doesn't have room for this file")
    uid, t = secrets.token_hex(12), store.now()
    _part(cfg, uid).touch()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("upload", uid),
        d=store.clean(
            {
                "account": by["id"],
                "email": by.get("email"),
                "namespace": ns,
                "filename": name,
                "title": (title or "").strip()[:200] or None,
                "size": size,
                "modified": modified,
                "attach": attach,
                "pipeline": pipeline,
                "collection": collection,
                "state": "receiving",
                "created_at": t,
                "touched_at": t,
            }
        ),
    )
    return get(db, uid)


class Chunk:
    """One chunk, appended to the partial file under a lock on it: whole, or not at all.

    with Chunk(cfg, row, offset) as c:
        for piece in body:
            c.write(piece)
        c.keep()  # anything that goes wrong before this cuts the chunk off again
        received(db, cfg, uid)  # still under the lock, so a chunk sent twice can't finish it twice
    """

    def __init__(self, cfg, row, offset):
        self.path, self.size, self.offset, self.kept = _part(cfg, row["id"]), row["size"], offset, False

    def __enter__(self):
        try:
            f = open(self.path, "r+b")  # noqa: SIM115 - closed in __exit__
        except FileNotFoundError:
            raise KeyError(self.path.name) from None
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.close()
            raise Busy("another chunk of this upload is arriving; wait for it to finish") from None
        have = os.fstat(f.fileno()).st_size
        if have != self.offset:
            f.close()
            raise Mismatch(f"this upload has {have} bytes; send the chunk that starts there")
        f.seek(have)
        self.f, self.start = f, have
        return self

    def write(self, piece):
        if self.f.tell() + len(piece) > self.size:
            raise ValueError("that's more than the file's size")
        self.f.write(piece)

    def keep(self):
        self.f.flush()
        self.kept = True

    def __exit__(self, kind, exc, tb):
        try:
            if kind is not None and not self.kept:
                self.f.truncate(self.start)  # a chunk that broke off goes; the client sends it again
        finally:
            self.f.close()  # and the lock with it
        return False


def received(db, cfg, uid, admin=False):
    """After a chunk: the upload stays alive, and becomes a recording once all of it is here."""
    row = get(db, uid)
    part = _part(cfg, uid)
    if not part.exists():
        raise KeyError(uid)  # cancelled, or expired, meanwhile
    db.q("UPDATE $r SET touched_at = $t", r=R("upload", uid), t=store.now())
    return finish(db, cfg, row, admin) if part.stat().st_size >= row["size"] else get(db, uid)


def finish(db, cfg, row, admin=False):
    """All of it is here: the file moves into the namespace's upload folder and becomes a recording, and the
    namespace's pipeline is queued. The same file already in the namespace (uploaded, scanned or imported before)
    isn't added twice: the upload points at that recording instead, and gives it back its media if it had lost it.
    `admin` creates the namespace if it doesn't exist (only admins may start an upload into a new one). An upload for
    a transcript-only recording becomes its media instead, and the steps that need media run (ATTACH_STEPS). If
    anything fails, the file goes back to where it was, so finishing can be tried again."""
    uid, by, target = row["id"], row.get("email"), row.get("attach")
    if target:
        rec = db.one("SELECT record::id(id) AS id, space, remote FROM $r", r=R("recording", target))
        if not rec:
            raise KeyError(target)
        if has_media(db, cfg, rec):
            raise HasMedia("that recording has audio already")
        sid = rec["space"]
    else:
        sid = store.ns_id(db, row["namespace"], create=admin)
    part, dest = _part(cfg, uid), _dir(cfg) / row["namespace"] / uid / row["filename"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(part, dest)
    try:
        modified = row.get("modified")
        if modified and modified / 1000 < dt.datetime.now(dt.timezone.utc).timestamp() + 86400:
            os.utime(dest, (dest.stat().st_atime, modified / 1000))
        st = dest.stat()
        fp = ingest.fingerprint(dest)
        deletion.forget(db, sid, fp)  # uploaded on purpose: a recording deleted before comes back
        dup = (
            None
            if target
            else db.one(
                "SELECT record::id(id) AS id, status, remote, path FROM recording WHERE space = $s AND fingerprint = $f LIMIT 1",
                s=sid,
                f=fp,
            )
        )
        kind = documents.kind_of(row["filename"])
        copy = bool(dup and (has_file(db, cfg, dup) if kind else has_media(db, cfg, dup)))  # it's here already, with its file
        job = None
        if target:
            rid, job = target, _attach(db, cfg, target, sid, dest, st, fp, by)
        elif copy:
            rid = dup["id"]
        elif kind:
            rid, job = _document(db, dest, st, fp, sid, row, kind, dup, by)
        else:
            dur, ch = ingest.probe(dest)
            media = store.clean(
                {"path": str(dest), "source": "audio", "size": st.st_size, "mtime": st.st_mtime, "duration_ms": dur, "channels": ch}
            )
            if dup:
                rid = dup["id"]
                db.q("UPDATE $r MERGE $d", r=R("recording", rid), d=media)
                steps = jobs.steps_for({**dup, "source": "audio"})
                job = jobs.enqueue(db, rid, steps, by=by) if steps else None
            else:
                rid = db.next_id("recording")
                db.q(
                    "CREATE $r CONTENT $d",
                    r=R("recording", rid),
                    d={
                        **media,
                        "space": sid,
                        "collection": _home(db, sid, row.get("collection")),
                        "fingerprint": fp,
                        "fp_key": f"{sid}:{fp}",
                        "title": row.get("title") or pathlib.Path(row["filename"]).stem,
                        "recorded_at": ingest.recorded_at(dest, st.st_mtime),
                        "status": "new",
                        "created_at": store.now(),
                    },
                )
                job = jobs.enqueue(db, rid, None, by=by, pipeline=row.get("pipeline"))
        db.q(
            "UPDATE $r MERGE $d",
            r=R("upload", uid),
            d=store.clean({"state": "done", "recording": rid, "job": job, "duplicate": bool(dup), "finished_at": store.now()}),
        )
    except BaseException:
        os.replace(dest, part)
        raise
    if copy:
        shutil.rmtree(dest.parent, ignore_errors=True)
    return get(db, uid)


def _document(db, dest, st, fp, sid, row, kind, dup, by):
    """A document or an image: a resource of its own, or the one the file was before (imported as a transcript, or
    whose file went), which takes it as this kind; its pages are drawn and read by the namespace's pipeline (or the
    one chosen)."""
    d = {"path": str(dest), "source": kind, "size": st.st_size, "mtime": st.st_mtime, "media": {"kind": kind}, "status": "new"}
    if dup:
        rid = dup["id"]
        db.q("UPDATE $r MERGE $d", r=R("recording", rid), d=d)
    else:
        rid = db.next_id("recording")
        db.q(
            "CREATE $r CONTENT $d",
            r=R("recording", rid),
            d={
                **d,
                "space": sid,
                "collection": _home(db, sid, row.get("collection")),
                "fingerprint": fp,
                "fp_key": f"{sid}:{fp}",
                "title": row.get("title") or pathlib.Path(row["filename"]).stem,
                "recorded_at": ingest.recorded_at(dest, st.st_mtime),
                "created_at": store.now(),
            },
        )
    return rid, jobs.enqueue(db, rid, None, by=by, pipeline=row.get("pipeline"))


def _attach(db, cfg, rid, sid, dest, st, fp, by):
    """The file becomes the media of a transcript-only recording; it takes the file's fingerprint (so scans and uploads
    know the file) unless another recording in the namespace has it."""
    dur, ch = ingest.probe(dest)
    d = store.clean({"path": str(dest), "source": "audio", "size": st.st_size, "mtime": st.st_mtime, "duration_ms": dur, "channels": ch})
    if not db.values("SELECT VALUE id FROM recording WHERE fp_key = $k", k=f"{sid}:{fp}"):
        d.update(fingerprint=fp, fp_key=f"{sid}:{fp}")
    db.q("UPDATE $r MERGE $d", r=R("recording", rid), d=d)
    return jobs.add_steps(db, rid, ATTACH_STEPS, by=by)


def cancel(db, cfg, row):
    """Stop an upload; its partial file goes. A finished one is only forgotten: it's a recording now."""
    if row.get("state") != "done":
        _part(cfg, row["id"]).unlink(missing_ok=True)
    db.q("DELETE $r", r=R("upload", row["id"]))


def sweep(db, cfg):
    """Uploads nobody has sent a chunk to for uploads.expire_hours go, with their partial files (finished ones are
    only forgotten). One receiving a chunk right now stays."""
    for uid in db.values("SELECT VALUE record::id(id) FROM upload WHERE touched_at < $t", t=_ago(cfg["uploads"]["expire_hours"])):
        part = _part(cfg, uid)
        try:
            with open(part, "rb") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                part.unlink()
        except FileNotFoundError:
            pass
        except BlockingIOError:
            continue
        db.q("DELETE $r", r=R("upload", uid))
