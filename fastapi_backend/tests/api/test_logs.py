"""Logging: records in a file per process that the log viewer reads, redacted, and sent to syslog only when turned on."""

from __future__ import annotations

import logging
import socket
import socketserver
import threading
import time

import pytest

from app.domain import logs
from tests.helpers import login, make_user


@pytest.fixture(autouse=True)
def _reset():
    yield
    logs.shutdown()


def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


class _Udp(socketserver.BaseRequestHandler):
    got: list[bytes] = []

    def handle(self):
        _Udp.got.append(self.request[0])


class _Tcp(socketserver.StreamRequestHandler):
    got: list[bytes] = []

    def handle(self):
        _Tcp.got.append(self.rfile.read())


def _wait(cond, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_redacts_credentials():
    text = (
        "GET https://bob:hunter2@example.com/x?api_key=abc123def&token=zzz Authorization: Bearer abcdefgh123 "
        "key sk-abcdefghijklmnopqrstu password=letmein prompt_tokens=1000"
    )
    out = logs.redact(text)
    for secret in ("hunter2", "abc123def", "zzz", "abcdefgh123", "sk-abcdefghijklmnopqrstu", "letmein"):
        assert secret not in out
    assert "prompt_tokens=1000" in out and "bob:***@example.com" in out


def test_viewer_reads_the_file_newest_first_and_filters(client, db, cfg):
    h = admin(client, db)
    lg = logging.getLogger("lens.test")
    lg.info("first line")
    lg.warning("second line with password=swordfish", extra={"job": 7, "api_key": "k"})
    try:
        raise ValueError("boom")
    except ValueError:
        lg.exception("third line")
    rows = client.get("/api/v1/admin/logs", params={"logger": "lens.test"}, headers=h).json()
    assert [r["msg"] for r in rows[:3]] == ["third line", "second line with password=***", "first line"]
    assert rows[0]["level"] == "error" and "ValueError: boom" in rows[0]["exc"] and rows[0]["process"] == "api"
    assert rows[1]["fields"] == {"job": 7, "api_key": "***"}
    warn = client.get("/api/v1/admin/logs", params={"logger": "lens.test", "level": "warning"}, headers=h).json()
    assert [r["msg"] for r in warn] == ["third line", "second line with password=***"]
    found = client.get("/api/v1/admin/logs", params={"q": "FIRST LINE"}, headers=h).json()
    assert [r["msg"] for r in found] == ["first line"]
    older = client.get("/api/v1/admin/logs", params={"logger": "lens.test", "before": rows[1]["ts"]}, headers=h).json()
    assert all(r["ts"] < rows[1]["ts"] for r in older)
    # admins only
    make_user(db, "a@x.io", "a password 123")
    assert client.get("/api/v1/admin/logs", headers=login(client, "a@x.io", "a password 123")).status_code == 403


def test_level_and_per_logger_levels_follow_the_settings(client, db, cfg):
    h = admin(client, db)
    body = {"level": "warning", "levels": {"lens.chatty": "error", "lens.loud": "debug"}}
    assert client.put("/api/v1/settings/logs", json=body, headers=h).status_code == 200
    client.get("/api/v1/settings/logs/status", headers=h)  # reads the settings: this process follows
    logging.getLogger("lens.quiet").info("not kept")
    logging.getLogger("lens.quiet").warning("kept")
    logging.getLogger("lens.chatty.sub").warning("not kept either")
    logging.getLogger("lens.loud").debug("kept: its own level")
    rows = client.get("/api/v1/admin/logs", params={"q": "kept"}, headers=h).json()
    assert [r["msg"] for r in rows] == ["kept: its own level", "kept"]
    assert client.get("/api/v1/settings/logs/status", headers=h).json()["level"] == "warning"


def test_settings_are_checked(client, db, cfg):
    h = admin(client, db)
    bad = [
        {"level": "loud"},
        {"syslog": True},  # nowhere to send to
        {"syslog_host": "169.254.169.254"},
        {"syslog_host": "host:514"},
        {"syslog_port": 0},
        {"syslog_transport": "carrier-pigeon"},
        {"levels": {"bad name!": "info"}},
        {"file_mb": 1000},
    ]
    for body in bad:
        assert client.put("/api/v1/settings/logs", json=body, headers=h).status_code == 400, body
    v = client.get("/api/v1/settings", headers=h).json()["logs"]["values"]
    assert v["syslog"] is False and v["level"] == "info" and v["file"] is True


def test_syslog_off_by_default_then_sent_over_udp(client, db, cfg):
    _Udp.got = []
    srv = socketserver.UDPServer(("127.0.0.1", 0), _Udp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        h = admin(client, db)
        s = client.get("/api/v1/settings/logs/status", headers=h).json()
        assert s["syslog"] is False and s["target"] is None
        logging.getLogger("lens.test").warning("while off")
        port = srv.server_address[1]
        r = client.put(
            "/api/v1/settings/logs",
            json={"syslog_host": "127.0.0.1", "syslog_port": port, "syslog_facility": "local3"},
            headers=h,
        )
        assert r.status_code == 200
        t = client.post("/api/v1/settings/logs/test", headers=h).json()
        assert t["ok"], t
        assert _wait(lambda: any(b"test message" in g for g in _Udp.got))
        assert not any(b"while off" in g for g in _Udp.got)
        client.put("/api/v1/settings/logs", json={"syslog": True}, headers=h)
        s = client.get("/api/v1/settings/logs/status", headers=h).json()
        assert s["syslog"] is True and s["target"] == f"udp://127.0.0.1:{port}"
        logging.getLogger("lens.test").warning("shipped with token=abcdef")
        assert _wait(lambda: any(b"shipped" in g for g in _Udp.got))
        line = next(g for g in _Udp.got if b"shipped" in g).decode()
        # <local3 * 8 + warning 4>1 ts host lens api.pid lens.test - msg
        assert line.startswith("<156>1 ") and " lens api." in line and " lens.test - shipped with token=***" in line
        assert "abcdef" not in line
    finally:
        srv.shutdown()


def test_syslog_over_tcp_uses_octet_counting():
    _Tcp.got = []
    srv = socketserver.TCPServer(("127.0.0.1", 0), _Tcp)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = {
            "logs": {"syslog_host": "127.0.0.1", "syslog_port": srv.server_address[1], "syslog_transport": "tcp", "syslog_format": "json"}
        }
        ok, error, _ = logs.test_send(cfg)
        assert ok, error
        assert _wait(lambda: _Tcp.got)
        n, _, msg = _Tcp.got[0].partition(b" ")
        assert int(n) == len(msg) and msg.startswith(b"<134>1 ") and b'"msg": "Lens ' in msg
    finally:
        srv.shutdown()


def test_unreachable_server_never_blocks():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens there
    h = logs.SyslogHandler("127.0.0.1", port, "tcp", logging.INFO, 16, "text", "octet", True, "lens")
    try:
        lg = logging.getLogger("lens.unreachable")
        lg.addHandler(h)
        t = time.time()
        for i in range(100):
            lg.warning("line %d", i)
        assert time.time() - t < 1
        assert _wait(lambda: h.stats["last_error"])
    finally:
        logging.getLogger("lens.unreachable").removeHandler(h)
        h.close()
