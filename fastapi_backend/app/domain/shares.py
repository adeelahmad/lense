"""File shares (docs/file-tree.md): the servers that share the graph as files (filetree.py) on the network, with
people's own access keys (s3.py): S3 (s3.py) and WebDAV (webdav.py). Files people add through them are imported
(share_import.py) while `shares.add_files` is on.

Off until an admin turns them on (`shares.enabled`, then `s3` and `webdav`); the process that runs routines (`lens
worker`) follows the settings as they change, as the sensor hub does, and says what each server is doing in
`app_service:share-<protocol>`.
"""

from __future__ import annotations

import http.server
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from . import s3, store, webdav

R = store.R
CHECK_SECONDS = 5
RETRY_SECONDS = 30
# protocol -> (its handler, the setting naming its port)
PROTOCOLS: dict[str, tuple[type[http.server.BaseHTTPRequestHandler], str]] = {
    "s3": (s3.Handler, "s3_port"),
    "webdav": (webdav.Handler, "webdav_port"),
}


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
    """{protocol: {enabled, port, service, add_files}}: whether it's on, its port, what the process running it says,
    and whether files people add are imported."""
    s, out = settings_of(cfg), {}
    for proto, (_, port) in PROTOCOLS.items():
        row = db.one("SELECT * FROM $r", r=_row(proto)) or {}
        row.pop("id", None)
        out[proto] = {"enabled": bool(s.get("enabled") and s.get(proto)), "port": int(s[port]), "service": row or None,
                      "add_files": bool(s.get("add_files"))}  # fmt: skip
    return out
