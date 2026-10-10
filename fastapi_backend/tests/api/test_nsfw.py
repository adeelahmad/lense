"""NSFW classification (docs/nsfw.md): off by default; on for the archive or for one namespace, the nsfw step scores
a resource's pictures with a local model and keeps the highest; resources are flagged by their namespace's threshold
(kept in step when it changes), shown blurred or left out of the Library, marked by hand, classified after the fact,
and reached by agents over MCP. The model is read the way its export expects (ViT: RGB, 384 pixels, [-1, 1])."""

from __future__ import annotations

import os
import shutil

import numpy as np
import pytest
from PIL import Image

from app.api import mcp_tools
from app.domain import components, jobs, machine, nsfw, settings, store
from tests.api.test_documents import _png, _upload
from tests.api.test_mcp import tool, tool_error
from tests.helpers import drain, login, make_user

R = store.R


class FakeModel:
    """Red pictures are NSFW, green ones nearly, anything else safe."""

    name = "fake-vit"

    def score(self, path):
        r, g, b = Image.open(path).convert("RGB").getpixel((2, 2))
        return 0.93 if r > 150 and g < 100 else 0.6 if g > 150 and r < 100 else 0.04


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(nsfw, "engine", lambda cfg: (FakeModel(), None))


@pytest.fixture
def env(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "ha": login(client, "root@x.io", "root password 1"),
        "ho": login(client, "own@x.io", "owner password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
    }


def _photo(client, h, colour, name, ns="pods"):
    return _upload(client, h, _png(Image.new("RGB", (48, 32), colour)), name, ns=ns)["recording"]


def _log(db, rid):
    return " ".join(x for j in db.rows("SELECT log FROM job WHERE recording = $r", r=rid) for x in j["log"])


def _ids(client, h, **params):
    return {r["id"] for r in client.get("/api/v1/recordings", params=params, headers=h).json()}


def test_off_by_default_nothing_is_fetched_or_classified(client, env, db, cfg, model):
    assert store.DEFAULTS["nsfw"] == {"enabled": False, "threshold": 0.8, "display": "blur", "max_frames": 300}
    rid = _photo(client, env["he"], "red", "a.png")
    drain(db, cfg)
    assert "nsfw skipped: NSFW classification is off in this namespace" in _log(db, rid)
    assert db.one("SELECT nsfw FROM $r", r=R("recording", rid)).get("nsfw") is None
    assert client.get(f"/api/v1/recordings/{rid}", headers=env["hv"]).json()["nsfw"] is None
    assert not components.BY_ID["nsfw"].wanted_by(db, cfg, machine.probe(cfg["data_dir"]))
    got = client.get("/api/v1/namespaces/pods/nsfw", headers=env["hv"]).json()
    assert (got["enabled"], got["own"], got["unclassified"]) == (False, {"enabled": None, "threshold": None, "display": None}, 1)
    r = client.put(f"/api/v1/recordings/{rid}/nsfw", json={"flagged": True}, headers=env["he"])
    assert r.status_code == 409 and "off" in r.json()["detail"]
    assert client.post("/api/v1/namespaces/pods/nsfw/classify", headers=env["he"]).status_code == 409
    assert client.get("/api/v1/recordings/nsfw", headers=env["hv"]).json() == {"on": False, "flagged": 0, "hidden": 0}


def test_a_namespace_classifies_flags_blurs_and_hides(client, env, db, cfg, model):
    hv, he = env["hv"], env["he"]
    assert client.put("/api/v1/namespaces/pods/nsfw", json={"enabled": True}, headers=he).status_code == 403  # owners
    got = client.put("/api/v1/namespaces/pods/nsfw", json={"enabled": True}, headers=env["ho"]).json()
    assert (got["enabled"], got["own"]["enabled"], got["archive"]["enabled"], got["threshold"], got["display"]) == (
        True,
        True,
        False,
        0.8,
        "blur",
    )
    assert components.BY_ID["nsfw"].wanted_by(db, cfg, machine.probe(cfg["data_dir"]))  # the model is wanted now
    red, white, green = _photo(client, he, "red", "red.png"), _photo(client, he, "white", "white.png"), _photo(client, he, "lime", "g.png")
    other = _photo(client, env["ha"], "red", "elsewhere.png", ns="calls")  # off there
    drain(db, cfg)
    assert "flagged NSFW: highest score 0.93 (threshold 0.8) over 1 page(s) (fake-vit)" in _log(db, red)
    assert "not NSFW: highest score 0.04" in _log(db, white)
    kept = db.one("SELECT nsfw FROM $r", r=R("recording", red))["nsfw"]
    assert (kept["score"], kept["flagged"], kept["checked"], kept["of"], kept["paged"], kept["model"]) == (
        0.93,
        True,
        1,
        1,
        True,
        "fake-vit",
    )
    assert kept["hits"][0]["score"] == 0.93 and kept["hits"][0]["t"] == 0
    assert db.one("SELECT nsfw FROM $r", r=R("recording", other)).get("nsfw") is None

    # the resource, its player and the Library say so; the web app blurs it
    rec = client.get(f"/api/v1/recordings/{red}", headers=hv).json()["nsfw"]
    assert (rec["flagged"], rec["score"], rec["blur"], rec["display"], rec["manual"]) == (True, 0.93, True, "blur", None)
    assert "sig=" in rec["hits"][0]["frame"] and client.get(rec["hits"][0]["frame"]).status_code == 200
    assert client.get(f"/api/v1/recordings/{red}/player", headers=hv).json()["nsfw"]["flagged"] is True
    rows = {r["id"]: r for r in client.get("/api/v1/recordings", headers=hv).json()}
    assert rows[red]["nsfw"]["blur"] is True and rows[white]["nsfw"]["flagged"] is False and rows[green]["nsfw"]["flagged"] is False
    assert _ids(client, hv, sensitive="only") == {red}
    assert red not in _ids(client, hv, sensitive="exclude") and white in _ids(client, hv, sensitive="exclude")
    assert nsfw.blurred(db, cfg, [red, white, other]) == {red}
    st = client.get("/api/v1/namespaces/pods/nsfw", headers=hv).json()
    assert (st["classified"], st["flagged"], st["unclassified"]) == (3, 1, 0)

    # hidden: left out of the Library unless asked for
    client.put("/api/v1/namespaces/pods/nsfw", json={"display": "hide"}, headers=env["ho"])
    assert red not in _ids(client, hv) and white in _ids(client, hv)
    assert red in _ids(client, hv, sensitive="include")
    assert client.get("/api/v1/recordings/nsfw", headers=hv).json() == {"on": True, "flagged": 1, "hidden": 1}
    assert client.get("/api/v1/recordings/nsfw", params={"ns": "calls"}, headers=env["ha"]).json()["on"] is False
    client.put("/api/v1/namespaces/pods/nsfw", json={"display": "show"}, headers=env["ho"])
    assert red in _ids(client, hv)
    assert client.get(f"/api/v1/recordings/{red}", headers=hv).json()["nsfw"]["blur"] is False  # flagged, shown as it is

    # a lower threshold flags the green one at once; a person's call wins over the score
    got = client.put("/api/v1/namespaces/pods/nsfw", json={"threshold": 0.55, "display": None}, headers=env["ho"]).json()
    assert (got["threshold"], got["display"], got["flagged"]) == (0.55, "blur", 2)
    assert _ids(client, hv, sensitive="only") == {red, green}
    assert client.put(f"/api/v1/recordings/{green}/nsfw", json={"flagged": False}, headers=hv).status_code == 403  # editors
    assert client.put(f"/api/v1/recordings/{green}/nsfw", json={"flagged": False}, headers=he).json()["manual"] is False
    client.put("/api/v1/namespaces/pods/nsfw", json={"threshold": 0.5}, headers=env["ho"])
    assert _ids(client, hv, sensitive="only") == {red}  # still not, by hand
    assert client.put(f"/api/v1/recordings/{green}/nsfw", json={"flagged": None}, headers=he).json()["flagged"] is True
    assert client.put(f"/api/v1/recordings/{white}/nsfw", json={"flagged": True}, headers=he).json()["flagged"] is True

    # off again: what was found stays, but nothing acts on it
    client.put("/api/v1/namespaces/pods/nsfw", json={"enabled": False}, headers=env["ho"])
    assert client.get(f"/api/v1/recordings/{red}", headers=hv).json()["nsfw"] is None
    assert _ids(client, hv, sensitive="only") == set()


def test_the_archive_setting_and_a_namespace_staying_off(client, env, db, cfg, model):
    ha = env["ha"]
    for bad in ({"threshold": 0.2}, {"display": "pixelate"}, {"max_frames": 0}, {"enabled": "yes"}):
        assert client.put("/api/v1/settings/nsfw", json=bad, headers=ha).status_code == 400, bad
    assert client.put("/api/v1/settings/nsfw", json={"enabled": True}, headers=ha).status_code == 200
    eff = settings.effective(db, cfg)
    assert nsfw.policy(db, eff, store.ns_id(db, "calls"))["enabled"] and components.BY_ID["nsfw"].needed(eff, None)
    client.put("/api/v1/namespaces/pods/nsfw", json={"enabled": False}, headers=env["ho"])
    pods, calls = _photo(client, env["he"], "red", "p.png"), _photo(client, ha, "red", "c.png", ns="calls")
    drain(db, eff)
    assert "nsfw skipped" in _log(db, pods) and db.one("SELECT nsfw FROM $r", r=R("recording", calls))["nsfw"]["flagged"] is True
    # the archive's threshold re-flags every namespace that follows it
    assert client.put("/api/v1/settings/nsfw", json={"threshold": 0.95}, headers=ha).status_code == 200
    assert db.one("SELECT nsfw FROM $r", r=R("recording", calls))["nsfw"]["flagged"] is False


def test_what_was_there_before_is_classified_when_asked(client, env, db, cfg, model):
    rid = _photo(client, env["he"], "red", "old.png")
    drain(db, cfg)
    got = client.put("/api/v1/namespaces/pods/nsfw", json={"enabled": True, "classify": True}, headers=env["ho"]).json()
    assert (got["queued"], got["unclassified"]) == (1, 1)
    drain(db, cfg)
    assert db.one("SELECT nsfw FROM $r", r=R("recording", rid))["nsfw"]["flagged"] is True
    assert client.post("/api/v1/namespaces/pods/nsfw/classify", headers=env["he"]).json()["jobs"] == []  # nothing left
    # moved to a namespace with a stricter threshold, it's flagged by that one
    calls = store.ns_id(db, "calls")
    nsfw.save_own(db, calls, {"enabled": True, "threshold": 0.99})
    assert client.post(f"/api/v1/recordings/{rid}/move", json={"namespace": "calls"}, headers=env["ha"]).status_code == 200
    assert db.one("SELECT nsfw FROM $r", r=R("recording", rid))["nsfw"]["flagged"] is False


def test_agents_turn_it_on_read_and_mark(client, env, db, cfg, model):
    he, ho, ha = env["he"], env["ho"], env["ha"]
    rid = _photo(client, he, "red", "a.png")
    drain(db, cfg)
    assert tool(client, env["hv"], "nsfw_settings", namespace="pods")["enabled"] is False
    assert tool_error(client, he, "set_nsfw", namespace="pods", enabled=True)  # owners
    assert not nsfw.policy(db, cfg, store.ns_id(db, "pods"))["enabled"]
    assert "only admins" in tool_error(client, ho, "set_nsfw", enabled=True)
    got = tool(client, ho, "set_nsfw", namespace="pods", enabled=True, threshold=0.7, classify_existing=True)
    assert (got["saved"], got["queued"], got["enabled"], got["threshold"]) == ({"enabled": True, "threshold": 0.7}, 1, True, 0.7)
    drain(db, cfg)
    assert tool(client, env["hv"], "get_recording", recording_id=rid)["nsfw"] == {
        "flagged": True,
        "score": 0.93,
        "threshold": 0.7,
        "checked": 1,
        "of": 1,
    }
    listed = tool(client, env["hv"], "list_recordings", sensitive="only")["recordings"]
    assert [(r["recording_id"], r["nsfw"]) for r in listed] == [(rid, True)]
    assert tool(client, he, "mark_nsfw", recording_id=rid, verdict="not_nsfw")["nsfw"]["flagged"] is False
    assert tool(client, he, "mark_nsfw", recording_id=rid, verdict="model")["nsfw"]["flagged"] is True
    assert tool(client, ha, "set_nsfw", display="hide")["saved"] == {"display": "hide"}
    assert set(mcp_tools.TOOLS) >= {"nsfw_settings", "set_nsfw", "mark_nsfw"}


def test_workers_run_the_step_from_older_lists(db, cfg):
    assert "nsfw" in jobs.Worker(db, lambda: cfg).can
    older = {**cfg, "workers": {**cfg["workers"], "steps": [s for s in cfg["workers"]["steps"] if s != "nsfw"]}}
    assert "nsfw" in jobs.Worker(db, lambda: older).can  # lists with objects run it too
    assert "nsfw" not in jobs.Worker(db, lambda: cfg, steps=["transcribe"]).can  # a list given by name is kept


# ---------- the model ----------
def _model_file(path, dynamic=True):
    """A model shaped like the real export (pixel_values → logits [sfw, nsfw]) whose answer follows the red channel."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    w = np.array([[-4.0, 4.0], [0.0, 0.0], [0.0, 0.0]], np.float32)
    nodes = [
        helper.make_node("ReduceMean", ["pixel_values"], ["mean"], axes=[2, 3], keepdims=0),
        helper.make_node("Constant", [], ["w"], value=numpy_helper.from_array(w, name="wv")),
        helper.make_node("MatMul", ["mean", "w"], ["logits"]),
    ]
    dims = ["batch_size", 3, "height", "width"] if dynamic else [1, 3, 64, 64]
    graph = helper.make_graph(
        nodes,
        "vit-shaped",
        [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, dims)],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch_size" if dynamic else 1, 2])],
    )
    m = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    m.ir_version = 8
    onnx.save(m, str(path))
    return str(path)


def test_the_model_is_read_as_its_export_expects(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(nsfw, "MODELS", tmp_path / "none")
    assert nsfw.engine(cfg) == (None, "the NSFW model isn't here yet (it's fetched the first time a job needs it)")
    models = tmp_path / "data" / "models"
    models.mkdir(parents=True)
    _model_file(models / nsfw.MODEL)
    found, why = nsfw.engine(cfg)
    assert why is None and found.size == (384, 384) and found.name == "vit-base-nsfw-detector"
    for colour, want in (("red", 0.9997), ("blue", 0.0003)):
        Image.new("RGB", (90, 60), colour).save(tmp_path / f"{colour}.png")
        assert round(found.score(tmp_path / f"{colour}.png"), 4) == want
    x = nsfw._pixels(Image.new("RGB", (10, 20), (255, 0, 128)), (8, 4))
    assert x.shape == (1, 3, 8, 4) and [round(float(v), 3) for v in x[0, :, 0, 0]] == [1.0, -1.0, 0.004]
    assert components.FILES[nsfw.MODEL][1] == "432763a6899ebc418c55f784b98f90565e5fc694c778d2ffcb0294b12f6a7404"


def test_long_resources_are_sampled_evenly():
    assert nsfw.spread(list(range(10)), 4) == [0, 3, 6, 9]
    assert nsfw.spread([1, 2], 5) == [1, 2] and nsfw.spread([1, 2, 3], 1) == [1]


@pytest.mark.skipif(not os.environ.get("LENS_TEST_NSFW_MODEL"), reason="LENS_TEST_NSFW_MODEL: the real model file")
def test_the_real_model(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(nsfw, "MODELS", tmp_path)
    shutil.copy(os.environ["LENS_TEST_NSFW_MODEL"], tmp_path / nsfw.MODEL)
    found, why = nsfw.engine(cfg)
    assert why is None and found.size == (384, 384)
    Image.new("RGB", (640, 480), (40, 90, 160)).save(tmp_path / "sky.jpg")
    assert found.score(tmp_path / "sky.jpg") < 0.5  # a plain blue picture is safe for work
