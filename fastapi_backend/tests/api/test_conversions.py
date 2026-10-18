"""The convert step: a video's soundtrack as an audio asset, a document's pages as image assets."""

from __future__ import annotations

import pathlib
import shutil

import pytest

from app.domain import ingest, keyring, settings, store, video
from tests.helpers import drain, login, make_user, quiet, text_pdf, write_wav
from tests.video_helpers import make_video, needs_ffmpeg

POPPLER = pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs poppler-utils")


@pytest.fixture
def h(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def _pipeline(client, h, steps, name="Convert"):
    r = client.post("/api/v1/pipelines", headers=h, json={"name": name, "steps": steps})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _run(client, h, db, cfg, pid, rid):
    r = client.post(f"/api/v1/pipelines/{pid}/run", headers=h, json={"recording": rid})
    assert r.status_code == 200, r.text
    drain(db, cfg)
    job = db.one("SELECT status, log FROM $j", j=store.R("job", r.json()["job"]))
    assert job["status"] == "succeeded", job["log"][-5:]
    return " ".join(job["log"])


def _made(db, rid):
    return db.rows(
        "SELECT record::id(id) AS id, title, source, path, status, converted_from, space, collection FROM recording "
        "WHERE converted_from.resource = $r ORDER BY id",
        r=rid,
    )


def _video(db, cfg, folder):
    mp4, tr = folder / "review.mp4", folder / "review.txt"
    make_video(mp4)
    tr.write_text("[00:00] Mara: Here is the invoice.\n[00:03] Tom: And this is Atlas.")
    return ingest.import_transcript(db, cfg, "pods", tr, audio=mp4, log=quiet)


def test_settings_are_checked(client, h):
    bad = [
        {"type": "convert"},
        {"type": "convert", "to": "pdf"},
        {"type": "convert", "to": "images", "format": "png"},
        {"type": "convert", "to": "audio", "format": "ogg"},
        {"type": "convert", "to": "audio", "pipeline": 999},
        {"type": "convert", "to": "audio", "pipeline": "standard"},
        {"type": "analyze", "to": "audio"},  # only a convert step converts
    ]
    for step in bad:
        r = client.post("/api/v1/pipelines", headers=h, json={"name": "x", "steps": [step]})
        assert r.status_code == 400, step
    other = _pipeline(client, h, ["summarize"], "After")
    ok = {"type": "convert", "to": "audio", "format": "mp3", "pipeline": other}
    pid = _pipeline(client, h, [ok, {"type": "convert", "to": "images", "pipeline": "none"}])
    assert client.get(f"/api/v1/pipelines/{pid}", headers=h).json()["steps"][0] == ok
    assert "convert" in client.get("/api/v1/pipelines", headers=h).json()["asset_steps"]


@needs_ffmpeg
def test_a_videos_soundtrack_becomes_an_audio_asset(client, h, db, cfg, folder):
    rid = _video(db, cfg, folder)
    pid = _pipeline(client, h, [{"type": "convert", "to": "audio", "pipeline": "none"}])
    log = _run(client, h, db, cfg, pid, rid)
    assert "audio asset (m4a, copied)" in log  # the video's sound is AAC: copied, not encoded again
    [a] = _made(db, rid)
    assert (a["source"], a["status"], a["converted_from"], a["title"]) == (
        "audio",
        "new",
        {"resource": rid, "to": "audio"},
        "review (audio)",
    )
    assert video.probe_media(a["path"])["kind"] == "audio" and a["path"].endswith(f"/uploads/pods/converted-{rid}/review.m4a")
    assert not db.values("SELECT VALUE id FROM job WHERE recording = $r", r=a["id"])  # "none": kept as it is

    d = client.get(f"/api/v1/recordings/{a['id']}", headers=h).json()
    assert d["converted_from"] == {"resource": rid, "to": "audio", "page": None, "title": "review"}

    again = _run(client, h, db, cfg, pid, rid)
    assert f"kept the audio made from it before (asset {a['id']})" in again and len(_made(db, rid)) == 1

    mp3 = _pipeline(client, h, [{"type": "convert", "to": "audio", "format": "mp3", "force": True}], "MP3")
    log = _run(client, h, db, cfg, mp3, rid)
    assert "audio asset (mp3, encoded)" in log
    [b] = _made(db, rid)
    assert b["id"] == a["id"] and b["path"].endswith(".mp3")  # forced: the same asset, its file made again
    assert not pathlib.Path(a["path"]).exists()  # the m4a it replaced is gone
    # with no pipeline named, the new asset runs the one its content type gives it, as an upload does
    assert db.values("SELECT VALUE id FROM job WHERE recording = $r", r=b["id"])

    # an asset a convert step made isn't converted again, and audio has no soundtrack to take
    log = _run(client, h, db, cfg, pid, b["id"])
    assert "isn't converted again" in log

    r = client.delete(f"/api/v1/recordings/{b['id']}", headers=h)
    assert r.status_code in (200, 204), r.text
    assert not pathlib.Path(b["path"]).exists()  # the file the step made goes with it; the video stays
    assert pathlib.Path(db.one("SELECT path FROM $r", r=store.R("recording", rid))["path"]).exists()


@needs_ffmpeg
def test_a_video_without_sound_or_audio_alone_is_skipped(client, h, db, cfg, folder):
    wav, tr = folder / "talk.wav", folder / "talk.txt"
    write_wav(wav)
    tr.write_text("[00:00] Ann: Hello.")
    rid = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    pid = _pipeline(client, h, [{"type": "convert", "to": "audio"}, {"type": "convert", "to": "images"}])
    log = _run(client, h, db, cfg, pid, rid)
    assert "it's audio already" in log and "only a document's pages" in log and not _made(db, rid)


@POPPLER
def test_a_documents_pages_become_image_assets(client, h, db, cfg):
    settings.save(db, cfg, "encryption", {"files": True})
    pid = _pipeline(client, h, ["read", {"type": "convert", "to": "images", "pipeline": "none"}])
    pages = [["Harbour report"], ["Second page"], ["Third page"]]
    data = text_pdf(pages)
    r = client.post("/api/v1/uploads", headers=h, json={"namespace": "pods", "filename": "harbour.pdf", "size": len(data), "pipeline": pid})
    assert r.status_code == 201, r.text
    r = client.put(f"/api/v1/uploads/{r.json()['id']}?offset=0", headers={**h, "Content-Type": "application/octet-stream"}, content=data)
    rid = r.json()["recording"]
    drain(db, settings.effective(db, cfg))
    made = _made(db, rid)
    assert [m["title"] for m in made] == ["harbour, page 1", "harbour, page 2", "harbour, page 3"]
    assert [m["converted_from"]["page"] for m in made] == [0, 1, 2] and {m["source"] for m in made} == {"image"}
    first = pathlib.Path(made[0]["path"])
    assert keyring.is_encrypted(first)  # kept encrypted, like an upload
    assert keyring.read_plain(db, cfg, first)[:3] == b"\xff\xd8\xff"  # a JPEG

    # without its pages drawn there's nothing to make images of
    bare = _pipeline(client, h, [{"type": "convert", "to": "images"}, {"type": "convert", "to": "audio"}], "Bare")
    data = text_pdf([["Alone"]])
    r = client.post("/api/v1/uploads", headers=h, json={"namespace": "pods", "filename": "alone.pdf", "size": len(data), "pipeline": bare})
    r = client.put(f"/api/v1/uploads/{r.json()['id']}?offset=0", headers={**h, "Content-Type": "application/octet-stream"}, content=data)
    other = r.json()["recording"]
    drain(db, settings.effective(db, cfg))
    log = " ".join(db.one("SELECT log FROM job WHERE recording = $r", r=other)["log"])
    assert "put a Read step before this one" in log and "only a video has a soundtrack" in log and not _made(db, other)
