"""Going back after a failed upgrade (docs/updates.md): the agent reports each step and the rollback, and the version
it went back to restores the backup taken first only when the new version had changed the database."""

from __future__ import annotations

import pytest

from app.domain import store, updates
from app.domain.store import R


@pytest.fixture
def control(tmp_path, monkeypatch):
    d = tmp_path / "control"
    (d / "inbox").mkdir(parents=True)
    (d / "agent").write_text("kind=systemd\n")
    monkeypatch.setenv("LENS_CONTROL_DIR", str(d))
    return d


@pytest.fixture
def kept(folder, db):
    """A database that can be backed up: the test server when there is one, else an embedded surrealkv one."""
    if not db.embedded:
        yield db
        return
    from tests.conftest import make_cfg

    conn = store.connect(make_cfg(folder, url="surrealkv://" + str(folder / "data" / "surrealdb")))
    yield conn
    conn.close()


def _upgraded_then_rolled_back(kept, cfg, control, newer_steps):
    """An upgrade whose backup was taken; the new version then wrote data (and ran `newer_steps`) and didn't start, so
    the agent went back and left the restore request."""
    kept.q("CREATE $r SET title = 'before'", r=R("recording", 1))
    run = updates.upgrade(kept, cfg, "v99.0.0", by="t", wait=True)
    assert run["state"] == "queued" and run["backup"].startswith("before-upgrade-")
    req = dict(line.split("=", 1) for line in (control / "inbox" / "upgrade").read_text().splitlines())
    assert req["backup"] == run["backup"]  # the agent is told which backup to go back to
    kept.q("UPDATE $r SET title = 'after'", r=R("recording", 1))
    kept.q("CREATE recording SET title = 'new'")
    for name in newer_steps:
        kept.q("CREATE migration CONTENT $d", d={"name": name, "done_at": store.now()})
    (control / "inbox" / "restore").write_text(f"id={run['id']}\nbackup={run['backup']}\n")
    (control / "upgrade-status").write_text(
        f"id={run['id']}\nref=v99.0.0\nstate=rolled-back\nstep=check\ncode=1\nerror=the new version didn't start\n"
    )
    return run


def test_the_backup_comes_back_when_the_new_version_changed_the_database(kept, cfg, control):
    _upgraded_then_rolled_back(kept, cfg, control, ["a-step-from-the-future"])
    said = updates.restore_on_start(kept, cfg)
    assert said.startswith("the database was put back"), said
    assert kept.values("SELECT VALUE title FROM recording") == ["before"]
    assert "a-step-from-the-future" not in kept.values("SELECT VALUE name FROM migration")
    assert not (control / "inbox" / "restore").exists()
    run = updates.run_state(kept)
    assert run["state"] == "rolled-back" and run["step"] == "check" and run["restore"] == said
    assert updates.restore_on_start(kept, cfg) is None  # once


def test_the_data_is_kept_when_the_new_version_left_the_database_alone(kept, cfg, control):
    _upgraded_then_rolled_back(kept, cfg, control, [])
    said = updates.restore_on_start(kept, cfg)
    assert "kept as it is" in said
    assert sorted(kept.values("SELECT VALUE title FROM recording")) == ["after", "new"]


def test_a_missing_backup_is_said(kept, cfg, control):
    run = _upgraded_then_rolled_back(kept, cfg, control, ["a-step-from-the-future"])
    (control / "inbox" / "restore").write_text(f"id={run['id']}\nbackup=../../etc/passwd\n")
    assert "can't be used" in updates.restore_on_start(kept, cfg)
    assert sorted(kept.values("SELECT VALUE title FROM recording")) == ["after", "new"]


def test_no_control_folder_nothing_to_do(db, cfg, monkeypatch):
    monkeypatch.delenv("LENS_CONTROL_DIR", raising=False)
    assert updates.restore_on_start(db, cfg) is None
