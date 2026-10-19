"""The player's captions (docs/video.md#captions): an asset's transcript, and its timed supplementary transcripts,
captions and translations, as WebVTT tracks with signed links that work without the Authorization header, in the
workspace's player, with a share link and on the public page, and only with what the viewer may read."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import ingest, metadata
from tests.helpers import login, make_user, quiet, write_wav

CAPTIONS = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nZanzibar at dawn.\n"
SUBTITLES = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nZanzibar à l'aube.\n"


def add(client, h, rid, name, body, role, **params):
    r = client.post(
        f"/api/v1/resources/{rid}/files",
        headers={**h, "Content-Type": "application/octet-stream"},
        params={"name": name, "role": role, **params},
        content=body.encode(),
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def env(client, db, cfg, folder):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "guest@x.io", "guest password 1")
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text("[00:00] Alice: A short clip.\n[00:02] Bob: Indeed.")
    rid = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    he = login(client, "ed@x.io", "editor password 1")
    files = {
        "captions": add(client, he, rid, "harbour.vtt", CAPTIONS, "captions", language="en", label="English captions"),
        "translation": add(client, he, rid, "fr.vtt", SUBTITLES, "translation", language="fr"),
        "untimed": add(client, he, rid, "notes.txt", "Le port.", "translation"),
        "attachment": add(client, he, rid, "notes.vtt", CAPTIONS, "attachment"),
    }
    return {"rid": rid, "he": he, "hg": login(client, "guest@x.io", "guest password 1"), "files": files}


def test_the_player_offers_the_transcript_and_timed_files_as_tracks(client, env):
    rid, f = env["rid"], env["files"]
    anon = TestClient(client.app, base_url="http://127.0.0.1")
    tracks = client.get(f"/api/v1/recordings/{rid}/player", headers=env["he"]).json()["captions"]
    assert [(t["kind"], t["label"], t["language"]) for t in tracks] == [
        ("captions", "Transcript", None),
        ("captions", "English captions", "en"),
        ("subtitles", "Translation: fr.vtt", "fr"),  # untimed text and attachments make no track
    ]
    assert [t["url"].split("?")[0] for t in tracks] == [
        f"/iiif/{rid}/transcript.vtt",
        f"/iiif/{rid}/files/{f['captions']['id']}.vtt",
        f"/iiif/{rid}/files/{f['translation']['id']}.vtt",
    ]
    # a <track> sends no Authorization header: the signed links work on their own, the plain ones don't
    first = anon.get(tracks[0]["url"])
    assert first.status_code == 200 and first.text.startswith("WEBVTT") and "A short clip." in first.text
    assert "Zanzibar at dawn." in anon.get(tracks[1]["url"]).text
    assert "Zanzibar à l'aube." in anon.get(tracks[2]["url"]).text
    assert anon.get(f"/iiif/{rid}/transcript.vtt").status_code == 404  # private
    other = tracks[0]["url"].replace("transcript.vtt", f"files/{f['captions']['id']}.vtt")
    assert anon.get(other).status_code == 404  # one track's signature opens that track only
    # a share link plays the transcript as captions; its files stay with the namespace
    share = client.post(f"/api/v1/recordings/{rid}/share", headers=env["he"]).json()
    shared = anon.get(f"/api/v1/recordings/{rid}/player", params={"s": share["token"]}).json()["captions"]
    assert [t["label"] for t in shared] == ["Transcript"] and anon.get(shared[0]["url"]).status_code == 200


def test_the_public_page_offers_what_the_visitor_may_read_as_tracks(client, env, db, cfg):
    rid = env["rid"]
    anon = TestClient(client.app, base_url="http://127.0.0.1")
    metadata.save(db, cfg, rid, {"access": "public", "open": ["media"]})
    assert anon.get(f"/api/v1/public/recordings/{rid}").json()["captions"] == []  # the text is closed
    metadata.save(db, cfg, rid, {"open": ["media", "transcript"]})
    tracks = anon.get(f"/api/v1/public/recordings/{rid}").json()["captions"]
    assert [t["label"] for t in tracks] == ["Transcript", "English captions", "Translation: fr.vtt"]
    assert all(anon.get(t["url"]).text.startswith("WEBVTT") for t in tracks)
    metadata.save(db, cfg, rid, {"open": ["transcript"]})
    assert anon.get(f"/api/v1/public/recordings/{rid}").json()["captions"] == []  # nothing to play them over
    # restricted: a signed-in visitor sees its title only; a member sees all of it
    metadata.save(db, cfg, rid, {"access": "restricted"})
    assert client.get(f"/api/v1/public/recordings/{rid}", headers=env["hg"]).json()["captions"] == []
    full = client.get(f"/api/v1/public/recordings/{rid}", headers=env["he"]).json()["captions"]
    assert [t["label"] for t in full] == ["Transcript", "English captions", "Translation: fr.vtt"]
    assert "Zanzibar at dawn." in anon.get(full[1]["url"]).text
