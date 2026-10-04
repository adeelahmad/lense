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
SHIPPED = ["access-levels", "collection-homes"]


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
    assert migrations.pending(db) == ["collection-homes"]
    assert store.migrate(db) == ["collection-homes"]
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
    assert "ran 2 upgrade(s): access-levels, collection-homes" in out
    assert "done     access-levels" in out
