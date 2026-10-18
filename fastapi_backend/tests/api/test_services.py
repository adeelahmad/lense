"""Lens's optional containers (the MCP runner, the browser, monitoring), turned on and off from the web app through the
machine's upgrade agent (services.py, packaging/host/lens-upgrade.sh; the agent itself: packaging/host/test-services.sh)."""

from __future__ import annotations

import socket

import pytest

from tests.helpers import login, make_user


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


def done(control, rid, state="done", **more):
    """The agent picks the request up and says how it went."""
    (control / "inbox" / "upgrade").unlink(missing_ok=True)
    extra = "".join(f"{k}={v}\n" for k, v in more.items())
    (control / "services-status").write_text(f"id={rid}\nstate={state}\n{extra}")


def of(client, h, profile):
    return next(s for s in client.get("/api/v1/admin/services", headers=h).json()["services"] if s["profile"] == profile)


def test_each_profile_is_turned_on_and_off_on_its_own(client, db, control):
    h = admin(client, db)
    got = client.get("/api/v1/admin/services", headers=h).json()["services"]
    assert [(s["profile"], s["on"], s["state"], s["agent"]) for s in got] == [
        ("mcp", False, None, "systemd"),
        ("browser", False, None, "systemd"),
        ("monitoring", False, None, "systemd"),
        ("mesh", False, None, "systemd"),  # started by adding a node (tests/api/test_mesh_service.py)
    ]
    assert "--profile browser up -d" in got[1]["manual"]

    r = client.put("/api/v1/admin/services/browser", json={"on": True}, headers=h)
    assert r.status_code == 202 and (r.json()["on"], r.json()["state"]) == (True, "queued")
    req = request_of(control)
    assert (req["action"], req["profile"], req["turn"], req["by"]) == ("services", "browser", "on", "root@x.io")
    # one request at a time, whichever the profile
    busy = client.put("/api/v1/admin/services/mcp", json={"on": True}, headers=h)
    assert busy.status_code == 409 and "busy" in busy.json()["detail"]

    # picked up, but still starting: the others wait for it
    done(control, req["id"], "running")
    busy = client.put("/api/v1/admin/services/mcp", json={"on": True}, headers=h)
    assert busy.status_code == 409 and "still changing the browser" in busy.json()["detail"]

    done(control, req["id"])
    assert of(client, h, "browser")["state"] == "done"
    assert client.put("/api/v1/admin/services/mcp", json={"on": True}, headers=h).status_code == 202
    mcp = request_of(control)
    assert (mcp["profile"], mcp["turn"]) == ("mcp", "on")
    done(control, mcp["id"], "failed", code=2, error="mcp runs in Docker")
    # the status file is about the MCP runner now; the browser keeps what the agent said about it
    assert (of(client, h, "browser")["on"], of(client, h, "browser")["state"]) == (True, "done")
    assert (of(client, h, "mcp")["state"], of(client, h, "mcp")["error"]) == ("failed", "mcp runs in Docker")

    # off, the same way
    r = client.put("/api/v1/admin/services/browser", json={"on": False}, headers=h)
    assert r.status_code == 202 and request_of(control)["turn"] == "off" and r.json()["on"] is False


def test_monitoring_through_the_same_switch(client, db, control):
    h = admin(client, db)
    r = client.put("/api/v1/admin/services/monitoring", json={"on": True}, headers=h)
    assert r.status_code == 202 and request_of(control)["profile"] == "monitoring"
    # as PUT /admin/monitoring: /metrics on with a token
    assert client.app.state.settings.current()["telemetry"]["prometheus"] is True
    assert client.get("/api/v1/admin/monitoring", headers=h).json()["on"] is True


def test_no_agent_shows_the_line_to_run(client, db, monkeypatch):
    monkeypatch.delenv("LENS_CONTROL_DIR", raising=False)
    h = admin(client, db)
    b = of(client, h, "browser")
    assert b["agent"] is None and b["manual"].endswith("--profile browser up -d")
    r = client.put("/api/v1/admin/services/browser", json={"on": True}, headers=h)
    assert r.status_code == 409 and "run the line shown" in r.json()["detail"]
    assert client.put("/api/v1/admin/services/fedora", json={"on": True}, headers=h).status_code == 404


def test_only_admins(client, db, control):
    make_user(db, "me@x.io", "my password 12")
    h = login(client, "me@x.io", "my password 12")
    assert client.get("/api/v1/admin/services", headers=h).status_code == 403
    assert client.put("/api/v1/admin/services/browser", json={"on": True}, headers=h).status_code == 403


def test_whether_it_answers(client, db, monkeypatch):
    """A container Lens reaches says whether it runs; one never turned on here is on while it does (set up by hand)."""
    h = admin(client, db)
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        monkeypatch.setenv("LENS_BROWSER_URL", f"http://127.0.0.1:{srv.getsockname()[1]}")
        b = of(client, h, "browser")
        assert (b["running"], b["on"]) == (True, True)
    with socket.socket() as gone:
        gone.bind(("127.0.0.1", 0))
        monkeypatch.setenv("LENS_BROWSER_URL", f"http://127.0.0.1:{gone.getsockname()[1]}")
        assert (of(client, h, "browser")["running"], of(client, h, "browser")["on"]) == (False, False)
    monkeypatch.delenv("LENS_BROWSER_URL")
    assert of(client, h, "browser")["running"] is None
    assert of(client, h, "monitoring")["running"] is None
