"""A local HTTP proxy for the browser Lens prints pages with, so that what the browser may reach is Lens's to decide.

Headless Chromium is started with this proxy as its only way out (loopback included). The proxy answers requests for
the pages Lens hands it itself (a document made into HTML, at http://document.lens/) and refuses everything else, so
a document can't make the browser fetch anything: not the internet, not the machine's own services, not the cloud's
metadata address. The page is also served with a Content-Security-Policy that allows no scripts and nothing from
outside it but data: images and fonts. LibreOffice is pointed at a proxy address that isn't there, for the same end.
"""

from __future__ import annotations

import socketserver
import threading

DOCUMENT_URL = "http://document.lens/"
# what a page Lens serves may use: its own inline styles, and images and fonts written into it; no scripts, frames,
# forms or anything fetched from elsewhere
CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'"
MAX_HEAD = 64 * 1024  # the most of a request's head that is read


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
        if page is None:
            self.server.refused.append(f"{method} {target}"[:300])
            self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
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


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    pages: dict[str, tuple[bytes, str]]
    refused: list[str]


class Guard:
    """The proxy, running while it's open:

    with Guard({DOCUMENT_URL: (html_bytes, "text/html; charset=utf-8")}) as g:
        run chromium with g.args() ... DOCUMENT_URL
    g.refused  # what the page asked for and didn't get
    """

    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.refused: list[str] = []
        self._server: _Server | None = None

    def __enter__(self):
        srv = _Server(("127.0.0.1", 0), _Handler)
        srv.pages, srv.refused = self.pages, self.refused
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


def nowhere_env(env):
    """Environment variables that send a program's web requests to a proxy address nothing listens on (port 9 of
    loopback), so what it would fetch fails at once."""
    out = dict(env)
    for k in ("http_proxy", "https_proxy", "ftp_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "ALL_PROXY"):
        out[k] = "http://127.0.0.1:9"
    for k in ("no_proxy", "NO_PROXY"):
        out.pop(k, None)
    return out
