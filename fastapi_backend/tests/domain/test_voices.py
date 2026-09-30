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
