"""Kokoro reading aloud on this machine (kokoro.py): espeak-ng turns the text into phonemes and a tiny model shaped like
Kokoro v1.0 (its token, style and speed inputs, a waveform out) reads them, so the tests run kokoro-onnx itself without
fetching the real model. The real one is read with on x64 and arm64 by .github/workflows/kokoro.yml."""

from __future__ import annotations

import io
import re
import wave

import numpy as np
import pytest

from app.domain import activity, components, jobs, kokoro, machine, podcasts, settings, speech, store, voice
from tests import fake_llm
from tests.helpers import drain, make_user, seed

pytest.importorskip("kokoro_onnx")
onnx = pytest.importorskip("onnx")

R = store.R
SAMPLES_PER_TOKEN = 240  # 10 ms of audio for each phoneme


def tiny_model(path):
    """A model with Kokoro v1.0's inputs: its waveform is the phonemes' token ids, 10 ms each, times the speed."""
    from onnx import TensorProto, helper

    graph = helper.make_graph(
        [
            helper.make_node("Cast", ["input_ids"], ["ids"], to=TensorProto.FLOAT),
            helper.make_node("Mul", ["ids", "scale"], ["level"]),
            helper.make_node("ReduceSum", ["style"], ["styled"], keepdims=0),
            helper.make_node("Mul", ["styled", "zero"], ["nothing"]),
            helper.make_node("Add", ["level", "nothing"], ["heard"]),
            helper.make_node("Tile", ["heard", "repeats"], ["long"]),
            helper.make_node("Mul", ["long", "speed"], ["fast"]),
            helper.make_node("Reshape", ["fast", "flat"], ["waveform"]),
        ],
        "tiny-kokoro",
        [
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, [1, None]),
            helper.make_tensor_value_info("style", TensorProto.FLOAT, [1, 256]),
            helper.make_tensor_value_info("speed", TensorProto.FLOAT, [1]),
        ],
        [helper.make_tensor_value_info("waveform", TensorProto.FLOAT, [None])],
        [
            helper.make_tensor("scale", TensorProto.FLOAT, [], [0.004]),
            helper.make_tensor("zero", TensorProto.FLOAT, [], [0.0]),
            helper.make_tensor("repeats", TensorProto.INT64, [2], [1, SAMPLES_PER_TOKEN]),
            helper.make_tensor("flat", TensorProto.INT64, [1], [-1]),
        ],
    )
    m = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.save(m, str(path))


@pytest.fixture
def here(cfg):
    """Kokoro fetched: the tiny model at each precision, and a voices file with a few voices."""
    d = components.models_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    for f in kokoro.MODELS.values():
        tiny_model(d / f)
    rng = np.random.default_rng(7)
    with open(d / kokoro.VOICES_FILE, "wb") as f:  # an .npz, as voices-v1.0.bin is
        np.savez(f, **{v: rng.random((510, 1, 256), dtype=np.float32) for v in ("af_heart", "am_michael", "bf_emma", "ef_dora")})
    kokoro._loaded.clear()
    yield cfg
    kokoro._loaded.clear()


def picked(db, cfg, **more):
    settings.save(db, cfg, "voice", {"tts_provider": "kokoro", **more}, "t")
    return settings.effective(db, cfg)


def test_kokoro_reads_answers_aloud_on_this_machine(db, here):
    eff = picked(db, here)
    assert voice.can_speak(eff) and speech.tts_voice(eff) == "af_heart" and eff["voice"]["kokoro_model"] == "fp32"
    with activity.scope(db, "chat:1", cfg=eff):
        data, ctype = voice.speak(eff, "The shipment leaves on Friday.")
    assert ctype == "audio/wav" and data[:4] == b"RIFF"
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24000, 1, 2)
        assert w.getnframes() > 10 * SAMPLES_PER_TOKEN  # the phonemes espeak-ng found, read
    # in the activity ledger, as the model it was read with
    rows = [r for r in db.rows("SELECT * FROM activity") if r["action"] == "model.speech"]
    assert [(r["model"], r["resources"]) for r in rows] == [("kokoro:kokoro-v1.0", ["chat:1"])]
    # another voice; each reads in its own language
    assert voice.speak(eff, "Good morning.", voice="bf_emma")[0][:4] == b"RIFF"
    assert (kokoro.lang("bf_emma"), kokoro.lang("ef_dora"), kokoro.lang("zf_xiaobei"), kokoro.lang("nope")) == (
        "en-gb",
        "es",
        "cmn",
        "en-us",
    )
    # a voice it doesn't have: the browser reads it
    with pytest.raises(speech.ProviderError, match="Kokoro couldn't read it: Voice am_nobody not found"):
        speech.speak(eff, "Hello", voice="am_nobody")
    assert voice.speak(eff, "Hello", voice="am_nobody") is None


def test_the_model_size_is_a_setting(db, here):
    eff = picked(db, here, kokoro_model="int8")
    assert kokoro.files(eff) == ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"] and kokoro.name(eff) == "kokoro-v1.0-int8"
    with activity.scope(db, "chat:1", cfg=eff):
        voice.speak(eff, "Hello")
    assert kokoro._loaded["model"].endswith("kokoro-v1.0.int8.onnx")
    rows = [r for r in db.rows("SELECT * FROM activity") if r["action"] == "model.speech"]
    assert [r["model"] for r in rows] == ["kokoro:kokoro-v1.0-int8"]
    voice.speak(picked(db, here, kokoro_model="fp16"), "Hello")  # the other one is let go
    assert kokoro._loaded["model"].endswith("kokoro-v1.0.fp16.onnx")
    with pytest.raises(ValueError):
        settings.save(db, here, "voice", {"kokoro_model": "fp64"}, "t")


def test_the_model_is_let_go_when_nothing_is_read(db, here, monkeypatch):
    eff = picked(db, here)
    voice.speak(eff, "Hello")
    assert kokoro._loaded.get("kokoro") is not None
    kokoro.let_go()  # just used: kept, and looked at again later
    assert kokoro._loaded.get("kokoro") is not None and kokoro._loaded["timer"].is_alive()
    kokoro._loaded["timer"].cancel()
    monkeypatch.setattr(kokoro, "IDLE_SECONDS", -1)
    kokoro.let_go()
    assert kokoro._loaded == {}


def test_every_voice_is_listed_before_it_is_fetched(db, cfg):
    eff = picked(db, cfg)
    assert not speech.tts_ready(eff) and voice.speak(eff, "Hello") is None  # not fetched yet: the browser reads it
    found, note = speech.voices(eff)
    assert len(found) == 54 and note.startswith("Kokoro is fetched once")
    assert found[0] == {"id": "af_alloy", "name": "Alloy", "detail": "American English, female"}
    assert {"id": "bm_george", "name": "George", "detail": "British English, male"} in found
    with pytest.raises(speech.ProviderError, match="Kokoro isn't here yet"):
        speech.speak(eff, "Hello")


def test_kokoro_is_fetched_once_it_is_picked(db, cfg, monkeypatch):
    c = components.BY_ID["kokoro"]
    m = machine.probe(cfg["data_dir"])
    assert not c.needed(settings.effective(db, cfg), m)  # nothing picked it: nothing fetched
    eff = picked(db, cfg, kokoro_model="int8")
    assert c.needed(eff, m) and not c.present(eff)
    # fetched straight away, not when a job first needs it (spoken answers aren't jobs); podcasts wait for it
    keeper = components.Keeper(db, lambda: eff, "w", {"podcast"})
    assert keeper._first_use(eff, c, m, set()) == set() and c.steps == {"podcast"}
    got, extras = [], []
    monkeypatch.setattr(components, "fetch_file", lambda cfg_, n, say=None: got.append(n))
    monkeypatch.setattr(components, "pip_install", lambda cfg_, extra, say=None: extras.append(extra))
    monkeypatch.setattr(components, "importable", lambda *mods: False)
    c.fetch(eff, m, None)
    assert extras == ["kokoro"] and got == ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"]
    # every file is pinned to kokoro-onnx's release and its SHA-256
    for n in (*kokoro.MODELS.values(), kokoro.VOICES_FILE):
        url, sha, mb = components.FILES[n]
        assert url == f"https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/{n}"
        assert re.fullmatch(r"[0-9a-f]{64}", sha) and mb > 0
    row = next(r for r in components.catalog(eff, db) if r["id"] == "kokoro")
    assert row["needed"] and "GPL-3.0" in row["license"] and "Apache-2.0" in row["license"]


def test_podcast_hosts_get_two_voices_in_one_language(cfg):
    cfg["voice"]["tts_provider"] = "kokoro"
    assert podcasts.voices(cfg) == {"a": "af_heart", "b": "am_michael"}
    cfg["podcasts"]["voice_a"] = "ef_dora"
    assert podcasts.voices(cfg) == {"a": "ef_dora", "b": "em_alex"}  # Spanish hosts
    assert podcasts.voices(cfg, {"voices": {"a": "bm_george"}}) == {"a": "bm_george", "b": "bf_emma"}
    cfg["podcasts"]["voice_b"] = "jf_alpha"
    assert podcasts.voices(cfg)["b"] == "jf_alpha"  # named: kept
    assert kokoro.partner("ff_siwis") == "ff_siwis"  # the only French voice
    assert kokoro.partner("hf_beta") == "hm_omega" and kokoro.partner("nope") == "am_michael"


@pytest.fixture
def llm(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    fake_llm.Handler.podcast_script = None
    yield fake_llm.Handler
    srv.shutdown()


def test_a_podcast_is_read_by_kokoro(db, here, folder, llm):
    from app.domain import auth

    cfg = here
    cfg["voice"].update(tts_provider="kokoro")
    a, _b, _c = seed(db, cfg, folder)
    uid = make_user(db, "admin@x.io", "admin password 1", admin=True)
    out = podcasts.create(db, cfg, {"recordings": [a]}, roles=auth.roles(db, {"id": uid, "admin": True}), admin=True)
    drain(db, cfg)
    assert jobs.get(db, out["job"])["status"] == "succeeded"
    ep = podcasts.view(db, out["episode"])
    assert ep["status"] == "ready" and ep["voicing"]["voices"] == {"a": "af_heart", "b": "am_michael"}
    rec = db.one("SELECT source, duration_ms FROM $r", r=R("recording", out["episode"]))
    assert rec["source"] == "audio" and rec["duration_ms"] > 0
    # not fetched: the episode stays a script
    for f in components.models_dir(cfg).iterdir():
        f.unlink()
    podcasts.regenerate(db, out["episode"])
    drain(db, cfg)
    assert podcasts.view(db, out["episode"])["status"] == "script_only"
