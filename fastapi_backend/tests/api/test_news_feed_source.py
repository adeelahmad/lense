"""News feeds (RSS and Atom) as a source: a feed's posts shown as files, browsed, watched and imported like any
source's. Everything runs against a feed served over HTTP from this process."""

from __future__ import annotations

import base64
import http.server
import json
import socket
import threading

import pytest

from app.domain import content_types, events, feeds, netguard, rss, sources, store
from tests.helpers import drain, login, make_user

R = store.R

POSTS = {
    "night": (
        "Night crossing",
        "Tue, 29 Sep 2026 09:30:00 +0100",
        "&lt;p&gt;The ships &lt;script&gt;steal()&lt;/script&gt;arrived &lt;a href='/pier' onclick='x()'&gt;at dawn&lt;/a&gt;."
        "&lt;/p&gt;&lt;p&gt;The lighthouse keeper wrote twice about the fog.&lt;/p&gt;",
    ),
    "buoys": ("New buoys", "Wed, 30 Sep 2026 14:00:00 +0000", "The new buoys are in, painted red."),
    "regatta": ("Autumn regatta", "Thu, 01 Oct 2026 08:00:00 +0000", "Two days on the water. Bring the logbook."),
}


def _rss(keys, edits=None):
    items = []
    for k in keys:
        title, date, text = POSTS[k]
        text = (edits or {}).get(k, text)
        items.append(
            f"<item><title>{title}</title><link>https://harbour.example.org/posts/{k}</link><guid isPermaLink='false'>harbour-{k}</guid>"
            f"<pubDate>{date}</pubDate><dc:creator>Mara Keane</dc:creator><category>Harbour</category>"
            f"<description>{text}</description></item>"
        )
    return (
        "<?xml version='1.0' encoding='utf-8'?><rss version='2.0' xmlns:dc='http://purl.org/dc/elements/1.1/'><channel>"
        "<title>Harbour news</title><link>https://harbour.example.org/</link><description>From the pier</description>"
        + "".join(reversed(items))
        + "</channel></rss>"
    )


PAGE = """<!doctype html><html><head><title>Harbour</title>
<link rel="stylesheet" href="/style.css"><link rel="alternate" type="application/rss+xml" title="Posts" href="{feed}">
</head><body><p>Welcome to the harbour.</p></body></html>"""


class _Site(http.server.BaseHTTPRequestHandler):
    feed = ""
    etag = '"v1"'
    auth: str | None = None
    feed_path = "/feed.xml?key=s3cret"  # where the page says the feed is: a private feed's address carries its key
    hits: list = []

    def do_GET(self):
        _Site.hits.append((self.path, self.headers.get("Host"), self.headers.get("Authorization"), self.headers.get("If-None-Match")))
        if self.path == "/blog":
            self._send(PAGE.format(feed=_Site.feed_path).encode(), "text/html; charset=utf-8")
        elif self.path == "/plain":
            self._send(b"<!doctype html><html><body>No feed here</body></html>", "text/html")
        elif self.path.split("?")[0] == "/feed.xml":
            if _Site.auth and self.headers.get("Authorization") != _Site.auth:
                self.send_response(401)
                self.end_headers()
            elif self.headers.get("If-None-Match") == _Site.etag:
                self.send_response(304)
                self.end_headers()
            else:
                self._send(_Site.feed.encode(), "application/rss+xml", _Site.etag)
        else:
            self.send_response(404)
            self.end_headers()

    def _send(self, body, ctype, etag=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def site(cfg, monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Site.feed, _Site.etag, _Site.auth, _Site.feed_path, _Site.hits = _rss(["night", "buoys"]), '"v1"', None, "/feed.xml?key=s3cret", []
    rss._FEEDS.clear()
    monkeypatch.setattr(netguard, "PORTS", netguard.PORTS | {srv.server_address[1]})  # the test server's port, as if 443
    cfg["documents"]["web_networks"] = ["127.0.0.0/8"]  # a network an admin allowed: the test server's
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def _segments(db, rid):
    return " ".join(s["text"] for s in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx", r=rid))


def _stale():
    for hit in rss._FEEDS.values():  # past FEED_SECONDS: the next listing asks the server again
        hit["at"] = 0


def test_a_news_feed_as_a_source(site, client, db, cfg, admin):  # site first: it sets what the app reads
    h = admin
    assert client.get("/api/v1/sources/backends", headers=h).json()["rss"]["label"] == "News feed (RSS or Atom)"
    # the blog's page, not its feed: the feed it names is read
    r = client.post("/api/v1/sources", headers=h, json={"name": "harbour", "type": "rss", "secrets": {"url": f"{site}/blog"}})
    assert r.status_code == 200 and r.json()["health"]["ok"], r.text
    sid = r.json()["id"]
    src = client.get("/api/v1/sources", headers=h).json()[0]
    assert (src["type"], src["label"], src["secrets"]["url"]) == ("rss", "News feed (RSS or Atom)", {"secret": True, "set": True})

    entries = client.get(f"/api/v1/sources/{sid}/browse", headers=h).json()
    assert [e["name"] for e in entries] == ["2026-09-30 New buoys.atom", "2026-09-29 Night crossing.atom"]
    night = entries[1]
    assert (night["title"], night["when"], night["modified"], night["dir"]) == (
        "Night crossing",
        "2026-09-29T08:30:00+00:00",
        "2026-09-29T08:30:00+00:00",
        False,
    )
    assert site not in client.get(f"/api/v1/sources/{sid}/browse", headers=h).text  # the address stays sealed
    assert client.get(f"/api/v1/sources/{sid}/browse", params={"path": "x"}, headers=h).status_code == 400

    # a post's file is an Atom entry of its own: the feed's HTML cleaned, its links made absolute
    atom = b"".join(sources.stream(db, cfg, sid, night["path"]))
    assert atom.startswith(b'<?xml version="1.0" encoding="utf-8"?>\n<entry xmlns="http://www.w3.org/2005/Atom">')
    assert b"<id>harbour-night</id>" in atom and b'href="https://harbour.example.org/posts/night"' in atom
    assert b"steal()" not in atom and b"onclick" not in atom and f"{site}/pier".encode() in atom  # read against the feed's address
    assert b'<title type="text">Harbour news</title>' in atom and b"s3cret" not in atom  # ... which isn't kept

    # watched with backfill: every post becomes a text resource, titled and dated by the post
    r = client.post("/api/v1/watches", headers=h, json={"source": sid, "namespace": "pods", "stable_seconds": 600, "backfill": True})
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    assert client.post("/api/v1/watches/preview", headers=h, json={"source": sid}).json()["transcripts"] == 2
    assert sources.poll_watch(db, cfg, wid)["new"] == 2  # a post is whole once listed: no waiting for it to settle
    drain(db, cfg)
    recs = {r["title"]: r for r in db.rows("SELECT record::id(id) AS id, title, source, recorded_at, status, remote, path FROM recording")}
    assert set(recs) == {"Night crossing", "New buoys"}
    one = recs["Night crossing"]
    assert (one["source"], one["recorded_at"], one["status"]) == ("transcript", "2026-09-29T08:30:00+00:00", "analyzed")
    assert one["remote"] == {"source": sid, "path": night["path"]} and one["path"] == f"harbour:{night['path']}"
    said = _segments(db, one["id"])
    assert "https://harbour.example.org/posts/night" in said and "Mara Keane" in said and "Harbour news" in said
    assert "keeper wrote twice about the fog" in said and "steal" not in said
    types = content_types.all_types(db)
    rec = db.one("SELECT source, path, title, media FROM $r", r=R("recording", one["id"]))
    assert content_types.recognise(db, rec, types)["key"] == "feed_post"
    # an activity automations can start on: the post's address, feed and author, its text marked as outside text
    a = events.get(db, cfg, f"create-recording-{one['id']}", events.viewer(1, None, admin=True))
    assert a["kind"] == "feed_post.received" and events.check_activity(a)
    assert a["actor"] == {"type": "Service", "id": f"lens:source/{sid}", "name": "harbour"}
    o = a["object"]
    assert (o["href"], o["site"], o["feed"], o["author"], o["published"], o["categories"]) == (
        "https://harbour.example.org/posts/night",
        "harbour.example.org",
        "Harbour news",
        "Mara Keane",
        "2026-09-29T08:30:00+00:00",
        ["Harbour"],
    )
    assert a["summary"] == "harbour received the post “Night crossing” from harbour.example.org"
    assert {"object.name", "object.href", "object.feed", "object.author"} <= set(a["lens"]["untrusted"])

    # asked again once the copy read is old: it hasn't changed (304), so nothing is new and nothing is parsed
    _stale()
    _Site.hits = []
    assert sources.poll_watch(db, cfg, wid)["new"] == 0
    assert [(p, etag) for p, _, _, etag in _Site.hits] == [("/feed.xml?key=s3cret", '"v1"')]
    # a new post, and a post edited since
    _Site.feed, _Site.etag = _rss(["night", "buoys", "regatta"], {"buoys": "The new buoys are in, painted red and white."}), '"v2"'
    _stale()
    assert sources.poll_watch(db, cfg, wid)["new"] == 2
    drain(db, cfg)
    titles = sorted(r["title"] for r in db.rows("SELECT title FROM recording"))
    assert titles == ["Autumn regatta", "New buoys", "Night crossing"]  # the edited post read into the one it was
    assert "red and white" in _segments(db, recs["New buoys"]["id"])

    # a post the feed has dropped since: its file is still the copy fetched last
    _Site.feed, _Site.etag = _rss(["regatta"]), '"v3"'
    _stale()
    assert sources.poll_watch(db, cfg, wid)["new"] == 0
    with pytest.raises(FileNotFoundError):
        rss.fetch(cfg, sources.get(db, sid), night["path"])
    assert b"Night crossing" in b"".join(sources.stream(db, cfg, sid, night["path"], space=store.ns_id(db, "pods")))

    # chosen on purpose, from the listing: already there
    regatta = client.get(f"/api/v1/sources/{sid}/browse", headers=h).json()[0]
    r = client.post("/api/v1/import/source", headers=h, json={"source": sid, "paths": [regatta["path"]], "namespace": "pods"})
    assert r.json()["results"][0]["status"] == "already"


def test_feed_addresses(site, client, db, cfg, admin, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(feeds, "_secret", lambda cfg, src, k: src[k])
        assert rss._url(cfg, {"url": "feed://harbour.example.org/rss"}) == "https://harbour.example.org/rss"
        assert rss._url(cfg, {"url": "feed:https://harbour.example.org/rss"}) == "https://harbour.example.org/rss"
        for bad in ("", "ftp://x/feed.xml", "/etc/passwd", "file:///etc/passwd", "https:///feed.xml"):
            with pytest.raises(RuntimeError):
                rss._url(cfg, {"url": bad})

    def health(url, password=None, **params):
        body = {"type": "rss", "params": params, "secrets": {"url": url, **({"pass": password} if password else {})}}
        sid = client.post("/api/v1/sources", headers=admin, json=body).json()["id"]
        rss._FEEDS.clear()
        _Site.hits = []
        return sources.test(db, cfg, sid)  # with this cfg, as the test changes it

    # what isn't public is refused, unless an admin allowed its network
    cfg["documents"]["web_networks"] = []
    refused = health(f"{site}/feed.xml")
    assert not refused["ok"] and "127.0.0.1 is on a private network" in refused["error"] and "Settings → Documents" in refused["error"]
    cfg["documents"]["web_networks"] = ["127.0.0.0/8"]
    assert health(f"{site}/feed.xml")["ok"]
    # a page that names no feed, and an address that is no feed at all
    plain = health(f"{site}/plain")
    assert not plain["ok"] and "names no news feed" in plain["error"]
    assert "404" in health(f"{site}/missing.xml")["error"]

    # a feed that asks for a password gets it; a feed the page names on another server never does
    _Site.auth = "Basic " + base64.b64encode(b"lens:feed password 1").decode()
    assert health(f"{site}/blog", "feed password 1", user="lens")["ok"]
    assert [a for p, _, a, _ in _Site.hits if p.startswith("/feed.xml")][-1] == _Site.auth
    real = socket.getaddrinfo  # where localhost is ::1 too (Ubuntu), only its IPv4 address: the test server's
    monkeypatch.setattr(
        netguard.socket,
        "getaddrinfo",
        lambda host, *a, **k: real(host, *a, **({**k, "family": socket.AF_INET} if host == "localhost" else k)),
    )
    _Site.feed_path = site.replace("127.0.0.1", "localhost") + "/feed.xml"
    elsewhere = health(f"{site}/blog", "feed password 1", user="lens")
    assert not elsewhere["ok"] and "401" in elsewhere["error"]
    assert [(h.split(":")[0], a) for p, h, a, _ in _Site.hits] == [("127.0.0.1", _Site.auth), ("localhost", None)]


def test_posts_leave_memory(site, client, db, cfg, admin):
    """A feed's posts don't stay in memory: its listing does, and a post wanted after that reads the feed in full."""
    r = client.post("/api/v1/sources", headers=admin, json={"name": "harbour", "type": "rss", "secrets": {"url": f"{site}/feed.xml"}})
    sid = r.json()["id"]
    first = client.get(f"/api/v1/sources/{sid}/browse", headers=admin).json()[0]
    (hit,) = rss._FEEDS.values()
    assert set(hit["bodies"]) == set(hit["entries"])
    hit["read_at"] -= rss.KEEP_SECONDS + 1
    _Site.hits = []
    assert client.get(f"/api/v1/sources/{sid}/browse", headers=admin).json()[0] == first  # the listing, from memory
    assert hit["bodies"] is None and _Site.hits == []
    # a post wanted now: read in full, without asking whether it changed (a 304 would bring no posts)
    assert b'<title type="text">New buoys</title>' in rss.fetch(cfg, sources.get(db, sid), first["path"])
    assert [(p, etag) for p, _, _, etag in _Site.hits] == [("/feed.xml", None)]
    (hit,) = rss._FEEDS.values()
    assert set(hit["bodies"]) == set(hit["entries"])


def test_what_a_feed_holds_stays_data(tmp_path):
    """An entity naming a file of this machine is never read in, and a post's HTML keeps no scripts."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    feed = (
        f"<?xml version='1.0'?><!DOCTYPE rss [<!ENTITY leak SYSTEM 'file://{secret}'>]>"
        "<rss version='2.0'><channel><title>Harbour news</title><item><title>Leak &leak;</title><guid>x</guid>"
        "<description>a &lt;img src=x onerror=steal()&gt; &leak; b</description></item></channel></rss>"
    ).encode()
    posts = rss.parse(feed, "https://harbour.example.org/feed.xml")["posts"]
    kept = b"".join(body for _, body in posts.values()) + "".join(e["name"] for e, _ in posts.values()).encode()
    assert b"TOP SECRET" not in kept and b"onerror" not in kept
    assert b"TOP SECRET" not in rss.text(feed).encode()


def test_a_feed_file_imported_as_text(client, db, admin):
    atom = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Harbour news</title><id>urn:harbour</id><updated>2026-10-01T08:00:00Z</updated>
<link href="https://harbour.example.org/"/>
<entry><title>Autumn regatta</title><id>urn:harbour:regatta</id><updated>2026-10-01T08:00:00Z</updated>
<link href="https://harbour.example.org/posts/regatta"/><content type="html">&lt;p&gt;Two days on the water.&lt;/p&gt;</content></entry>
</feed>"""
    r = client.post(
        "/api/v1/import", headers=admin, json={"namespace": "pods", "filename": "harbour.atom", "data": base64.b64encode(atom).decode()}
    )
    assert r.status_code == 200, r.text
    said = _segments(db, r.json()["id"])
    assert "Two days on the water." in said and "https://harbour.example.org/posts/regatta" in said


def test_subscribing_in_one_step(site, client, db, cfg, admin):
    """An agent follows a feed into a namespace with one call (MCP), sees it listed, and stops following it."""
    from tests.api.test_mcp import tool, tool_error

    viewer = make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    assert viewer and "only admins" in tool_error(client, login(client, "vi@x.io", "viewer password 1"), "news_feeds")
    assert "can't be read, so nothing was added" in tool_error(client, admin, "subscribe_feed", url=f"{site}/plain", namespace="news")
    assert "news" not in store.space_names(db).values() and tool(client, admin, "news_feeds")["feeds"] == []
    assert "https://" in tool_error(client, admin, "subscribe_feed", url="ftp://x/feed.xml", namespace="news")

    got = tool(client, admin, "subscribe_feed", url=f"{site}/blog", namespace="news", backfill=True)
    assert (got["name"], got["title"], got["namespace"], got["posts"], got["added"], got["already"]) == (
        "Harbour news",
        "Harbour news",
        "news",
        2,
        True,
        False,
    )
    assert sources.poll_watch(db, cfg, got["watch"])["new"] == 2
    # the same feed again: reused, and already watched into that namespace; another namespace gets its own watch
    again = tool(client, admin, "subscribe_feed", url=f"{site}/blog", namespace="news")
    assert (again["feed"], again["watch"], again["added"], again["already"]) == (got["feed"], got["watch"], False, True)
    other = tool(client, admin, "subscribe_feed", url=f"{site}/blog", namespace="harbour")
    assert other["feed"] == got["feed"] and other["watch"] != got["watch"] and not other["already"]

    (feed,) = tool(client, admin, "news_feeds")["feeds"]
    assert (feed["feed"], feed["name"], feed["title"], feed["health"]["ok"]) == (got["feed"], "Harbour news", "Harbour news", True)
    assert [(w["namespace"], w["new_last_scan"]) for w in feed["watching"]] == [("news", 2), ("harbour", None)]
    assert site not in json.dumps(feed)  # the address stays sealed

    assert tool(client, admin, "unsubscribe_feed", feed=got["feed"]) == {"unsubscribed": got["feed"]}
    assert tool(client, admin, "news_feeds")["feeds"] == [] and db.rows("SELECT id FROM watch_path") == []
    assert len(db.rows("SELECT id FROM recording")) == 2  # what it brought in stays
    assert tool_error(client, admin, "unsubscribe_feed", feed=got["feed"])
