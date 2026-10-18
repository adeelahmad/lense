"""netguard on its own (the browser-proxy service): the browser's only way out, to public web addresses only, each
connection with its own allowance of bytes, for as long as it runs."""

from __future__ import annotations

import http.server
import pathlib
import socket
import subprocess
import sys
import threading
import time

import pytest

from app.domain import netguard


def send(proxy, raw):
    """One request to the proxy; what came back."""
    with socket.create_connection(proxy, timeout=10) as s:
        s.sendall(raw)
        out = b""
        while chunk := s.recv(65536):
            out += chunk
        return out


@pytest.fixture
def forwarder():
    srv = netguard.forwarder("127.0.0.1:0", max_bytes=1000)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_only_public_addresses_on_the_webs_ports(forwarder, capsys):
    at = forwarder.server_address
    for ask in (
        b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n",  # this machine
        b"CONNECT 169.254.169.254:80 HTTP/1.1\r\n\r\n",  # a cloud's metadata address
        b"GET http://10.0.0.7/admin HTTP/1.1\r\nHost: 10.0.0.7\r\n\r\n",  # the local network
        b"CONNECT 1.1.1.1:22 HTTP/1.1\r\n\r\n",  # a public address, but not a web port
        b"GET /page HTTP/1.1\r\nHost: example.org\r\n\r\n",  # not a proxy request
    ):
        assert send(at, ask).startswith(b"HTTP/1.1 403"), ask
    said = capsys.readouterr().out
    assert "refused CONNECT 127.0.0.1:443" in said and "port 22 isn't a web port" in said
    assert len(forwarder.refused) == 5 and list(forwarder.asked) == []  # nothing piles up while it runs


def test_each_connection_has_its_own_allowance(forwarder, monkeypatch):
    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            body = b"x" * 600
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    port = site.server_address[1]
    monkeypatch.setattr(netguard, "PORTS", netguard.PORTS | {port})  # as if it were 80
    forwarder.allow = lambda ip: True  # as if 127.0.0.1 were public
    try:
        ask = f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode()
        # 600 bytes back (and the head), twice: more than the 1000 allowed, but each connection on its own
        for _ in range(2):
            got = send(forwarder.server_address, ask)
            assert got.startswith(b"HTTP/1.0 200") and got.endswith(b"x" * 600)
    finally:
        site.shutdown()
        site.server_close()


def test_it_runs_on_its_own(tmp_path):
    """As the browser-proxy service runs it: the file alone, with the standard library."""
    src = pathlib.Path(netguard.__file__)
    (tmp_path / "netguard.py").write_text(src.read_text())
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    p = subprocess.Popen(
        [sys.executable, "-I", str(tmp_path / "netguard.py"), "--listen", f"127.0.0.1:{port}"],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert "Forwarding to public addresses" in (p.stdout.readline() if p.stdout else "")
        for _ in range(50):
            try:
                got = send(("127.0.0.1", port), b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")
                break
            except OSError:
                time.sleep(0.1)
        assert got.startswith(b"HTTP/1.1 403")
    finally:
        p.terminate()
        p.wait(10)
