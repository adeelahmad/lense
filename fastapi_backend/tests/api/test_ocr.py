"""OCR engines (docs/configuration.md#video-ocr-faces-and-objects): docTR reads an image's text and a video's frames;
without it the step says why; choosing it is an admin's."""

from __future__ import annotations

import io
import subprocess
import sys

import pytest

from app.domain import ingest, jobs, store, video
from tests import fake_doctr
from tests.helpers import drain, login, make_user, quiet, scan
from tests.video_helpers import make_video, needs_ffmpeg

R = store.R


@pytest.fixture
def doctr(monkeypatch):
    calls = fake_doctr.install(monkeypatch)
    yield calls
    video._doctr_predictor.cache_clear()


@pytest.fixture
def people(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {"ha": login(client, "root@x.io", "root password 1"), "he": login(client, "ed@x.io", "editor password 1")}


def _image(client, h):
    out = io.BytesIO()
    scan(["The harbour report"]).save(out, format="PNG")
    data = out.getvalue()
    up = client.post("/api/v1/uploads", headers=h, json={"namespace": "pods", "filename": "report.png", "size": len(data)}).json()
    put = client.put(f"/api/v1/uploads/{up['id']}?offset=0", headers={**h, "Content-Type": "application/octet-stream"}, content=data)
    return put.json()


def _video(db, cfg, folder, name):
    made, mp4, tr = folder / "made.mp4", folder / f"{name}.mp4", folder / f"{name}.txt"
    make_video(made)
    # a file of its own: the same video twice would be one recording
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", made, "-c", "copy", "-metadata", f"title={name}", mp4], check=True)
    tr.write_text(f"[00:00] Mara: The {name} review.\n[00:06] Tom: Thank you all.")
    rid = ingest.import_transcript(db, cfg, "pods", tr, audio=mp4, log=quiet)
    jobs.enqueue(db, rid, None, by="test")  # the standard pipeline, OCR included
    drain(db, cfg)
    return rid


def test_doctr_reads_an_image(client, people, db, cfg, doctr):
    cfg["video"]["ocr_engine"] = "doctr"
    up = _image(client, people["he"])
    drain(db, cfg)
    rid = up["recording"]
    assert db.one("SELECT engine FROM $r", r=R("recording", rid))["engine"] == "image+ocr:doctr"
    texts = [x["text"] for x in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx", r=rid)]
    assert texts == ["The harbour report Ships arrived at", "Galway 1921"]  # its blocks, as docTR laid them out
    log = client.get(f"/api/v1/jobs/{up['job']}/log", headers=people["he"]).json()["lines"]
    assert any("1 page(s), 1 read by OCR (doctr), 2 block(s) of text" in x for x in log), log


@needs_ffmpeg
def test_a_video_read_by_doctr_and_without_it(client, people, db, cfg, folder, doctr, monkeypatch):
    cfg["video"].update(ocr_engine="doctr", sample_seconds=3)
    rid = _video(db, cfg, folder, "atlas")
    p = client.get(f"/api/v1/resources/{rid}/player", headers=people["he"]).json()
    assert {x["text"] for x in p["screen_text"]} == {"The harbour report", "Ships arrived at", "Galway 1921"}
    assert all(x["t0"] == 0 for x in p["screen_text"])  # on every frame, from the first

    monkeypatch.setitem(sys.modules, "doctr", None)  # docTR isn't installed
    video._doctr_predictor.cache_clear()
    rid = _video(db, cfg, folder, "harbour")
    job = db.one("SELECT status, log FROM job WHERE recording = $r", r=rid)
    assert job["status"] == "succeeded"
    assert any('ocr skipped: docTR isn\'t installed (pip install "lens[doctr]"; it brings PyTorch)' in x for x in job["log"]), job["log"]
    assert client.get(f"/api/v1/resources/{rid}/player", headers=people["he"]).json()["screen_text"] == []


def test_choosing_doctr(client, people):
    ha = people["ha"]
    assert client.put("/api/v1/settings/video", json={"ocr_engine": "doctr"}, headers=ha).status_code == 200
    assert client.get("/api/v1/settings", headers=ha).json()["video"]["values"]["ocr_engine"] == "doctr"
    r = client.put("/api/v1/settings/video", json={"ocr_engine": "magic"}, headers=ha)
    assert r.status_code == 400 and "doctr" in r.json()["detail"]
    assert client.put("/api/v1/settings/video", json={"ocr_engine": "doctr"}, headers=people["he"]).status_code == 403
