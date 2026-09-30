"""Who is speaking: diarisation inside a recording, voice IDs inside a namespace.

Speaker ids belong to one namespace and are never merged across namespaces. Across
namespaces the archive only records links: ones you declare, and (optionally) voice
matches it suggests as graph edges.
"""
from __future__ import annotations

import bisect
import os
import pathlib
import re

import numpy as np

from collections import Counter

from . import ingest, store

GENERIC = re.compile(r"^(?:SPEAKER_?\d+|spk_?\d+|S\d+|CH\d+|Speaker \d+|unknown)$", re.I)
_CACHE = {}


# ---------- inside one recording ----------
def assign_by_overlap(segs, turns):
    turns = sorted(turns)
    out = []
    for s in segs:
        best, lab = 0, None
        for a, b, l in turns:
            if a >= s["t1"]:
                break
            ov = min(b, s["t1"]) - max(a, s["t0"])
            if ov > best:
                best, lab = ov, l
        out.append(lab)
    return out


def looks_dual_channel(x):
    """True when a stereo file carries different people on each side (not a stereo mix)."""
    if x.ndim < 2 or x.shape[1] < 2:
        return False
    step = max(1, len(x) // 400000)
    a, b = x[::step, 0], x[::step, 1]
    if min(float(np.sqrt(np.mean(a * a))), float(np.sqrt(np.mean(b * b)))) < 1e-4:
        return False
    return float(np.corrcoef(a, b)[0, 1]) < 0.8


def channel_labels(stereo, segs, ratio=1.4):
    out, last = [], None
    for s in segs:
        x = stereo[s["t0"] * ingest.SR // 1000:s["t1"] * ingest.SR // 1000]
        if len(x):
            e = np.sqrt(np.mean(np.square(x), axis=0)) + 1e-12
            if e.max() / e.min() >= ratio or last is None:
                last = f"CH{int(e.argmax())}"
        out.append(last)
    return out


def cluster(embs, threshold=0.55, min_k=None, max_k=None):
    """Average-linkage clustering on cosine distance, relabelled in order of first appearance."""
    X = np.asarray(embs, dtype=np.float64)
    if len(X) == 1:
        return np.zeros(1, dtype=int)
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist
    X = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    Z = linkage(pdist(X, "cosine"), "average")
    lab = fcluster(Z, t=threshold, criterion="distance")
    k = len(set(lab))
    if max_k and k > max_k:
        lab = fcluster(Z, t=max_k, criterion="maxclust")
    elif min_k and k < min_k <= len(X):
        lab = fcluster(Z, t=min_k, criterion="maxclust")
    order = {}
    return np.array([order.setdefault(int(v), len(order)) for v in lab])


class Embedder:
    """SpeechBrain ECAPA voiceprints (192-d), L2-normalised."""

    def __init__(self, cfg):
        sc = cfg["speakers"]
        try:
            import torch
            try:
                from speechbrain.inference.speaker import EncoderClassifier
            except ImportError:
                from speechbrain.pretrained import EncoderClassifier
        except ImportError as e:
            raise RuntimeError("voice IDs need SpeechBrain: pip install 'lens-archive[voices]'") from e
        self.torch = torch
        save = pathlib.Path(cfg["data_dir"]) / "models" / sc["model"].replace("/", "_")
        self.enc = EncoderClassifier.from_hparams(source=sc["model"], savedir=str(save),
                                                  run_opts={"device": ingest.pick_device(cfg["transcribe"]["device"])})

    def __call__(self, clips):
        out = []
        for c in clips:
            with self.torch.no_grad():
                e = self.enc.encode_batch(self.torch.from_numpy(np.ascontiguousarray(c, dtype=np.float32))[None])
            e = e.squeeze().cpu().numpy().astype(np.float32)
            out.append(e / (np.linalg.norm(e) + 1e-9))
        return np.stack(out)


def get_embedder(cfg, log=print):
    if cfg["speakers"]["embedder"] == "none":
        return None
    if "embedder" not in _CACHE:
        try:
            _CACHE["embedder"] = Embedder(cfg)
        except RuntimeError as e:
            log(f"  {e}; speakers are kept per recording only")
            _CACHE["embedder"] = None
    return _CACHE["embedder"]


def pyannote_turns(cfg, path):
    d = cfg["diarize"]
    if "pyannote" not in _CACHE:
        try:
            import torch
            from pyannote.audio import Pipeline
        except ImportError as e:
            raise RuntimeError("pip install 'lens-archive[pyannote]'") from e
        tok = os.environ.get(d["pyannote"].get("token_env") or "HF_TOKEN")
        try:
            pipe = Pipeline.from_pretrained(d["pyannote"]["model"], token=tok)
        except TypeError:
            pipe = Pipeline.from_pretrained(d["pyannote"]["model"], use_auth_token=tok)
        pipe.to(torch.device(ingest.pick_device(cfg["transcribe"]["device"])))
        _CACHE["pyannote"] = pipe
    kw = {k: d[k] for k in ("min_speakers", "max_speakers") if d.get(k)}
    out = _CACHE["pyannote"](str(path), **kw)
    ann = getattr(out, "speaker_diarization", out)
    return [(int(t.start * 1000), int(t.end * 1000), str(l)) for t, _, l in ann.itertracks(yield_label=True)]


def cluster_labels(mono, segs, embed, cfg):
    d = cfg["diarize"]
    long_ix = [i for i, s in enumerate(segs) if s["t1"] - s["t0"] >= 1000]
    if not long_ix:
        return ["S0"] * len(segs)
    clips = [mono[segs[i]["t0"] * ingest.SR // 1000:min(segs[i]["t1"], segs[i]["t0"] + 10000) * ingest.SR // 1000] for i in long_ix]
    lab = cluster(embed(clips), d["cluster_threshold"], d.get("min_speakers"), d.get("max_speakers"))
    out = [None] * len(segs)
    for i, l in zip(long_ix, lab):
        out[i] = f"S{l}"
    mids = [(segs[i]["t0"] + segs[i]["t1"]) / 2 for i in long_ix]
    for i, s in enumerate(segs):  # short backchannels take the label of the nearest long turn
        if out[i] is None:
            m = (s["t0"] + s["t1"]) / 2
            j = bisect.bisect_left(mids, m)
            near = min((k for k in (j - 1, j) if 0 <= k < len(mids)), key=lambda k: abs(mids[k] - m))
            out[i] = out[long_ix[near]]
    return out


def voice_print(signal, segs, embed, seconds=60):
    spans = sorted(((s["t1"] - s["t0"], s["t0"], s["t1"]) for s in segs), reverse=True)
    clips, total = [], 0.0
    for d, a, b in spans:
        if d < 500 or (d < 1000 and clips):
            break
        b = min(b, a + 8000)
        clips.append(signal[a * ingest.SR // 1000:b * ingest.SR // 1000])
        total += (b - a) / 1000
        if total >= seconds:
            break
    if not clips:
        return None
    E, w = embed(clips), np.array([len(c) for c in clips], dtype=np.float64)
    v = (E * w[:, None]).sum(axis=0)
    return v / (np.linalg.norm(v) + 1e-9), total


# ---------- the namespace registry ----------
R = store.R


def _vec(v):
    return None if v is None else [float(x) for x in np.asarray(v).ravel()]


def new_speaker(db, nid, name=None, emb=None, weight=0.0):
    n = len(db.values("SELECT VALUE id FROM speaker WHERE space = $s", s=nid)) + 1
    while db.values("SELECT VALUE id FROM speaker WHERE space = $s AND label = $l", s=nid, l=f"Speaker {n}"):
        n += 1
    sid = db.next_id("speaker")
    db.q("CREATE $r CONTENT $d", r=R("speaker", sid), d=store.clean({"space": nid, "label": f"Speaker {n}", "label_key": f"{nid}:Speaker {n}", "name": name,
                                                                      "embedding": _vec(emb), "n_obs": float(weight), "created_at": store.now()}))
    return sid


def _update_centroid(db, sid, emb, weight):
    row = db.one("SELECT embedding, n_obs FROM $r", r=R("speaker", sid))
    old, n = row.get("embedding"), row.get("n_obs") or 0
    v = emb * weight if old is None else np.asarray(old, dtype=np.float64) * n + emb * weight
    db.q("UPDATE $r SET embedding = $e, n_obs = $n", r=R("speaker", sid), e=_vec(v / (np.linalg.norm(v) + 1e-9)), n=float(n + weight))


def match(db, cfg, nid, prints):
    """prints: {local_label: (embedding, seconds)} -> {local_label: (speaker_id, score, how)}.

    Greedy one-to-one assignment by similarity: two voices in one recording never map to the
    same speaker. Close-but-unsure matches become new speakers with a suggested merge.
    """
    hi, lo = cfg["speakers"]["match_threshold"], cfg["speakers"]["review_threshold"]
    reg = [(r["id"], np.asarray(r["embedding"], dtype=np.float64)) for r in
           db.rows("SELECT record::id(id) AS id, embedding FROM speaker WHERE space = $s AND embedding != NONE", s=nid)]
    pairs = sorted(((float(np.dot(e, v)), l, sid) for l, (e, _) in prints.items() for sid, v in reg if v.shape == e.shape), reverse=True)
    out, used = {}, set()
    for sim, l, sid in pairs:
        if sim < hi:
            break
        if l in out or sid in used:
            continue
        out[l] = (sid, sim, "voice")
        used.add(sid)
        _update_centroid(db, sid, prints[l][0], prints[l][1])
    for l, (e, w) in prints.items():
        if l in out:
            continue
        sid = new_speaker(db, nid, emb=e, weight=w)
        out[l] = (sid, None, "new")
        near = [(sc, c) for sc, ll, c in pairs if ll == l and lo <= sc < hi]
        if near:
            db.q("CREATE suggestion CONTENT $d", d={"speaker": sid, "candidate": near[0][1], "score": near[0][0], "space": nid})
    return out


def assign_labels(db, nid, rid, mapping):
    """Speakers named in an imported transcript. Generic labels (SPEAKER_00, S1, CH0) say nothing
    about identity across recordings, so each gets a fresh speaker; real names are reused."""
    ids = {}
    for local, disp in mapping.items():
        if GENERIC.match(str(disp)):
            sid = new_speaker(db, nid)
        else:
            row = db.one("SELECT record::id(id) AS id FROM speaker WHERE space = $s AND (name = $n OR label = $n) LIMIT 1", s=nid, n=disp)
            sid = row["id"] if row else new_speaker(db, nid, name=disp)
        ids[local] = sid
        db.run(["DELETE appearance WHERE recording = $rid AND local_label = $l", "CREATE appearance CONTENT $a",
                "UPDATE segment SET speaker = $sid WHERE recording = $rid AND local_speaker = $l"],
               rid=rid, l=local, sid=sid, a={"recording": rid, "speaker": sid, "local_label": local, "method": "label", "space": nid})
    db.q("UPDATE $r SET status = 'diarized', diarizer = 'labels', diarized_at = $t", r=R("recording", rid), t=store.now())
    return ids


def diarize_one(db, cfg, rid, log=print):
    r = db.one("SELECT record::id(id) AS id, space, path, source, channels, title, remote FROM $r", r=R("recording", rid))
    segs = db.rows("SELECT record::id(id) AS id, idx, t0, t1 FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    engine = cfg["diarize"]["engine"]
    path = ingest.audio_path(db, cfg, r) if r.get("source") == "audio" and engine != "none" and segs else None
    labels, how, mono, stereo = [None] * len(segs), "none", None, None
    if segs and r.get("source") == "audio" and engine != "none":
        if engine in ("auto", "channels") and (r.get("channels") or 1) >= 2:
            stereo = ingest.decode(path, channels=2)
            if engine == "channels" or looks_dual_channel(stereo):
                labels, how = channel_labels(stereo, segs), "channels"
        if how == "none" and engine == "pyannote":
            labels, how = assign_by_overlap(segs, pyannote_turns(cfg, path)), "pyannote"
        elif how == "none" and engine in ("auto", "cluster"):
            emb = get_embedder(cfg, log)
            if emb is not None:
                mono = ingest.decode(path)
                labels, how = cluster_labels(mono, segs, emb, cfg), "cluster"
    groups = {}
    for s, l in zip(segs, labels):
        if l is not None:
            groups.setdefault(l, []).append(s)
    prints = {}
    emb = get_embedder(cfg, log) if groups else None
    if emb is not None:
        for l, g in groups.items():
            if how == "channels":
                sig = stereo[:, int(l[2:])]
            else:
                if mono is None:
                    mono = stereo.mean(axis=1) if stereo is not None else ingest.decode(path)
                sig = mono
            vp = voice_print(sig, g, emb, cfg["speakers"]["sample_seconds"])
            if vp is not None:
                prints[l] = vp
    ids = match(db, cfg, r["space"], prints) if prints else {}
    for l in groups:
        if l not in ids:
            ids[l] = (new_speaker(db, r["space"]), None, "new")
    stmts = ["DELETE appearance WHERE recording = $rid", "UPDATE segment SET speaker = NONE, local_speaker = NONE WHERE recording = $rid"]
    params = {"rid": r["id"], "rec": R("recording", r["id"]), "t": store.now(), "how": how,
              "apps": [store.clean({"recording": r["id"], "speaker": sid, "local_label": l, "score": sc, "method": m, "space": r["space"],
                                    "embedding": _vec(prints[l][0]) if l in prints else None}) for l, (sid, sc, m) in ids.items()]}
    if params["apps"]:
        stmts.append("INSERT INTO appearance $apps")
    for k, (l, g) in enumerate(groups.items()):
        stmts.append(f"UPDATE $g{k} SET local_speaker = $l{k}, speaker = $s{k}")
        params.update({f"g{k}": [R("segment", s["id"]) for s in g], f"l{k}": l, f"s{k}": ids[l][0]})
    stmts.append("UPDATE $rec SET status = 'diarized', diarizer = $how, diarized_at = $t, analyzed_at = NONE")
    db.run(stmts, **params)
    log(f"  {r['title']}: {len(groups)} speaker(s) by {how}")
    return len(groups)


def diarize_pending(db, cfg, ns=None, limit=0, force=False, log=print):
    where = "status IN ['transcribed', 'diarized', 'analyzed']" if force else "status = 'transcribed'"
    if ns:
        where += " AND space = $s"
    rows = db.rows(f"SELECT record::id(id) AS id, title, recorded_at FROM recording WHERE {where} ORDER BY recorded_at, id",
                   s=store.ns_id(db, ns, create=False) if ns else None)[:limit or None]
    done = 0
    for r in rows:
        try:
            diarize_one(db, cfg, r["id"], log)
            done += 1
        except Exception as e:  # noqa: BLE001
            db.q("UPDATE $r SET error = $e", r=R("recording", r["id"]), e=f"diarize: {type(e).__name__}: {e}"[:500])
            log(f"  {r['title']}: diarisation failed ({type(e).__name__}: {e})")
    return done

# ---------- editing identities (every merge can be undone) ----------
def rename(db, sid, name):
    db.q("UPDATE $r SET name = $n", r=R("speaker", sid), n=(name or "").strip() or None)


def merge(db, src, dst):
    a, b = db.one("SELECT * FROM $r", r=R("speaker", src)), db.one("SELECT * FROM $r", r=R("speaker", dst))
    if not a or not b or src == dst:
        raise ValueError("pick two different speakers")
    if a["space"] != b["space"]:
        raise ValueError("speakers from different namespaces are linked, not merged")
    links = db.rows("SELECT record::id(in) AS a, record::id(out) AS b FROM same_as WHERE in = $r OR out = $r", r=R("speaker", src))
    snap = {"speaker": {k: a.get(k) for k in ("space", "label", "name", "embedding", "n_obs", "created_at")},
            "dst": {k: b.get(k) for k in ("embedding", "n_obs", "name")},
            "segments": db.values("SELECT VALUE record::id(id) FROM segment WHERE speaker = $s", s=src),
            "appearances": db.values("SELECT VALUE record::id(id) FROM appearance WHERE speaker = $s", s=src),
            "suggestions": db.rows("SELECT speaker, candidate, score, space FROM suggestion WHERE speaker = $s OR candidate = $s", s=src),
            "links": links}
    ea, eb, patch = a.get("embedding"), b.get("embedding"), {}
    if ea is not None and eb is not None:
        v = np.asarray(ea) * (a.get("n_obs") or 1) + np.asarray(eb) * (b.get("n_obs") or 1)
        patch = {"embedding": _vec(v / (np.linalg.norm(v) + 1e-9)), "n_obs": float((a.get("n_obs") or 0) + (b.get("n_obs") or 0))}
    elif eb is None and ea is not None:
        patch = {"embedding": ea, "n_obs": a.get("n_obs") or 0}
    if not b.get("name") and a.get("name"):
        patch["name"] = a["name"]
    moved = sorted({tuple(sorted((dst if x["a"] == src else x["a"], dst if x["b"] == src else x["b"]))) for x in links} - {(dst, dst)})
    snap["moved_links"] = [list(x) for x in moved]
    mid = db.next_id("merge")
    stmts = ["UPDATE segment SET speaker = $dst WHERE speaker = $src", "UPDATE appearance SET speaker = $dst WHERE speaker = $src",
             "UPDATE mentions SET speaker = $dst WHERE speaker = $src", "DELETE suggestion WHERE speaker = $src OR candidate = $src",
             "DELETE same_as WHERE in = $srcr OR out = $srcr", "DELETE $srcr", "CREATE $mr CONTENT $m"]
    params = {"src": src, "dst": dst, "srcr": R("speaker", src), "dstr": R("speaker", dst), "mr": R("merge", mid), "patch": patch,
              "m": {"from_id": src, "into_id": dst, "space": a["space"], "snapshot": snap, "at": store.now(), "undone": False}}
    if patch:
        stmts.append("UPDATE $dstr MERGE $patch")
    for k, (x, y) in enumerate(moved):
        stmts.append(f"RELATE $la{k}->same_as->$lb{k}")
        params.update({f"la{k}": R("speaker", x), f"lb{k}": R("speaker", y)})
    db.run(stmts, **params)
    return mid


def undo(db, merge_id):
    m = db.one("SELECT * FROM $r", r=R("merge", merge_id))
    if not m or m.get("undone"):
        raise ValueError("nothing to undo")
    sn, src, dst = m["snapshot"], m["from_id"], m["into_id"]
    d = sn["dst"]
    stmts = ["CREATE $srcr CONTENT $sp", "UPDATE $segs SET speaker = $src", "UPDATE $apps SET speaker = $src",
             "UPDATE mentions SET speaker = $src WHERE in IN $segs", "UPDATE $dstr MERGE $dpatch", "UPDATE $mr SET undone = true"]
    params = {"src": src, "srcr": R("speaker", src), "dstr": R("speaker", dst), "mr": R("merge", merge_id), "sp": {**store.clean(sn["speaker"]), "label_key": f"{sn['speaker']['space']}:{sn['speaker']['label']}"},
              "segs": [R("segment", i) for i in sn["segments"]], "apps": [R("appearance", i) for i in sn["appearances"]],
              "dpatch": {"embedding": d.get("embedding"), "n_obs": d.get("n_obs"), "name": d.get("name")}}
    for k, (x, y) in enumerate(sn.get("moved_links", [])):
        stmts.append(f"DELETE same_as WHERE in = $ma{k} AND out = $mb{k}")
        params.update({f"ma{k}": R("speaker", x), f"mb{k}": R("speaker", y)})
    for k, ln in enumerate(sn["links"]):
        stmts.append(f"RELATE $oa{k}->same_as->$ob{k}")
        params.update({f"oa{k}": R("speaker", ln["a"]), f"ob{k}": R("speaker", ln["b"])})
    if sn["suggestions"]:
        stmts.append("INSERT INTO suggestion $sugg")
        params["sugg"] = sn["suggestions"]
    db.run(stmts, **params)


def link(db, a, b):
    ra, rb = db.one("SELECT space FROM $r", r=R("speaker", a)), db.one("SELECT space FROM $r", r=R("speaker", b))
    if not ra or not rb:
        raise ValueError("unknown speaker")
    if ra["space"] == rb["space"]:
        raise ValueError("same namespace: merge them instead")
    x, y = R("speaker", min(a, b)), R("speaker", max(a, b))
    if not db.values("SELECT VALUE id FROM same_as WHERE in = $x AND out = $y", x=x, y=y):
        db.q("RELATE $x->same_as->$y", x=x, y=y)


def list_speakers(db, nid):
    sp = db.rows("SELECT record::id(id) AS id, label, name, embedding != NONE AS has_voice FROM speaker WHERE space = $s", s=nid)
    talk = {r["speaker"]: r for r in db.rows("SELECT speaker, math::sum(dur) AS talk_ms, count() AS segments FROM segment "
                                             "WHERE space = $s AND speaker > 0 GROUP BY speaker", s=nid)}
    recs = Counter(r["speaker"] for r in db.rows("SELECT speaker, recording FROM segment WHERE space = $s AND speaker > 0 "
                                                 "GROUP BY speaker, recording", s=nid))
    names = {r["id"]: r.get("name") or r["label"] for r in sp}
    sug = {}
    for g in db.rows("SELECT speaker, candidate, score FROM suggestion WHERE space = $s", s=nid):
        if g["candidate"] in names:
            sug.setdefault(g["speaker"], []).append({"id": g["candidate"], "name": names[g["candidate"]], "score": round(g["score"], 3)})
    out = [{"id": r["id"], "label": r["label"], "name": r.get("name"), "has_voice": bool(r.get("has_voice")),
            "talk_ms": (talk.get(r["id"]) or {}).get("talk_ms", 0), "segments": (talk.get(r["id"]) or {}).get("segments", 0),
            "recordings": recs.get(r["id"], 0), "display": names[r["id"]], "suggestions": sug.get(r["id"], [])} for r in sp]
    return sorted(out, key=lambda x: (-x["talk_ms"], x["id"]))


def cross_namespace_matches(db, cfg, nids):
    """Likely same voice in two different namespaces: shown as edges, never merged."""
    rows = db.rows("SELECT record::id(id) AS id, space, embedding FROM speaker WHERE space IN $s AND embedding != NONE", s=list(nids)) if nids else []
    rows = [(r["id"], r["space"], np.asarray(r["embedding"], dtype=np.float64)) for r in rows]
    hi, out = cfg["speakers"]["match_threshold"], []
    for i, (a, na, ea) in enumerate(rows):
        for b, nb, eb in rows[i + 1:]:
            if na != nb and ea.shape == eb.shape:
                sc = float(np.dot(ea, eb))
                if sc >= hi:
                    out.append((a, b, sc))
    return out
