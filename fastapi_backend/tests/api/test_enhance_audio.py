"""Enhancing low quality sound before it's transcribed (docs/enhance.md): off by default; turned on, the enhance step
listens to a recording and, when it's noisy, quiet or clipped, makes a cleaned-up copy (FLAC, 16 kHz mono) that the
transcribe step hears instead, keeping the original and its times: every sample of the copy stays where it was, so
words land where they were said. Asked for, it enhances whatever the settings. WebRTC's processing, where it's chosen
and installed, runs 10 ms at a time and is put back in step the same way."""

from __future__ import annotations

import io
import subprocess
import sys
import types
import wave

import numpy as np
import pytest

from app.api import mcp_tools
from app.domain import components, enhance, enhance_audio, ingest, keyring, machine, settings, store
from tests.api.test_documents import _upload
from tests.api.test_mcp import tool
from tests.helpers import drain, login, make_user

R = store.R
SR = enhance_audio.SR


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


class Heard:
    """A transcription engine that keeps what it was given."""

    name = "sensevoice"

    def __init__(self):
        self.got = []

    def transcribe(self, audio):
        self.got.append(np.array(audio))
        return [{"t0": 0, "t1": 1000, "text": "hello there", "lang": "en"}]


@pytest.fixture
def heard(monkeypatch):
    e = Heard()
    monkeypatch.setattr(ingest, "get_engine", lambda cfg, log=None: e)
    return e


def on(cfg, **more):
    return {**cfg, "enhance": {**cfg["enhance"], "audio": True, **more}, "diarize": {**cfg["diarize"], "engine": "none"}}


def _speech(seconds, level_db=-20.0, seed=1):
    """Something like speech: a voice's harmonics in syllables four times a second, with pauses."""
    t = np.arange(int(seconds * SR)) / SR
    phase = 2 * np.pi * np.cumsum(120 + 20 * np.sin(2 * np.pi * 0.5 * t)) / SR
    x = sum(np.sin(k * phase) / k for k in range(1, 25))
    env = np.clip(np.sin(2 * np.pi * 4 * t), 0, 1) ** 2 * (np.sin(2 * np.pi * 0.25 * t) > -0.3)
    x = x * env
    return (x / np.sqrt(np.mean(x[env > 0.1] ** 2)) * 10 ** (level_db / 20)).astype(np.float32)


def _noise(n, level_db, seed=2):
    return (np.random.default_rng(seed).normal(0, 1, n) * 10 ** (level_db / 20)).astype(np.float32)


def _wav(x, rate=SR):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return out.getvalue()


def _clicked(x, *at):
    x = x.copy()
    for i, t in enumerate(at):
        x[int(t * SR)] = 0.9 if i % 2 == 0 else -0.9
    return x


def _log(db, rid):
    return " ".join(x for j in db.rows("SELECT log FROM job WHERE recording = $r", r=rid) for x in j["log"])


def _decode(data):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "-", "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"], input=data, capture_output=True, check=True
    )
    return np.frombuffer(out.stdout, np.float32)


def _record(client, h, x, name="call.wav"):
    return _upload(client, h, _wav(x), name)["recording"]


def test_off_by_default_the_original_is_heard(client, env, db, cfg, heard):
    noisy = _speech(6) + _noise(6 * SR, -24)
    rid = _record(client, env["he"], noisy)
    drain(db, {**cfg, "diarize": {**cfg["diarize"], "engine": "none"}})
    assert "enhance skipped: enhancing sound is off (Settings → Enhance)" in _log(db, rid)
    assert len(heard.got) == 1 and not enhance.files(cfg, rid)
    assert client.get(f"/api/v1/recordings/{rid}", headers=env["hv"]).json()["enhanced"] is None
    assert not components.BY_ID["enhance-sound"].wanted_by(db, cfg, machine.probe(cfg["data_dir"]))


def test_noisy_sound_is_transcribed_from_a_copy_in_step_with_it(client, env, db, cfg, heard):
    x = _clicked(_speech(12) + _noise(12 * SR, -30), 3.3, 9.1)
    rid = _record(client, env["he"], x)
    drain(db, on(cfg))
    log = _log(db, rid)
    assert "enhanced its sound (noisy; signal to noise" in log and "afftdn=nr=12" in log
    assert "transcribed from its enhanced sound" in log
    e = db.one("SELECT enhanced FROM $r", r=R("recording", rid))["enhanced"]
    assert (e["kind"], e["low"], e["engine"], e["enhanced"]) == ("audio", ["noisy"], "ffmpeg", True)
    assert e["measured"]["snr_db"] < 15 and e["measured"]["duration"] == 12.0
    assert e["filters"][0] == "highpass=f=70" and e["filters"][1].startswith("afftdn")

    # what was transcribed is the copy: as long as the original, less noisy, and its clicks where they were
    got = heard.got[-1]
    assert len(got) == len(x)
    before, after = enhance_audio.summarize(*_levels(x)), enhance_audio.summarize(*_levels(got))
    assert after["floor_db"] < before["floor_db"] - 6 and after["snr_db"] > before["snr_db"] + 6
    assert enhance_audio.lag(x, got) == 0

    # the waveform is still the original's, and the original is kept as it was
    env_ = db.one("SELECT envelope FROM $r", r=R("recording", rid))["envelope"]
    assert env_ == ingest.envelope(ingest.decode(ingest.audio_path(db, cfg, db.one("SELECT * FROM $r", r=R("recording", rid)))))

    api = client.get(f"/api/v1/recordings/{rid}", headers=env["hv"]).json()["enhanced"]
    assert api["kind"] == "audio" and api["low"] == ["noisy"] and api["filters"] == e["filters"]
    r = client.get(api["download"])
    assert r.status_code == 200 and r.headers["content-type"] == "audio/flac"
    assert 'filename="call-enhanced.flac"' in r.headers["content-disposition"]
    assert len(_decode(r.content)) == len(x)
    assert client.get(f"/api/v1/recordings/{rid}/enhanced", headers=env["hv"]).status_code == 200


def _levels(x):
    k = len(x) // enhance_audio.FRAME
    f = x[: k * enhance_audio.FRAME].reshape(k, -1).astype(np.float64)
    return np.sqrt(np.mean(f * f, axis=1)), len(x), int(np.count_nonzero(np.abs(x) >= 0.999)), float(np.abs(x).max())


def test_quiet_and_clipped_sound(client, env, db, cfg, heard):
    quiet = _speech(8, level_db=-48) + _noise(8 * SR, -90)
    rid = _record(client, env["he"], quiet)
    drain(db, on(cfg))
    e = db.one("SELECT enhanced FROM $r", r=R("recording", rid))["enhanced"]
    assert e["low"] == ["quiet"] and e["filters"][1].startswith("volume=") and e["filters"][2].startswith("dynaudnorm")
    assert enhance_audio.summarize(*_levels(heard.got[-1]))["level_db"] > e["measured"]["level_db"] + 10

    clipped = np.clip(_speech(8, level_db=-6) * 3, -1, 1) + _noise(8 * SR, -60)
    rid = _record(client, env["he"], clipped, "loud.wav")
    drain(db, on(cfg))
    e = db.one("SELECT enhanced FROM $r", r=R("recording", rid))["enhanced"]
    assert "clipped" in e["low"] and e["filters"][0] == "adeclip" and e["measured"]["clipped"] > enhance_audio.CLIPPED


def test_good_or_silent_sound_is_left_as_it_is(client, env, db, cfg, heard):
    rid = _record(client, env["he"], _speech(6) + _noise(6 * SR, -70))
    drain(db, on(cfg))
    assert "good enough as it is (signal to noise" in _log(db, rid)
    e = db.one("SELECT enhanced FROM $r", r=R("recording", rid))["enhanced"]
    assert e["enhanced"] is False and e["low"] == [] and not enhance.files(cfg, rid)
    assert "transcribed from its enhanced sound" not in _log(db, rid)

    rid = _record(client, env["he"], np.zeros(3 * SR, np.float32), "hush.wav")
    drain(db, on(cfg))
    assert "it's silent" in _log(db, rid)


def test_asked_for_it_is_enhanced_and_transcribed_again(client, env, db, cfg, heard, app):
    he, hv = env["he"], env["hv"]
    rid = _record(client, he, _speech(6) + _noise(6 * SR, -70))
    drain(db, {**cfg, "diarize": {**cfg["diarize"], "engine": "none"}})
    assert client.post(f"/api/v1/recordings/{rid}/enhance", headers=hv).status_code == 403
    job = client.post(f"/api/v1/recordings/{rid}/enhance", headers=he).json()["job"]
    steps = [s["type"] for s in db.one("SELECT steps FROM $r", r=R("job", job))["steps"]]
    assert steps[:2] == ["enhance", "transcribe"] and "enhance" not in steps[1:]
    drain(db, {**cfg, "diarize": {**cfg["diarize"], "engine": "none"}})
    e = db.one("SELECT enhanced FROM $r", r=R("recording", rid))["enhanced"]
    assert e["forced"] is True and e["enhanced"] is True and e["low"] == []
    assert "enhanced its sound (on request" in _log(db, rid) and "transcribed from its enhanced sound" in _log(db, rid)
    # on request with nothing wrong: a gentle clean-up of everything
    assert [f.split("=")[0] for f in e["filters"]] == ["highpass", "afftdn", "dynaudnorm"] and "nr=6" in e["filters"][1]

    # agents too
    assert "audio or video" in mcp_tools.TOOLS["enhance"].description
    got = tool(client, he, "enhance", recording_id=rid)
    assert got["job"]
    # a transcript has no sound to enhance
    t = client.post("/api/v1/import", headers=he, json={"namespace": "pods", "text": "[00:01] Ann: Hello."}).json()
    r = client.post(f"/api/v1/recordings/{t['id']}/enhance", headers=he)
    assert r.status_code == 400 and "only images, audio and video" in r.json()["detail"]


def test_the_copy_is_encrypted_where_the_files_are(client, env, db, cfg, heard):
    eff = settings.effective(db, cfg)
    x = _speech(5) + _noise(5 * SR, -28)
    rid = _record(client, env["he"], x)
    drain(db, on(eff))
    path = enhance.enhanced_path(cfg, rid, "audio")
    assert keyring.is_encrypted(path) and str(path) in list(keyring.recording_files(db, cfg, rid))
    r = client.get(f"/api/v1/recordings/{rid}/enhanced", headers=env["he"])
    assert r.status_code == 200 and len(_decode(r.content)) == len(x)
    assert client.delete(f"/api/v1/recordings/{rid}", headers=env["ha"]).status_code == 200
    assert not path.exists()


def test_measuring_and_deciding():
    opts = {"min_snr_db": 15, "min_level_db": -35}
    m = enhance_audio.summarize(*_levels(_speech(4) + _noise(4 * SR, -26)))
    assert enhance_audio.assess(m, opts) == ["noisy"] and 3 < m["snr_db"] < 15
    m = enhance_audio.summarize(*_levels(_speech(4) + _noise(4 * SR, -70)))
    assert enhance_audio.assess(m, opts) == [] and m["snr_db"] > 30 and -24 < m["level_db"] < -16
    assert enhance_audio.summarize(np.empty(0), 0, 0, 0.0)["level_db"] == -100.0
    assert enhance_audio.filters(["clipped", "noisy", "quiet"], {"floor_db": -95.0, "level_db": -50.0, "peak_db": -12.0}) == [
        "adeclip",
        "highpass=f=70",
        "afftdn=nr=12:nf=-80",
        "volume=11.0dB",  # as far as its peak allows
        "dynaudnorm=f=250:g=31:m=10:p=0.9",
    ]
    x = _noise(SR, -20)
    assert enhance_audio.lag(x, np.concatenate([np.zeros(37, np.float32), x[:-37]])) == 37


class FakeWebrtc:
    """webrtc_noise_gain's AudioProcessor, as far as Lens uses it: 10 ms in, 10 ms out, a frame late (as WebRTC's own
    noise suppression is), at half the level; speech where it's loud."""

    made: list = []

    def __init__(self, gain, ns):
        FakeWebrtc.made.append((gain, ns))
        self.held = bytes(320)

    def Process10ms(self, chunk):
        assert len(chunk) == 320
        out, self.held = self.held, chunk
        y = (np.frombuffer(out, np.int16) // 2).astype(np.int16).tobytes()
        return types.SimpleNamespace(audio=y, is_speech=int(np.abs(np.frombuffer(chunk, np.int16)).max() > 2000))


def test_webrtc_where_its_chosen(client, env, db, cfg, heard, monkeypatch):
    m = machine.probe(cfg["data_dir"])
    c = components.BY_ID["enhance-sound"]
    assert c.needed(on(cfg, audio_engine="webrtc"), {**m, "os": "linux", "arch": "arm64"})
    assert not c.needed(on(cfg, audio_engine="webrtc"), {**m, "os": "darwin", "arch": "arm64"})
    assert not c.needed(on(cfg), {**m, "os": "linux", "arch": "x86_64"})

    x = _clicked(_speech(6.03) + _noise(int(6.03 * SR), -30), 1.5, 4.25)  # not a whole number of 10 ms frames
    # not installed yet: ffmpeg does it, saying why
    monkeypatch.setattr(enhance_audio, "webrtc_installed", lambda: False)
    rid = _record(client, env["he"], x)
    drain(db, on(cfg, audio_engine="webrtc"))
    assert "WebRTC's audio processing isn't here yet" in _log(db, rid)
    assert db.one("SELECT enhanced FROM $r", r=R("recording", rid))["enhanced"]["engine"] == "ffmpeg"

    monkeypatch.setattr(enhance_audio, "webrtc_installed", lambda: True)
    monkeypatch.setitem(sys.modules, "webrtc_noise_gain", types.SimpleNamespace(AudioProcessor=FakeWebrtc))
    enhance_audio.webrtc_delay.cache_clear()
    job = client.post(f"/api/v1/recordings/{rid}/enhance", headers=env["he"]).json()["job"]
    assert job
    drain(db, on(cfg, audio_engine="webrtc"))
    e = db.one("SELECT enhanced FROM $r", r=R("recording", rid))["enhanced"]
    assert e["engine"] == "webrtc" and 0 < e["speech"] < 1 and "filters" not in e
    assert FakeWebrtc.made[-1] == (0, 3) and enhance_audio.webrtc_delay(0, 3) == 160
    got = heard.got[-1]
    assert len(got) == len(x) and enhance_audio.lag(x, got) == 0  # put back in step
    assert "by WebRTC (" in _log(db, rid)
    enhance_audio.webrtc_delay.cache_clear()


def test_sound_settings_are_checked(db, cfg):
    settings.save(db, cfg, "enhance", {"audio": True, "audio_engine": "webrtc", "min_snr_db": 20, "min_level_db": -40})
    eff = settings.effective(db, cfg)["enhance"]
    assert (eff["audio"], eff["audio_engine"], eff["min_snr_db"], eff["min_level_db"]) == (True, "webrtc", 20, -40)
    for bad, said in (
        ({"audio_engine": "rnnoise"}, "enhance.audio_engine must be one of"),
        ({"min_snr_db": 90}, "enhance.min_snr_db is a number from 0 to 60"),
        ({"min_level_db": 3}, "enhance.min_level_db is a number from -70 to 0"),
        ({"audio": 1}, "enhance.audio is true or false"),
    ):
        with pytest.raises(ValueError, match=said):
            settings.save(db, cfg, "enhance", bad)
