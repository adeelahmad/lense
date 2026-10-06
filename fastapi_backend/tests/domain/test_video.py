"""Video processing at the domain level: probing, shots, frames, text on screen and faces (with consent modes)."""

from __future__ import annotations

import pytest

from app.domain import faces, ingest, jobs, store, video
from tests.helpers import drain, quiet, write_wav
from tests.video_helpers import FakeFaces, has_tesseract, make_video, needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.fixture
def rid(db, cfg, folder, monkeypatch):
    cfg["video"]["sample_seconds"] = 1
    monkeypatch.setattr(video, "face_engine", lambda cfg: FakeFaces())
    mp4, tr = folder / "review.mp4", folder / "review.txt"
    make_video(mp4)
    tr.write_text("[00:00] Mara: Here is the invoice for 2026.\n[00:03] Tom: And this is Project Atlas.\n[00:06] Mara: Thank you all.")
    return ingest.import_transcript(db, cfg, "pods", tr, audio=mp4, log=quiet)


def test_probe_and_shots(db, cfg, folder, rid):
    info = video.probe_media(folder / "review.mp4")
    assert (info["kind"], info["width"], info["height"], info["has_audio"]) == ("video", 640, 360, True)
    assert abs(info["duration_ms"] - 9000) < 200
    assert video.shots_of(9.0, [3.0, 3.2, 6.0, 8.8], 1.0) == [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)]  # too-short shots are joined
    jobs.enqueue(db, rid, None, by="test")
    drain(db, cfg)
    shots = db.rows("SELECT idx, t0, t1, frame FROM shot WHERE recording = $r ORDER BY idx", r=rid)
    assert [s["t0"] for s in shots] == [0, 3000, 6000]
    assert all((video.frames_dir(cfg, rid) / s["frame"]).is_file() for s in shots)
    rec = db.one("SELECT media FROM $r", r=store.R("recording", rid))
    assert rec["media"]["kind"] == "video"
    texts = [x["text"] for x in db.rows("SELECT text, t0 FROM ocr_span WHERE recording = $r ORDER BY t0", r=rid)]
    if has_tesseract:
        assert texts == ["INVOICE 2026", "PROJECT ATLAS", "THANK YOU"]
    assert faces.tracks_for(db, rid) == []  # faces are off until an owner turns them on


def test_face_modes(db, cfg, rid):
    sid = store.ns_id(db, "pods")
    with pytest.raises(ValueError):
        faces.set_mode(db, sid, "recognize")  # needs a purpose
    with pytest.raises(ValueError):
        faces.set_mode(db, sid, "everything")
    faces.set_mode(db, sid, "detect", user="root@x.io")
    jobs.enqueue(db, rid, None, by="test")
    drain(db, cfg)
    tracks = faces.tracks_for(db, rid)
    assert len(tracks) == 1 and tracks[0]["cover"]  # without descriptors, one box in the same place is one track
    assert tracks[0]["face"] is None  # detected, not recognised
    assert [e for e in db.values("SELECT VALUE embedding FROM face_track") if e] == []  # no descriptors while only detecting
    faces.set_mode(db, sid, "recognize", "Name the hosts", "root@x.io")
    jobs.enqueue(db, rid, ["faces"], by="test")
    drain(db, cfg)
    assert [t["spans"] for t in faces.tracks_for(db, rid)] == [[[0, 3000], [6000, 9000]], [[3000, 6000]]]
    assert len(faces.list_faces(db, sid)) == 2
    faces.set_mode(db, sid, "off", cfg=cfg)
    assert (db.values("SELECT VALUE id FROM face"), db.values("SELECT VALUE id FROM face_track")) == ([], [])
    assert not list(video.frames_dir(cfg, rid).glob("face-*.jpg"))  # crops go too


@needs_ffmpeg
def test_ffmpeg_refuses_playlists_that_point_at_other_files(folder):
    """ffmpeg picks a demuxer by content: a "clip.mp3" that is really an ffconcat list must not read the file it names."""
    folder.mkdir(parents=True, exist_ok=True)
    write_wav(folder / "secret.wav")
    trick = folder / "clip.mp3"
    trick.write_text("ffconcat version 1.0\nfile secret.wav\n")
    with pytest.raises(RuntimeError):
        ingest.decode(trick)
    assert ingest.probe(trick)[0] is None
    assert len(ingest.decode(folder / "secret.wav")) > 0  # real media still decodes
