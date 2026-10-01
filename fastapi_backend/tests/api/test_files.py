"""A resource's files: its primary file, and supplementary transcripts, captions, translations, indexes, thumbnails
and attachments, read into lines search finds, downloadable through signed links, public as the resource's open parts
are, and listed in IIIF."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.domain import files, iiif, iiif_auth, ingest, metadata, settings, store
from tests.helpers import login, make_user, quiet, seed, write_wav

R = store.R
CAPTIONS = "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nThe zanzibar harbour at dawn.\n\n00:00:04.000 --> 00:00:06.000\n<v Ann>Fishing boats come in.\n"
TRANSLATION = "Le port de Zanzibar à l'aube.\n\nLes bateaux de pêche rentrent."
INDEX = json.dumps(
    [
        {"start": 0, "title": "Arrival", "synopsis": "Reaching the quay.", "keywords": ["harbour", "dawn"]},
        {"start": "00:00:04", "title": "The boats", "synopsis": "Fishing boats return."},
    ]
)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    make_user(db, "guest@x.io", "guest password 1")
    return {
        "a": a,
        "b": b,
        "call": call,
        "hr": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hx": login(client, "out@x.io", "outsider password 1"),
        "hg": login(client, "guest@x.io", "guest password 1"),
    }


def add(client, h, rid, name, body, role, **params):
    """Add a file the way the web app does: the raw body, its name and role in the query."""
    content = body.encode() if isinstance(body, str) else body
    return client.post(
        f"/api/v1/resources/{rid}/files",
        headers={**h, "Content-Type": "application/octet-stream"},
        params={"name": name, "role": role, **params},
        content=content,
    )


def cfg_duration(client, h, rid):
    return client.get(f"/api/v1/resources/{rid}", headers=h).json()["duration_ms"]


def listed(client, h, rid):
    r = client.get(f"/api/v1/resources/{rid}/files", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_add_list_download_and_read_lines(client, env, cfg):
    he, hv, a = env["he"], env["hv"], env["a"]
    cap = add(client, he, a, "harbour.vtt", CAPTIONS, "captions", language="en", label="English captions")
    assert cap.status_code == 200, cap.text
    cap = cap.json()
    assert (cap["role"], cap["name"], cap["language"], cap["label"], cap["lines"], cap["timed"]) == (
        "captions",
        "harbour.vtt",
        "en",
        "English captions",
        2,
        True,
    )
    assert cap["content_type"] == "text/vtt" and cap["size"] == len(CAPTIONS.encode()) and cap["created_by"] == "ed@x.io"
    tr = add(client, he, a, "harbour-fr.txt", TRANSLATION, "translation", language="fr").json()
    assert (tr["lines"], tr["timed"]) == (2, False)  # prose: no times
    ix = add(client, he, a, "index.json", INDEX, "index").json()
    assert (ix["lines"], ix["timed"]) == (2, True)
    page = add(client, he, a, "evil.html", "<script>alert(1)</script>", "attachment").json()
    assert page["lines"] is None and page["timed"] is None
    pic = add(client, he, a, "../../cover.png", PNG, "thumbnail").json()
    assert pic["name"] == "cover.png"  # only the last part of a name is kept

    seen = listed(client, hv, a)
    assert seen["primary"] is None  # a transcript without audio
    assert not seen["can_change"] and listed(client, he, a)["can_change"]
    assert [f["role"] for f in seen["files"]] == ["captions", "translation", "index", "thumbnail", "attachment"]
    assert not any(f["public"] for f in seen["files"])  # a private recording
    link = next(f["download"] for f in seen["files"] if f["id"] == cap["id"])
    assert re.search(r"/api/v1/recordings/\d+/files/\d+/download\?full=1&exp=\d+&sig=", link)  # a member's link

    anon = TestClient(client.app, base_url="http://127.0.0.1")
    got = anon.get(link)  # a signed link works on its own
    assert got.status_code == 200 and got.text == CAPTIONS
    assert 'attachment; filename="harbour.vtt"' == got.headers["content-disposition"]
    assert got.headers["x-content-type-options"] == "nosniff" and "sandbox" in got.headers["content-security-policy"]
    plain = f"/api/v1/resources/{a}/files/{cap['id']}/download"
    assert anon.get(plain).status_code == 401
    assert anon.get(link[:-4] + "AAAA").status_code == 401  # tampered
    assert client.get(plain, headers=hv).status_code == 200
    evil = client.get(f"/api/v1/resources/{a}/files/{page['id']}/download", headers=hv)
    assert evil.headers["content-type"] == "application/octet-stream"  # a page is never served as one
    assert evil.headers["content-disposition"].startswith("attachment")

    lines = client.get(f"/api/v1/resources/{a}/files/{cap['id']}/lines", headers=hv).json()
    assert lines["total"] == 2
    assert [(x["t0"], x["t1"], x["text"], x["speaker"]) for x in lines["lines"]] == [
        (1000, 3500, "The zanzibar harbour at dawn.", None),
        (4000, 6000, "Fishing boats come in.", "Ann"),
    ]
    second = client.get(f"/api/v1/resources/{a}/files/{tr['id']}/lines", headers=hv, params={"offset": 1, "limit": 1}).json()
    assert [(x["t0"], x["text"]) for x in second["lines"]] == [(None, "Les bateaux de pêche rentrent.")]
    entries = client.get(f"/api/v1/resources/{a}/files/{ix['id']}/lines", headers=hv).json()["lines"]
    assert [(x["t0"], x["t1"], x["title"], x["synopsis"], x["keywords"]) for x in entries] == [
        (0, 4000, "Arrival", "Reaching the quay.", ["harbour", "dawn"]),
        (4000, cfg_duration(client, hv, a), "The boats", "Fishing boats return.", None),  # the last runs to the end
    ]
    assert (files.folder(cfg, a) / str(cap["id"]) / "harbour.vtt").read_text() == CAPTIONS
    # another recording's file isn't this one's
    assert client.get(f"/api/v1/resources/{env['b']}/files/{cap['id']}/download", headers=hv).status_code == 404


def test_who_may_add_change_and_delete(client, env, db, cfg):
    a, b = env["a"], env["b"]
    assert add(client, env["hv"], a, "x.vtt", CAPTIONS, "captions").status_code == 403
    assert add(client, env["hx"], a, "x.vtt", CAPTIONS, "captions").status_code == 404
    assert client.get(f"/api/v1/resources/{a}/files", headers=env["hx"]).status_code == 404
    assert add(client, env["hg"], a, "x.vtt", CAPTIONS, "captions").status_code == 404
    f = add(client, env["he"], a, "x.vtt", CAPTIONS, "captions").json()
    url = f"/api/v1/resources/{a}/files/{f['id']}"
    assert client.patch(url, headers=env["hv"], json={"label": "Mine"}).status_code == 403
    assert client.delete(url, headers=env["hv"]).status_code == 403
    assert client.delete(url, headers=env["hx"]).status_code == 404
    # a read-only API key can't add one
    key = client.post("/api/v1/tokens", headers=env["he"], json={"name": "ro", "scope": "read"}).json()["token"]
    assert add(client, {"Authorization": f"Bearer {key}"}, a, "y.vtt", CAPTIONS, "captions").status_code == 403
    # an editor of just one collection, with no role in the namespace
    coll = "/api/v1/namespaces/pods/collections"
    talks = client.post(coll, headers=env["he"], json={"name": "Talks"}).json()["id"]
    client.post("/api/v1/recordings/collection", headers=env["he"], json={"recordings": [a], "collection": talks})
    assert client.put(f"{coll}/{talks}/members", headers=env["hr"], json={"email": "guest@x.io", "role": "editor"}).status_code == 200
    hg = login(client, "guest@x.io", "guest password 1")
    assert add(client, hg, a, "z.vtt", CAPTIONS, "captions").status_code == 200
    assert add(client, hg, b, "z.vtt", CAPTIONS, "captions").status_code == 404
    assert len(listed(client, hg, a)["files"]) == 2


def test_what_files_are_refused(client, env, db, cfg):
    he, a = env["he"], env["a"]
    r = add(client, he, a, "notes.txt", "Some notes", "captions")
    assert (r.status_code, r.json()["detail"]) == (400, "Captions files are .srt .vtt; this is .txt.")
    assert add(client, he, a, "x.vtt", CAPTIONS, "subtitles").status_code == 422
    r = add(client, he, a, "empty.vtt", "WEBVTT\n\n", "captions")
    assert (r.status_code, r.json()["detail"]) == (400, "empty.vtt has no text Lens can read as captions.")
    r = add(client, he, a, "index.json", "{not json", "index")
    assert r.status_code == 400 and r.json()["detail"].startswith("Lens couldn't read index.json as an index: ")
    r = add(client, he, a, "index.xml", "<ROOT><record/></ROOT>", "index")
    assert r.json()["detail"] == "Lens couldn't read index.xml as an index: it has no index points (OHMS <point> elements)"
    r = add(client, he, a, "scan.pdf", b"%PDF-1.4 not really", "transcript")
    assert r.status_code == 400 and "/" not in r.json()["detail"]  # no server paths in errors
    assert add(client, he, a, "x.vtt", b"", "captions").json()["detail"] == "The file is empty."
    assert add(client, he, a, "...", CAPTIONS, "attachment").json()["detail"] == "Name the file."
    settings.save(db, cfg, "server", {"max_upload_mb": 0})
    r = add(client, he, a, "x.vtt", CAPTIONS, "captions")
    assert (r.status_code, r.json()["detail"]) == (413, "Files can be up to 0 MB.")
    assert listed(client, he, a)["files"] == [] and not list((files.folder(cfg, a).parent / ".partial").iterdir())


def test_change_a_file_and_the_audit_log(client, env, db, cfg):
    he, a = env["he"], env["a"]
    f = add(client, he, a, "harbour.vtt", CAPTIONS, "attachment").json()
    assert f["lines"] is None
    url = f"/api/v1/resources/{a}/files/{f['id']}"
    got = client.patch(url, headers=he, json={"role": "captions", "label": "Captions", "language": "en", "description": "From the BBC."})
    assert got.status_code == 200, got.text
    assert (got.json()["role"], got.json()["lines"], got.json()["label"], got.json()["description"]) == (
        "captions",
        2,
        "Captions",
        "From the BBC.",
    )
    hits = client.get("/api/v1/search", headers=he, params={"q": "zanzibar"}).json()["hits"]
    assert [h["source"] for h in hits] == ["file"]
    r = client.patch(url, headers=he, json={"role": "thumbnail"})
    assert (r.status_code, r.json()["detail"]) == (400, "Thumbnail files are .gif .jpeg .jpg .png .webp; this is .vtt.")
    assert client.patch(url, headers=he, json={"role": None}).status_code == 400
    assert client.patch(url, headers=he, json={"language": "not a language"}).status_code == 400
    cleared = client.patch(url, headers=he, json={"label": None, "description": None}).json()
    assert cleared["label"] is None and cleared["description"] is None and cleared["language"] == "en"
    back = client.patch(url, headers=he, json={"role": "attachment"}).json()
    assert back["lines"] is None and back["timed"] is None
    assert client.get("/api/v1/search", headers=he, params={"q": "zanzibar"}).json()["hits"] == []
    assert client.delete(url, headers=he).status_code == 200
    assert client.get(url + "/download", headers=he).status_code == 404
    assert not (files.folder(cfg, a) / str(f["id"])).exists()
    log = [x for x in client.get("/api/v1/audit", headers=env["hr"]).json() if x["action"].startswith("file.")]
    assert sorted(x["action"] for x in log) == ["file.add", "file.delete", "file.update", "file.update", "file.update"]
    changes = [x["detail"]["changes"] for x in log if x["action"] == "file.update"]
    assert {"role": ["captions", "attachment"]} in changes and {
        "label": ["Captions", None],
        "description": ["From the BBC.", None],
    } in changes
    assert {x["target"] for x in log} == {f"recording:{a}"}


def test_search_finds_the_lines_of_files(client, env, db, cfg):
    he, a = env["he"], env["a"]
    cap = add(client, he, a, "harbour.vtt", CAPTIONS, "captions", label="English captions").json()
    tr = add(client, he, a, "fr.txt", TRANSLATION, "translation").json()
    res = client.get("/api/v1/search", headers=env["hv"], params={"q": "zanzibar", "facets": True}).json()
    by_file = {h["file"]: h for h in res["hits"]}
    assert set(by_file) == {cap["id"], tr["id"]} and all(h["source"] == "file" for h in res["hits"])
    hit = by_file[cap["id"]]
    assert (hit["recording_id"], hit["t0"], hit["t1"], hit["file_label"], hit["file_role"], hit["line"]) == (
        a,
        1000,
        3500,
        "English captions",
        "captions",
        0,
    )
    assert "<mark>" in hit["snippet"].lower()
    assert by_file[tr["id"]]["t0"] is None and by_file[tr["id"]]["file_label"] == "fr.txt"  # untimed, no label
    assert res["facets"]["recordings"] == [{"id": a, "title": res["hits"][0]["title"], "count": 2}]
    # what was said stays what a speaker filter finds
    spk = db.values("SELECT VALUE speaker FROM segment WHERE recording = $r AND speaker > 0 LIMIT 1", r=a)[0]
    assert client.get("/api/v1/search", headers=he, params={"q": "zanzibar", "speaker": spk}).json()["hits"] == []
    # only in what someone may read
    assert client.get("/api/v1/search", headers=env["hx"], params={"q": "zanzibar"}).json()["hits"] == []
    assert client.get("/api/v1/search", headers=he, params={"q": "zanzibar", "ns": "calls"}).status_code == 404


def test_moving_and_deleting_a_resource(client, env, db, cfg):
    hr, a = env["hr"], env["a"]
    f = add(client, hr, a, "harbour.vtt", CAPTIONS, "captions").json()
    assert client.post(f"/api/v1/resources/{a}/move", headers=hr, json={"namespace": "calls"}).status_code == 200
    assert db.values("SELECT VALUE space FROM file_line WHERE file = $f", f=f["id"]) == [store.ns_id(db, "calls")] * 2
    assert client.get("/api/v1/search", headers=env["hv"], params={"q": "zanzibar"}).json()["hits"] == []
    assert [h["file"] for h in client.get("/api/v1/search", headers=env["hx"], params={"q": "zanzibar"}).json()["hits"]] == [f["id"]]
    assert client.get(f"/api/v1/resources/{a}/files/{f['id']}/download", headers=env["hx"]).text == CAPTIONS
    assert client.delete(f"/api/v1/resources/{a}", headers=hr).status_code == 200
    assert db.values("SELECT VALUE id FROM resource_file") == [] and db.values("SELECT VALUE id FROM file_line") == []
    assert not files.folder(cfg, a).exists()


def test_public_resources_open_their_files_with_their_parts(client, env, db, cfg):
    he, a = env["he"], env["a"]
    ids = {
        role: add(client, he, a, name, body, role).json()["id"]
        for role, name, body in (
            ("captions", "harbour.vtt", CAPTIONS),
            ("index", "index.json", INDEX),
            ("thumbnail", "cover.png", PNG),
            ("attachment", "release.pdf", b"%PDF-1.4"),
        )
    }
    metadata.save(db, cfg, a, {"access": "public", "open": ["transcript"]})
    anon = TestClient(client.app, base_url="http://127.0.0.1")
    page = anon.get(f"/api/v1/public/recordings/{a}").json()
    assert [f["id"] for f in page["files"]] == [ids["captions"]] and page["files_closed"] == 3
    assert anon.get(page["files"][0]["url"]).text == CAPTIONS
    flags = {f["id"]: f["public"] for f in listed(client, he, a)["files"]}
    assert flags == {ids["captions"]: True, ids["index"]: False, ids["thumbnail"]: False, ids["attachment"]: False}
    metadata.save(db, cfg, a, {"open": ["transcript", "index", "media"]})
    page = anon.get(f"/api/v1/public/recordings/{a}").json()
    assert {f["role"] for f in page["files"]} == {"captions", "index", "thumbnail"} and page["files_closed"] == 1
    # signed in without permission: the attachment is closed, so they may ask
    assert client.get(f"/api/v1/public/recordings/{a}", headers=env["hx"]).json()["can_request"] is True
    # with permission, every file
    full = client.get(f"/api/v1/public/recordings/{a}", headers=env["hv"]).json()
    assert len(full["files"]) == 4 and full["files_closed"] == 0
    metadata.save(db, cfg, a, {"access": "private"})
    assert anon.get(f"/api/v1/public/recordings/{a}").status_code == 404


def test_iiif_lists_every_file(client, env, db, cfg, folder):
    he = env["he"]
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text("[00:00] Alice: A short clip.\n[00:02] Bob: Indeed.")
    rid = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    cap = add(client, he, rid, "harbour.srt", "1\n00:00:01,000 --> 00:00:02,000\nZanzibar.\n", "captions", language="en").json()
    tr2 = add(client, he, rid, "fr.txt", TRANSLATION, "translation").json()
    ix = add(client, he, rid, "index.txt", "00:00 Arrival\nReaching the quay.\n00:02 The boats", "index").json()
    att = add(client, he, rid, "release.pdf", b"%PDF-1.4", "attachment").json()
    pic = add(client, he, rid, "cover.png", PNG, "thumbnail").json()
    primary = listed(client, he, rid)["primary"]
    assert (primary["kind"], primary["name"], primary["size"]) == ("audio", "clip.wav", wav.stat().st_size)
    metadata.save(db, cfg, rid, {"access": "public", "open": ["transcript", "index"]})  # media closed
    anon = TestClient(client.app, base_url="https://127.0.0.1")
    m = anon.get(f"/iiif/{rid}/manifest").json()
    assert iiif.validate(m) in (None, [])
    base = f"https://127.0.0.1/iiif/{rid}"
    rendering = {r["id"]: r for r in m["rendering"]}
    assert {f"{base}/files/{x['id']}" for x in (cap, tr2, ix, att, pic)} <= set(rendering)
    assert "service" not in rendering[f"{base}/files/{cap['id']}"]  # open with the transcript
    assert rendering[f"{base}/files/{att['id']}"]["service"][0]["type"] == "AuthProbeService2"  # never open
    assert rendering[f"{base}/files/{pic['id']}"]["type"] == "Image" and "service" in rendering[f"{base}/files/{pic['id']}"]
    bodies = [x["body"] for x in m["items"][0]["annotations"][0]["items"]]
    assert [b["id"] for b in bodies[1:]] == [f"{base}/files/{cap['id']}.vtt"]  # the untimed translation has no captions
    assert bodies[1]["language"] == "en"
    toc = next(s for s in m["structures"] if s["id"] == f"{base}/range/file{ix['id']}")
    assert [x["label"]["none"][0] for x in toc["items"]] == ["Arrival", "The boats"]
    assert "thumbnail" not in m or all("/files/" not in t["id"] for t in m["thumbnail"])  # media closed

    vtt = anon.get(f"/iiif/{rid}/files/{cap['id']}.vtt")
    assert vtt.status_code == 200 and vtt.text.startswith("WEBVTT") and "Zanzibar." in vtt.text
    assert vtt.headers["access-control-allow-origin"] == "*"
    assert anon.get(f"/iiif/{rid}/files/{tr2['id']}.vtt").status_code == 404
    assert anon.get(f"/iiif/{rid}/files/{att['id']}").status_code == 401
    assert anon.get(f"/iiif/{rid}/files/x").status_code == 404
    assert anon.get(f"/iiif/{env['a']}/files/{cap['id']}").status_code == 404  # another resource's

    probe = f"/iiif/auth/probe/{rid}/file{att['id']}"
    assert anon.get(probe).json()["status"] == 401
    assert anon.get(f"/iiif/auth/probe/{rid}/file{cap['id']}").json()["status"] == 200
    assert anon.get(f"/iiif/auth/probe/{rid}/file999").json()["status"] == 404
    acct = db.values("SELECT VALUE record::id(id) FROM account WHERE email = 'vi@x.io'")[0]
    token, _ = iiif_auth.issue_token(db, cfg, acct, "https://viewer.example")
    res = anon.get(probe, headers={"Authorization": f"Bearer {token}"}).json()
    assert res["status"] == 302 and res["location"]["format"] == "application/pdf"
    assert anon.get(res["location"]["id"].replace("https://127.0.0.1", "")).content == b"%PDF-1.4"
    vres = anon.get(f"/iiif/auth/probe/{rid}/vtt{cap['id']}", headers={"Authorization": f"Bearer {token}"}).json()
    assert vres["status"] == 200  # open anyway

    metadata.save(db, cfg, rid, {"open": ["transcript", "index", "media"]})
    m = anon.get(f"/iiif/{rid}/manifest").json()
    assert m["thumbnail"][0]["id"] == f"{base}/files/{pic['id']}"
    assert anon.get(f"/iiif/{rid}/files/{pic['id']}").content == PNG
