"""Where Lens keeps the files it makes its own, such as a note's attachments (docs/storage.md): on this machine, or on
any storage connection (S3 or S3-compatible, Google Drive, Dropbox, OneDrive, SFTP, SMB, WebDAV, a folder), through rclone.

Settings → Storage (the `files` section) chooses it: `store` is "local" (data_dir/objects, the default) or
"connection" (`connection`, a storage source of sources.py, under its `folder`). Files going to a connection are
encrypted with their namespace's key (keyring.py) before they leave the machine, so the remote only ever holds
ciphertext. `crypt` wraps the connection in an rclone crypt remote too, which also hides the names and sizes of what is
kept there; its password is made here, kept sealed, and never needs typing.

Each file records where it went (`where`, from put()), so changing the setting never strands what is already stored:
older files are read from wherever they were written.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import tempfile
import time

from . import feeds, keyring, settings, sources, store

R = store.R
KEY_RX = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}(/[a-z0-9][a-z0-9._-]{0,80}){0,8}$")
CRYPT = "lenscrypt"
TIMEOUT = 3600  # seconds one copy may take


def _key(key):
    if not KEY_RX.match(key or ""):
        raise ValueError(f"not a storage key: {key!r}")
    return key


def _local_root(cfg):
    return pathlib.Path(cfg["data_dir"]) / "objects"


def _tmp(cfg):
    d = pathlib.Path(cfg["data_dir"]) / "tmp"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def target(db, cfg):
    """Where new files go, from the `files` settings: {"store": "local"} or {"store": "connection", ...}."""
    f = cfg.get("files") or {}
    if f.get("store") != "connection":
        return {"store": "local"}
    if not f.get("connection"):
        raise RuntimeError("Settings → Storage keeps files on a connection, but none is chosen")
    return {"store": "connection", "connection": int(f["connection"]), "folder": _folder(f.get("folder")), "crypt": bool(f.get("crypt"))}


def _folder(folder):
    folder = str(folder or "").strip().rstrip("/")  # a leading / stays: a folder on this machine is a full path
    if ".." in pathlib.PurePosixPath(folder).parts or "\n" in folder:
        raise ValueError("the folder can't contain .. or line breaks")
    return folder


def check_connection(db, sid):
    """ValueError unless `sid` is a storage connection files can be kept on (not an email account or a calendar)."""
    try:
        src = sources.get(db, int(sid))
    except (KeyError, TypeError, ValueError):
        raise ValueError("that connection doesn't exist") from None
    if feeds.handles(src):
        raise ValueError(f"{sources.BACKENDS[src['type']]['label']} isn't storage: choose a storage connection")
    return src


# ---------- the crypt remote's password ----------
def _crypt_secrets(db, cfg):
    """rclone crypt's two passwords, made the first time they are needed and kept sealed like any setting's secret."""
    row = db.one("SELECT sealed FROM $r", r=R("app_setting", "files_crypt")) or {}
    sealed = row.get("sealed") or {}
    if not sealed.get("password"):
        sealed = {k: settings.seal(cfg, secrets.token_urlsafe(32), f"files:crypt:{k}") for k in ("password", "salt")}
        db.q("UPSERT $r CONTENT $d", r=R("app_setting", "files_crypt"), d={"data": "{}", "sealed": sealed, "updated_at": store.now()})
    return {k: settings.unseal(cfg, sealed[k], f"files:crypt:{k}") for k in ("password", "salt")}


# ---------- rclone ----------
@contextlib.contextmanager
def _remote(db, cfg, where):
    """A private rclone config for `where`, and the remote prefix files go under ("name:folder/" or "lenscrypt:")."""
    src = check_connection(db, where["connection"])
    folder = where.get("folder") or ""
    if src["type"] == "local":
        folder = sources.check_path(cfg, src, folder)  # only inside sources.local_roots
    else:
        folder = folder.lstrip("/")
    name, text = sources._config(cfg, src)
    base = f"{name}:{folder}"
    if where.get("crypt"):
        pw = _crypt_secrets(db, cfg)
        text += (
            f"[{CRYPT}]\ntype = crypt\nremote = {sources._one_line(base)}\n"
            f"password = {sources.obscure(cfg, pw['password'])}\npassword2 = {sources.obscure(cfg, pw['salt'])}\n"
        )
        prefix = f"{CRYPT}:"
    else:
        prefix = base.rstrip("/") + "/" if folder else base
    d = tempfile.mkdtemp(prefix="la-rclone-")
    conf = os.path.join(d, "rclone.conf")
    fd = os.open(conf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        yield conf, prefix
        sources._writeback(db, cfg, src, conf)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _missing(err):
    m = str(err).lower()
    return "not found" in m or "doesn't exist" in m or "does not exist" in m or "nosuchkey" in m


def _rclone(cfg, conf, *argv, timeout=TIMEOUT):
    out = subprocess.run(
        [sources._bin(cfg), "--config", conf, "--retries", "2", "--low-level-retries", "3", *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if out.returncode:
        raise RuntimeError(sources._clean_err(out.stderr) or f"rclone exited with {out.returncode}")
    return out.stdout


# ---------- files ----------
def _send(db, cfg, where, key, path):
    """Copy the bytes at `path` to `key` as they are."""
    if where["store"] == "local":
        dest = _local_root(cfg) / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}")
        try:
            shutil.copyfile(path, tmp)
            os.replace(tmp, dest)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
        return
    with _remote(db, cfg, where) as (conf, prefix):
        _rclone(cfg, conf, "copyto", str(path), prefix + key)


def _fetch(db, cfg, where, key, dest):
    """Copy what is kept at `key` to `dest` as it is. KeyError when it isn't there."""
    if where.get("store", "local") == "local":
        p = _local_root(cfg) / key
        if not p.is_file():
            raise KeyError(key)
        shutil.copyfile(p, dest)
        return
    with _remote(db, cfg, where) as (conf, prefix):
        try:
            _rclone(cfg, conf, "copyto", prefix + key, str(dest))
        except RuntimeError as e:
            if _missing(e):
                raise KeyError(key) from None
            raise


@contextlib.contextmanager
def _scratch(cfg):
    fd, tmp = tempfile.mkstemp(dir=_tmp(cfg))
    os.close(fd)
    try:
        yield tmp
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def put(db, cfg, sid, key, path, where=None):
    """Keep the file at `path` (left as it is) under `key` for namespace `sid`, where the settings say (or at
    `where`). Returns where it went, to keep beside the file's row. On this machine it is encrypted as Lens's other
    files are (keyring.protect); going to a connection, always. A vault nobody has unlocked raises keyring.Locked."""
    key, where = _key(key), where or target(db, cfg)
    with _scratch(cfg) as tmp:
        shutil.copyfile(path, tmp)
        if where["store"] == "local":
            keyring.protect(db, cfg, sid, tmp)
        else:
            keyring.encrypt_file(db, cfg, sid, tmp, force=True)  # it leaves the machine: only ever as ciphertext
        _send(db, cfg, where, key, tmp)
    return where


@contextlib.contextmanager
def open_file(db, cfg, where, key):
    """The plain bytes of a kept file, as a file to read. KeyError when it isn't there."""
    key, where = _key(key), where or {"store": "local"}
    with _scratch(cfg) as tmp:
        _fetch(db, cfg, where, key, tmp)
        with keyring.open_plain(db, cfg, tmp) as f:
            yield f


def delete(db, cfg, where, key):
    """Remove a kept file; one already gone is fine."""
    key, where = _key(key), where or {"store": "local"}
    if where.get("store", "local") == "local":
        with contextlib.suppress(FileNotFoundError):
            (_local_root(cfg) / key).unlink()
        return
    with _remote(db, cfg, where) as (conf, prefix):
        try:
            _rclone(cfg, conf, "deletefile", prefix + key, timeout=120)
        except RuntimeError as e:
            if not _missing(e):
                raise


def test(db, cfg, where=None):
    """Write a small file where files would go, read it back and remove it: {ok, store, seconds} or {ok, store, error}."""
    t0, probe, store_ = time.monotonic(), secrets.token_bytes(64), None
    try:
        where = where or target(db, cfg)
        store_, key = where["store"], f"lens-check/{secrets.token_hex(8)}"
        with _scratch(cfg) as tmp, _scratch(cfg) as back:
            pathlib.Path(tmp).write_bytes(probe)
            _send(db, cfg, where, key, tmp)
            _fetch(db, cfg, where, key, back)
            delete(db, cfg, where, key)
            if pathlib.Path(back).read_bytes() != probe:
                raise RuntimeError("what was read back isn't what was written")
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "store": store_, "error": str(e)[:300] or type(e).__name__}
    return {"ok": True, "store": store_, "seconds": round(time.monotonic() - t0, 2)}
