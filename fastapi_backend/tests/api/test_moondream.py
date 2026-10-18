"""Moondream describing pictures on this machine (docs/configuration.md#descriptions): with llm.describe_engine
"moondream" the describe step needs no LLM provider; Moondream 3 Preview is fetched only where it can run (a GPU, or a
Mac with the memory), Moondream 2 on a CPU with the memory; both are pinned to a revision; each picture is a ledger
row; and admins and agents choose it. PyTorch isn't installed for these tests: the model is a fake."""

from __future__ import annotations

import contextlib
import os
import sys
import types

import huggingface_hub
import pytest
from PIL import Image

from app.domain import components, descriptions, machine, moondream, store
from tests.api.test_documents import _png, _upload
from tests.api.test_mcp import tool, tool_error
from tests.helpers import drain, login, make_user

R = store.R
CPU = {**machine.probe(), "os": "linux", "cuda": False, "gpus": [], "memory_gb": 16.0, "apple_silicon": False}
GPU = {**CPU, "cuda": True, "gpus": [{"name": "NVIDIA RTX 4090", "memory_gb": 24.0}]}


class Model:
    """Moondream's caption skill: says whether the picture is redder than it is blue."""

    def __init__(self):
        self.lengths = []

    def caption(self, picture, length):
        assert picture.mode == "RGB"
        self.lengths.append(length)
        r, _, b = picture.getpixel((0, 0))
        return {"caption": "A red square." if r > b else "A blue square."}


@pytest.fixture
def server(monkeypatch):
    """This machine, as the tests say it is."""
    box = {"m": CPU}
    monkeypatch.setattr(machine, "probe", lambda data_dir=None: dict(box["m"]))
    return box


@pytest.fixture
def model(cfg, monkeypatch, server):
    """Moondream 2 "fetched": PyTorch and transformers "importable", its weights marked here, the model a fake."""
    torch = types.ModuleType("torch")
    torch.inference_mode = contextlib.nullcontext
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", types.ModuleType("transformers"))
    components._mark(cfg, moondream._mark("moondream2"))
    fake, loads = Model(), []
    monkeypatch.setattr(moondream, "_load", lambda cfg, key: loads.append(key) or {"key": key, "model": fake, "device": "cpu"})
    cfg["llm"].update(base_url=None, model=None, vision_model=None, describe_engine="moondream", moondream_model="moondream2")
    return fake, loads


@pytest.fixture
def people(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {"ha": login(client, "root@x.io", "root password 1"), "he": login(client, "ed@x.io", "editor password 1")}


def test_moondream_describes_pictures_without_a_model_provider(client, people, db, cfg, model):
    fake, loads = model
    red = _upload(client, people["he"], _png(Image.new("RGB", (64, 48), (220, 20, 20))), "red.png")["recording"]
    blue = _upload(client, people["he"], _png(Image.new("RGBA", (64, 48), (20, 20, 220, 255))), "blue.png")["recording"]
    drain(db, cfg)
    got = {rid: descriptions.for_recording(db, rid) for rid in (red, blue)}
    assert [(d["text"], d["model"], d["paged"]) for d in got[red]] == [("A red square.", "Moondream-2", True)]
    assert got[blue][0]["text"] == "A blue square."
    assert set(loads) == {"moondream2"} and fake.lengths == ["normal", "normal"]
    log = " ".join(db.one("SELECT log FROM job WHERE recording = $r", r=red)["log"])
    assert "1 page(s) described (Moondream-2)" in log
    # each picture is a row in the activity ledger, for its resource
    rows = db.rows("SELECT model, resources, detail FROM activity WHERE action = 'model.describe'")
    assert len(rows) == 2 and {r["model"] for r in rows} == {"Moondream-2"}
    assert all(r["detail"] == {"engine": "moondream"} for r in rows)
    assert any(f"recording:{red}" in r["resources"] for r in rows)


def test_a_model_that_fails_fails_the_picture_not_the_job(cfg, folder, model):
    fake, _ = model
    fake.caption = lambda picture, length: 1 / 0
    Image.new("RGB", (8, 8), "white").save(folder / "w.png")
    eye, why = descriptions.engine(cfg)
    assert why is None
    with pytest.raises(descriptions.Failed, match="Moondream-2 couldn't read it: division by zero"):
        eye.describe(folder / "w.png")


def test_where_each_moondream_can_run(cfg, server):
    cfg["llm"]["describe_engine"] = "moondream"
    # Moondream 3 Preview (the default): an NVIDIA GPU with the memory, or a Mac with 32 GB; never a CPU
    assert moondream.choice(cfg) == "moondream3-preview"
    assert "needs an NVIDIA GPU with 20 GB, or a Mac with 32 GB" in moondream.cannot_run(cfg, CPU)
    assert moondream.cannot_run(cfg, GPU) is None
    assert moondream.cannot_run(cfg, {**GPU, "gpus": [{"name": "small", "memory_gb": 8.0}]})
    assert moondream.cannot_run(cfg, {**GPU, "gpus": [{"name": "NVIDIA GPU", "memory_gb": None}]}) is None
    mac = {**CPU, "os": "darwin", "apple_silicon": True}
    assert moondream.cannot_run(cfg, {**mac, "memory_gb": 64.0}) is None
    assert moondream.cannot_run(cfg, {**mac, "memory_gb": 16.0})
    # Moondream 2: anywhere with the memory
    cfg["llm"]["moondream_model"] = "moondream2"
    assert moondream.cannot_run(cfg, CPU) is None
    assert moondream.cannot_run(cfg, {**CPU, "memory_gb": 4.0}) == (
        "Moondream-2 needs about 10 GB of memory on a CPU and this server has 4.0 GB: pick Florence-2"
    )
    # an unknown model is the default
    cfg["llm"]["moondream_model"] = "moondream9"
    assert moondream.choice(cfg) == "moondream3-preview"


def test_on_a_cpu_moondream_3_is_never_fetched_and_the_step_says_why(client, people, db, cfg, server):
    cfg["llm"]["describe_engine"] = "moondream"
    c = components.BY_ID["moondream"]
    assert not c.needed(cfg, CPU) and c.needed(cfg, GPU)  # 19 GB isn't fetched where it can't run
    rid = _upload(client, people["he"], _png(Image.new("RGB", (64, 48), "white")), "white.png")["recording"]
    drain(db, cfg)
    log = " ".join(db.one("SELECT log FROM job WHERE recording = $r", r=rid)["log"])
    assert "describe skipped: Moondream-3-Preview needs an NVIDIA GPU" in log
    st = descriptions.status(cfg)
    assert (st["engine"], st["model"], st["ready"]) == ("moondream", "Moondream-3-Preview", False)
    assert "pick moondream2" in st["why_not"]


def test_moondream_is_fetched_pinned(cfg, monkeypatch, server):
    c = components.BY_ID["moondream"]
    assert not c.needed(cfg, CPU) and c.steps == {"describe"} and "BSL-1.1" in c.license and "Apache-2.0" in c.license
    cfg["llm"].update(describe_engine="moondream", moondream_model="moondream2")
    assert c.needed(cfg, CPU) and not c.present(cfg)
    got = []
    monkeypatch.setattr(components, "importable", lambda *mods: False)
    monkeypatch.setattr(components, "pip_install", lambda cfg, extra, say=None: got.append(extra))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda repo, revision=None, token=None: got.append((repo, revision)))
    c.fetch(cfg, CPU, None)
    assert got == [
        "moondream",
        ("vikhyatk/moondream2", "5d6c926f44e26b07957b0dd315bbedcb4c17a5fe"),
        ("moondream/starmie-v1", None),
    ]
    monkeypatch.setattr(components, "importable", lambda *mods: True)
    assert c.present(cfg)  # marked: the next check doesn't fetch it again
    cfg["llm"]["moondream_model"] = "moondream3-preview"
    assert not c.present(cfg)
    assert moondream.MODELS["moondream3-preview"]["revision"] == "5112966d1a723413b1c9a1e8bea272b72e647b35"


def test_admins_and_agents_choose_moondream(client, people, db, server):
    ha = people["ha"]
    assert client.get("/api/v1/settings", headers=ha).json()["llm"]["values"]["moondream_model"] == "moondream3-preview"
    assert client.put("/api/v1/settings/llm", json={"moondream_model": "moondream9"}, headers=ha).status_code == 400
    got = tool(client, ha, "picture_descriptions")
    assert set(got["engines"]) == {"llm", "florence-2", "moondream"} and "BSL 1.1" in got["engines"]["moondream"]
    out = tool(client, ha, "set_picture_descriptions", engine="moondream", moondream_model="moondream2")
    assert out["saved"] == {"describe_engine": "moondream", "moondream_model": "moondream2"}
    assert (out["engine"], out["model"], out["moondream_model"], out["ready"]) == ("moondream", "Moondream-2", "moondream2", False)
    assert "fetched the first time a job waits" in out["why_not"]
    assert tool_error(client, ha, "set_picture_descriptions", moondream_model="moondream9")
    audit = db.rows("SELECT target, detail FROM audit_log WHERE action = 'settings.save'")
    assert {"target": "llm", "detail": ["describe_engine", "moondream_model", "mcp"]} in audit


def test_pytorch_never_asks_for_more_threads_than_openmp_gives(cfg, monkeypatch, server):
    """With OMP_THREAD_LIMIT below PyTorch's threads, its CPU kernels write nonsense ("and, (, (, (…"): seen with the
    real Moondream 2 under this suite's OMP_THREAD_LIMIT=1."""
    calls = []
    torch = types.ModuleType("torch")
    torch.float32, torch.bfloat16 = "float32", "bfloat16"
    torch.get_num_threads = lambda: 4
    torch.set_num_threads = calls.append
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch.backends = types.SimpleNamespace()

    class Loaded:
        def to(self, where):
            calls.append(where)
            return self

        def eval(self):
            pass

    transformers = types.ModuleType("transformers")
    transformers.AutoModelForCausalLM = types.SimpleNamespace(
        from_pretrained=lambda repo, **kw: calls.append((repo, kw["revision"], kw["dtype"], kw["trust_remote_code"])) or Loaded()
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setattr(moondream, "_loaded", {})
    monkeypatch.setenv("OMP_THREAD_LIMIT", "1")
    got = moondream._load(cfg, "moondream2")
    assert calls == [1, ("vikhyatk/moondream2", moondream.MODELS["moondream2"]["revision"], "float32", True), "cpu"]
    assert moondream._load(cfg, "moondream2") is got and len(calls) == 3  # loaded once a process
    monkeypatch.setattr(moondream, "_loaded", {})
    monkeypatch.delenv("OMP_THREAD_LIMIT")
    calls.clear()
    moondream._load(cfg, "moondream2")
    assert 1 not in calls


@pytest.mark.skipif(not os.environ.get("LENS_TEST_MOONDREAM"), reason="LENS_TEST_MOONDREAM: fetch and run the real Moondream 2")
def test_the_real_model(cfg, folder):
    from PIL import ImageDraw

    cfg["llm"].update(describe_engine="moondream", moondream_model="moondream2")
    c = components.BY_ID["moondream"]
    m = machine.probe(cfg["data_dir"])
    c.fetch(cfg, m, print)
    assert c.present(cfg)
    img = Image.new("RGB", (640, 480), "white")
    ImageDraw.Draw(img).ellipse((220, 140, 420, 340), fill=(220, 20, 20))
    img.save(folder / "dot.png")
    eye, why = descriptions.engine(cfg)
    assert why is None and eye.name == "Moondream-2"
    assert "red" in eye.describe(folder / "dot.png").lower()
