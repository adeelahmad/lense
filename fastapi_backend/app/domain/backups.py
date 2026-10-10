"""The database backups in <data_dir>/backups (docs/database.md, Backups): listed, taken, downloaded, deleted and
restored from the web app, and taken on a schedule. migrations.backup() takes them: before upgrades (before-upgrade-*),
on request (manual-*), on backups.schedule (scheduled-*, the newest backups.keep kept) and before a restore
(before-restore-*, the newest RESTORE_KEEP kept).

Restoring replaces the whole database with a backup's: a server's database is removed and the export imported into it;
an embedded folder is swapped for the copy, with the database closed meanwhile. What was there is backed up first and
put back if importing fails. A backup from an older Lens is brought up to date afterwards (its schema, then the named
steps), and every process reloads its settings.

Backups hold the database only: files Lens keeps under data_dir, and secret.key (keep that apart from the backups,
docs/encryption.md), are backed up with the data folder.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import gzip
import json
import logging
import pathlib
import re
import shutil
import threading
import time

from . import migrations, schedule, store
from .store import R

log = logging.getLogger("lens")

RESTORE_KEEP = 3


class BackupError(ValueError):
    """A backup that can't be used here, or work that can't start."""


class Busy(BackupError):
    """A restore is already running."""


NAME = re.compile(r"^(?P<label>[a-z][a-z-]{0,40})-(?P<stamp>\d{8}-\d{6})(?:-\d+)?\.(?P<kind>surql\.gz|surrealkv)$")


def folder(db):
    return pathlib.Path(db.data_dir or ".") / "backups"


def _size(p):
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return p.stat().st_size


def listing(db):
    """Newest first: name, label, kind (export or folder), size and when it was taken."""
    d = folder(db)
    out = []
    if d.is_dir():
        for p in d.iterdir():
            m = NAME.match(p.name)
            if not m:
                continue
            at = dt.datetime.strptime(m["stamp"], "%Y%m%d-%H%M%S").replace(tzinfo=dt.timezone.utc)
            try:
                size = _size(p)
            except OSError:
                continue
            out.append(
                {
                    "name": p.name,
                    "label": m["label"],
                    "kind": "folder" if m["kind"] == "surrealkv" else "export",
                    "bytes": size,
                    "taken_at": at.isoformat(timespec="seconds"),
                }
            )
    return sorted(out, key=lambda b: (b["taken_at"], b["name"]), reverse=True)


def path(db, name):
    """The backup called `name`, or ValueError; never anything outside the backups folder."""
    if not NAME.match(name or ""):
        raise ValueError("no such backup")
    p = folder(db) / name
    if not p.exists():
        raise ValueError("no such backup")
    return p


def take(db):
    """Back up now; returns the backup's entry, or None for an in-memory database."""
    dst = migrations.backup(db, "manual")
    if dst is None:
        return None
    return next((b for b in listing(db) if b["name"] == dst.name), None)


def delete(db, name):
    p = path(db, name)
    shutil.rmtree(p) if p.is_dir() else p.unlink()


def in_progress(db):
    """Backups being written now, by any process sharing the data folder."""
    d = folder(db)
    return sorted(p.name.removesuffix(".part") for p in d.glob("*.part")) if d.is_dir() else []


def prune(db, label, keep):
    """Only the newest `keep` backups with this label are kept."""
    for old in [b for b in listing(db) if b["label"] == label][keep:]:
        with contextlib.suppress(OSError, ValueError):
            delete(db, old["name"])


def _take(db, label):
    dst = migrations.backup(db, label)
    return dst and next((b for b in listing(db) if b["name"] == dst.name), None)


# ---------- restoring one ----------
def _seq(db):
    return db.values("SELECT VALUE n FROM $r", r=R("seq", "settings")) or [0]


def usable(db, name):
    """The backup `name`, if this database can be restored from it: ValueError if there is none, BackupError if it's
    of the other kind of database."""
    src = path(db, name)
    got = "folder" if src.is_dir() else "export"
    want = "folder" if db.embedded else "export"
    if got != want:
        raise BackupError(
            f"{name} is a backup of {'an embedded database' if got == 'folder' else 'a SurrealDB server'}, and this "
            f"Lens uses {'an embedded database' if want == 'folder' else 'a SurrealDB server'}"
        )
    return src


def restore(db, cfg, name):
    """Replace the database with the backup `name`. Returns the before-restore backup's entry (None when the database
    held nothing worth keeping)."""
    src = usable(db, name)
    seq_was = _seq(db)[0]
    safety = _take(db, "before-restore") if migrations._has_data(db) else None
    if safety:
        prune(db, "before-restore", RESTORE_KEEP)
    if db.embedded:
        _swap(db, src)
    else:
        _import(db, src, safety and path(db, safety["name"]))
    _after(db, cfg, seq_was)
    return safety


def _swap(db, src):
    live = pathlib.Path(db.url.partition("://")[2])
    with db.closed():
        part = live.with_name(live.name + ".restoring")
        shutil.rmtree(part, ignore_errors=True)
        shutil.copytree(src, part)
        old = live.with_name(live.name + ".replaced")
        shutil.rmtree(old, ignore_errors=True)
        if live.exists():
            live.rename(old)
        part.rename(live)
        shutil.rmtree(old, ignore_errors=True)  # the before-restore backup holds it


def _clear(db):
    """Remove Lens's database on the server and define it again, empty."""
    import urllib.request

    ns, name = db._target
    req = urllib.request.Request(
        migrations.server_url(db, "sql"),
        data=f"REMOVE DATABASE IF EXISTS `{name}`; DEFINE DATABASE `{name}`;".encode(),
        headers={**migrations.server_headers(db, database=False), "Accept": "application/json"},
        method="POST",
    )
    _check(migrations.server_opener().open(req, timeout=120).read(), "clearing the database")


def _check(body, what):
    try:
        results = json.loads(body or b"[]")
    except ValueError:
        return
    for r in results if isinstance(results, list) else []:
        if isinstance(r, dict) and r.get("status") == "ERR":
            raise RuntimeError(f"{what} failed: {r.get('result')}")


def _post_import(db, sql: pathlib.Path):
    import urllib.request

    with open(sql, "rb") as body:
        req = urllib.request.Request(
            migrations.server_url(db, "import"),
            data=body,
            headers={
                **migrations.server_headers(db),
                "Accept": "application/json",
                "Content-Type": "text/plain",
                "Content-Length": str(sql.stat().st_size),
            },
            method="POST",
        )
        _check(migrations.server_opener().open(req, timeout=3600).read(), "importing the backup")


def _unzip(src: pathlib.Path) -> pathlib.Path:
    out = src.with_name(f".{src.name}.{time.time_ns()}.surql")
    with gzip.open(src, "rb") as i, open(out, "wb") as o:
        shutil.copyfileobj(i, o, 1 << 20)
    return out


def _import(db, src, safety):
    sql = _unzip(src)
    try:
        _clear(db)
        _post_import(db, sql)
    except Exception:
        if safety is not None:  # put back what was there
            back = _unzip(safety)
            try:
                _clear(db)
                _post_import(db, back)
            except Exception:  # noqa: BLE001
                log.exception("putting the database back after a failed restore failed too; restore %s", safety.name)
            finally:
                back.unlink(missing_ok=True)
        raise
    finally:
        sql.unlink(missing_ok=True)


def _after(db, cfg, seq_was):
    """A restored database gets this Lens's schema and upgrades, and every process rebuilds its settings."""
    store.prepare(db, cfg)
    migrations.run(db, backup_first=False)
    n = max(seq_was, _seq(db)[0]) + 1
    db.q("UPSERT $r SET n = $n", r=R("seq", "settings"), n=n)


# ---------- restoring in the background (one at a time per process) ----------
work = {"running": False, "name": None, "error": None, "finished_at": None, "before": None}
_lock = threading.Lock()


def start_restore(db, cfg, name, then=None):
    """Restore `name` in the background; `work` follows it. Busy if a restore is already running here.
    then(before_entry) runs once it has worked."""
    with _lock:
        if work["running"]:
            raise Busy(f"{work['name']} is being restored; wait for it to finish")
        work.update(running=True, name=name, error=None, finished_at=None, before=None)

    def go():
        try:
            out = restore(db, cfg, name)
            work.update(before=out and out["name"])
            if then:
                then(out)
        except Exception as e:  # noqa: BLE001 - shown in the app
            log.exception("restoring %s failed", name)
            work.update(error=f"{type(e).__name__}: {e}"[:500])
        finally:
            work.update(running=False, finished_at=store.now())

    threading.Thread(target=go, daemon=True, name="backup-restore").start()


# ---------- on a schedule ----------
def schedule_state(db):
    return db.one("SELECT * OMIT id FROM $r", r=R("backup_schedule", "next")) or {}


def next_at(cfg, after=None):
    b = cfg["backups"]
    when = schedule.next_after(b["schedule"], b.get("timezone") or "UTC", after) if b.get("schedule") else None
    return when.isoformat().replace("+00:00", "Z") if when else None


def plan(cfg):
    """What the next time was worked out from: a change to either means working it out again."""
    b = cfg["backups"]
    return f"{b.get('schedule') or ''}|{b.get('timezone') or 'UTC'}"


def run_due(db, cfg, log=None, now=None):
    """Take the scheduled backup when it's due; True if this process took it. Processes sharing the database take
    turns: whoever moves the next time on takes it."""
    b = cfg["backups"]
    now = now or dt.datetime.now(dt.timezone.utc)
    st = schedule_state(db)
    plan_ = plan(cfg)
    if st.get("plan") != plan_ or not st.get("at") and b.get("schedule"):
        db.q("UPSERT $r MERGE $d", r=R("backup_schedule", "next"), d={"plan": plan_, "at": next_at(cfg, now)})
        return False
    if not b.get("schedule") or not st.get("at") or st["at"] > now.isoformat().replace("+00:00", "Z"):
        return False
    took = db.rows(
        "UPDATE $r SET at = $nxt WHERE at = $was RETURN AFTER", r=R("backup_schedule", "next"), nxt=next_at(cfg, now), was=st["at"]
    )
    if not took:
        return False
    try:
        got = _take(db, "scheduled")
        prune(db, "scheduled", int(b.get("keep") or 7))
        db.q(
            "UPDATE $r MERGE $d",
            r=R("backup_schedule", "next"),
            d={"last_at": store.now(), "last_name": got and got["name"], "last_error": None},
        )
    except Exception as e:  # noqa: BLE001 - shown in the app; tried again at the next time
        db.q(
            "UPDATE $r MERGE $d", r=R("backup_schedule", "next"), d={"last_at": store.now(), "last_error": f"{type(e).__name__}: {e}"[:500]}
        )
        if log:
            log(f"scheduled backup: {type(e).__name__}: {e}")
        return True
    if got and remote(cfg):
        send_quietly(db, cfg, got["name"], log)
    return True


# ---------- copies on a storage connection ----------
# Every new backup (taken by hand or on the schedule) is also copied to backups.connection, under backups.folder, as
# <name>.lensbk: the export (or the embedded folder as a .tar.gz), encrypted with a key derived from secret.key. Not with
# a namespace's key: those are kept in the database, and a backup must open when the database is gone. So keep
# secret.key (or ARCHIVE_SECRET_KEY) somewhere other than the connection, or the copies can't be read.
SEALED = ".lensbk"
MAGIC = b"LENSBK1"
CHUNK = 1 << 20
HEADER_LEN = len(MAGIC) + 7


def _box(cfg):
    from . import keyring, settings

    return keyring._aes(keyring.derive(settings.secret_key(cfg), "lens/backup/v1"))


def _nonce(prefix, i, last):
    import struct

    return prefix + struct.pack(">IB", i, 1 if last else 0)


def seal(cfg, src, dst):
    """Encrypt the file at src into dst: AES-256-GCM in CHUNK pieces, each bound to its place and the header."""
    import secrets as _secrets

    box, prefix = _box(cfg), _secrets.token_bytes(7)
    header = MAGIC + prefix
    with open(src, "rb") as i, open(dst, "wb") as o:
        o.write(header)
        n, part = 0, i.read(CHUNK)
        while True:
            nxt = i.read(CHUNK)
            o.write(box.encrypt(_nonce(prefix, n, not nxt), part, header))
            if not nxt:
                break
            n, part = n + 1, nxt


def unseal(cfg, src, dst):
    """The plain file of seal(); BackupError if it's damaged or was sealed with another secret.key."""
    from cryptography.exceptions import InvalidTag

    box = _box(cfg)
    with open(src, "rb") as i, open(dst, "wb") as o:
        header = i.read(HEADER_LEN)
        if len(header) != HEADER_LEN or not header.startswith(MAGIC):
            raise BackupError("that isn't a Lens backup copy")
        prefix, n = header[len(MAGIC) :], 0
        part = i.read(CHUNK + 16)
        while True:
            nxt = i.read(CHUNK + 16)
            try:
                o.write(box.decrypt(_nonce(prefix, n, not nxt), part, header))
            except InvalidTag:
                raise BackupError("this copy doesn't open: it was cut short or changed, or sealed with another secret.key") from None
            if not nxt:
                break
            n, part = n + 1, nxt


def remote(cfg):
    """Where copies go ({store: connection, ...} for blobs), or None when backups.connection isn't set."""
    b = cfg.get("backups") or {}
    if not b.get("connection"):
        return None
    return {"store": "connection", "connection": int(b["connection"]), "folder": b.get("folder") or "lens-backups", "crypt": False}


def _package(src: pathlib.Path, out: pathlib.Path):
    """A backup as one file: the export as it is, an embedded folder as a .tar.gz."""
    import tarfile

    if not src.is_dir():
        shutil.copyfile(src, out)
        return
    with tarfile.open(out, "w:gz") as tar:
        tar.add(src, arcname=src.name)


def send(db, cfg, name):
    """Copy the backup `name` to the connection; prunes the scheduled copies there to backups.keep."""
    from . import blobs

    where = remote(cfg)
    if where is None:
        raise BackupError("choose a storage connection for backups first")
    src = path(db, name)
    tmp = folder(db) / f".send-{time.time_ns()}"
    sealed = tmp.with_name(tmp.name + SEALED)
    try:
        _package(src, tmp)
        seal(cfg, tmp, sealed)
        blobs._send(db, cfg, where, name + SEALED, sealed)
    finally:
        tmp.unlink(missing_ok=True)
        sealed.unlink(missing_ok=True)
    keep = int((cfg.get("backups") or {}).get("keep") or 7)
    for old in [b for b in remote_listing(db, cfg) if b["label"] == "scheduled"][keep:]:
        with contextlib.suppress(RuntimeError):
            remote_delete(db, cfg, old["name"])
    db.q("UPSERT $r MERGE $d", r=R("backup_remote", "last"), d={"at": store.now(), "name": name, "error": None})


def send_quietly(db, cfg, name, log=None):
    """send(), recording a failure instead of raising (the backup itself is kept here either way)."""
    try:
        send(db, cfg, name)
    except Exception as e:  # noqa: BLE001 - shown in the app
        db.q(
            "UPSERT $r MERGE $d",
            r=R("backup_remote", "last"),
            d={"at": store.now(), "name": name, "error": f"{type(e).__name__}: {e}"[:500]},
        )
        if log:
            log(f"copying backup {name} to the connection: {type(e).__name__}: {e}")


def remote_state(db):
    return db.one("SELECT * OMIT id FROM $r", r=R("backup_remote", "last")) or {}


def remote_listing(db, cfg):
    """The copies on the connection, newest first, as listing() has them (bytes: the sealed copy's size)."""
    from . import blobs

    where = remote(cfg)
    if where is None:
        return []
    with blobs._remote(db, cfg, where) as (conf, prefix):
        try:
            out = blobs._rclone(cfg, conf, "lsjson", "--files-only", prefix, timeout=120)
        except RuntimeError as e:
            if blobs._missing(e):
                return []
            raise
    got = []
    for f in json.loads(out or "[]"):
        name = str(f.get("Name") or "").removesuffix(SEALED)
        m = NAME.match(name)
        if not m or not str(f.get("Name")).endswith(SEALED):
            continue
        at = dt.datetime.strptime(m["stamp"], "%Y%m%d-%H%M%S").replace(tzinfo=dt.timezone.utc)
        got.append(
            {
                "name": name,
                "label": m["label"],
                "kind": "folder" if m["kind"] == "surrealkv" else "export",
                "bytes": int(f.get("Size") or 0),
                "taken_at": at.isoformat(timespec="seconds"),
            }
        )
    return sorted(got, key=lambda b: (b["taken_at"], b["name"]), reverse=True)


def remote_delete(db, cfg, name):
    if not NAME.match(name or ""):
        raise ValueError("no such backup")
    where = remote(cfg)
    if where is None:
        raise BackupError("no storage connection is set for backups")
    _remote_rm(db, cfg, where, name + SEALED)


def _remote_rm(db, cfg, where, key):
    from . import blobs

    with blobs._remote(db, cfg, where) as (conf, prefix):
        try:
            blobs._rclone(cfg, conf, "deletefile", prefix + key, timeout=120)
        except RuntimeError as e:
            if not blobs._missing(e):
                raise


def fetch(db, cfg, name):
    """Bring the copy of `name` back from the connection into the backups folder (to restore or download it)."""
    import tarfile

    from . import blobs

    if not NAME.match(name or ""):
        raise ValueError("no such backup")
    where = remote(cfg)
    if where is None:
        raise BackupError("no storage connection is set for backups")
    d = folder(db)
    d.mkdir(parents=True, exist_ok=True)
    dst = d / name
    if dst.exists():
        return dst
    sealed, plain = d / f".fetch-{time.time_ns()}{SEALED}", d / f".fetch-{time.time_ns()}"
    try:
        try:
            blobs._fetch(db, cfg, where, name + SEALED, sealed)
        except KeyError:
            raise ValueError("no such backup on the connection") from None
        unseal(cfg, sealed, plain)
        part = dst.with_name(dst.name + ".fetching")
        if name.endswith(".surrealkv"):
            out = d / f".untar-{time.time_ns()}"
            with tarfile.open(plain, "r:gz") as tar:
                tar.extractall(out, filter="data")
            (out / name).rename(part)
            shutil.rmtree(out, ignore_errors=True)
        else:
            plain.rename(part)
        part.rename(dst)
    finally:
        sealed.unlink(missing_ok=True)
        plain.unlink(missing_ok=True)
    return dst


# ---------- copying to or from the connection in the background (one at a time per process) ----------
copying = {"running": None, "name": None, "error": None, "finished_at": None}
_copy_lock = threading.Lock()


def start_copy(db, cfg, what, name):
    """what = send (a backup here to the connection) or fetch (a copy there back here), in the background."""
    fn = {"send": send, "fetch": fetch}[what]
    with _copy_lock:
        if copying["running"]:
            raise Busy(f"{copying['name']} is being copied; wait for it to finish")
        copying.update(running=what, name=name, error=None, finished_at=None)

    def go():
        try:
            fn(db, cfg, name)
        except Exception as e:  # noqa: BLE001 - shown in the app
            log.exception("copying %s (%s) failed", name, what)
            copying.update(error=f"{type(e).__name__}: {e}"[:500])
            if what == "send":
                db.q("UPSERT $r MERGE $d", r=R("backup_remote", "last"), d={"at": store.now(), "name": name, "error": copying["error"]})
        finally:
            copying.update(running=None, finished_at=store.now())

    threading.Thread(target=go, daemon=True, name=f"backup-{what}").start()
