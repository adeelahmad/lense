"""Many web pages at once (links.py, POST /import/web/links): a list of addresses, a browser's bookmark export or
Chrome's Bookmarks file, each link a page captured as a document, tagged with its bookmark folders."""

from __future__ import annotations

import base64
import json

import pytest

from app.domain import convert, links, store
from tests.helpers import login, make_user

R = store.R

NETSCAPE = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 PERSONAL_TOOLBAR_FOLDER="true">Bookmarks bar</H3>
    <DL><p>
        <DT><A HREF="https://8.8.8.8/harbour" ADD_DATE="1">Harbour &amp; news</A>
        <DT><H3>Research</H3>
        <DL><p>
            <DT><H3>Tides</H3>
            <DL><p>
                <DT><A HREF="https://8.8.4.4/tides">Tide tables</A>
            </DL><p>
            <DT><A HREF="https://1.1.1.1/lamps">Lamps</A>
        </DL><p>
    </DL><p>
    <DT><A HREF="javascript:alert(1)">Not a page</A>
    <DT><A HREF="https://8.8.8.8/harbour">Harbour again</A>
</DL><p>
"""

CHROME = {
    "roots": {
        "bookmark_bar": {
            "name": "Bookmarks bar",
            "type": "folder",
            "children": [
                {"type": "url", "name": "Harbour", "url": "https://8.8.8.8/harbour"},
                {"type": "folder", "name": "Research", "children": [{"type": "url", "name": "Lamps", "url": "https://1.1.1.1/lamps"}]},
            ],
        },
        "other": {"name": "Other bookmarks", "type": "folder", "children": []},
    },
    "version": 1,
}


def test_reading_lists_and_bookmarks():
    got = links.read(NETSCAPE.encode())
    assert got == [
        {"url": "https://8.8.8.8/harbour", "title": "Harbour & news", "folders": []},
        {"url": "https://8.8.4.4/tides", "title": "Tide tables", "folders": ["Research", "Tides"]},
        {"url": "https://1.1.1.1/lamps", "title": "Lamps", "folders": ["Research"]},
    ]
    assert links.read(json.dumps(CHROME)) == [
        {"url": "https://8.8.8.8/harbour", "title": "Harbour", "folders": []},
        {"url": "https://1.1.1.1/lamps", "title": "Lamps", "folders": ["Research"]},
    ]
    text = "# reading list\nhttps://8.8.8.8/a\n\nThe tides - https://8.8.4.4/b.\nnot a link\nhttps://8.8.8.8/a\n"
    assert links.read(text) == [
        {"url": "https://8.8.8.8/a", "title": None, "folders": []},
        {"url": "https://8.8.4.4/b", "title": "The tides", "folders": []},
    ]
    assert links.tags(["A" * 50, "b", "c", "d", "e", "f"]) == ["b", "c", "d", "e", "f"]
    assert links.tags(["x" * 50]) == ["x" * 40]


@pytest.fixture
def app(cfg, db, monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(convert, "chromium", lambda cfg: "/usr/bin/chromium")  # nothing is captured: no job runs here
    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "view@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "ha": login(client, "root@x.io", "root password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "view@x.io", "viewer password 1"),
    }


def test_a_bookmarks_file_becomes_pages_to_capture(client, env, db):
    he = env["he"]
    data = base64.b64encode(NETSCAPE.encode()).decode()
    r = client.post("/api/v1/import/web/links", headers=he, json={"namespace": "pods", "filename": "bookmarks.html", "data": data})
    assert r.status_code == 200, r.text
    got = r.json()["results"]
    assert [(x["url"], x["status"]) for x in got] == [
        ("https://8.8.8.8/harbour", "queued"),
        ("https://8.8.4.4/tides", "queued"),
        ("https://1.1.1.1/lamps", "queued"),
    ]
    recs = {x["url"]: db.one("SELECT title, web, tags, source, status FROM $r", r=R("recording", x["recording"])) for x in got}
    tides = recs["https://8.8.4.4/tides"]
    assert (tides["title"], tides["web"], tides["source"], tides["tags"]) == (
        "Tide tables",
        {"url": "https://8.8.4.4/tides"},
        "document",
        ["Research", "Tides"],
    )
    assert recs["https://8.8.8.8/harbour"]["title"] == "Harbour & news" and not recs["https://8.8.8.8/harbour"].get("tags")
    assert all(x["job"] for x in got)
    harbour = got[0]["recording"]

    # again, with a pasted list: what's there already isn't captured twice; what can't be is skipped, saying why
    text = "https://8.8.8.8/harbour\nhttp://127.0.0.1/admin\nhttps://9.9.9.9/new\n"
    r = client.post("/api/v1/import/web/links", headers=he, json={"namespace": "pods", "text": text, "folders_as_tags": False})
    got = r.json()["results"]
    assert [(x["url"], x["status"]) for x in got] == [
        ("https://8.8.8.8/harbour", "already"),
        ("http://127.0.0.1/admin", "skipped"),
        ("https://9.9.9.9/new", "queued"),
    ]
    assert got[0]["recording"] == harbour
    assert "isn't a public address" in got[1]["detail"]
    audit = db.rows("SELECT detail FROM audit_log WHERE action = 'import.web'")
    assert sorted(a["detail"]["links"] for a in audit) == [1, 3]


def test_what_is_refused(client, env, monkeypatch):
    he, hv = env["he"], env["hv"]
    url = "/api/v1/import/web/links"
    assert client.post(url, headers=hv, json={"namespace": "pods", "text": "https://8.8.8.8/"}).status_code == 403
    r = client.post(url, headers=he, json={"namespace": "pods", "text": "nothing here"})
    assert r.status_code == 400 and "no http" in r.json()["detail"]
    r = client.post(url, headers=he, json={"namespace": "pods"})
    assert r.status_code == 400
    monkeypatch.setattr(links, "MAX", 2)
    r = client.post(url, headers=he, json={"namespace": "pods", "text": "https://8.8.8.8/1\nhttps://8.8.8.8/2\nhttps://8.8.8.8/3"})
    assert r.status_code == 400 and "2 links at most" in r.json()["detail"]
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    r = client.post(url, headers=he, json={"namespace": "pods", "text": "https://8.8.8.8/1"})
    assert r.status_code == 400 and "Chromium" in r.json()["detail"]
