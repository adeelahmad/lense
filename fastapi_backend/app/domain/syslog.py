"""Syslog, as routers, DNS servers (Pi-hole, dnsmasq, Unbound), NAS boxes and firewalls send it: a listener on UDP and
TCP, and a parser for both formats in use (RFC 5424 and the older BSD one, RFC 3164, in the shapes devices actually
send). Senders are taken only from the networks the caller allows; everything else is dropped unread.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import ipaddress
import re
import socket
import socketserver
import threading

SEVERITIES = ("emergency", "alert", "critical", "error", "warning", "notice", "info", "debug")
MAX_LINE = 64 * 1024
RFC5424 = re.compile(r"^<(\d{1,3})>1 (\S+) (\S+) (\S+) (\S+) (\S+) (-|(?:\[(?:[^\]\\]|\\.)*\])+)(?: (.*))?$", re.S)
PRI = re.compile(r"^<(\d{1,3})>(.*)$", re.S)
BSD_TIME = re.compile(r"^([A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d)(?:\.\d+)? (.*)$", re.S)
ISO_TIME = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?) (.*)$", re.S)
HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,253}$")
TAG = re.compile(r"^([^\s:\[]{1,64})(?:\[([^\]]*)\])?:(?:\s|$)(.*)$", re.S)


def _nil(v):
    return None if v in (None, "-") else v


def _iso(text):
    try:
        t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)


def _bsd(text, now):
    try:
        t = dt.datetime.strptime(f"{now.year} {' '.join(text.split())}", "%Y %b %d %H:%M:%S")
    except ValueError:
        return None
    t = t.replace(tzinfo=dt.timezone.utc)  # BSD syslog has no zone; most devices send UTC or close enough
    if t - now > dt.timedelta(days=2):  # December's line read in January
        t = t.replace(year=now.year - 1)
    return t


def parse(data, now=None):
    """One message: {facility, level, at, host, app, pid, message}. `at` is when the device says it happened (a
    datetime), or None; `level` is 0 (emergency) to 7 (debug)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    text = (data.decode("utf-8", "replace") if isinstance(data, bytes) else data).strip("\x00\r\n ").lstrip("\ufeff")
    out = {"facility": None, "level": None, "at": None, "host": None, "app": None, "pid": None, "message": text}
    m = RFC5424.match(text)
    if m:
        pri = int(m.group(1))
        out.update(
            facility=pri >> 3,
            level=pri & 7,
            at=_iso(m.group(2)) if m.group(2) != "-" else None,
            host=_nil(m.group(3)),
            app=_nil(m.group(4)),
            pid=_nil(m.group(5)),
            message=(m.group(8) or "").lstrip("\ufeff"),
        )
        return out
    m = PRI.match(text)
    if m:
        pri, text = int(m.group(1)), m.group(2)
        out.update(facility=pri >> 3, level=pri & 7)
    for rx, read in ((BSD_TIME, lambda s: _bsd(s, now)), (ISO_TIME, _iso)):
        t = rx.match(text)
        if t:
            out["at"], text = read(t.group(1)), t.group(2)
            break
    # what follows is "tag[pid]: message", "host tag[pid]: message", "host message" or just the message
    tag = TAG.match(text)
    if not tag:
        first, _, rest = text.partition(" ")
        tag = TAG.match(rest) if HOST.match(first) else None
        if tag:
            out["host"] = first
        elif out["at"] and rest and HOST.match(first):
            out["host"], text = first, rest
    if tag:
        out.update(app=tag.group(1), pid=tag.group(2) or None, message=tag.group(3))
    else:
        out["message"] = text
    return out


def allowed(address, networks):
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return any(ip in ipaddress.ip_network(n, strict=False) for n in networks)


class Listener:
    """UDP and TCP syslog on one port. `on_message(parsed, address)` is told each message from an allowed sender."""

    def __init__(self, host, port, networks, on_message, log=None, tcp=True):
        self.networks, self.on_message, self.log = list(networks), on_message, log or (lambda *a: None)
        me = self

        class Udp(socketserver.BaseRequestHandler):
            def handle(self):
                me._take(self.request[0], self.client_address[0])

        class Tcp(socketserver.StreamRequestHandler):
            def handle(self):
                if not allowed(self.client_address[0], me.networks):
                    return
                self.request.settimeout(300)
                with contextlib.suppress(OSError, socket.timeout):
                    for frame in _frames(self.rfile):
                        me._take(frame, self.client_address[0])

        class UdpServer(socketserver.ThreadingUDPServer):
            daemon_threads = True
            allow_reuse_address = True
            max_packet_size = MAX_LINE

        class TcpServer(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        if ":" in host:
            UdpServer.address_family = TcpServer.address_family = socket.AF_INET6
        self.udp = UdpServer((host, port), Udp)
        self.port = self.udp.server_address[1]
        try:
            self.tcp = TcpServer((host, self.port), Tcp) if tcp else None
        except OSError:
            self.udp.server_close()
            raise
        self.threads = []

    def start(self):
        for s in filter(None, (self.udp, self.tcp)):
            th = threading.Thread(target=s.serve_forever, daemon=True, name=f"syslog-{self.port}")
            th.start()
            self.threads.append(th)
        return self

    def stop(self):
        for s in filter(None, (self.udp, self.tcp)):
            s.shutdown()
            s.server_close()

    def _take(self, data, address):
        if not allowed(address, self.networks):
            return
        try:
            self.on_message(parse(data), address)
        except Exception as e:  # noqa: BLE001 - one bad line mustn't stop the listener
            self.log(f"syslog: {address}: {type(e).__name__}: {e}")


def _frames(f):
    """Messages on a TCP stream: octet-counted ("123 <34>1 ...", RFC 6587), or one per line."""
    while True:
        first = f.read(1)
        if not first:
            return
        if first in b"\r\n ":
            continue
        if first.isdigit():  # a count, a space, then exactly that many bytes
            digits, c = first, b""
            for _ in range(7):
                c = f.read(1)
                if not c:
                    return
                if not c.isdigit():
                    break
                digits, c = digits + c, b""
            if c == b" " and int(digits) <= MAX_LINE:
                frame = f.read(int(digits))
                if len(frame) < int(digits):
                    return
                yield frame
                continue
            line = digits + c + (f.readline(MAX_LINE) if c and c not in b"\r\n" else b"")  # not a count after all
        else:
            line = first + f.readline(MAX_LINE)
        if line.strip():
            yield line
