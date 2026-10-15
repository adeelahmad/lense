"""File shares (docs/file-tree.md): the servers that share the graph as files (filetree.py) on the network, read-only, with
people's own access keys (s3.py): S3 (s3.py) and WebDAV (webdav.py).

Off until an admin turns them on (`shares.enabled`, then `s3` and `webdav`); the process that runs routines (`lens
worker`) follows the settings as they change, as the sensor hub does, and says what each server is doing in
`app_service:share-<protocol>`.

The web app also relays them at its own address, /s3 and /dav (nextjs-frontend/lens-relay.js), so they can be reached
wherever the web app is, Settings › Remote access included. Those requests keep their path and carry X-Forwarded-Prefix,
and the servers turn them away until an admin turns on `shares.remote`.
"""

from __future__ import annotations

import http.server
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from . import ipgroups, s3, store, webdav

R = store.R
CHECK_SECONDS = 5
RETRY_SECONDS = 30
# protocol -> (its handler, the setting naming its port)
PROTOCOLS: dict[str, tuple[type[http.server.BaseHTTPRequestHandler], str]] = {
    "s3": (s3.Handler, "s3_port"),
    "webdav": (webdav.Handler, "webdav_port"),
}


# protocol -> the path the web app relays it at (nextjs-frontend/lens-relay.js)
WEB_PATHS = {"s3": "/s3", "webdav": "/dav"}


def _row(proto):
    return R("app_service", f"share-{proto}")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, bind, port, db, cfg_fn: Callable[[], dict[str, Any]], log=None, handler=s3.Handler):
        self.db, self.cfg_fn, self.log = db, cfg_fn, log or (lambda *a: None)
        if ":" in bind:
            self.address_family = socket.AF_INET6
        super().__init__((bind, port), handler)
        self.port = self.server_address[1]

    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True, name="share-server").start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()


def settings_of(cfg):
    return {**store.DEFAULTS["shares"], **(cfg.get("shares") or {})}


def web_path(headers, proto):
    """The path the web app relays this protocol at, when the request came that way (its X-Forwarded-Prefix), else "".
    Anyone on the network could send the header too; it only gets their request turned away, or signed and linked
    under that path."""
    prefix = (headers.get("x-forwarded-prefix") or "").strip()
    return prefix if prefix == WEB_PATHS[proto] else ""


def share_path(path, prefix):
    """The path a request asks for within the share: without the web app's prefix (the web app relays the path as the
    client sent it, which is also the path S3 clients sign)."""
    if prefix and (path == prefix or path.startswith(prefix + "/")):
        return path[len(prefix) :] or "/"
    return path


def remote(cfg):
    """Whether the shares may be reached at the web app's address (shares.remote)."""
    return bool(settings_of(cfg).get("remote"))


def visitor(handler, cfg):
    """The address a request comes from, for throttles (ipgroups.visitor)."""
    peer, port = handler.client_address[0], handler.client_address[1]
    return ipgroups.visitor(cfg, peer, port, handler.headers.get("x-forwarded-for", ""))


def wanted(cfg):
    """{protocol: (bind, port) it should listen on, or None while it's off}."""
    s = settings_of(cfg)
    return {
        proto: (s.get("bind") or "0.0.0.0", int(s[port])) if s.get("enabled") and s.get(proto) else None
        for proto, (_, port) in PROTOCOLS.items()
    }


class Service:
    """Starts, stops or moves each server as the settings change, and says what it's doing."""

    def __init__(self, db, cfg_fn, log=None, name=None):
        self.db, self.cfg_fn, self.log = db, cfg_fn, log or (lambda *a: None)
        self.name = name or f"{socket.gethostname()}-{id(self) % 10000}"
        self.servers: dict[str, Server] = {}
        self.state: dict[str, dict[str, Any]] = {p: {} for p in PROTOCOLS}
        self.retry = dict.fromkeys(PROTOCOLS, 0.0)

    def apply(self):
        for proto, want in wanted(self.cfg_fn()).items():
            st = self.state[proto]
            if want == st.get("want") and (proto in self.servers or want is None or time.monotonic() < self.retry[proto]):
                continue
            self._stop(proto)
            if want is None:
                self.state[proto] = {}
                self.db.q("DELETE $r", r=_row(proto))
                continue
            try:
                srv = Server(want[0], want[1], self.db, self.cfg_fn, self.log, PROTOCOLS[proto][0]).start()
                self.servers[proto] = srv
                self.state[proto] = {"want": want, "running": True, "port": srv.port, "since": store.now()}
                self.log(f"shares: {proto} on port {srv.port}")
            except OSError as e:
                self.state[proto] = {"want": want, "running": False, "error": f"port {want[1]}: {e.strerror or e}"}
                self.retry[proto] = time.monotonic() + RETRY_SECONDS
            out = {k: v for k, v in self.state[proto].items() if k != "want"}
            self.db.q("UPSERT $r CONTENT $d", r=_row(proto), d=store.clean({"process": self.name, "at": store.now(), **out}))

    def _stop(self, proto):
        srv = self.servers.pop(proto, None)
        if srv is not None:
            try:
                srv.stop()
            except Exception:  # noqa: BLE001
                pass

    def close(self):
        for proto in list(self.servers):
            self._stop(proto)

    def loop(self, stop):
        while True:
            try:
                self.apply()
            except Exception as e:  # noqa: BLE001 - keep following the settings
                self.log(f"shares: {type(e).__name__}: {e}")
            if stop.wait(CHECK_SECONDS):
                break
        self.close()


def start(db, cfg_fn, stop, log=None, name=None):
    """The thread that runs the servers that are turned on, until `stop` is set."""
    svc = Service(db, cfg_fn, log, name)
    threading.Thread(target=svc.loop, args=(stop,), daemon=True, name="shares").start()
    return svc


def status(db, cfg):
    """{protocol: {enabled, port, service}}: whether it's on, its port, and what the process running it says."""
    s, out = settings_of(cfg), {}
    for proto, (_, port) in PROTOCOLS.items():
        row = db.one("SELECT * FROM $r", r=_row(proto)) or {}
        row.pop("id", None)
        out[proto] = {"enabled": bool(s.get("enabled") and s.get(proto)), "port": int(s[port]), "service": row or None}
    return out
