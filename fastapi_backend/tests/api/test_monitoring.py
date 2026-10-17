"""Turning the monitoring profile (Prometheus, node-exporter, Grafana) on and off from the web app (docs/prometheus.md)."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

from tests.helpers import login, make_user

AGENT = pathlib.Path(__file__).resolve().parents[3] / "packaging" / "host" / "lens-upgrade.sh"


@pytest.fixture
def control(tmp_path, monkeypatch):
    """The control folder install.sh shares with the containers, with an agent watching it."""
    d = tmp_path / "control"
    (d / "inbox").mkdir(parents=True)
    (d / "agent").write_text("kind=systemd\n")
    monkeypatch.setenv("LENS_CONTROL_DIR", str(d))
    return d


def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def request_of(control):
    return dict(line.split("=", 1) for line in (control / "inbox" / "upgrade").read_text().splitlines())


def test_turning_monitoring_on_and_off(client, db, control):
    h = admin(client, db)
    v = client.get("/api/v1/admin/monitoring", headers=h).json()
    assert (v["on"], v["state"], v["agent"], v["grafana_port"]) == (False, None, "systemd", 3001)

    r = client.put("/api/v1/admin/monitoring", json={"on": True}, headers=h)
    assert r.status_code == 202 and r.json()["on"] is True and r.json()["state"] == "queued"
    req = request_of(control)
    assert (req["action"], req["profile"], req["turn"], req["by"]) == ("services", "monitoring", "on", "root@x.io")
    # /metrics turned on with a token made for it, and the token where the monitoring Prometheus reads it
    t = client.app.state.settings.current()["telemetry"]
    assert t["prometheus"] is True and len(t["prometheus_token"]) == 64
    assert (control / "prometheus-token").read_text() == t["prometheus_token"]
    assert client.get("/metrics", headers={"Authorization": f"Bearer {t['prometheus_token']}"}).status_code == 200
    # one request at a time
    assert client.put("/api/v1/admin/monitoring", json={"on": False}, headers=h).status_code == 409

    # the agent picks it up and says how it went
    (control / "inbox" / "upgrade").unlink()
    (control / "services-status").write_text(f"id={req['id']}\nprofile=monitoring\nstate=done\ngrafana_port=3005\ncode=0\n")
    v = client.get("/api/v1/admin/monitoring", headers=h).json()
    assert (v["state"], v["grafana_port"], v["error"]) == ("done", 3005, None)

    # a new scrape token in Settings reaches Prometheus too
    assert client.put("/api/v1/settings/telemetry", json={"prometheus_token": "b" * 32}, headers=h).status_code == 200
    assert (control / "prometheus-token").read_text() == "b" * 32

    # off stops the containers and turns /metrics off again, since monitoring turned it on
    r = client.put("/api/v1/admin/monitoring", json={"on": False}, headers=h)
    assert r.status_code == 202 and request_of(control)["turn"] == "off"
    assert client.app.state.settings.current()["telemetry"]["prometheus"] is False


def test_a_failed_change_says_why(client, db, control):
    h = admin(client, db)
    client.put("/api/v1/admin/monitoring", json={"on": True}, headers=h)
    rid = request_of(control)["id"]
    (control / "inbox" / "upgrade").unlink()
    (control / "services-status").write_text(f"id={rid}\nstate=failed\ncode=2\nerror=monitoring runs in Docker\n")
    v = client.get("/api/v1/admin/monitoring", headers=h).json()
    assert (v["state"], v["error"]) == ("failed", "monitoring runs in Docker")
    # and it can be tried again
    assert client.put("/api/v1/admin/monitoring", json={"on": True}, headers=h).status_code == 202


def test_no_agent_shows_the_line_to_run(client, db, monkeypatch):
    monkeypatch.delenv("LENS_CONTROL_DIR", raising=False)
    h = admin(client, db)
    v = client.get("/api/v1/admin/monitoring", headers=h).json()
    assert v["agent"] is None and "--profile monitoring up -d" in v["manual"]
    r = client.put("/api/v1/admin/monitoring", json={"on": True}, headers=h)
    assert r.status_code == 409 and "run the line shown" in r.json()["detail"]


def test_only_admins(client, db, control):
    make_user(db, "me@x.io", "my password 12")
    h = login(client, "me@x.io", "my password 12")
    assert client.get("/api/v1/admin/monitoring", headers=h).status_code == 403
    assert client.put("/api/v1/admin/monitoring", json={"on": True}, headers=h).status_code == 403


@pytest.mark.skipif(not shutil.which("sh") or not shutil.which("paste"), reason="needs a POSIX shell")
def test_the_agent_changes_compose_profiles(tmp_path):
    """packaging/host/lens-upgrade.sh, with a stand-in for docker: .env's COMPOSE_PROFILES and the compose commands."""
    inst, bin_ = tmp_path / "lens", tmp_path / "bin"
    (inst / ".lens-control" / "inbox").mkdir(parents=True)
    bin_.mkdir()
    calls = tmp_path / "calls"
    (bin_ / "docker").write_text(f'#!/bin/sh\necho "docker $*" >> {calls}\n')
    (bin_ / "docker").chmod(0o755)
    env = inst / ".env"
    env.write_text("ACCESS_SECRET_KEY=x\nCOMPOSE_PROFILES=matterbridge\nLENS_GRAFANA_PORT=3005\n")
    env_vars = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}

    def ask(on):
        (inst / ".lens-control" / "inbox" / "upgrade").write_text(f"id=r{on}\naction=services\nmonitoring={on}\n")
        subprocess.run(["sh", str(AGENT), str(inst)], check=True, env=env_vars, timeout=30)
        status = (inst / ".lens-control" / "services-status").read_text()
        return dict(line.split("=", 1) for line in status.splitlines())

    s = ask("on")
    assert (s["id"], s["state"], s["grafana_port"]) == ("ron", "done", "3005")
    assert "COMPOSE_PROFILES=matterbridge,monitoring" in env.read_text().splitlines()
    assert "ACCESS_SECRET_KEY=x" in env.read_text().splitlines()
    assert calls.read_text().splitlines()[-1].endswith("--profile monitoring up -d prometheus node-exporter grafana")
    assert not (inst / ".lens-control" / "inbox" / "upgrade").exists()

    s = ask("off")
    assert s["state"] == "done" and "COMPOSE_PROFILES=matterbridge" in env.read_text().splitlines()
    assert calls.read_text().splitlines()[-1].endswith("rm -s -f prometheus node-exporter grafana")

    s = ask("sideways")
    assert s["state"] == "failed" and "on or off" in s["error"]
