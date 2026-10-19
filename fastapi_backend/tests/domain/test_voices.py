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


def test_merging_keeps_who_they_are_not(db):
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    a, b, c, d = (speakers.new_speaker(db, pods) for _ in range(4))
    e = speakers.new_speaker(db, calls)
    speakers.not_same(db, b, c, by="ann@example.com")  # moves to a-c
    speakers.not_same(db, b, a)  # would be a-a: dropped
    speakers.not_same(db, b, d)  # a-d is already there: kept once
    speakers.not_same(db, a, d)
    speakers.not_same(db, e, b)  # across namespaces too
    before = db.rows("SELECT a, b, by FROM not_same ORDER BY a, b")

    def pairs():
        return sorted((r["a"], r["b"]) for r in db.rows("SELECT a, b FROM not_same"))

    mid = speakers.merge(db, b, a)
    assert pairs() == sorted([(min(a, c), max(a, c)), (min(a, d), max(a, d)), (min(a, e), max(a, e))])
    assert db.one("SELECT by FROM $p", p=speakers._pair(a, c))["by"] == "ann@example.com"
    ids = db.rows("SELECT record::id(id) AS k, a, b FROM not_same")
    assert all(r["k"] == f"{r['a']}-{r['b']}" for r in ids)  # keyed as not_same() keys them
    speakers.undo(db, mid)
    assert db.rows("SELECT a, b, by FROM not_same ORDER BY a, b") == before


def test_upgrade_gives_pairs_lost_to_merges_to_who_they_were_merged_into(db):
    pods = store.ns_id(db, "pods")
    a, b, c, x = (speakers.new_speaker(db, pods) for _ in range(4))
    speakers.not_same(db, b, c)
    speakers.not_same(db, b, a)
    speakers.not_same(db, x, c)
    # as merges left it before they carried pairs over: b went into x, then x into a
    for src, dst in ((b, x), (x, a)):
        db.q("CREATE $r CONTENT $m", r=store.R("merge", db.next_id("merge")), m={"from_id": src, "into_id": dst, "undone": False})
        db.q("DELETE $r", r=store.R("speaker", src))
    speakers.migrate_not_same(db)
    once = sorted((r["a"], r["b"]) for r in db.rows("SELECT a, b FROM not_same"))
    assert (min(a, c), max(a, c)) in once and (min(a, b), max(a, b)) in once  # the old rows stay, for undo
    assert len(once) == 4  # b-c and x-c both became a-c, b-a became nothing
    speakers.migrate_not_same(db)
    assert sorted((r["a"], r["b"]) for r in db.rows("SELECT a, b FROM not_same")) == once


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
