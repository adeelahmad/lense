"""Voice matching, suggestions, merge/undo/link, channel splitting and clustering."""

from __future__ import annotations

import numpy as np
import pytest

from app.domain import ingest, speakers, store
from tests.helpers import unit


def test_match_suggest_merge_undo_link(db, cfg):
    rng = np.random.default_rng(3)
    A, B = unit(rng.normal(size=192)), unit(rng.normal(size=192))
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    first = speakers.match(db, cfg, pods, {"S0": (A, 40.0), "S1": (B, 40.0)})
    sa, sb = first["S0"][0], first["S1"][0]
    u = unit(rng.normal(size=192))
    u = unit(u - A * np.dot(u, A))
    near, unsure = unit(0.9 * A + 0.436 * u), unit(0.42 * A + 0.907 * u)
    second = speakers.match(db, cfg, pods, {"S0": (near, 30.0), "S1": (unsure, 30.0)})
    assert second["S0"][0] == sa
    new = second["S1"][0]
    assert new not in (sa, sb)
    sug = [s for s in speakers.list_speakers(db, pods) if s["id"] == new][0]["suggestions"]
    assert sug[0]["id"] == sa
    rid = db.next_id("recording")
    db.q("CREATE $r CONTENT $d", r=store.R("recording", rid), d={"space": pods, "fingerprint": "fp", "status": "analyzed"})
    db.q("INSERT INTO segment $s", s=ingest.segment_rows(rid, pods, [{"t0": 0, "t1": 1000, "text": "hello"}]))
    db.q("UPDATE segment SET speaker = $s WHERE recording = $r", s=new, r=rid)
    other = speakers.new_speaker(db, calls, name="Alice")
    speakers.link(db, new, other)
    mid = speakers.merge(db, new, sa)
    assert db.values("SELECT VALUE speaker FROM segment WHERE recording = $r", r=rid) == [sa]
    assert db.rows("SELECT record::id(in) AS a, record::id(out) AS b FROM same_as") == [{"a": sa, "b": other}]
    speakers.undo(db, mid)
    assert db.values("SELECT VALUE speaker FROM segment WHERE recording = $r", r=rid) == [new]
    assert db.rows("SELECT record::id(in) AS a, record::id(out) AS b FROM same_as") == [{"a": new, "b": other}]
    with pytest.raises(ValueError):
        speakers.merge(db, other, sa)  # never across namespaces
    with pytest.raises(ValueError):
        speakers.undo(db, mid)  # only once


def test_channels_and_clustering():
    rng = np.random.default_rng(0)
    x = rng.normal(scale=0.01, size=(48000, 2)).astype(np.float32)
    x[0:16000, 0] *= 20
    x[16000:32000, 1] *= 20
    x[32000:, 0] *= 20
    segs = [{"t0": 0, "t1": 1000}, {"t0": 1000, "t1": 2000}, {"t0": 2000, "t1": 3000}]
    assert speakers.channel_labels(x, segs) == ["CH0", "CH1", "CH0"]
    assert speakers.looks_dual_channel(x)
    assert not speakers.looks_dual_channel(np.stack([x[:, 0], x[:, 0]], axis=1))
    c = rng.normal(size=(3, 64))
    E = np.stack([c[i] + 0.05 * rng.normal(size=64) for i in (0, 1, 0, 2, 1)])
    assert list(speakers.cluster(E, 0.3)) == [0, 1, 0, 2, 1]


def _basis(n, seed=5):
    q, _ = np.linalg.qr(np.random.default_rng(seed).normal(size=(192, n)))
    return [q[:, i] for i in range(n)]


def test_a_voice_never_goes_to_someone_it_was_said_not_to_be(db, cfg):
    e1, e2, e3, e4 = _basis(4)
    pods = store.ns_id(db, "pods")
    sa, sx = speakers.new_speaker(db, pods, emb=e1, weight=40.0), speakers.new_speaker(db, pods, emb=e2, weight=40.0)
    # X's voice in one recording, split off from A (or turned down as A): not A's average, nor quite X's
    db.q("CREATE appearance CONTENT $a", a={"recording": 1, "speaker": sx, "local_label": "S0", "space": pods, "embedding": list(e3)})
    sure = unit(0.7 * e1 + 0.714 * e3)  # above match_threshold for A, nearer that recording than anything of A's
    unsure = unit(0.45 * e1 + 0.40 * e2 + 0.6 * e3 + 0.527 * e4)  # unsure of A and X both, A's average nearer
    before = speakers.match(db, cfg, pods, {"S0": (sure, 1.0)})
    assert before["S0"][0] == sa  # nobody said otherwise yet
    db.q("UPDATE $r SET embedding = $e, n_obs = 40.0", r=store.R("speaker", sa), e=list(e1))
    speakers.not_same(db, sa, sx)
    got = speakers.match(db, cfg, pods, {"S0": (sure, 1.0), "S1": (unsure, 1.0)})
    assert got["S0"][0] not in (sa, sx) and got["S0"][2] == "new"
    assert got["S1"][2] == "new"
    sug = {s["id"]: [g["id"] for g in s["suggestions"]] for s in speakers.list_speakers(db, pods)}
    assert sug[got["S0"][0]] == [] and sug[got["S1"][0]] == [sx]  # suggested as X, never as A
    # a voice that sounds more like A still goes to A, and two voices nearest X don't spill over to A
    assert speakers.match(db, cfg, pods, {"S0": (unit(0.9 * e1 + 0.1 * e3), 1.0)})["S0"][0] == sa
    two = speakers.match(db, cfg, pods, {"S0": (e2, 1.0), "S1": (unit(0.8 * e2 + 0.6 * e1), 1.0)})
    assert two["S0"][0] == sx and two["S1"][0] != sa
