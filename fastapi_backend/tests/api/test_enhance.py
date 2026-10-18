"""Enhancing low quality images (docs/enhance.md): off by default; turned on, the enhance step measures an image before
it's read and, when it's small, blurry, noisy, dull or has faces too small to recognise, makes an enhanced copy that the
read step and the steps after it work from, keeping the original. Asked for (the API, MCP, an automation), it enhances
whatever the settings and the picture's quality. Real-ESRGAN is read the way its export expects: RGB from 0 to 1, in
tiles of 128 pixels, four times as big."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageFilter

from app.api import mcp_tools
from app.domain import actions, components, enhance, faces, jobs, keyring, machine, settings, store, video
from tests.api.test_documents import _png, _upload
from tests.api.test_mcp import tool, tool_error
from tests.helpers import drain, login, make_user

R = store.R


@pytest.fixture
def env(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "ha": login(client, "root@x.io", "root password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
    }


def on(cfg, **more):
    return {**cfg, "enhance": {**cfg["enhance"], "images": True, **more}}


def _scene(w, h, seed=1):
    """A picture with edges and texture: rectangles on a gradient."""
    rng = np.random.default_rng(seed)
    a = np.tile(np.linspace(40, 210, w, dtype=np.float32), (h, 1))[..., None].repeat(3, axis=2)
    for _ in range(12):
        x, y = int(rng.integers(0, w - 8)), int(rng.integers(0, h - 8))
        a[y : y + int(rng.integers(4, h // 3)), x : x + int(rng.integers(4, w // 3))] = rng.integers(0, 255, 3)
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))


def _photo(client, h, img, name="photo.png", ns="pods"):
    return _upload(client, h, _png(img), name, ns=ns)["recording"]


def _log(db, rid):
    return " ".join(x for j in db.rows("SELECT log FROM job WHERE recording = $r", r=rid) for x in j["log"])


def _page(db, rid):
    return db.one("SELECT width, height FROM page WHERE recording = $r AND idx = 0", r=rid)


def test_off_by_default_the_original_is_read(client, env, db, cfg):
    assert store.DEFAULTS["enhance"] == {
        "images": False,
        "image_engine": "classic",
        "only_faces": False,
        "min_side": 720,
        "min_sharpness": 100,
        "min_face_px": 64,
        "max_scale": 4,
        "audio": False,
        "audio_engine": "ffmpeg",
        "min_snr_db": 15,
        "min_level_db": -35,
    }
    assert store.DEFAULTS["workers"]["steps"][0] == "enhance" and jobs.PIPELINE[0] == "enhance"
    rid = _photo(client, env["he"], _scene(160, 120).filter(ImageFilter.GaussianBlur(3)))
    drain(db, cfg)
    assert "enhance skipped: enhancing images is off (Settings → Enhance)" in _log(db, rid)
    assert _page(db, rid) == {"width": 160, "height": 120}
    assert client.get(f"/api/v1/recordings/{rid}", headers=env["hv"]).json()["enhanced"] is None
    assert not enhance.files(cfg, rid)
    assert not components.BY_ID["enhance"].wanted_by(db, cfg, machine.probe(cfg["data_dir"]))


def test_a_low_quality_image_is_read_from_its_enhanced_copy(client, env, db, cfg):
    he, hv = env["he"], env["hv"]
    small = _scene(240, 180).filter(ImageFilter.GaussianBlur(2))
    rid = _photo(client, he, small)
    drain(db, on(cfg))
    log = _log(db, rid)
    assert "enhanced (small, blurry): 240×180 to 960×720 by classic" in log and "read from its enhanced copy" in log
    assert _page(db, rid) == {"width": 960, "height": 720}  # its page is drawn from the copy
    e = db.one("SELECT enhanced FROM $r", r=R("recording", rid))["enhanced"]
    assert (e["low"], e["scale"], e["engine"], e["enhanced"]) == (["small", "blurry"], 4.0, "classic", True)
    assert e["measured"]["width"] == 240 and e["measured"]["sharpness"] < 100

    got = client.get(f"/api/v1/recordings/{rid}", headers=hv).json()["enhanced"]
    assert (got["width"], got["height"], got["low"]) == (960, 720, ["small", "blurry"])
    r = client.get(got["download"])  # a signed link, for a member
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).size == (960, 720)
    assert client.get(f"/api/v1/recordings/{rid}/enhanced", headers=hv).status_code == 200
    # the original stays as it was
    assert Image.open(io.BytesIO(client.get(f"/api/v1/recordings/{rid}/media", headers=hv).content)).size == (240, 180)
    # a share link's visitor sees its pages, not the copy
    link = client.post(f"/api/v1/recordings/{rid}/share", headers=he, json={}).json()
    assert client.get(f"/api/v1/recordings/{rid}/enhanced?s={link['token']}").status_code == 404

    # deleting the resource removes the copy
    path = enhance.enhanced_path(cfg, rid)
    assert path.is_file()
    assert client.delete(f"/api/v1/recordings/{rid}", headers=env["ha"]).status_code == 200
    assert not path.exists()


def test_a_good_image_is_left_as_it_is(client, env, db, cfg):
    rid = _photo(client, env["he"], _scene(900, 760))
    drain(db, on(cfg))
    assert "good enough as it is (900×760" in _log(db, rid)
    e = db.one("SELECT enhanced FROM $r", r=R("recording", rid))["enhanced"]
    assert e["enhanced"] is False and e["low"] == [] and not enhance.files(cfg, rid)
    assert _page(db, rid) == {"width": 900, "height": 760}
    assert client.get(f"/api/v1/recordings/{rid}", headers=env["hv"]).json()["enhanced"].get("download") is None


def test_asked_for_it_is_enhanced_whatever_the_settings_and_kept(client, env, db, cfg):
    he, hv = env["he"], env["hv"]
    rid = _photo(client, he, _scene(900, 760))
    drain(db, cfg)
    assert client.post(f"/api/v1/recordings/{rid}/enhance", headers=hv).status_code == 403  # viewers don't process
    job = client.post(f"/api/v1/recordings/{rid}/enhance", headers=he).json()["job"]
    steps = [s["type"] for s in db.one("SELECT steps FROM $r", r=R("job", job))["steps"]]
    assert steps[:2] == ["enhance", "read"] and "enhance" not in steps[1:]
    drain(db, cfg)
    e = db.one("SELECT enhanced FROM $r", r=R("recording", rid))["enhanced"]
    assert e["forced"] is True and e["enhanced"] is True and e["low"] == [] and enhance.files(cfg, rid)
    assert "enhanced (on request)" in _log(db, rid)
    # processed again with enhancing off, the copy made on request stays
    jobs.enqueue(db, rid, ["enhance", "read"])
    drain(db, cfg)
    assert "kept the copy made on request" in _log(db, rid) and enhance.files(cfg, rid)

    # documents aren't enhanced
    doc = _upload(client, he, b"%PDF-1.4\n", "x.pdf")["recording"]
    r = client.post(f"/api/v1/recordings/{doc}/enhance", headers=he)
    assert r.status_code == 400 and "only images" in r.json()["detail"]


def test_agents_and_automations_enhance_too(client, env, db, cfg, app):
    he, hv = env["he"], env["hv"]
    rid = _photo(client, he, _scene(200, 150))
    drain(db, cfg)
    assert "enhance" in mcp_tools.TOOLS and mcp_tools.TOOLS["enhance"].writes
    assert tool_error(client, hv, "enhance", recording_id=rid) == "needs the process permission for this asset"
    got = tool(client, he, "enhance", recording_id=rid)
    assert got["job"] and got["url"].endswith(f"/resources/{rid}")
    drain(db, cfg)
    assert tool(client, hv, "get_recording", recording_id=rid)["enhanced"]["scale"] == 4.0  # 720 / 150, at most max_scale

    # the automation action, on the asset the activity is about
    me = db.one("SELECT record::id(id) AS id FROM account WHERE email = 'ed@x.io'")["id"]
    intent = {
        "key": "1-n1",
        "action": "enhance",
        "params": {"asset": f"lens:recording/{rid}"},
        "effect": "lens",
        "risk": "routine",
        "untrusted": False,
        "spaces": [store.ns_id(db, "pods")],
        "run_as": me,
        "node": "n1",
        "automation": 4,
        "run": 1,
    }
    assert actions.perform(db, cfg, intent, None, True)["would"].startswith("enhance photo")
    out = actions.perform(db, cfg, intent, None, False)
    assert db.one("SELECT created_by FROM $r", r=R("job", out["job"]))["created_by"] == "automation: Automation 4"
    assert actions.describe("enhance", {"asset": 7}) == "Enhance asset 7"


def test_faces_too_small_to_recognise_and_only_faces(client, env, db, cfg, monkeypatch):
    class FakeFaces:
        name = "fake"

        def faces(self, path):
            img = Image.open(path)
            return (
                [{"box": [0.4, 0.4, 0.05, 30 / img.height], "score": 0.9, "embedding": np.ones(4)}] if img.getpixel((0, 0))[0] > 100 else []
            )

    monkeypatch.setattr(video, "face_engine", lambda cfg: FakeFaces())
    he = env["he"]
    with_face = _scene(1200, 900, seed=3)
    with_face.putpixel((0, 0), (250, 250, 250))
    no_face = _scene(1200, 900, seed=4)
    no_face.putpixel((0, 0), (5, 5, 5))
    a, b = _photo(client, he, with_face, "a.png"), _photo(client, he, no_face, "b.png")
    drain(db, on(cfg, only_faces=True))
    # the namespace doesn't find faces: none were looked for
    assert "face detection is off in this namespace (enhance.only_faces)" in _log(db, a)
    faces.set_mode(db, store.ns_id(db, "pods"), "detect")
    jobs.enqueue(db, a, ["enhance"])
    jobs.enqueue(db, b, ["enhance"])
    drain(db, on(cfg, only_faces=True))
    assert "there's no face in it (enhance.only_faces)" in _log(db, b)
    e = db.one("SELECT enhanced FROM $r", r=R("recording", a))["enhanced"]
    assert e["low"] == ["small_faces"] and e["measured"]["smallest_face"] == 30 and e["scale"] == round(64 / 30, 2)


def test_measuring(cfg):
    sharp = _scene(400, 300)
    m = enhance.measure(sharp)
    assert m["sharpness"] > 100 and m["noise"] < 2 and m["contrast"] > 30
    assert enhance.measure(sharp.filter(ImageFilter.GaussianBlur(3)))["sharpness"] < 100
    rng = np.random.default_rng(0)
    flat = np.full((300, 400, 3), 128, dtype=np.float32) + rng.normal(0, 12, (300, 400, 1))
    noisy = enhance.measure(Image.fromarray(flat.clip(0, 255).astype(np.uint8)))
    assert 10 < noisy["noise"] < 14  # Immerkær's estimate finds the noise it was given
    assert enhance.measure(Image.new("RGB", (400, 300), (90, 90, 90)))["contrast"] < enhance.DULL
    opts = enhance.options(cfg)
    assert enhance.assess(m, None, opts) == (["small"], 2.4)
    assert enhance.assess({**m, "width": 4000, "height": 3000}, [20], opts) == (["small_faces"], 1.0)  # MAX_SIDE
    assert enhance.assess(m, [80], {**opts, "max_scale": 1})[1] == 1.0
    assert enhance.classic(sharp, ["small"], 2.4).size == (960, 720)


class Nearest:
    """Stands in for Real-ESRGAN's session: each 128-pixel tile four times as big, nearest neighbour."""

    def __init__(self):
        self.calls = 0

    def get_inputs(self):
        return [type("I", (), {"name": "image", "shape": [1, 3, 128, 128]})()]

    def run(self, _, feeds):
        x = feeds["image"]
        assert x.shape == (1, 3, 128, 128) and x.dtype == np.float32 and 0 <= x.min() and x.max() <= 1
        self.calls += 1
        return [x.repeat(4, axis=2).repeat(4, axis=3)]


def test_real_esrgan_is_run_in_tiles_without_seams(cfg, monkeypatch):
    fake = Nearest()
    for name in enhance.MODEL_FILES:
        p = components.models_dir(cfg) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    monkeypatch.setattr(enhance, "_session", lambda path: fake)
    img = _scene(250, 130)
    real = enhance.RealEsrgan(cfg)
    big = real.upscale(img)
    assert big.size == (1000, 520) and fake.calls == 3 * 2
    want = np.asarray(img).repeat(4, axis=0).repeat(4, axis=1)
    assert np.abs(np.asarray(big).astype(int) - want).max() <= 1  # every tile in its place
    said = []
    out, engine = enhance.enhance_image(on(cfg, image_engine="realesrgan"), img, ["small"], 2.0, said.append)
    assert (out.size, engine, said) == ((500, 260), "realesr-general-x4v3", [])
    big = Image.new("RGB", (1300, 1200))
    out, engine = enhance.enhance_image(on(cfg, image_engine="realesrgan"), big, ["blurry"], 1.0, said.append)
    assert engine == "classic" and "too large for Real-ESRGAN" in said[0]
    assert components.BY_ID["enhance"].needed(on(cfg, image_engine="realesrgan"), machine.probe(cfg["data_dir"]))


def test_settings_are_checked(db, cfg):
    base = cfg
    settings.save(db, base, "enhance", {"images": True, "image_engine": "realesrgan", "min_side": 1000, "max_scale": 2.5})
    eff = settings.effective(db, base)["enhance"]
    assert (eff["images"], eff["image_engine"], eff["min_side"], eff["max_scale"]) == (True, "realesrgan", 1000, 2.5)
    for bad, said in (
        ({"image_engine": "anime4k"}, "enhance.image_engine must be one of"),
        ({"min_side": 10}, "enhance.min_side is a number from 64 to 4000"),
        ({"max_scale": 8}, "enhance.max_scale is a number from 1 to 4"),
        ({"images": "yes"}, "enhance.images is true or false"),
        ({"denoise": True}, "unknown setting enhance.denoise"),
    ):
        with pytest.raises(ValueError, match=said):
            settings.save(db, base, "enhance", bad)


def test_the_copy_is_encrypted_where_the_files_are(client, env, db, cfg):
    eff = settings.effective(db, cfg)  # a new archive encrypts its files
    rid = _photo(client, env["he"], _scene(200, 150))
    drain(db, on(eff))
    path = enhance.enhanced_path(cfg, rid)
    assert keyring.is_encrypted(path)
    assert str(path) in list(keyring.recording_files(db, cfg, rid))
    r = client.get(f"/api/v1/recordings/{rid}/enhanced", headers=env["he"])
    assert r.status_code == 200 and Image.open(io.BytesIO(r.content)).size == (800, 600)
