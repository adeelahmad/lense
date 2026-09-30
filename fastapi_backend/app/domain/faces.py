"""People on screen: face tracks per recording and, where a namespace turns recognition on, a registry of face
identities matched across its recordings the way voices are (auto-match, review, new).

Off by default. An owner can choose detect (boxes and screen time, no identities or face descriptors) or recognize
(identities; the purpose is recorded). Turning recognition off deletes the namespace's face descriptors. Face data can
be deleted per person or per namespace, and is never published through IIIF unless video.publish_faces is on.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from . import speakers as spk, store, video

R = store.R
MODES = ("off", "detect", "recognize")


def mode(db, sid):
    return (db.one("SELECT faces_mode FROM $s", s=R("space", sid)) or {}).get("faces_mode") or "off"


def set_mode(db, sid, new, purpose=None, user=None, cfg=None):
    """off, detect or recognize. Turning faces off deletes all face data (with cfg, the crops on disk too)."""
    if new not in MODES:
        raise ValueError(f"face mode is one of {', '.join(MODES)}")
    if new == "recognize" and not (purpose or "").strip():
        raise ValueError("say what face recognition is for in this namespace")
    db.q("UPDATE $s MERGE $p", s=R("space", sid), p=store.clean({"faces_mode": new, "faces_purpose": (purpose or "").strip() or None,
                                                                 "faces_set_by": user, "faces_set_at": store.now()}))
    if new != "recognize":  # descriptors only exist while recognition is on
        db.q("UPDATE face SET embedding = NONE WHERE space = $s", s=sid)
        db.q("UPDATE face_track SET embedding = NONE WHERE space = $s", s=sid)
    if new == "off":
        delete_namespace(db, cfg, sid, keep_mode=True)


def _vec(v):
    return [float(x) for x in np.asarray(v).ravel()]


def new_face(db, sid, emb=None, weight=0.0, name=None):
    n = len(db.values("SELECT VALUE id FROM face WHERE space = $s", s=sid)) + 1
    while db.values("SELECT VALUE id FROM face WHERE label_key = $k", k=f"{sid}:Face {n}"):
        n += 1
    fid = db.next_id("face")
    db.q("CREATE $r CONTENT $d", r=R("face", fid), d=store.clean({"space": sid, "label": f"Face {n}", "label_key": f"{sid}:Face {n}", "name": name,
                                                                  "embedding": _vec(emb) if emb is not None else None, "n_obs": float(weight),
                                                                  "created_at": store.now()}))
    return fid


def match(db, cfg, sid, prints):
    """prints {local: (embedding, seconds on screen)} -> {local: (face id, score, how)}; same approach as voices."""
    hi, lo = cfg["video"]["face_match_threshold"], cfg["video"]["face_review_threshold"]
    reg = [(r["id"], np.asarray(r["embedding"], dtype=np.float64)) for r in
           db.rows("SELECT record::id(id) AS id, embedding FROM face WHERE space = $s AND embedding != NONE", s=sid)]
    pairs = sorted(((float(np.dot(e, v)), l, fid) for l, (e, _) in prints.items() for fid, v in reg if v.shape == e.shape), reverse=True)
    out, used = {}, set()
    for sim, l, fid in pairs:
        if sim < hi:
            break
        if l in out or fid in used:
            continue
        out[l], _ = (fid, sim, "face"), used.add(fid)
        row = db.one("SELECT embedding, n_obs FROM $r", r=R("face", fid))
        n, e = row.get("n_obs") or 0, prints[l][0]
        c = np.asarray(row["embedding"]) * n + e * prints[l][1]
        db.q("UPDATE $r SET embedding = $e, n_obs = $n", r=R("face", fid), e=_vec(c / (np.linalg.norm(c) + 1e-9)), n=float(n + prints[l][1]))
    for l, (e, w) in prints.items():
        if l in out:
            continue
        fid = new_face(db, sid, e, w)
        out[l] = (fid, None, "new")
        near = [(sc, c) for sc, ll, c in pairs if ll == l and lo <= sc < hi]
        if near:
            db.q("CREATE face_suggestion CONTENT $d", d={"kind": "face", "face": fid, "candidate": near[0][1], "score": near[0][0], "space": sid})
    return out


def _spans(times, step):
    out = []
    for t in sorted(times):
        if out and t - out[-1][1] <= step * 2:
            out[-1][1] = t + step
        else:
            out.append([t, t + step])
    return out


def _iou(a, b):
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0, min(ax2, bx2) - max(a[0], b[0])), max(0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter + 1e-9)


def _crop(cfg, rid, det, name):
    try:
        from PIL import Image
        img = Image.open(video.frames_dir(cfg, rid) / det["frame"])
        W, H = img.size
        x, y, w, h = det["box"]
        m = 0.25
        box = (max(0, int((x - w * m) * W)), max(0, int((y - h * m) * H)), min(W, int((x + w * (1 + m)) * W)), min(H, int((y + h * (1 + m)) * H)))
        img.crop(box).resize((160, int(160 * (box[3] - box[1]) / max(1, box[2] - box[0]))), Image.LANCZOS).save(video.frames_dir(cfg, rid) / name, quality=88)
        return name
    except (OSError, ValueError, ZeroDivisionError):
        return None


def clear_recording(db, cfg, rid):
    db.q("DELETE face_track WHERE recording = $r", r=rid)
    for f in video.frames_dir(cfg, rid).glob("face-*.jpg"):
        f.unlink(missing_ok=True)


def store_tracks(db, cfg, rid, sid, dets, mode_, step, say):
    clear_recording(db, cfg, rid)
    if not dets:
        return say("no faces found")
    if mode_ == "recognize" and len(dets) > 1:
        labels = spk.cluster(np.stack([d["embedding"] for d in dets]), cfg["video"]["face_cluster_threshold"])
    elif mode_ == "recognize":
        labels = [0]
    else:  # detect only: follow boxes from one sample to the next, no descriptors kept
        labels, last, nxt = [], [], 0
        for d in sorted(dets, key=lambda d: d["t"]):
            best = max(((l, _iou(d["box"], b)) for l, t, b in last if 0 < d["t"] - t <= step * 2), key=lambda x: x[1], default=(None, 0))
            lab = best[0] if best[1] >= 0.3 else nxt
            nxt += lab == nxt
            labels.append(lab)
            last = [(l, t, b) for l, t, b in last if d["t"] - t <= step * 2] + [(lab, d["t"], d["box"])]
        dets = sorted(dets, key=lambda d: d["t"])
    groups = defaultdict(list)
    for d, lab in zip(dets, labels):
        groups[int(lab)].append(d)
    tracks = []
    for n, (lab, g) in enumerate(sorted(groups.items(), key=lambda kv: min(d["t"] for d in kv[1]))):
        spans = _spans({d["t"] for d in g}, step)
        best = max(g, key=lambda d: d["score"] * d["box"][2] * d["box"][3])
        cen = None
        if mode_ == "recognize":
            c = np.mean(np.stack([d["embedding"] for d in g]), axis=0)
            cen = c / (np.linalg.norm(c) + 1e-9)
        tracks.append({"local": f"P{n + 1}", "spans": spans, "screen_ms": sum(b - a for a, b in spans), "first_ms": spans[0][0],
                       "cover": _crop(cfg, rid, best, f"face-{n + 1}.jpg"), "centroid": cen,
                       "boxes": [[d["t"]] + [round(x, 4) for x in d["box"]] for d in sorted(g, key=lambda d: d["t"])][:500],
                       "score": round(float(np.mean([d["score"] for d in g])), 3)})
    ids = match(db, cfg, sid, {t["local"]: (t["centroid"], t["screen_ms"] / 1000) for t in tracks}) if mode_ == "recognize" else {}
    rows = [store.clean({"recording": rid, "space": sid, "local": t["local"], "face": ids.get(t["local"], (None,))[0], "spans": t["spans"],
                         "screen_ms": t["screen_ms"], "first_ms": t["first_ms"], "cover": t["cover"], "boxes": t["boxes"], "score": t["score"],
                         "method": mode_, "match": ids.get(t["local"], (None, None, None))[2], "embedding": _vec(t["centroid"]) if t["centroid"] is not None else None})
            for t in tracks]
    db.q("INSERT INTO face_track $rows", rows=rows)
    if ids:
        _suggest_speakers(db, rid, sid)
    say(f"{len(tracks)} face(s) on screen" + (f", {sum(1 for x in ids.values() if x[2] == 'face')} recognised" if ids else " (detect only)"))


def _suggest_speakers(db, rid, sid):
    """A face that's on screen while one speaker talks is probably that speaker."""
    talk = defaultdict(list)
    for s in db.rows("SELECT speaker, t0, t1 FROM segment WHERE recording = $r AND speaker > 0", r=rid):
        talk[s["speaker"]].append((s["t0"], s["t1"]))
    for tr in db.rows("SELECT face, spans, screen_ms FROM face_track WHERE recording = $r AND face > 0", r=rid):
        linked = (db.one("SELECT speaker FROM $f", f=R("face", tr["face"])) or {}).get("speaker")
        if linked or not talk:
            continue
        best = max(((s, sum(max(0, min(b, d) - max(a, c)) for a, b in tr["spans"] for c, d in ranges)) for s, ranges in talk.items()), key=lambda x: x[1])
        spoken = sum(d - c for c, d in talk[best[0]])
        ratio = best[1] / max(1, min(tr["screen_ms"], spoken))
        if ratio >= 0.6:
            db.q("UPSERT $r CONTENT $d", r=R("face_suggestion", f"speaker-{tr['face']}-{best[0]}"),
                 d={"kind": "speaker", "face": tr["face"], "speaker": best[0], "score": round(ratio, 3), "space": sid})


def list_faces(db, sid):
    rows = db.rows("SELECT record::id(id) AS id, label, name, speaker, embedding != NONE AS has_print, created_at FROM face WHERE space = $s", s=sid)
    stats = defaultdict(lambda: {"screen_ms": 0, "recordings": set(), "cover": None})
    for t in db.rows("SELECT face, recording, screen_ms, cover FROM face_track WHERE space = $s AND face > 0", s=sid):
        x = stats[t["face"]]
        x["screen_ms"] += t.get("screen_ms") or 0
        x["recordings"].add(t["recording"])
        x["cover"] = x["cover"] or ({"recording": t["recording"], "file": t["cover"]} if t.get("cover") else None)
    names = {r["id"]: r.get("name") or r["label"] for r in rows}
    spk_names = spk.list_speakers(db, sid)
    spk_names = {s["id"]: s["display"] for s in spk_names}
    sugg = defaultdict(list)
    for g in db.rows("SELECT kind, face, candidate, speaker, score FROM face_suggestion WHERE space = $s", s=sid):
        if g["kind"] == "face" and g.get("candidate") in names:
            sugg[g["face"]].append({"kind": "face", "id": g["candidate"], "name": names[g["candidate"]], "score": round(g["score"], 3)})
        elif g["kind"] == "speaker" and g.get("speaker") in spk_names:
            sugg[g["face"]].append({"kind": "speaker", "id": g["speaker"], "name": spk_names[g["speaker"]], "score": g["score"]})
    out = [{"id": r["id"], "label": r["label"], "name": r.get("name"), "display": names[r["id"]], "speaker": r.get("speaker"),
            "speaker_name": spk_names.get(r.get("speaker")), "has_print": bool(r.get("has_print")), "screen_ms": stats[r["id"]]["screen_ms"],
            "recordings": len(stats[r["id"]]["recordings"]), "cover": stats[r["id"]]["cover"], "suggestions": sugg.get(r["id"], [])} for r in rows]
    return sorted(out, key=lambda x: (-x["screen_ms"], x["id"]))


def rename(db, fid, name):
    db.q("UPDATE $r SET name = $n", r=R("face", fid), n=(name or "").strip()[:80] or None)


def link_speaker(db, fid, speaker):
    f = db.one("SELECT space FROM $r", r=R("face", fid))
    if speaker is not None:
        s = db.one("SELECT space FROM $r", r=R("speaker", int(speaker)))
        if not f or not s or s["space"] != f["space"]:
            raise ValueError("the speaker must be in the same namespace")
    db.q("UPDATE $r SET speaker = $s", r=R("face", fid), s=speaker)
    db.q("DELETE face_suggestion WHERE kind = 'speaker' AND face = $f", f=fid)


def dismiss(db, fid, kind, other):
    field = "candidate" if kind == "face" else "speaker"
    db.q(f"DELETE face_suggestion WHERE kind = $k AND face = $f AND {field} = $o", k=kind, f=fid, o=int(other))


def merge(db, src, dst, user=None):
    a, b = db.one("SELECT * FROM $r", r=R("face", src)), db.one("SELECT * FROM $r", r=R("face", dst))
    if not a or not b or src == dst or a["space"] != b["space"]:
        raise ValueError("pick two different faces in the same namespace")
    snap = {"face": {k: a.get(k) for k in ("space", "label", "label_key", "name", "embedding", "n_obs", "speaker", "created_at")},
            "dst": {k: b.get(k) for k in ("embedding", "n_obs", "name", "speaker")},
            "tracks": db.values("SELECT VALUE record::id(id) FROM face_track WHERE face = $f", f=src)}
    patch = {}
    if a.get("embedding") and b.get("embedding"):
        c = np.asarray(a["embedding"]) * (a.get("n_obs") or 1) + np.asarray(b["embedding"]) * (b.get("n_obs") or 1)
        patch = {"embedding": _vec(c / (np.linalg.norm(c) + 1e-9)), "n_obs": float((a.get("n_obs") or 0) + (b.get("n_obs") or 0))}
    if not b.get("name") and a.get("name"):
        patch["name"] = a["name"]
    if not b.get("speaker") and a.get("speaker"):
        patch["speaker"] = a["speaker"]
    mid = db.next_id("face_merge")
    stmts = ["UPDATE face_track SET face = $dst WHERE face = $src", "DELETE face_suggestion WHERE face = $src OR candidate = $src", "DELETE $sr",
             "CREATE $mr CONTENT $m"] + (["UPDATE $dr MERGE $patch"] if patch else [])
    db.run(stmts, src=src, dst=dst, sr=R("face", src), dr=R("face", dst), mr=R("face_merge", mid), patch=patch,
           m={"src": src, "dst": dst, "space": a["space"], "snapshot": snap, "by": user, "at": store.now(), "undone": False})
    return mid


def undo(db, mid):
    m = db.one("SELECT * FROM $r", r=R("face_merge", int(mid)))
    if not m or m.get("undone"):
        raise ValueError("nothing to undo")
    sn = m["snapshot"]
    db.run(["CREATE $sr CONTENT $f", "UPDATE $tracks SET face = $src", "UPDATE $dr MERGE $dpatch", "UPDATE $mr SET undone = true"],
           sr=R("face", m["src"]), f=store.clean(sn["face"]), tracks=[R("face_track", t) for t in sn["tracks"]], src=m["src"],
           dr=R("face", m["dst"]), dpatch=sn["dst"], mr=R("face_merge", int(mid)))


def delete_face(db, cfg, fid):
    """Remove a person's face data: the identity, its descriptor, and its tracks and crops."""
    for t in db.rows("SELECT recording, cover FROM face_track WHERE face = $f", f=fid):
        if t.get("cover"):
            (video.frames_dir(cfg, t["recording"]) / t["cover"]).unlink(missing_ok=True)
    db.run(["DELETE face_track WHERE face = $f", "DELETE face_suggestion WHERE face = $f OR candidate = $f", "DELETE $r"], f=fid, r=R("face", fid))


def delete_namespace(db, cfg, sid, keep_mode=False):
    if cfg:
        for t in db.rows("SELECT recording, cover FROM face_track WHERE space = $s", s=sid):
            if t.get("cover"):
                (video.frames_dir(cfg, t["recording"]) / t["cover"]).unlink(missing_ok=True)
    db.run(["DELETE face_track WHERE space = $s", "DELETE face_suggestion WHERE space = $s", "DELETE face WHERE space = $s", "DELETE face_merge WHERE space = $s"], s=sid)
    if not keep_mode:
        db.q("UPDATE $r SET faces_mode = 'off'", r=R("space", sid))


def tracks_for(db, rid):
    rows = db.rows("SELECT record::id(id) AS id, local, face, spans, screen_ms, first_ms, cover, boxes, score, method, match FROM face_track "
                   "WHERE recording = $r ORDER BY first_ms", r=rid)
    names = {f["id"]: f.get("name") or f["label"] for f in db.rows("SELECT record::id(id) AS id, name, label FROM face WHERE id IN $ids",
                                                                   ids=[R("face", t["face"]) for t in rows if t.get("face")])} if rows else {}
    for t in rows:
        t["name"] = names.get(t.get("face")) or f"Person {t['local'][1:]}"
    return rows
