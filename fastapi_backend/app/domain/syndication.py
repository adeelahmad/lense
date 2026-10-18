"""Published feeds (docs/feeds.md): RSS 2.0 feeds and podcast feeds that Lens writes, for feed readers and podcast apps
to follow. Routines fill them through a workflow's publish nodes; people and agents add to them through the API and MCP.

A feed (`feed:<n>`) belongs to one namespace: its editors make, change and delete it, and its readers see it. Its kind
is `rss` (any items) or `podcast` (RSS with Apple Podcasts' itunes tags, listing only items with audio). It is
private: its address carries a token (`lfd_...`), which is all a reader needs. The token is kept sealed with the server's
key, so the namespace's readers can see the address again, and is found by its hash; `rotate` gives a new one and the
old address stops working at once. A feed asks podcast directories not to list it (itunes:block, podcast:locked) and
search engines not to index it.

An item (`feed_item:<n>`) is one of:

- `asset`: a resource (a recording, a document, a page...): its title and summary, a link to its page in the web app,
  and its audio (or video) as the enclosure when it has some;
- `episode`: a podcast episode (podcasts.py), listed once it's made: its audio as the enclosure, the outline's
  summary, and the sources it cites;
- `activity`: something on the timeline (events.py): its summary, when it happened, its kind, and a link to what it is
  about;
- `text`: a title, text and a link that someone (or a workflow) wrote.

An item is kept once per feed for what it's about (its `guid`), so a routine that runs again adds only what's new; the
newest KEEP items are kept. What an item may show is bounded by who can already read the feed's namespace: its asset or
activity is in that namespace, or in one that everyone who reads the feed's namespace can read too, and never in a
vault. A resource is read when the feed is (its title as it is now, its audio if it's still there), so one deleted
since drops out. Text is written escaped twice over: once for the XML, and once for readers that show an item's
description as HTML, so nothing in it can become markup.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import os
import pathlib
import re
import secrets
import urllib.parse
from xml.sax.saxutils import escape, quoteattr

from . import events, keyring, podcasts, render, settings, store, video

R = store.R
KINDS = ("rss", "podcast")
ITEM_KINDS = ("asset", "episode", "activity", "text")
TOKEN_PREFIX = "lfd_"  # recognisable in logs and secret scanners
TOKEN_RX = re.compile(r"^lfd_[A-Za-z0-9_-]{20,64}$")
PATH = "/api/v1/feeds/public/"
LANG_RX = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
DEFAULTS = {"language": "en", "category": "Education", "explicit": False, "max_items": 50}
MAX_ITEMS = 500  # the most items a feed lists
KEEP = 1000  # items kept per feed, newest first
TITLE_MAX, TEXT_MAX, DESCRIPTION_MAX = 200, 4000, 2000
SEEN_SECONDS = 60  # a feed's last read is written at most this often
TTL_MINUTES = 30  # how long readers may keep a copy before asking again
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "podcast": "https://podcastindex.org/namespace/1.0",
}
FIELDS = (
    "record::id(id) AS id, space, kind, title, description, author, language, category, explicit, image, max_items, "
    "created_by, created_at, updated_at, fetched_at"
)
ITEM_FIELDS = (
    "record::id(id) AS id, feed, kind, guid, title, summary, link, published_at, recording, activity, categories, origin, by, created_at"
)
# where a timeline object's lens: reference opens in the web app (components/timeline/model.ts objectHref)
PAGES = {
    "recording": "/resources/{id}",
    "sensor": "/sensors",
    "storage_source": "/sources",
    "chat": "/chat/{id}",
    "entity": "/entities/{id}",
    "topic": "/topics/{id}",
    "automation_run": "/activity",
}
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _context(fid):
    return f"feed:{int(fid)}:token"


def _text(v, most=None):
    """Text without characters XML can't hold, trimmed (to `most` characters)."""
    s = _XML_BAD.sub("", str(v or "")).strip()
    return s[:most] if most else s


def _line(v, most=None):
    return " ".join(_text(v).split())[:most] if most else " ".join(_text(v).split())


def _href(v):
    """An http or https address, or None."""
    v = _line(v)
    return v if v and urllib.parse.urlsplit(v).scheme in ("http", "https") and len(v) <= 2000 else None


# ---------- feeds ----------
def _check(d):
    """A feed's settings, checked: ValueError naming the first problem."""
    out = {}
    if "kind" in d:
        if d["kind"] not in KINDS:
            raise ValueError(f"a feed's kind is {' or '.join(KINDS)}")
        out["kind"] = d["kind"]
    if "title" in d:
        if not _line(d["title"]):
            raise ValueError("give the feed a title")
        out["title"] = _line(d["title"], TITLE_MAX)
    if "description" in d:
        out["description"] = _text(d["description"], DESCRIPTION_MAX) or None
    if "author" in d:
        out["author"] = _line(d["author"], TITLE_MAX) or None
    if "language" in d:
        lang = _line(d["language"]) or DEFAULTS["language"]
        if not LANG_RX.match(lang):
            raise ValueError("language is a language tag, like en or en-GB")
        out["language"] = lang
    if "category" in d:
        out["category"] = _line(d["category"], 60) or None
    if "explicit" in d:
        out["explicit"] = bool(d["explicit"])
    if "image" in d:
        img = d["image"]
        if img and not (_href(img) or "").startswith("https://"):
            raise ValueError("the image is an https:// address (podcast apps fetch it)")
        out["image"] = _href(img) if img else None
    if "max_items" in d:
        n = d["max_items"]
        if n is not None and (not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_ITEMS):
            raise ValueError(f"a feed lists 1 to {MAX_ITEMS} items")
        out["max_items"] = n or DEFAULTS["max_items"]
    return out


def create(db, cfg, space, title, kind="rss", by=None, **opts):
    """A new feed in namespace `space`; its id. ValueError for a vault, or settings that don't fit."""
    if int(space) not in store.space_names(db):
        raise KeyError(space)
    if podcasts._vault(db, int(space)):
        raise ValueError("a vault's resources are opened with a passkey, so a vault can't publish a feed")
    d = _check({"title": title, "kind": kind, **{k: v for k, v in opts.items() if v is not None}})
    fid, token, t = db.next_id("feed"), TOKEN_PREFIX + secrets.token_urlsafe(32), store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("feed", fid),
        d=store.clean(
            {
                **{k: v for k, v in DEFAULTS.items() if kind == "podcast" or k != "category"},
                **d,
                "space": int(space),
                "token_hash": _hash(token),
                "token": settings.seal(cfg, token, _context(fid)),
                "created_by": by,
                "created_at": t,
                "updated_at": t,
            }
        ),
    )
    return fid


def update(db, fid, **changes):
    get(db, fid)
    d = _check(changes)
    sets = {k: v for k, v in d.items() if v is not None}
    db.q("UPDATE $r MERGE $d", r=R("feed", int(fid)), d={**sets, "updated_at": store.now()})
    for k in (k for k, v in d.items() if v is None):
        db.q(f"UPDATE $r SET {k} = NONE", r=R("feed", int(fid)))


def remove(db, fid):
    """Delete a feed and its items: its address stops working at once."""
    get(db, fid)
    db.run(["DELETE feed_item WHERE feed = $f", "DELETE $r"], f=int(fid), r=R("feed", int(fid)))


def get(db, fid):
    """A feed's row (without its token); KeyError when there's none."""
    row = db.one(f"SELECT {FIELDS} FROM $r", r=R("feed", int(fid)))
    if not row:
        raise KeyError(fid)
    return row


def list_feeds(db, spaces=None):
    """Feeds in namespaces `spaces` (None: every one), oldest first, each with how many items it holds."""
    rows = db.rows(
        f"SELECT {FIELDS} FROM feed" + (" WHERE space IN $s" if spaces is not None else "") + " ORDER BY id", s=sorted(spaces or [])
    )
    return [{**r, **_fill(db, r["id"])} for r in rows]


def _fill(db, fid):
    """How many items a feed holds, and when the newest was published."""
    n = db.one("SELECT count() AS n FROM feed_item WHERE feed = $f GROUP ALL", f=int(fid)) or {}
    last = db.values("SELECT VALUE published_at FROM feed_item WHERE feed = $f ORDER BY published_at DESC LIMIT 1", f=int(fid))
    return {"items": n.get("n", 0), "last_item_at": last[0] if last else None}


def view(db, cfg, row, base):
    """A feed as people and agents see it: its settings, its namespace's name, its address, and how full it is."""
    if "items" not in row:
        row = {**row, **_fill(db, row["id"])}
    token = token_of(db, cfg, row["id"])
    return store.clean(
        {
            **row,
            "namespace": store.space_names(db).get(row["space"]),
            "explicit": bool(row.get("explicit")),
            "max_items": row.get("max_items") or DEFAULTS["max_items"],
            "url": address(base, token),
            "path": PATH + token,
            "page": f"{base}/feeds/{row['id']}",
        }
    )


def token_of(db, cfg, fid):
    row = db.one("SELECT token FROM $r", r=R("feed", int(fid)))
    if not row:
        raise KeyError(fid)
    return settings.unseal(cfg, row["token"], _context(fid))


def rotate(db, cfg, fid):
    """A new token for a feed: the old address stops working at once. The new token."""
    get(db, fid)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    db.q(
        "UPDATE $r SET token_hash = $h, token = $t, updated_at = $u",
        r=R("feed", int(fid)),
        h=_hash(token),
        t=settings.seal(cfg, token, _context(fid)),
        u=store.now(),
    )
    return token


def address(base, token):
    return f"{base.rstrip('/')}{PATH}{token}"


def by_token(db, token):
    """The feed whose token this is; KeyError for any other."""
    if not isinstance(token, str) or not TOKEN_RX.match(token):
        raise KeyError("token")
    row = db.one(f"SELECT {FIELDS} FROM feed WHERE token_hash = $h LIMIT 1", h=_hash(token))
    if not row or podcasts._vault(db, row["space"]):  # a namespace made a vault since: its feeds stop
        raise KeyError("token")
    return row


def seen(db, feed, now=None):
    """Note that a reader fetched the feed (at most once a minute, so readers that poll often don't write each time)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    last = feed.get("fetched_at")
    if last and (now - events._parse(last)).total_seconds() < SEEN_SECONDS:
        return
    db.q("UPDATE $r SET fetched_at = $t", r=R("feed", feed["id"]), t=now.isoformat(timespec="seconds"))


# ---------- what may go in ----------
class Fit:
    """Whether what's in a namespace may show in a feed of namespace `space`: the same namespace, or one everyone who
    reads the feed's can read too, and not a vault (podcasts.target's rule for where an episode may go)."""

    def __init__(self, db, space):
        self.db, self.space, self._ok = db, int(space), {}
        self._readers = None

    def __call__(self, sid):
        if sid is None:
            return False
        sid = int(sid)
        if sid == self.space:
            return True
        if sid not in self._ok:
            if self._readers is None:
                self._readers = podcasts._readers(self.db, self.space)
            self._ok[sid] = not podcasts._vault(self.db, sid) and self._readers <= podcasts._readers(self.db, sid, collections=False)
        return self._ok[sid]


def entry_from(value):
    """What a workflow passes on, as something to publish: a resource ({id, title...} as select nodes pass them, a
    {recording: id}, or an id), an activity (anything with a kind, published and lens), or text ({title, summary or
    text, link}). None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return {"recording": value}
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("kind"), str) and isinstance(value.get("lens"), dict) and value.get("published"):
        return {"activity": value}
    for k in ("recording", "episode"):
        if isinstance(value.get(k), int) and not isinstance(value.get(k), bool):
            return {"recording": value[k]}
    if isinstance(value.get("id"), int) and not isinstance(value["id"], bool) and ("title" in value or "recorded_at" in value):
        return {"recording": value["id"]}
    text = value.get("summary") if value.get("summary") is not None else value.get("text")
    if value.get("title") or text:
        return store.clean({"title": value.get("title"), "summary": text, "link": value.get("link"), "guid": value.get("guid")})
    return None


def _guid_text(e):
    return "text/" + (
        _line(e.get("guid"), 120) or hashlib.sha256(f"{e.get('title')}\n{e.get('summary')}\n{e.get('link')}".encode()).hexdigest()[:32]
    )


def _activity_item(a):
    lens = a.get("lens") or {}
    obj = a.get("object") or {}
    actor = (a.get("actor") or {}).get("name")
    kind = a.get("kind")
    label = (events.kind_info(kind) or {}).get("label") if kind in {k["name"] for k in events.kinds()} else None
    about = _line(obj.get("name"), TITLE_MAX)
    summary = " · ".join(x for x in (label or kind, f"by {actor}" if actor else None, about or None) if x)
    return store.clean(
        {
            "kind": "activity",
            "guid": f"activity/{a['id']}",
            "title": _line(a.get("summary"), TITLE_MAX) or kind,
            "summary": summary,
            "link": obj.get("url") if isinstance(obj.get("url"), str) else None,
            "published_at": events._parse(a["published"]).isoformat(timespec="seconds"),
            "activity": store.clean(
                {
                    "id": a["id"],
                    "kind": kind,
                    "object": obj.get("id") if isinstance(obj.get("id"), str) else None,
                    "source": lens.get("source"),
                }
            ),
            "categories": [kind] if kind else None,
        }
    )


def publish(db, cfg, fid, entries, origin=None, by=None):
    """Add `entries` (entry_from's shapes) to feed `fid`: {added: [item ids], already, refused: [{what, why}]}. An entry
    already in the feed isn't added again; one that may not show there is refused, with why."""
    feed = get(db, fid)
    fits = Fit(db, feed["space"])
    have = set(db.values("SELECT VALUE guid FROM feed_item WHERE feed = $f", f=feed["id"]))
    out = {"added": [], "already": 0, "refused": []}
    t = store.now()
    for e in entries or []:
        item, why = None, None
        if not isinstance(e, dict):
            why, what = "not something a feed can list", str(e)[:80]
        elif e.get("recording") is not None:
            what = f"resource {e['recording']}"
            rec = db.one("SELECT space, title FROM $r", r=R("recording", int(e["recording"])))
            if not rec:
                why = "no such resource"
            elif not fits(rec["space"]):
                why = "it's in a namespace not everyone who reads the feed can read"
            else:
                episode = bool(db.one("SELECT id FROM $r", r=R("podcast", int(e["recording"]))))
                item = {
                    "kind": "episode" if episode else "asset",
                    "guid": f"recording/{int(e['recording'])}",
                    "title": _line(rec.get("title"), TITLE_MAX) or None,
                    "recording": int(e["recording"]),
                    "published_at": t,
                }
        elif e.get("activity") is not None:
            a = e["activity"]
            what = f"activity {a.get('id') if isinstance(a, dict) else a}"
            if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a.get("published"):
                why = "not an activity"
            elif ((a.get("lens") or {}).get("audience") or "space") != "space":
                why = "it's only for its owner or admins"
            elif not events.spaces_of(a) or not all(fits(s) for s in events.spaces_of(a)):
                why = "it's in a namespace not everyone who reads the feed can read"
            else:
                item = _activity_item(a)
        else:
            what = f"text {_line(e.get('title'), 60)!r}"
            title, text = _line(e.get("title"), TITLE_MAX), _text(e.get("summary"), TEXT_MAX)
            if not title and not text:
                why = "give it a title or some text"
            elif e.get("link") and not _href(e["link"]):
                why = "a link is an http or https address"
            else:
                item = store.clean(
                    {
                        "kind": "text",
                        "guid": _guid_text(e),
                        "title": title or _line(text, 80),
                        "summary": text or None,
                        "link": _href(e.get("link")),
                        "published_at": t,
                    }
                )
        if item is None:
            out["refused"].append({"what": what, "why": why})
            continue
        if item["guid"] in have:
            out["already"] += 1
            continue
        iid = db.next_id("feed_item")
        db.q(
            "CREATE $r CONTENT $d",
            r=R("feed_item", iid),
            d=store.clean(
                {**item, "feed": feed["id"], "key": f"{feed['id']}:{item['guid']}", "origin": origin or None, "by": by, "created_at": t}
            ),
        )
        have.add(item["guid"])
        out["added"].append(iid)
    if out["added"]:
        _prune(db, feed["id"])
    return out


def _prune(db, fid):
    """Keep a feed's newest KEEP items."""
    old = db.rows("SELECT id, published_at FROM feed_item WHERE feed = $f ORDER BY published_at DESC, id DESC START $k", f=int(fid), k=KEEP)
    if old:
        db.q("DELETE $ids", ids=[r["id"] for r in old])


def items(db, fid, limit=50, offset=0):
    """A feed's items, newest first."""
    return db.rows(
        f"SELECT {ITEM_FIELDS} FROM feed_item WHERE feed = $f ORDER BY published_at DESC, id DESC LIMIT $l START $o",
        f=int(fid),
        l=int(limit),
        o=int(offset),
    )


def remove_item(db, fid, iid):
    got = db.rows("DELETE $r WHERE feed = $f RETURN BEFORE", r=R("feed_item", int(iid)), f=int(fid))
    if not got:
        raise KeyError(iid)


# ---------- media ----------
def media(db, cfg, rid):
    """(content type, size in bytes) of a resource's audio or video, for an enclosure; None without any (or when it
    can't be read)."""
    rec = db.one("SELECT source, path, remote, size, media FROM $r", r=R("recording", int(rid))) or {}
    if rec.get("source") != "audio":
        return None
    is_video = (rec.get("media") or {}).get("kind") == "video"
    path = render.has_audio(db, cfg, int(rid))
    name = path or (rec.get("remote") or {}).get("path") or ""
    ext = pathlib.PurePosixPath(str(name)).suffix.lower()
    ctype = (video.VIDEO_TYPES.get(ext) if is_video else None) or render.AUDIO_TYPES.get(ext) or "audio/mpeg"
    if path:
        try:
            size = keyring.plain_size(db, cfg, path) if keyring.is_encrypted(path) else os.path.getsize(path)
        except (keyring.Locked, keyring.Damaged, OSError):
            return None
        return ctype, size
    if rec.get("remote") and rec.get("size"):
        return ctype, int(rec["size"])
    return None


def item_recording(db, feed, iid):
    """The recording behind item `iid` of `feed`, for its enclosure; KeyError when it has none (or may not show)."""
    it = db.one("SELECT recording FROM $r WHERE feed = $f", r=R("feed_item", int(iid)), f=feed["id"])
    if not it or it.get("recording") is None:
        raise KeyError(iid)
    rec = db.one("SELECT * FROM $r", r=R("recording", int(it["recording"])))
    if not rec or not Fit(db, feed["space"])(rec.get("space")):
        raise KeyError(iid)
    return int(it["recording"]), rec


# ---------- the feed itself ----------
def _rfc822(t):
    return email.utils.format_datetime(events._parse(t), usegmt=True)


def _duration(ms):
    s = max(0, int((ms or 0) // 1000))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _el(tag, text=None, **attrs):
    a = "".join(f" {k.replace('__', ':')}={quoteattr(str(v))}" for k, v in attrs.items() if v is not None)
    return f"<{tag}{a}/>" if text is None else f"<{tag}{a}>{escape(_text(text))}</{tag}>"


def _html(text):
    """Plain text as the HTML a reader shows: escaped, with its line breaks kept."""
    return "<br>".join(html.escape(line) for line in _text(text).splitlines())


def _object_link(base, obj):
    url = obj.get("url") if isinstance(obj, dict) else None
    if isinstance(url, str) and url.startswith("/") and not url.startswith("//"):
        return base + url
    if _href(url):
        return _href(url)
    m = re.match(r"^lens:([a-z_]+)/(.+)$", obj.get("id") or "" if isinstance(obj, dict) else "")
    if m and m.group(1) in PAGES:
        return base + PAGES[m.group(1)].format(id=urllib.parse.quote(m.group(2), safe=""))
    return None


def _shown(db, cfg, feed, base, token):
    """The items a reader sees, newest first, each as {title, link, guid, published, description, categories,
    enclosure}: resources read as they are now, episodes once they're made, and only items with audio in a podcast
    feed."""
    fits, out = Fit(db, feed["space"]), []
    limit = feed.get("max_items") or DEFAULTS["max_items"]
    rows = items(db, feed["id"], limit=min(KEEP, limit * 3 if feed["kind"] == "podcast" else limit))
    rids = [r["recording"] for r in rows if r.get("recording") is not None]
    recs = (
        {
            r["id"]: r
            for r in db.rows(
                "SELECT record::id(id) AS id, space, title, summary, duration_ms, recorded_at FROM recording WHERE id IN $ids",
                ids=[R("recording", x) for x in rids],
            )
        }
        if rids
        else {}
    )
    for it in rows:
        if len(out) >= limit:
            break
        shown = {
            "title": it.get("title"),
            "guid": f"lens:feed-{feed['id']}:{it['guid']}",
            "published": it["published_at"],
            "categories": it.get("categories") or [],
            "description": it.get("summary") or "",
            "link": it.get("link"),
            "enclosure": None,
        }
        rid = it.get("recording")
        if rid is not None:
            rec = recs.get(rid)
            if not rec or not fits(rec["space"]):
                continue  # deleted, or moved somewhere the feed's readers can't read
            shown["title"] = rec.get("title") or shown["title"] or "Untitled"
            shown["link"] = f"{base}/resources/{rid}"
            shown["description"] = ((rec.get("summary") or {}).get("summary") if isinstance(rec.get("summary"), dict) else None) or ""
            if it["kind"] == "episode":
                p = db.one("SELECT status, outline, sources FROM $r", r=R("podcast", rid)) or {}
                if p.get("status") not in ("ready", "script_only"):
                    continue  # still being made, or failed
                shown["description"] = _episode_text(p, base) or shown["description"]
            got = media(db, cfg, rid)
            if got:
                shown["enclosure"] = {"url": f"{address(base, token)}/items/{it['id']}/audio", "type": got[0], "length": got[1]}
                shown["duration_ms"] = rec.get("duration_ms")
        elif it["kind"] == "activity":
            obj = {"id": (it.get("activity") or {}).get("object"), "url": it.get("link")}
            shown["link"] = _object_link(base, obj) or f"{base}/timeline"
        if feed["kind"] == "podcast" and not shown["enclosure"]:
            continue
        out.append(shown)
    return out


def _episode_text(p, base):
    """An episode's description: the outline's summary (or its key ideas), then the sources it draws on."""
    o = p.get("outline") or {}
    text = _text(o.get("summary")) or "\n".join(f"- {_line(i.get('point'))}" for i in (o.get("ideas") or [])[:6] if isinstance(i, dict))
    cited = []
    for s in p.get("sources") or []:
        rid = (s.get("ref") or {}).get("recording")
        if rid is not None and rid not in [c[1] for c in cited]:
            cited.append((_line(s.get("title"), TITLE_MAX) or "Untitled", rid))
    if cited:
        text += (
            ("\n\n" if text else "") + "Sources:\n" + "\n".join(f"{n}. {t}: {base}/resources/{rid}" for n, (t, rid) in enumerate(cited, 1))
        )
    return text


def render_xml(db, cfg, feed, base, token):
    """The feed as RSS 2.0 (with Apple Podcasts' tags for a podcast feed), UTF-8."""
    pod = feed["kind"] == "podcast"
    shown = _shown(db, cfg, feed, base, token)
    me = address(base, token)
    ns = f' xmlns:atom="{NS["atom"]}"' + (f' xmlns:itunes="{NS["itunes"]}" xmlns:podcast="{NS["podcast"]}"' if pod else "")
    built = max([s["published"] for s in shown] + [feed.get("updated_at") or feed.get("created_at") or store.now()])
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<rss version="2.0"{ns}>',
        "<channel>",
        _el("title", feed["title"]),
        _el("link", f"{base}/feeds/{feed['id']}"),
        _el("description", _html(feed.get("description") or feed["title"])),
        _el("language", feed.get("language") or DEFAULTS["language"]),
        _el("lastBuildDate", _rfc822(built)),
        _el("generator", "Lens"),
        _el("ttl", str(TTL_MINUTES)),
        _el("atom:link", href=me, rel="self", type="application/rss+xml"),
    ]
    if feed.get("image"):
        parts.append(f"<image>{_el('url', feed['image'])}{_el('title', feed['title'])}{_el('link', f'{base}/feeds/{feed["id"]}')}</image>")
    if pod:
        parts += [
            _el("itunes:author", feed.get("author") or "Lens"),
            _el("itunes:explicit", "true" if feed.get("explicit") else "false"),
            f"<itunes:category text={quoteattr(_line(feed.get('category') or DEFAULTS['category']))}/>",
            _el("itunes:type", "episodic"),
            _el("itunes:block", "Yes"),  # private: not listed in Apple Podcasts
            _el("podcast:locked", "yes"),  # nor imported by other platforms
        ]
        if feed.get("image"):
            parts.append(_el("itunes:image", href=feed["image"]))
    for s in shown:
        bits = [
            _el("title", s["title"] or "Untitled"),
            _el("guid", s["guid"], isPermaLink="false"),
            _el("pubDate", _rfc822(s["published"])),
        ]
        if s.get("link"):
            bits.append(_el("link", s["link"]))
        if s.get("description"):
            bits.append(_el("description", _html(s["description"])))
        bits += [_el("category", c) for c in s["categories"]]
        enc = s.get("enclosure")
        if enc:
            bits.append(_el("enclosure", url=enc["url"], length=str(enc["length"]), type=enc["type"]))
            if pod:
                if s.get("duration_ms"):
                    bits.append(_el("itunes:duration", _duration(s["duration_ms"])))
                bits.append(_el("itunes:episodeType", "full"))
        parts.append("<item>" + "".join(bits) + "</item>")
    parts += ["</channel>", "</rss>"]
    return "\n".join(parts).encode("utf-8")
