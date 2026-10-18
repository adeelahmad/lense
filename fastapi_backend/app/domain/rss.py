"""News feeds (RSS and Atom) as a source, a file sensor as feeds.py's calendar feeds are: a feed is one folder of
posts, each a file `<id>.atom` of its own, so browsing, choosing posts, watching and importing work as they do for any
source. Watching a feed subscribes to it: each new post becomes a text resource of the namespace, titled and dated by
the post, with its address, author and feed written in, so answers cite it.

The address can be the feed's own or a page of the site that names its feed (`<link rel="alternate"
type="application/rss+xml">`, as blogs and news sites do): the first feed the page names is read. feedparser (BSD)
reads RSS 0.9x, 1.0 and 2.0 and Atom 0.3 and 1.0, drops scripts, styles and event handlers from the posts' HTML and
makes their links absolute. Whatever the feed was, each post is written as an Atom entry document (RFC 4287
§4.1.2): its id, title, the post's page and its enclosures (a podcast's audio), when it was published and updated, its
authors, categories, summary and content, and the feed it came from (its title and site, never its address). `text`
reads one as Markdown, as ingest.py reads emails and calendar events.

The address is kept sealed, like a password (a private feed's address often carries its key), and fetched as calendar
feeds are (feeds.get): public addresses only, and the networks in documents.web_networks, on ports 80 and 443; the
password, for a feed that asks for one, is never sent to another server, a feed that a page names elsewhere included.
A feed read is reused for FEED_SECONDS, then asked for again with its ETag and Last-Modified, so one that hasn't
changed costs a small request and no parsing. Its posts stay in memory for KEEP_SECONDS (a scan reads the new ones
right after listing them), its listing for as long as Lens runs: a post wanted later is read again, so many feeds
don't fill a small machine's memory.

A post's file is named by its id (an RSS item's guid), so it stays the same file however the feed reorders or retitles
it. Its time is when it was published; a post whose text changes (its size) is read again into the resource it
already is.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import io
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

TYPE = {
    "label": "News feed (RSS or Atom)",
    "fields": {"user": ""},
    "secrets": ["url", "pass"],  # the address is kept like a password: a private feed's carries its key
}
MAX_FEED = 20 * 2**20
MAX_POSTS = 500  # a feed's first posts (its newest, as feeds list them) that are listed
FEED_SECONDS = 60  # a feed read for a listing is reused this long (a scan then reads each of its posts)
KEEP_SECONDS = 300  # how long a feed's posts stay in memory after it was read (its listing stays)
AGENT = "Lens feed reader"
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"
FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/rdf+xml")
ATOM = "http://www.w3.org/2005/Atom"
EPOCH = "1970-01-01T00:00:00+00:00"  # for posts that don't say when: their size tells
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
# source id -> the feed as last read: {url, found, at, read_at, etag, last_modified, title, site, entries, bodies}
_FEEDS: dict = {}


def _plain(v):
    """Text on one line, without control characters."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(v or "")).split())


def _href(v):
    v = _plain(v)
    return v if urllib.parse.urlsplit(v).scheme in ("http", "https") else None


def _xml(v):
    return _XML_BAD.sub("", str(v))


def _html_text(markup):
    from . import mailbox  # its reader of HTML mail bodies, a line per block

    return mailbox.html_text(markup)


def _url(cfg, src):
    from . import feeds  # it imports this

    return address(feeds._secret(cfg, src, "url"))


def address(url):
    """A feed's address as it's fetched: https:// for feed://, and checked to be a web address."""
    url = str(url or "").strip()
    url = re.sub(r"^feed:(?=https?://)", "", url, flags=re.I)  # feed:https://…, as some sites link their feeds
    url = re.sub(r"^feed://", "https://", url, flags=re.I)
    if not re.match(r"^https?://[^\s/]+\S*$", url, re.I):
        raise RuntimeError("the feed's address starts with https:// (or feed://)")
    return url


def _server(url):
    u = urllib.parse.urlsplit(url)
    return u.hostname, u.port or (443 if u.scheme == "https" else 80)


# ---------- reading a feed ----------
class _Alternates(HTMLParser):
    """The feeds a web page names: its <link rel="alternate" type="application/rss+xml" href="…"> (or Atom's)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k.lower(): (v or "").strip() for k, v in attrs}
        kind = a.get("type", "").lower().split(";")[0].strip()
        if "alternate" in a.get("rel", "").lower().split() and kind in FEED_TYPES and a.get("href"):
            self.found.append(a["href"])


def _named_feed(raw, base):
    """The address of the first feed a page names, or None."""
    p = _Alternates()
    p.feed(raw[: 2 * 2**20].decode("utf-8", "replace"))
    p.close()
    for href in p.found:
        url = urllib.parse.urljoin(base, href)
        if urllib.parse.urlsplit(url).scheme in ("http", "https"):
            return url
    return None


def _page(ctype, raw):
    """Whether what came back is a web page rather than a feed (some servers call their feeds text/html)."""
    head = raw[:1024].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith((b"<!doctype html", b"<html")):
        return True
    kind = (ctype or "").split(";")[0].strip().lower()
    return kind in ("text/html", "application/xhtml+xml") and not head.startswith((b"<?xml", b"<rss", b"<feed", b"<rdf"))


def read(cfg, src, bodies=False):
    """The feed as last read: {title, site, entries: {file: its entry in a listing}, bodies: {file: its Atom entry
    document} or None}. Reused for FEED_SECONDS; after that asked for again with its ETag and Last-Modified, and kept
    if the server says it hasn't changed. With `bodies`, the posts themselves are wanted: read again in full when they
    have left memory (KEEP_SECONDS)."""
    from . import feeds  # it imports this

    url, now = _url(cfg, src), time.monotonic()
    for h in list(_FEEDS.values()):
        if h["bodies"] is not None and now - h["read_at"] > KEEP_SECONDS:
            h["bodies"] = None
    hit = _FEEDS.get(src["id"])
    if hit and hit["url"] != url:
        hit = None
    whole = hit is not None and (hit["bodies"] is not None or not bodies)  # what's kept is all that's wanted
    if whole and now - hit["at"] < FEED_SECONDS:
        return hit

    def get(address, headers=None):
        # the source's password goes only to the server its address is on
        auth = _server(address) == _server(url)
        return feeds.get(cfg, src, address, "feed", ACCEPT, MAX_FEED, AGENT, headers, auth=auth)

    target = hit["found"] if hit else url
    cond = {k: v for k, v in (("If-None-Match", hit["etag"]), ("If-Modified-Since", hit["last_modified"])) if v} if whole else {}
    status, headers, raw, final = get(target, cond)
    if status == 304 and whole:
        hit["at"] = now
        return hit
    if _page(headers.get("Content-Type"), raw):
        named = _named_feed(raw, final) if target == url else None
        if not named:
            raise RuntimeError(
                "that address is a web page that names no news feed: give the feed's own address (it often ends in /feed, /rss or .xml)"
            )
        target = named
        status, headers, raw, final = get(target)
        if _page(headers.get("Content-Type"), raw):
            raise RuntimeError("the feed that page names is a web page too, not a news feed")
    feed = parse(raw, final, headers.get("Content-Type"))
    out = {
        "url": url,
        "found": target,
        "at": now,
        "read_at": now,
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
        "title": feed["title"],
        "site": feed["site"],
        "entries": {k: e for k, (e, _) in feed["posts"].items()},
        "bodies": {k: b for k, (_, b) in feed["posts"].items()},
    }
    _FEEDS[src["id"]] = out
    return out


def _feedparser(raw, headers=None):
    import feedparser  # loaded when a feed is read: a server without feeds never needs it

    # from a stream: given a string, feedparser would fetch an address, or open a file of that name
    return feedparser.parse(io.BytesIO(raw), response_headers=headers or {})


def parse(raw, base, ctype=None):
    """A feed's title, site and posts, {title, site, posts: {file: (entry, Atom entry document)}}, from its bytes;
    `base` is the address it came from, which relative links are read against (without its query, so a private feed's
    key stays out of what is kept)."""
    u = urllib.parse.urlsplit(base)
    base = urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, "", ""))
    d = _feedparser(raw, {"content-location": base, **({"content-type": ctype} if ctype else {})})
    if not d.get("version") and not d.get("entries"):
        raise RuntimeError("that address doesn't give a news feed (RSS or Atom)")
    feed = d.get("feed") or {}
    title = _plain(feed.get("title")) or u.hostname or "News feed"
    site = _href(feed.get("link"))
    feed_id = _plain(feed.get("id")) or site
    posts = {}
    for e in d.get("entries") or []:
        if len(posts) >= MAX_POSTS:
            break
        p = _post(e)
        if p is None:
            continue
        rel = f"{hashlib.sha1(p['id'].encode()).hexdigest()[:20]}.atom"
        if rel in posts:  # the same post twice: the first (newest) one
            continue
        body = _atom(p, title, site, feed_id)
        day = (p["published"] or p["updated"] or "")[:10]
        name = re.sub(r"[\x00-\x1f\x7f/\\]", "", f"{day} {p['title']}".strip())[:150].strip()
        entry = {
            "path": rel,
            "rel": rel,
            "name": f"{name or 'Post'}.atom",
            "dir": False,
            "size": len(body),
            "modified": p["published"] or p["updated"] or EPOCH,
            "title": p["title"] or None,
            "when": p["published"] or p["updated"],
            "settled": True,  # a post is whole once its feed lists it: no waiting for it to settle
            "post": _listed(p, title, site),
        }
        posts[rel] = (entry, body)
    return {"title": title, "site": site, "posts": posts}


def _listed(p, feed_title, site):
    """What a listing says of a post beside its file, kept on its resource as `post` (sources._post_of) for automations
    and the timeline: its address, the feed and site it came from, who wrote it, when, and its categories."""
    who = next((a["name"] or a["email"] for a in p["authors"] if a["name"] or a["email"]), None)
    out = {
        "href": p["page"],
        "feed": feed_title,
        "site": site,
        "author": who,
        "published": p["published"] or p["updated"],
        "categories": p["categories"][:10],
    }
    return {k: v for k, v in out.items() if v}


def _iso(t):
    try:
        return dt.datetime.fromtimestamp(calendar.timegm(t), dt.UTC).isoformat(timespec="seconds") if t else None
    except (OverflowError, ValueError, TypeError):
        return None


def _post(e):
    """What a feed's entry says of a post, tidied: None when it has nothing to tell it by."""
    links = [l for l in e.get("links") or [] if isinstance(l, dict)]
    page = _href(e.get("link")) or next((_href(l.get("href")) for l in links if l.get("rel") == "alternate" and _href(l.get("href"))), None)
    enclosures = [
        {"href": _href(l.get("href")), "type": _plain(l.get("type"))[:100], "length": _plain(l.get("length"))[:20]}
        for l in links
        if l.get("rel") == "enclosure" and _href(l.get("href"))
    ][:20]
    title = e.get("title") or ""
    if "html" in str((e.get("title_detail") or {}).get("type") or ""):
        title = _html_text(title)
    title = _plain(title)[:300]
    content = next((c for c in e.get("content") or [] if isinstance(c, dict) and c.get("value")), {})
    summary = e.get("summary") or ""
    summary_type = str((e.get("summary_detail") or {}).get("type") or "text/html")
    if summary == content.get("value"):  # feedparser repeats an Atom entry's content as its summary
        summary = ""
    authors = []
    for a in e.get("authors") or ([{"name": e.get("author")}] if e.get("author") else []):
        if isinstance(a, dict) and (a.get("name") or a.get("email")):
            authors.append({"name": _plain(a.get("name"))[:200], "email": _plain(a.get("email"))[:200], "uri": _href(a.get("href"))})
    published, updated = _iso(e.get("published_parsed")), _iso(e.get("updated_parsed"))
    text_of = content.get("value") or summary
    pid = _plain(e.get("id")) or page
    if not pid and (title or text_of):
        pid = "sha1:" + hashlib.sha1(f"{title}\n{published}\n{text_of}".encode()).hexdigest()
    if not pid:
        return None
    return {
        "id": pid[:2000],
        "title": title,
        "page": page,
        "enclosures": enclosures,
        "published": published,
        "updated": updated,
        "authors": authors[:20],
        "categories": [
            c for c in dict.fromkeys(_plain(t.get("label") or t.get("term"))[:100] for t in e.get("tags") or [] if isinstance(t, dict)) if c
        ][:30],
        "summary": summary,
        "summary_type": "html" if "html" in summary_type else "text",
        "content": content.get("value") or "",
        "content_type": "html" if "html" in str(content.get("type") or "text/html") else "text",
    }


def _atom(p, feed_title, site, feed_id):
    """A post as an Atom entry document of its own (RFC 4287 §4.1.2), the feed it came from as its <source>."""

    def add(parent, tag, text=None, **attrs):
        el = ET.SubElement(parent, tag, {k: _xml(v) for k, v in attrs.items() if v})
        if text:
            el.text = _xml(text)
        return el

    root = ET.Element("entry", {"xmlns": ATOM})
    add(root, "id", p["id"])
    add(root, "title", p["title"] or "(no title)", type="text")
    if p["page"]:
        add(root, "link", rel="alternate", href=p["page"])
    for x in p["enclosures"]:
        add(root, "link", rel="enclosure", href=x["href"], type=x["type"], length=x["length"])
    if p["published"]:
        add(root, "published", p["published"])
    add(root, "updated", p["updated"] or p["published"] or EPOCH)
    for a in p["authors"]:
        el = add(root, "author")
        add(el, "name", a["name"] or a["email"])
        if a["email"]:
            add(el, "email", a["email"])
        if a["uri"]:
            add(el, "uri", a["uri"])
    for c in p["categories"]:
        add(root, "category", term=c)
    if p["summary"]:
        add(root, "summary", p["summary"], type=p["summary_type"])
    if p["content"]:
        add(root, "content", p["content"], type=p["content_type"])
    source = add(root, "source")
    if feed_id:
        add(source, "id", feed_id)
    add(source, "title", feed_title, type="text")
    if site:
        add(source, "link", rel="alternate", href=site)
    ET.indent(root)
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="utf-8") + b"\n"


# ---------- a post as text ----------
def _who(a):
    return f"{a['name']} ({a['email']})" if a["name"] and a["email"] and a["name"] != a["email"] else a["name"] or a["email"]


def text(raw):
    """A post's file, or any RSS or Atom feed (one section per post), as Markdown: the post's title as its heading,
    'Label — value' lines (its address, who wrote it, when, the feed, its categories and enclosures), then its text."""
    d = _feedparser(raw)
    feed = d.get("feed") or {}
    out = []
    for e in d.get("entries") or []:
        p = _post(e)
        if p is None:
            continue
        source = e.get("source") or {}
        name, site = _plain(source.get("title") or feed.get("title")), _href(source.get("link") or feed.get("link"))
        rows = [
            ("Link", p["page"]),
            ("By", ", ".join(_who(a) for a in p["authors"])),
            ("Published", p["published"]),
            ("Updated", p["updated"] if p["updated"] not in (p["published"], EPOCH) else None),
            ("Feed", f"{name} ({site})" if name and site else name or site),
            ("Categories", ", ".join(p["categories"])),
            ("Enclosures", ", ".join(x["href"] + (f" ({x['type']})" if x["type"] else "") for x in p["enclosures"])),
        ]
        body = p["content"] or p["summary"]
        kind = p["content_type"] if p["content"] else p["summary_type"]
        body = _html_text(body) if kind == "html" else body.strip()
        head = [f"{k} — {v}" for k, v in rows if v]
        out.append("\n".join([f"# {p['title'] or '(no title)'}", "", *head, "", body]).strip())
    if not out:
        raise SystemExit("no posts found: that isn't a news feed or a post of one (RSS or Atom)")
    return "\n\n".join(out) + "\n"


# ---------- what feeds.py calls ----------
def browse(cfg, src, path):
    if path:
        raise ValueError("a news feed has no folders")
    return sorted(read(cfg, src)["entries"].values(), key=lambda e: e["when"] or "", reverse=True)


def list_files(cfg, src, path, cursor=None):
    return browse(cfg, src, path)


def fetch(cfg, src, path):
    body = read(cfg, src, bodies=True)["bodies"].get(path)
    if body is None:
        raise FileNotFoundError("that post isn't in the feed any more")
    return body


def immutable(src):
    return False  # a post can be edited


def forget(src):
    """Drop what was read of this source's feed, so the next call fetches it whole."""
    _FEEDS.pop(src["id"], None)


def test(cfg, src):
    forget(src)
    read(cfg, src)


# ---------- subscribing in one step: a feed as a source and a watch (MCP, the assistant) ----------
def _watching(w, names):
    return {
        "watch": w["id"],
        "namespace": names.get(w["space"]),
        "enabled": w.get("enabled", True),
        "backfill": bool(w.get("backfill")),
        "last_scan_at": w.get("last_scan_at"),
        "next_scan_at": w.get("next_scan_at"),
        "last_error": w.get("last_error"),
        "new_last_scan": (w.get("last_stats") or {}).get("new"),
    }


def subscriptions(db):
    """Every news feed, the namespaces it's watched into and how its last scan went: [{feed, name, title, health,
    watching: [...]}]. Their addresses stay sealed. `title` is the feed's own, once read since Lens started."""
    from . import sources, store

    names, watches = store.space_names(db), sources.list_watches(db)
    out = []
    for s in sources.list_sources(db):
        if s["type"] != "rss":
            continue
        hit = _FEEDS.get(s["id"])
        out.append(
            {
                "feed": s["id"],
                "name": s["name"],
                "title": hit["title"] if hit else None,
                "health": s.get("health"),
                "watching": [_watching(w, names) for w in watches if w["source"] == s["id"]],
            }
        )
    return out


def subscribe(db, cfg, url, namespace, name=None, backfill=False, by=None, user=None, password=None):
    """Subscribe `namespace` (made if it's new) to the news feed at `url` (or the feed a site's page names): a news feed
    source, checked before it's kept, named by the feed's title unless `name` says, and a watch that brings each new
    post in as text, and with `backfill` those it lists already. A feed already a source is reused, and a namespace
    already watching it is told so: {feed, name, title, watch, namespace, posts, added, already}."""
    from . import sources, store

    try:
        url = address(url)
    except RuntimeError as e:
        raise ValueError(str(e)) from None
    ns = str(namespace or "").strip().lower()
    if not store.NS_RX.match(ns):
        raise ValueError("a namespace's name uses lowercase letters, digits, - and _")
    same = None
    for s in db.rows("SELECT record::id(id) AS id, type, sealed FROM storage_source WHERE type = 'rss'"):
        try:
            if _url(cfg, s) == url:
                same = s["id"]
                break
        except (RuntimeError, ValueError):
            continue
    added = same is None
    if added:
        secrets = {"url": url, **({"pass": password} if password else {})}
        sid = sources.create(db, cfg, (name or "News feed")[:80], "rss", {"user": user} if user else {}, secrets, by)
        health = sources.test(db, cfg, sid)
        if not health["ok"]:
            sources.remove(db, sid)
            raise ValueError(f"that feed can't be read, so nothing was added: {health['error']}")
    else:
        sid = same
    try:
        feed = read(cfg, sources.get(db, sid))
    except RuntimeError as e:
        raise ValueError(f"that feed can't be read right now: {e}") from None
    space = store.ns_id(db, ns)
    if added and not name and feed["title"]:
        db.q("UPDATE $r SET name = $n", r=store.R("storage_source", sid), n=feed["title"][:80])
    have = next((w for w in sources.list_watches(db) if w["source"] == sid and w["space"] == space), None)
    wid = have["id"] if have else sources.create_watch(db, cfg, sid, "", space, by, backfill=bool(backfill))
    return {
        "feed": sid,
        "name": sources.get(db, sid)["name"],
        "title": feed["title"],
        "watch": wid,
        "namespace": ns,
        "posts": len(feed["entries"]),
        "added": added,
        "already": have is not None,
    }


def unsubscribe(db, sid):
    """Stop following a news feed: its source and its watches go; the posts it brought in stay."""
    from . import sources

    if sources.get(db, sid)["type"] != "rss":
        raise ValueError("that source isn't a news feed")
    sources.remove(db, sid)
    _FEEDS.pop(sid, None)
