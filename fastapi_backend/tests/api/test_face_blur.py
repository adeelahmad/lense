"""Faces blurred for sharing (face_blur.py, docs/video.md): only when someone asks, ffmpeg makes a copy of a video with
the faces found blurred over their tracks' boxes, kept as one of its files; owners can have visitors play it."""

from __future__ import annotations

import datetime as dt
import subprocess

import pytest

from app.domain import face_blur, faces, files, ingest, jobs, metadata, quiet, store
from tests.api.test_mcp import tool, tool_error
from tests.helpers import drain
from tests.helpers import quiet as hush
from tests.video_helpers import needs_ffmpeg

R = store.R


def test_regions_cover_each_track_between_and_around_its_samples():
    """Between two samples that saw a face, the blur covers both boxes; a sample before the first and after the last
    too; samples more than two steps apart are separate stretches; and a margin goes round each box."""
    moving = {"boxes": [[2000, 0.1, 0.1, 0.2, 0.2], [3000, 0.3, 0.1, 0.2, 0.2], [4000, 0.5, 0.1, 0.2, 0.2], [9000, 0.7, 0.7, 0.1, 0.1]]}
    assert face_blur.regions([moving], 1000, margin=0) == [
        (1.0, 3.0, 0.1, 0.1, 0.4, 0.2),  # the first pair's boxes together, from a step before the first
        (3.0, 5.0, 0.3, 0.1, 0.4, 0.2),  # to a step after the last
        (8.0, 10.0, 0.7, 0.7, 0.1, 0.1),  # seen once, five steps later: a step either side
    ]
    edge = {"boxes": [[0, 0.0, 0.9, 0.1, 0.1]]}
    assert face_blur.regions([edge], 1000) == [(0.0, 1.0, 0.0, 0.88, 0.12, 0.12)]  # the margin stays on the picture
    tiny = {"boxes": [[0, 0.5, 0.5, 0.001, 0.001]]}
    assert face_blur.regions([tiny], 1000, margin=0)[0][4:] == (face_blur.SMALLEST, face_blur.SMALLEST)
    assert face_blur.regions([{"boxes": []}, {}], 1000) == []

    many = {"boxes": [[t * 1000, 0.1, 0.1, 0.1, 0.1] for t in range(40)]}  # 39 pieces
    merged = face_blur.regions([many], 1000, most=10)
    assert len(merged) == 10  # 39, 20, then 10: halved until there are no more than `most`
    assert (merged[0][0], merged[-1][1]) == (0.0, 40.0)  # still all of the time it was seen, and a step after


def test_the_filter_graph_blurs_each_region_for_its_time():
    g = face_blur.graph([(1.0, 3.0, 0.1, 0.2, 0.3, 0.4), (8.0, 10.5, 0.5, 0.5, 0.2, 0.2)])
    chains = g.split(";")
    assert chains[0] == "[0:v]format=yuv420p,split=3[m0][c1][c2]"
    assert chains[1].startswith("[c1]crop=w=iw*0.3000:h=ih*0.4000:x=iw*0.1000:y=ih*0.2000,boxblur=")
    assert chains[1].endswith(":enable='between(t,1.000,3.000)'[b1]")
    assert chains[2] == "[m0][b1]overlay=x=main_w*0.1000:y=main_h*0.2000:enable='between(t,1.000,3.000)'[m1]"
    assert chains[4] == "[m1][b2]overlay=x=main_w*0.5000:y=main_h*0.5000:enable='between(t,8.000,10.500)'[v]"  # the output


@pytest.fixture
def env(request, cfg, db, folder, monkeypatch):
    from tests.api.test_video import Env

    e = Env(request, cfg, db, folder, monkeypatch)
    e.c.put("/api/v1/namespaces/pods/faces/mode", headers=e.h, json={"mode": "recognize", "purpose": "Our hosts", "reprocess": True})
    drain(db, cfg)
    return e


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name:format_tags", "-of", "default=nw=1", str(path)],
        capture_output=True,
        text=True,
    )
    return out.stdout


@needs_ffmpeg
def test_blurring_faces_for_sharing(env, new_client, monkeypatch):
    c, h, he, rid, db, cfg = env.c, env.h, env.he, env.rid, env.db, env.cfg
    anon = new_client()
    tracks = faces.tracks_for(db, rid)
    assert len(tracks) == 2  # FakeFaces: one person in scenes 1 and 3, another in scene 2
    assert c.get(f"/api/v1/recordings/{rid}/blur-faces", headers=he).json() == {"blurred": None, "job": None}  # nothing asked
    original = c.get(f"/api/v1/recordings/{rid}/audio", headers={**h, "Range": "bytes=0-4095"}).content

    # asking: the right to process it, a video, faces of those asked for
    assert c.post(f"/api/v1/recordings/{rid}/blur-faces", json={"who": "chosen", "tracks": ["nope"]}, headers=he).status_code == 400
    assert c.post(f"/api/v1/recordings/{rid}/blur-faces", json={"who": "some"}, headers=he).status_code == 422
    (env.folder / "note.txt").write_text("[00:00] Mara: Just words.")
    words = ingest.import_transcript(db, cfg, "pods", env.folder / "note.txt", log=hush)
    r = c.post(f"/api/v1/recordings/{words}/blur-faces", json={}, headers=he)
    assert (r.status_code, r.json()["detail"]) == (400, "Only a video's faces are blurred so far.")

    # heavy: it waits for the quiet processing hours like the other heavy steps
    assert face_blur.STEP in quiet.HEAVY and face_blur.STEP in jobs.STEPS
    jid = c.post(f"/api/v1/recordings/{rid}/blur-faces", json={}, headers=he).json()["job"]
    assert c.post(f"/api/v1/recordings/{rid}/blur-faces", json={}, headers=he).json()["job"] == jid  # the same job
    hour = dt.datetime.now(dt.UTC).hour
    away = {**cfg, "workers": {**cfg["workers"], "heavy_hours": f"{(hour + 2) % 24:02d}:00-{(hour + 3) % 24:02d}:00"}}
    assert "heavy steps run" in face_blur.status(db, away, rid)["job"]["waits"]

    # ffmpeg failing fails the job, and says why
    def broken(*a, **k):
        raise OSError("no ffmpeg here")

    monkeypatch.setattr(face_blur.subprocess, "Popen", broken)
    drain(db, cfg)
    got = c.get(f"/api/v1/recordings/{rid}/blur-faces", headers=he).json()
    assert got["blurred"] is None and got["job"]["status"] == "failed" and "no ffmpeg here" in got["job"]["error"]
    monkeypatch.undo()

    # every face, then only the unnamed ones (one is named), the copy replaced each time
    c.post(f"/api/v1/recordings/{rid}/blur-faces", json={}, headers=he)
    drain(db, cfg)
    first = c.get(f"/api/v1/recordings/{rid}/blur-faces", headers=he).json()["blurred"]
    assert (first["faces"], first["who"], first["visitors"]) == (2, "all", False)
    f = files.get(db, rid, first["file"])
    assert (f["role"], f["label"], f["name"]) == ("attachment", "Faces blurred", "review-faces-blurred.mp4")
    assert "every face found" in f["description"]
    saved = c.get(first["download"])  # a signed link
    assert saved.status_code == 200 and saved.content[4:8] == b"ftyp"
    probed = _probe(files.path_of(cfg, f))
    assert "codec_name=h264" in probed and "codec_name=aac" in probed

    named = next(t for t in tracks if t.get("face"))
    faces.rename(db, named["face"], "Mara Quint")
    c.post(f"/api/v1/recordings/{rid}/blur-faces", json={"who": "unnamed"}, headers=he)
    drain(db, cfg)
    second = c.get(f"/api/v1/recordings/{rid}/blur-faces", headers=he).json()
    assert (second["blurred"]["faces"], second["blurred"]["who"], second["job"]["status"]) == (1, "unnamed", "succeeded")
    assert named["id"] not in second["blurred"]["tracks"]
    assert [x["id"] for x in files.of(db, rid)] == [second["blurred"]["file"]]  # the first copy replaced

    # visitors: owners choose; members still play the original, visitors the copy
    metadata.save(db, cfg, rid, {"access": "public"})
    url = anon.get(f"/api/v1/public/recordings/{rid}").json()["media"]["url"]
    assert anon.get(url, headers={"Range": "bytes=0-4095"}).content == original  # nothing changes until someone picks
    on = {"on": True}
    assert c.put(f"/api/v1/recordings/{rid}/blur-faces/visitors", json=on, headers=he).status_code == 403
    assert c.put(f"/api/v1/recordings/{rid}/blur-faces/visitors", json=on, headers=h).json()["blurred"]["visitors"] is True
    copy = files.path_of(cfg, files.get(db, rid, second["blurred"]["file"]))
    seen = anon.get(url, headers={"Range": "bytes=0-4095"})
    assert (seen.status_code, seen.headers["content-type"], seen.content) == (206, "video/mp4", copy.read_bytes()[:4096])
    assert anon.get(f"/iiif/{rid}/media", headers={"Range": "bytes=0-4095"}).content == seen.content
    assert c.get(f"/api/v1/recordings/{rid}/audio", headers={**he, "Range": "bytes=0-4095"}).content == original
    assert anon.get(f"/api/v1/recordings/{rid}/blur-faces").status_code in (401, 404)  # members only

    # the copy gone missing: visitors get nothing rather than the faces
    hidden = copy.with_name("elsewhere")
    copy.rename(hidden)
    assert anon.get(url).status_code == 404
    hidden.rename(copy)
    # deleting the copy in the Files tab takes the choice with it
    assert c.delete(f"/api/v1/recordings/{rid}/files/{second['blurred']['file']}", headers=he).status_code == 200
    assert anon.get(url, headers={"Range": "bytes=0-4095"}).content == original
    assert c.get(f"/api/v1/recordings/{rid}/blur-faces", headers=he).json()["blurred"] is None
    r = c.put(f"/api/v1/recordings/{rid}/blur-faces/visitors", json=on, headers=h)
    assert (r.status_code, r.json()["detail"]) == (400, "Make a copy with the faces blurred first.")

    # agents: the same through MCP
    out = tool(c, he, "get_face_blur", recording_id=rid)
    assert [p["track"] for p in out["faces"]] == [t["id"] for t in tracks] and "blurred" not in out
    other = next(t["id"] for t in tracks if t["id"] != named["id"])
    job = tool(c, he, "blur_faces", recording_id=rid, who="chosen", tracks=[other])["job"]
    drain(db, cfg)
    out = tool(c, he, "get_face_blur", recording_id=rid)
    assert (out["job"]["id"], out["blurred"]["tracks"]) == (job, [other])
    assert out["blurred"]["download"].endswith(f"/files/{out['blurred']['file']}/download")
    assert tool_error(c, he, "set_face_blur_for_visitors", recording_id=rid, on=True)  # owners
    assert tool(c, h, "set_face_blur_for_visitors", recording_id=rid, on=True)["blurred"]["visitors"] is True
    assert tool_error(c, he, "blur_faces", recording_id=rid, who="everyone") == "who is one of all, unnamed, chosen"

    audited = [a["action"] for a in c.get("/api/v1/audit", headers=h).json()]
    assert {"recording.blur_faces", "recording.blur_faces.visitors"} <= set(audited)
