"""New releases, upgrading through the machine's agent, and the backups (docs/updates.md)."""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from app.domain import migrations, updates
from tests.helpers import login, make_user


class GitHub(http.server.BaseHTTPRequestHandler):
    release: dict | None = None
    calls: list[str] = []

    def do_GET(self):  # noqa: N802
        GitHub.calls.append(self.path)
        if self.path != "/repos/adeelahmad/lense/releases/latest" or GitHub.release is None:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(GitHub.release).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


@pytest.fixture
def github(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(updates, "API", f"http://127.0.0.1:{srv.server_port}")
    GitHub.release, GitHub.calls = None, []
    yield GitHub
    srv.shutdown()


@pytest.fixture
def control(tmp_path, monkeypatch):
    """The control folder install.sh shares with the containers, with an agent watching it."""
    d = tmp_path / "control"
    (d / "inbox").mkdir(parents=True)
    (d / "agent").write_text("kind=systemd\n")
    monkeypatch.setenv("LENS_CONTROL_DIR", str(d))
    monkeypatch.setattr(migrations, "backup", lambda db, label="manual": None)  # mem:// has nothing to copy
    return d


def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def release(tag):
    return {
        "tag_name": tag,
        "name": f"Lens {tag}",
        "html_url": f"https://github.com/adeelahmad/lense/releases/tag/{tag}",
        "published_at": "2026-10-10T12:00:00Z",
        "body": "## Features\n- more",
    }


def test_versions_compare():
    assert updates.newer("v0.10.0", "0.9.9") and updates.newer("v1.0.0", "v0.99.0")
    assert not updates.newer("v0.6.0", "0.6.0") and not updates.newer("nightly", "0.6.0")
    assert updates.version_key(updates.current())  # the running version is X.Y.Z


def test_a_newer_release_is_found_and_shown(client, db, github):
    h = admin(client, db)
    s = client.get("/api/v1/admin/updates", headers=h).json()
    assert (s["available"], s["latest"], s["checked_at"], s["check"]) == (False, None, None, True)
    assert github.calls == []  # reading never calls GitHub
    github.release = release("v99.0.0")
    s = client.post("/api/v1/admin/updates/check", headers=h).json()
    assert s["available"] and s["latest"]["tag"] == "v99.0.0" and s["latest"]["notes"].startswith("## Features")
    assert s["error"] is None and s["checked_at"]
    assert s["agent"] is None and "LENS_REF=v99.0.0 sh" in s["manual"]
    # members who aren't admins see none of it
    make_user(db, "m@x.io", "member password 1")
    assert client.get("/api/v1/admin/updates", headers=login(client, "m@x.io", "member password 1")).status_code == 403


def test_an_older_or_missing_release_is_not_an_update(client, db, github):
    h = admin(client, db)
    github.release = release("v0.0.1")
    assert client.post("/api/v1/admin/updates/check", headers=h).json()["available"] is False
    github.release = None  # no release at all
    s = client.post("/api/v1/admin/updates/check", headers=h).json()
    assert (s["available"], s["latest"], s["error"]) == (False, None, None)


def test_an_unreachable_github_is_said(client, db, monkeypatch):
    monkeypatch.setattr(updates, "API", "http://127.0.0.1:9")
    s = client.post("/api/v1/admin/updates/check", headers=admin(client, db)).json()
    assert s["error"].startswith("couldn't reach GitHub") and s["available"] is False


def test_the_daily_check_runs_when_due_and_can_be_turned_off(db, cfg, github):
    github.release = release("v99.0.0")
    assert updates.due(db, cfg)
    said = []
    updates.tick(db, cfg, said.append)
    assert said == [f"Lens v99.0.0 is out (this is {updates.current()})"] and len(github.calls) == 1
    updates.tick(db, cfg, said.append)  # not due again for a day
    assert len(github.calls) == 1
    cfg["updates"]["check"] = False
    db.q("UPDATE $r SET checked_at = '2000-01-01T00:00:00+00:00'", r=updates.ROW)
    assert not updates.due(db, cfg)


def test_settings(client, db):
    h = admin(client, db)
    assert client.put("/api/v1/settings/updates", json={"check": False, "auto_install": True}, headers=h).status_code == 200
    v = client.get("/api/v1/settings", headers=h).json()["updates"]["values"]
    assert (v["check"], v["auto_install"]) == (False, True)
    assert client.put("/api/v1/settings/updates", json={"check_hours": 0}, headers=h).status_code == 400
    assert client.put("/api/v1/settings/updates", json={"repo": "someone/else"}, headers=h).status_code == 400


def test_one_click_upgrade_hands_the_release_to_the_agent(client, db, github, control):
    h = admin(client, db)
    assert client.post("/api/v1/admin/updates/upgrade", headers=h).status_code == 409  # no release known yet
    github.release = release("v99.0.0")
    client.post("/api/v1/admin/updates/check", headers=h)
    r = client.post("/api/v1/admin/updates/upgrade", headers=h)
    assert r.status_code == 202
    for _ in range(100):  # the backup and the hand-over run after the response
        run = client.get("/api/v1/admin/updates", headers=h).json()["upgrade"]
        if run["state"] != "backing-up":
            break
    assert run["state"] == "queued" and run["ref"] == "v99.0.0" and run["by"] == "root@x.io"
    req = dict(line.split("=", 1) for line in (control / "inbox" / "upgrade").read_text().splitlines())
    assert req["ref"] == "v99.0.0" and req["id"] == run["id"]
    # one at a time
    assert client.post("/api/v1/admin/updates/upgrade", headers=h).status_code == 409
    # the agent picks it up and says how it went
    (control / "inbox" / "upgrade").unlink()
    (control / "upgrade.log").write_text("Updating Lens in /home/me/lens...\nLens is running\n")
    (control / "upgrade-status").write_text(f"id={run['id']}\nref=v99.0.0\nstate=running\nstarted_at=2026-10-10T12:00:00Z\n")
    run = client.get("/api/v1/admin/updates", headers=h).json()["upgrade"]
    assert run["state"] == "running" and run["log"][-1] == "Lens is running"
    (control / "upgrade-status").write_text(f"id={run['id']}\nref=v99.0.0\nstate=failed\ncode=1\n")
    run = client.get("/api/v1/admin/updates", headers=h).json()["upgrade"]
    assert run["state"] == "failed" and "exit 1" in run["error"]
    # and another can start
    assert client.post("/api/v1/admin/updates/upgrade", json={"ref": "v99.0.0", "backup": False}, headers=h).status_code == 202
    assert client.post("/api/v1/admin/updates/upgrade", json={"ref": "../etc", "backup": False}, headers=h).status_code in (409, 422)


def test_no_agent_no_upgrade(client, db, github):
    github.release = release("v99.0.0")
    h = admin(client, db)
    client.post("/api/v1/admin/updates/check", headers=h)
    r = client.post("/api/v1/admin/updates/upgrade", headers=h)
    assert r.status_code == 409 and "run the line shown" in r.json()["detail"]


def test_a_failed_backup_stops_the_upgrade(client, db, github, control, monkeypatch):
    def full(*_a, **_k):
        raise OSError("No space left on device")

    monkeypatch.setattr(migrations, "backup", full)
    github.release = release("v99.0.0")
    h = admin(client, db)
    client.post("/api/v1/admin/updates/check", headers=h)
    updates.upgrade(db, client.app.state.settings.current(), by="t", wait=True)
    run = client.get("/api/v1/admin/updates", headers=h).json()["upgrade"]
    assert run["state"] == "failed" and "backing up failed: No space left" in run["error"]
    assert not (control / "inbox" / "upgrade").exists()


def test_auto_install_once_per_release(db, cfg, github, control):
    github.release = release("v99.0.0")
    cfg["updates"]["auto_install"] = True
    updates.tick(db, cfg)
    run = updates.run_state(db)
    assert run["ref"] == "v99.0.0" and run["by"] == "auto_install"
    for _ in range(100):
        if (control / "inbox" / "upgrade").exists():
            break
        threading.Event().wait(0.02)
    (control / "inbox" / "upgrade").unlink()
    (control / "upgrade-status").write_text(f"id={run['id']}\nstate=failed\ncode=1\n")
    updates.tick(db, cfg)  # failed: not tried again by itself
    assert updates.run_state(db)["id"] == run["id"]


def test_backups(client, db, folder, monkeypatch):
    h = admin(client, db)
    assert client.get("/api/v1/admin/backups", headers=h).json()["backups"] == []
    monkeypatch.setattr(migrations, "backup", lambda db, label="manual": None)  # what an in-memory database gives
    assert client.post("/api/v1/admin/backups", headers=h).status_code == 400
    d = folder / "data" / "backups"
    d.mkdir(parents=True)
    (d / "manual-20261001-101010.surql.gz").write_bytes(b"x" * 10)
    (d / "before-upgrade-20261002-101010.surql.gz").write_bytes(b"y" * 20)
    (d / "before-upgrade-20261003-101010.surrealkv").mkdir()
    (d / "notes.txt").write_text("not a backup")
    got = client.get("/api/v1/admin/backups", headers=h).json()["backups"]
    assert [(b["name"], b["label"], b["kind"], b["bytes"]) for b in got] == [
        ("before-upgrade-20261003-101010.surrealkv", "before-upgrade", "folder", 0),
        ("before-upgrade-20261002-101010.surql.gz", "before-upgrade", "export", 20),
        ("manual-20261001-101010.surql.gz", "manual", "export", 10),
    ]
    r = client.get("/api/v1/admin/backups/manual-20261001-101010.surql.gz", headers=h)
    assert r.status_code == 200 and r.content == b"x" * 10
    assert client.get("/api/v1/admin/backups/before-upgrade-20261003-101010.surrealkv", headers=h).status_code == 400
    assert client.get("/api/v1/admin/backups/notes.txt", headers=h).status_code == 400
    assert client.get("/api/v1/admin/backups/..%2Fsecret.key", headers=h).status_code in (400, 404)
    assert client.delete("/api/v1/admin/backups/manual-20261001-101010.surql.gz", headers=h).status_code == 200
    assert not (d / "manual-20261001-101010.surql.gz").exists()
    assert client.delete("/api/v1/admin/backups/manual-20261001-101010.surql.gz", headers=h).status_code == 400


def test_a_backup_taken_from_the_web_app(client, db, folder, monkeypatch):
    def fake(db_, label="manual"):
        d = folder / "data" / "backups"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{label}-20261010-120000.surql.gz"
        p.write_bytes(b"z")
        return p

    monkeypatch.setattr(migrations, "backup", fake)
    b = client.post("/api/v1/admin/backups", headers=admin(client, db)).json()
    assert (b["name"], b["label"], b["bytes"]) == ("manual-20261010-120000.surql.gz", "manual", 1)
