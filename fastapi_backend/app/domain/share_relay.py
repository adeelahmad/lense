"""A TCP relay that lets in only the addresses a file share allows (docs/file-shares.md).

Some servers Lens runs for a file share, such as `rclone serve nfs`, have no list of who may connect. The relay listens
on the published port, closes every connection from outside `networks` at once, and copies the rest to the server,
which listens on 127.0.0.1 only.
"""

from __future__ import annotations

import ipaddress
import selectors
import socket
import socketserver
import threading

CHUNK = 64 * 1024


def networks(values):
    """The allowed ranges, as ip_network objects (bad ones are refused when the setting is saved, and skipped here)."""
    out = []
    for v in values or ():
        try:
            out.append(ipaddress.ip_network(str(v).strip(), strict=False))
        except ValueError:
            continue
    return out


def allowed(address, nets):
    """True when `address` (an IP string, IPv4-mapped IPv6 too) is in one of `nets`."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip.version == n.version and ip in n for n in nets)


class Relay:
    """Listens on (host, port) and relays allowed clients to 127.0.0.1:target. `nets` can be changed while it runs."""

    def __init__(self, host, port, target, nets, log=None):
        self.host, self.port, self.target, self.nets = host, port, target, list(nets)
        self.log = log or (lambda *a: None)
        self.refused = 0
        relay = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                relay._serve(self.request, self.client_address[0])

        family = socket.AF_INET6 if ":" in host else socket.AF_INET

        class Server(socketserver.ThreadingTCPServer):
            address_family = family
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server((host, port), Handler)
        self.port = self.server.server_address[1]
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name=f"share-relay-{self.port}")
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def _serve(self, client, address):
        if not allowed(address, self.nets):
            self.refused += 1
            self.log(f"file share: refused {address} on port {self.port} (not in the allowed networks)")
            client.close()
            return
        try:
            upstream = socket.create_connection(("127.0.0.1", self.target), timeout=10)
        except OSError as e:
            self.log(f"file share: the server on port {self.target} isn't answering: {e}")
            client.close()
            return
        upstream.settimeout(None)
        client.settimeout(None)
        sel = selectors.DefaultSelector()
        sel.register(client, selectors.EVENT_READ, upstream)
        sel.register(upstream, selectors.EVENT_READ, client)
        try:
            while True:
                for key, _ in sel.select():
                    data = key.fileobj.recv(CHUNK)
                    if not data:
                        return
                    key.data.sendall(data)
        except OSError:
            pass
        finally:
            sel.close()
            for s in (client, upstream):
                try:
                    s.close()
                except OSError:
                    pass
