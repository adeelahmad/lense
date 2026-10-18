"""Playlists (docs/api.md#playlists): clips of moments of any resources, kept in order to play back to back. A playlist
is its maker's; a shared one is read by everyone signed in, each reader getting only the clips of resources they can
read, with what a player needs; clips go with their resource, and private playlists with their maker."""

from __future__ import annotations

import pytest

from app.domain import analyze, auth, ingest, playlists, store
from tests.api.test_iiif import CLIP
from tests.helpers import login, make_user, quiet, seed, write_wav

R = store.R
P = "/api/v1/playlists"


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav, seconds=3.0)
    tr.write_text(CLIP)
    sound = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    make_user(db, "both@x.io", "both password 1", roles={"pods": "viewer", "calls": "viewer"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "viewer"})
    return {
        "a": a,
        "b": b,
        "call": call,
        "sound": sound,
        "hb": login(client, "both@x.io", "both password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hx": login(client, "out@x.io", "outsider password 1"),
    }


def _items(client, pid, h):
    r = client.get(f"{P}/{pid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_playlist_crud_and_clips(client, env, db):
    a, sound, hv = env["a"], env["sound"], env["hv"]
    # nothing yet; a playlist with a unique name
    assert client.get(P, headers=hv).json() == []
    r = client.post(P, headers=hv, json={"name": "  Best   bits ", "description": "for the team"})
    assert r.status_code == 200, r.text
    pl = r.json()
    assert (pl["name"], pl["description"], pl["shared"], pl["mine"], pl["count"], pl["duration_ms"]) == (
        "Best bits",
        "for the team",
        False,
        True,
        0,
        0,
    )
    pid = pl["id"]
    assert client.post(P, headers=hv, json={"name": "best BITS"}).status_code == 400

    # a viewer clips a moment of a resource they can read, with a title, a note and the words picked
    url = f"{P}/{pid}/items"
    r = client.post(url, headers=hv, json={"recording": sound, "t0": 500, "t1": 2000, "title": "The intro", "note": "start here"})
    assert r.status_code == 200, r.text
    c = r.json()
    assert {k: c[k] for k in ("playlist", "position", "recording", "t0", "t1", "title", "note", "media", "namespace")} == {
        "playlist": pid,
        "position": 0,
        "recording": sound,
        "t0": 500,
        "t1": 2000,
        "title": "The intro",
        "note": "start here",
        "media": "audio",
        "namespace": "pods",
    }
    # what a player needs: the resource's signed audio link (as the recording player gets it), and its page there
    assert c["audio"].startswith(f"/api/v1/recordings/{sound}/audio?") and "sig=" in c["audio"]
    assert client.get(c["audio"]).status_code in (200, 206)
    assert c["link"] == f"/resources/{sound}"
    # a moment without an end lasts 30 seconds, or stops at the end of the resource; a text has no audio
    a_end = db.one("SELECT duration_ms FROM $r", r=R("recording", a))["duration_ms"]
    t = client.post(url, headers=hv, json={"recording": a, "t0": 4000, "quote": "the capsid\n model"}).json()
    assert (t["t0"], t["t1"], t["quote"], t["audio"], t["media"], t["link"]) == (
        4000,
        min(34000, a_end),
        "the capsid model",
        None,
        "transcript",
        f"/resources/{a}?t=4",
    )
    end = db.one("SELECT duration_ms FROM $r", r=R("recording", sound))["duration_ms"]
    assert client.post(url, headers=hv, json={"recording": sound, "t0": 1000}).json()["t1"] == end
    assert client.post(url, headers=hv, json={"recording": sound, "t0": end + 5000}).status_code == 400
    assert client.post(url, headers=hv, json={"recording": sound, "t0": 2000, "t1": 1000}).status_code == 400
    assert client.post(url, headers=hv, json={"t0": 0}).status_code == 400
    assert client.post(url, headers=hv, json={"recording": env["call"], "t0": 0}).status_code == 404  # not one they read

    # change one; an empty note clears it
    r = client.patch(f"{url}/{c['id']}", headers=hv, json={"title": "Intro", "note": "", "t1": 2500})
    assert r.status_code == 200, r.text
    assert (r.json()["title"], r.json()["note"], r.json()["t0"], r.json()["t1"]) == ("Intro", None, 500, 2500)

    # the list counts them and how long they play
    listed = client.get(P, headers=hv).json()
    assert [(p["id"], p["count"]) for p in listed] == [(pid, 3)]
    assert listed[0]["duration_ms"] == 2000 + min(30000, a_end - 4000) + (end - 1000)
    # rename, describe; delete takes the clips with it
    r = client.patch(f"{P}/{pid}", headers=hv, json={"name": "Keepers", "description": ""})
    assert (r.json()["name"], r.json()["description"]) == ("Keepers", None)
    assert client.delete(f"{P}/{pid}", headers=hv).status_code == 200
    assert client.get(f"{P}/{pid}", headers=hv).status_code == 404
    assert db.values("SELECT VALUE id FROM playlist_item WHERE playlist = $p", p=pid) == []


def test_order_reorder_remove_and_undo(client, env):
    a, b, sound, hv = env["a"], env["b"], env["sound"], env["hv"]
    pid = client.post(P, headers=hv, json={"name": "Order"}).json()["id"]
    url = f"{P}/{pid}/items"
    ids = [
        client.post(url, headers=hv, json={"recording": r, "t0": 0, "title": n}).json()["id"]
        for r, n in ((a, "one"), (b, "two"), (sound, "three"))
    ]
    one, two, three = ids
    titles = lambda: [i["title"] for i in _items(client, pid, hv)]  # noqa: E731
    assert titles() == ["one", "two", "three"]
    # added at a place, the others make room
    four = client.post(url, headers=hv, json={"recording": a, "t0": 1000, "title": "four", "position": 1}).json()
    assert four["position"] == 1 and titles() == ["one", "four", "two", "three"]
    # reorder: the ones named first, as named, then the others as they were
    r = client.put(f"{P}/{pid}/order", headers=hv, json={"items": [three, one]})
    assert r.status_code == 200, r.text
    assert [i["title"] for i in r.json()["items"]] == ["three", "one", "four", "two"]
    assert [i["position"] for i in r.json()["items"]] == [0, 1, 2, 3]
    assert client.put(f"{P}/{pid}/order", headers=hv, json={"items": [one, one]}).status_code == 400
    assert client.put(f"{P}/{pid}/order", headers=hv, json={"items": [99999]}).status_code == 400
    # remove one; Undo adds it back where it was
    gone = next(i for i in _items(client, pid, hv) if i["id"] == one)
    assert client.delete(f"{url}/{one}", headers=hv).status_code == 200
    assert titles() == ["three", "four", "two"]
    assert client.delete(f"{url}/{one}", headers=hv).status_code == 404
    back = {k: gone[k] for k in ("recording", "t0", "t1", "title", "note", "quote", "position")}
    assert client.post(url, headers=hv, json={k: v for k, v in back.items() if v is not None}).json()["position"] == 1
    assert titles() == ["three", "one", "four", "two"]


def test_last_used_is_the_default(client, env, db):
    hv, a = env["hv"], env["a"]
    uid = auth.find_account(db, "vi@x.io")["id"]
    # none yet: a clip goes in a new one called Clips
    first = playlists.default_for(db, uid)
    assert playlists.get(db, first)["name"] == "Clips"
    second = client.post(P, headers=hv, json={"name": "Second"}).json()["id"]
    assert client.get(P, headers=hv).json()[0]["id"] == second  # the newest is the latest used
    client.post(f"{P}/{first}/items", headers=hv, json={"recording": a, "t0": 0})
    assert [p["id"] for p in client.get(P, headers=hv).json()] == [first, second]  # adding to it uses it
    assert playlists.default_for(db, uid) == first


def test_readers_get_only_clips_of_resources_they_can_read(client, env, db):
    a, call, hb, hv, he, hx = env["a"], env["call"], env["hb"], env["hv"], env["he"], env["hx"]
    # someone who reads pods and calls keeps clips of both
    pid = client.post(P, headers=hb, json={"name": "Mixed"}).json()["id"]
    url = f"{P}/{pid}/items"
    client.post(url, headers=hb, json={"recording": a, "t0": 0, "title": "pods clip"})
    client.post(url, headers=hb, json={"recording": call, "t0": 0, "title": "calls clip", "note": "secret plan"})
    assert [i["title"] for i in _items(client, pid, hb)] == ["pods clip", "calls clip"]
    # private: nobody else sees it, or changes it
    for h in (hv, hx):
        assert client.get(f"{P}/{pid}", headers=h).status_code == 404
        assert all(p["id"] != pid for p in client.get(P, headers=h).json())
    # shared: everyone signed in reads it, each getting only the clips of resources they can read
    r = client.patch(f"{P}/{pid}", headers=hb, json={"shared": True})
    assert r.status_code == 200 and r.json()["shared"] is True
    assert db.rows("SELECT action, target FROM audit_log WHERE action = 'playlist.share'") == [
        {"action": "playlist.share", "target": f"playlist:{pid}"}
    ]
    seen = client.get(f"{P}/{pid}", headers=hv).json()
    assert [i["title"] for i in seen["items"]] == ["pods clip"] and seen["hidden"] == 0 and seen["mine"] is False
    assert "secret plan" not in str(seen)
    assert next(p for p in client.get(P, headers=hv).json() if p["id"] == pid)["count"] == 1
    assert [i["title"] for i in _items(client, pid, hx)] == ["calls clip"]
    # only its maker changes it
    item = _items(client, pid, hv)[0]["id"]
    assert client.post(url, headers=he, json={"recording": a, "t0": 0}).status_code == 404
    assert client.patch(f"{P}/{pid}", headers=he, json={"name": "Mine"}).status_code == 404
    assert client.delete(f"{url}/{item}", headers=hv).status_code == 404
    assert client.put(f"{P}/{pid}/order", headers=hv, json={"items": [item]}).status_code == 404
    assert client.delete(f"{P}/{pid}", headers=hv).status_code == 404
    # its maker losing access to calls: the clip is left out for them too, counted as hidden
    auth.set_role(db, auth.find_account(db, "both@x.io")["id"], store.ns_id(db, "calls"), None)
    mine = client.get(f"{P}/{pid}", headers=login(client, "both@x.io", "both password 1")).json()
    assert [i["title"] for i in mine["items"]] == ["pods clip"] and mine["hidden"] == 1


def test_clips_of_highlights(client, env):
    a, he, hv = env["a"], env["he"], env["hv"]
    h = client.post(
        f"/api/v1/resources/{a}/highlights", headers=he, json={"t0": 3000, "t1": 7000, "quote": "the capsid", "label": "Key claim"}
    ).json()
    pid = client.post(P, headers=hv, json={"name": "From highlights"}).json()["id"]
    r = client.post(f"{P}/{pid}/items", headers=hv, json={"highlight": h["id"]})
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["recording"], c["t0"], c["t1"], c["title"], c["quote"], c["highlight"]) == (a, 3000, 7000, "Key claim", "the capsid", h["id"])
    assert client.post(f"{P}/{pid}/items", headers=hv, json={"highlight": h["id"], "recording": env["b"]}).status_code == 400
    # a reader without access to the highlight's resource can't clip it
    hx = env["hx"]
    other = client.post(P, headers=hx, json={"name": "Elsewhere"}).json()["id"]
    assert client.post(f"{P}/{other}/items", headers=hx, json={"highlight": h["id"]}).status_code == 404
    # the clip stays when the highlight goes
    client.delete(f"/api/v1/resources/{a}/highlights/{h['id']}", headers=he)
    assert [i["title"] for i in _items(client, pid, hv)] == ["Key claim"]


def test_clips_go_with_their_resource_and_private_playlists_with_their_maker(client, env, db, cfg):
    from app.domain import deletion

    a, b, hv = env["a"], env["b"], env["hv"]
    pid = client.post(P, headers=hv, json={"name": "Going"}).json()["id"]
    keep = client.post(P, headers=hv, json={"name": "Kept", "shared": True}).json()["id"]
    for p in (pid, keep):
        client.post(f"{P}/{p}/items", headers=hv, json={"recording": a, "t0": 0})
        client.post(f"{P}/{p}/items", headers=hv, json={"recording": b, "t0": 0})
    deletion.delete(db, cfg, b)
    assert [i["recording"] for i in _items(client, pid, hv)] == [a]
    assert db.values("SELECT VALUE id FROM playlist_item WHERE recording = $r", r=b) == []
    auth.remove_account(db, auth.find_account(db, "vi@x.io")["id"])
    assert db.values("SELECT VALUE record::id(id) FROM playlist") == [keep]
    assert db.values("SELECT VALUE playlist FROM playlist_item") == [keep]


def test_agents_list_playlists_and_add_moments(client, env):
    from tests.api.test_mcp import tool, tool_error
    from tests.api.test_oauth import _bearer, _grant

    a, call, hv = env["a"], env["call"], env["hv"]
    # a read-only app lists but doesn't add
    _, tokens = _grant(client, hv, scope="read")
    ro = _bearer(tokens)
    assert tool(client, ro, "list_playlists") == {"playlists": []}
    assert "read-only" in tool_error(client, ro, "add_to_playlist", recording_id=a, t0=1000)
    # with none yet, a moment goes in a new playlist called Clips; then in the one used last
    got = tool(client, hv, "add_to_playlist", recording_id=a, t0=1000, t1=4000, title="Opening", note="from an agent")
    assert (got["playlist"], got["position"], got["t0"], got["t1"], got["title"]) == ("Clips", 0, 1000, 4000, "Opening")
    assert got["url"].endswith(f"/playlists/{got['playlist_id']}")
    other = client.post(P, headers=hv, json={"name": "Talks"}).json()["id"]
    assert tool(client, hv, "add_to_playlist", recording_id=a, t0=2000)["playlist_id"] == other
    # by name, made when there's none of that name; by id
    named = tool(client, hv, "add_to_playlist", recording_id=a, t0=0, playlist="clips")
    assert named["playlist_id"] == got["playlist_id"] and named["position"] == 1
    fresh = tool(client, hv, "add_to_playlist", recording_id=a, t0=0, playlist="Quotes")
    assert fresh["playlist"] == "Quotes" and fresh["playlist_id"] not in (other, got["playlist_id"])
    # only resources the person can read, and nothing is made for one they can't
    assert "not found" in tool_error(client, hv, "add_to_playlist", recording_id=call, t0=0, playlist="Nope")
    assert all(p["name"] != "Nope" for p in tool(client, hv, "list_playlists")["playlists"])
    assert "t0" in tool_error(client, hv, "add_to_playlist", recording_id=a)
    # one playlist, its clips in order with links to their moments
    one = tool(client, hv, "list_playlists", playlist_id=got["playlist_id"])
    assert (one["name"], one["clips"]) == ("Clips", 2)
    assert [(c.get("title"), c["t0"]) for c in one["items"]] == [("Opening", 1000), (None, 0)]
    assert one["items"][0]["url"].endswith(f"/resources/{a}?t=1") and one["items"][0]["note"] == "from an agent"
    listed = tool(client, hv, "list_playlists")["playlists"]
    assert [p["name"] for p in listed] == ["Quotes", "Clips", "Talks"]
