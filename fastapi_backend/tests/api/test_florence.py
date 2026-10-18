"""Florence-2 describing pictures on this machine (docs/configuration.md#descriptions): with llm.describe_engine
"florence-2" the describe step needs no LLM provider; the picture is read the way the model was trained (768 x 768,
ImageNet's mean and spread); the decoder starts from EOS with BOS forced, keeps the encoder's attention cache from its
first step, never repeats three tokens and stops at EOS; each picture is a ledger row; the model is fetched as a
component, pinned and hashed; and admins and agents choose it."""

from __future__ import annotations

import os
import shutil

import numpy as np
import pytest
from PIL import Image
from tokenizers import Tokenizer, models, pre_tokenizers, processors

from app.domain import components, descriptions, florence, machine, store
from tests.api.test_documents import _png, _upload
from tests.api.test_mcp import tool, tool_error
from tests.helpers import drain, login, make_user

R = store.R
WORDS = ["<s>", "<pad>", "</s>", "<unk>", "A", "red", "blue", "square", "on", "white", "Describe", "with", "a"]
WORDS += ["paragraph", "what", "is", "shown", "in", "the", "image."]
ID = {w: i for i, w in enumerate(WORDS)}
DIM, HEADS, HEAD = 4, 2, 3


def tokenizer():
    """A word-level tokenizer that adds <s> and </s> the way Florence-2's (BART's) does."""
    tok = Tokenizer(models.WordLevel(ID, unk_token="<unk>"))
    tok.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tok.add_special_tokens(["<s>", "<pad>", "</s>", "<unk>"])
    tok.post_processor = processors.TemplateProcessing(single="<s> $A </s>", special_tokens=[("<s>", 0), ("</s>", 2)])
    return tok


class Port:
    """An ONNX Runtime input or output: its name and shape."""

    def __init__(self, name, shape=None):
        self.name, self.shape = name, shape


class Vision:
    """577 features that say whether the picture is redder than it is blue."""

    def run(self, _, feed):
        x = feed["pixel_values"]
        assert x.shape == (1, 3, 768, 768) and x.dtype == np.float32
        return [np.full((1, 577, DIM), float(x[0, 0].mean() > x[0, 2].mean()), dtype=np.float32)]


class Embed:
    def run(self, _, feed):
        return [np.repeat(feed["input_ids"][..., None], DIM, axis=2).astype(np.float32)]


class Encoder:
    seen: list = []

    def run(self, _, feed):
        x = feed["inputs_embeds"]
        assert feed["attention_mask"].shape == x.shape[:2]
        Encoder.seen.append(x[0, 577:, 0].astype(int).tolist())  # the prompt's tokens, after the picture's features
        return [x]


class Decoder:
    """Writes `red` after a red picture, else `other`, a token a step (it counts the tokens in its cache), each
    followed by `then` as the next likeliest; past the end, EOS. Its cached branch hands back no encoder cache, as the
    merged ONNX decoder's does, so it must be kept from the first step."""

    def __init__(self, red, other, then=None):
        self.red, self.other, self.then, self.steps = red, other, then, 0

    def get_inputs(self):
        cache = [
            Port(f"past_key_values.0.{p}.{kv}", ["batch_size", HEADS, "past", HEAD])
            for p in ("decoder", "encoder")
            for kv in ("key", "value")
        ]
        return [Port("encoder_attention_mask"), Port("encoder_hidden_states"), Port("inputs_embeds"), *cache, Port("use_cache_branch")]

    def get_outputs(self):
        return [Port("logits"), *(Port(f"present.0.{p}.{kv}") for p in ("decoder", "encoder") for kv in ("key", "value"))]

    def run(self, _, feed):
        self.steps += 1
        seen = feed["past_key_values.0.decoder.key"].shape[2]
        enc = feed["encoder_hidden_states"]
        if feed["use_cache_branch"][0]:
            assert feed["past_key_values.0.encoder.key"].shape == (1, HEADS, enc.shape[1], HEAD)  # kept from the first step
        else:
            assert seen == 0
        script = self.red if enc[0, 0, 0] == 1.0 else self.other
        logits = np.zeros((1, 1, len(WORDS)), dtype=np.float32)
        if 0 < seen <= len(script):
            logits[0, 0, ID[script[seen - 1]]] = 2.0
            if self.then:
                logits[0, 0, ID[self.then]] = 1.0
        else:
            logits[0, 0, ID["</s>"]] = 2.0
        grown = np.zeros((1, HEADS, seen + 1, HEAD), dtype=np.float32)
        kept = (
            np.zeros((1, HEADS, enc.shape[1], HEAD), dtype=np.float32) if not feed["use_cache_branch"][0] else np.zeros((0, HEADS, 0, HEAD))
        )
        return [logits, grown, grown, kept, kept]


def sessions(decoder):
    return {"vision_encoder": Vision(), "embed_tokens": Embed(), "encoder_model": Encoder(), "decoder_model_merged": decoder}


def test_the_decoder_starts_with_bos_keeps_its_cache_and_never_repeats_three_tokens():
    tok = tokenizer()
    prompt = tok.encode(florence.PROMPT).ids
    assert prompt[0] == 0 and prompt[-1] == 2  # <s> … </s>, as BART's tokenizer adds them
    red = np.full((1, 3, 768, 768), 0.0, dtype=np.float32)
    red[0, 0] = 1.0
    ids, read = florence.generate(sessions(Decoder(["A", "red", "square"], ["A", "blue", "square"])), prompt, red)
    assert tok.decode(ids, skip_special_tokens=True) == "A red square" and ids[0] == 0 and ids[-1] == 2
    assert read == 577 + len(prompt) and Encoder.seen[-1] == prompt

    # it wants "red square on red square on …": the second "red square on" is never written, the next likeliest is
    dec = Decoder(["red", "square", "on", "red", "square", "on"], [], then="white")
    ids, _ = florence.generate(sessions(dec), prompt, red)
    assert tok.decode(ids, skip_special_tokens=True) == "red square on red square white"
    # and it stops at the most it may write
    ids, _ = florence.generate(sessions(Decoder(["A"] * 10, [], then="red")), prompt, red, most=4)
    assert len(ids) == 4


def test_a_picture_is_read_as_the_model_was_trained(folder):
    path = folder / "blue.png"
    Image.new("RGB", (40, 20), (0, 0, 255)).save(path)
    x = florence.pixels(path)
    assert x.shape == (1, 3, 768, 768) and x.dtype == np.float32
    assert np.allclose(x[0, :, 5, 5], [(0 - 0.485) / 0.229, (0 - 0.456) / 0.224, (1 - 0.406) / 0.225], atol=1e-5)


@pytest.fixture
def model(cfg, monkeypatch):
    """Florence-2 base "fetched": its files in the models folder, ONNX Runtime handing back the fakes for them."""
    import onnxruntime

    for n in florence.files("base"):
        p = components.models_dir(cfg) / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"onnx")
    tokenizer().save(str(components.models_dir(cfg) / florence.files("base")[-1]))
    fakes = sessions(Decoder(["A", "red", "square", "on", "white"], ["A", "blue", "square"]))
    opened = []
    monkeypatch.setattr(
        onnxruntime, "InferenceSession", lambda path, providers: opened.append(path) or fakes[next(p for p in fakes if p in path)]
    )
    monkeypatch.setattr(florence, "_loaded", {})
    cfg["llm"].update(base_url=None, model=None, vision_model=None, describe_engine="florence-2")
    return opened


@pytest.fixture
def people(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {"ha": login(client, "root@x.io", "root password 1"), "he": login(client, "ed@x.io", "editor password 1")}


def test_florence_describes_pictures_without_a_model_provider(client, people, db, cfg, model):
    red = _upload(client, people["he"], _png(Image.new("RGB", (64, 48), (220, 20, 20))), "red.png")["recording"]
    blue = _upload(client, people["he"], _png(Image.new("RGB", (64, 48), (20, 20, 220))), "blue.png")["recording"]
    drain(db, cfg)
    assert len(model) == 4  # the four ONNX models, opened once for both pictures
    got = {rid: descriptions.for_recording(db, rid) for rid in (red, blue)}
    assert [(d["text"], d["model"], d["paged"]) for d in got[red]] == [("A red square on white", "Florence-2-base", True)]
    assert got[blue][0]["text"] == "A blue square"
    log = " ".join(db.one("SELECT log FROM job WHERE recording = $r", r=red)["log"])
    assert "1 page(s) described (Florence-2-base)" in log
    # each picture is a row in the activity ledger, for its resource, with what it read and wrote
    rows = db.rows("SELECT model, tokens_in, tokens_out, resources, detail FROM activity WHERE action = 'model.describe'")
    assert len(rows) == 2 and {r["model"] for r in rows} == {"Florence-2-base"}
    mine = next(r for r in rows if f"recording:{red}" in r["resources"])
    prompt = tokenizer().encode(florence.PROMPT).ids
    assert (mine["tokens_in"], mine["tokens_out"], mine["detail"]) == (577 + len(prompt), 7, {"engine": "florence-2"})


def test_without_its_model_the_step_says_so(client, people, db, cfg):
    cfg["llm"]["describe_engine"] = "florence-2"
    rid = _upload(client, people["he"], _png(Image.new("RGB", (64, 48), "white")), "white.png")["recording"]
    drain(db, cfg)
    log = " ".join(db.one("SELECT log FROM job WHERE recording = $r", r=rid)["log"])
    assert "describe skipped: the Florence-2-base model isn't here yet (it's fetched the first time a job needs it)" in log


def test_florence_is_fetched_pinned_and_checked(cfg, monkeypatch):
    c = components.BY_ID["florence"]
    m = machine.probe(cfg["data_dir"])
    assert not c.needed(cfg, m) and c.steps == {"describe"} and c.license == "MIT"
    cfg["llm"]["describe_engine"] = "florence-2"
    cfg["llm"]["florence_model"] = "large"
    assert c.needed(cfg, m) and not c.present(cfg)
    got = []
    monkeypatch.setattr(components, "importable", lambda *mods: False)
    monkeypatch.setattr(components, "pip_install", lambda cfg, extra, say=None: got.append(extra))
    monkeypatch.setattr(components, "fetch_file", lambda cfg, name, say=None: got.append(name))
    c.fetch(cfg, m, None)
    assert got == ["florence", *florence.files("large")]
    url, sha, mb = components.FILES["florence-2-large-ft/vision_encoder_int8.onnx"]
    assert url == (
        "https://huggingface.co/onnx-community/Florence-2-large-ft/resolve/04ace74913c28d7ec94af32cee5c111a10126b6f/onnx/vision_encoder_int8.onnx"
    )
    assert (sha, mb) == ("51d21e33e3b8cd34a9882e0611a2e4e03fb2b26daecea8accd1d4769ea11ba72", 367)
    assert all(n in components.FILES for s in florence.SIZES for n in florence.files(s))


def test_admins_and_agents_choose_what_describes_pictures(client, people, db):
    ha = people["ha"]
    assert client.get("/api/v1/settings", headers=ha).json()["llm"]["values"]["describe_engine"] == "llm"  # the default
    r = client.put("/api/v1/settings/llm", json={"describe_engine": "moondream-9"}, headers=ha)
    assert r.status_code == 400
    assert client.put("/api/v1/settings/llm", json={"florence_model": "huge"}, headers=ha).status_code == 400
    assert client.put("/api/v1/settings/llm", json={"describe_engine": "florence-2"}, headers=people["he"]).status_code == 403

    # agents, over MCP: admins only
    assert "only admins" in tool_error(client, people["he"], "picture_descriptions")
    got = tool(client, ha, "picture_descriptions")
    assert (got["engine"], got["ready"], got["why_not"]) == ("llm", False, "no LLM is configured")
    assert {"llm", "florence-2"} <= set(got["engines"]) and got["settings"].endswith("/settings/llm")
    assert "say what to change" in tool_error(client, ha, "set_picture_descriptions")
    assert tool_error(client, ha, "set_picture_descriptions", describe_max=0) == "llm.describe_max is a whole number from 1 to 1000"
    out = tool(client, ha, "set_picture_descriptions", engine="florence-2", florence_model="large")
    assert out["saved"] == {"describe_engine": "florence-2", "florence_model": "large"}
    assert (out["engine"], out["model"], out["ready"]) == ("florence-2", "Florence-2-large", False)
    assert "fetched the first time a job waits" in out["why_not"]
    assert client.get("/api/v1/settings", headers=ha).json()["llm"]["values"]["florence_model"] == "large"
    audit = db.rows("SELECT target, detail FROM audit_log WHERE action = 'settings.save'")
    assert {"target": "llm", "detail": ["describe_engine", "florence_model", "mcp"]} in audit
    assert "only admins" in tool_error(client, people["he"], "set_picture_descriptions", engine="llm")


@pytest.mark.skipif(not os.environ.get("LENS_TEST_FLORENCE_DIR"), reason="LENS_TEST_FLORENCE_DIR: the real base model's files")
def test_the_real_model(cfg, folder, monkeypatch):
    from PIL import ImageDraw

    for n in florence.files("base"):
        (components.models_dir(cfg) / n).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(os.path.join(os.environ["LENS_TEST_FLORENCE_DIR"], n.rsplit("/", 1)[-1]), components.models_dir(cfg) / n)
    monkeypatch.setattr(florence, "_loaded", {})
    img = Image.new("RGB", (640, 480), "white")
    ImageDraw.Draw(img).ellipse((220, 140, 420, 340), fill=(220, 20, 20))
    img.save(folder / "dot.png")
    cfg["llm"]["describe_engine"] = "florence-2"
    eye, why = descriptions.engine(cfg)
    assert why is None and eye.name == "Florence-2-base"
    assert "red" in eye.describe(folder / "dot.png", "The image “dot”.").lower()
