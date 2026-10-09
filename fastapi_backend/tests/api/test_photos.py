"""Searching photos by what they show (photos.py, embeddings.photos): off by default; on, each image resource gets
anytopdf's CLIP vector when it's indexed, and a query finds the photos like it that the person may see."""

from __future__ import annotations

import os

import pytest
from PIL import Image

from app.domain import components, deletion, photos, settings, store
from tests import fake_anytopdf
from tests.api.test_documents import _png, _upload
from tests.helpers import drain, login, make_user

R = store.R
REAL = os.environ.get("LENS_TEST_ANYTOPDF")


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    return {"he": login(client, "ed@x.io", "editor password 1"), "ho": login(client, "out@x.io", "outsider password 1")}


@pytest.fixture
def clip(cfg, tmp_path):
    (tmp_path / "bin").mkdir()
    cfg["documents"]["anytopdf"] = fake_anytopdf.make(tmp_path / "bin" / "anytopdf", tmp_path / "anytopdf.log")
    return fake_anytopdf.clip(tmp_path / "bin" / "plugins", photos.model_dir(cfg))


@pytest.fixture
def on(cfg):
    cfg["embeddings"]["photos"] = True


def _photo(text):
    return _png(Image.new("RGB", (32, 24), "white")) + text.encode()  # the fake reads what it shows from its bytes


def test_off_by_default(clip, client, env, db, cfg):
    assert store.DEFAULTS["embeddings"]["photos"] is False
    _upload(client, env["he"], _photo("bus"), "street.png")
    drain(db, settings.effective(db, cfg))
    assert not db.rows("SELECT * FROM photo_vector")
    r = client.get("/api/v1/search/photos", params={"q": "a bus"}, headers=env["he"])
    assert r.status_code == 409 and "off" in r.json()["detail"]


def test_photos_found_by_what_they_show(on, clip, client, env, db, cfg):
    bus = _upload(client, env["he"], _photo("bus"), "street.png")["recording"]
    beach = _upload(client, env["he"], _photo("beach"), "holiday.png")["recording"]
    doc = _upload(client, env["he"], b"A bus timetable\n", "times.txt")["recording"]
    drain(db, settings.effective(db, cfg))
    assert {v["recording"] for v in db.rows("SELECT recording FROM photo_vector")} == {bus, beach}  # photos only
    r = client.get("/api/v1/search/photos", params={"q": "a bus on a city street"}, headers=env["he"])
    assert r.status_code == 200, r.text
    hits = r.json()["hits"]
    assert [h["recording"] for h in hits] == [bus]
    assert hits[0]["namespace"] == "pods" and hits[0]["title"] and hits[0]["similarity"] > 0.9
    assert hits[0]["thumb"] and "sig=" in hits[0]["thumb"]
    assert doc not in [h["recording"] for h in hits]
    # someone without a role in the namespace finds none of them
    assert client.get("/api/v1/search/photos", params={"q": "a bus"}, headers=env["ho"]).json()["hits"] == []
    assert client.get("/api/v1/search/photos", params={"q": "a bus", "ns": "pods"}, headers=env["ho"]).status_code == 404
    # indexed again: the same file isn't read again; deleted, its vector goes too
    assert photos.index_recording(db, settings.effective(db, cfg), bus, say=lambda m: None) is False
    deletion.delete(db, cfg, bus)
    assert not db.rows("SELECT * FROM photo_vector WHERE recording = $r", r=bus)


def test_the_model_is_fetched_only_when_asked_for(cfg):
    c = components.BY_ID["clip"]
    assert not c.needed(cfg, None)
    cfg["embeddings"]["photos"] = True
    assert c.needed(cfg, None) is bool(photos.anytopdf.archive())
    assert c.steps == {"embed"}


@pytest.mark.skipif(
    not (REAL and photos.model_ready({"data_dir": os.environ.get("LENS_TEST_CLIP_DATA", "/nowhere")})),
    reason="LENS_TEST_ANYTOPDF and LENS_TEST_CLIP_DATA (its models/clip)",
)
def test_the_real_clip(tmp_path):
    cfg = {"data_dir": os.environ["LENS_TEST_CLIP_DATA"], "documents": {"anytopdf": REAL}, "embeddings": {"photos": True}}
    from PIL import ImageDraw

    img = Image.new("RGB", (400, 300), (40, 90, 200))
    ImageDraw.Draw(img).rectangle((0, 200, 400, 300), fill=(230, 200, 140))
    img.save(tmp_path / "p.jpg")
    pic, _ = photos._encode(cfg, "--encode-image", str(tmp_path / "p.jpg"))
    sea, model = photos._encode(cfg, "--encode-text", "a blue sky over sand")
    assert len(pic) == len(sea) == 512 and model.startswith("clip")
