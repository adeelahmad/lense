"""A small MQTT broker (the sensor hub) and the wire format it shares with the bridge client.

Devices on the network (routers, DNS servers, Zigbee and Tasmota gateways, Home Assistant) publish to it as they would
to Mosquitto, and anything can subscribe to it: it is an ordinary local broker for MQTT 3.1 and 3.1.1. Every message
published to it is also handed to Lens (sensors.py), which is what makes it a hub rather than only a broker.

What it does: CONNECT with a username and password (checked by the caller), PUBLISH at QoS 0, 1 and 2 (acknowledged
as the level asks), SUBSCRIBE and UNSUBSCRIBE with + and # wildcards, retained messages, last wills, keep-alive and
PING. What it doesn't (refine later): MQTT 5 (a client asking for it is told the protocol isn't supported, and most
fall back to 3.1.1), persistent sessions (every session is clean), and delivery above QoS 0 to subscribers.
"""

from __future__ import annotations

import contextlib
import secrets
import socket
import socketserver
import ssl
import struct
import threading
import time

CONNECT, CONNACK, PUBLISH, PUBACK, PUBREC, PUBREL, PUBCOMP = 1, 2, 3, 4, 5, 6, 7
SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK, PINGREQ, PINGRESP, DISCONNECT = 8, 9, 10, 11, 12, 13, 14
# CONNACK return codes
ACCEPTED, BAD_PROTOCOL, BAD_CLIENT_ID, BAD_LOGIN, NOT_AUTHORIZED = 0, 1, 2, 4, 5
MAX_RETAINED = 10_000


class ProtocolError(Exception):
    pass


# ---------- the wire ----------
def _varint(n):
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def packet(kind, flags=0, body=b""):
    return bytes([(kind << 4) | flags]) + _varint(len(body)) + body


def string(s):
    b = s.encode() if isinstance(s, str) else s
    return struct.pack("!H", len(b)) + b


def _exact(f, n):
    data = f.read(n)
    if data is None or len(data) < n:
        raise EOFError
    return data


def read_packet(f, max_bytes):
    """(kind, flags, body) of the next packet; EOFError when the other end has gone."""
    first = _exact(f, 1)[0]
    n, mult = 0, 1
    for _ in range(4):
        b = _exact(f, 1)[0]
        n += (b & 0x7F) * mult
        if not b & 0x80:
            break
        mult *= 128
    else:
        raise ProtocolError("malformed length")
    if n > max_bytes:
        raise ProtocolError(f"a packet of {n} bytes is over the limit")
    return first >> 4, first & 0x0F, _exact(f, n) if n else b""


def take_string(body, i):
    if i + 2 > len(body):
        raise ProtocolError("truncated")
    (n,) = struct.unpack_from("!H", body, i)
    if i + 2 + n > len(body):
        raise ProtocolError("truncated")
    return body[i + 2 : i + 2 + n], i + 2 + n


def publish_packet(topic, payload, qos=0, retain=False, pid=None):
    body = string(topic) + (struct.pack("!H", pid) if qos else b"") + payload
    return packet(PUBLISH, (qos << 1) | (1 if retain else 0), body)


def parse_publish(flags, body):
    """(topic, payload, qos, retain, packet id)."""
    qos = (flags >> 1) & 3
    if qos == 3:
        raise ProtocolError("QoS 3")
    raw, i = take_string(body, 0)
    topic = raw.decode("utf-8", "replace")
    pid = None
    if qos:
        if i + 2 > len(body):
            raise ProtocolError("truncated")
        (pid,) = struct.unpack_from("!H", body, i)
        i += 2
    return topic, body[i:], qos, bool(flags & 1), pid


def valid_filter(f):
    if not f:
        return False
    levels = f.split("/")
    for k, level in enumerate(levels):
        if "#" in level and (level != "#" or k != len(levels) - 1):
            return False
        if "+" in level and level != "+":
            return False
    return True


def matches(flt, topic):
    """Whether a topic filter (with + and #) takes a topic. Topics starting with $ aren't taken by a filter that
    starts with a wildcard."""
    if topic.startswith("$") and flt[:1] in ("+", "#"):
        return False
    f, t = flt.split("/"), topic.split("/")
    for k, level in enumerate(f):
        if level == "#":
            return True
        if k >= len(t):
            return False
        if level != "+" and level != t[k]:
            return False
    return len(f) == len(t)


# ---------- the broker ----------
class _Session:
    def __init__(self, client_id, sock, login):
        self.client_id, self.sock, self.login = client_id, sock, login
        self.subs: dict[str, int] = {}
        self.lock = threading.Lock()
        self.will = None
        self.closed = False

    def send(self, data):
        with self.lock:
            if self.closed:
                return
            try:
                self.sock.sendall(data)
            except OSError:
                self.closed = True

    def close(self):
        self.closed = True
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self.sock.close()


class Broker:
    """The hub's broker. `authenticate(username, password, client_id, address)` returns what the session runs as (any
    truthy value), or None to refuse; `on_message(topic, payload, login, address)` is told every message published to
    it (topics starting with $ aside), and should be quick."""

    def __init__(self, host, port, authenticate, on_message, max_bytes=256 * 1024, log=None, ssl_context=None):
        self.authenticate, self.on_message, self.max_bytes, self.log = authenticate, on_message, max_bytes, log or (lambda *a: None)
        self.sessions: dict[str, _Session] = {}
        self.retained: dict[str, bytes] = {}
        self.lock = threading.Lock()
        broker = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                sock = self.request
                if ssl_context is not None:
                    try:
                        sock = ssl_context.wrap_socket(sock, server_side=True)
                    except (ssl.SSLError, OSError):
                        return
                broker._serve(sock, self.client_address)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        if ":" in host:
            Server.address_family = socket.AF_INET6
        self.server = Server((host, port), Handler)
        self.port = self.server.server_address[1]
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name=f"mqtt-hub-{self.port}")
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for s in sessions:
            s.close()

    def clients(self):
        with self.lock:
            return len(self.sessions)

    def publish(self, topic, payload, retain=False, origin=None):
        """Send a message to every subscriber whose filter takes it (at QoS 0), and keep it when retained."""
        if retain:
            with self.lock:
                if payload:
                    if topic in self.retained or len(self.retained) < MAX_RETAINED:
                        self.retained[topic] = payload
                else:
                    self.retained.pop(topic, None)
        data = publish_packet(topic, payload)
        with self.lock:
            targets = [s for s in self.sessions.values() if any(matches(f, topic) for f in s.subs)]
        for s in targets:
            s.send(data)

    def _connect(self, f, sock, address):
        kind, _flags, body = read_packet(f, self.max_bytes)
        if kind != CONNECT:
            raise ProtocolError("the first packet must be CONNECT")
        name, i = take_string(body, 0)
        if name not in (b"MQTT", b"MQIsdp") or i + 4 > len(body):
            raise ProtocolError("not MQTT")
        level, cflags = body[i], body[i + 1]
        (keepalive,) = struct.unpack_from("!H", body, i + 2)
        i += 4
        if level not in (3, 4):
            sock.sendall(packet(CONNACK, 0, bytes([0, BAD_PROTOCOL])))
            raise ProtocolError(f"MQTT protocol level {level} isn't supported (only 3.1 and 3.1.1)")
        if cflags & 1:
            raise ProtocolError("reserved flag set")
        raw_id, i = take_string(body, i)
        client_id = raw_id.decode("utf-8", "replace")
        will = None
        if cflags & 0x04:
            wt, i = take_string(body, i)
            wm, i = take_string(body, i)
            will = (wt.decode("utf-8", "replace"), wm, bool(cflags & 0x20))
        user = pw = None
        if cflags & 0x80:
            u, i = take_string(body, i)
            user = u.decode("utf-8", "replace")
        if cflags & 0x40:
            p, i = take_string(body, i)
            pw = p.decode("utf-8", "replace")
        if not client_id:
            if not cflags & 0x02:
                sock.sendall(packet(CONNACK, 0, bytes([0, BAD_CLIENT_ID])))
                raise ProtocolError("an empty client id needs a clean session")
            client_id = "lens-" + secrets.token_hex(6)
        login = self.authenticate(user, pw, client_id, address)
        if not login:
            sock.sendall(packet(CONNACK, 0, bytes([0, BAD_LOGIN if user else NOT_AUTHORIZED])))
            raise ProtocolError(f"refused {user or 'an anonymous client'} from {address[0]}")
        session = _Session(client_id, sock, login)
        session.will = will
        with self.lock:
            old = self.sessions.get(client_id)
            self.sessions[client_id] = session
        if old:
            old.will = None  # taken over, not lost
            old.close()
        session.send(packet(CONNACK, 0, bytes([0, ACCEPTED])))
        return session, keepalive

    def _serve(self, sock, address):
        sock.settimeout(10)
        f = sock.makefile("rb")
        session = None
        try:
            session, keepalive = self._connect(f, sock, address)
            sock.settimeout(keepalive * 1.5 if keepalive else None)
            while True:
                kind, flags, body = read_packet(f, self.max_bytes)
                if kind == PUBLISH:
                    topic, payload, qos, retain, pid = parse_publish(flags, body)
                    if not topic or "+" in topic or "#" in topic:
                        raise ProtocolError("a topic name can't have wildcards")
                    if qos == 1:
                        session.send(packet(PUBACK, 0, struct.pack("!H", pid)))
                    elif qos == 2:
                        session.send(packet(PUBREC, 0, struct.pack("!H", pid)))
                    self._take(topic, payload, retain, session.login, address)
                elif kind == PUBREL:
                    session.send(packet(PUBCOMP, 0, body[:2]))
                elif kind == SUBSCRIBE:
                    self._subscribe(session, body)
                elif kind == UNSUBSCRIBE:
                    (pid,) = struct.unpack_from("!H", body, 0)
                    i = 2
                    while i < len(body):
                        flt, i = take_string(body, i)
                        with self.lock:  # publishers read every session's filters under it
                            session.subs.pop(flt.decode("utf-8", "replace"), None)
                    session.send(packet(UNSUBACK, 0, struct.pack("!H", pid)))
                elif kind == PINGREQ:
                    session.send(packet(PINGRESP))
                elif kind == DISCONNECT:
                    session.will = None
                    break
                elif kind in (PUBACK, PUBREC, PUBCOMP):
                    continue  # we only send QoS 0, so these don't come; a client sending them anyway is harmless
                else:
                    raise ProtocolError(f"unexpected packet {kind}")
        except (EOFError, ProtocolError, OSError, socket.timeout, struct.error) as e:
            if isinstance(e, ProtocolError):
                self.log(f"mqtt hub: {address[0]}: {e}")
        finally:
            if session:
                with self.lock:
                    if self.sessions.get(session.client_id) is session:
                        del self.sessions[session.client_id]
                if session.will:
                    self._take(session.will[0], session.will[1], session.will[2], session.login, address)
                session.close()
            else:
                with contextlib.suppress(OSError):
                    sock.close()

    def _take(self, topic, payload, retain, login, address):
        self.publish(topic, payload, retain)
        if not topic.startswith("$"):
            try:
                self.on_message(topic, payload, login, address)
            except Exception as e:  # noqa: BLE001 - a bad message mustn't drop the device
                self.log(f"mqtt hub: {topic}: {type(e).__name__}: {e}")

    def _subscribe(self, session, body):
        (pid,) = struct.unpack_from("!H", body, 0)
        i, codes, added = 2, [], []
        while i < len(body):
            raw, i = take_string(body, i)
            if i >= len(body):
                raise ProtocolError("truncated")
            i += 1  # the QoS asked for: we grant 0
            flt = raw.decode("utf-8", "replace")
            if valid_filter(flt):
                with self.lock:
                    session.subs[flt] = 0
                codes.append(0)
                added.append(flt)
            else:
                codes.append(0x80)
        session.send(packet(SUBACK, 0, struct.pack("!H", pid) + bytes(codes)))
        with self.lock:
            kept = list(self.retained.items())
        for topic, payload in kept:
            if any(matches(f, topic) for f in added):
                session.send(publish_packet(topic, payload, retain=True))


# ---------- a client, for bridges to another broker ----------
class Client:
    """A minimal MQTT 3.1.1 subscriber: connect, subscribe to filters and hand each message to `on_message(topic,
    payload)` until `stop` is set or the connection fails (raised as OSError or ProtocolError)."""

    def __init__(self, host, port, username=None, password=None, tls=False, client_id=None, keepalive=60, max_bytes=256 * 1024):
        self.host, self.port, self.username, self.password, self.tls = host, int(port), username, password, tls
        self.client_id = client_id or "lens-" + secrets.token_hex(6)
        self.keepalive, self.max_bytes = keepalive, max_bytes

    def run(self, filters, on_message, stop):
        sock = socket.create_connection((self.host, self.port), timeout=15)
        if self.tls:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        heard = [time.monotonic()]
        done = threading.Event()

        def pinger():  # keeps the connection alive, and closes it when told to stop or the broker goes quiet
            while not done.wait(1):
                if stop.is_set() or time.monotonic() - heard[0] > self.keepalive * 1.5:
                    break
                if time.monotonic() - heard[0] > self.keepalive / 2:
                    with contextlib.suppress(OSError):
                        sock.sendall(packet(PINGREQ))
            with contextlib.suppress(OSError):
                sock.sendall(packet(DISCONNECT))
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

        try:
            flags = 0x02 | (0x80 if self.username else 0) | (0x40 if self.password else 0)
            body = string("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive) + string(self.client_id)
            if self.username:
                body += string(self.username)
            if self.password:
                body += string(self.password)
            sock.sendall(packet(CONNECT, 0, body))
            f = sock.makefile("rb")
            kind, _, ack = read_packet(f, self.max_bytes)
            if kind != CONNACK or len(ack) < 2:
                raise ProtocolError("no CONNACK")
            if ack[1]:
                why = {BAD_PROTOCOL: "protocol refused", BAD_LOGIN: "bad username or password", NOT_AUTHORIZED: "not authorised"}
                raise ProtocolError(why.get(ack[1], f"refused ({ack[1]})"))
            sub = struct.pack("!H", 1) + b"".join(string(t) + b"\x00" for t in filters)
            sock.sendall(packet(SUBSCRIBE, 2, sub))
            sock.settimeout(None)
            threading.Thread(target=pinger, daemon=True, name="mqtt-bridge-ping").start()
            while not stop.is_set():
                kind, flags, body = read_packet(f, self.max_bytes)
                heard[0] = time.monotonic()
                if kind == PUBLISH:
                    topic, payload, qos, _retain, pid = parse_publish(flags, body)
                    if qos == 1:
                        sock.sendall(packet(PUBACK, 0, struct.pack("!H", pid)))
                    elif qos == 2:
                        sock.sendall(packet(PUBREC, 0, struct.pack("!H", pid)))
                    on_message(topic, payload)
                elif kind == PUBREL:
                    sock.sendall(packet(PUBCOMP, 0, body[:2]))
        except (EOFError, OSError) as e:
            if not stop.is_set():
                raise OSError(f"the broker closed the connection{'' if isinstance(e, EOFError) else f': {e}'}") from None
        finally:
            done.set()
            with contextlib.suppress(OSError):
                sock.close()
