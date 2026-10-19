"""Auto scaling on Linux hosts over SSH (docs/scaling.md "Providers"): host keys trusted on first use and held to,
workers started, found, counted per host and stopped. Each host is a real SSH server on this machine (paramiko's,
speaking the SSH protocol to the driver's client), which checks the client's key and runs each command with this
machine's `sh` in a home folder of its own: the real shell the driver sends."""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from app.domain import scaling, scaling_ssh
from app.domain import scaling_drivers as drivers
from tests.helpers import login, make_user

paramiko = pytest.importorskip("paramiko")

CLIENT = paramiko.ECDSAKey.generate()  # the provider's key, made here: no key is kept in the repository
PRIVATE = io.StringIO()
CLIENT.write_private_key(PRIVATE)
PRIVATE = PRIVATE.getvalue()


class Host(paramiko.ServerInterface):
    """An SSH server on 127.0.0.1 that lets `user` in with the provider's key and runs what it is asked in `home`."""

    def __init__(self, home, user="lens"):
        self.home, self.user, self.key = home, user, paramiko.ECDSAKey.generate()
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.spec = f"{user}@127.0.0.1:{self.port}"
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def public(self):
        return f"{self.key.get_name()} {self.key.get_base64()}"

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn):
        """One client's connection, served apart, so one that goes away mid-handshake (as a client that refuses the
        host key does) leaves the host answering the next."""
        t = paramiko.Transport(conn)
        t.add_server_key(self.key)
        try:
            t.start_server(server=self)
        except (EOFError, OSError, paramiko.SSHException):
            t.close()

    def close(self):
        self.sock.close()

    def get_allowed_auths(self, username):
        return "publickey"

    def check_auth_publickey(self, username, key):
        ok = username == self.user and key.get_base64() == CLIENT.get_base64()
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        threading.Thread(target=self._run, args=(channel, command.decode()), daemon=True).start()
        return True

    def _run(self, channel, command):
        env = {"HOME": self.home, "PATH": os.environ["PATH"]}
        proc = subprocess.Popen(["sh", "-c", command], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)

        def feed():
            try:
                while data := channel.recv(65536):
                    proc.stdin.write(data)
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        threading.Thread(target=feed, daemon=True).start()
        out = proc.stdout.read()
        code = proc.wait(timeout=30)
        channel.sendall(out)
        channel.send_exit_status(code)
        channel.close()


@pytest.fixture
def hosts(tmp_path):
    if not sys.platform.startswith("linux") and sys.platform != "darwin":
        pytest.skip("needs a POSIX shell")
    hs = []
    for name in ("box1", "box2"):
        os.makedirs(tmp_path / name)
        hs.append(Host(str(tmp_path / name)))
    yield hs
    for x in hs:
        x.close()


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def add(client, h, hosts, **over):
    body = {"name": "Boxes", "driver": "ssh", "settings": {"hosts": [x.spec for x in hosts]}, "secrets": {"private_key": PRIVATE}, **over}
    r = client.post("/api/v1/scaling/providers", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_host_specs():
    assert scaling_ssh.parse_host("lens@box1") == ("lens", "box1", 22)
    assert scaling_ssh.parse_host("ops@[fd00::5]:2200") == ("ops", "fd00::5", 2200)
    assert scaling_ssh.known_name("fd00::5", 2200) == "[fd00::5]:2200"
    for bad in ("box1", "lens@", "lens@box1:0", "lens@box1:99999", "Lens;rm@box", "lens@bo x"):
        with pytest.raises(ValueError):
            scaling_ssh.parse_host(bad)


def test_ssh_trusts_host_keys_on_first_use_and_holds_to_them(client, db, cfg, h, hosts, monkeypatch):
    monkeypatch.setattr(drivers.Driver, "ready", lambda self: True)
    p = add(client, h, hosts)
    box1, box2 = hosts
    assert p["check"]["ok"] is True and p["check"]["host_keys"] == {box1.spec: box1.public, box2.spec: box2.public}
    assert add(client, h, hosts, name="Bad key", secrets={"private_key": "nope"})["check"]["error"].startswith(
        "the private key can't be read"
    )
    other = io.StringIO()
    paramiko.ECDSAKey.generate().write_private_key(other)
    assert "refused the key" in add(client, h, hosts, name="Other key", secrets={"private_key": other.getvalue()})["check"]["error"]

    first = box1.public
    box1.key = paramiko.ECDSAKey.generate()  # someone else answers at box1 now
    r = client.post(f"/api/v1/scaling/providers/{p['id']}/check", headers=h).json()
    assert r["ok"] is False and "host key changed" in r["error"]
    assert r["host_keys"][box1.spec] == first  # a failed check doesn't forget the keys it trusts
    drv = drivers.get("ssh")
    with pytest.raises(drivers.DriverError, match="host key changed"):
        drv.launch(scaling.opened(db, cfg, p["id"]), {"per_host": 1}, "lens-w-1", {"env": {}, "line": "sleep 30", "tag": "lens"})


def test_ssh_starts_counts_and_stops_workers(client, db, cfg, h, hosts, monkeypatch):
    monkeypatch.setattr(drivers.Driver, "ready", lambda self: True)
    p = add(client, h, hosts)
    drv = drivers.get("ssh")
    opened = scaling.opened(db, cfg, p["id"])
    boot = {"env": {"LENS_SCALING_UNIT": "x"}, "line": "sleep 30", "tag": "lens"}
    a = drv.launch(opened, {"per_host": 1, "steps": ["ocr"]}, "lens-w-1", {**boot, "env": {"LENS_SCALING_UNIT": "lens-w-1"}})
    b = drv.launch(opened, {"per_host": 1}, "lens-w-2", {**boot, "env": {"LENS_SCALING_UNIT": "lens-w-2"}})
    assert a == {"id": f"{hosts[0].spec}#lens-w-1"} and b == {"id": f"{hosts[1].spec}#lens-w-2"}  # one each: per_host 1
    with pytest.raises(drivers.DriverError, match="every host runs its 1 worker"):
        drv.launch(opened, {"per_host": 1}, "lens-w-3", boot)

    box1 = os.path.join(hosts[0].home, ".lens-scaling")
    for _ in range(50):  # the script removes itself as it starts, since it holds the join token
        if not os.path.exists(os.path.join(box1, "lens-w-1.sh")):
            break
        time.sleep(0.1)
    assert sorted(os.listdir(box1)) == ["lens-w-1.log", "lens-w-1.pid"]

    n1 = {"name": "lens-w-1", "external_id": a["id"]}
    assert drv.describe(opened, n1) == {"state": "running"}
    assert {u["name"] for u in drv.listing(opened, "lens")} == {"lens-w-1", "lens-w-2"}
    drv.terminate(opened, n1)
    assert drv.describe(opened, n1) == {"state": "gone"} and os.listdir(box1) == []
    drv.terminate(opened, n1)  # already gone: no error
    drv.terminate(opened, {"name": "lens-w-2", "external_id": b["id"]})
    assert drv.listing(opened, "lens") == []
