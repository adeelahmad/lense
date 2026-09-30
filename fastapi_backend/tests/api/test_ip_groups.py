"""IP groups (docs/access.md): owners give the visitors from some addresses permission on all of a namespace's recordings,
or on chosen ones, without signing in. The address comes from the peer, or from the trusted proxies' X-Forwarded-For."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import analyze, ingest, ipgroups, metadata
from tests.api.test_iiif import BASE, CLIP
from tests.helpers import login, make_user, quiet, seed, write_wav

LAB = "198.51.100.7"  # in the reading room's range
ELSEWHERE = "203.0.113.9"
URL = "/api/v1/namespaces/pods/ip-groups"


@pytest.fixture
def env(app, db, cfg, folder, client):
    a, b, call = seed(db, cfg, folder)
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text(CLIP)
    clip = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)  # private: the namespace's default
    analyze.analyze_pending(db, cfg, log=quiet)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "guest@x.io", "guest password 1")  # no role anywhere
    return {
        "app": app,
        "a": a,
        "b": b,
        "call": call,
        "clip": clip,
        "hr": login(client, "root@x.io", "root password 1"),
        "ho": login(client, "own@x.io", "owner password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hg": login(client, "guest@x.io", "guest password 1"),
    }


def visitor(app, address=LAB, base=None):
    """A client whose connection comes from this address."""
    return TestClient(app, base_url=base or "http://127.0.0.1", client=(address, 50000))


def test_addresses_and_proxies():
    assert ipgroups.ranges([" 198.51.100.0/24", "198.51.100.7", "198.51.100.9/24", "2001:DB8::/48", "2001:db8::1/128"]) == [
        "198.51.100.0/24",
        "198.51.100.7",
        "2001:db8::/48",
        "2001:db8::1",
    ]
    assert ipgroups.ranges(["10.0.0.0/8", "2001::/16"]) == ["10.0.0.0/8", "2001::/16"]
    for bad in ([], ["nonsense"], ["10.0.0.0/7"], ["0.0.0.0/0"], ["2001::/15"], [""], [5], "198.51.100.7"):
        with pytest.raises(ValueError):
            ipgroups.ranges(bad)
    assert ipgroups.proxies([" 10.0.0.1", "172.16.0.0/12", "::1"]) == ["10.0.0.1/32", "172.16.0.0/12", "::1/128"]
    for bad in (["frontend"], "10.0.0.1", [None]):
        with pytest.raises(ValueError):
            ipgroups.proxies(bad)

    parsed = [
        str(ipgroups.address(x))
        for x in ("198.51.100.7", " 198.51.100.7:5678", "[2001:db8::1]:443", "::ffff:198.51.100.7", '"2001:db8::1"')
    ]
    assert parsed == ["198.51.100.7", "198.51.100.7", "2001:db8::1", "198.51.100.7", "2001:db8::1"]
    assert [ipgroups.address(x) for x in ("unknown", "_hidden", "", None, "testclient")] == [None] * 5

    trusted = ("127.0.0.0/8", "::1/128", "10.0.0.0/8")

    def walk(peer, forwarded=None, port=50000):
        found = ipgroups.client_address(peer, port, forwarded, trusted)
        return str(found) if found else None

    assert walk(LAB) == LAB  # straight to the server
    assert walk(LAB, ELSEWHERE) is None  # a proxy the server wasn't told to trust: its address isn't the visitor's
    assert walk("127.0.0.1", ELSEWHERE) == ELSEWHERE  # the web app on the same machine
    assert walk("::ffff:127.0.0.1", ELSEWHERE) == ELSEWHERE
    assert walk("127.0.0.1", f"192.0.2.66, {ELSEWHERE}") == ELSEWHERE  # whatever the visitor made up on the left
    assert walk("127.0.0.1", f"{ELSEWHERE}, 10.0.0.5") == ELSEWHERE  # past a trusted reverse proxy
    assert walk("127.0.0.1") is None  # the web app asking on its own behalf
    assert walk("127.0.0.1", "10.0.0.5") is None
    assert walk("127.0.0.1", f"{ELSEWHERE}, unknown") is None
    assert walk(ELSEWHERE, f"192.0.2.66, {ELSEWHERE}", port=0) == ELSEWHERE  # uvicorn took the peer from the header
    assert walk("testclient") is None and walk(None) is None
    assert ipgroups.client_address("127.0.0.1", 50000, ELSEWHERE, ()) is None  # nobody trusted


def test_owners_manage_ip_groups(client, env, db):
    ho, he, hg, hr = env["ho"], env["he"], env["hg"], env["hr"]
    # owners only; the namespace looks absent to people without a role
    assert client.get(URL, headers=he).status_code == 403
    assert client.get(URL, headers=hg).status_code == 404
    assert client.post(URL, headers=he, json={"name": "Lab", "ranges": [LAB]}).status_code == 403
    assert client.get(URL, headers=ho).json() == {"address": None, "groups": []}  # the test client's peer isn't an address
    r = client.post(URL, headers=ho, json={"name": "Lab", "ranges": [LAB, "nonsense"]})
    assert r.json()["detail"] == "nonsense isn't an address or a range like 203.0.113.0/24"
    for body in (
        {"name": "Lab", "ranges": ["nonsense"]},
        {"name": "Lab", "ranges": ["0.0.0.0/0"]},
        {"name": "  ", "ranges": [LAB]},
        {"name": "Lab", "ranges": []},
    ):
        assert client.post(URL, headers=ho, json=body).status_code in (400, 422), body

    r = client.post(URL, headers=ho, json={"name": " Reading  room ", "ranges": ["198.51.100.0/24", "2001:db8::/48"], "everything": True})
    assert r.status_code == 200, r.text
    g = r.json()["groups"][0]
    assert (g["name"], g["ranges"], g["everything"], g["chosen"], g["here"], g["by"]) == (
        "Reading room",
        ["198.51.100.0/24", "2001:db8::/48"],
        True,
        0,
        False,
        "own@x.io",
    )
    assert client.post(URL, headers=ho, json={"name": "reading ROOM", "ranges": ["192.0.2.1"]}).status_code == 400  # one name once
    # asked from inside the range, the list says so
    d = visitor(env["app"]).get(URL, headers=ho).json()
    assert (d["address"], d["groups"][0]["here"]) == (LAB, True)

    gid = g["id"]
    assert client.patch(f"{URL}/{gid}", headers=he, json={"name": "x"}).status_code == 403
    assert client.patch(f"{URL}/{gid}", headers=ho, json={}).status_code == 400
    assert client.patch(f"{URL}/{gid}", headers=ho, json={"name": None}).status_code == 400
    assert client.patch(f"/api/v1/namespaces/calls/ip-groups/{gid}", headers=hr, json={"name": "x"}).status_code == 404
    g = client.patch(f"{URL}/{gid}", headers=ho, json={"everything": False, "ranges": [LAB]}).json()["groups"][0]
    assert (g["name"], g["ranges"], g["everything"], g["updated_by"]) == ("Reading room", [LAB], False, "own@x.io")

    assert client.delete(f"{URL}/{gid}", headers=he).status_code == 403
    assert client.delete(f"{URL}/{gid}", headers=ho).json()["groups"] == []
    assert client.delete(f"{URL}/{gid}", headers=ho).status_code == 404
    audit = sorted((x["action"], x["target"]) for x in db.rows("SELECT action, target FROM audit_log WHERE action CONTAINS 'ip_group'"))
    space = audit[0][1]
    assert audit == [
        ("namespace.ip_group.create", space),
        ("namespace.ip_group.delete", space),
        ("namespace.ip_group.update", space),
    ]
    update = db.rows("SELECT detail FROM audit_log WHERE action = 'namespace.ip_group.update'")[0]["detail"]
    assert update["everything"] == {"from": True, "to": False} and "name" not in update


def test_ip_group_opens_a_namespace(client, env, db, cfg):
    app, clip, a, b = env["app"], env["clip"], env["a"], env["b"]
    metadata.save(db, cfg, a, {"access": "restricted"})
    lab, far = visitor(app), visitor(app, ELSEWHERE)
    page = f"/api/v1/public/recordings/{clip}"
    assert lab.get(page).status_code == 404 and lab.get("/api/v1/public/collections/pods").status_code == 404

    client.post(URL, headers=env["ho"], json={"name": "Reading room", "ranges": ["198.51.100.0/24"], "everything": True})
    # from the reading room, without signing in: all of it, everywhere visitors look
    d = lab.get(page).json()
    assert (d["view"], d["network"], d["granted"], d["member"], d["closed"]) == ("full", "Reading room", False, False, [])
    assert d["media"]["url"] and d["transcript"]["segments"]
    coll = lab.get("/api/v1/public/collections/pods").json()
    assert coll["network"] == "Reading room" and coll["member"] is False
    assert sorted((x["id"], x["view"]) for x in coll["items"]) == sorted([(clip, "full"), (a, "full"), (b, "full")])
    home = lab.get("/api/v1/public/home").json()
    assert [(c["name"], c["recordings"], c["network"]) for c in home["collections"]] == [("pods", 3, "Reading room")]
    assert home["shared"] == []  # "shared with you" is for people given permission
    found = lab.get("/api/v1/public/search", params={"q": "capsid"}).json()["items"]
    assert sorted(x["id"] for x in found) == sorted([clip, a]) and all(x["hits"] for x in found)  # a restricted one's lines too
    assert lab.get(f"/api/v1/public/recordings/{env['call']}").status_code == 404  # another namespace stays closed
    # signed in there, nothing to ask for
    d = lab.get(page, headers=env["hg"]).json()
    assert d["view"] == "full" and not d.get("can_request")
    assert lab.post(f"{page}/request", headers=env["hg"], json={}).status_code == 400

    # from elsewhere: nothing
    assert far.get(page).status_code == 404 and far.get("/api/v1/public/collections/pods").status_code == 404
    # through the web app on this machine, which passes the visitor's address on
    web = visitor(app, "127.0.0.1")
    assert web.get(page, headers={"X-Forwarded-For": LAB}).json()["view"] == "full"
    assert web.get(page, headers={"X-Forwarded-For": f"{LAB}, {ELSEWHERE}"}).status_code == 404  # the proxy saw ELSEWHERE
    assert web.get(page).status_code == 404  # the web app on its own behalf
    # a proxy the server doesn't trust yet: its X-Forwarded-For counts for nothing, and neither does its own address
    proxy = visitor(app, "192.0.2.10")
    client.post(URL, headers=env["ho"], json={"name": "Proxies", "ranges": ["192.0.2.0/24"], "everything": True})
    assert proxy.get(page, headers={"X-Forwarded-For": ELSEWHERE}).status_code == 404
    assert client.put("/api/v1/settings/server", headers=env["hr"], json={"trusted_proxies": ["nonsense"]}).status_code == 400
    assert client.put("/api/v1/settings/server", headers=env["ho"], json={"trusted_proxies": ["192.0.2.10"]}).status_code == 403
    assert (
        client.put("/api/v1/settings/server", headers=env["hr"], json={"trusted_proxies": ["127.0.0.1", "192.0.2.10"]}).status_code == 200
    )
    assert client.get("/api/v1/settings", headers=env["hr"]).json()["server"]["values"]["trusted_proxies"] == [
        "127.0.0.1/32",
        "192.0.2.10/32",
    ]
    assert proxy.get(page, headers={"X-Forwarded-For": LAB}).json()["network"] == "Reading room"
    assert proxy.get(page, headers={"X-Forwarded-For": ELSEWHERE}).status_code == 404


def test_ip_group_opens_chosen_recordings(client, env, db):
    clip, b, ho, he = env["clip"], env["b"], env["ho"], env["he"]
    lab = visitor(env["app"])
    groups = client.post(URL, headers=ho, json={"name": "Lab", "ranges": [LAB]}).json()["groups"]
    gid = groups[0]["id"]
    whole = client.post(URL, headers=ho, json={"name": "Campus", "ranges": ["192.0.2.0/24"], "everything": True}).json()["groups"]
    campus = next(g["id"] for g in whole if g["name"] == "Campus")
    other = client.post("/api/v1/namespaces/calls/ip-groups", headers=env["hr"], json={"name": "Calls", "ranges": [LAB]}).json()

    url = f"/api/v1/recordings/{clip}/ip-groups"
    assert client.get(url, headers=he).status_code == 403
    assert client.put(f"{url}/{gid}", headers=he).status_code == 403
    assert [(g["name"], g["everything"], g["opens"]) for g in client.get(url, headers=ho).json()] == [
        ("Campus", True, True),
        ("Lab", False, False),
    ]
    assert client.put(f"{url}/{campus}", headers=ho).status_code == 400  # it opens everything already
    assert client.put(f"{url}/{other['groups'][0]['id']}", headers=ho).status_code == 404  # another namespace's group
    assert client.delete(f"{url}/{gid}", headers=ho).status_code == 404  # it doesn't open this one

    assert [g["opens"] for g in client.put(f"{url}/{gid}", headers=ho).json()] == [True, True]
    assert client.put(f"{url}/{gid}", headers=ho).status_code == 200  # again: no change
    listed = client.get(URL, headers=ho).json()["groups"][1]
    assert listed["chosen"] == 1 and "recordings" not in listed and "space" not in listed  # the count, not the list
    d = lab.get(f"/api/v1/public/recordings/{clip}").json()
    assert (d["view"], d["network"]) == ("full", "Lab")
    assert lab.get(f"/api/v1/public/recordings/{b}").status_code == 404  # not chosen
    coll = lab.get("/api/v1/public/collections/pods").json()
    assert coll["network"] is None and [x["id"] for x in coll["items"]] == [clip]
    # a chosen recording that moved to another namespace no longer counts
    db.q(
        "UPDATE $r SET space = $s",
        r=ipgroups.R("recording", clip),
        s=db.values("SELECT VALUE record::id(id) FROM space WHERE name = 'calls'")[0],
    )
    assert ipgroups.of(db, ipgroups.address(LAB)).recordings == {}
    db.q(
        "UPDATE $r SET space = $s",
        r=ipgroups.R("recording", clip),
        s=db.values("SELECT VALUE record::id(id) FROM space WHERE name = 'pods'")[0],
    )

    assert [g["opens"] for g in client.delete(f"{url}/{gid}", headers=ho).json()] == [True, False]
    assert lab.get(f"/api/v1/public/recordings/{clip}").status_code == 404
    actions = sorted(x["action"] for x in db.rows("SELECT action FROM audit_log WHERE action CONTAINS 'recording.ip_group'"))
    assert actions == ["recording.ip_group.close", "recording.ip_group.open"]


def test_ip_group_opens_iiif(client, env):
    clip, app = env["clip"], env["app"]
    client.post(URL, headers=env["ho"], json={"name": "Reading room", "ranges": ["198.51.100.0/24"], "everything": True})
    lab, far = visitor(app, base=BASE), visitor(app, ELSEWHERE, base=BASE)
    assert far.get(f"/iiif/{clip}/manifest").status_code == 404
    assert far.get(f"/iiif/auth/probe/{clip}/audio").json()["status"] == 401
    assert lab.get(f"/iiif/{clip}/manifest").status_code == 200
    assert lab.get(f"/iiif/{clip}/transcript.vtt").status_code == 200
    assert lab.get(f"/iiif/{clip}/audio", headers={"Range": "bytes=0-9"}).status_code == 206
    items = lab.get("/iiif/collection/pods").json()["items"]
    assert str(clip) in [i["id"].rsplit("/", 2)[-2] for i in items]
    assert [c["label"] for c in lab.get("/iiif/collection").json()["items"]] and far.get("/iiif/collection").json().get("items", []) == []
    assert lab.get("/iiif/collection/pods/search", params={"q": "capsid"}).json()["items"]
    # a viewer's probe from the reading room gets a signed link, without signing in
    res = lab.get(f"/iiif/auth/probe/{clip}/audio").json()
    assert res["status"] == 302
    assert far.get(res["location"]["id"].replace(BASE, ""), headers={"Range": "bytes=0-9"}).status_code == 206
