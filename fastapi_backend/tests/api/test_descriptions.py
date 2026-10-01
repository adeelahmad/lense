"""Descriptions of what's seen (docs/api.md#descriptions): a model that can see images describes a video's shots and a
document's pages from their pictures; they're shown, found, moved and deleted with their resource; without such a
model (or with one that can't see) the step says so; the settings are checked."""

from __future__ import annotations

import io
import shutil

import pytest

from app.domain import descriptions, ingest, jobs, store
from tests import fake_llm
from tests.helpers import drain, login, make_user, quiet, scan, text_pdf
from tests.video_helpers import make_video, needs_ffmpeg

R = store.R
POPPLER = pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs poppler-utils")


@pytest.fixture
def llm(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake", vision_model="fake-eyes")
    fake_llm.Handler.seen = []
    yield fake_llm.Handler
    srv.shutdown()


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


def _upload(client, h, data, name):
    up = client.post("/api/v1/uploads", headers=h, json={"namespace": "pods", "filename": name, "size": len(data)}).json()
    return client.put(
        f"/api/v1/uploads/{up['id']}?offset=0", headers={**h, "Content-Type": "application/octet-stream"}, content=data
    ).json()


def _asked(llm):
    """What the model was shown: the requests with a picture in them."""
    return [b for b in llm.seen if fake_llm._picture(b)]


def _log(db, rid):
    return " ".join(db.one("SELECT log, created_at FROM job WHERE recording = $r ORDER BY created_at DESC LIMIT 1", r=rid)["log"])


@needs_ffmpeg
def test_a_videos_shots_are_described_shown_and_found(client, people, db, cfg, folder, llm):
    mp4, tr = folder / "review.mp4", folder / "review.txt"
    make_video(mp4)  # three scenes: dark blue, yellow, dark blue
    tr.write_text("[00:00] Mara: Here is the invoice.\n[00:06] Mara: Thank you all.")
    rid = ingest.import_transcript(db, cfg, "pods", tr, audio=mp4, log=quiet)
    jobs.enqueue(db, rid, None, by="test")  # the standard pipeline, describe included
    drain(db, cfg)
    assert "3 shot(s) described (fake-eyes)" in _log(db, rid)
    d = client.get(f"/api/v1/resources/{rid}/player", headers=people["hv"]).json()["descriptions"]
    assert [(x["idx"], x["text"]) for x in d] == [
        (0, "A dark blue screen with white lettering in the middle."),
        (1, "A yellow wall with large black letters on it."),
        (2, "A dark blue screen with white lettering in the middle."),
    ]
    assert d[0]["t0"] == 0 and abs(d[1]["t0"] - 3000) < 500 and d[1]["t1"] == d[2]["t0"]
    assert all(x["model"] == "fake-eyes" and "sig=" in x["frame"] for x in d)
    # what the model was asked: the model that can see, the prompt, and each shot's keyframe
    asked = _asked(llm)
    assert len(asked) == 3 and {b["model"] for b in asked} == {"fake-eyes"}
    assert asked[1]["messages"][0] == {"role": "system", "content": descriptions.PROMPT}
    about = asked[1]["messages"][1]["content"][0]["text"]
    assert about.startswith("The first frame of a shot in the video “review”, at 0:0")

    # search: where the shot starts, for those who may read it
    res = client.get("/api/v1/search", params={"q": "yellow wall", "facets": "true"}, headers=people["hv"]).json()
    hit = next(h for h in res["hits"] if h["source"] == "described")
    assert (hit["recording_id"], hit["t0"], hit["page"]) == (rid, d[1]["t0"], None)
    assert "<mark>yellow</mark>" in hit["snippet"] and "sig=" in hit["frame"]
    assert res["facets"]["recordings"][0]["id"] == rid
    assert client.get("/api/v1/search", params={"q": "yellow wall"}, headers=people["hc"]).json()["total"] == 0

    # moved, they go along; deleted, they go
    assert client.post(f"/api/v1/resources/{rid}/move", json={"namespace": "calls"}, headers=people["ha"]).status_code == 200
    assert db.values("SELECT VALUE space FROM description WHERE recording = $r", r=rid) == [store.ns_id(db, "calls")] * 3
    assert client.delete(f"/api/v1/resources/{rid}", headers=people["ha"]).status_code == 200
    assert db.values("SELECT VALUE id FROM description WHERE recording = $r", r=rid) == []


@POPPLER
def test_a_documents_pages_are_described(client, people, db, cfg, llm):
    cfg["llm"]["describe_max"] = 1
    up = _upload(client, people["he"], text_pdf([["The harbour report"], ["The keeper and his dog."]]), "harbour.pdf")
    drain(db, cfg)
    rid = up["recording"]
    log = _log(db, rid)
    assert "1 page(s) described (fake-eyes); the 1 after the first 1 weren't (llm.describe_max)" in log, log
    (d,) = client.get(f"/api/v1/resources/{rid}/player", headers=people["hv"]).json()["descriptions"]
    assert (d["idx"], d["t0"], d["t1"], d["paged"]) == (0, 0, 1, True)
    assert d["text"] == "A white page with a few lines of black printed text."  # its spaces made single
    assert _asked(llm)[0]["messages"][1]["content"][0]["text"] == "Page 1 of the document “harbour”."
    hit = next(h for h in client.get("/api/v1/search", params={"q": "white page"}, headers=people["hv"]).json()["hits"])
    assert (hit["source"], hit["page"], hit["t0"]) == ("described", 0, None)


def test_describing_needs_a_model_that_can_see(client, people, db, cfg, llm, monkeypatch):
    out = io.BytesIO()
    scan(["Galway harbour"]).save(out, format="PNG")
    cfg["llm"]["vision_model"] = None
    up = _upload(client, people["he"], out.getvalue(), "harbour.png")
    drain(db, cfg)
    assert "describe skipped: no model that can see images is chosen (llm.vision_model)" in _log(db, up["recording"])
    assert _asked(llm) == []

    # a model that turns out not to see: the step fails, saying what the server said
    cfg["llm"]["vision_model"] = "fake-blind"
    monkeypatch.setattr(fake_llm.Handler, "blind", True)
    jobs.enqueue(db, up["recording"], ["describe"], by="test")
    drain(db, cfg)
    job = db.one("SELECT status, error, created_at FROM job WHERE recording = $r ORDER BY created_at DESC LIMIT 1", r=up["recording"])
    assert job["status"] == "failed" and "fake-blind couldn't describe its pages" in job["error"], job
    assert "does not support image input" in job["error"]


def test_description_settings_are_checked(client, people):
    ha = people["ha"]
    assert client.put("/api/v1/settings/llm", json={"vision_model": "  llava  ", "describe_max": 20}, headers=ha).status_code == 200
    assert {k: client.get("/api/v1/settings", headers=ha).json()["llm"]["values"][k] for k in ("vision_model", "describe_max")} == {
        "vision_model": "llava",
        "describe_max": 20,
    }
    r = client.put("/api/v1/settings/llm", json={"describe_max": 0}, headers=ha)
    assert (r.status_code, r.json()["detail"]) == (400, "llm.describe_max is a whole number from 1 to 1000")
    assert client.put("/api/v1/settings/llm", json={"vision_model": 5}, headers=ha).status_code == 400
    assert client.put("/api/v1/settings/llm", json={"vision_model": ""}, headers=ha).status_code == 200  # none
    assert client.get("/api/v1/settings", headers=ha).json()["llm"]["values"]["vision_model"] is None
    assert client.put("/api/v1/settings/llm", json={"vision_model": "llava"}, headers=people["he"]).status_code == 403
