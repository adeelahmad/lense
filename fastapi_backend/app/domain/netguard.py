"""A local HTTP proxy for the browser Lens prints pages with, so that what the browser may reach is Lens's to decide.

Headless Chromium is started with this proxy as its only way out (loopback included). Printing a document, the proxy
answers requests for the page Lens hands it (a document made into HTML, at http://document.lens/) and refuses
everything else, so a document can't make the browser fetch anything: not the internet, not the machine's own
services, not the cloud's metadata address. That page is also served with a Content-Security-Policy that allows no
scripts and nothing from outside it but data: images and fonts. LibreOffice is pointed at a proxy address that isn't
there, for the same end.

Capturing a web page, the proxy also forwards requests to the web (`forward`), on ports 80 and 443 only, to public
addresses only: it resolves each host itself, refuses it when any of its addresses isn't public (or in a network an
admin allowed), and connects to the address it checked, so a name can't be made to point elsewhere in between.
"""

from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import threading
import urllib.parse
import urllib.request

DOCUMENT_URL = "http://document.lens/"
# what a page Lens serves may use: its own inline styles, and images and fonts written into it; no scripts, frames,
# forms or anything fetched from elsewhere
CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'"
MAX_HEAD = 64 * 1024  # the most of a request's head that is read
PORTS = frozenset({80, 443})  # the web's own ports: nothing else is forwarded to
IDLE = 60  # seconds a forwarded connection may stay quiet
HOP = frozenset({"connection", "proxy-connection", "keep-alive", "proxy-authorization", "te", "trailer", "upgrade"})


def public_ip(ip, networks=()):
    """Whether an address may be reached for a web page: a public one, or in one of `networks` (ip_network objects)."""
    a = ipaddress.ip_address(ip)
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    if any(a in n for n in networks):
        return True
    return a.is_global and not a.is_multicast


def resolve(host, port, allow=public_ip):
    """Where to connect for host:port: every address the name has must be allowed (a name with an internal address
    among its addresses is refused), and the first is used. ValueError otherwise."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as e:
        raise ValueError(f"{host} can't be found ({e})") from None
    addrs = [i[4][0] for i in infos]
    bad = next((a for a in addrs if not allow(a)), None)
    if bad or not addrs:
        at = f" ({bad})" if bad and bad != host else ""
        raise ValueError(f"{host} isn't a public address{at}: only public web pages can be captured")
    return infos[0][4][:2]


def _pipe(a, b, budget):
    """Copy both ways between two sockets until either closes, quiet too long, or `budget` (a one-item list of bytes
    that may still come back) runs out."""
    socks = [a, b]
    while True:
        ready, _, _ = select.select(socks, [], [], IDLE)
        if not ready:
            return
        for s in ready:
            try:
                data = s.recv(65536)
            except OSError:
                return
            if not data:
                return
            if s is b:
                budget[0] -= len(data)
                if budget[0] < 0:
                    return
            try:
                (b if s is a else a).sendall(data)
            except OSError:
                return


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def handle(self):
        head, size = [], 0
        while True:
            line = self.rfile.readline(8192)
            size += len(line)
            if not line or line in (b"\r\n", b"\n") or size > MAX_HEAD:
                break
            head.append(line)
        request = head[0].decode("latin-1").strip() if head else ""
        method, _, rest = request.partition(" ")
        target = rest.rpartition(" ")[0] if " " in rest else rest
        page = self.server.pages.get(target) if method == "GET" else None
        if page is None and self.server.forward:
            try:
                if method == "CONNECT":
                    return self._tunnel(target)
                if target.startswith("http://"):
                    return self._relay(method, target, head[1:])
            except ValueError as e:
                return self._refuse(f"{method} {target}: {e}")
        if page is None:
            return self._refuse(f"{method} {target}")
        body, ctype = page
        self.wfile.write(
            (
                "HTTP/1.1 200 OK\r\n"
                f"Content-Type: {ctype}\r\n"
                f"Content-Security-Policy: {CSP}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Cache-Control: no-store\r\n"
                "Connection: close\r\n\r\n"
            ).encode("latin-1")
            + body
        )

    def _refuse(self, why):
        self.server.refused.append(why[:300])
        self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

    def _connect(self, host, port):
        if port not in PORTS:
            raise ValueError(f"port {port} isn't a web port")
        return socket.create_connection(resolve(host, port, self.server.allow), timeout=15)

    def _tunnel(self, target):
        """CONNECT host:443, for HTTPS: a tunnel to the address checked."""
        host, _, port = target.rpartition(":")
        upstream = self._connect(host.strip("[]"), int(port) if port.isdigit() else 443)
        with upstream:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            _pipe(self.connection, upstream, self.server.budget)

    def _relay(self, method, url, headers):
        """A plain http:// request, sent on to the address checked as one request on its own connection."""
        u = urllib.parse.urlsplit(url)
        if not u.hostname:
            raise ValueError("no host")
        upstream = self._connect(u.hostname, u.port or 80)
        with upstream:
            kept = [h for h in headers if h.split(b":", 1)[0].strip().lower().decode("latin-1") not in HOP]
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            upstream.sendall(f"{method} {path} HTTP/1.1\r\n".encode("latin-1") + b"".join(kept) + b"Connection: close\r\n\r\n")
            length = next((int(h.split(b":", 1)[1]) for h in kept if h.lower().startswith(b"content-length:")), 0)
            if length:
                upstream.sendall(self.rfile.read(length))
            _pipe(self.connection, upstream, self.server.budget)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    pages: dict[str, tuple[bytes, str]]
    refused: list[str]
    forward: bool
    allow: object
    budget: list[int]


class Guard:
    """The proxy, running while it's open:

    with Guard({DOCUMENT_URL: (html_bytes, "text/html; charset=utf-8")}) as g:
        run chromium with g.args() ... DOCUMENT_URL
    g.refused  # what the page asked for and didn't get

    `forward` lets it reach the web, to public addresses (and `networks`, ip_network objects an admin allowed), with
    at most `max_bytes` coming back."""

    def __init__(self, pages=None, forward=False, networks=(), max_bytes=500 * 2**20):
        self.pages = dict(pages or {})
        self.refused: list[str] = []
        self.forward, self.networks, self.max_bytes = forward, tuple(networks), max_bytes
        self._server: _Server | None = None

    def __enter__(self):
        srv = _Server(("127.0.0.1", 0), _Handler)
        srv.pages, srv.refused, srv.forward, srv.budget = self.pages, self.refused, self.forward, [self.max_bytes]
        networks = self.networks
        srv.allow = lambda ip: public_ip(ip, networks)
        threading.Thread(target=srv.serve_forever, name="netguard", daemon=True).start()
        self._server = srv
        return self

    def __exit__(self, *exc):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        return False

    @property
    def url(self):
        assert self._server is not None, "the proxy isn't running"
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def args(self):
        """Chromium's flags to go through this proxy and nothing else (not even loopback)."""
        return [f"--proxy-server={self.url}", "--proxy-bypass-list=<-loopback>"]


class Through(urllib.request.ProxyHandler):
    """urllib's proxy handler without its exceptions: every request goes through the proxy, whatever no_proxy in the
    environment says (it would otherwise reach hosts it names directly, past the checks)."""

    def proxy_open(self, req, proxy, type):  # noqa: A002 - urllib's own parameter name
        req.set_proxy(urllib.parse.urlsplit(proxy).netloc, "http")
        return None


def opener(guard):
    """A urllib opener whose every request (http, and https by CONNECT) goes through `guard`, and that opens nothing
    else, not even when a page redirects to it (urllib's usual opener would follow a redirect to ftp:// itself, straight
    to the address)."""
    o = urllib.request.OpenerDirector()
    for h in (
        Through({"http": guard.url, "https": guard.url}),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        o.add_handler(h)
    return o


def nowhere_env(env):
    """Environment variables that send a program's web requests to a proxy address nothing listens on (port 9 of
    loopback), so what it would fetch fails at once."""
    out = dict(env)
    for k in ("http_proxy", "https_proxy", "ftp_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "ALL_PROXY"):
        out[k] = "http://127.0.0.1:9"
    for k in ("no_proxy", "NO_PROXY"):
        out.pop(k, None)
    return out
