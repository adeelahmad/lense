"""Restoring backups from the web app, downloading them by link, and taking them on a schedule (listing, taking and
deleting them: test_updates.py). They run against the test server when there is one (LENS_TEST_SURREAL_URL), else an
embedded surrealkv database."""

from __future__ import annotations

import datetime as dt
import gzip
import io
import tarfile
import time

import pytest
from fastapi.testclient import TestClient

from app.domain import backups, migrations, settings, store
from tests.helpers import login, make_user

R = store.R


@pytest.fixture
def kept(folder, cfg, db):
    """A database whose data survives being closed: the test server when there is one, else embedded surrealkv."""
    from tests.conftest import make_cfg

    if not db.embedded:
        yield db, cfg
        return
    cfg = make_cfg(folder, url="surrealkv://" + str(folder / "data" / "surrealdb"))
    conn = store.connect(cfg)
    yield conn, cfg
    conn.close()


@pytest.fixture
def web(kept):
    from app.main import create_app

    conn, cfg = kept
    backups.work.update(running=False, name=None, error=None, finished_at=None, before=None)
    client = TestClient(create_app(cfg, conn, background=False), base_url="http://127.0.0.1")
    make_user(conn, "admin@example.com", "pw-admin-123", admin=True)
    return client, conn, cfg, login(client, "admin@example.com", "pw-admin-123")


def _settle():
    for _ in range(600):
        if not backups.work["running"]:
            return backups.work
        time.sleep(0.05)
    raise AssertionError("still restoring")


def _recording(db, title):
    sid = store.ns_id(db, "pods")
    db.q("CREATE $r CONTENT $d", r=R("recording", db.next_id("recording")), d={"space": sid, "title": title})


def test_take_download_and_restore(web):
    client, db, cfg, h = web
    _recording(db, "kept")
    b = client.post("/api/v1/admin/backups", headers=h).json()
    listed = client.get("/api/v1/admin/backups", headers=h).json()
    assert [x["name"] for x in listed["backups"]] == [b["name"]]
    assert listed["database"] == ("embedded" if db.embedded else "server") and listed["restore"]["running"] is False

    link = client.post(f"/api/v1/admin/backups/{b['name']}/link", headers=h).json()["url"]
    got = client.get(link)  # no sign-in: the link is the permission
    assert got.status_code == 200
    if db.embedded:
        names = tarfile.open(fileobj=io.BytesIO(got.content), mode="r:gz").getnames()
        assert names and all(n.startswith(b["name"]) for n in names)
    else:
        assert gzip.decompress(got.content).startswith(b"-- ")
    assert client.get(link.replace("sig=", "sig=x")).status_code == 403
    assert client.get(f"/api/v1/admin/backups/{b['name']}/file").status_code == 403

    _recording(db, "after")
    db.q("DELETE recording WHERE title = 'kept'")
    r = client.post(f"/api/v1/admin/backups/{b['name']}/restore", json={"confirm": b["name"]}, headers=h)
    assert r.status_code == 202, r.text
    w = _settle()
    assert w["error"] is None, w["error"]
    assert sorted(db.values("SELECT VALUE title FROM recording")) == ["kept"]
    labels = sorted(x["label"] for x in backups.listing(db))
    assert labels == ["before-restore", "manual"] and w["before"].startswith("before-restore-")  # what was there, kept
    assert db.values("SELECT VALUE action FROM audit_log WHERE action = 'backup.restored'") == ["backup.restored"]
    # the restored database works with this Lens: schema, upgrades and the settings every process caches
    assert migrations.pending(db) == []
    settings.save(db, cfg, "backups", {"keep": 3}, "t")
    h = login(client, "admin@example.com", "pw-admin-123")
    assert client.get("/api/v1/admin/backups", headers=h).json()["schedule"]["keep"] == 3


def test_restore_needs_the_name_a_person_and_a_real_backup(web):
    client, db, _cfg, h = web
    _recording(db, "x")
    e = client.post("/api/v1/admin/backups", headers=h).json()
    assert client.post(f"/api/v1/admin/backups/{e['name']}/restore", json={"confirm": "nope"}, headers=h).status_code == 400
    gone = "manual-20200101-000000.surql.gz"
    assert client.post(f"/api/v1/admin/backups/{gone}/restore", json={"confirm": gone}, headers=h).status_code == 400
    assert client.post("/api/v1/admin/backups/..%2Fsecret.key/link", headers=h).status_code in (400, 404)
    other = "manual-20200101-000000." + ("surql.gz" if db.embedded else "surrealkv")
    (backups.folder(db) / other).mkdir() if other.endswith("surrealkv") else (backups.folder(db) / other).write_bytes(b"")
    r = client.post(f"/api/v1/admin/backups/{other}/restore", json={"confirm": other}, headers=h)
    assert r.status_code == 400 and "this Lens uses" in r.json()["detail"]

    tok = client.post("/api/v1/tokens", json={"name": "agent", "scope": "write"}, headers=h).json()["token"]
    bearer = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/admin/backups", headers=bearer).status_code == 200  # an agent can list and take them
    assert client.post(f"/api/v1/admin/backups/{e['name']}/link", headers=bearer).status_code == 403
    assert client.post(f"/api/v1/admin/backups/{e['name']}/restore", json={"confirm": e["name"]}, headers=bearer).status_code == 403


def test_a_failed_restore_puts_back_what_was_there(web, monkeypatch):
    _client, db, cfg, _h = web
    _recording(db, "old")
    e = backups._take(db, "manual")
    _recording(db, "new")

    if db.embedded:
        real = backups.shutil.copytree

        def broken(src, dst, *a, **k):
            if str(src).endswith(e["name"]):
                raise OSError("No space left on device")
            return real(src, dst, *a, **k)

        monkeypatch.setattr(backups.shutil, "copytree", broken)
    else:
        real = backups._post_import
        calls = []

        def broken(db_, sql):
            calls.append(sql)
            if len(calls) == 1:
                raise RuntimeError("importing the backup failed: boom")
            return real(db_, sql)

        monkeypatch.setattr(backups, "_post_import", broken)
    with pytest.raises((OSError, RuntimeError)):
        backups.restore(db, cfg, e["name"])
    assert sorted(db.values("SELECT VALUE title FROM recording")) == ["new", "old"]


def test_one_restore_at_a_time(web):
    client, db, _cfg, h = web
    _recording(db, "x")
    e = backups._take(db, "manual")
    backups.work.update(running=True, name="other")
    try:
        r = client.post(f"/api/v1/admin/backups/{e['name']}/restore", json={"confirm": e["name"]}, headers=h)
        assert r.status_code == 409
    finally:
        backups.work["running"] = False


def test_backups_on_a_schedule(web):
    client, db, cfg, h = web
    _recording(db, "x")
    bad = client.put("/api/v1/settings/backups", json={"schedule": "every night"}, headers=h)
    assert bad.status_code == 400 and "five fields" in bad.json()["detail"]
    ok = client.put("/api/v1/settings/backups", json={"schedule": "0 3 * * *", "timezone": "UTC", "keep": 2}, headers=h)
    assert ok.status_code == 200
    eff = settings.effective(db, cfg)
    t = dt.datetime(2030, 1, 1, 2, 0, tzinfo=dt.UTC)
    assert backups.run_due(db, eff, now=t) is False  # the first look plans the next time
    assert backups.schedule_state(db)["at"] == "2030-01-01T03:00:00Z"
    assert client.get("/api/v1/admin/backups", headers=h).json()["schedule"]["next_at"] == "2030-01-01T03:00:00Z"
    assert backups.run_due(db, eff, now=t + dt.timedelta(minutes=30)) is False
    for day in range(3):
        assert backups.run_due(db, eff, now=t + dt.timedelta(days=day, hours=1, seconds=day)) is True
        time.sleep(1.01)  # backups are named to the second
    st = backups.schedule_state(db)
    assert st["at"] == "2030-01-04T03:00:00Z" and st.get("last_error") is None and st["last_name"]
    assert len([b for b in backups.listing(db) if b["label"] == "scheduled"]) == 2  # keep
    settings.save(db, cfg, "backups", {"schedule": None}, "t")  # turned off: nothing more
    eff = settings.effective(db, cfg)
    assert backups.run_due(db, eff, now=t + dt.timedelta(days=9)) is False
    assert backups.run_due(db, eff, now=t + dt.timedelta(days=9)) is False
