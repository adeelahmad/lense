"""The database backups in <data_dir>/backups (docs/database.md, Backups): listed, taken, downloaded and deleted from
the web app. migrations.backup() takes them, before upgrades (before-upgrade-*) and on request (manual-*)."""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import shutil

from . import migrations

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
