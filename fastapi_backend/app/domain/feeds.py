"""Sources that aren't file storage: IMAP mailboxes and iCalendar feeds, shown as folders of files so browsing,
choosing files, watching and importing work for them as they do for rclone's storage (sources.py).

- An IMAP account's folders are its mailboxes, and each message is a file, `<mailbox>/<uid>.eml` (UIDs don't change
  while a mailbox exists). A message is an email like any other: a document whose attachments are kept and made
  resources of their own, or its text where the server can't make PDFs. Nothing is changed on the server: mailboxes
  are opened read-only and messages fetched with BODY.PEEK, so they stay unread.
- An iCalendar feed (an https:// or webcal:// address, as calendars publish them) is one folder of events, each a
  file `<id>.ics` of its own (the id stands for its UID and, for a changed occurrence, its RECURRENCE-ID). An event is
  read as text: its title, when and where, who, and its description.

Both keep the names of their files (`name`), a `title` and `when` for the resource made from one, beside what rclone's
listings have (path, rel, size, modified).
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import email
import imaplib
import re
import ssl
import time
import urllib.error
import urllib.request
from email import policy

from . import calendars

TYPES = {
    "imap": {
        "label": "Email (IMAP)",
        "fields": {"host": "", "port": "993", "security": "ssl", "user": ""},
        "secrets": ["pass"],
    },
    "ical": {
        "label": "Calendar feed (iCal)",
        "fields": {"url": "", "user": ""},
        "secrets": ["pass"],
    },
}
SECURITY = ("ssl", "starttls", "none")
SKIP_MAILBOXES = {"\\noselect", "\\nonexistent", "\\trash", "\\junk", "\\drafts"}  # not taken when a whole account is watched
MAX_CALENDAR = 50 * 1024 * 1024
FEED_SECONDS = 60  # a calendar fetched for a listing is reused this long (a scan reads each of its events)
_FEEDS: dict = {}


def handles(src):
    return src["type"] in TYPES


def _secret(cfg, src, k):
    from . import settings  # settings imports sources, which imports this

    sealed = (src.get("sealed") or {}).get(k)
    return settings.unseal(cfg, sealed, f"source:{src['id']}:{k}") if sealed else ""


def _params(src):
    return {k: str(v).strip() for k, v in (src.get("params") or {}).items() if v is not None}


def _entry(path, rel, name, size, modified, title=None, when=None):
    return {"path": path, "rel": rel, "name": name, "dir": False, "size": size, "modified": modified, "title": title, "when": when}


def _folder(path, name):
    return {"path": path, "rel": path, "name": name, "dir": True, "size": None, "modified": None}


# ---------- IMAP ----------
def _mutf7(name):
    """A mailbox name as people read it (IMAP sends non-ASCII names in modified UTF-7: '&AOk-t&AOk-' is 'été')."""

    def dec(m):
        if not m.group(1):
            return "&"
        b = m.group(1).replace(",", "/")
        try:
            return base64.b64decode(b + "=" * (-len(b) % 4)).decode("utf-16-be")
        except ValueError:
            return m.group(0)

    return re.sub(r"&([^-]*)-", dec, name)


def _quote(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ok(typ, data, what):
    if typ != "OK":
        said = b" ".join(d for d in data if isinstance(d, bytes)).decode("utf-8", "replace").strip()
        raise RuntimeError(f"{what} failed: {said or typ}"[:300])
    return data


@contextlib.contextmanager
def _imap(cfg, src):
    p = _params(src)
    host, security = p.get("host"), p.get("security") or "ssl"
    if not host:
        raise RuntimeError("the mail server's host name isn't set")
    if security not in SECURITY:
        raise RuntimeError(f"security is one of: {', '.join(SECURITY)}")
    try:
        port = int(p.get("port") or (993 if security == "ssl" else 143))
    except ValueError:
        raise RuntimeError("the port is a number, such as 993") from None
    try:
        if security == "ssl":
            conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=60)
        else:
            conn = imaplib.IMAP4(host, port, timeout=60)
            if security == "starttls":
                conn.starttls(ssl.create_default_context())
    except (OSError, imaplib.IMAP4.error) as e:
        raise RuntimeError(f"couldn't reach {host}:{port}: {e}") from None
    try:
        try:
            conn.login(p.get("user") or "", _secret(cfg, src, "pass"))
        except imaplib.IMAP4.error as e:
            raise RuntimeError(f"the mail server refused to sign in: {e}") from None
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.logout()


LIST_RX = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.*)$', re.I)


def _mailboxes(conn):
    """[(name as the server knows it, flags)] of every mailbox."""
    out = []
    for item in _ok(*conn.list(), "listing mailboxes"):
        if item is None:
            continue
        literal = None
        if isinstance(item, tuple):
            item, literal = item[0], item[1]
        m = LIST_RX.match(item.strip())
        if not m:
            continue
        raw = literal if literal is not None else m.group("name").strip()
        if literal is None and raw.startswith(b'"'):
            raw = re.sub(rb"\\(.)", rb"\1", raw[1:-1])
        flags = {f.lower() for f in m.group("flags").decode("ascii", "replace").split()}
        out.append((raw.decode("utf-8", "replace"), flags))
    return out


def _select(conn, mailbox):
    typ, data = conn.select(_quote(mailbox), readonly=True)
    if typ != "OK":
        raise ValueError(f"there's no mailbox {mailbox}")
    try:
        return int(data[0] or 0)
    except (TypeError, ValueError):
        return 0


def _internal(meta):
    m = re.search(rb'INTERNALDATE "([^"]+)"', meta)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1).decode().strip(), "%d-%b-%Y %H:%M:%S %z").isoformat(timespec="seconds")
    except ValueError:
        return None


def _messages(conn, mailbox, base):
    """The messages of a mailbox as files: uid, size, when it arrived, its subject (headers only; nothing is read)."""
    if not _select(conn, mailbox):
        return []
    typ, data = conn.uid("FETCH", "1:*", "(UID RFC822.SIZE INTERNALDATE BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])")
    data = _ok(typ, data, f"reading {mailbox}")
    out, i = [], 0
    while i < len(data):
        item = data[i]
        if not isinstance(item, tuple):
            i += 1
            continue
        meta, header = item[0], item[1] or b""
        if i + 1 < len(data) and isinstance(data[i + 1], bytes):
            meta += b" " + data[i + 1]  # some servers send UID and the rest after the header
            i += 1
        i += 1
        uid, size = re.search(rb"UID (\d+)", meta), re.search(rb"RFC822\.SIZE (\d+)", meta)
        if not uid:
            continue
        h = email.message_from_bytes(header, policy=policy.default)
        try:
            subject = str(h.get("subject") or "").strip()
        except (ValueError, TypeError):
            subject = ""
        arrived = _internal(meta)
        try:
            sent = h["date"].datetime.isoformat(timespec="seconds") if h.get("date") and h["date"].datetime else None
        except (AttributeError, TypeError, ValueError):
            sent = None
        name = re.sub(r"[\x00-\x1f\x7f/\\]", "", subject)[:150].strip() or "(no subject)"
        rel = f"{uid.group(1).decode()}.eml"
        out.append(
            _entry(
                f"{mailbox}/{rel}",
                f"{base}{rel}",
                f"{name}.eml",
                int(size.group(1)) if size else None,
                arrived or sent,
                subject or None,
                sent or arrived,
            )
        )
    return sorted(out, key=lambda e: e["modified"] or "", reverse=True)


MESSAGE_RX = re.compile(r"^(?P<mailbox>.+)/(?P<uid>\d+)\.eml$")


def _message_path(path):
    m = MESSAGE_RX.match(path or "")
    if not m:
        raise ValueError("that isn't a message (mailbox/uid.eml)")
    return m.group("mailbox"), m.group("uid")


def _imap_browse(cfg, src, path):
    with _imap(cfg, src) as conn:
        boxes = _mailboxes(conn)
        if not path:
            return [_folder(n, _mutf7(n)) for n, flags in boxes if "\\noselect" not in flags and "\\nonexistent" not in flags]
        if path not in {n for n, _ in boxes}:
            raise ValueError(f"there's no mailbox {path}")
        return _messages(conn, path, "")


def _imap_files(cfg, src, path):
    with _imap(cfg, src) as conn:
        boxes = _mailboxes(conn)
        if path:
            if path not in {n for n, _ in boxes}:
                raise ValueError(f"there's no mailbox {path}")
            return _messages(conn, path, "")
        return [e for n, flags in boxes if not flags & SKIP_MAILBOXES for e in _messages(conn, n, f"{n}/")]


def _imap_fetch(cfg, src, path):
    mailbox, uid = _message_path(path)
    with _imap(cfg, src) as conn:
        _select(conn, mailbox)
        typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
        data = _ok(typ, data, f"reading message {uid}")
        raw = next((d[1] for d in data if isinstance(d, tuple) and d[1]), None)
    if raw is None:
        raise FileNotFoundError(f"message {uid} isn't in {mailbox} any more")
    return raw


def _imap_test(cfg, src):
    with _imap(cfg, src) as conn:
        if not _mailboxes(conn):
            raise RuntimeError("signed in, but the account has no mailboxes")


# ---------- iCalendar feeds ----------
def _url(src):
    url = _params(src).get("url", "")
    url = re.sub(r"^webcals?://", "https://", url, flags=re.I)
    if not re.match(r"^https?://\S+$", url, re.I):
        raise RuntimeError("the calendar's address starts with https:// (or webcal://)")
    return url


def _download(cfg, src):
    url = _url(src)
    hit = _FEEDS.get(src["id"])
    if hit and hit[0] == url and time.monotonic() - hit[1] < FEED_SECONDS:
        return hit[2]
    req = urllib.request.Request(url, headers={"User-Agent": "Lens calendar source", "Accept": "text/calendar, */*;q=0.5"})
    user, pw = _params(src).get("user", ""), _secret(cfg, src, "pass")
    if user or pw:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode("ascii"))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read(MAX_CALENDAR + 1)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"the calendar's server answered {e.code} {e.reason}") from None
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"couldn't fetch the calendar: {getattr(e, 'reason', e)}") from None
    if len(raw) > MAX_CALENDAR:
        raise RuntimeError("the calendar is larger than 50 MB")
    text = raw.decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text[:4096].upper():
        raise RuntimeError("that address doesn't give a calendar (no BEGIN:VCALENDAR)")
    cal = calendars.parse(text)
    _FEEDS[src["id"]] = (url, time.monotonic(), cal)
    return cal


EPOCH = "1970-01-01T00:00:00+00:00"  # for events that don't say when they changed: their size tells


def _events(cfg, src):
    cal = _download(cfg, src)
    out = {}
    for ev in cal["events"]:
        body = calendars.event_ics(cal, ev).encode("utf-8")
        start = calendars.start(ev)
        title = calendars.summary(ev) or "(no title)"
        day = calendars.iso(start)[:10] + " " if start is not None else ""
        name = re.sub(r"[\x00-\x1f\x7f/\\]", "", f"{day}{title}")[:150].strip()
        rel = f"{calendars.file_id(ev)}.ics"
        out[rel] = (
            _entry(rel, rel, f"{name}.ics", len(body), calendars.iso(calendars.changed(ev)) or EPOCH, title, calendars.iso(start)),
            body,
        )
    return out


def _ical_browse(cfg, src, path):
    if path:
        raise ValueError("a calendar has no folders")
    return sorted((e for e, _ in _events(cfg, src).values()), key=lambda e: e["when"] or "", reverse=True)


def _ical_fetch(cfg, src, path):
    hit = _events(cfg, src).get(path)
    if hit is None:
        raise FileNotFoundError("that event isn't in the calendar any more")
    return hit[1]


def _ical_test(cfg, src):
    _FEEDS.pop(src["id"], None)
    _download(cfg, src)


# ---------- what sources.py calls ----------
def check_path(path):
    return (path or "").strip().strip("/")


def browse(cfg, src, path=""):
    return (_imap_browse if src["type"] == "imap" else _ical_browse)(cfg, src, check_path(path))


def list_files(cfg, src, path=""):
    path = check_path(path)
    return _imap_files(cfg, src, path) if src["type"] == "imap" else _ical_browse(cfg, src, path)


def fetch(cfg, src, path):
    """The file's bytes: a message as the server keeps it, an event as an .ics of its own."""
    return (_imap_fetch if src["type"] == "imap" else _ical_fetch)(cfg, src, check_path(path))


def immutable(src):
    """Whether a file, once fetched, never changes (an IMAP message), so a cached copy can be kept."""
    return src["type"] == "imap"


def test(cfg, src):
    (_imap_test if src["type"] == "imap" else _ical_test)(cfg, src)
