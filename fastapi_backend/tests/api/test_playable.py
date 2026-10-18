"""Playable copies (playable.py): media browsers can't play gets a copy they can, made once, with the setting on."""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from app.domain import deletion, ingest, jobs, playable
from tests.helpers import login, make_user, quiet
from tests.video_helpers import make_video, needs_ffmpeg

pytestmark = needs_ffmpeg


def make_avi(path):
    """Two seconds of MPEG-4 Part 2 video and PCM sound in an AVI, the kind of file an old camera writes."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2:r=10"]
    cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "mpeg4", "-c:a", "pcm_s16le", "-shortest", str(path)]
    subprocess.run(cmd, capture_output=True, check=True)


def _import(db, cfg, folder, media):
    tr = folder / f"{media.stem}.txt"
    tr.write_text("[00:00] Mara: Here is the camera's clip.")
    return ingest.import_transcript(db, cfg, "pods", tr, audio=media, log=quiet)


def test_a_camera_clip_gets_a_copy_every_browser_plays(request, cfg, db, folder):
    avi = folder / "camera.avi"
    make_avi(avi)
    rid = _import(db, cfg, folder, avi)
    said: list[str] = []
    assert playable.codecs(avi) == ("mpeg4", "pcm_s16le")
    assert playable.needed(avi)
    with pytest.raises(jobs.Skip):  # the transcript came with it
        jobs._transcribe(db, cfg, rid, said.append)
    assert playable.copy_of(db, cfg, rid) is None  # off by default: nothing made
    assert not playable.path_of(cfg, rid).exists()

    cfg["video"]["playable_copies"], cfg["video"]["playable_height"] = True, 180
    with pytest.raises(jobs.Skip):
        jobs._transcribe(db, cfg, rid, said.append)
    path, ctype = playable.copy_of(db, cfg, rid)
    assert ctype == "video/mp4" and any("every browser plays" in s for s in said)
    assert playable.codecs(path) == ("h264", "aac")
    height = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert height == "180"  # no taller than the setting
    assert playable.ensure(db, cfg, rid, said.append) == path  # made once, not again

    make_user(db, "root@x.io", "root password 1", admin=True)
    c = request.getfixturevalue("client")
    h = login(c, "root@x.io", "root password 1")
    r = c.get(f"/api/v1/recordings/{rid}/audio", headers={**h, "Range": "bytes=0-99"})
    assert (r.status_code, r.headers["content-type"], r.content[4:8]) == (206, "video/mp4", b"ftyp")  # the player's copy
    r = c.get(f"/api/v1/recordings/{rid}/media", headers={**h, "Range": "bytes=0-3"})
    assert (r.status_code, r.content) == (206, b"RIFF")  # the file itself, to save

    deletion.delete(db, cfg, rid)
    assert not pathlib.Path(path).exists()


def test_a_file_browsers_play_gets_no_copy(cfg, db, folder):
    mp4 = folder / "phone.mp4"
    make_video(mp4)
    rid = _import(db, cfg, folder, mp4)
    cfg["video"]["playable_copies"] = True
    assert not playable.needed(mp4)
    assert playable.ensure(db, cfg, rid, quiet) is None
    assert playable.copy_of(db, cfg, rid) is None
