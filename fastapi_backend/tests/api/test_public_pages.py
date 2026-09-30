"""A recording's public page (docs/access.md): what visitors, signed-in people without permission and members see."""

from __future__ import annotations

import urllib.parse

import pytest

from app.domain import analyze, ingest, metadata, store
from tests.helpers import login, make_user, quiet, seed, write_wav

CLIP = "[00:00] Alice: A short clip about the capsid.\n[00:02] Bob: Indeed it is short.\n[00:02] Alice: Bye."
CC = "https://creativecommons.org/licenses/by/4.0/"


@pytest.fixture
def env(db, cfg, folder, client):
    a, b, call = seed(db, cfg, folder)
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text(CLIP)
    clip = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    db.q("DELETE section WHERE recording = $r", r=clip)  # one chapter, whatever the analysis found
    db.q("CREATE section CONTENT $d", d={"recording": clip, "idx": 0, "seg0": 0, "seg1": 3, "t0": 0, "t1": 2500, "title": "The capsid"})
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "viewer"})  # signed in, no role in pods
    return {
        "a": a,
        "b": b,
        "call": call,
        "clip": clip,
        "wav": wav,
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "ho": login(client, "out@x.io", "outsider password 1"),
        "ha": login(client, "root@x.io", "root password 1"),
    }


def page(client, rid, headers=None):
    return client.get(f"/api/v1/public/recordings/{rid}", headers=headers or {})


def test_visitors_see_a_public_recording_and_its_open_parts(client, new_client, env, db, cfg):
    clip, anon = env["clip"], new_client()
    metadata.save(db, cfg, clip, {"access": "public", "open": ["media", "index"], "rights": CC, "featured": True})
    r = page(anon, clip)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["view"], d["member"], d["access"], d["open"], d["featured"]) == ("public", False, "public", ["media", "index"], True)
    assert d["title"] and d["namespace"] == "pods" and d["media_kind"] == "audio"
    # the description is what IIIF publishes: rights, speakers (without workspace ids); access isn't part of it
    desc = d["description"]
    assert desc["rights"] == "http://creativecommons.org/licenses/by/4.0/"  # the canonical form of CC
    assert {c["name"] for c in desc["contributors"]} == {"Alice", "Bob"}
    assert all("speaker" not in c for c in desc["contributors"]) and "access" not in desc and "open" not in desc
    # open parts: the audio plays through its signed link; the chapter is listed; the transcript stays closed
    url = d["media"]["url"]
    path, _, query = url.partition("?")
    assert path == f"/api/v1/recordings/{clip}/audio" and "sig" in urllib.parse.parse_qs(query)
    got = anon.get(url, headers={"Range": "bytes=0-9"})
    assert (got.status_code, got.content) == (206, env["wav"].read_bytes()[:10])
    assert anon.get(path).status_code == 401
    assert d["chapters"] == [{"t0": 0, "t1": 2500, "title": "The capsid"}]
    assert d["transcript"] is None and d["closed"] == ["transcript"]
    # signed in without a role: the same page
    assert page(client, clip, env["ho"]).json()["view"] == "public"

    # with the transcript open too: who said what, and downloads anyone can fetch
    metadata.save(db, cfg, clip, {"open": ["media", "transcript", "index"]})
    t = page(anon, clip).json()["transcript"]
    assert [s["name"] for s in t["speakers"]] == ["Alice", "Bob"]
    assert [s["text"] for s in t["segments"]][0] == "A short clip about the capsid."
    assert {s["s"] for s in t["segments"]} == {s["key"] for s in t["speakers"]}
    vtt = next(x for x in t["downloads"] if x["format"] == "vtt")
    got = anon.get(vtt["url"])
    assert got.status_code == 200 and "A short clip about the capsid." in got.text


def test_closed_parts_stay_with_people_who_have_permission(client, new_client, env, db, cfg):
    clip, anon = env["clip"], new_client()
    metadata.save(db, cfg, clip, {"access": "public", "open": []})
    d = page(anon, clip).json()
    assert (d["media"], d["transcript"], d["chapters"]) == (None, None, None)
    assert d["closed"] == ["media", "transcript", "index"] and d["description"]
    # members see all of it; downloads come from the workspace when the transcript isn't open to everyone
    for h in (env["hv"], env["ha"]):
        d = page(client, clip, h).json()
        assert (d["view"], d["member"], d["closed"]) == ("full", True, [])
        assert d["media"]["url"] and d["chapters"] and d["transcript"]["segments"] and d["transcript"]["downloads"] == []


def test_restricted_and_private_recordings(client, new_client, env, db, cfg):
    clip, anon = env["clip"], new_client()
    metadata.save(db, cfg, clip, {"access": "restricted"})
    # visitors don't know it exists; signed-in people see its title with a lock; members see all of it
    assert page(anon, clip).status_code == 404
    d = page(client, clip, env["ho"]).json()
    assert (d["view"], d["title"], d["member"]) == ("locked", "clip", False)
    assert (d["description"], d["media"], d["transcript"], d["chapters"]) == (None, None, None, None)
    assert d["closed"] == ["media", "transcript", "index"]
    assert page(client, clip, env["hv"]).json()["view"] == "full"

    metadata.save(db, cfg, clip, {"access": "private"})
    assert page(anon, clip).status_code == 404
    assert page(client, clip, env["ho"]).status_code == 404
    assert page(client, clip, env["hv"]).json()["view"] == "full"
    # recordings that follow a private namespace default, and ones that don't exist, look the same
    assert page(anon, env["a"]).status_code == 404
    assert page(anon, 999999).status_code == 404


def test_text_that_looks_like_a_link_stays_text(client, new_client, env, db, cfg):
    clip = env["clip"]
    victim = f"/api/v1/recordings/{env['call']}/audio"
    metadata.save(db, cfg, clip, {"access": "public", "attribution": victim, "open": ["transcript"]})
    db.q("UPDATE segment SET text = $t WHERE recording = $r AND idx = 0", t=victim, r=clip)
    d = page(new_client(), clip).json()
    assert d["description"]["attribution"] == {"none": [victim]}
    assert d["transcript"]["segments"][0]["text"] == victim


def test_home_features_public_recordings_for_everyone(client, new_client, env, db, cfg):
    clip, a, call = env["clip"], env["a"], env["call"]
    metadata.save(db, cfg, clip, {"access": "public", "featured": True})
    metadata.save(db, cfg, a, {"access": "public"})
    metadata.save(db, cfg, call, {"access": "restricted", "featured": True})  # featured, but only public ones are shown

    def home(headers=None):
        r = client.get("/api/v1/public/home", headers=headers or {})
        assert r.status_code == 200, r.text
        d = r.json()
        return [c["id"] for c in d["featured"]], {c["name"]: (c["recordings"], c["member"]) for c in d["collections"]}

    # visitors: the featured public recording; pods has two public recordings, calls nothing they may see
    assert home() == ([clip], {"pods": (2, False)})
    # signed in: calls' restricted recording is listed (locked); members count everything in their namespaces
    assert home(env["hv"]) == ([clip], {"pods": (3, True), "calls": (1, False)})
    assert home(env["ho"]) == ([clip], {"pods": (2, False), "calls": (1, True)})
    assert home(env["ha"])[1] == {"pods": (3, True), "calls": (1, True)}
    card = new_client().get("/api/v1/public/home").json()["featured"][0]
    assert (card["view"], card["namespace"], card["featured"], card["media_kind"]) == ("public", "pods", True, "audio")


def test_collection_pages(client, new_client, env, db, cfg):
    clip, a, call = env["clip"], env["a"], env["call"]
    metadata.save(db, cfg, clip, {"access": "public"})
    metadata.save(db, cfg, a, {"access": "public"})
    metadata.save(db, cfg, call, {"access": "restricted"})
    metadata.save_namespace(db, store.ns_id(db, "pods"), meta={"summary": {"en": ["Our podcast."]}})
    anon = new_client()

    def coll(ns, headers=None, **params):
        return client.get(f"/api/v1/public/collections/{ns}", params=params, headers=headers or {})

    d = coll("pods").json()
    assert (d["name"], d["label"], d["summary"], d["member"], d["total"]) == ("pods", "pods", "Our podcast.", False, 2)
    assert {x["id"] for x in d["items"]} == {clip, a} and {x["view"] for x in d["items"]} == {"public"}
    # restricted recordings: hidden from visitors (a collection with nothing for them looks absent), locked for others
    assert coll("calls").status_code == 404 and anon.get("/api/v1/public/collections/nowhere").status_code == 404
    locked = coll("calls", env["hv"]).json()["items"]
    assert [(x["id"], x["view"], x["summary"], x["poster"]) for x in locked] == [(call, "locked", None, None)]
    # members see all of their namespace's recordings
    d = coll("pods", env["hv"]).json()
    assert (d["member"], d["total"]) == (True, 3) and {x["view"] for x in d["items"]} == {"full"}
    # recordings that follow a public namespace default are listed too; pages of the list
    metadata.save_namespace(db, store.ns_id(db, "pods"), profile={"default_access": "public"})
    assert coll("pods").json()["total"] == 3
    page2 = coll("pods", limit=1, offset=1).json()
    assert (page2["total"], len(page2["items"])) == (3, 1)


def test_posters_come_with_media_the_visitor_may_play(client, new_client, env, db, cfg):
    clip = env["clip"]
    db.q("CREATE shot CONTENT $d", d={"recording": clip, "idx": 0, "t0": 0, "t1": 1000, "frame": "f0.jpg"})
    metadata.save(db, cfg, clip, {"access": "public", "featured": True, "open": ["media"]})
    anon = new_client()
    poster = anon.get("/api/v1/public/home").json()["featured"][0]["poster"]
    path, _, query = poster.partition("?")
    assert path == f"/api/v1/recordings/{clip}/frames/f0.jpg" and "sig=" in query
    metadata.save(db, cfg, clip, {"open": ["transcript"]})
    assert anon.get("/api/v1/public/home").json()["featured"][0]["poster"] is None
    assert client.get("/api/v1/public/home", headers=env["hv"]).json()["featured"][0]["poster"]  # members may play it


def test_search_finds_only_what_the_visitor_may_read(client, new_client, env, db, cfg):
    clip, a, b, call = env["clip"], env["a"], env["b"], env["call"]
    metadata.save(db, cfg, clip, {"access": "public"})  # every part open
    metadata.save(db, cfg, a, {"access": "public", "open": ["media"]})  # its transcript is closed
    metadata.save(db, cfg, call, {"access": "restricted"})  # b stays private
    anon = new_client()

    def search(q, headers=None, **params):
        r = client.get("/api/v1/public/search", params={"q": q, **params}, headers=headers or {})
        assert r.status_code == 200, r.text
        return r.json()

    def ids(d):
        return [x["id"] for x in d["items"]]

    # visitors: only transcripts open to everyone are searched; restricted and private recordings aren't there
    d = search("capsid")
    assert ids(d) == [clip] and d["total"] == 1
    hit = d["items"][0]["hits"][0]
    assert "<mark>capsid</mark>" in hit["snippet"] and hit["t0"] >= 0
    # a closed transcript is found by its title only, without its lines
    d = search("ep1")
    assert ids(d) == [a] and d["items"][0]["hits"] == []
    assert search("call")["total"] == 0 and anon.get("/api/v1/public/search", params={"q": "Dave"}).json()["total"] == 0
    assert search("exploit")["total"] == 0 and ids(search("exploit", env["hv"])) == [b]  # b is private: members only
    # signed in: a restricted recording matches on its title, locked; its transcript is never searched
    d = search("call", env["hv"])
    assert [(x["id"], x["view"], x["hits"]) for x in d["items"]] == [(call, "locked", [])]
    assert set(ids(search("capsid", env["hv"]))) == {clip, a}  # a member of pods reads its closed transcripts too
    assert set(ids(search("capsid", env["ho"]))) == {clip, call}  # a member of calls, not of pods
    # title matches come first; pages of results; nothing to look for
    db.q("UPDATE $r SET title = 'The capsid episode'", r=store.R("recording", a))
    assert ids(search("capsid")) == [a, clip]
    page = search("capsid", limit=1, offset=1)
    assert (page["total"], ids(page)) == (2, [clip])
    assert search("   ")["total"] == 0
