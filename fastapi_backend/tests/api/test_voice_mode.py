"""Voice recognition per namespace (speakers.voice_mode): on by default; off, voices are told apart in each recording
only, and the namespace's voiceprints are deleted."""

from __future__ import annotations

import numpy as np

from app.domain import ingest, speakers, store
from tests.helpers import login, make_user, unit

R = store.R


def _recording(db, nid, n):
    rid = db.next_id("recording")
    db.q(
        "CREATE $r CONTENT $d", r=R("recording", rid), d={"space": nid, "fingerprint": f"fp{n}", "status": "transcribed", "source": "audio"}
    )
    segs = [{"t0": 0, "t1": 1000, "text": "hello"}, {"t0": 1000, "t1": 2000, "text": "hi"}]
    db.q("INSERT INTO segment $s", s=ingest.segment_rows(rid, nid, segs))
    for i, who in enumerate(("A", "B")):
        db.q("UPDATE segment SET provider_speaker = $p WHERE recording = $r AND idx = $i", p=who, r=rid, i=i)
    return rid


def test_voices_can_be_told_apart_without_being_recognised(client, db, cfg, monkeypatch):
    rng = np.random.default_rng(5)
    prints = {"A": unit(rng.normal(size=192)), "B": unit(rng.normal(size=192))}
    monkeypatch.setattr(ingest, "audio_path", lambda *a, **k: "unused.wav")
    monkeypatch.setattr(ingest, "decode", lambda *a, **k: np.zeros(16000, dtype=np.float32))
    monkeypatch.setattr(speakers, "get_embedder", lambda cfg, log=None: object())
    monkeypatch.setattr(speakers, "voice_print", lambda sig, g, emb, s: (prints[g[0]["provider_speaker"]], 30.0))
    nid = store.ns_id(db, "pods")

    def who(rid):
        return sorted(db.values("SELECT VALUE speaker FROM appearance WHERE recording = $r", r=rid))

    first, second = _recording(db, nid, 1), _recording(db, nid, 2)
    speakers.diarize_one(db, cfg, first, log=lambda *a: None)
    speakers.diarize_one(db, cfg, second, log=lambda *a: None)
    assert who(first) == who(second)  # recognised: the same two people
    assert db.values("SELECT VALUE embedding FROM speaker WHERE space = $s AND embedding != NONE", s=nid)

    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    ho, he = login(client, "own@x.io", "owner password 1"), login(client, "ed@x.io", "editor password 1")
    assert client.get("/api/v1/speakers", params={"ns": "pods"}, headers=ho).json()["voices"] == "recognize"
    url = "/api/v1/namespaces/pods/voices/mode"
    assert client.put(url, headers=he, json={"mode": "separate"}).status_code == 403  # owners only
    assert client.put(url, headers=ho, json={"mode": "never"}).status_code == 422
    assert client.put(url, headers=ho, json={"mode": "separate"}).status_code == 200
    assert client.get("/api/v1/speakers", params={"ns": "pods"}, headers=ho).json()["voices"] == "separate"
    assert db.values("SELECT VALUE embedding FROM speaker WHERE space = $s AND embedding != NONE", s=nid) == []
    assert db.values("SELECT VALUE embedding FROM appearance WHERE space = $s AND embedding != NONE", s=nid) == []
    assert db.one("SELECT detail FROM audit_log WHERE action = 'voices.mode'")["detail"] == {"mode": "separate"}

    third = _recording(db, nid, 3)
    speakers.diarize_one(db, cfg, third, log=lambda *a: None)
    assert len(who(third)) == 2 and not set(who(third)) & set(who(first))  # told apart, matched with nobody
    assert db.values("SELECT VALUE embedding FROM speaker WHERE space = $s AND embedding != NONE", s=nid) == []

    assert client.put(url, headers=ho, json={"mode": "recognize"}).status_code == 200
    fourth, fifth = _recording(db, nid, 4), _recording(db, nid, 5)
    speakers.diarize_one(db, cfg, fourth, log=lambda *a: None)
    speakers.diarize_one(db, cfg, fifth, log=lambda *a: None)
    assert who(fourth) == who(fifth)  # recognising again, from then on
