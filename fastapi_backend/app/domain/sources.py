"""Storage sources through rclone, and the folders on them that the archive watches.

A source is one connection: S3 or S3-compatible, Dropbox, Google Drive, OneDrive, SFTP, SMB, WebDAV, or a folder on
this machine (only inside sources.local_roots). A watch maps a folder on a source to a namespace: new audio becomes a
recording queued for the whole pipeline; new transcripts are imported and queued for analysis. Credentials are stored
encrypted and handed to rclone in a private temporary config file per call; OAuth tokens rclone refreshes are saved.
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

from . import ingest, jobs, settings, store

R = store.R
BACKENDS = {
    "s3": {"label": "Amazon S3 or S3-compatible", "fields": {"provider": "AWS", "region": "", "endpoint": "", "access_key_id": ""},
           "secrets": ["secret_access_key"]},
    "dropbox": {"label": "Dropbox", "fields": {}, "secrets": ["token"], "oauth": "rclone authorize dropbox"},
    "drive": {"label": "Google Drive", "fields": {"scope": "drive.readonly", "root_folder_id": ""}, "secrets": ["token"], "oauth": "rclone authorize drive"},
    "onedrive": {"label": "OneDrive", "fields": {"drive_id": "", "drive_type": ""}, "secrets": ["token"], "oauth": "rclone authorize onedrive"},
    "sftp": {"label": "SFTP", "fields": {"host": "", "user": "", "port": "22"}, "secrets": ["pass", "key_pem"]},
    "smb": {"label": "SMB / Windows share", "fields": {"host": "", "user": "", "domain": ""}, "secrets": ["pass"]},
    "webdav": {"label": "WebDAV", "fields": {"url": "", "vendor": "other", "user": ""}, "secrets": ["pass"]},
    "local": {"label": "Folder on this machine", "fields": {}, "secrets": []},
}
OBSCURED = {"pass"}  # rclone wants these obscured in its config file
TRANSCRIPT_EXT = {".txt", ".text", ".md", ".markdown", ".mdx", ".docx", ".doc", ".pdf", ".srt", ".vtt", ".json", ".jsonl"}
WATCH = {"kinds": "both", "poll_minutes": 5, "stable_seconds": 30, "backfill": False, "include": [], "exclude": [], "steps": None,
         "pipeline": None, "enabled": True}


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
    lines = [re.sub(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\s+(ERROR|NOTICE|CRITICAL)\s*:?\s*", "", l).strip() for l in (text or "").splitlines() if l.strip()]
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
    name, d, conf = _private_conf(cfg, src)
    try:
        out = subprocess.run([_bin(cfg), "--config", conf, "--retries", "1", "--low-level-retries", "2", *argv(name)],
                             capture_output=True, text=True, timeout=timeout)
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
    name, d, conf = _private_conf(cfg, src)
    cmd = [_bin(cfg), "--config", conf, "cat", f"{name}:{p}", "--offset", str(offset)] + (["--count", str(count)] if count is not None else [])
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
    return [{"path": f"{base}/{e['Path']}" if base else e["Path"], "rel": e["Path"], "name": e.get("Name") or pathlib.PurePosixPath(e["Path"]).name,
             "dir": bool(e.get("IsDir")), "size": e.get("Size"), "modified": e.get("ModTime")} for e in json.loads(out or "[]")]


def browse(db, cfg, sid, path=""):
    src = get(db, sid)
    if src["type"] == "local" and not path:
        return [{"path": r, "rel": r, "name": r, "dir": True} for r in cfg["sources"].get("local_roots") or []]
    p = check_path(cfg, src, path)
    out = run(db, cfg, src, lambda n: ["lsjson", "--max-depth", "1", "--no-mimetype", f"{n}:{p}"], timeout=120)
    return sorted(_entries(out, p.rstrip("/")), key=lambda e: (not e["dir"], e["name"].lower()))


def list_files(db, cfg, src, path):
    p = check_path(cfg, src, path)
    out = run(db, cfg, src, lambda n: ["lsjson", "-R", "--files-only", "--no-mimetype", f"{n}:{p}"], timeout=900)
    return _entries(out, p.rstrip("/"))


def test(db, cfg, sid):
    src = get(db, sid)
    try:
        if src["type"] == "local":
            roots = cfg["sources"].get("local_roots") or []
            if not roots:
                raise RuntimeError("no sources.local_roots are configured")
            target = check_path(cfg, src, roots[0])
        else:
            target = ""
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
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
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
    db.q("CREATE $r CONTENT $d", r=R("storage_source", sid), d=store.clean({"name": (name or spec["label"])[:80], "type": typ,
                                                                            "params": {**spec["fields"], **(params or {})}, "sealed": sealed,
                                                                            "created_at": store.now(), "created_by": user}))
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
    db.q("UPDATE $r MERGE $d", r=R("storage_source", sid), d=store.clean({"name": name, "params": {**(src.get("params") or {}), **(params or {})} if params else None}))
    db.q("UPDATE $r SET sealed = $s", r=R("storage_source", sid), s=sealed)  # SET, not MERGE: a MERGE can't drop a removed secret


def remove(db, sid):
    wids = db.values("SELECT VALUE record::id(id) FROM watch_path WHERE source = $s", s=sid)
    db.run(["DELETE remote_file WHERE watch IN $w", "DELETE watch_path WHERE source = $s", "DELETE $r"], w=wids, s=sid, r=R("storage_source", sid))


def view(src):
    spec = BACKENDS[src["type"]]
    return {"id": src["id"], "name": src["name"], "type": src["type"], "label": spec["label"], "params": src.get("params") or {},
            "secrets": {k: {"secret": True, "set": k in (src.get("sealed") or {})} for k in spec["secrets"]}, "oauth": spec.get("oauth"),
            "health": src.get("health"), "created_at": src.get("created_at")}


def list_sources(db):
    counts = {}
    for w in db.rows("SELECT source FROM watch_path"):
        counts[w["source"]] = counts.get(w["source"], 0) + 1
    return [{**view(s), "watches": counts.get(s["id"], 0)} for s in
            db.rows("SELECT record::id(id) AS id, name, type, params, sealed, health, created_at FROM storage_source ORDER BY id")]


# ---------- watched folders ----------
def _check_watch(opts):
    out = {k: opts[k] for k in WATCH if k in opts}
    if out.get("kinds", "both") not in ("audio", "transcripts", "both"):
        raise ValueError("kinds is audio, transcripts or both")
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
    db.q("CREATE $r CONTENT $d", r=R("watch_path", wid), d=store.clean({**WATCH, **_check_watch(opts), "source": sid, "path": p, "space": space,
                                                                        "created_at": store.now(), "created_by": user, "next_scan_at": store.now()}))
    return wid


def update_watch(db, wid, **opts):
    db.q("UPDATE $r MERGE $d", r=R("watch_path", wid), d=_check_watch(opts))


def remove_watch(db, wid):
    db.run(["DELETE remote_file WHERE watch = $w", "DELETE $r"], w=wid, r=R("watch_path", wid))


def list_watches(db, spaces=None):
    q = ("SELECT record::id(id) AS id, source, path, space, kinds, poll_minutes, stable_seconds, backfill, include, exclude, steps, enabled, "
         "last_scan_at, next_scan_at, last_stats, last_error FROM watch_path")
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


def _ingest(db, cfg, src, w, f, kind):
    title, shown = pathlib.PurePosixPath(f["path"]).stem, f"{src['name']}:{f['path']}"
    if kind == "transcript":
        ns = (db.one("SELECT name FROM $s", s=R("space", w["space"])) or {})["name"]
        rid = ingest.import_transcript(db, cfg, ns, cached_copy(db, cfg, src["id"], f["path"]), title=title, log=lambda *a: None)
        db.q("UPDATE $r SET path = $p, remote = $m", r=R("recording", rid), p=shown, m={"source": src["id"], "path": f["path"]})
        jobs.enqueue(db, rid, w.get("steps") or None, by=f"watch:{w['id']}", pipeline=w.get("pipeline"))
        return rid
    fp = "rclone-" + hashlib.sha1(f"{src['id']}:{f['path']}:{f['size']}:{f['modified']}".encode()).hexdigest()[:24]
    known = db.one("SELECT record::id(id) AS id FROM recording WHERE fp_key = $k", k=f"{w['space']}:{fp}")
    if known:
        return known["id"]
    rid = db.next_id("recording")
    db.q("CREATE $r CONTENT $d", r=R("recording", rid), d={"space": w["space"], "path": shown, "remote": {"source": src["id"], "path": f["path"]},
                                                            "source": "audio", "fingerprint": fp, "fp_key": f"{w['space']}:{fp}", "title": title,
                                                            "recorded_at": _when(f["modified"]).isoformat(timespec="seconds"), "size": f["size"],
                                                            "status": "new", "created_at": store.now()})
    jobs.enqueue(db, rid, w.get("steps") or None, by=f"watch:{w['id']}", pipeline=w.get("pipeline"))
    return rid


def kind_of(cfg, w, f):
    """'audio', 'transcript', or None when a watched folder should ignore the file."""
    p = pathlib.PurePosixPath(f["path"])
    ext = p.suffix.lower()
    kind = "audio" if ext in {e.lower() for e in cfg["audio"]["extensions"]} else "transcript" if ext in TRANSCRIPT_EXT else None
    if not kind or p.name.startswith(".") or (w.get("kinds") or "both") not in ("both", "audio" if kind == "audio" else "transcripts"):
        return None
    rel = f.get("rel", f["path"])
    if (w.get("include") and not any(fnmatch.fnmatch(rel, g) for g in w["include"])) or any(fnmatch.fnmatch(rel, g) for g in w.get("exclude") or []):
        return None
    return kind


def poll_watch(db, cfg, wid, log=print):
    w = db.one("SELECT record::id(id) AS id, source, path, space, kinds, poll_minutes, stable_seconds, backfill, include, exclude, steps, pipeline, last_scan_at "
               "FROM $r", r=R("watch_path", wid))
    src = get(db, w["source"])
    first, now = not w.get("last_scan_at"), dt.datetime.now(dt.timezone.utc)
    audio_ext = {e.lower() for e in cfg["audio"]["extensions"]}
    known = {r["path"]: r for r in db.rows("SELECT path, size, modified, status FROM remote_file WHERE watch = $w", w=wid)}
    stats = {"seen": 0, "new": 0, "waiting": 0, "skipped": 0, "errors": 0}
    for f in list_files(db, cfg, src, w["path"]):
        kind = kind_of(cfg, w, f)
        if not kind:
            continue
        stats["seen"] += 1
        k = known.get(f["path"])
        if k and k["size"] == f["size"] and k["modified"] == f["modified"] and k["status"] != "waiting":
            continue
        key = R("remote_file", f"{wid}-{hashlib.sha1(f['path'].encode()).hexdigest()[:20]}")
        row = {"watch": wid, "path": f["path"], "size": f["size"], "modified": f["modified"], "seen_at": store.now()}
        if first and not w.get("backfill"):
            db.q("UPSERT $k CONTENT $d", k=key, d={**row, "status": "skipped"})
            stats["skipped"] += 1
        elif (now - _when(f["modified"])).total_seconds() < (w.get("stable_seconds") or 0):
            db.q("UPSERT $k CONTENT $d", k=key, d={**row, "status": "waiting"})
            stats["waiting"] += 1
        else:
            try:
                rid = _ingest(db, cfg, src, w, f, kind)
                db.q("UPSERT $k CONTENT $d", k=key, d={**row, "status": "queued", "recording": rid})
                stats["new"] += 1
            except Exception as e:  # noqa: BLE001 - one bad file must not stop the scan
                db.q("UPSERT $k CONTENT $d", k=key, d={**row, "status": "error", "error": f"{type(e).__name__}: {e}"[:300]})
                stats["errors"] += 1
                log(f"  {f['path']}: {type(e).__name__}: {e}")
    nxt = (now + dt.timedelta(minutes=w.get("poll_minutes") or 5)).isoformat(timespec="seconds")
    db.q("UPDATE $r SET last_scan_at = $t, next_scan_at = $n, last_stats = $s, last_error = NONE", r=R("watch_path", wid), t=store.now(), n=nxt, s=stats)
    return stats


def poll_due(db, cfg, log=print):
    done = 0
    for w in db.rows("SELECT record::id(id) AS id, poll_minutes, next_scan_at FROM watch_path WHERE enabled = true AND next_scan_at <= $n", n=store.now()):
        try:
            poll_watch(db, cfg, w["id"], log)
            done += 1
        except Exception as e:  # noqa: BLE001 - recorded on the watch, retried next time
            nxt = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=w.get("poll_minutes") or 5)).isoformat(timespec="seconds")
            db.q("UPDATE $r SET last_error = $e, next_scan_at = $n", r=R("watch_path", w["id"]), e=f"{type(e).__name__}: {e}"[:300], n=nxt)
            log(f"  watch {w['id']}: {type(e).__name__}: {e}")
    return done
