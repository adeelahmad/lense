"""Sources that aren't file storage: IMAP mailboxes and iCalendar feeds, shown as folders of files so browsing,
choosing files, watching and importing work for them as they do for rclone's storage (sources.py).

- An IMAP account's folders are its mailboxes, and each message is a file, `<mailbox>/<uidvalidity>-<uid>.eml`: a
  UID stands for one message only while the mailbox's UIDVALIDITY stays the same, so a mailbox the server rebuilt
  gives new files rather than old names for other messages. A message is an email like any other: a document whose
  attachments are kept and made resources of their own, or its text where the server can't make PDFs. Nothing is
  changed on the server: mailboxes are opened read-only and messages fetched with BODY.PEEK, so they stay unread. A
  watch keeps, per mailbox, the last UID it has seen (its cursor), and asks only for messages after it.
- An iCalendar feed (an https:// or webcal:// address, as calendars publish them) is one folder of events, each a
  file `<id>.ics` of its own (the id stands for its UID and, for a changed occurrence, its RECURRENCE-ID). An event is
  read as text: its title, when and where, who, and its description. The address is kept sealed, like a password
  (a private calendar's address is all it takes to read it), and fetched as web pages are captured (netguard.py):
  public addresses only, and the networks in documents.web_networks, on ports 80 and 443. Its password is never sent
  on to another server it redirects to.

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
import urllib.parse
import urllib.request
from email import policy

from . import calendars, netguard

TYPES = {
    "imap": {
        "label": "Email (IMAP)",
        "fields": {"host": "", "port": "993", "security": "ssl", "user": ""},
        "secrets": ["pass"],
    },
    "ical": {
        "label": "Calendar feed (iCal)",
        "fields": {"user": ""},
        "secrets": ["url", "pass"],
    },
}
SECURITY = ("ssl", "starttls", "none")
# not taken when a whole account is watched: what can't be opened, the bin, junk and drafts, and the mailboxes that
# only show messages kept elsewhere (Gmail's All Mail, Starred and Important)
SKIP_MAILBOXES = {"\\noselect", "\\nonexistent", "\\trash", "\\junk", "\\drafts", "\\all", "\\flagged", "\\important"}
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


def _entry(path, rel, name, size, modified, title=None, when=None, **more):
    return {"path": path, "rel": rel, "name": name, "dir": False, "size": size, "modified": modified, "title": title, "when": when, **more}


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
    """Open a mailbox read-only: (how many messages, its UIDVALIDITY)."""
    typ, data = conn.select(_quote(mailbox), readonly=True)
    if typ != "OK":
        raise ValueError(f"there's no mailbox {mailbox}")
    _, validity = conn.response("UIDVALIDITY")
    try:
        return int(data[0] or 0), int((validity or [b"0"])[0] or 0)
    except (TypeError, ValueError):
        return 0, 0


def _internal(meta):
    m = re.search(rb'INTERNALDATE "([^"]+)"', meta)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1).decode().strip(), "%d-%b-%Y %H:%M:%S %z").isoformat(timespec="seconds")
    except ValueError:
        return None


def _messages(conn, mailbox, base, after=None):
    """The messages of a mailbox as files: uid, size, when it arrived, its subject and Message-ID (headers only;
    nothing is read). With `after` ({validity, uid}, a watch's cursor), only those after that UID, unless the mailbox
    has been rebuilt since (its UIDVALIDITY changed)."""
    count, validity = _select(conn, mailbox)
    if not count:
        return []
    last = int(after["uid"]) if after and int(after.get("validity") or -1) == validity else 0
    typ, data = conn.uid("FETCH", f"{last + 1}:*", "(UID RFC822.SIZE INTERNALDATE BODY.PEEK[HEADER.FIELDS (SUBJECT DATE MESSAGE-ID)])")
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
        if not uid or int(uid.group(1)) <= last:  # n:* always answers with the last message, even one before n
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
        msgid = str(h.get("message-id") or "").strip().strip("<>").strip() or None
        name = re.sub(r"[\x00-\x1f\x7f/\\]", "", subject)[:150].strip() or "(no subject)"
        rel = f"{validity}-{uid.group(1).decode()}.eml"
        out.append(
            _entry(
                f"{mailbox}/{rel}",
                f"{base}{rel}",
                f"{name}.eml",
                int(size.group(1)) if size else None,
                arrived or sent,
                subject or None,
                sent or arrived,
                key=f"message-id:{msgid}" if msgid else None,
                cursor={"mailbox": mailbox, "validity": validity, "uid": int(uid.group(1))},
            )
        )
    return sorted(out, key=lambda e: e["modified"] or "", reverse=True)


MESSAGE_RX = re.compile(r"^(?P<mailbox>.+)/(?P<validity>\d+)-(?P<uid>\d+)\.eml$")


def _message_path(path):
    m = MESSAGE_RX.match(path or "")
    if not m:
        raise ValueError("that isn't a message (mailbox/uidvalidity-uid.eml)")
    return m.group("mailbox"), int(m.group("validity")), m.group("uid")


def _selectable(boxes):
    return [n for n, flags in boxes if "\\noselect" not in flags and "\\nonexistent" not in flags]


def _imap_browse(cfg, src, path):
    with _imap(cfg, src) as conn:
        boxes = _mailboxes(conn)
        if not path:
            return [_folder(n, _mutf7(n)) for n in _selectable(boxes)]
        if path not in _selectable(boxes):
            raise ValueError(f"there's no mailbox {path}")
        return _messages(conn, path, "")


def _imap_files(cfg, src, path, cursor=None):
    """A mailbox's messages, or the whole account's (no path): every mailbox but those in SKIP_MAILBOXES, INBOX first,
    each message once however many mailboxes show it (by its Message-ID). With `cursor` ({mailbox: {validity, uid}}),
    only messages after it."""
    cursor = cursor or {}
    with _imap(cfg, src) as conn:
        boxes = _mailboxes(conn)
        if path:
            if path not in _selectable(boxes):
                raise ValueError(f"there's no mailbox {path}")
            return _messages(conn, path, "", cursor.get(path))
        names = sorted((n for n, flags in boxes if not flags & SKIP_MAILBOXES), key=lambda n: n.upper() != "INBOX")
        out, seen = [], {}
        for n in names:
            for e in _messages(conn, n, f"{n}/", cursor.get(n)):
                if e["key"] and e["key"] in seen:  # listed once; its place here still moves this mailbox's cursor on
                    seen[e["key"]].setdefault("also", []).append(e["cursor"])
                    continue
                if e["key"]:
                    seen[e["key"]] = e
                out.append(e)
        return out


def _imap_fetch(cfg, src, path):
    mailbox, validity, uid = _message_path(path)
    with _imap(cfg, src) as conn:
        _, now = _select(conn, mailbox)
        if now != validity:
            raise FileNotFoundError(f"{mailbox} has been rebuilt on the server: message {uid} there is another one now")
        typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
        data = _ok(typ, data, f"reading message {uid}")
        raw = next((d[1] for d in data if isinstance(d, tuple) and d[1]), None)
    if raw is None:
        raise FileNotFoundError(f"message {uid} isn't in {mailbox} any more")
    return raw


def advance(cursor, files, held=()):
    """A watch's cursor after a scan of these files: each mailbox's last UID listed, but before the first one still
    `held` (paths waiting to settle, to be listed again). Mailboxes the scan didn't list keep theirs."""
    out = {c["mailbox"]: dict(c) for c in cursor or []}
    first_held = {}
    for f in files:
        for c in [f["cursor"], *f.get("also", [])] if f.get("cursor") else []:
            if f["path"] in held:
                first_held[c["mailbox"]] = min(first_held.get(c["mailbox"], c["uid"]), c["uid"])
            old = out.get(c["mailbox"])
            if not old or old["validity"] != c["validity"] or c["uid"] > old["uid"]:
                out[c["mailbox"]] = dict(c)
    for box, uid in first_held.items():
        out[box]["uid"] = min(out[box]["uid"], uid - 1)
    return list(out.values())


def _imap_test(cfg, src):
    with _imap(cfg, src) as conn:
        if not _mailboxes(conn):
            raise RuntimeError("signed in, but the account has no mailboxes")


# ---------- iCalendar feeds ----------
def _url(cfg, src):
    url = _secret(cfg, src, "url").strip()
    url = re.sub(r"^webcals?://", "https://", url, flags=re.I)
    if not re.match(r"^https?://[^\s/]+\S*$", url, re.I):
        raise RuntimeError("the calendar's address starts with https:// (or webcal://)")
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follows a calendar's redirects, but not with its password to another server (or to plain http)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and req.has_header("Authorization"):
            a, b = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(new.full_url)
            same = (a.hostname, a.port) == (b.hostname, b.port) and (a.scheme == b.scheme or b.scheme == "https")
            if not same:
                why = "it redirects to another server, which isn't given its password: use the address it ends at"
                raise urllib.error.HTTPError(newurl, code, why, headers, fp)
        return new


def _opener(guard):
    """netguard's opener (every request through the guard), with _Redirects."""
    o = urllib.request.OpenerDirector()
    for h in (
        netguard.Through({"http": guard.url, "https": guard.url}),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        _Redirects(),
        urllib.request.HTTPErrorProcessor(),
    ):
        o.add_handler(h)
    return o


def _download(cfg, src):
    from . import webcapture  # the networks an admin allowed besides public ones; it imports what imports this

    url = _url(cfg, src)
    hit = _FEEDS.get(src["id"])
    if hit and hit[0] == url and time.monotonic() - hit[1] < FEED_SECONDS:
        return hit[2]
    try:
        webcapture.check_url(cfg, url)
    except ValueError as e:
        if "public address" in str(e):
            host = urllib.parse.urlsplit(url).hostname
            raise RuntimeError(
                f"that calendar can't be fetched: {host} is on a private network. An admin can allow that network: "
                "LENS_WEB_NETWORKS in .env (e.g. 192.168.1.0/24), or documents.web_networks in archive.yaml"
            ) from None
        raise RuntimeError(f"that calendar can't be fetched: {e}") from None
    req = urllib.request.Request(url, headers={"User-Agent": "Lens calendar source", "Accept": "text/calendar, */*;q=0.5"})
    user, pw = _params(src).get("user", ""), _secret(cfg, src, "pass")
    if user or pw:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode("ascii"))
    with netguard.Guard(forward=True, networks=webcapture.networks(cfg), max_bytes=MAX_CALENDAR + 65536) as guard:
        try:
            with _opener(guard).open(req, timeout=60) as r:
                raw = r.read(MAX_CALENDAR + 1)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"the calendar's server answered {e.code} {e.reason}") from None
        except (urllib.error.URLError, OSError) as e:
            refused = f"; refused: {guard.refused[0]}" if guard.refused else ""
            raise RuntimeError(f"couldn't fetch the calendar: {getattr(e, 'reason', e)}{refused}") from None
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


def list_files(cfg, src, path="", cursor=None):
    """Every file under `path`; for IMAP with a watch's `cursor` (a list of {mailbox, validity, uid}), only messages
    after it."""
    path = check_path(path)
    if src["type"] == "imap":
        return _imap_files(cfg, src, path, {c["mailbox"]: c for c in cursor or []})
    return _ical_browse(cfg, src, path)


def fetch(cfg, src, path):
    """The file's bytes: a message as the server keeps it, an event as an .ics of its own."""
    return (_imap_fetch if src["type"] == "imap" else _ical_fetch)(cfg, src, check_path(path))


def immutable(src):
    """Whether a file, once fetched, never changes (an IMAP message), so a cached copy can be kept."""
    return src["type"] == "imap"


def test(cfg, src):
    (_imap_test if src["type"] == "imap" else _ical_test)(cfg, src)
