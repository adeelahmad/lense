"""Auto scaling on Linux hosts over SSH (docs/scaling.md "Providers"): host keys trusted on first use and held to,
workers started, found, counted per host and stopped. Paramiko is an extra, so a stand-in for it here runs each
command with this machine's `sh` in a home folder of its own: the real shell the driver sends."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import types

import pytest

from app.domain import scaling, scaling_ssh
from app.domain import scaling_drivers as drivers
from tests.helpers import login, make_user


class Key:
    def __init__(self, name="ssh-ed25519", b64="QUFBQQ=="):
        self.name, self.b64 = name, b64

    def get_name(self):
        return self.name

    def get_base64(self):
        return self.b64


def fake_paramiko(hosts):
    """`hosts`: {(host, port): {"home": path, "key": Key}}."""
    m = types.ModuleType("paramiko")

    class SSHException(Exception):
        pass

    class AuthenticationException(SSHException):
        pass

    class PasswordRequiredException(AuthenticationException):
        pass

    class BadHostKeyException(SSHException):
        pass

    class MissingHostKeyPolicy:
        pass

    class Out:
        def __init__(self, data, code):
            self.data, self.channel = data, types.SimpleNamespace(recv_exit_status=lambda: code)

        def read(self):
            return self.data

    class In:
        def __init__(self, run):
            self.buf, self.run = [], run
            self.channel = types.SimpleNamespace(shutdown_write=lambda: None)

        def write(self, s):
            self.buf.append(s)

    class Keys(dict):
        def add(self, name, kind, key):
            self[name] = key

    class SSHClient:
        def __init__(self):
            self.keys, self.policy, self.host = Keys(), None, None

        def get_host_keys(self):
            return self.keys

        def set_missing_host_key_policy(self, policy):
            self.policy = policy

        def connect(self, host, port=22, username=None, pkey=None, **kw):
            h = hosts.get((host, port))
            if h is None:
                raise OSError("no route to host")
            name = host if port == 22 else f"[{host}]:{port}"
            if name in self.keys:
                if self.keys[name].get_base64() != h["key"].get_base64():
                    raise BadHostKeyException()
            else:
                self.policy.missing_host_key(self, name, h["key"])
            if pkey != "the key":
                raise AuthenticationException()
            self.host = h

        def exec_command(self, command, timeout=None):
            client = self

            class Stdin(In):
                pass

            stdin = Stdin(None)

            class Lazy(Out):
                def __init__(self):
                    self._done = None
                    self.channel = types.SimpleNamespace(recv_exit_status=lambda: self._run()[0])

                def _run(self):
                    if self._done is None:
                        env = {"HOME": client.host["home"], "PATH": os.environ["PATH"]}
                        r = subprocess.run(
                            ["sh", "-c", command], input="".join(stdin.buf).encode(), capture_output=True, env=env, timeout=30
                        )
                        self._done = (r.returncode, r.stdout)
                    return self._done

                def read(self):
                    return self._run()[1]

            return stdin, Lazy(), None

        def close(self):
            pass

    class PKey:
        @staticmethod
        def from_type_string(kind, data):
            import base64

            return Key(kind, base64.b64encode(data).decode())

    class Ed25519Key:
        @staticmethod
        def from_private_key(f, password=None):
            if f.read() != "PRIVATE":
                raise SSHException("not a key")
            return "the key"

    class Other:
        @staticmethod
        def from_private_key(f, password=None):
            raise SSHException("not a key")

    m.__dict__.update(
        SSHException=SSHException,
        AuthenticationException=AuthenticationException,
        PasswordRequiredException=PasswordRequiredException,
        BadHostKeyException=BadHostKeyException,
        MissingHostKeyPolicy=MissingHostKeyPolicy,
        SSHClient=SSHClient,
        PKey=PKey,
        Ed25519Key=Ed25519Key,
        ECDSAKey=Other,
        RSAKey=Other,
    )
    return m


@pytest.fixture
def hosts(tmp_path, monkeypatch):
    if not sys.platform.startswith("linux") and sys.platform != "darwin":
        pytest.skip("needs a POSIX shell")
    hs = {
        ("box1", 22): {"home": str(tmp_path / "box1"), "key": Key(b64="Ym94MQ==")},
        ("10.0.0.5", 2222): {"home": str(tmp_path / "box2"), "key": Key(b64="Ym94Mg==")},
    }
    for h in hs.values():
        os.makedirs(h["home"])
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko(hs))
    return hs


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def add(client, h, **over):
    body = {
        "name": "Boxes",
        "driver": "ssh",
        "settings": {"hosts": ["lens@box1", "lens@10.0.0.5:2222"]},
        "secrets": {"private_key": "PRIVATE"},
        **over,
    }
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
    p = add(client, h)
    assert p["check"]["ok"] is True and p["check"]["host_keys"] == {
        "lens@box1": "ssh-ed25519 Ym94MQ==",
        "lens@10.0.0.5:2222": "ssh-ed25519 Ym94Mg==",
    }
    assert add(client, h, name="Bad key", secrets={"private_key": "nope"})["check"]["error"].startswith("the private key can't be read")

    hosts[("box1", 22)]["key"] = Key(b64="ZXZpbA==")  # someone else answers at box1 now
    r = client.post(f"/api/v1/scaling/providers/{p['id']}/check", headers=h).json()
    assert r["ok"] is False and "host key changed" in r["error"]
    assert r["host_keys"]["lens@box1"] == "ssh-ed25519 Ym94MQ=="  # a failed check doesn't forget the keys it trusts
    drv = drivers.get("ssh")
    with pytest.raises(drivers.DriverError, match="host key changed"):
        drv.launch(scaling.opened(db, cfg, p["id"]), {"per_host": 1}, "lens-w-1", {"env": {}, "line": "sleep 30", "tag": "lens"})


def test_ssh_starts_counts_and_stops_workers(client, db, cfg, h, hosts, monkeypatch):
    monkeypatch.setattr(drivers.Driver, "ready", lambda self: True)
    p = add(client, h)
    drv = drivers.get("ssh")
    opened = scaling.opened(db, cfg, p["id"])
    boot = {"env": {"LENS_SCALING_UNIT": "x"}, "line": "sleep 30", "tag": "lens"}
    a = drv.launch(opened, {"per_host": 1, "steps": ["ocr"]}, "lens-w-1", {**boot, "env": {"LENS_SCALING_UNIT": "lens-w-1"}})
    b = drv.launch(opened, {"per_host": 1}, "lens-w-2", {**boot, "env": {"LENS_SCALING_UNIT": "lens-w-2"}})
    assert a == {"id": "lens@box1#lens-w-1"} and b == {"id": "lens@10.0.0.5:2222#lens-w-2"}  # one each: per_host 1
    with pytest.raises(drivers.DriverError, match="every host runs its 1 worker"):
        drv.launch(opened, {"per_host": 1}, "lens-w-3", boot)

    box1 = os.path.join(hosts[("box1", 22)]["home"], ".lens-scaling")
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
