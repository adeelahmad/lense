"""Objects seen in videos, documents and images (docs/api.md#objects): the objects step and what it keeps, the player,
the Library's filter and its counts, search (hits, the filter and the facet), saved searches and views, moving and
deleting, the settings, and a step skipped without a detector."""

from __future__ import annotations

import shutil

import pytest

from app.domain import ingest, jobs, objects, store
from tests.helpers import drain, login, make_user, quiet, text_pdf
from tests.video_helpers import make_video, needs_ffmpeg

R = store.R
POPPLER = pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs poppler-utils")


class FakeObjects:
    """On a video's frames, a car on the yellow scene and a person on the dark blue ones; on a document's pages, a
    person on each and a dog on the second."""

    name = "fake"

    def detect(self, path):
        from PIL import Image

        if path.name.startswith("page-"):
            found = [{"label": "person", "score": 0.9, "box": [0.1, 0.1, 0.3, 0.6]}]
            if path.name == "page-0002.jpg":
                found.append({"label": "dog", "score": 0.7, "box": [0.5, 0.6, 0.3, 0.3]})
            return found
        r, g, b = Image.open(path).convert("RGB").getpixel((2, 2))
        return (
            [{"label": "car", "score": 0.8, "box": [0.2, 0.4, 0.5, 0.3]}]
            if r > 150 and g > 150
            else [{"label": "person", "score": 0.95, "box": [0.4, 0.2, 0.2, 0.6]}]
        )


@pytest.fixture
def detector(monkeypatch):
    monkeypatch.setattr(objects, "engine", lambda cfg: (FakeObjects(), None))


@pytest.fixture
def people(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "view@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "calls@x.io", "calls password 1", roles={"calls": "viewer"})
    return {
        "ha": login(client, "root@x.io", "root password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "view@x.io", "viewer password 1"),
        "hc": login(client, "calls@x.io", "calls password 1"),
    }


def _video(db, cfg, folder):
    cfg["video"]["sample_seconds"] = 1
    mp4, tr = folder / "review.mp4", folder / "review.txt"
    make_video(mp4)
    tr.write_text("[00:00] Mara: Here is the invoice.\n[00:03] Tom: The car park opens.\n[00:06] Mara: Thank you all.")
    rid = ingest.import_transcript(db, cfg, "pods", tr, audio=mp4, log=quiet)
    jobs.enqueue(db, rid, None, by="test")  # the standard pipeline, objects included
    drain(db, cfg)
    return rid


@needs_ffmpeg
def test_objects_in_a_video_are_kept_shown_and_found(client, people, db, cfg, folder, detector):
    rid = _video(db, cfg, folder)
    hv, he, hc = people["hv"], people["he"], people["hc"]
    log = " ".join(db.one("SELECT log FROM job WHERE recording = $r", r=rid)["log"])
    assert "2 kind(s) of object on screen (fake): person (6), car (3)" in log
    p = client.get(f"/api/v1/resources/{rid}/player", headers=hv).json()
    person, car = p["objects"]
    assert (person["label"], person["spans"], person["screen_ms"], person["count"]) == ("person", [[0, 3000], [6000, 9000]], 6000, 6)
    assert (car["label"], car["spans"], car["first_ms"], car["box"]) == ("car", [[3000, 6000]], 3000, [0.2, 0.4, 0.5, 0.3])
    assert car["boxes"][0] == [3000, 0.2, 0.4, 0.5, 0.3, 0.8] and "sig=" in car["frame"]
    assert client.get(car["frame"]).headers["content-type"] == "image/jpeg"
    assert db.one("SELECT objects FROM $r", r=R("recording", rid))["objects"] == ["car", "person"]

    # the Library: a filter, and what it can filter by
    other = ingest.import_text(db, cfg, "pods", "[00:00] Ann: Nothing to see here.", title="Radio")
    assert [r["id"] for r in client.get("/api/v1/resources", params={"object": "Car"}, headers=hv).json()] == [rid]
    assert other not in [r["id"] for r in client.get("/api/v1/resources", params={"object": ["car", "dog"]}, headers=hv).json()]
    assert client.get("/api/v1/resources/objects", headers=hv).json() == [
        {"object": "car", "recordings": 1},
        {"object": "person", "recordings": 1},
    ]
    assert client.get("/api/v1/resources/objects", headers=hc).json() == []  # nothing they can read
    view = {"name": "Cars", "state": {"objects": ["car"]}}
    assert client.post("/api/v1/views", json=view, headers=he).json()["state"]["objects"] == ["car"]

    # search: the kind of object is a hit where it's first seen, a filter and a facet
    res = client.get("/api/v1/search", params={"q": "car", "facets": "true"}, headers=hv).json()
    hit = next(h for h in res["hits"] if h["source"] == "object")
    assert (hit["recording_id"], hit["t0"], hit["t1"], hit["page"], hit["box"]) == (rid, 3000, 6000, None, [0.2, 0.4, 0.5, 0.3])
    assert "<mark>car</mark>" in hit["snippet"] and "sig=" in hit["frame"]
    assert {h["source"] for h in res["hits"]} == {"said", "object"}  # "The car park opens." was said too
    assert res["facets"]["objects"] == [{"name": "car", "count": 1}, {"name": "person", "count": 1}]
    assert client.get("/api/v1/search", params={"q": "nothing", "object": "car"}, headers=hv).json()["total"] == 0
    hits = client.get("/api/v1/search", params={"q": "thank", "object": "person"}, headers=hv).json()["hits"]
    assert hits and {h["recording_id"] for h in hits} == {rid}  # said, and on screen where there's OCR
    assert client.get("/api/v1/search", params={"q": "car"}, headers=hc).json()["total"] == 0  # not their namespace
    saved = client.post("/api/v1/searches", json={"name": "Cars", "q": "car", "object": "car"}, headers=he).json()
    assert (saved["q"], saved["object"]) == ("car", "car")

    # moved, they go along; deleted, they go
    assert client.post(f"/api/v1/resources/{rid}/move", json={"namespace": "calls"}, headers=people["ha"]).status_code == 200
    assert db.values("SELECT VALUE space FROM object_track WHERE recording = $r", r=rid) == [store.ns_id(db, "calls")] * 2
    assert client.delete(f"/api/v1/resources/{rid}", headers=people["ha"]).status_code == 200
    assert db.values("SELECT VALUE id FROM object_track WHERE recording = $r", r=rid) == []


@POPPLER
def test_objects_on_a_documents_pages_count_pages(client, people, db, cfg, folder, detector):
    data, he = text_pdf([["The harbour report"], ["The keeper and his dog."]]), people["he"]
    up = client.post("/api/v1/uploads", headers=he, json={"namespace": "pods", "filename": "harbour.pdf", "size": len(data)}).json()
    put = client.put(f"/api/v1/uploads/{up['id']}?offset=0", headers={**he, "Content-Type": "application/octet-stream"}, content=data)
    rid = put.json()["recording"]
    drain(db, cfg)
    p = client.get(f"/api/v1/resources/{rid}/player", headers=people["hv"]).json()
    person, dog = p["objects"]
    assert (person["label"], person["spans"], person["screen_ms"], person["paged"]) == ("person", [[0, 2]], 2, True)
    assert (dog["label"], dog["spans"], dog["first_ms"]) == ("dog", [[1, 2]], 1)
    hit = next(h for h in client.get("/api/v1/search", params={"q": "dog"}, headers=people["hv"]).json()["hits"] if h["source"] == "object")
    assert (hit["page"], hit["t0"], hit["t1"]) == (1, None, None)


@needs_ffmpeg
def test_without_a_detector_the_step_says_why(client, people, db, cfg, folder, monkeypatch):
    monkeypatch.setattr(objects, "MODELS", folder / "no-models")
    rid = _video(db, cfg, folder)
    job = db.one("SELECT status, log FROM job WHERE recording = $r", r=rid)
    assert job["status"] == "succeeded"
    assert any("objects skipped: no YOLOX model" in x for x in job["log"]), job["log"]
    assert client.get(f"/api/v1/resources/{rid}/player", headers=people["hv"]).json()["objects"] == []


def test_object_settings_are_checked(client, people):
    ha = people["ha"]
    r = client.put("/api/v1/settings/video", json={"object_min_score": 2}, headers=ha)
    assert (r.status_code, r.json()["detail"]) == (400, "video.object_min_score is a number from 0.05 to 0.95")
    r = client.put("/api/v1/settings/video", json={"object_engine": "magic"}, headers=ha)
    assert r.status_code == 400 and "ultralytics" in r.json()["detail"]
    assert client.put("/api/v1/settings/video", json={"object_engine": "off", "object_min_score": 0.5}, headers=ha).status_code == 200
    assert client.put("/api/v1/settings/video", json={"yolox_model": "/tmp/x.onnx"}, headers=ha).status_code == 400  # startup only
    assert client.put("/api/v1/settings/video", json={"object_min_score": 0.5}, headers=people["he"]).status_code == 403
    assert "yolox_model" in client.get("/api/v1/settings", headers=ha).json()["bootstrap"]
