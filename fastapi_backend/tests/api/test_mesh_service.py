"""Nebula on the primary (docs/mesh.md, "On this machine"): the optional `mesh` container (mesh/sidecar.py) runs it
with the config Lens's keeper writes into the folder the two share. The container makes its own key pair and
publishes the public key; the keeper signs it and writes the config; the container checks it with `nebula -test`,
runs Nebula, reloads it when the config changes (a removed node's certificate on the blocklist) and relays the
database and the API on the mesh address. Nebula is a stand-in here; the same flow was run with Nebula 1.11.2 in
Docker: a worker node reached the database and the API through the container, a compute node only the API, and a
removed node nothing."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import pathlib
import socket
import sys
import tarfile
import threading
import time

import pytest

from app.domain import mesh, services, settings
from tests.api.test_mesh import admin, joined, nebula, server_db  # noqa: F401 - fixtures

SIDECAR = pathlib.Path(__file__).resolve().parents[3] / "mesh" / "sidecar.py"


def load_sidecar():
    spec = importlib.util.spec_from_file_location("lens_mesh_sidecar", SIDECAR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sidecar():
    return load_sidecar()


@pytest.fixture
def stub_nebula(nebula):  # noqa: F811 - the fixture from test_mesh.py
    """A nebula that passes -test, then runs until stopped, saying when it's reloaded (SIGHUP)."""
    (nebula / "nebula").write_text(
        f"#!{sys.executable}\n"
        "import json, signal, sys, time\n"
        "if '-test' in sys.argv:\n"
        "    cfg = json.load(open(sys.argv[sys.argv.index('-config') + 1]))\n"
        "    sys.exit(0 if cfg['pki'].get('key') and cfg['pki'].get('cert') else 1)\n"
        "signal.signal(signal.SIGHUP, lambda *_: print(json.dumps({'msg': 'reloaded'}), flush=True))\n"
        "print(json.dumps({'msg': 'Nebula interface is active'}), flush=True)\n"
        "while True:\n"
        "    time.sleep(0.1)\n"
    )
    return nebula


def echo_server():
    s = socket.create_server(("127.0.0.1", 0))

    def serve():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            with c:
                c.sendall(b"db:" + c.recv(100))

    threading.Thread(target=serve, daemon=True).start()
    return s


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_sidecar_pins_the_nebula_lens_does(sidecar):
    assert sidecar.NEBULA_VERSION == mesh.NEBULA_VERSION
    assert sidecar.SHA256 == {k: v for k, v in mesh.SHA256.items() if k.startswith("nebula-linux-")}
    assert sidecar.release_name("aarch64") == "nebula-linux-arm64.tar.gz"
    assert sidecar.relays("8000=surrealdb:8000, 8080=backend:8000") == [(8000, ("surrealdb", 8000)), (8080, ("backend", 8000))]
    with pytest.raises(ValueError):
        sidecar.relays("8000=surrealdb")


def test_the_sidecar_downloads_nebula_only_with_its_checksum(sidecar, tmp_path, monkeypatch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name in ("nebula", "nebula-cert"):
            data = b"#!/bin/sh\necho " + name.encode() + b"\n"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    blob = buf.getvalue()
    monkeypatch.delenv("LENS_NEBULA_DIR", raising=False)
    monkeypatch.setattr(sidecar.platform, "machine", lambda: "x86_64")

    def opener(url, timeout):
        assert url == sidecar.RELEASES + "nebula-linux-amd64.tar.gz"
        return io.BytesIO(blob)

    with pytest.raises(RuntimeError, match="checksum"):
        sidecar.programs(tmp_path, opener=opener)
    assert not any((tmp_path / "bin").rglob("nebula*"))
    monkeypatch.setitem(sidecar.SHA256, "nebula-linux-amd64.tar.gz", hashlib.sha256(blob).hexdigest())
    got = sidecar.programs(tmp_path, opener=opener)
    assert pathlib.Path(got["nebula"]).read_bytes().endswith(b"echo nebula\n")
    assert sidecar.programs(tmp_path, fetch=False) == got  # kept: not fetched again


def test_the_keeper_and_the_sidecar_bring_up_the_primary(client, db, cfg, server_db, stub_nebula, sidecar, tmp_path, monkeypatch):  # noqa: F811
    shared, state = tmp_path / "shared", tmp_path / "state"
    shared.mkdir()
    monkeypatch.setenv("LENS_MESH_DIR", str(shared))
    target = echo_server()
    port = free_port()
    svc = sidecar.Service(shared, state, f"{port}=127.0.0.1:{target.getsockname()[1]}", listen_port="4242")
    h = admin(client, db)
    current = lambda: settings.effective(db, cfg)  # noqa: E731
    keeper = mesh.Keeper(db, current, "test")

    # off: nothing written but "off"; the container runs nothing
    keeper.tick()
    assert json.loads((shared / "lens.json").read_text()) == {"on": False}
    svc.tick()
    assert svc.status()["state"] == "off" and not (shared / "host.pub").exists()

    # adding a node turns the mesh on: the keeper says so, and asks the machine to start the container
    asked = []
    monkeypatch.setattr(services, "ask", lambda db_, profile, on, by=None: asked.append((profile, on)))
    token = client.post("/api/v1/mesh/tokens", json={"role": "worker"}, headers=h).json()["secret"]
    keeper.tick()
    assert asked == [("mesh", True)]
    lens = json.loads((shared / "lens.json").read_text())
    assert lens["on"] is True and lens["ip"] == "10.73.0.1" and lens["config"] is None  # waiting for the key
    svc.tick()  # makes its key pair; publishes the public key
    assert svc.status()["state"] == "waiting"
    pub = (shared / "host.pub").read_text()
    assert "PUBLIC KEY" in pub and "PRIVATE" not in pub
    assert (state / "host.key").stat().st_mode & 0o077 == 0  # the private key stays in the container's own volume

    keeper.tick()  # signs it
    lens = json.loads((shared / "lens.json").read_text())
    assert lens["public_key"] == pub and lens["config"]["pki"]["cert"] and "key" not in lens["config"]["pki"]
    primary = next(n for n in client.get("/api/v1/mesh/nodes", headers=h).json() if n["role"] == "primary")
    assert primary["fingerprint"] and primary["not_after"]
    keeper.tick()
    assert json.loads((shared / "lens.json").read_text())["config"]["pki"]["cert"] == lens["config"]["pki"]["cert"]  # not signed again

    # the container runs Nebula with its own key added, and relays the mesh address's ports (127.0.0.1 here)
    lens["ip"] = "127.0.0.1"
    (shared / "lens.json").write_text(json.dumps(lens))
    svc.tick()
    s = svc.status()
    assert s["nebula"] is True and s["state"] == "running", s
    run = json.loads((state / "nebula.json").read_text())
    assert run["pki"]["key"] == str(state / "host.key") and run["listen"]["port"] == 4242
    with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
        c.sendall(b"hello")
        assert c.recv(100) == b"db:hello"

    # a node removed: the blocklist grows, the keeper writes it, and Nebula reloads it without a restart
    nid, _, _ = joined(client, cfg, token)
    pid = s["pid"]
    assert client.delete(f"/api/v1/mesh/nodes/{nid}", headers=h).status_code == 200
    keeper.tick()
    lens2 = json.loads((shared / "lens.json").read_text())
    assert lens2["config"]["pki"]["blocklist"] and lens2["version"] != lens["version"]
    lens2["ip"] = "127.0.0.1"
    (shared / "lens.json").write_text(json.dumps(lens2))
    svc.tick()
    for _ in range(50):
        if any("reloaded" in line for line in svc.status()["log"]):
            break
        time.sleep(0.05)
    assert svc.status()["pid"] == pid and any("reloaded" in line for line in svc.status()["log"])

    # what Settings › Network shows about it
    svc.save()
    got = client.get("/api/v1/mesh", headers=h).json()["service"]
    assert got["available"] is True and got["nebula"] is True and got["state"] == "running"
    assert got["relays"][0]["listening"] is True and got["manual"].endswith("--profile mesh up -d")

    # turned off: the keeper says so and asks the machine to stop the container; Nebula and the relays stop
    assert client.put("/api/v1/settings/mesh", json={"enabled": False}, headers=h).status_code == 200
    keeper.tick()
    assert asked[-1] == ("mesh", False)
    assert json.loads((shared / "lens.json").read_text()) == {"on": False}
    svc.tick()
    assert svc.status()["nebula"] is False and svc.status()["state"] == "off"
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)
    svc.stop()
    target.close()


def test_a_stopped_nebula_is_started_again_after_a_while(db, cfg, server_db, stub_nebula, sidecar, tmp_path, monkeypatch):  # noqa: F811
    shared, state = tmp_path / "shared", tmp_path / "state"
    shared.mkdir()
    monkeypatch.setenv("LENS_MESH_DIR", str(shared))
    cfg = {**cfg, "mesh": {"enabled": True}}
    monkeypatch.setattr(services, "ask", lambda *a, **k: None)
    keeper = mesh.Keeper(db, lambda: cfg, "test")
    svc = sidecar.Service(shared, state, "")
    keeper.tick()
    svc.tick()
    keeper.tick()
    svc.tick()
    first = svc.status()["pid"]
    assert first
    svc.proc.kill()
    svc.proc.wait()
    svc.tick()
    s = svc.status()
    assert s["state"] == "error" and "Nebula stopped" in s["error"] and s["restarts"] == 1
    svc.next_try = 0
    svc.tick()
    assert svc.status()["nebula"] is True and svc.status()["pid"] != first
    svc.stop()


def test_no_mesh_folder_no_keeper(db, cfg, monkeypatch):
    monkeypatch.delenv("LENS_MESH_DIR", raising=False)
    assert mesh.start(db, lambda: cfg, threading.Event(), "test") is None
    assert mesh.service_view(db, cfg)["available"] is False
