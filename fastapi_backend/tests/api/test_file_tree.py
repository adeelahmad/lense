"""The graph as folders and files (docs/file-tree.md): namespaces, collections, topics and entities as folders, assets as
files with a metadata sidecar and their text, seen exactly as the web app shows them to each person."""

from __future__ import annotations

import json

import pytest

from app.domain import filetree, hierarchy, keyring, store, topics
from tests.api.test_mcp import tool, tool_error
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import login, make_user, seed

URL = "/api/v1/tree"


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    t = topics.create(db, store.ns_id(db, "pods"), "Gene therapy")
    topics.tag(db, t, [a])
    make_user(db, "admin@x.io", "admin password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "out@x.io", "outsider password 1")
    return {
        "a": a,
        "b": b,
        "call": call,
        "folder": folder,
        "ha": login(client, "admin@x.io", "admin password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "hx": login(client, "out@x.io", "outsider password 1"),
    }


def _names(client, h, path=""):
    r = client.get(URL, headers=h, params={"path": path})
    assert r.status_code == 200, r.text
    return [e["name"] for e in r.json()["entries"]]


def _read(client, h, path, **headers):
    return client.get(f"{URL}/file", headers={**h, **headers}, params={"path": path})


def test_namespaces_collections_topics_and_entities_are_folders(client, env):
    ha = env["ha"]
    assert _names(client, ha) == ["calls", "pods"]
    assert _names(client, ha, "pods") == ["Collections", "Entities", "namespace.json", "Topics"]
    assert _names(client, ha, "pods/Collections") == ["General"]
    assert _names(client, ha, "pods/Collections/General") == [
        "ep1 [1].json",
        "ep1 [1].original.txt",
        "ep1 [1].txt",
        "ep2 [2].json",
        "ep2 [2].original.txt",
        "ep2 [2].txt",
    ]
    assert _names(client, ha, "pods/Topics") == ["Gene therapy"]
    assert _names(client, ha, "pods/Topics/Gene therapy") == ["ep1 [1].json", "ep1 [1].original.txt", "ep1 [1].txt"]
    assert "Organisation" in _names(client, ha, "pods/Entities")
    assert "Dyno Therapeutics" in _names(client, ha, "pods/Entities/Organisation")
    assert "ep2 [2].txt" in _names(client, ha, "pods/Entities/Organisation/Dyno Therapeutics")
    # a file isn't a folder, nor the other way round; what isn't there is 404
    assert client.get(URL, headers=ha, params={"path": "pods/namespace.json"}).status_code == 400
    assert client.get(URL, headers=ha, params={"path": "pods/Collections/General/ep1 [1].txt"}).status_code == 400
    assert _read(client, ha, "pods/Collections").status_code == 400
    assert client.get(URL, headers=ha, params={"path": "pods/Nothing"}).status_code == 404
    assert _read(client, ha, "pods/Collections/General/nope.txt").status_code == 404


def test_an_assets_files(client, env, folder):
    ha = env["ha"]
    base = "pods/Collections/General/ep1 [1]"
    text = _read(client, ha, f"{base}.txt")
    assert text.status_code == 200 and text.headers["content-type"].startswith("text/plain")
    assert text.text.startswith("Alice: Welcome back.")
    meta = _read(client, ha, f"{base}.json").json()
    assert meta["id"] == env["a"] and meta["title"] == "ep1" and meta["namespace"] == "pods" and meta["collection"] == "General"
    assert meta["subject"] == ["Gene therapy"]
    assert {"name": "Dyno Therapeutics", "type": "Organisation"} in meta["entities"]
    assert meta["text"] == "ep1 [1].txt" and meta["original_name"] == "ep1.txt"
    # its own file, as it was imported, with byte ranges
    own = (folder / "ep1.txt").read_bytes()
    assert _read(client, ha, f"{base}.original.txt").content == own
    part = _read(client, ha, f"{base}.original.txt", range="bytes=5-14")
    assert part.status_code == 206 and part.content == own[5:15] and part.headers["content-range"] == f"bytes 5-14/{len(own)}"
    # the size listed is the size read
    listed = {e["name"]: e["size"] for e in client.get(URL, headers=ha, params={"path": "pods/Collections/General"}).json()["entries"]}
    for name, size in listed.items():
        assert len(_read(client, ha, f"pods/Collections/General/{name}").content) == size, name
    ns = _read(client, ha, "pods/namespace.json").json()
    assert ns["namespace"] == "pods" and ns["topics"] == 1 and ns["collections"][0]["path"] == "General"


def test_people_see_what_the_web_app_shows_them(client, env, db, folder):
    hv, hx = env["hv"], env["hx"]
    assert _names(client, hv) == ["pods"]
    assert client.get(URL, headers=hv, params={"path": "calls"}).status_code == 404
    assert _read(client, hv, "calls/namespace.json").status_code == 404
    assert _names(client, hx) == []
    assert client.get(URL).status_code == 401
    # a role on one collection: just that collection, and the topics and entities of what's in it
    pods = store.ns_id(db, "pods")
    talks = hierarchy.create(db, pods, "Talks")
    db.q("UPDATE $r SET collection = $c", r=store.R("recording", env["b"]), c=talks)
    hierarchy.give(db, talks, _account(db, "out@x.io"), "viewer")
    assert _names(client, hx) == ["pods"]
    assert _names(client, hx, "pods/Collections") == ["Talks"]
    assert _names(client, hx, "pods/Collections/Talks") == ["ep2 [2].json", "ep2 [2].original.txt", "ep2 [2].txt"]
    assert _names(client, hx, "pods/Topics") == []  # Gene therapy is about ep1, which they can't see
    assert _names(client, hx, "pods/Entities/Organisation/Dyno Therapeutics") == ["ep2 [2].json", "ep2 [2].original.txt", "ep2 [2].txt"]
    assert _read(client, hx, "pods/Collections/General/ep1 [1].txt").status_code == 404


def _account(db, email):
    return db.one("SELECT record::id(id) AS id FROM account WHERE email = $e", e=email)["id"]


def test_names_are_safe_and_unique(db, cfg, env):
    pods = store.ns_id(db, "pods")
    hierarchy.create(db, pods, 'a/b: "c"?')
    db.q("UPDATE $r SET title = $t", r=store.R("recording", env["a"]), t="Q3: plans / ideas?")
    t = filetree.Tree(db, cfg, filetree.Viewer(admin=True, roles={pods: "owner"}))
    assert [e.name for e in t.list("pods/Collections")] == ["a_b_ _c__", "General"]
    assert "Q3_ plans _ ideas_ [1].txt" in [e.name for e in t.list("pods/Collections/General")]
    assert filetree._unique([("Talks", 1), ("talks", 2)]) == [("Talks", 1), ("talks [2]", 2)]
    assert filetree.clean("..") == "_" and filetree.clean("x. ") == "x" and len(filetree.clean("y" * 500)) == filetree.NAME_MAX


def test_encrypted_files_are_read_plain(client, db, cfg, env, folder):
    """An asset's own file kept encrypted (a namespace's key) reads as it was, and is listed at its plain size."""
    own = (folder / "ep1.txt").read_bytes()
    keyring.encrypt_file(db, cfg, store.ns_id(db, "pods"), str(folder / "ep1.txt"), force=True)
    assert keyring.is_encrypted(folder / "ep1.txt")
    got = _read(client, env["ha"], "pods/Collections/General/ep1 [1].original.txt")
    assert got.status_code == 200 and got.content == own
    sizes = {
        e["name"]: e["size"] for e in client.get(URL, headers=env["ha"], params={"path": "pods/Collections/General"}).json()["entries"]
    }
    assert sizes["ep1 [1].original.txt"] == len(own)


def test_agents_browse_it_over_mcp(client, env):
    _, tokens = _grant(client, env["hv"], scope="read")
    h = _bearer(tokens)
    assert [e["name"] for e in tool(client, h, "list_files", path="")["entries"]] == ["pods"]
    got = tool(client, h, "read_file", path="pods/Topics/Gene therapy/ep1 [1].json")
    assert json.loads(got["text"])["subject"] == ["Gene therapy"]
    own = tool(client, h, "read_file", path="pods/Collections/General/ep1 [1].original.txt")
    assert "text" not in own and own["download"].startswith("/api/v1/tree/file?path=")
    assert "a file" in tool_error(client, h, "list_files", path="pods/namespace.json")
    assert "not found" in tool_error(client, h, "read_file", path="calls/namespace.json")
