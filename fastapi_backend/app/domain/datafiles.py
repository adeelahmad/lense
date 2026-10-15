"""The files Lens keeps under data_dir (uploads, objects, media, frames, renditions, reports, exports, podcasts,
certificates and the rest), kept with each database backup in <data_dir>/backups/.files/<backup name>/ (docs/database.md,
Backups) and put back when it is restored.

A file that hasn't changed since the newest earlier backup is a hard link to that backup's copy, so backing up again
takes no more room than what changed. Left out: what Lens fetches or rebuilds by itself (models, bin, python, cache,
search), what's passing (tmp, locks, logs, unfinished uploads), the backups, the embedded database (the backup's own
part) and secret.key (keep it apart from the backups, docs/encryption.md).
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import shutil
import time

SKIP = {"backups", "secret.key", "tmp", "locks", "logs", "bin", "models", "python", "cache", "search"}
FOLDER = ".files"


def folder(data_dir) -> pathlib.Path:
    return pathlib.Path(data_dir or ".") / "backups" / FOLDER


def _skipped(name, skip):
    return name in skip or name == ".partial" or name.endswith((".part", ".partial"))


def _entries(data, skip):
    """The top-level entries of data_dir that are backed up."""
    if not data.is_dir():
        return []
    return sorted(p for p in data.iterdir() if not _skipped(p.name, skip) and not p.is_symlink())


def _walk(data, skip):
    """(path, its path under data_dir) of every file backed up; symlinks and partial files are left out."""
    for top in _entries(data, skip):
        if top.is_file():
            yield top, pathlib.Path(top.name)
            continue
        for root, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if not _skipped(d, ()) and not os.path.islink(os.path.join(root, d))]
            for f in files:
                p = pathlib.Path(root, f)
                if _skipped(f, ()) or p.is_symlink() or not p.is_file():
                    continue
                yield p, p.relative_to(data)


def _skip_for(data, db_path):
    """SKIP, and the embedded database's folder when it's under data_dir."""
    skip = set(SKIP)
    if db_path:
        with contextlib.suppress(ValueError, OSError):
            skip.add(pathlib.Path(db_path).resolve().relative_to(data.resolve()).parts[0])
    return skip


def path(data_dir, name):
    """The files kept with the backup `name`, or None (a backup from before files were kept, or of none)."""
    p = folder(data_dir) / name
    return p if p.is_dir() else None


def _newest(data_dir):
    d = folder(data_dir)
    if not d.is_dir():
        return None
    done = [p for p in d.iterdir() if p.is_dir() and not p.name.endswith(".part") and not p.name.startswith(".")]
    return max(done, key=lambda p: p.stat().st_mtime_ns, default=None)


def snapshot(data_dir, name, db_path=None):
    """Keep data_dir's files with the backup `name`; returns where, or None when there are none. RuntimeError when the
    disk hasn't room for what changed since the last backup."""
    if not data_dir:
        return None
    data = pathlib.Path(data_dir or ".")
    skip = _skip_for(data, db_path)
    prev = _newest(data_dir)
    files = list(_walk(data, skip))
    if not files:
        return None
    dst = folder(data_dir) / name
    part = dst.with_name(dst.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    plan, need = [], 0
    for src, rel in files:
        try:
            st = src.stat()
        except OSError:  # gone meanwhile
            continue
        old = prev / rel if prev else None
        same = False
        if old is not None:
            with contextlib.suppress(OSError):
                o = old.stat()
                same = o.st_size == st.st_size and o.st_mtime_ns == st.st_mtime_ns
        plan.append((src, rel, old if same else None))
        need += 0 if same else st.st_size
    free = shutil.disk_usage(part).free
    if need > free - (64 << 20):
        shutil.rmtree(part, ignore_errors=True)
        raise RuntimeError(
            f"backing up the files needs {need >> 20} MB and the disk has {free >> 20} MB free; free some space, or delete older backups"
        )
    try:
        for src, rel, old in plan:
            out = part / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            if old is not None:
                try:
                    os.link(old, out)
                    continue
                except OSError:  # another file system, or too many links
                    pass
            with contextlib.suppress(FileNotFoundError):  # gone meanwhile
                shutil.copy2(src, out)
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise
    part.rename(dst)
    return dst


def restore(data_dir, name, db_path=None):
    """Put back the files kept with the backup `name` in place of data_dir's (nothing changes when it has none). What
    was there is put back if copying fails."""
    src = path(data_dir, name) if data_dir else None
    if src is None:
        return False
    data = pathlib.Path(data_dir or ".")
    skip = _skip_for(data, db_path)
    held = folder(data_dir) / f".replaced-{time.time_ns()}"
    held.mkdir(parents=True)
    moved = []
    try:
        for p in _entries(data, skip):
            p.rename(held / p.name)
            moved.append(p.name)
        for p in sorted(src.iterdir()):
            if _skipped(p.name, skip):
                continue
            if p.is_dir():
                shutil.copytree(p, data / p.name, symlinks=False)
            else:
                shutil.copy2(p, data / p.name)
    except BaseException:
        for p in _entries(data, skip):
            if p.name in moved or (src / p.name).exists():
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        for n in moved:
            with contextlib.suppress(OSError):
                (held / n).rename(data / n)
        shutil.rmtree(held, ignore_errors=True)
        raise
    shutil.rmtree(held, ignore_errors=True)  # the before-restore backup holds them
    return True


def drop(data_dir, name):
    p = folder(data_dir) / name
    shutil.rmtree(p, ignore_errors=True)


def tidy(data_dir, names):
    """Remove the files kept with backups that are gone (names: the backups there are)."""
    d = folder(data_dir)
    if not d.is_dir():
        return
    keep = set(names)
    for p in d.iterdir():
        if p.name.startswith(".replaced-") or p.name.endswith(".part"):
            continue  # a restore or backup under way
        if p.name not in keep:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
