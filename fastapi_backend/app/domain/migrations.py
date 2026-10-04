"""Upgrading the data of an existing install: named steps that run once per database, in order (docs/database.md).

The schema itself (tables, fields, indexes) is applied on every start from store.SCHEMA. A step here is for what that
can't do: rewriting stored data, or changing or removing an index that already exists. Each step:

- has a name that never changes, and goes at the end of STEPS (never reorder, rename or remove one: a database
  remembers steps by name, so a renamed step runs again and a removed one is simply never run);
- is safe to run twice, because a start that stops half way runs it again;
- lives with the code it serves, so STEPS only names it.

Every process that opens the database (the API, workers, `lens watch`, `lens` commands) checks for pending steps when it
starts. One of them takes the lock and runs them; the others wait until it is done, so nothing reads data in a shape
its code doesn't expect. A dead holder's lock lapses after LEASE seconds. What ran, when, how long it took and why a
step failed are kept in `migration:⟨name⟩`; `lens migrations` lists them.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid

from .store import R, now

log = logging.getLogger("lens")

LEASE = 300  # seconds a lock holder may go quiet before another process takes over
WAIT_LOG = 30  # how often a waiting process says what it is waiting for

# The steps that came before named steps, tracked then by a counter (seq:migrations = how many had run): a database
# whose counter reached n has done the first n of these, under these names.
LEGACY = ("access-levels", "collection-homes")


def steps():
    """[(name, fn(db))] in the order they run. Append only."""
    from . import access, hierarchy

    return [
        # IIIF-only access levels (public, transcript, signed-in, private) become the access setting
        ("access-levels", access.migrate_legacy),
        # every namespace gets its default collection and every recording a home
        ("collection-homes", hierarchy.migrate_homes),
    ]


class Failed(RuntimeError):
    def __init__(self, name, err):
        super().__init__(
            f"upgrading the database stopped at step {name!r}: {err}. Fix the cause and start Lens again; the step runs "
            "again from the start and is safe to repeat (`lens migrations` lists what has run)."
        )
        self.step = name


def _done(db):
    return {r["name"] for r in db.rows("SELECT name FROM migration WHERE done_at != NONE")}


def _adopt_legacy(db):
    """Record the steps an older Lens ran under its counter, so they aren't run again."""
    n = int((db.one("SELECT n FROM $r", r=R("seq", "migrations")) or {}).get("n") or 0)
    if not n:
        return
    have = _done(db)
    for name in LEGACY[:n]:
        if name not in have:
            db.q("UPSERT $r CONTENT $d", r=R("migration", name), d={"name": name, "done_at": now(), "took_ms": 0, "legacy": True})


def pending(db, registered=None):
    """Names of the steps this database has still to run, in order."""
    _adopt_legacy(db)
    done = _done(db)
    return [name for name, _ in (registered if registered is not None else steps()) if name not in done]


def status(db, registered=None):
    """Every step this code knows and any the database ran that it doesn't (from a newer Lens), in order:
    [{name, state: done|pending|failed|unknown, done_at, took_ms, failed_at, error}]."""
    _adopt_legacy(db)
    rows = {r["name"]: r for r in db.rows("SELECT * OMIT id FROM migration")}
    out = []
    for name, _ in registered if registered is not None else steps():
        r = rows.pop(name, {})
        state = "done" if r.get("done_at") else "failed" if r.get("error") else "pending"
        out.append({"name": name, "state": state, **{k: r.get(k) for k in ("done_at", "took_ms", "failed_at", "error")}})
    for name, r in sorted(rows.items(), key=lambda kv: str(kv[1].get("done_at"))):
        out.append({"name": name, "state": "unknown", **{k: r.get(k) for k in ("done_at", "took_ms", "failed_at", "error")}})
    return out


# ---------- the lock ----------
def _take(db, holder):
    db.q("DELETE $r WHERE until < $t", r=R("migration_lock", "run"), t=time.time())
    try:
        db.q("CREATE $r CONTENT $d", r=R("migration_lock", "run"), d={"holder": holder, "until": time.time() + LEASE, "since": now()})
        return True
    except Exception as e:  # noqa: BLE001
        if "already exists" in str(e):
            return False
        raise


def _renew(db, holder):
    db.q("UPDATE $r SET until = $u WHERE holder = $h", r=R("migration_lock", "run"), u=time.time() + LEASE, h=holder)


def _release(db, holder):
    db.q("DELETE $r WHERE holder = $h", r=R("migration_lock", "run"), h=holder)


def _holder(db):
    return (db.one("SELECT holder FROM $r", r=R("migration_lock", "run")) or {}).get("holder")


def run(db, registered=None, poll=1.0):
    """Run the pending steps, or wait while another process runs them. Returns the names this process ran."""
    registered = registered if registered is not None else steps()
    if not pending(db, registered):
        return []
    holder = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    said = 0.0
    while not _take(db, holder):
        if not pending(db, registered):
            return []  # the process that held the lock finished them
        if time.time() - said > WAIT_LOG:
            log.info("waiting for %s to finish upgrading the database", _holder(db) or "another process")
            said = time.time()
        time.sleep(poll)
    stop = threading.Event()

    def beat():
        while not stop.wait(LEASE / 5):
            try:
                _renew(db, holder)
            except Exception:  # noqa: BLE001
                log.exception("could not renew the database upgrade lock")

    beater = threading.Thread(target=beat, name="migration-lease", daemon=True)
    beater.start()
    ran = []
    try:
        fns = dict(registered)
        for name in pending(db, registered):  # again: another process may have run some before this one got the lock
            log.info("upgrading the database: %s", name)
            t = time.time()
            try:
                fns[name](db)
            except Exception as e:
                db.q("UPSERT $r MERGE $d", r=R("migration", name), d={"name": name, "failed_at": now(), "error": str(e)[:2000]})
                log.exception("upgrading the database stopped at %s", name)
                raise Failed(name, e) from e
            took = int((time.time() - t) * 1000)
            db.q(
                "UPSERT $r CONTENT $d",
                r=R("migration", name),
                d={"name": name, "done_at": now(), "took_ms": took, "by": holder},
            )
            log.info("upgraded the database: %s (%d ms)", name, took)
            ran.append(name)
    finally:
        stop.set()
        beater.join(timeout=5)
        _release(db, holder)
    return ran
