"""The file tree over NFS: the relay that lets only the allowed networks in, the runner that starts and stops rclone
with the setting, the settings' checks and the status over the API. rclone itself is a stand-in here (it isn't
installed in CI): a script that listens where `--addr` says and echoes, which is all the runner and relay rely on."""

from __future__ import annotations

import socket
import socketserver
import sys
import threading
import time

import pytest

from app.domain import nfs_share, settings, share_relay

FAKE_RCLONE = """#!{python}
import socketserver, sys
args = sys.argv[1:]
assert args[:2] == ["serve", "nfs"] and "--read-only" in args, args
host, port = args[args.index("--addr") + 1].rsplit(":", 1)

class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(4096):
            self.request.sendall(data)

print("Serving NFS on", host, port, flush=True)
socketserver.ThreadingTCPServer((host, int(port)), Echo).serve_forever()
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def echo(port, payload=b"ping"):
    """What comes back through the port; b"" when the connection is closed on us."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(payload)
        try:
            return s.recv(4096)
        except ConnectionResetError:
            return b""


@pytest.fixture
def echo_server():
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            while data := self.request.recv(4096):
                self.request.sendall(data)

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_allowed_addresses():
    nets = share_relay.networks(["192.168.0.0/16", "fc00::/7", "not a network"])
    assert len(nets) == 2
    assert share_relay.allowed("192.168.1.20", nets)
    assert share_relay.allowed("::ffff:192.168.1.20", nets)  # IPv4 on a dual-stack socket
    assert share_relay.allowed("fd12::1", nets)
    assert not share_relay.allowed("8.8.8.8", nets)
    assert not share_relay.allowed("garbage", nets)


def test_the_relay_lets_in_only_the_allowed_networks(echo_server):
    relay = share_relay.Relay("127.0.0.1", 0, echo_server, share_relay.networks(["127.0.0.0/8"])).start()
    try:
        assert echo(relay.port) == b"ping"
        relay.nets = share_relay.networks(["10.0.0.0/8"])  # changed while running
        assert echo(relay.port) == b""
        assert relay.refused == 1
    finally:
        relay.stop()


def test_settings_are_checked(db, cfg):
    settings.save(db, cfg, "nfs", {"enabled": True, "allowed_networks": ["192.168.1.7", "192.168.1.0/24", "192.168.1.7/32"]}, "t")
    assert settings.Settings(db, cfg).current()["nfs"]["allowed_networks"] == ["192.168.1.7/32", "192.168.1.0/24"]
    for bad in ({"allowed_networks": []}, {"allowed_networks": ["0.0.0.0/0"]}, {"allowed_networks": ["nope"]},
                {"port": 0}, {"enabled": "yes"}, {"account": "me"}, {"bind": "0.0.0.0"}):  # fmt: skip
        with pytest.raises(ValueError):
            settings.save(db, cfg, "nfs", bad, "t")


def test_the_runner_follows_the_setting(db, cfg, folder, monkeypatch):
    from tests.helpers import make_user

    make_user(db, "ad@x.io", "admin password 1", admin=True)
    exe = folder / "rclone"
    exe.write_text(FAKE_RCLONE.format(python=sys.executable))
    exe.chmod(0o755)
    monkeypatch.setattr(nfs_share, "rclone_binary", lambda: str(exe))
    seen = {}

    def remote(db_, cfg_, account):
        seen["account"] = account["email"]
        return ":s3:", {"RCLONE_S3_SECRET_ACCESS_KEY": "s3cret"}

    port = free_port()
    cfg["nfs"]["bind"] = "127.0.0.1"
    settings.save(db, cfg, "nfs", {"enabled": True, "port": port}, "t")
    s = settings.Settings(db, cfg)
    revoked = []
    run = nfs_share.Runner(db, s.current, "test", remote=remote, revoke=lambda db_, aid: revoked.append(aid))
    try:
        run.tick()
        assert run.phase == "running", (run.error, run.lines)
        assert seen["account"] == "ad@x.io"  # nobody picked: the first admin
        assert "s3cret" not in " ".join(run.proc.args)  # the key isn't on the command line
        assert echo(port) == b"ping"
        st = nfs_share.status(db, s.current())
        assert st["phase"] == "running" and st["account"] == "ad@x.io" and st["process"] == "test"

        settings.save(db, cfg, "nfs", {"allowed_networks": ["10.0.0.0/8"]}, "t")
        run.tick()
        assert echo(port) == b""  # this machine isn't allowed any more, without a restart
        run.tick()
        assert nfs_share.status(db, s.current())["refused"] == 1

        settings.save(db, cfg, "nfs", {"enabled": False}, "t")
        run.tick()
        assert run.proc is None and run.relay is None
        assert len(revoked) == 1  # its S3 key goes once the share stops
        assert nfs_share.status(db, s.current())["phase"] == "off"
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
    finally:
        run.stop_serving()


def test_it_says_what_is_wrong(db, cfg, monkeypatch):
    settings.save(db, cfg, "nfs", {"enabled": True, "port": free_port()}, "t")
    s = settings.Settings(db, cfg)
    monkeypatch.setattr(nfs_share, "rclone_binary", lambda: None)
    run = nfs_share.Runner(db, s.current, "test")
    run.tick()
    assert run.phase == "error" and "rclone" in run.error
    assert nfs_share.status(db, s.current())["error"] == run.error
    monkeypatch.setattr(nfs_share, "rclone_binary", lambda: "/bin/true")
    run.next_try = time.monotonic() - 1
    run.tick()
    assert run.phase == "error" and "admin" in run.error  # no account to show the tree as


def test_status_over_the_api(client, db, monkeypatch):
    from tests.helpers import login, make_user

    monkeypatch.setenv("LENS_NFS_PORT", "12049")
    make_user(db, "ad@x.io", "admin password 1", admin=True)
    make_user(db, "me@x.io", "member password 1")
    h = login(client, "ad@x.io", "admin password 1")
    j = client.get("/api/v1/settings/nfs/status", headers=h).json()
    assert j["enabled"] is False and j["phase"] == "off" and j["port"] == 12049 and "port=12049" in j["mount"]
    assert client.put("/api/v1/settings/nfs", headers=h, json={"allowed_networks": ["0.0.0.0/0"]}).status_code == 400
    m = login(client, "me@x.io", "member password 1")
    assert client.get("/api/v1/settings/nfs/status", headers=m).status_code == 403
