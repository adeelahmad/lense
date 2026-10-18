"""The clean_audio step: a recording's sound cleaned up with WebRTC's audio processing (pywebrtc-audio) before it's
transcribed, with silence left out of what's transcribed and the transcript put back on the recording's timeline."""

from __future__ import annotations

import json
import wave

import numpy as np
import pytest

from app.domain import audio_cleanup, ingest, jobs, pipelines, settings, store
from tests.helpers import drain, quiet

R = store.R
SR = 16000


def babble(seconds, seed):
    """Something WebRTC's voice detection hears as speech: syllables of a voice with two formants, with gaps."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    out, i = np.zeros(n, np.float32), 0
    while i < n:
        size, f0 = int(rng.uniform(0.12, 0.25) * SR), rng.uniform(100, 220)
        t = np.arange(size) / SR
        phase = np.cumsum(2 * np.pi * f0 * (1 + 0.1 * np.sin(2 * np.pi * rng.uniform(2, 5) * t)) / SR)
        f1, f2 = rng.uniform(300, 800), rng.uniform(900, 2200)
        s = sum(
            np.sin(k * phase) * (np.exp(-(((k * f0 - f1) / 200) ** 2)) + 0.6 * np.exp(-(((k * f0 - f2) / 300) ** 2))) for k in range(1, 30)
        )
        out[i : i + size] = (0.3 * s * np.sin(np.pi * np.arange(size) / size))[: n - i]
        i += size + int(rng.uniform(0.03, 0.12) * SR)
    return out


def quiet_for(seconds):
    return np.zeros(int(seconds * SR), np.float32)


def write(path, *channels):
    x = np.stack(channels, axis=1) if len(channels) > 1 else channels[0][:, None]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def talk():
    """2 s of quiet, 4 s of speech, 10 s of quiet, 3 s of speech, 3 s of quiet, over a faint hiss."""
    x = np.concatenate([quiet_for(2), babble(4, 1), quiet_for(10), babble(3, 2), quiet_for(3)])
    return (x + np.random.default_rng(0).normal(0, 0.003, len(x))).astype(np.float32)


class Engine:
    """Hears what it's given and says two lines with word times, at 1 s and at 7 s of what it heard."""

    name = "sensevoice"
    heard = []

    def transcribe(self, audio):
        Engine.heard.append(len(audio))
        return [
            {"t0": 1000, "t1": 2000, "text": "first words", "words": json.dumps([["first", 1000, 1500], ["words", 1500, 2000]])},
            {"t0": 7000, "t1": 8000, "text": "later on", "words": None},
        ]


@pytest.fixture
def recording(db, cfg, folder, monkeypatch):
    def make(*channels):
        wav, tr = folder / "talk.wav", folder / "talk.txt"
        write(wav, *(channels or (talk(),)))
        tr.write_text("[00:00] Ann: Hello.\n")
        return ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)

    Engine.heard = []
    monkeypatch.setattr(ingest, "get_engine", lambda cfg, log=None: Engine())
    return make


def _run(db, cfg, rid, *steps):
    jid = jobs.enqueue(db, rid, [*steps, {"type": "transcribe", "force": True}])
    drain(db, cfg)
    job = db.one("SELECT status, log FROM $j", j=R("job", jid))
    assert job["status"] == "succeeded", job["log"]
    return job["log"]


def _segments(db, rid):
    return db.rows("SELECT idx, t0, t1, text, words FROM segment WHERE recording = $r ORDER BY idx", r=rid)


def test_speech_is_found_and_silence_left_out_of_transcription(db, cfg, recording):
    rid = recording()
    log = _run(db, cfg, rid, "clean_audio")
    saved = db.one("SELECT audio_cleanup FROM $r", r=R("recording", rid))["audio_cleanup"]
    assert saved["duration_ms"] == 22000
    assert 6000 <= saved["speech_ms"] <= 8000  # 7 s were spoken
    assert saved["noise"] == "moderate" and saved["gain"] is True and saved["engine"].startswith("pywebrtc-audio")
    assert any("speech in" in line and "of silence left out of transcription" in line for line in log)
    # the 10 s pause and the 3 s at the end weren't transcribed; the 2 s at the start are too short to leave out
    heard = Engine.heard[-1] / SR
    assert 9 <= heard <= 11.5
    first, later = _segments(db, rid)
    assert (first["t0"], first["t1"]) == (1000, 2000)  # before the pause: where it was
    assert json.loads(first["words"]) == [["first", 1000, 1500], ["words", 1500, 2000]]
    assert 15000 <= later["t0"] <= 16500  # after it: on the recording's timeline again
    assert later["t1"] - later["t0"] == 1000
    # the player's waveform is still the whole recording's
    rec = db.one("SELECT envelope, path FROM $r", r=R("recording", rid))
    assert rec["envelope"] == ingest.envelope(ingest.decode(rec["path"]))


def test_without_the_step_or_with_every_option_off_it_is_transcribed_as_it_is(db, cfg, recording):
    rid = recording()
    _run(db, cfg, rid)
    assert Engine.heard[-1] == 22 * SR
    _run(db, cfg, rid, "clean_audio")
    assert Engine.heard[-1] < 22 * SR
    cfg["transcribe"]["cleanup"] = {"noise": "off", "gain": False, "high_pass": False, "echo": "off", "trim_silence": False}
    log = _run(db, cfg, rid, "clean_audio")
    assert db.one("SELECT audio_cleanup FROM $r", r=R("recording", rid)).get("audio_cleanup") is None
    assert any("transcribed as it is" in line for line in log)
    assert Engine.heard[-1] == 22 * SR


def test_cleaned_but_not_trimmed(db, cfg, recording):
    cfg["transcribe"]["cleanup"] = {"trim_silence": False, "noise": "very_high"}
    rid = recording()
    _run(db, cfg, rid, "clean_audio")
    assert Engine.heard[-1] == 22 * SR
    assert [s["t0"] for s in _segments(db, rid)] == [1000, 7000]


def test_echo_of_the_far_side_is_taken_out(cfg):
    far = np.concatenate([babble(20, 5), quiet_for(4)])
    lag = int(0.03 * SR)
    echo = (np.concatenate([np.zeros(lag, np.float32), far[:-lag]]) * 0.5).astype(np.float32)
    o = {**audio_cleanup.DEFAULTS, "noise": "off", "gain": False, "high_pass": False}
    out, _, _ = audio_cleanup.process(echo, o, far)

    def rms(x):  # once it has settled
        return float(np.sqrt(np.mean(np.square(x[4 * SR : 20 * SR]))))

    assert rms(out) < rms(echo) / 20


def test_echo_needs_a_second_channel(db, cfg, recording, folder):
    cfg["transcribe"]["cleanup"] = {"echo": "right"}
    rid = recording()  # one channel
    log = _run(db, cfg, rid, "clean_audio")
    assert db.one("SELECT audio_cleanup FROM $r", r=R("recording", rid))["audio_cleanup"]["echo_note"] == "no second channel"
    assert any("echo not cancelled: no second channel" in line for line in log)

    mic, speakers = talk(), np.concatenate([quiet_for(16), babble(3, 9), quiet_for(3)])
    rid = recording(mic, speakers)
    log = _run(db, cfg, rid, "clean_audio")
    assert any("echo of the right channel cancelled" in line for line in log)
    assert Engine.heard[-1] < 22 * SR  # both sides heard, the quiet left out


def test_documents_and_imported_text_have_no_sound(db, cfg):
    rid = ingest.import_text(db, cfg, "pods", "Ann|N|Hello there.", "Note")
    assert jobs.for_source([{"type": "clean_audio"}, {"type": "analyze"}], "transcript") == [{"type": "analyze"}]
    assert jobs.for_source([{"type": "clean_audio"}, {"type": "transcribe"}], "document") == [{"type": "read"}]
    jid = jobs.enqueue(db, rid, ["clean_audio"])
    drain(db, cfg)
    assert any(
        "clean_audio skipped: imported text has no sound to clean up" in line
        for line in db.one("SELECT log FROM $j", j=R("job", jid))["log"]
    )


def test_a_worker_without_the_library_transcribes_the_original(db, cfg, recording, monkeypatch):
    rid = recording()
    _run(db, cfg, rid, "clean_audio")

    def missing(cfg, say=None):
        raise RuntimeError("pywebrtc-audio isn't installed and couldn't be fetched (no network)")

    monkeypatch.setattr(audio_cleanup, "ensure", missing)
    _run(db, cfg, rid)
    assert Engine.heard[-1] == 22 * SR


def test_spans_joins_and_times():
    probs = np.zeros(400, np.float32)  # 4 s of 10 ms frames
    probs[50:100] = 0.9  # 0.5–1.0 s
    probs[120:150] = 0.9  # a 0.2 s pause, then 1.2–1.5 s
    probs[340:360] = 0.9  # 3.4–3.6 s, after 1.9 s of quiet
    assert audio_cleanup.speech_ms(probs) == 1000
    assert audio_cleanup.speech_spans(probs, 1000, 4000) == [(0, 1800), (3100, 4000)]  # padded, short ends kept
    assert audio_cleanup.speech_spans(probs, 100, 4000) == [(200, 1800), (3100, 3900)]
    x = np.arange(4 * SR, dtype=np.float32)
    kept, joins = audio_cleanup.cut(x, [(200, 1800), (3100, 3900)])
    assert len(kept) == int(2.4 * SR) and kept[0] == 0.2 * SR and kept[int(1.6 * SR)] == 3.1 * SR
    to = audio_cleanup.to_recording
    assert [to(0, joins), to(1600, joins), to(1600, joins, end=True), to(2400, joins, end=True)] == [200, 3100, 1800, 3900]
    assert audio_cleanup.speech_spans(np.zeros(100, np.float32), 1000, 1000) == []


def test_settings_are_checked(db, cfg):
    settings.save(db, cfg, "transcribe", {"cleanup": {"noise": "high", "echo": "left", "min_silence_ms": 1500}})
    eff = settings.effective(db, cfg)
    assert audio_cleanup.options(eff) == {**audio_cleanup.DEFAULTS, "noise": "high", "echo": "left", "min_silence_ms": 1500}
    for bad, says in (
        ({"noise": "loud"}, "noise is one of"),
        ({"echo": "both"}, "echo is one of"),
        ({"gain": "yes"}, "gain is true or false"),
        ({"min_silence_ms": 10}, "min_silence_ms"),
        ({"only_if_poor": "sometimes"}, "only_if_poor is true or false"),
        ({"snr_floor_db": 80}, "snr_floor_db is a number"),
        ({"denoise": True}, "has no setting denoise"),
    ):
        with pytest.raises(ValueError, match=says):
            settings.save(db, cfg, "transcribe", {"cleanup": bad})


def test_pipelines_and_workers_take_the_step(db, cfg):
    assert pipelines.validate_steps(db, ["clean_audio", "transcribe"]) == [{"type": "clean_audio"}, {"type": "transcribe"}]
    assert "clean_audio" not in pipelines.STANDARD  # nothing changes until a pipeline has it
    cfg["workers"]["steps"] = ["transcribe", "diarize"]  # a list written before the step existed
    assert "clean_audio" in jobs.Worker(db, lambda: cfg, name="older").can
    from app.domain import components

    assert components.BY_ID["webrtc-audio"].steps == {"clean_audio"}  # fetched when a job first waits for it


def test_an_agent_reads_what_the_step_did(client, db, cfg, recording):
    from tests.helpers import login, make_user

    rid = recording()
    _run(db, cfg, rid, "clean_audio")
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "ed@x.io", "editor password 1")
    got = client.get(f"/api/v1/recordings/{rid}", headers=h).json()["audio_cleanup"]
    assert got["kept_ms"] < got["duration_ms"] == 22000 and got["trim_silence"] is True
    pid = client.post(
        "/api/v1/pipelines",
        headers=login(client, *_admin(db)),
        json={"name": "Clean then transcribe", "steps": ["clean_audio", "transcribe"]},
    )
    assert pid.status_code in (200, 201), pid.text


def noisy():
    """The same speech and pauses as talk(), but under loud, steady noise: a low signal-to-noise recording."""
    x = np.concatenate([quiet_for(2), babble(4, 1), quiet_for(10), babble(3, 2), quiet_for(3)])
    return (x + np.random.default_rng(1).normal(0, 0.06, len(x))).astype(np.float32)


def test_quality_reads_signal_to_noise_and_clipping():
    clean_snr, clip = audio_cleanup.quality(talk())
    noisy_snr, _ = audio_cleanup.quality(noisy())
    assert clean_snr is not None and noisy_snr is not None
    assert clean_snr > noisy_snr  # the clean recording measures a higher signal-to-noise
    assert clip < 1  # talk() isn't clipped
    clipped = np.clip(talk() * 20, -1, 1)
    assert audio_cleanup.quality(clipped)[1] > audio_cleanup.CLIP_MAX  # driven into clipping


def test_quality_gate_leaves_a_clean_recording_as_it_is(db, cfg, recording):
    cfg["transcribe"]["cleanup"] = {"only_if_poor": True}  # the clean-sounding talk() is over the default 20 dB floor
    rid = recording()
    log = _run(db, cfg, rid, "clean_audio")
    assert db.one("SELECT audio_cleanup FROM $r", r=R("recording", rid)).get("audio_cleanup") is None
    assert any("good quality" in line and "left as it is" in line for line in log)
    assert Engine.heard[-1] == 22 * SR  # it transcribed the whole original, nothing trimmed or cleaned


def test_quality_gate_still_cleans_a_low_quality_recording(db, cfg, recording):
    cfg["transcribe"]["cleanup"] = {"only_if_poor": True, "trim_silence": False}
    rid = recording(noisy())
    _run(db, cfg, rid, "clean_audio")
    saved = db.one("SELECT audio_cleanup FROM $r", r=R("recording", rid))["audio_cleanup"]
    assert saved and not saved.get("skipped")
    assert saved["snr_db"] < saved["snr_floor_db"]  # under the floor, so it was cleaned


def _admin(db):
    from tests.helpers import make_user

    make_user(db, "ad@x.io", "admin password 1", admin=True)
    return "ad@x.io", "admin password 1"
