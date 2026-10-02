"""Storage sources through rclone, and the folders on them that the archive watches.

A source is one connection: S3 or S3-compatible, Dropbox, Google Drive, OneDrive, SFTP, SMB, WebDAV, or a folder on
this machine (only inside sources.local_roots); or, without rclone, an email account (IMAP) or a calendar feed (iCal),
whose messages and events are shown as files (feeds.py). A watch maps a folder on a source to a namespace: new audio, video,
documents (PDFs) and images become resources queued for the whole pipeline, their files staying on the source; new
transcripts are imported and queued for analysis. Credentials are stored encrypted and handed to rclone in a private
temporary config file per call; OAuth tokens rclone refreshes are saved.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

from . import convert, deletion, feeds, ingest, jobs, settings, store

R = store.R
BACKENDS = {
    "s3": {
        "label": "Amazon S3 or S3-compatible",
        "fields": {"provider": "AWS", "region": "", "endpoint": "", "access_key_id": ""},
        "secrets": ["secret_access_key"],
    },
    "dropbox": {"label": "Dropbox", "fields": {}, "secrets": ["token"], "oauth": "rclone authorize dropbox"},
    "drive": {
        "label": "Google Drive",
        "fields": {"scope": "drive.readonly", "root_folder_id": ""},
        "secrets": ["token"],
        "oauth": "rclone authorize drive",
    },
    "onedrive": {
        "label": "OneDrive",
        "fields": {"drive_id": "", "drive_type": ""},
        "secrets": ["token"],
        "oauth": "rclone authorize onedrive",
    },
    "sftp": {"label": "SFTP", "fields": {"host": "", "user": "", "port": "22"}, "secrets": ["pass", "key_pem"]},
    "smb": {"label": "SMB / Windows share", "fields": {"host": "", "user": "", "domain": ""}, "secrets": ["pass"]},
    "webdav": {"label": "WebDAV", "fields": {"url": "", "vendor": "other", "user": ""}, "secrets": ["pass"]},
    "local": {"label": "Folder on this machine", "fields": {}, "secrets": []},
    **feeds.TYPES,
}
OBSCURED = {"pass"}  # rclone wants these obscured in its config file
SKIPPED = "not audio, video, a document, an image or a transcript"
TRANSCRIPT_EXT = {".txt", ".text", ".md", ".markdown", ".mdx", ".docx", ".doc", ".pdf", ".srt", ".vtt", ".json", ".jsonl", ".eml", ".ics"}
# what a watched folder picks up (its `kinds`): the first three are from before documents, and read PDFs as transcripts
TAKES = {
    "audio": {"audio"},
    "transcripts": {"transcript"},
    "both": {"audio", "transcript"},
    "documents": {"document", "image"},
    "all": {"audio", "transcript", "document", "image"},
}
WATCH = {
    "kinds": "all",
    "poll_minutes": 5,
    "stable_seconds": 30,
    "backfill": False,
    "include": [],
    "exclude": [],
    "steps": None,
    "pipeline": None,
    "enabled": True,
}


def _bin(cfg):
    b = os.environ.get("RCLONE_BINARY") or cfg["sources"].get("rclone") or shutil.which("rclone")
    if not b:
        raise RuntimeError("rclone isn't installed on this machine")
    return b


def _one_line(v):
    return str(v).replace("\r", "").replace("\n", "\\n")  # no way to smuggle extra lines into the config


def obscure(cfg, value):
    out = subprocess.run([_bin(cfg), "obscure", "-"], input=value, capture_output=True, text=True, timeout=30)  # stdin keeps it out of ps
    if out.returncode:
        raise RuntimeError("rclone obscure failed")
    return out.stdout.strip()


def get(db, sid):
    row = db.one("SELECT record::id(id) AS id, name, type, params, sealed, health, created_at FROM $r", r=R("storage_source", sid))
    if not row:
        raise KeyError(sid)
    return row


def _config(cfg, src):
    name, spec = f"src{src['id']}", BACKENDS[src["type"]]
    lines = [f"[{name}]", f"type = {src['type']}"]
    for k, v in (src.get("params") or {}).items():
        if k in spec["fields"] and v not in (None, ""):
            lines.append(f"{k} = {_one_line(v)}")
    for k, sealed in (src.get("sealed") or {}).items():
        if k in spec["secrets"]:
            v = settings.unseal(cfg, sealed, f"source:{src['id']}:{k}")
            lines.append(f"{k} = {_one_line(obscure(cfg, v) if k in OBSCURED else v)}")
    return name, "\n".join(lines) + "\n"


def _clean_err(text):
    lines = [
        re.sub(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\s+(ERROR|NOTICE|CRITICAL)\s*:?\s*", "", l).strip()
        for l in (text or "").splitlines()
        if l.strip()
    ]
    return (lines[-1] if lines else "")[:300]


def _private_conf(cfg, src):
    name, text = _config(cfg, src)
    d = tempfile.mkdtemp(prefix="la-rclone-")
    conf = os.path.join(d, "rclone.conf")
    fd = os.open(conf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return name, d, conf


def _writeback(db, cfg, src, conf):
    if "token" not in BACKENDS[src["type"]]["secrets"] or not os.path.exists(conf):
        return
    new = next((l.split("=", 1)[1].strip() for l in open(conf) if l.startswith("token")), None)
    old = settings.unseal(cfg, src["sealed"]["token"], f"source:{src['id']}:token") if "token" in (src.get("sealed") or {}) else None
    if new and new != old:
        db.q("UPDATE $r SET sealed.token = $t", r=R("storage_source", src["id"]), t=settings.seal(cfg, new, f"source:{src['id']}:token"))


def run(db, cfg, src, argv, timeout=300):
    if feeds.handles(src):
        raise RuntimeError(f"{BACKENDS[src['type']]['label']} isn't storage: files can't be read or written through rclone")
    name, d, conf = _private_conf(cfg, src)
    try:
        out = subprocess.run(
            [_bin(cfg), "--config", conf, "--retries", "1", "--low-level-retries", "2", *argv(name)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        _writeback(db, cfg, src, conf)
        if out.returncode:
            raise RuntimeError(_clean_err(out.stderr) or f"rclone exited with {out.returncode}")
        return out.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def stream(db, cfg, sid, path, offset=0, count=None):
    """Bytes of a remote file, for playback with Range requests."""
    src = get(db, sid)
    p = check_path(cfg, src, path)
    if feeds.handles(src):
        data = cached_copy(db, cfg, sid, p).read_bytes()
        return iter([data[offset : None if count is None else offset + count]])
    name, d, conf = _private_conf(cfg, src)
    cmd = [_bin(cfg), "--config", conf, "cat", f"{name}:{p}", "--offset", str(offset)] + (
        ["--count", str(count)] if count is not None else []
    )
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def chunks():
        try:
            while True:
                b = proc.stdout.read(1 << 16)
                if not b:
                    break
                yield b
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            shutil.rmtree(d, ignore_errors=True)

    return chunks()


def check_path(cfg, src, path):
    path = (path or "").strip()
    if ".." in pathlib.PurePosixPath(path).parts:
        raise ValueError("paths can't contain ..")
    if src["type"] == "local":
        roots = [os.path.realpath(os.path.expanduser(r)) for r in cfg["sources"].get("local_roots") or []]
        real = os.path.realpath(os.path.expanduser(path or "/"))
        if not any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
            raise ValueError("that folder isn't inside sources.local_roots (set in archive.yaml)")
        return real
    return path.lstrip("/")


def _entries(out, base):
    return [
        {
            "path": f"{base}/{e['Path']}" if base else e["Path"],
            "rel": e["Path"],
            "name": e.get("Name") or pathlib.PurePosixPath(e["Path"]).name,
            "dir": bool(e.get("IsDir")),
            "size": e.get("Size"),
            "modified": e.get("ModTime"),
        }
        for e in json.loads(out or "[]")
    ]


def browse(db, cfg, sid, path=""):
    src = get(db, sid)
    if feeds.handles(src):
        return feeds.browse(cfg, src, check_path(cfg, src, path))
    if src["type"] == "local" and not path:
        return [{"path": r, "rel": r, "name": r, "dir": True} for r in cfg["sources"].get("local_roots") or []]
    p = check_path(cfg, src, path)
    out = run(db, cfg, src, lambda n: ["lsjson", "--max-depth", "1", "--no-mimetype", f"{n}:{p}"], timeout=120)
    return sorted(_entries(out, p.rstrip("/")), key=lambda e: (not e["dir"], e["name"].lower()))


def list_files(db, cfg, src, path, cursor=None):
    """Every file under `path`. A watch hands its `cursor` to sources that keep one (IMAP: only newer messages)."""
    p = check_path(cfg, src, path)
    if feeds.handles(src):
        return feeds.list_files(cfg, src, p, cursor)
    out = run(db, cfg, src, lambda n: ["lsjson", "-R", "--files-only", "--no-mimetype", f"{n}:{p}"], timeout=900)
    return _entries(out, p.rstrip("/"))


def test(db, cfg, sid):
    src = get(db, sid)
    try:
        if feeds.handles(src):
            feeds.test(cfg, src)
        elif src["type"] == "local":
            roots = cfg["sources"].get("local_roots") or []
            if not roots:
                raise RuntimeError("no sources.local_roots are configured")
            target = check_path(cfg, src, roots[0])
        else:
            target = ""
        if not feeds.handles(src):
            run(db, cfg, src, lambda n: ["lsjson", "--max-depth", "1", "--dirs-only", f"{n}:{target}"], timeout=60)
        health = {"ok": True, "checked_at": store.now()}
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
        health = {"ok": False, "checked_at": store.now(), "error": str(e)[:300]}
    db.q("UPDATE $r SET health = $h", r=R("storage_source", sid), h=health)
    return health


def cache_file(cfg, sid, path):
    base = pathlib.Path(cfg["sources"].get("cache_dir") or pathlib.Path(cfg["data_dir"]) / "cache" / "remote")
    return base / (hashlib.sha1(f"{sid}:{path}".encode()).hexdigest() + pathlib.PurePosixPath(path).suffix.lower())


def cached_copy(db, cfg, sid, path):
    src = get(db, sid)
    p = check_path(cfg, src, path)
    if src["type"] == "local":
        return pathlib.Path(p)
    dest = cache_file(cfg, sid, p)
    if not dest.exists() or (feeds.handles(src) and not feeds.immutable(src)):  # a calendar's event may have changed
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        if feeds.handles(src):
            tmp.write_bytes(feeds.fetch(cfg, src, p))
        else:
            run(db, cfg, src, lambda n: ["copyto", f"{n}:{p}", str(tmp)], timeout=6 * 3600)
        tmp.replace(dest)
    return dest


# ---------- managing sources ----------
def _check_params(typ, params, secret_values):
    spec = BACKENDS.get(typ)
    if not spec:
        raise ValueError(f"type is one of: {', '.join(BACKENDS)}")
    bad = sorted(set(params or {}) - set(spec["fields"])) or sorted(set(secret_values or {}) - set(spec["secrets"]))
    if bad:
        raise ValueError(f"{typ} has no option {bad[0]}")
    for k, v in (params or {}).items():
        if not isinstance(v, (str, int, float, bool)):
            raise ValueError(f"{k} must be a plain value")
    return spec


def create(db, cfg, name, typ, params=None, secret_values=None, user=None):
    spec = _check_params(typ, params, secret_values)
    sid = db.next_id("storage_source")
    sealed = {k: settings.seal(cfg, str(v), f"source:{sid}:{k}") for k, v in (secret_values or {}).items() if isinstance(v, str) and v}
    db.q(
        "CREATE $r CONTENT $d",
        r=R("storage_source", sid),
        d=store.clean(
            {
                "name": (name or spec["label"])[:80],
                "type": typ,
                "params": {**spec["fields"], **(params or {})},
                "sealed": sealed,
                "created_at": store.now(),
                "created_by": user,
            }
        ),
    )
    return sid


def update(db, cfg, sid, name=None, params=None, secret_values=None):
    src = get(db, sid)
    _check_params(src["type"], params, secret_values)
    sealed = dict(src.get("sealed") or {})
    for k, v in (secret_values or {}).items():
        if v is None or v == "":
            sealed.pop(k, None)
        elif isinstance(v, str):
            sealed[k] = settings.seal(cfg, v, f"source:{sid}:{k}")
    db.q(
        "UPDATE $r MERGE $d",
        r=R("storage_source", sid),
        d=store.clean({"name": name, "params": {**(src.get("params") or {}), **(params or {})} if params else None}),
    )
    db.q("UPDATE $r SET sealed = $s", r=R("storage_source", sid), s=sealed)  # SET, not MERGE: a MERGE can't drop a removed secret


def remove(db, sid):
    wids = db.values("SELECT VALUE record::id(id) FROM watch_path WHERE source = $s", s=sid)
    db.run(
        ["DELETE remote_file WHERE watch IN $w", "DELETE watch_path WHERE source = $s", "DELETE $r"],
        w=wids,
        s=sid,
        r=R("storage_source", sid),
    )


def view(src):
    spec = BACKENDS[src["type"]]
    return {
        "id": src["id"],
        "name": src["name"],
        "type": src["type"],
        "label": spec["label"],
        "params": src.get("params") or {},
        "secrets": {k: {"secret": True, "set": k in (src.get("sealed") or {})} for k in spec["secrets"]},
        "oauth": spec.get("oauth"),
        "health": src.get("health"),
        "created_at": src.get("created_at"),
    }


def list_sources(db):
    counts = {}
    for w in db.rows("SELECT source FROM watch_path"):
        counts[w["source"]] = counts.get(w["source"], 0) + 1
    return [
        {**view(s), "watches": counts.get(s["id"], 0)}
        for s in db.rows("SELECT record::id(id) AS id, name, type, params, sealed, health, created_at FROM storage_source ORDER BY id")
    ]


# ---------- watched folders ----------
def _check_watch(opts):
    out = {k: opts[k] for k in WATCH if k in opts}
    if out.get("kinds", WATCH["kinds"]) not in TAKES:
        raise ValueError(f"kinds is one of: {', '.join(TAKES)}")
    if not isinstance(out.get("poll_minutes", 5), int) or out.get("poll_minutes", 5) < 1:
        raise ValueError("poll_minutes must be a whole number of at least 1")
    if not isinstance(out.get("stable_seconds", 30), int) or out.get("stable_seconds", 30) < 0:
        raise ValueError("stable_seconds must be 0 or more")
    for k in ("include", "exclude"):
        if not all(isinstance(g, str) for g in out.get(k, [])):
            raise ValueError(f"{k} is a list of patterns such as *.m4a")
    if out.get("pipeline") is not None and not isinstance(out["pipeline"], int):
        raise ValueError("pipeline is a pipeline id")
    if out.get("steps") and not set(out["steps"]) <= set(jobs.STEPS):
        raise ValueError(f"steps are {', '.join(jobs.STEPS)}")
    return out


def create_watch(db, cfg, sid, path, space, user=None, **opts):
    src = get(db, sid)
    p = check_path(cfg, src, path)
    wid = db.next_id("watch_path")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("watch_path", wid),
        d=store.clean(
            {
                **WATCH,
                **_check_watch(opts),
                "source": sid,
                "path": p,
                "space": space,
                "created_at": store.now(),
                "created_by": user,
                "next_scan_at": store.now(),
            }
        ),
    )
    return wid


def update_watch(db, wid, **opts):
    db.q("UPDATE $r MERGE $d", r=R("watch_path", wid), d=_check_watch(opts))
    if {"kinds", "include", "exclude"} & set(opts):  # what it takes changed: look through the mailboxes again
        db.q("UPDATE $r SET cursor = NONE", r=R("watch_path", wid))


def remove_watch(db, wid):
    db.run(["DELETE remote_file WHERE watch = $w", "DELETE $r"], w=wid, r=R("watch_path", wid))


def list_watches(db, spaces=None):
    q = (
        "SELECT record::id(id) AS id, source, path, space, kinds, poll_minutes, stable_seconds, backfill, include, exclude, steps, enabled, "
        "last_scan_at, next_scan_at, last_stats, last_error FROM watch_path"
    )
    rows = db.rows(q + (" WHERE space IN $sp" if spaces is not None else ""), sp=sorted(spaces or []))
    names, srcs = store.space_names(db), {s["id"]: s["name"] for s in db.rows("SELECT record::id(id) AS id, name FROM storage_source")}
    return [{**r, "namespace": names.get(r["space"]), "source_name": srcs.get(r["source"])} for r in rows]


def _when(s):
    s = re.sub(r"(\.\d{6})\d+", r"\1", (s or "").replace("Z", "+00:00"))
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return dt.datetime.now(dt.timezone.utc)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _ingest(db, cfg, src, f, kind, space, by, steps=None, pipeline=None, collection=None):
    """One file of a source becomes a resource in namespace `space` (in `collection`, else the namespace's default),
    and its processing is queued: (id, job). Audio, video, documents and images stay on the source; a transcript is
    imported from it."""
    title, shown = f.get("title") or pathlib.PurePosixPath(f["path"]).stem, f"{src['name']}:{f['path']}"
    if kind == "transcript":
        ns = (db.one("SELECT name FROM $s", s=R("space", space)) or {})["name"]
        local = cached_copy(db, cfg, src["id"], f["path"])
        if feeds.handles(src) and not feeds.immutable(src):
            _same_resource(db, space, src["id"], f["path"], ingest.fingerprint(local))
        rid = ingest.import_transcript(db, cfg, ns, local, title=title, log=lambda *a: None, collection=collection)
        db.q(
            "UPDATE $r MERGE $d",
            r=R("recording", rid),
            d=store.clean({"path": shown, "remote": {"source": src["id"], "path": f["path"]}, "recorded_at": f.get("when")}),
        )
        return rid, jobs.enqueue(db, rid, steps or None, by=by, pipeline=pipeline)
    if f.get("key"):  # the same message in two mailboxes (a Message-ID) is one resource
        fp = "feed-" + hashlib.sha1(f"{src['id']}:{f['key']}".encode()).hexdigest()[:24]
    else:
        fp = "rclone-" + hashlib.sha1(f"{src['id']}:{f['path']}:{f['size']}:{f['modified']}".encode()).hexdigest()[:24]
    known = db.one("SELECT record::id(id) AS id FROM recording WHERE fp_key = $k", k=f"{space}:{fp}")
    if known:
        return known["id"], None
    rid = db.next_id("recording")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("recording", rid),
        d={
            "space": space,
            "collection": store.home(db, space, collection),
            "path": shown,
            "remote": {"source": src["id"], "path": f["path"]},
            "source": kind,  # audio (and video), document or image
            **({} if kind == "audio" else {"media": {"kind": kind}}),
            "fingerprint": fp,
            "fp_key": f"{space}:{fp}",
            "title": title,
            "recorded_at": f.get("when") or _when(f["modified"]).isoformat(timespec="seconds"),
            "size": f["size"],
            "status": "new",
            "created_at": store.now(),
        },
    )
    return rid, jobs.enqueue(db, rid, steps or None, by=by, pipeline=pipeline)


def _same_resource(db, space, sid, path, fp):
    """A calendar event that changed is read again into the resource it already is (found by where it came from), not
    made a second one: that resource takes the new file's fingerprint, which the import then finds."""
    old = db.one(
        "SELECT record::id(id) AS id, fingerprint FROM recording WHERE space = $sp AND remote.source = $s AND remote.path = $p LIMIT 1",
        sp=space,
        s=sid,
        p=path,
    )
    if old and old.get("fingerprint") != fp:
        db.q("UPDATE $r SET fingerprint = $f, fp_key = $k", r=R("recording", old["id"]), f=fp, k=f"{space}:{fp}")


def file_kind(cfg, name, documents_as="document"):
    """'audio' (audio and video), 'document', 'image', 'transcript', or None for a file Lens doesn't import. A file
    that can be either a document or a transcript (a PDF, a Word or text file) is a document unless `documents_as`
    says 'transcript', or the server can't make a PDF of it (then it's read as a transcript)."""
    p = pathlib.PurePosixPath(name)
    ext = p.suffix.lower()
    if p.name.startswith("."):
        return None
    if ext in {e.lower() for e in cfg["audio"]["extensions"]}:
        return "audio"
    if ext in store.DOCUMENT_EXT:
        if ext in TRANSCRIPT_EXT and (documents_as == "transcript" or convert.unavailable(cfg, name)):
            return "transcript"
        return None if convert.unavailable(cfg, name) else "document"
    if ext in store.IMAGE_EXT:
        return "image"
    return "transcript" if ext in TRANSCRIPT_EXT else None


def kind_of(cfg, w, f):
    """'audio', 'document', 'image', 'transcript', or None when a watched folder should ignore the file. The kinds
    from before documents (audio, transcripts, both) read PDFs, Word and text files as transcripts, as they did."""
    kinds = w.get("kinds") or WATCH["kinds"]
    kind = file_kind(cfg, f["path"], "transcript" if kinds in ("transcripts", "both") else "document")
    if kind not in TAKES.get(kinds, ()):
        return None
    rel = f.get("rel", f["path"])
    if (w.get("include") and not any(fnmatch.fnmatch(rel, g) for g in w["include"])) or any(
        fnmatch.fnmatch(rel, g) for g in w.get("exclude") or []
    ):
        return None
    return kind


def poll_watch(db, cfg, wid, log=print):
    w = db.one(
        "SELECT record::id(id) AS id, source, path, space, kinds, poll_minutes, stable_seconds, backfill, include, exclude, steps, pipeline, last_scan_at, "
        "cursor FROM $r",
        r=R("watch_path", wid),
    )
    src = get(db, w["source"])
    first, now = not w.get("last_scan_at"), dt.datetime.now(dt.timezone.utc)
    known = {r["path"]: r for r in db.rows("SELECT path, size, modified, status FROM remote_file WHERE watch = $w", w=wid)}
    gone = deletion.gone_remote(db, w["space"])  # recordings someone deleted stay deleted
    stats = {"seen": 0, "new": 0, "waiting": 0, "skipped": 0, "errors": 0}
    files, held = list_files(db, cfg, src, w["path"], w.get("cursor")), set()
    for f in files:
        kind = kind_of(cfg, w, f)
        if not kind:
            continue
        stats["seen"] += 1
        k = known.get(f["path"])
        if k and k["size"] == f["size"] and k["modified"] == f["modified"] and k["status"] != "waiting":
            continue
        key = R("remote_file", f"{wid}-{hashlib.sha1(f['path'].encode()).hexdigest()[:20]}")
        row = {"watch": wid, "path": f["path"], "size": f["size"], "modified": f["modified"], "seen_at": store.now()}
        if (first and not w.get("backfill")) or (src["id"], f["path"]) in gone:
            db.q("UPSERT $k CONTENT $d", k=key, d={**row, "status": "skipped"})
            stats["skipped"] += 1
        elif (now - _when(f["modified"])).total_seconds() < (w.get("stable_seconds") or 0):
            db.q("UPSERT $k CONTENT $d", k=key, d={**row, "status": "waiting"})
            stats["waiting"] += 1
            held.add(f["path"])
        else:
            try:
                rid, _job = _ingest(db, cfg, src, f, kind, w["space"], f"watch:{wid}", w.get("steps"), w.get("pipeline"))
                db.q("UPSERT $k CONTENT $d", k=key, d={**row, "status": "queued", "recording": rid})
                stats["new"] += 1
            except Exception as e:  # noqa: BLE001 - one bad file must not stop the scan
                db.q("UPSERT $k CONTENT $d", k=key, d={**row, "status": "error", "error": f"{type(e).__name__}: {e}"[:300]})
                stats["errors"] += 1
                log(f"  {f['path']}: {type(e).__name__}: {e}")
    nxt = (now + dt.timedelta(minutes=w.get("poll_minutes") or 5)).isoformat(timespec="seconds")
    db.q(
        "UPDATE $r SET last_scan_at = $t, next_scan_at = $n, last_stats = $s, last_error = NONE",
        r=R("watch_path", wid),
        t=store.now(),
        n=nxt,
        s=stats,
    )
    if any(f.get("cursor") for f in files):
        db.q("UPDATE $r SET cursor = $c", r=R("watch_path", wid), c=feeds.advance(w.get("cursor"), files, held))
    return stats


def imported(db, sid, paths):
    """Which of these files of a source are recordings already, and where: {path: [{recording, namespace}]}."""
    names = store.space_names(db)
    out = {}
    for r in db.rows(
        "SELECT record::id(id) AS id, space, remote.path AS path FROM recording WHERE remote.source = $s AND remote.path IN $p",
        s=sid,
        p=list(paths),
    ):
        out.setdefault(r["path"], []).append({"recording": r["id"], "namespace": names.get(r["space"], "")})
    return out


def import_files(db, cfg, sid, paths, space, by, pipeline=None, collection=None, documents_as="document"):
    """Chosen files of a source, imported into namespace `space` (into `collection`, else its default) now rather than
    watched: audio, video, documents and images stay on the source and run the pipeline (the namespace's, or
    `pipeline`); transcripts are imported, and PDFs, Word and text files too when `documents_as` is 'transcript'. One
    result per path:
    queued (with the recording and job), already (it's a recording of the namespace from this source), skipped (a
    kind of file Lens doesn't import; a folder) or error. Choosing a file on purpose brings back one deleted before."""
    src = get(db, sid)
    folders = {}
    for path in paths:
        p = check_path(cfg, src, path)
        parent = str(pathlib.PurePosixPath(p).parent)
        folders.setdefault("" if parent in (".", "/") and src["type"] != "local" else parent, []).append(p)
    have = imported(db, sid, [p for ps in folders.values() for p in ps])
    ns = store.space_names(db).get(space)
    results = []
    for folder, wanted in folders.items():
        try:
            listing = {e["path"]: e for e in browse(db, cfg, sid, folder)}
        except (ValueError, RuntimeError) as e:
            results += [{"path": p, "status": "error", "detail": str(e)[:300]} for p in wanted]
            continue
        for p in wanted:
            f, kind = listing.get(p), file_kind(cfg, p, documents_as)
            mine = [x for x in have.get(p, []) if x["namespace"] == ns]
            if f is None:
                results.append({"path": p, "status": "error", "detail": "not found"})
            elif f["dir"] or not kind:
                results.append({"path": p, "status": "skipped", "detail": SKIPPED})
            elif mine:
                results.append({"path": p, "status": "already", "recording": mine[0]["recording"]})
            else:
                try:
                    deletion.forget(db, space, path=f"{src['name']}:{p}")  # chosen on purpose: it may come back
                    rid, job = _ingest(db, cfg, src, f, kind, space, by, None, pipeline, collection)
                    results.append(store.clean({"path": p, "status": "queued", "recording": rid, "job": job}))
                except Exception as e:  # noqa: BLE001 - one bad file must not stop the rest
                    results.append({"path": p, "status": "error", "detail": f"{type(e).__name__}: {e}"[:300]})
    return results


def poll_due(db, cfg, log=print):
    done = 0
    for w in db.rows(
        "SELECT record::id(id) AS id, poll_minutes, next_scan_at FROM watch_path WHERE enabled = true AND next_scan_at <= $n", n=store.now()
    ):
        nxt = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=w.get("poll_minutes") or 5)).isoformat(timespec="seconds")
        # taken first, so a second process polling (the API and a worker) doesn't scan it too
        if not db.rows(
            "UPDATE $r SET next_scan_at = $n WHERE next_scan_at = $was RETURN AFTER",
            r=R("watch_path", w["id"]),
            n=nxt,
            was=w["next_scan_at"],
        ):
            continue
        try:
            poll_watch(db, cfg, w["id"], log)
            done += 1
        except Exception as e:  # noqa: BLE001 - recorded on the watch, retried next time
            db.q("UPDATE $r SET last_error = $e, next_scan_at = $n", r=R("watch_path", w["id"]), e=f"{type(e).__name__}: {e}"[:300], n=nxt)
            log(f"  watch {w['id']}: {type(e).__name__}: {e}")
    return done
