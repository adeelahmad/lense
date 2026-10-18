"""Logging: one structured record for every line the API and each worker write, kept on this machine, shown in the web
app, and sent to a syslog server only when an admin turns that on (docs/logging.md).

Every process (the API, each `lens worker`, `lens watch`) logs through Python's logging with three outputs:

* the console (docker logs), as text or as one JSON object per line (logs.format);
* a file per process under data_dir/logs, one JSON object per line, rotated at logs.file_mb and kept logs.file_keep
  times; the web app's log viewer reads these (`read`), so it shows the workers' lines too;
* a syslog server (RFC 5424 over UDP, TCP or TLS), off by default: lines go through a queue to a background thread, so
  a slow or missing server never holds up a request; when the queue is full new lines are dropped and counted;
* an HTTP endpoint (Grafana Loki's push API, or JSON for Vector, Fluent Bit, Logstash and the like), off by default,
  sent in batches from a background thread the same way.

Before any line leaves the process it is redacted: bearer and basic credentials, key=value pairs naming a key, token,
secret or password, the password in a URL, and well-known key shapes (sk-…, ghp_…, xox…-, JWTs) become ***.

Settings are re-read when someone saves them (settings.Settings.current calls `apply`), so a change applies to the API
and every worker without a restart. LENS_LOG_LEVEL and LENS_LOG_FORMAT in .env win over the app.
"""

from __future__ import annotations

import contextlib
import datetime
import ipaddress
import json
import logging
import logging.handlers
import os
import pathlib
import queue
import re
import socket
import ssl
import sys
import tempfile
import threading
import time
import urllib.parse

from . import __version__

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR, "critical": logging.CRITICAL}
NAMES = {v: k for k, v in LEVELS.items()}
# RFC 5424 severities
SEVERITY = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4, logging.ERROR: 3, logging.CRITICAL: 2}
FACILITIES = {"user": 1, "daemon": 3, "auth": 4, "syslog": 5, **{f"local{i}": 16 + i for i in range(8)}}
TRANSPORTS = ("udp", "tcp", "tls")
HTTP_KINDS = ("loki", "json", "ndjson")
LOKI_PUSH = "/loki/api/v1/push"
BATCH = 500  # the most lines in one HTTP request
BATCH_SECONDS = 2.0  # how long a line waits for others to share its request
MAX_BYTES = 8192  # the most of one line sent to syslog (rsyslog's default maxMessageSize)
QUEUE = 10_000  # lines waiting for the syslog server before new ones are dropped
PRUNE_DAYS = 14  # a log file no process has written for this long (an old container's) is removed
LOGGER_RX = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_STD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "lens"}

# what is never written out: the match keeps its first group and the rest becomes ***
_SECRETS = [
    re.compile(r"(?i)\b((?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(
        r"(?i)\b((?:api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|token|secret|"
        r"client[_-]?secret|password|passwd|pwd|authorization|cookie|set-cookie|x-api-key)[\"']?\s*[:=]\s*[\"']?)"
        r"(?!\*\*\*)[^\s\"'&,;}]+"
    ),
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^/\s@]+(?=@)"),
    re.compile(r"()\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,})"),
    re.compile(r"()\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
]
_SECRET_KEY = re.compile(r"(?i)(key|token|secret|password|passwd|authorization|cookie)")


def redact(text):
    """The text with credentials replaced by ***."""
    if not text:
        return text
    for rx in _SECRETS:
        text = rx.sub(lambda m: m.group(1) + "***", text)
    return text


_role = ["api"]
_lock = threading.Lock()
_state = {"key": None, "cfg": None, "console": None, "file": None, "syslog": None, "http": None, "levels": {}}
_host = socket.gethostname()


def _clean(value, limit=4000):
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return redact(str(value))[:limit]


def record(r):
    """A log record as the dict written to the file and sent as JSON: redacted once, then shared by every output."""
    got = getattr(r, "lens", None)
    if got is not None:
        return got
    try:
        msg = r.getMessage()
    except Exception:  # noqa: BLE001 - a bad format string still logs something
        msg = f"{r.msg} {r.args}"
    out = {
        "ts": datetime.datetime.fromtimestamp(r.created, datetime.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": NAMES.get(r.levelno, r.levelname.lower()),
        "logger": r.name,
        "msg": redact(msg),
        "process": _role[0],
        "pid": r.process,
        "host": _host,
    }
    if r.exc_info and r.exc_info[0] is not None:
        out["exc"] = redact(logging.Formatter().formatException(r.exc_info))
    elif r.exc_text:
        out["exc"] = redact(r.exc_text)
    fields = {k: ("***" if _SECRET_KEY.search(k) else _clean(v)) for k, v in r.__dict__.items() if k not in _STD and not k.startswith("_")}
    if fields:
        out["fields"] = fields
    r.lens = out
    return out


class JsonFormatter(logging.Formatter):
    def format(self, r):
        return json.dumps(record(r), ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, r):
        d = record(r)
        line = f"{d['ts']} {d['level'].upper()} {d['process']} {d['logger']}: {d['msg']}"
        if d.get("fields"):
            line += " " + " ".join(f"{k}={v}" for k, v in d["fields"].items())
        return line + ("\n" + d["exc"] if d.get("exc") else "")


_gate = {"level": logging.INFO, "levels": {}}


def _override(name):
    """The level an admin gave this logger or the nearest of its parents (logs.levels), or None."""
    levels = _gate["levels"]
    while name:
        if name in levels:
            return levels[name]
        name = name.rpartition(".")[0]
    return None


class Gate(logging.Filter):
    """What an output keeps: the logger's own level when it has one, else logs.level (the console and the file); for
    syslog, syslog_level too."""

    def __init__(self, floor=None):
        super().__init__()
        self.floor = floor

    def filter(self, r):
        own = _override(r.name)
        if self.floor is not None:
            return r.levelno >= self.floor and (own is None or r.levelno >= own)
        return r.levelno >= (own if own is not None else _gate["level"])


def _ours(h):
    return getattr(h, "_lens", False)


def _tag(h):
    h._lens = True
    return h


def set_role(role):
    """What this process is (api, worker, watcher): each record says it, and it names the process's log file."""
    _role[0] = role


def _env_level():
    return LEVELS.get((os.environ.get("LENS_LOG_LEVEL") or "").strip().lower(), logging.INFO)


def _env_format():
    f = (os.environ.get("LENS_LOG_FORMAT") or "").strip().lower()
    return f if f in ("text", "json") else "text"


def setup(role=None):
    """Log to the console now, before the settings are read (called once per process as it starts)."""
    again = bool(role) and role != _role[0] and _state["cfg"] is not None
    if role:
        set_role(role)
    root = logging.getLogger()
    with _lock:
        if _state["console"] is None:
            h = _tag(logging.StreamHandler(sys.stderr))
            h.addFilter(Gate())
            root.addHandler(h)
            _state["console"] = h
        _state["console"].setFormatter(JsonFormatter() if _env_format() == "json" else TextFormatter())
        if _state["key"] is None:
            _gate["level"] = _env_level()
            root.setLevel(_env_level())
        # uvicorn's own lines (the access log, startup and errors) go through these outputs too, in the same form
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            lg.handlers = [h for h in lg.handlers if _ours(h)]
            lg.propagate = True
    if again:  # the settings were read before this process knew what it is: its file is named for it now
        _close("file")
        _state["key"] = None
        apply(_state["cfg"])


def printer(name="lens"):
    """A `log=print` replacement for the long-running loops (the worker, the watcher): their lines become records."""
    lg = logging.getLogger(name)

    def log(*args, **_):
        lg.info(" ".join(str(a) for a in args))

    return log


def section(cfg):
    return (cfg or {}).get("logs") or {}


def level(name, default=logging.INFO):
    return LEVELS.get(str(name or "").lower(), default)


def _wanted(cfg):
    s = section(cfg)
    data_dir = (cfg or {}).get("data_dir")
    ship = None
    if s.get("syslog") and s.get("syslog_host"):
        ship = (
            str(s["syslog_host"]),
            int(s.get("syslog_port") or 514),
            s.get("syslog_transport") if s.get("syslog_transport") in TRANSPORTS else "udp",
            level(s.get("syslog_level")),
            FACILITIES.get(s.get("syslog_facility"), FACILITIES["local0"]),
            s.get("syslog_format") if s.get("syslog_format") in ("text", "json") else "text",
            s.get("syslog_framing") if s.get("syslog_framing") in ("octet", "newline") else "octet",
            s.get("syslog_verify") is not False,
            s.get("syslog_app_name") or "lens",
            *_certs(s),
        )
    http = None
    if s.get("http") and s.get("http_url"):
        http = (
            push_url(str(s["http_url"]), _kind(s)),
            _kind(s),
            level(s.get("http_level")),
            s.get("http_headers") if isinstance(s.get("http_headers"), str) else None,
            s.get("http_verify") is not False,
            *_certs(s),
        )
    levels = s.get("levels") if isinstance(s.get("levels"), dict) else {}
    return (
        level(s.get("level")),
        s.get("format") if s.get("format") in ("text", "json") else "text",
        tuple(sorted((k, level(v)) for k, v in levels.items() if isinstance(k, str) and LOGGER_RX.match(k))),
        (str(pathlib.Path(data_dir) / "logs"), int(s.get("file_mb") or 5), int(s.get("file_keep") or 2))
        if data_dir and s.get("file", True)
        else None,
        ship,
        http,
    )


def _certs(s):
    """(client certificate and key, server CA) as PEM text, or None each: for syslog over TLS and https endpoints."""
    out = []
    for k in ("client_cert", "server_ca"):
        v = s.get(k) if isinstance(s.get(k), str) and s.get(k) else None
        blocks = PEM_RX.findall(v) if v else []
        out.append(_rebuild(blocks) if blocks else v)  # one read from a password manager may have lost its line breaks
    return tuple(out)


def file_name(role=None):
    host = re.sub(r"[^A-Za-z0-9_.-]", "-", _host)[:64] or "host"
    return f"{role or _role[0]}-{host}.jsonl"


def apply(cfg):
    """Make this process's logging match the settings. Cheap when nothing changed."""
    want = _wanted(cfg)
    _state["cfg"] = cfg
    if want == _state["key"]:
        return
    with _lock:
        if want == _state["key"]:
            return
        lvl, fmt, levels, file, ship, http = want
        root = logging.getLogger()
        if _state["console"] is None:
            _state["console"] = _tag(logging.StreamHandler(sys.stderr))
            _state["console"].addFilter(Gate())
            root.addHandler(_state["console"])
        _state["console"].setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
        _gate["level"], _gate["levels"] = lvl, dict(levels)
        old = _state["key"]
        if file != (old[3] if old else None):
            _close("file")
            if file is not None:
                folder, mb, keep = file
                try:
                    pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
                    _prune(folder)
                    h = _tag(
                        logging.handlers.RotatingFileHandler(
                            pathlib.Path(folder) / file_name(), maxBytes=mb * 1024 * 1024, backupCount=keep, encoding="utf-8"
                        )
                    )
                    h.setFormatter(JsonFormatter())
                    h.addFilter(Gate())
                    root.addHandler(h)
                    _state["file"] = h
                except OSError as e:
                    sys.stderr.write(f"logs: can't write to {folder} ({e}); the log viewer won't show this process\n")
        if ship != (old[4] if old else None):
            _close("syslog")
            if ship is not None:
                h = _tag(SyslogHandler(*ship))
                root.addHandler(h)
                _state["syslog"] = h
        if http != (old[5] if old else None):
            _close("http")
            if http is not None:
                h = _tag(HttpHandler(*http))
                root.addHandler(h)
                _state["http"] = h
        for name in set(_state["levels"]) - {k for k, _ in levels}:
            logging.getLogger(name).setLevel(logging.NOTSET)  # an override taken away: back to the root's level
        for name, v in levels:
            logging.getLogger(name).setLevel(v)
        _state["levels"] = dict(levels)
        lowest = min([lvl, *(v for _, v in levels), *([ship[3]] if ship else []), *([http[2]] if http else [])])
        root.setLevel(lowest)
        _state["key"] = want


def _close(which):
    h = _state[which]
    _state[which] = None
    if h is not None:
        logging.getLogger().removeHandler(h)
        with contextlib.suppress(Exception):
            h.close()


def shutdown():
    """Stop the syslog and HTTP threads and close the file (tests; atexit does it otherwise)."""
    with _lock:
        _close("syslog")
        _close("http")
        _close("file")
        _state["key"] = None


def _prune(folder):
    cutoff = time.time() - PRUNE_DAYS * 86400
    for p in pathlib.Path(folder).glob("*.jsonl*"):
        with contextlib.suppress(OSError):
            if p.stat().st_mtime < cutoff:
                p.unlink()


# --- syslog (RFC 5424, sent over UDP per RFC 5426, TCP per RFC 6587 or TLS per RFC 5425) ---


def _printable(s, n):
    s = "".join(c if 33 <= ord(c) <= 126 else "_" for c in str(s))
    return s[:n] or "-"


def rfc5424(r, facility, fmt, app_name, host=None):
    """One record as an RFC 5424 message (bytes, without framing)."""
    d = record(r)
    pri = facility * 8 + SEVERITY.get(r.levelno, 6)
    head = " ".join(
        [
            f"<{pri}>1",
            d["ts"],
            _printable(host or _host, 255),
            _printable(app_name, 48),
            _printable(f"{d['process']}.{d['pid']}", 128),
            _printable(d["logger"], 32),
            "-",
        ]
    )
    if fmt == "json":
        body = json.dumps(d, ensure_ascii=False, default=str)
    else:
        body = d["msg"] + ("\n" + d["exc"] if d.get("exc") else "")
    return (head + " " + body).encode("utf-8", "replace")[:MAX_BYTES]


def tls_context(verify=True, client_cert=None, server_ca=None):
    """An ssl context for a log server: its certificate checked against the system's CAs, or `server_ca` (PEM) when
    given, unless verify is off; and this machine's own certificate and key (`client_cert`, PEM, for servers that ask
    for one: mutual TLS). ValueError when a PEM doesn't load."""
    ctx = ssl.create_default_context()
    if server_ca:
        try:
            ctx = ssl.create_default_context(cadata=server_ca)
        except (ssl.SSLError, ValueError) as e:
            raise ValueError(f"the log server's CA certificate doesn't load: {e}") from None
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if client_cert:
        # ssl loads a certificate and key only from a file: one only this process can read, gone once loaded
        fd, path = tempfile.mkstemp(prefix="lens-cert-", suffix=".pem")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(client_cert)
            ctx.load_cert_chain(path)
        except (ssl.SSLError, OSError) as e:
            raise ValueError(f"the client certificate doesn't load (a PEM certificate and its unencrypted key): {e}") from None
        finally:
            with contextlib.suppress(OSError):
                os.unlink(path)
    return ctx


PEM_RX = re.compile(r"-----BEGIN ([A-Z0-9 ]{1,40})-----(.*?)-----END \1-----", re.S)


def _rebuild(blocks):
    out = []
    for kind, body in blocks:
        b64 = "".join(body.split())
        out.append(f"-----BEGIN {kind}-----\n" + "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64)) + f"\n-----END {kind}-----\n")
    return "".join(out)


def pem(text, key=False):
    """`text` as PEM that ssl takes, its blocks rebuilt (a password field pasted into loses the line breaks); a
    certificate, and with `key` its private key too. ValueError otherwise."""
    want = "a PEM certificate" + (" and its private key" if key else "")
    blocks = PEM_RX.findall(text) if isinstance(text, str) and len(text) <= 64_000 else []
    if not blocks:
        raise ValueError(f"paste {want}, from -----BEGIN to -----END")
    text = _rebuild(blocks)
    if key:
        if not any("PRIVATE KEY" in k for k, _ in blocks):
            raise ValueError(f"paste {want}: the key (-----BEGIN PRIVATE KEY-----, unencrypted) is missing")
        tls_context(client_cert=text)
    else:
        tls_context(server_ca=text)
    return text


class Sender:
    """A connection to a syslog server; `send` raises OSError when the line couldn't go."""

    def __init__(self, host, port, transport, framing="octet", verify=True, timeout=5.0, client_cert=None, server_ca=None):
        self.host, self.port, self.transport, self.framing, self.verify, self.timeout = host, port, transport, framing, verify, timeout
        self.client_cert, self.server_ca = client_cert, server_ca
        self.sock = None

    def _connect(self):
        infos = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_DGRAM if self.transport == "udp" else socket.SOCK_STREAM)
        err = None
        for family, kind, proto, _, addr in infos:
            if ipaddress.ip_address(addr[0].split("%")[0]).is_link_local:
                err = OSError(f"{self.host} is a link-local address; Lens doesn't send logs there")
                continue
            s = socket.socket(family, kind, proto)
            s.settimeout(self.timeout)
            try:
                s.connect(addr)
                if self.transport == "tls":
                    try:
                        ctx = tls_context(self.verify, self.client_cert, self.server_ca)
                    except ValueError as e:
                        raise OSError(str(e)) from None
                    s = ctx.wrap_socket(s, server_hostname=self.host)
                return s
            except OSError as e:
                err = e
                s.close()
        raise err or OSError(f"couldn't find {self.host}")

    def send(self, msg):
        if self.sock is None:
            self.sock = self._connect()
        try:
            if self.transport == "udp":
                self.sock.send(msg)
            elif self.framing == "newline":
                self.sock.sendall(msg.replace(b"\n", b" ") + b"\n")
            else:
                self.sock.sendall(str(len(msg)).encode() + b" " + msg)
        except OSError:
            self.close()
            raise

    def close(self):
        if self.sock is not None:
            with contextlib.suppress(OSError):
                self.sock.close()
            self.sock = None


class SyslogHandler(logging.Handler):
    """Queues each record and sends it from a background thread; never blocks the caller."""

    def __init__(self, host, port, transport, lvl, facility, fmt, framing, verify, app_name, client_cert=None, server_ca=None):
        super().__init__()
        self.addFilter(Gate(lvl))
        self.facility, self.fmt, self.app_name = facility, fmt, app_name
        self.target = f"{transport}://{host}:{port}"
        self.sender = Sender(host, port, transport, framing, verify, client_cert=client_cert, server_ca=server_ca)
        self.q = queue.Queue(QUEUE)
        self.stats = {"sent": 0, "dropped": 0, "last_sent": None, "last_error": None, "error_at": None}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="lens-syslog", daemon=True)
        self._thread.start()

    def emit(self, r):
        try:
            self.q.put_nowait(rfc5424(r, self.facility, self.fmt, self.app_name))
        except queue.Full:
            self.stats["dropped"] += 1
        except Exception:  # noqa: BLE001 - logging never raises into the caller
            self.handleError(r)

    def _run(self):
        wait = 1.0
        while not self._stop.is_set():
            try:
                msg = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            if msg is None:
                break
            while not self._stop.is_set():
                try:
                    self.sender.send(msg)
                    self.stats["sent"] += 1
                    self.stats["last_sent"] = time.time()
                    wait = 1.0
                    break
                except OSError as e:
                    # never logged (that would come back here): the status in Settings → Logs shows it
                    self.stats["last_error"] = f"{type(e).__name__}: {e}"[:300]
                    self.stats["error_at"] = time.time()
                    self._stop.wait(wait)
                    wait = min(wait * 2, 30.0)
        self.sender.close()

    def close(self):
        self._stop.set()
        with contextlib.suppress(queue.Full):
            self.q.put_nowait(None)
        self._thread.join(timeout=2)
        super().close()


def status(cfg):
    """What this process does with its logs now."""
    s = section(cfg)
    h = _state["syslog"]
    f = _state["file"]
    return {
        "level": NAMES.get(level(s.get("level")), "info"),
        "file": f.baseFilename if f is not None else None,
        "syslog": bool(h),
        "target": h.target if h else None,
        "sent": h.stats["sent"] if h else 0,
        "dropped": h.stats["dropped"] if h else 0,
        "queued": h.q.qsize() if h else 0,
        "last_sent": h.stats["last_sent"] if h else None,
        "last_error": h.stats["last_error"] if h else None,
        "error_at": h.stats["error_at"] if h else None,
        "http": _http_status(),
    }


def test_send(cfg):
    """Send one line to the saved syslog server now (on or off): (ok, error, ms)."""
    s = section(cfg)
    if not s.get("syslog_host"):
        return False, "set the syslog server's address first", None
    transport = s.get("syslog_transport") if s.get("syslog_transport") in TRANSPORTS else "udp"
    client_cert, server_ca = _certs(s)
    sender = Sender(
        str(s["syslog_host"]),
        int(s.get("syslog_port") or 514),
        transport,
        s.get("syslog_framing") or "octet",
        s.get("syslog_verify") is not False,
        client_cert=client_cert,
        server_ca=server_ca,
    )
    r = logging.LogRecord("lens.logs", logging.INFO, __file__, 0, "Lens %s: test message from Settings → Logs", (__version__,), None)
    t = time.monotonic()
    try:
        sender.send(
            rfc5424(r, FACILITIES.get(s.get("syslog_facility"), 16), s.get("syslog_format") or "text", s.get("syslog_app_name") or "lens")
        )
        return True, None, int((time.monotonic() - t) * 1000)
    except (OSError, ValueError) as e:
        return False, f"{type(e).__name__}: {e}"[:300], None
    finally:
        sender.close()


# --- an HTTP endpoint: Grafana Loki's push API (Loki, Grafana Cloud, Alloy's loki.source.api), or JSON records ---


def _kind(s):
    return s.get("http_kind") if s.get("http_kind") in HTTP_KINDS else "loki"


def push_url(url, kind):
    """Where the lines go: for Loki, a bare server address gets the push API's path."""
    url = url.strip()
    if kind == "loki" and urllib.parse.urlsplit(url).path in ("", "/"):
        return url.rstrip("/") + LOKI_PUSH
    return url


def headers(text):
    """`key=value,key2=value2` (values URL-encoded, as OTEL_EXPORTER_OTLP_HEADERS) to a dict; ValueError if malformed."""
    out = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        k, sep, v = part.partition("=")
        k = k.strip()
        if not sep or not HEADER_RX.match(k):
            raise ValueError("logs.http_headers is key=value pairs separated by commas, like Authorization=Bearer%20abc")
        out[k] = urllib.parse.unquote(v.strip())
    return out


HEADER_RX = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$")


def http_body(records, kind):
    """(content type, bytes) for one request carrying these records (the dicts `record` makes)."""
    if kind == "loki":
        streams = {}
        for d in records:
            sec, _, frac = d["ts"].rstrip("Z").partition(".")
            t = datetime.datetime.fromisoformat(sec).replace(tzinfo=datetime.UTC)
            ns = int(t.timestamp()) * 1_000_000_000 + int((frac or "0").ljust(9, "0")[:9])
            labels = (("service_name", "lens"), ("host", d["host"]), ("process", d["process"]), ("level", d["level"]))
            streams.setdefault(labels, []).append([str(ns), json.dumps(d, ensure_ascii=False, default=str)])
        body = {"streams": [{"stream": dict(k), "values": v} for k, v in streams.items()]}
        return "application/json", json.dumps(body, ensure_ascii=False).encode()
    if kind == "ndjson":
        return "application/x-ndjson", "".join(json.dumps(d, ensure_ascii=False, default=str) + "\n" for d in records).encode()
    return "application/json", json.dumps(records, ensure_ascii=False, default=str).encode()


def _check_host(url):
    """OSError when the endpoint's host is, or resolves to, a link-local address (cloud metadata)."""
    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as e:
        raise OSError(f"couldn't find {host}: {e}") from None
    for *_, addr in infos:
        if ipaddress.ip_address(addr[0].split("%")[0]).is_link_local:
            raise OSError(f"{host} is a link-local address; Lens doesn't send logs there")


class HttpSender:
    """POSTs batches of records to one endpoint; `send` raises OSError when a batch didn't go."""

    def __init__(self, url, kind, header_text=None, verify=True, timeout=10.0, client_cert=None, server_ca=None):
        import httpx  # only when an endpoint is set

        self.url, self.kind = url, kind
        self.headers = headers(header_text) if header_text else {}
        tls = tls_context(verify, client_cert, server_ca) if (client_cert or server_ca) else verify
        # no redirects (a token never follows one to another server), no proxy from the environment: a log server is
        # usually on this network, and what happens to the lines is the admin's choice alone
        self.client = httpx.Client(timeout=timeout, verify=tls, follow_redirects=False, trust_env=False)
        self._error = httpx.HTTPError

    def send(self, records):
        _check_host(self.url)
        kind, body = http_body(records, self.kind)
        try:
            r = self.client.post(self.url, content=body, headers={**self.headers, "Content-Type": kind})
        except self._error as e:
            raise OSError(f"{type(e).__name__}: {e}") from None
        if r.status_code >= 300:
            raise OSError(f"HTTP {r.status_code}: {r.text[:200]}")

    def close(self):
        with contextlib.suppress(Exception):
            self.client.close()


class HttpHandler(logging.Handler):
    """Queues each record and sends them in batches from a background thread; never blocks the caller."""

    def __init__(self, url, kind, lvl, header_text, verify, client_cert=None, server_ca=None):
        super().__init__()
        self.addFilter(Gate(lvl))
        self.target = url
        self.sender = HttpSender(url, kind, header_text, verify, client_cert=client_cert, server_ca=server_ca)
        self.q = queue.Queue(QUEUE)
        self.stats = {"sent": 0, "dropped": 0, "last_sent": None, "last_error": None, "error_at": None}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="lens-log-http", daemon=True)
        self._thread.start()

    def emit(self, r):
        if threading.current_thread() is self._thread:
            return  # the HTTP client's own lines about sending (httpx, httpcore) would come back here forever
        try:
            self.q.put_nowait(record(r))
        except queue.Full:
            self.stats["dropped"] += 1
        except Exception:  # noqa: BLE001 - logging never raises into the caller
            self.handleError(r)

    def _batch(self):
        try:
            first = self.q.get(timeout=0.5)
        except queue.Empty:
            return []
        batch = [first]
        end = time.monotonic() + BATCH_SECONDS
        while len(batch) < BATCH and first is not None:
            try:
                d = self.q.get(timeout=max(0.0, end - time.monotonic()))
            except queue.Empty:
                break
            batch.append(d)
            if d is None:
                break
        return batch

    def _run(self):
        wait = 1.0
        while not self._stop.is_set():
            batch = self._batch()
            done = None in batch
            batch = [d for d in batch if d is not None]
            while batch and not self._stop.is_set():
                try:
                    self.sender.send(batch)
                    self.stats["sent"] += len(batch)
                    self.stats["last_sent"] = time.time()
                    wait = 1.0
                    break
                except OSError as e:
                    # never logged (that would come back here): the status in Settings → Logs shows it
                    self.stats["last_error"] = str(e)[:300]
                    self.stats["error_at"] = time.time()
                    self._stop.wait(wait)
                    wait = min(wait * 2, 30.0)
            if done:
                break
        self.sender.close()

    def close(self):
        self._stop.set()
        with contextlib.suppress(queue.Full):
            self.q.put_nowait(None)
        self._thread.join(timeout=2)
        super().close()


def _http_status():
    h = _state["http"]
    return {
        "on": bool(h),
        "target": h.target if h else None,
        "sent": h.stats["sent"] if h else 0,
        "dropped": h.stats["dropped"] if h else 0,
        "queued": h.q.qsize() if h else 0,
        "last_sent": h.stats["last_sent"] if h else None,
        "last_error": h.stats["last_error"] if h else None,
        "error_at": h.stats["error_at"] if h else None,
    }


def test_http(cfg):
    """Send one line to the saved HTTP endpoint now (on or off): (ok, error, ms)."""
    s = section(cfg)
    if not s.get("http_url"):
        return False, "set the endpoint's address first", None
    r = logging.LogRecord("lens.logs", logging.INFO, __file__, 0, "Lens %s: test message from Settings → Logs", (__version__,), None)
    t = time.monotonic()
    sender = None
    try:
        client_cert, server_ca = _certs(s)
        sender = HttpSender(
            push_url(str(s["http_url"]), _kind(s)),
            _kind(s),
            s.get("http_headers") if isinstance(s.get("http_headers"), str) else None,
            s.get("http_verify") is not False,
            client_cert=client_cert,
            server_ca=server_ca,
        )
        sender.send([record(r)])
        return True, None, int((time.monotonic() - t) * 1000)
    except (OSError, ValueError) as e:
        return False, str(e)[:300], None
    finally:
        if sender is not None:
            sender.close()


# --- reading the files back, for the log viewer ---


def _lines_backwards(path, chunk=65536):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        rest = b""
        while end > 0:
            n = min(chunk, end)
            end -= n
            f.seek(end)
            buf = f.read(n) + rest
            parts = buf.split(b"\n")
            rest = parts[0]
            for line in reversed(parts[1:]):
                if line.strip():
                    yield line
        if rest.strip():
            yield rest


def files(cfg):
    folder = pathlib.Path((cfg or {}).get("data_dir") or ".") / "logs"
    return sorted(folder.glob("*.jsonl*")) if folder.is_dir() else []


def read(cfg, limit=200, min_level=None, q=None, process=None, logger=None, before=None):
    """The newest log records from every process's file, newest first, matching the filters. `before` is a record's
    ts: only older ones (the next page)."""
    lo = level(min_level, logging.DEBUG) if min_level else logging.DEBUG
    needle = (q or "").lower()
    found = []
    for p in files(cfg):
        got = 0
        try:
            for raw in _lines_backwards(p):
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(d, dict) or not isinstance(d.get("ts"), str):
                    continue
                if before and d["ts"] >= before:
                    continue
                if LEVELS.get(d.get("level"), logging.INFO) < lo:
                    continue
                if process and d.get("process") != process:
                    continue
                if logger and not str(d.get("logger", "")).startswith(logger):
                    continue
                if needle and needle not in (str(d.get("msg", "")) + " " + str(d.get("exc", ""))).lower():
                    continue
                found.append(d)
                got += 1
                if got >= limit:
                    break
        except OSError:
            continue
    found.sort(key=lambda d: d["ts"], reverse=True)
    return found[:limit]
