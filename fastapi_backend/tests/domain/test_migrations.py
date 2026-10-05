"""Database upgrades (app/domain/migrations.py): named steps that run once per database, under a lock, with a record."""

from __future__ import annotations

import json
import threading
import time

import pytest

from app import cli
from app.domain import migrations, store
from app.domain.store import R

# Every step that has shipped, in order. A database remembers steps by name, so these may never be renamed, reordered
# or removed: add new steps to the end of migrations.steps() and here.
SHIPPED = ["access-levels", "collection-homes", "tour-seen"]


def test_steps_are_append_only():
    names = [n for n, _ in migrations.steps()]
    assert names[: len(SHIPPED)] == SHIPPED, "a shipped step was renamed, reordered or removed"
    assert names == SHIPPED, "add the new step's name to SHIPPED too"
    assert len(set(names)) == len(names)
    assert list(migrations.LEGACY) == SHIPPED[: len(migrations.LEGACY)]


def test_a_fresh_database_has_run_every_step(db):
    assert migrations.pending(db) == []
    assert [(m["name"], m["state"]) for m in migrations.status(db)] == [(n, "done") for n in SHIPPED]
    assert store.migrate(db) == []  # nothing left
    assert db.rows("SELECT * FROM migration_lock") == []


def _old_shape(db):
    """Data as an older Lens left it: an IIIF-only access level and a recording without a collection."""
    sid = store.ns_id(db, "pods")
    rid = db.next_id("recording")
    db.q("CREATE $r CONTENT $d", r=R("recording", rid), d={"space": sid, "title": "old", "meta_json": json.dumps({"access": "signed-in"})})
    return rid


def _everything(db):
    """Every table's rows, minus the upgrade bookkeeping."""
    tables = [s.split()[5] for s in store.SCHEMA if s.startswith("DEFINE TABLE")]
    skip = {"migration", "migration_lock", "seq"}
    return {t: sorted(map(repr, db.rows(f"SELECT * FROM {t}"))) for t in tables if t not in skip}


@pytest.mark.parametrize("name", SHIPPED)
def test_every_step_is_safe_to_repeat(db, name):
    _old_shape(db)
    step = dict(migrations.steps())[name]
    step(db)
    once = _everything(db)
    step(db)
    assert _everything(db) == once


def test_steps_an_older_lens_counted_are_not_run_again(db):
    """Before named steps, a counter (seq:migrations) said how many had run."""
    db.q("DELETE migration")
    db.q("UPSERT $r SET n = 1", r=R("seq", "migrations"))
    rid = _old_shape(db)
    assert migrations.pending(db) == ["collection-homes", "tour-seen"]
    assert store.migrate(db) == ["collection-homes", "tour-seen"]
    row = db.one("SELECT meta_json, collection FROM $r", r=R("recording", rid))
    assert json.loads(row["meta_json"]) == {"access": "signed-in"}  # access-levels was counted as done, so not rerun
    assert row["collection"] == store.default_collection(db, store.ns_id(db, "pods"))
    assert db.one("SELECT legacy FROM $r", r=R("migration", "access-levels"))["legacy"] is True


def test_a_failed_step_is_recorded_and_runs_again_next_start(db):
    calls = []

    def broken(_db):
        calls.append("broken")
        raise ValueError("disk full")

    registered = [("one", lambda _db: calls.append("one")), ("two", broken), ("three", lambda _db: calls.append("three"))]
    with pytest.raises(migrations.Failed, match="'two': disk full"):
        migrations.run(db, registered)
    assert calls == ["one", "broken"]
    got = {m["name"]: m for m in migrations.status(db, registered)}
    assert (got["one"]["state"], got["two"]["state"], got["three"]["state"]) == ("done", "failed", "pending")
    assert got["two"]["error"] == "disk full"
    assert db.rows("SELECT * FROM migration_lock") == []  # released, so the next start isn't kept waiting

    registered[1] = ("two", lambda _db: calls.append("two"))  # the cause fixed
    assert migrations.run(db, registered) == ["two", "three"]
    assert calls == ["one", "broken", "two", "three"]
    assert [m["state"] for m in migrations.status(db, registered)][:3] == ["done", "done", "done"]


def test_one_process_runs_the_steps_while_the_others_wait(db):
    calls = []

    def slow(_db):
        calls.append(1)
        time.sleep(0.3)

    registered = [("slow", slow)]
    results = []
    threads = [threading.Thread(target=lambda: results.append(migrations.run(db, registered, poll=0.02))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert calls == [1]
    assert sorted(results) == [[], [], [], ["slow"]]


def test_waits_for_a_live_holder_and_takes_over_from_a_dead_one(db, monkeypatch):
    calls = []
    registered = [("x", lambda _db: calls.append(1))]
    db.q("CREATE $r CONTENT $d", r=R("migration_lock", "run"), d={"holder": "elsewhere", "until": time.time() + 60})
    t = threading.Thread(target=lambda: migrations.run(db, registered, poll=0.02))
    t.start()
    time.sleep(0.2)
    assert calls == [] and t.is_alive()  # waiting: nothing reads data in a shape its code doesn't expect
    db.q("UPDATE $r SET until = $u", r=R("migration_lock", "run"), u=time.time() - 1)  # the holder died
    t.join(5)
    assert calls == [1]
    assert db.rows("SELECT * FROM migration_lock") == []


def test_steps_from_a_newer_lens_are_listed(db):
    db.q("UPSERT $r CONTENT $d", r=R("migration", "from-the-future"), d={"name": "from-the-future", "done_at": store.now()})
    assert migrations.status(db)[-1]["name"] == "from-the-future" and migrations.status(db)[-1]["state"] == "unknown"
    assert migrations.pending(db) == []


def test_lens_migrations_lists_and_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SURREAL_URL", raising=False)
    cfg_path = tmp_path / "archive.yaml"
    cfg_path.write_text("data_dir: ./data\nnamespaces:\n  pods:\n    paths: []\n")
    cli.main(["--config", str(cfg_path), "migrations"])  # opens without upgrading: a fresh database, all pending
    out = capsys.readouterr().out
    assert "pending  access-levels" in out and "pending  collection-homes" in out
    cli.main(["--config", str(cfg_path), "migrations", "--run"])
    out = capsys.readouterr().out
    assert "ran 3 upgrade(s): access-levels, collection-homes, tour-seen" in out
    assert "done     access-levels" in out


# ---------- the backup taken before upgrading ----------
@pytest.fixture
def kept(folder, db):
    """A database whose data survives a restart: the test server when there is one, else an embedded surrealkv one."""
    if not db.embedded:
        yield db
        return
    from tests.conftest import make_cfg

    conn = store.connect(make_cfg(folder, url="surrealkv://" + str(folder / "data" / "surrealdb")))
    yield conn
    conn.close()


def _restored(path, folder, target):
    """The rows of `recording` in a backup of the database `target` (namespace, database)."""
    from tests.conftest import TEST_URL, make_cfg

    if path.is_dir():
        conn = store.DB(make_cfg(folder, url="surrealkv://" + str(path), database={"namespace": target[0], "database": target[1]}))
    else:
        import gzip

        sql = gzip.open(path).read().decode()
        assert sql.startswith("-- ")  # SurrealQL that `surreal import` takes
        conn = store.DB(make_cfg(folder, url=TEST_URL))  # a new, empty database on the test server
        conn.q(sql)
    try:
        return conn.rows("SELECT title, meta_json FROM recording")
    finally:
        conn.close()


def test_a_database_with_data_is_backed_up_before_it_is_upgraded(kept, folder):
    rid = _old_shape(kept)
    kept.q("UPDATE $r SET title = 'before'", r=R("recording", rid))
    registered = [("retitle", lambda d: d.q("UPDATE recording SET title = 'after'"))]
    assert migrations.run(kept, registered) == ["retitle"]
    backups = sorted((folder / "data" / "backups").glob("before-upgrade-*"))
    assert len(backups) == 1
    assert [r["title"] for r in _restored(backups[0], folder, kept._target)] == ["before"]
    assert kept.values("SELECT VALUE title FROM recording") == ["after"]  # and the open database still works


def test_only_the_newest_backups_are_kept(kept, folder):
    _old_shape(kept)
    old = folder / "data" / "backups"
    old.mkdir(parents=True, exist_ok=True)
    for n in range(5):
        (old / f"before-upgrade-2020010{n}-000000.surql.gz").write_bytes(b"")
    (old / "manual-20200101-000000.surql.gz").write_bytes(b"")
    migrations.run(kept, [("x", lambda d: None)])
    left = sorted(p.name for p in old.iterdir())
    assert len([n for n in left if n.startswith("before-upgrade-")]) == migrations.KEEP
    assert "before-upgrade-20200104-000000.surql.gz" in left and "manual-20200101-000000.surql.gz" in left


def test_no_backup_for_a_database_without_data_or_in_memory(folder, kept, db):
    migrations.run(kept, [("x", lambda d: None)])  # no recordings or accounts yet
    if db.embedded:
        migrations.run(db, [("y", lambda d: None)])  # mem://
    assert not (folder / "data" / "backups").exists()


def test_a_failed_backup_stops_the_upgrade(kept, monkeypatch):
    _old_shape(kept)
    calls = []

    def fail(*_a, **_k):
        raise OSError("No space left on device")

    monkeypatch.setattr(migrations, "backup", fail)
    with pytest.raises(RuntimeError, match="backing it up first failed: No space left on device"):
        migrations.run(kept, [("x", lambda d: calls.append(1))])
    assert calls == [] and migrations.pending(kept, [("x", None)]) == ["x"]
    assert kept.rows("SELECT * FROM migration_lock") == []
    monkeypatch.setenv("LENS_UPGRADE_BACKUP", "off")
    assert migrations.run(kept, [("x", lambda d: calls.append(1))]) == ["x"]


def test_lens_backup(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SURREAL_URL", raising=False)
    cfg_path = tmp_path / "archive.yaml"
    cfg_path.write_text("data_dir: ./data\nnamespaces:\n  pods:\n    paths: []\n")
    cli.main(["--config", str(cfg_path), "backup"])
    assert "backed up to " in capsys.readouterr().out
    assert len(list((tmp_path / "data" / "backups").glob("manual-*.surrealkv"))) == 1


def test_people_who_signed_in_before_the_tour_skip_it(db):
    from app.domain import auth

    old = auth.create_account(db, "old@x.io", "old password 12", "Old")
    new = auth.create_account(db, "new@x.io", "new password 12", "New")
    db.q("UPDATE $r SET last_login_at = $t", r=R("account", old), t=store.now())
    dict(migrations.steps())["tour-seen"](db)
    assert auth.toured_at(db, old) and auth.toured_at(db, new) is None
