"""Logging: records in a file per process that the log viewer reads, redacted, and sent to syslog only when turned on."""

from __future__ import annotations

import datetime as dt
import http.server
import ipaddress
import json
import logging
import socket
import socketserver
import ssl
import threading
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

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


class _Http(http.server.BaseHTTPRequestHandler):
    got: list[tuple[str, dict, bytes]] = []
    status = 204

    def do_POST(self):  # noqa: N802 - the stdlib's name
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _Http.got.append((self.path, dict(self.headers), body))
        self.send_response(_Http.status)
        self.end_headers()

    def log_message(self, *_):
        pass


@pytest.fixture
def http_server():
    _Http.got, _Http.status = [], 204
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Http)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_http_settings_are_checked(client, db, cfg):
    h = admin(client, db)
    bad = [
        {"http": True},  # nowhere to send to
        {"http_url": "loki:3100"},
        {"http_url": "http://169.254.169.254/push"},
        {"http_url": "https://user:pass@logs.example.com"},
        {"http_kind": "carrier-pigeon"},
        {"http_level": "loud"},
        {"http_headers": "no equals sign"},
    ]
    for body in bad:
        assert client.put("/api/v1/settings/logs", json=body, headers=h).status_code == 400, body
    v = client.get("/api/v1/settings", headers=h).json()["logs"]["values"]
    assert v["http"] is False and v["http_kind"] == "loki" and v["http_headers"] == {"secret": True, "set": False}
    s = client.get("/api/v1/settings/logs/status", headers=h).json()
    assert s["http"]["on"] is False and s["http"]["target"] is None


def test_http_sends_to_loki_in_batches_with_the_sealed_headers(client, db, cfg, http_server):
    h = admin(client, db)
    logging.getLogger("lens.test").warning("while off")
    body = {"http": True, "http_url": http_server, "http_headers": "Authorization=Bearer%20s3cret,X-Scope-OrgID=home"}
    assert client.put("/api/v1/settings/logs", json=body, headers=h).status_code == 200
    v = client.get("/api/v1/settings", headers=h).json()["logs"]["values"]
    assert v["http_headers"] == {"secret": True, "set": True}  # write-only
    s = client.get("/api/v1/settings/logs/status", headers=h).json()["http"]
    assert s["on"] is True and s["target"] == f"{http_server}/loki/api/v1/push"
    for i in range(3):
        logging.getLogger("lens.test").warning("shipped %d with token=abcdef", i)
    assert _wait(lambda: any(b"shipped 2" in g[2] for g in _Http.got))
    path, headers, raw = next(g for g in _Http.got if b"shipped" in g[2])
    assert path == "/loki/api/v1/push" and headers["Authorization"] == "Bearer s3cret" and headers["X-Scope-OrgID"] == "home"
    streams = json.loads(raw)["streams"]
    lines = [json.loads(line) for st in streams for _, line in st["values"]]
    ours = [d for d in lines if d["logger"] == "lens.test"]
    assert [d["msg"] for d in ours] == [f"shipped {i} with token=***" for i in range(3)]  # one request, redacted
    st = next(st for st in streams if st["stream"]["level"] == "warning")
    assert st["stream"]["service_name"] == "lens" and st["stream"]["process"] == "api"
    assert all(len(ts) == 19 and ts.isdigit() for ts, _ in st["values"])  # nanoseconds since the epoch
    assert b"while off" not in b"".join(g[2] for g in _Http.got) and b"abcdef" not in raw
    # the sender's own request lines (httpx logs each one) aren't sent back to the server
    time.sleep(3)
    assert not any(b"/loki/api/v1/push" in g[2] for g in _Http.got)
    s = client.get("/api/v1/settings/logs/status", headers=h).json()["http"]
    assert s["sent"] >= 3 and s["last_error"] is None


def test_http_test_line_as_ndjson_and_headers_dropped_for_a_new_host(client, db, cfg, http_server):
    h = admin(client, db)
    assert client.post("/api/v1/settings/logs/test", params={"to": "http"}, headers=h).json()["ok"] is False
    body = {"http_url": f"{http_server}/ingest", "http_kind": "ndjson", "http_headers": "Authorization=Bearer%20s3cret"}
    assert client.put("/api/v1/settings/logs", json=body, headers=h).status_code == 200
    t = client.post("/api/v1/settings/logs/test", params={"to": "http"}, headers=h).json()
    assert t["ok"], t
    path, headers, raw = _Http.got[-1]
    assert path == "/ingest" and headers["Content-Type"] == "application/x-ndjson" and raw.endswith(b"\n")
    assert json.loads(raw)["msg"].endswith("test message from Settings → Logs")
    _Http.status = 500
    t = client.post("/api/v1/settings/logs/test", params={"to": "http"}, headers=h).json()
    assert t["ok"] is False and "HTTP 500" in t["error"]
    # the token was for that server: moving to another host drops it
    moved = http_server.replace("127.0.0.1", "localhost")
    assert client.put("/api/v1/settings/logs", json={"http_url": moved}, headers=h).status_code == 200
    v = client.get("/api/v1/settings", headers=h).json()["logs"]["values"]
    assert v["http_headers"] == {"secret": True, "set": False}


def test_unreachable_http_endpoint_never_blocks():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens there
    h = logs.HttpHandler(f"http://127.0.0.1:{port}/push", "json", logging.INFO, None, True)
    try:
        lg = logging.getLogger("lens.unreachable")
        lg.addHandler(h)
        t = time.time()
        for i in range(100):
            lg.warning("line %d", i)
        assert time.time() - t < 1
        assert _wait(lambda: h.stats["last_error"], seconds=8)
        assert h.stats["sent"] == 0
    finally:
        logging.getLogger("lens.unreachable").removeHandler(h)
        h.close()


def _pem(obj):
    if isinstance(obj, x509.Certificate):
        return obj.public_bytes(serialization.Encoding.PEM).decode()
    return obj.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def _cert(subject, key, issuer_key=None, issuer=None, ca=False, ip=None):
    now = dt.datetime.now(dt.UTC)
    b = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer or subject)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if ip:
        b = b.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]), critical=False)
    return b.sign(issuer_key or key, hashes.SHA256())


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """A private CA, a log server's certificate for 127.0.0.1, and Lens's client certificate, all signed by it."""
    ca_key, srv_key, cli_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
    ca = _cert("logs-ca", ca_key, ca=True)
    srv = _cert("logs.home.lan", srv_key, ca_key, "logs-ca", ip="127.0.0.1")
    cli = _cert("lens", cli_key, ca_key, "logs-ca")
    d = tmp_path_factory.mktemp("pki")
    (d / "server.pem").write_text(_pem(srv) + _pem(srv_key))
    (d / "ca.pem").write_text(_pem(ca))
    return {"ca": _pem(ca), "client": _pem(cli) + _pem(cli_key), "server": str(d / "server.pem"), "ca_file": str(d / "ca.pem")}


def _mtls(pki):
    """A server context that asks for a client certificate signed by the CA."""
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(pki["server"])
    ctx.load_verify_locations(pki["ca_file"])
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def test_client_certificates_for_syslog_over_tls_and_https(client, db, cfg, pki):
    h = admin(client, db)
    # a pasted certificate loses its line breaks in a password field: it's rebuilt, checked and sealed
    flat = " ".join(pki["client"].split("\n"))
    bad = [
        {"client_cert": "not a certificate"},
        {"client_cert": pki["ca"]},
        {"server_ca": "-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----"},
    ]
    for body in bad:
        assert client.put("/api/v1/settings/logs", json=body, headers=h).status_code == 400, body
    assert client.put("/api/v1/settings/logs", json={"client_cert": flat, "server_ca": pki["ca"]}, headers=h).status_code == 200
    v = client.get("/api/v1/settings", headers=h).json()["logs"]["values"]
    assert v["client_cert"] == {"secret": True, "set": True} and v["server_ca"].startswith("-----BEGIN CERTIFICATE-----\n")

    # syslog over TLS to a server that wants a client certificate
    _Tcp.got = []
    srv = socketserver.TCPServer(("127.0.0.1", 0), _Tcp)
    srv.socket = _mtls(pki).wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        body = {"syslog_host": "127.0.0.1", "syslog_port": srv.server_address[1], "syslog_transport": "tls"}
        assert client.put("/api/v1/settings/logs", json=body, headers=h).status_code == 200
        t = client.post("/api/v1/settings/logs/test", headers=h).json()
        assert t["ok"], t
        assert _wait(lambda: any(b"test message" in g for g in _Tcp.got))
        # without the client certificate the server turns it away (over TLS 1.3 the sender learns only afterwards)
        assert client.put("/api/v1/settings/logs", json={"client_cert": ""}, headers=h).status_code == 200
        before = len(_Tcp.got)
        client.post("/api/v1/settings/logs/test", headers=h)
        time.sleep(1)
        assert len(_Tcp.got) == before
    finally:
        srv.shutdown()

    # an https endpoint that wants one too
    _Http.got, _Http.status = [], 204
    web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Http)
    web.socket = _mtls(pki).wrap_socket(web.socket, server_side=True)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        url = f"https://127.0.0.1:{web.server_address[1]}/ingest"
        assert client.put("/api/v1/settings/logs", json={"http_url": url, "http_kind": "json"}, headers=h).status_code == 200
        assert client.post("/api/v1/settings/logs/test", params={"to": "http"}, headers=h).json()["ok"] is False
        assert client.put("/api/v1/settings/logs", json={"client_cert": pki["client"]}, headers=h).status_code == 200
        t = client.post("/api/v1/settings/logs/test", params={"to": "http"}, headers=h).json()
        assert t["ok"], t
        assert _Http.got[-1][0] == "/ingest"
    finally:
        web.shutdown()
