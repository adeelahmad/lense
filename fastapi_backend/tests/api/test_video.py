"""Shots, text on screen, people on screen, search, chat and IIIF for video."""

from __future__ import annotations

import shutil

import pytest

from app.domain import faces, iiif, ingest, jobs, metadata, search, video
from tests.helpers import drain, login, make_user, quiet
from tests.video_helpers import FakeFaces, has_tesseract, make_video, needs_ffmpeg, sse

pytestmark = needs_ffmpeg


class Env:
    def __init__(self, request, cfg, db, folder, monkeypatch):
        cfg["video"]["sample_seconds"] = 1
        monkeypatch.setattr(video, "face_engine", lambda cfg: FakeFaces())
        self.cfg, self.db, self.folder = cfg, db, folder
        self.mp4, tr = folder / "review.mp4", folder / "review.txt"
        make_video(self.mp4)
        tr.write_text("[00:00] Mara: Here is the invoice for 2026.\n[00:03] Tom: And this is Project Atlas.\n[00:06] Mara: Thank you all.")
        self.rid = ingest.import_transcript(db, cfg, "pods", tr, audio=self.mp4, log=quiet)
        make_user(db, "root@x.io", "root password 1", admin=True)
        make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
        self.c = request.getfixturevalue("client")  # the app reads cfg as changed above
        self.h, self.he = login(self.c, "root@x.io", "root password 1"), login(self.c, "ed@x.io", "editor password 1")
        jobs.enqueue(db, self.rid, None, by="test")  # the standard pipeline
        drain(db, cfg)


@pytest.fixture
def env(request, cfg, db, folder, monkeypatch):
    return Env(request, cfg, db, folder, monkeypatch)


def test_shots_text_search_chat_and_iiif(env):
    c, h, rid, db = env.c, env.h, env.rid, env.db
    job = db.one("SELECT status, log FROM job WHERE recording = $r", r=rid)
    assert job["status"] == "succeeded", job["log"][-5:]
    assert "kept it" in " ".join(job["log"])  # imported transcript not replaced
    p = c.get(f"/api/v1/recordings/{rid}/player", headers=h).json()
    assert (p["media"]["kind"], p["media"]["width"], len(p["shots"])) == ("video", 640, 3)
    assert [s["t0"] for s in p["shots"]] == [0, 3000, 6000]
    frame = p["shots"][1]["frame"]
    assert "sig=" in frame  # frames in API responses are signed links: an <img> tag can't send a bearer token
    assert c.get(frame).headers["content-type"] == "image/jpeg"
    assert c.get(frame.split("?")[0]).status_code == 401
    assert c.get(frame.split("?")[0], headers=h).status_code == 200
    assert c.get(f"/api/v1/recordings/{rid}/frames/..%2Fx.jpg", headers=h).status_code == 404
    r = c.get(f"/api/v1/recordings/{rid}/media", headers={**h, "Range": "bytes=0-99"})
    assert (r.status_code, r.headers["content-type"], len(r.content)) == (206, "video/mp4", 100)
    assert c.get(f"/api/v1/recordings/{rid}/media").status_code == 401
    assert p["faces"] == []  # face detection is off by default
    if has_tesseract:
        assert [t["text"] for t in p["screen_text"]] == ["INVOICE 2026", "PROJECT ATLAS", "THANK YOU"]
        assert (p["screen_text"][1]["t0"], p["screen_text"][1]["t1"]) == (3000, 6000)
        hits = c.get("/api/v1/search", params={"q": "atlas"}, headers=h).json()["hits"]
        assert sorted(x["source"] for x in hits) == ["said", "screen"]
        span = p["screen_text"][2]["id"]
        assert c.patch(f"/api/v1/recordings/{rid}/ocr/{span}", headers=env.he, json={"text": "  "}).status_code == 400
        assert c.patch(f"/api/v1/recordings/{rid}/ocr/{span}", headers=env.he, json={"text": "THANK YOU ALL"}).status_code == 200
        fixed = [x for x in search.search(db, "thank all")["hits"] if x["source"] == "screen"]
        assert "ALL" in fixed[0]["snippet"]
        cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
        ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "what did the invoice slide say"}).text)
        assert "On screen: INVOICE 2026" in [x["text"] for x in ev["passages"][0]]
    metadata.save(db, env.cfg, rid, {"access": "public"})
    man = c.get(f"/iiif/{rid}/manifest").json()
    assert iiif.validate(man) == []
    body = man["items"][0]["items"][0]["items"][0]["body"]
    assert (body["type"], body["width"], man["items"][0]["height"]) == ("Video", 640, 360)
    assert man["thumbnail"][0]["id"].endswith(".jpg")
    assert c.get(man["thumbnail"][0]["id"].replace("http://127.0.0.1", "")).status_code == 200
    assert [r["label"]["en"][0] for r in man["structures"]][-1] == "Shots"
    layers = [a["id"].rsplit("/", 1)[1] for a in man["items"][0]["annotations"][1:]]
    assert "screen" in layers
    assert "faces" not in layers  # never published by default
    r = c.get(f"/iiif/{rid}/media", headers={"Range": "bytes=0-99"})
    assert (r.status_code, r.headers["content-type"]) == (206, "video/mp4")
    if has_tesseract:
        screen = c.get(f"/iiif/{rid}/annotations/screen").json()
        assert iiif.validate(screen) == []
        assert "#xywh=" in screen["items"][0]["target"]


def test_people_on_screen(env):
    c, h, he, rid, db = env.c, env.h, env.he, env.rid, env.db
    assert c.put("/api/v1/namespaces/pods/faces/mode", headers=h, json={"mode": "recognize"}).status_code == 400  # needs a purpose
    assert c.put("/api/v1/namespaces/pods/faces/mode", headers=he, json={"mode": "recognize", "purpose": "x"}).status_code == 403
    purpose = "Name the hosts in our own videos"
    r = c.put("/api/v1/namespaces/pods/faces/mode", headers=h, json={"mode": "recognize", "purpose": purpose, "reprocess": True})
    assert len(r.json()["jobs"]) == 1
    drain(db, env.cfg)
    tracks = c.get(f"/api/v1/recordings/{rid}/player", headers=h).json()["faces"]
    assert [t["spans"] for t in tracks] == [[[0, 3000], [6000, 9000]], [[3000, 6000]]]  # the same person in scenes 1 and 3
    reg = c.get("/api/v1/namespaces/pods/faces", headers=he).json()
    assert (reg["mode"], reg["purpose"]) == ("recognize", purpose)
    assert [[g["name"] for g in f["suggestions"]] for f in reg["faces"]] == [["Mara"], ["Tom"]]  # who's talking while on screen
    cover = reg["faces"][0]["cover_url"]
    assert "sig=" in cover and c.get(cover).headers["content-type"] == "image/jpeg"
    f1, f2 = [f["id"] for f in reg["faces"]]
    mara = [g["id"] for g in reg["faces"][0]["suggestions"]][0]
    assert c.post(f"/api/v1/faces/{f1}/speaker", headers=he, json={"speaker": mara}).status_code == 200
    assert c.post(f"/api/v1/faces/{f1}", headers=he, json={"name": "Mara Quint"}).status_code == 200
    assert c.get(f"/api/v1/recordings/{rid}/player", headers=h).json()["faces"][0]["name"] == "Mara Quint"
    mid = c.post(f"/api/v1/faces/{f2}/merge", headers=he, json={"into": f1}).json()["merge"]
    assert len(c.get("/api/v1/namespaces/pods/faces", headers=he).json()["faces"]) == 1
    assert c.post(f"/api/v1/faces/merges/{mid}/undo", headers=he).status_code == 200
    assert len(c.get("/api/v1/namespaces/pods/faces", headers=he).json()["faces"]) == 2

    second, tr2 = env.folder / "again.mp4", env.folder / "again.txt"  # a second video with the same people
    shutil.copy(env.mp4, second)
    tr2.write_text("[00:00] Ann: Hello.\n[00:03] Ben: Hi.\n[00:06] Ann: Bye.")
    rid2 = ingest.import_transcript(db, env.cfg, "pods", tr2, audio=second, log=quiet)
    jobs.enqueue(db, rid2, ["shots", "faces"], by="test")
    drain(db, env.cfg)
    assert {t["face"] for t in faces.tracks_for(db, rid2)} == {f1, f2}  # recognised, not new
    assert c.delete(f"/api/v1/faces/{f2}", headers=he).status_code == 403  # deleting face data is for owners
    assert c.delete(f"/api/v1/faces/{f2}", headers=h).status_code == 200
    assert len(c.get("/api/v1/namespaces/pods/faces", headers=h).json()["faces"]) == 1
    track = faces.tracks_for(db, rid2)[0]["id"]
    assert c.delete(f"/api/v1/recordings/{rid2}/faces/nope", headers=he).status_code == 404
    assert c.delete(f"/api/v1/recordings/{rid2}/faces/{track}", headers=he).status_code == 200  # not a face
    assert track not in [t["id"] for t in faces.tracks_for(db, rid2)]
    c.put("/api/v1/namespaces/pods/faces/mode", headers=h, json={"mode": "detect"})
    assert [e for e in db.values("SELECT VALUE embedding FROM face") if e] == []  # descriptors removed outside recognition
    c.put("/api/v1/namespaces/pods/faces/mode", headers=h, json={"mode": "off"})
    assert (db.values("SELECT VALUE id FROM face"), db.values("SELECT VALUE id FROM face_track")) == ([], [])
    assert c.get("/api/v1/namespaces/pods/faces", headers=h).json()["faces"] == []
    assert not list(video.frames_dir(env.cfg, rid).glob("face-*.jpg"))  # turning faces off deletes the crops too
    actions = [a["action"] for a in c.get("/api/v1/audit", headers=h).json()]
    assert {"faces.mode", "face.merge", "face.merge.undo", "face.delete", "face.not_a_face"} <= set(actions)
