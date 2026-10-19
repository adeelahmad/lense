"""Import runs: one bulk import is one run, so a mistaken one is undone whole (docs/api.md#import-runs).

A folder scan that finds new files (`scan`), the files of a zip or tar archive made resources (`archive`), a list of
links or a bookmarks file captured as web pages (`links`) and chosen files of a storage source (`source`) each make
one `import_run`: what started it (`kind`), who (`created_by`), when, the namespace (`space`), a line saying what it
read (`source`) and how many assets it made (`assets`). Every asset it made carries the run's id (`import_run` on the
recording). Single imports (one upload, one pasted transcript, one web page) make no run, and nor did imports from
before runs existed: those assets have no `import_run` and nothing changes for them.

Undoing a run deletes every asset of it still here through the normal deletion path (deletion.py), so each goes
exactly as a delete takes it, needing the same rights: the remove permission in its namespace (owners), or admin of
its collection. When any asset is one the person may not delete, nothing is deleted. An asset a job is working on is
left and its job asked to stop; undoing the run again takes the rest. Each deletion is audited as `recording.delete`
and the undo as `import.undo`. Lens keeps no recently deleted list of assets, so an undo can't itself be undone: the
media files stay where they were, and importing them again on purpose brings them back.

A batch run (batches.py) is something else: steps run over assets that are already here, so runs are kept apart.
"""

from __future__ import annotations

from . import auth, deletion, hierarchy, roles, store

R = store.R
KINDS = ("scan", "archive", "links", "source")
FIELDS = "record::id(id) AS id, kind, space, source, created_by, created_at, assets, undone_at, undone_by, undone, busy"


def mark(db):
    """The last recording id handed out so far: what an import makes from here on has a higher one (`record`'s `after`)."""
    got = db.values("SELECT VALUE n FROM $r", r=R("seq", "recording"))
    return int(got[0] or 0) if got else 0


def record(db, kind, space, ids, by=None, source=None, after=None):
    """A run of `kind` in namespace `space` that made the recordings `ids`: its id, or None when it made nothing. With
    `after` (from `mark`, taken before the import), only recordings made since count: an import that answered with an
    asset that was already here (the same file) never puts that asset in its run."""
    ids = list(dict.fromkeys(int(i) for i in ids if i is not None and (after is None or int(i) > after)))
    if not ids:
        return None
    run = db.next_id("import_run")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("import_run", run),
        d=store.clean(
            {
                "kind": kind,
                "space": space,
                "source": str(source)[:300] if source else None,
                "created_by": by,
                "created_at": store.now(),
                "assets": 0,  # counted as they're tagged
            }
        ),
    )
    tag(db, run, ids)
    return run


def tag(db, run, ids):
    """Mark recordings as made by run `run` (the ones it has already are left as they are)."""
    ids = [R("recording", int(i)) for i in dict.fromkeys(ids)]
    if not ids:
        return 0
    new = db.values("SELECT VALUE record::id(id) FROM recording WHERE id IN $ids AND (import_run ?? 0) != $run", ids=ids, run=int(run))
    if new:
        db.q("UPDATE recording SET import_run = $run WHERE id IN $ids", ids=[R("recording", i) for i in new], run=int(run))
        db.q("UPDATE $r SET assets = (assets ?? 0) + $n", r=R("import_run", int(run)), n=len(new))
    return len(new)


def archive(db, rid, by=None, title=None):
    """An archive's files made resources of their own (convert.keep_attachments): they and the archive are one run,
    the archive's own when it came in with one (a source's files), else a new `archive` run. None when it made none."""
    made = db.values("SELECT VALUE record::id(id) FROM recording WHERE attached_to.resource = $r", r=int(rid))
    if not made:
        return None
    rec = db.one("SELECT space, title, import_run FROM $r", r=R("recording", int(rid))) or {}
    if rec.get("import_run"):
        tag(db, rec["import_run"], made)
        return rec["import_run"]
    if by is None:  # whoever brought the archive in: the person whose upload's job it is
        first = db.one("SELECT created_by, created_at FROM job WHERE recording = $r ORDER BY created_at LIMIT 1", r=int(rid)) or {}
        by = first.get("created_by") if "@" in str(first.get("created_by") or "") else None
    return record(db, "archive", rec.get("space"), [int(rid), *made], by, title or rec.get("title"))


def _left(db, ids):
    """{run: how many of its assets are still here}."""
    if not ids:
        return {}
    rows = db.rows("SELECT import_run, count() AS n FROM recording WHERE import_run IN $ids GROUP BY import_run", ids=list(ids))
    return {r["import_run"]: r["n"] for r in rows}


def _view(db, row, left, names):
    n = left.get(row["id"], 0)
    status = ("undone" if not n else "partly undone") if row.get("undone_at") else "imported"
    return {**row, "namespace": names.get(row.get("space")), "left": n, "status": status}


def get(db, run):
    """One run, with how many of its assets are still here (`left`) and its status. KeyError when there is none."""
    row = db.one(f"SELECT {FIELDS} FROM $r", r=R("import_run", int(run)))
    if not row:
        raise KeyError(run)
    return _view(db, row, _left(db, [row["id"]]), store.space_names(db))


def listing(db, by=None, spaces=None, limit=50):
    """Recent runs, newest first: everyone's (`by` None), else those `by` started or made in `spaces`."""
    where = "" if by is None else " WHERE created_by = $e OR space IN $sp"
    rows = db.rows(
        f"SELECT {FIELDS} FROM import_run{where} ORDER BY created_at DESC, id DESC LIMIT $n",
        e=by,
        sp=sorted(spaces or ()),
        n=int(limit),
    )
    left, names = _left(db, [r["id"] for r in rows]), store.space_names(db)
    return [_view(db, r, left, names) for r in rows]


def members(db, run):
    """The assets of the run still here: [{id, space, collection}], an archive's files before the archive."""
    rows = db.rows("SELECT record::id(id) AS id, space, collection, attached_to FROM recording WHERE import_run = $run", run=int(run))
    rows.sort(key=lambda r: (r.get("attached_to") is None, r["id"]))
    return [{k: r.get(k) for k in ("id", "space", "collection")} for r in rows]


def may_remove(db, user):
    """can(space, collection): whether this person may delete an asset there, as a delete checks it
    (deps.Access.need_in): the remove permission of their namespace role, or a collection role that ranks as one."""
    perms = auth.permissions(db, user)
    colls = {} if user.get("admin") else hierarchy.roles_of(db, user.get("id"))
    need = auth.ROLES[roles.minimum("remove")]

    def can(space, collection):
        return "remove" in perms.get(space, ()) or hierarchy.ROLES.get((colls.get(space) or {}).get(collection) or "", 0) >= need

    return can


def refused(db, run, can):
    """The assets of the run this person may not delete (can(space, collection), as may_remove gives it)."""
    return [m["id"] for m in members(db, run) if not can(m["space"], m.get("collection"))]


def undo(db, cfg, run, who):
    """Delete every asset of the run still here (`who`: the person, {id, email}, as the audit log keeps them), once the
    caller has checked they may (`refused`). Returns {run, deleted, busy, namespaces}: busy are the assets a job is
    working on, left for now with their jobs asked to stop."""
    deleted, busy, spaces = [], [], set()
    for m in members(db, run):
        try:
            gone = deletion.delete(db, cfg, m["id"], who.get("email"))
        except KeyError:
            continue  # deleted meanwhile
        except deletion.Running:
            db.q(
                "UPDATE job SET cancel_requested = true, updated_at = $t WHERE recording = $r AND status = 'running'",
                r=m["id"],
                t=store.now(),
            )
            busy.append(m["id"])
            continue
        auth.audit(db, who, "recording.delete", f"recording:{m['id']}", {**gone, "import_run": int(run)})
        deleted.append(m["id"])
        if gone.get("namespace"):
            spaces.add(gone["namespace"])
    db.q(
        "UPDATE $r SET undone_at = $t, undone_by = $by, undone = (undone ?? 0) + $n, busy = $busy",
        r=R("import_run", int(run)),
        t=store.now(),
        by=who.get("email"),
        n=len(deleted),
        busy=len(busy),
    )
    auth.audit(db, who, "import.undo", f"import_run:{int(run)}", {"deleted": len(deleted), "busy": len(busy), "recordings": deleted})
    return {"run": int(run), "deleted": deleted, "busy": busy, "namespaces": sorted(spaces)}
