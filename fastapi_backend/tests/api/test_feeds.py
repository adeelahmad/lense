"""Published feeds: who makes and fills a feed, what may go in it, and what feed readers and podcast apps read at its
private address (RSS 2.0, and Apple Podcasts' tags for a podcast feed)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.domain import store, syndication
from tests import fake_llm
from tests.api.test_mcp import tool, tool_error
from tests.helpers import drain, login, make_user, seed

R = store.R
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


@pytest.fixture
def env(client, db, cfg, folder):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    fake_llm.Handler.podcast_script = None
    a, b, c = seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ca@x.io", "calls password 1", roles={"calls": "editor"})
    make_user(db, "root@x.io", "root password 1", admin=True)
    yield {
        "ids": (a, b, c),
        "ed": login(client, "ed@x.io", "editor password 1"),
        "vi": login(client, "vi@x.io", "viewer password 1"),
        "ca": login(client, "ca@x.io", "calls password 1"),
        "root": login(client, "root@x.io", "root password 1"),
    }
    srv.shutdown()


def _read(client, path, **headers):
    r = client.get(path, headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/rss+xml") and r.headers["x-robots-tag"] == "noindex, nofollow"
    return ET.fromstring(r.content).find("channel"), r


def test_making_filling_and_reading_a_feed(client, db, cfg, env):
    a, b, c = env["ids"]
    # only editors make feeds; readers of the namespace see them, nobody else does
    assert client.post("/api/v1/feeds", headers=env["vi"], json={"namespace": "pods", "title": "Pods"}).status_code == 403
    assert client.post("/api/v1/feeds", headers=env["ca"], json={"namespace": "pods", "title": "Pods"}).status_code == 404
    r = client.post("/api/v1/feeds", headers=env["ed"], json={"namespace": "pods", "title": "Pods news", "description": "What's new"})
    assert r.status_code == 201, r.text
    feed = r.json()
    assert feed["kind"] == "rss" and feed["namespace"] == "pods" and feed["items"] == 0 and feed["max_items"] == 50
    assert feed["path"].startswith("/api/v1/feeds/public/lfd_") and feed["url"].endswith(feed["path"])
    fid = feed["id"]
    assert [f["id"] for f in client.get("/api/v1/feeds", headers=env["vi"]).json()] == [fid]
    assert client.get(f"/api/v1/feeds/{fid}", headers=env["vi"]).json()["url"] == feed["url"]
    assert client.get("/api/v1/feeds", headers=env["ca"]).json() == []
    assert client.get(f"/api/v1/feeds/{fid}", headers=env["ca"]).status_code == 404
    assert client.patch(f"/api/v1/feeds/{fid}", headers=env["vi"], json={"title": "x"}).status_code == 403
    assert client.patch(f"/api/v1/feeds/{fid}", headers=env["ed"], json={"image": "http://x.io/a.png"}).status_code == 400
    assert client.patch(f"/api/v1/feeds/{fid}", headers=env["ed"], json={"max_items": 10}).json()["max_items"] == 10
    assert not db.one("SELECT token FROM $r", r=R("feed", fid))["token"].startswith("lfd_")  # sealed

    # filling it: what the person can't read is 404; text that would be markup stays text
    items = f"/api/v1/feeds/{fid}/items"
    assert client.post(items, headers=env["ed"], json={"recordings": [c]}).status_code == 404
    assert client.post(items, headers=env["vi"], json={"recordings": [a]}).status_code == 403
    assert client.post(items, headers=env["ed"], json={}).status_code == 400
    note = {"title": "Weekly <b>digest</b>", "summary": "Line one <script>alert(1)</script>\nLine two", "link": "https://x.io/d"}
    out = client.post(items, headers=env["ed"], json={"recordings": [a, b], "texts": [note, {"title": "Bad", "link": "ftp://x"}]}).json()
    assert len(out["added"]) == 3 and out["already"] == 0
    assert out["refused"] == [{"what": "text 'Bad'", "why": "a link is an http or https address"}]
    again = client.post(items, headers=env["ed"], json={"recordings": [a], "texts": [note]}).json()
    assert again == {"added": [], "already": 2, "refused": []}
    # an admin can read everything, but what goes in is bounded by who reads the feed's namespace
    out = client.post(items, headers=env["root"], json={"recordings": [c]}).json()
    assert out["added"] == [] and "not everyone who reads the feed can read" in out["refused"][0]["why"]
    listed = client.get(items, headers=env["vi"]).json()
    assert {i["kind"] for i in listed} == {"asset", "text"} and len(listed) == 3

    # what a feed reader sees, with no session
    ch, r = _read(client, feed["path"])
    assert ch.findtext("title") == "Pods news" and ch.findtext("generator") == "Lens"
    got = {i.findtext("title"): i for i in ch.findall("item")}
    assert set(got) == {"ep1", "ep2", "Weekly <b>digest</b>"}
    assert got["ep1"].findtext("link") == f"http://127.0.0.1/resources/{a}" and got["ep1"].find("enclosure") is None
    assert got["ep1"].find("guid").get("isPermaLink") == "false"
    text = got["Weekly <b>digest</b>"]
    assert text.findtext("description") == "Line one &lt;script&gt;alert(1)&lt;/script&gt;<br>Line two"  # escaped for HTML
    assert text.findtext("link") == "https://x.io/d"
    assert ch.find("{http://www.w3.org/2005/Atom}link").get("href") == feed["url"]
    assert client.get(feed["path"], headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    assert db.one("SELECT fetched_at FROM $r", r=R("feed", fid))["fetched_at"]
    assert client.get("/api/v1/feeds/public/lfd_" + "x" * 43).status_code == 404
    assert client.get("/api/v1/feeds/public/nope").status_code == 404

    # a deleted resource drops out; an item taken out is gone
    db.q("DELETE $r", r=R("recording", b))
    ch, _ = _read(client, feed["path"])
    assert "ep2" not in {i.findtext("title") for i in ch.findall("item")}
    tid = next(i["id"] for i in listed if i["kind"] == "text")
    assert client.delete(f"{items}/{tid}", headers=env["vi"]).status_code == 403
    assert client.delete(f"{items}/{tid}", headers=env["ed"]).json() == {"ok": True}
    assert client.delete(f"{items}/{tid}", headers=env["ed"]).status_code == 404

    # a new address: the old one stops working at once
    new = client.post(f"/api/v1/feeds/{fid}/token", headers=env["ed"]).json()
    assert new["url"] != feed["url"] and client.get(feed["path"]).status_code == 404
    _read(client, new["path"])
    # a namespace made a vault stops its feeds
    db.q("UPSERT $r SET vault = true", r=R("data_key", feed["space"]))
    assert client.get(new["path"]).status_code == 404
    db.q("UPSERT $r SET vault = false", r=R("data_key", feed["space"]))
    assert client.delete(f"/api/v1/feeds/{fid}", headers=env["vi"]).status_code == 403
    assert client.delete(f"/api/v1/feeds/{fid}", headers=env["ed"]).json() == {"ok": True}
    assert client.get(new["path"]).status_code == 404 and not db.rows("SELECT id FROM feed_item")


def test_a_podcast_feed_lists_what_has_audio(client, db, cfg, env, folder):
    a, b, _c = env["ids"]
    pod = client.post("/api/v1/feeds", headers=env["ed"], json={"namespace": "pods", "title": "Learning", "kind": "podcast"}).json()
    rss = client.post("/api/v1/feeds", headers=env["ed"], json={"namespace": "pods", "title": "All"}).json()
    assert pod["category"] == "Education" and pod["language"] == "en" and rss.get("category") is None
    ep = client.post("/api/v1/podcasts", headers=env["ed"], json={"selection": {"recordings": [a, b]}}).json()["episode"]
    for f in (pod, rss):
        out = client.post(f"/api/v1/feeds/{f['id']}/items", headers=env["ed"], json={"recordings": [ep, a]}).json()
        assert len(out["added"]) == 2
    assert {i["kind"] for i in client.get(f"/api/v1/feeds/{pod['id']}/items", headers=env["ed"]).json()} == {"episode", "asset"}
    # while it's being made, the episode isn't listed anywhere; a transcript without audio isn't a podcast's
    ch, _ = _read(client, pod["path"])
    assert ch.findall("item") == [] and ch.findtext(f"{ITUNES}block") == "Yes"
    assert ch.find(f"{ITUNES}category").get("text") == "Education"
    drain(db, cfg)
    ch, _ = _read(client, rss["path"])
    titles = [i.findtext("title") for i in ch.findall("item")]
    assert "Capsids and shipments" in titles and "ep1" in titles  # a script-only episode is text in an RSS feed
    episode = next(i for i in ch.findall("item") if i.findtext("title") == "Capsids and shipments")
    assert "Sources:" in episode.findtext("description") and f"/resources/{a}" in episode.findtext("description")
    assert _read(client, pod["path"])[0].findall("item") == []

    # with its audio made, it's an episode podcast apps play
    audio = folder / "episode.mp3"
    audio.write_bytes(b"ID3" + bytes(range(256)) * 40)
    db.q("UPDATE $r SET source = 'audio', path = $p, duration_ms = 754000", r=R("recording", ep), p=str(audio))
    db.q("UPDATE $r SET status = 'ready'", r=R("podcast", ep))
    ch, _ = _read(client, pod["path"])
    (item,) = ch.findall("item")
    enc = item.find("enclosure")
    assert enc.get("type") == "audio/mpeg" and enc.get("length") == str(audio.stat().st_size)
    assert enc.get("url").startswith(pod["url"] + "/items/") and item.findtext(f"{ITUNES}duration") == "0:12:34"
    path = enc.get("url").removeprefix("http://127.0.0.1")
    got = client.get(path, headers={"Range": "bytes=0-2"})
    assert got.status_code == 206 and got.content == b"ID3" and got.headers["referrer-policy"] == "no-referrer"
    head = client.head(path)
    assert head.status_code == 200 and head.headers["content-length"] == str(audio.stat().st_size) and head.content == b""
    assert client.get(path.replace(pod["path"], rss["path"])).status_code == 404  # an item of another feed
    # a recording moved where the feed's readers can't read it stops being served
    db.q("UPDATE $r SET space = $s", r=R("recording", ep), s=store.ns_id(db, "calls"))
    assert client.get(path).status_code == 404


def test_timeline_activities_in_a_feed(client, db, cfg, env):
    feed = client.post("/api/v1/feeds", headers=env["ed"], json={"namespace": "pods", "title": "What happened"}).json()
    page = client.get("/api/v1/activities", headers=env["ed"]).json()["items"]
    assert page, "the seeded assets are on the timeline"
    act = page[0]
    out = client.post(f"/api/v1/feeds/{feed['id']}/items", headers=env["ed"], json={"activities": [act["id"], "nope-1"]}).json()
    assert len(out["added"]) == 1 and out["refused"] == [{"what": "activity nope-1", "why": "not found"}]
    (item,) = client.get(f"/api/v1/feeds/{feed['id']}/items", headers=env["ed"]).json()
    assert item["kind"] == "activity" and item["categories"] == [act["kind"]] and item["title"] == act["summary"]
    ch, _ = _read(client, feed["path"])
    (shown,) = ch.findall("item")
    assert shown.findtext("category") == act["kind"] and shown.findtext("link").startswith("http://127.0.0.1/")
    # another namespace's activity can't be named by someone who can't read it
    theirs = client.get("/api/v1/activities", headers=env["ca"]).json()["items"][0]
    out = client.post(f"/api/v1/feeds/{feed['id']}/items", headers=env["ed"], json={"activities": [theirs["id"]]}).json()
    assert out["added"] == [] and out["refused"][0]["why"] == "not found"


def test_vaults_and_settings(db, cfg, env):
    sid = store.ns_id(db, "pods")
    with pytest.raises(ValueError, match="language"):
        syndication.create(db, cfg, sid, "x", language="english please")
    with pytest.raises(ValueError, match="kind"):
        syndication.create(db, cfg, sid, "x", kind="atom")
    with pytest.raises(ValueError, match="title"):
        syndication.create(db, cfg, sid, "  ")
    db.q("UPSERT $r SET vault = true", r=R("data_key", sid))
    with pytest.raises(ValueError, match="vault"):
        syndication.create(db, cfg, sid, "Secret")
    assert syndication.entry_from(7) == {"recording": 7}
    assert syndication.entry_from({"id": 3, "title": "x"}) == {"recording": 3}
    assert syndication.entry_from({"title": "Digest", "text": "hi"}) == {"title": "Digest", "summary": "hi"}
    assert syndication.entry_from(True) is None and syndication.entry_from("x") is None


def test_agents_use_feeds(client, db, cfg, env):
    a, _b, c = env["ids"]
    he = env["ed"]
    assert "needs editor access" in tool_error(client, env["vi"], "create_feed", namespace="pods", title="x")
    feed = tool(client, he, "create_feed", namespace="pods", title="For my phone", kind="podcast")
    assert feed["kind"] == "podcast" and feed["url"].startswith("http") and feed["items"] == 0
    assert "give recording_ids" in tool_error(client, he, "publish_to_feed", feed_id=feed["feed_id"])
    assert "not found" in tool_error(client, he, "publish_to_feed", feed_id=feed["feed_id"], recording_ids=[c])
    out = tool(client, he, "publish_to_feed", feed_id=feed["feed_id"], recording_ids=[a], title="Note", text="Read this")
    assert len(out["added"]) == 2
    listed = tool(client, env["vi"], "list_feeds")["feeds"]
    assert [f["feed_id"] for f in listed] == [feed["feed_id"]] and listed[0]["items"] == 2
    one = tool(client, env["vi"], "list_feeds", feed_id=feed["feed_id"])
    assert {i["kind"] for i in one["newest"]} == {"asset", "text"} and any(i.get("url") for i in one["newest"])
    assert tool(client, env["ca"], "list_feeds")["feeds"] == []
    assert "not found" in tool_error(client, env["ca"], "list_feeds", feed_id=feed["feed_id"])


def _audio(db, folder, rid, name):
    """Give a resource audio podcast apps can play."""
    f = folder / name
    f.write_bytes(b"ID3" + bytes(range(256)) * 8)
    db.q("UPDATE $r SET source = 'audio', path = $p, duration_ms = 61000", r=R("recording", rid), p=str(f))
    return f


def _titles(client, path):
    return sorted(i.findtext("title") for i in _read(client, path)[0].findall("item"))


def test_a_collection_feed_lists_its_assets_with_media(client, db, cfg, env, folder):
    a, b, c = env["ids"]
    _audio(db, folder, a, "a.mp3")
    _audio(db, folder, c, "c.mp3")
    cid = client.post(
        "/api/v1/collections",
        headers=env["root"],
        json={"name": "Commute", "description": "For the train", "recordings": [a, b, c], "shared": True},
    ).json()["id"]
    assert client.get(f"/api/v1/collections/{cid}/feed").status_code == 401
    mine = client.get(f"/api/v1/collections/{cid}/feed", headers=env["root"]).json()
    assert mine["collection"] == cid and mine["kind"] == "podcast" and mine["episodes"] == 2
    assert mine["path"].startswith("/api/v1/feeds/public/lfd_") and mine["url"].endswith(mine["path"])
    assert mine["page"].endswith(f"/collections/{cid}")
    assert client.get(f"/api/v1/collections/{cid}/feed", headers=env["root"]).json()["url"] == mine["url"]  # the same one after
    assert not db.one("SELECT token FROM $r", r=R("feed", mine["id"]))["token"].startswith("lfd_")  # sealed
    # with no session, a podcast app reads the assets with audio or video (not b, a transcript), as their reader may
    ch, r = _read(client, mine["path"])
    assert ch.findtext("title") == "Commute" and ch.findtext("description") == "For the train"
    assert ch.findtext(f"{ITUNES}block") == "Yes" and ch.findtext("link").endswith(f"/collections/{cid}")
    assert _titles(client, mine["path"]) == ["call", "ep1"]
    enc = next(i for i in ch.findall("item") if i.findtext("title") == "ep1").find("enclosure")
    assert enc.get("url") == f"{mine['url']}/items/{a}/audio" and enc.get("type") == "audio/mpeg"
    got = client.get(enc.get("url").removeprefix("http://127.0.0.1"), headers={"Range": "bytes=0-2"})
    assert got.status_code == 206 and got.content == b"ID3" and got.headers["referrer-policy"] == "no-referrer"
    assert client.get(f"{mine['path']}/items/{b}/audio").status_code == 404  # no media: not an episode, not served
    assert client.get(mine["path"], headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    # each person's address lists what they may read: a viewer of pods hears ep1 alone, and can't fetch the call
    theirs = client.get(f"/api/v1/collections/{cid}/feed", headers=env["vi"]).json()
    assert theirs["url"] != mine["url"] and theirs["episodes"] == 1 and _titles(client, theirs["path"]) == ["ep1"]
    assert client.get(f"{theirs['path']}/items/{c}/audio").status_code == 404
    # namespaces' feeds don't show collections' feeds
    assert client.get("/api/v1/feeds", headers=env["root"]).json() == []
    assert client.get(f"/api/v1/feeds/{mine['id']}", headers=env["root"]).status_code == 404
    assert client.post(f"/api/v1/feeds/{mine['id']}/token", headers=env["root"]).status_code == 404
    # a collection no longer shared stops its other readers' feeds; one never shared has none to give
    client.patch(f"/api/v1/collections/{cid}", headers=env["root"], json={"shared": False})
    assert client.get(theirs["path"]).status_code == 404 and _titles(client, mine["path"]) == ["call", "ep1"]
    assert client.get(f"/api/v1/collections/{cid}/feed", headers=env["vi"]).status_code == 404
    # a namespace made a vault drops out of the feed
    db.q("UPSERT $r SET vault = true", r=R("data_key", store.ns_id(db, "calls")))
    assert _titles(client, mine["path"]) == ["ep1"]


def test_a_collection_feed_token_rotates_and_stops(client, db, cfg, env, folder):
    a, _b, _c = env["ids"]
    _audio(db, folder, a, "a.mp3")
    cid = client.post("/api/v1/collections", headers=env["ed"], json={"name": "Mine", "recordings": [a]}).json()["id"]
    feed = client.get(f"/api/v1/collections/{cid}/feed", headers=env["ed"]).json()
    audio = f"{feed['path']}/items/{a}/audio"
    assert client.get(audio).status_code == 200
    # a new address: the old one, and its audio, stop working at once
    new = client.post(f"/api/v1/collections/{cid}/feed/token", headers=env["ed"]).json()
    assert new["id"] == feed["id"] and new["url"] != feed["url"]
    assert client.get(feed["path"]).status_code == 404 and client.get(audio).status_code == 404
    assert _titles(client, new["path"]) == ["ep1"]
    assert set(db.values("SELECT VALUE action FROM audit_log")) >= {"feed.create", "feed.token"}
    # stopped: the address goes; asking again makes another
    assert client.delete(f"/api/v1/collections/{cid}/feed", headers=env["ed"]).json() == {"ok": True}
    assert client.get(new["path"]).status_code == 404
    again = client.get(f"/api/v1/collections/{cid}/feed", headers=env["ed"]).json()
    assert again["url"] not in (feed["url"], new["url"])
    # a disabled account's feeds stop; a deleted collection's feeds go
    db.q("UPDATE $r SET disabled = true", r=R("account", db.values("SELECT VALUE record::id(id) FROM account WHERE email = 'ed@x.io'")[0]))
    assert client.get(again["path"]).status_code == 404
    db.q("UPDATE account SET disabled = false WHERE email = 'ed@x.io'")
    assert client.get(again["path"]).status_code == 200
    assert client.delete(f"/api/v1/collections/{cid}", headers=env["ed"]).json() == {"ok": True}
    assert client.get(again["path"]).status_code == 404 and not db.rows("SELECT id FROM feed WHERE collection = $c", c=cid)


def test_agents_get_a_collection_feed(client, db, cfg, env, folder):
    a, _b, _c = env["ids"]
    _audio(db, folder, a, "a.mp3")
    cid = client.post("/api/v1/collections", headers=env["ed"], json={"name": "Mine", "recordings": [a]}).json()["id"]
    got = tool(client, env["ed"], "list_feeds", collection_id=cid)
    assert got["collection"] == cid and got["episodes"] == 1 and got["url"].startswith("http")
    assert got["url"].endswith(client.get(f"/api/v1/collections/{cid}/feed", headers=env["ed"]).json()["path"])  # the same one
    assert "not found" in tool_error(client, env["vi"], "list_feeds", collection_id=cid)  # not shared with them
