"""A Gmail or Google Workspace mailbox read through Google's Gmail API: feeds.py hands its `gmail` sources here when
they read `via` the API (the default). `via` imap reads the same mailbox over IMAP instead (feeds.py), signed in the
same way (google_auth.py).

Labels are the folders: the inbox, sent mail, starred and important, the inbox's categories (Primary, Social,
Promotions, Updates, Forums) and the labels people made. Each message is a file `<label id>/<message id>.eml`, or
`<message id>.eml` when the whole mailbox is watched (everything but spam, the bin, drafts and chats; a message with
several labels is one message). A message is read as Gmail keeps it (format raw: the RFC 822 message), so it becomes
an email document as an IMAP one does. Nothing is changed in Gmail: the scope is read only (gmail.readonly), so
messages stay unread.

Listing a label asks for its messages' ids, then each one's headers (format metadata), several at a time. Headers once
read are remembered in memory, since a message never changes (its labels as Gmail last said them). Browsing shows a
label's newest BROWSE messages.

A watch keeps Gmail's history id as its cursor and asks only for what was added since (history.list): messages added
to the mailbox and, for a label, messages the label was put on. A watch that doesn't take what's there already starts
from the mailbox's history id at that moment, so nothing old is read at all. When the history is too old for Gmail to
keep (it keeps about a week), the watch reads what arrived from an hour before its last scan on (the cursor keeps
when that was), and skips what it already has.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import email
import re
import time
from concurrent.futures import ThreadPoolExecutor
from email import policy

import httpx

from . import activity, feeds, google_auth

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT = 60
WORKERS = 8  # messages whose headers are asked for at once
PAGE = 500  # ids per page (Gmail's most)
BROWSE = 200  # the newest messages a label shows when browsing
NAMES = {  # Gmail's own labels shown as folders, by the names its web app gives them
    "INBOX": "Inbox",
    "SENT": "Sent",
    "STARRED": "Starred",
    "IMPORTANT": "Important",
    "CATEGORY_PERSONAL": "Primary",
    "CATEGORY_SOCIAL": "Social",
    "CATEGORY_PROMOTIONS": "Promotions",
    "CATEGORY_UPDATES": "Updates",
    "CATEGORY_FORUMS": "Forums",
}
LEFT_OUT = {"SPAM", "TRASH", "DRAFT", "CHAT"}  # never read when the whole mailbox is watched
WHOLE = "-in:chats -in:drafts"  # the whole mailbox (Gmail leaves spam and the bin out of a search itself)
MAX_REMEMBERED = 20000
_META: dict[tuple, dict] = {}  # (source id, message id) -> the headers, size and arrival Gmail gave for a message


class Listing(list):
    """Files, and where a watch's next scan starts when nothing in them moves it on (`marks`, as feeds.advance takes
    cursors): Gmail's history id when the listing began."""

    def __init__(self, files=(), marks=()):
        super().__init__(files)
        self.marks = list(marks)


class _Expired(Exception):
    """Gmail no longer keeps the history a watch's cursor points into."""


def _why(r):
    try:
        e = r.json().get("error") or {}
    except ValueError:
        e = {}
    said = (e.get("message") if isinstance(e, dict) else str(e)) or r.reason_phrase
    if r.status_code == 429 or "ratelimit" in str(e).lower().replace(" ", ""):
        return f"Gmail is rate limiting this account: {said}"[:300]
    return f"Gmail answered {r.status_code}: {said}"[:300]


class _Gmail:
    def __init__(self, cfg, src):
        self.src = src
        self.http = httpx.Client(
            base_url=API, headers={"Authorization": f"Bearer {google_auth.token(cfg, src)}", "Accept": "application/json"}, timeout=TIMEOUT
        )

    def get(self, path, **params):
        try:
            r = self.http.get(path, params=params)
        except httpx.HTTPError as e:
            raise RuntimeError(f"couldn't reach Gmail: {e}") from None
        if r.status_code == 404:
            raise FileNotFoundError(_why(r))
        if r.status_code >= 400:
            raise RuntimeError(_why(r))
        return r.json()


@contextlib.contextmanager
def _gmail(cfg, src):
    g = _Gmail(cfg, src)
    try:
        yield g
    finally:
        g.http.close()


def _labels(g):
    """{label id: name} of the labels shown as folders: Gmail's own (NAMES) first, then people's by name."""
    mine, own = {}, {}
    for lab in g.get("/labels").get("labels") or []:
        lid = lab.get("id")
        if lid in NAMES:
            own[lid] = NAMES[lid]
        elif lid and lab.get("type") == "user":
            mine[lid] = lab.get("name") or lid
    return {**{k: own[k] for k in NAMES if k in own}, **dict(sorted(mine.items(), key=lambda kv: kv[1].lower()))}


def _ids(g, label, limit=None, after=None):
    """Ids of a label's messages, or the whole mailbox's (no label), newest first; only those that arrived `after`
    (seconds since 1970), if it's given."""
    out, page = [], None
    q = " ".join(x for x in ("" if label else WHOLE, f"after:{int(after)}" if after else "") if x)
    while True:
        params = {"maxResults": PAGE, **({"labelIds": label} if label else {}), **({"q": q} if q else {})}
        if page:
            params["pageToken"] = page
        got = g.get("/messages", **params)
        out += [m["id"] for m in got.get("messages") or [] if m.get("id")]
        page = got.get("nextPageToken")
        if not page or (limit and len(out) >= limit):
            return out[:limit] if limit else out


def _meta(g, ids):
    """{id: what Gmail says of the message: headers, size, arrival, labels}, several asked for at once; a message
    gone since is left out."""
    sid = g.src["id"]
    wanted = list(dict.fromkeys(i for i in ids if (sid, i) not in _META))

    def one(i):
        try:
            return i, g.get(f"/messages/{i}", format="metadata", metadataHeaders=list(feeds.HEADERS))
        except FileNotFoundError:
            return i, None

    if wanted:
        with ThreadPoolExecutor(min(WORKERS, len(wanted))) as pool:
            for i, m in pool.map(one, wanted):
                if m:
                    _META[(sid, i)] = m
        if len(_META) > MAX_REMEMBERED:
            for k in list(_META)[: len(_META) // 5]:
                del _META[k]
    return {i: _META[(sid, i)] for i in ids if (sid, i) in _META}


def _entry(m, label, names, mark):
    head = "".join(f"{h.get('name')}: {h.get('value')}\r\n" for h in (m.get("payload") or {}).get("headers") or [])
    h = email.message_from_string(head + "\r\n", policy=policy.default)
    try:
        subject = str(h.get("subject") or "").strip()
    except (ValueError, TypeError):
        subject = ""
    try:
        arrived = dt.datetime.fromtimestamp(int(m.get("internalDate")) / 1000, dt.timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        arrived = None
    try:
        sent = h["date"].datetime.isoformat(timespec="seconds") if h.get("date") and h["date"].datetime else None
    except (AttributeError, TypeError, ValueError):
        sent = None
    msgid = str(h.get("message-id") or "").strip().strip("<>").strip() or None
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "", subject)[:150].strip() or "(no subject)"
    rel = f"{m['id']}.eml"
    shown = names.get(label, label) if label else [names[x] for x in m.get("labelIds") or [] if x in names]
    return feeds._entry(
        f"{label}/{rel}" if label else rel,
        rel,
        f"{name}.eml",
        int(m.get("sizeEstimate") or 0) or None,
        arrived or sent,
        subject or None,
        sent or arrived,
        key=f"message-id:{msgid}" if msgid else None,
        cursor=mark,
        mail=feeds._addressing(h, subject, shown),
        settled=True,  # a message is whole once Gmail lists it: no waiting for it to settle
    )


def _files(g, ids, label, names, mark):
    """These messages as files of a label (or of the whole mailbox, which leaves out drafts and spam), newest first."""
    meta = _meta(g, ids)
    files = [
        _entry(meta[i], label, names, mark(i)) for i in ids if i in meta and (label or not set(meta[i].get("labelIds") or []) & LEFT_OUT)
    ]
    return sorted(files, key=lambda e: e["modified"] or "", reverse=True)


def _check(names, path):
    if path and path not in names:
        raise ValueError(f"there's no label {path}")


def _since(g, label, names, start):
    """What was added since history id `start`, and where the next scan starts."""
    added, page, now = {}, None, start
    while True:
        params = {"startHistoryId": start, "historyTypes": ["messageAdded", *(["labelAdded"] if label else [])], "maxResults": PAGE}
        if label:
            params["labelId"] = label
        if page:
            params["pageToken"] = page
        try:
            got = g.get("/history", **params)
        except FileNotFoundError:
            raise _Expired from None
        now = max(now, int(got.get("historyId") or now))
        for rec in got.get("history") or []:
            hid = int(rec.get("id") or now)
            for a in [*(rec.get("messagesAdded") or []), *(rec.get("labelsAdded") or [])]:
                m = a.get("message") or {}
                if (g.src["id"], m.get("id")) in _META and "labelIds" in m:  # remembered headers, with today's labels
                    _META[(g.src["id"], m["id"])]["labelIds"] = m["labelIds"]
            for a in rec.get("messagesAdded") or []:
                m = a.get("message") or {}
                labels = set(m.get("labelIds") or [])
                if m.get("id") and (label in labels if label else not labels & LEFT_OUT):
                    added.setdefault(m["id"], hid)
            for a in rec.get("labelsAdded") or []:
                m = a.get("message") or {}
                if label and m.get("id") and label in (a.get("labelIds") or []):
                    added.setdefault(m["id"], hid)
        page = got.get("nextPageToken")
        if not page:
            break
    key, at = label or "", int(time.time())
    files = _files(g, list(added), label, names, lambda i: {"mailbox": key, "validity": 0, "uid": added[i], "at": at})
    return Listing(files, [{"mailbox": key, "validity": 0, "uid": now, "at": at}])


# ---------- what feeds.py calls ----------
def browse(cfg, src, path):
    if path:
        return latest(cfg, src, path, BROWSE)
    with _gmail(cfg, src) as g:
        return [feeds._folder(lid, name) for lid, name in _labels(g).items()]


def latest(cfg, src, path, limit):
    """The newest `limit` messages of a label, or of the whole mailbox."""
    with _gmail(cfg, src) as g, activity.call("gmail.list", detail={"label": path or "*"}):
        names = _labels(g)
        _check(names, path)
        return _files(g, _ids(g, path, limit), path, names, lambda i: None)


def list_files(cfg, src, path, cursor=None, fresh=False, headers=True):
    """Every message of a label (`path`), or of the whole mailbox. With a watch's `cursor` ({mailbox: {uid}}: a
    history id), only those added since; `fresh` (a watch's first scan that leaves what's there) lists nothing, and
    only marks where the next one starts. Without `headers`, only the files' names (for counting them)."""
    key = path or ""
    since = (cursor or {}).get(key)
    with _gmail(cfg, src) as g, activity.call("gmail.list", detail={"label": key or "*", "since": bool(since)}):
        names = _labels(g)
        _check(names, path)
        if since:
            with contextlib.suppress(_Expired):
                return _since(g, path, names, int(since["uid"]))
        after = int(since["at"]) - 3600 if since and since.get("at") else None  # Gmail forgot that history
        mark = {"mailbox": key, "validity": 0, "uid": int(g.get("/profile")["historyId"]), "at": int(time.time())}
        if fresh:
            return Listing([], [mark])
        ids = _ids(g, path, after=after)
        if not headers:
            return Listing([feeds._entry(f"{path}/{i}.eml" if path else f"{i}.eml", f"{i}.eml", f"{i}.eml", None, None) for i in ids])
        return Listing(_files(g, ids, path, names, lambda i: mark), [mark])


def find(cfg, src, paths):
    """{path: file} for these messages, wherever they are (a label's listing shows only its newest)."""
    wanted = {}
    for p in paths:
        label, i = _message(p)
        wanted.setdefault(label, []).append(i)
    with _gmail(cfg, src) as g:
        names = _labels(g)
        out = {}
        for label, ids in wanted.items():
            out.update({f["path"]: f for f in _files(g, ids, label, names, lambda i: None)})
        return out


MESSAGE_RX = re.compile(r"^(?:(?P<label>[^/]+)/)?(?P<id>[0-9A-Za-z]+)\.eml$")


def _message(path):
    m = MESSAGE_RX.match(path or "")
    if not m:
        raise ValueError("that isn't a message (label/id.eml)")
    return m.group("label") or "", m.group("id")


def fetch(cfg, src, path):
    _, i = _message(path)
    with _gmail(cfg, src) as g, activity.call("gmail.read"):
        try:
            raw = g.get(f"/messages/{i}", format="raw").get("raw") or ""
        except FileNotFoundError:
            raise FileNotFoundError(f"message {i} isn't in the mailbox any more") from None
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def test(cfg, src):
    with _gmail(cfg, src) as g:
        try:
            g.get("/profile")
        except FileNotFoundError as e:
            raise RuntimeError(str(e)) from None
