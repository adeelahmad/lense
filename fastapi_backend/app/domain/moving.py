"""Moving a recording to another namespace (docs/api.md).

It keeps its transcript, media, frames, outputs, notes, supplementary files, the people given permission on it and,
unless they are revoked, its share links. Its IIIF manifest stays as it was: what it had from its old namespace (the default access and
open parts, and the metadata profile's defaults) is pinned on the recording wherever the new namespace would change it,
and the change is kept in its metadata history.

What belongs to a namespace follows it. Its speakers and faces are matched by name in the new namespace, or start
there with this recording's voice and face; unnamed ones left with nothing in the old namespace are removed there.
Its entities are found again by analysis, which runs in the new namespace (after identifying the speakers again from
their voices, when asked). The old namespace keeps a note of the file (domain/deletion.py), so its scans and watched
folders don't import it again.
"""

from __future__ import annotations

import os
import pathlib
import shutil

from . import access as acc, auth, deletion, faces as facemod, jobs, metadata as md, render, speakers as spk, store

R = store.R
# rows that belong to the recording and say which namespace they are in
SPACED = (
    "segment",
    "appearance",
    "section",
    "shot",
    "ocr_span",
    "face_track",
    "object_track",
    "description",
    "job",
    "output",
    "share_link",
    "note",
    "resource_file",
    "file_line",
    "page",
)


class Conflict(RuntimeError):
    """The new namespace already has the same file."""


def _pins(db, cfg, rid, src, dst):
    """What the recording had from its old namespace that the new one would change: its default access and open parts,
    and the metadata profile's defaults. Saved on the recording, they keep its manifest as it was."""
    before, own = md.defaults(db, cfg, rid), md.stored(db, rid)
    level, open_ = acc.namespace_defaults(db, [dst]).get(dst, ("private", list(acc.PARTS)))
    old_profile = md.namespace(db, src)["profile"].get("defaults") or {}
    new_profile = md.namespace(db, dst)["profile"].get("defaults") or {}
    after = {"access": level, "open": open_, **{k: v for k, v in new_profile.items() if v is not None}}
    keys = {"access", "open"} | set(old_profile) | set(new_profile)
    return {k: before[k] for k in sorted(keys) if k not in own and before.get(k) is not None and before[k] != after.get(k)}


def _speakers(db, rid, dst):
    """{speaker here: speaker there}: matched by name, else a new speaker with this recording's voice."""
    apps = db.rows("SELECT speaker, embedding FROM appearance WHERE recording = $r", r=rid)
    ids = {a["speaker"] for a in apps if a.get("speaker")} | {
        s for s in db.values("SELECT VALUE speaker FROM segment WHERE recording = $r AND speaker > 0", r=rid) if s
    }
    voice = {a["speaker"]: a["embedding"] for a in apps if a.get("speaker") and a.get("embedding") is not None}
    out = {}
    for sid in sorted(ids):
        row = db.one("SELECT name, embedding FROM $s", s=R("speaker", sid)) or {}
        name, emb = row.get("name"), voice.get(sid, row.get("embedding"))
        found = (
            db.one("SELECT record::id(id) AS id FROM speaker WHERE space = $d AND (name = $n OR label = $n) LIMIT 1", d=dst, n=name)
            if name
            else None
        )
        out[sid] = found["id"] if found else spk.new_speaker(db, dst, name=name, emb=emb, weight=1.0 if emb is not None else 0.0)
    return out


def _faces(db, rid, dst, speakers):
    """{face here: face there}: matched by name, else a new face with this recording's look, linked to the speaker it
    was linked with where that speaker came along."""
    tracks = db.rows("SELECT face, embedding FROM face_track WHERE recording = $r AND face > 0", r=rid)
    out = {}
    for fid in sorted({t["face"] for t in tracks}):
        row = db.one("SELECT name, embedding, speaker FROM $f", f=R("face", fid)) or {}
        emb = next((t["embedding"] for t in tracks if t["face"] == fid and t.get("embedding") is not None), row.get("embedding"))
        name = row.get("name")
        found = db.one("SELECT record::id(id) AS id FROM face WHERE space = $d AND name = $n LIMIT 1", d=dst, n=name) if name else None
        if found:
            out[fid] = found["id"]
            continue
        out[fid] = facemod.new_face(db, dst, emb=emb, weight=1.0 if emb is not None else 0.0, name=name)
        if row.get("speaker") in speakers:
            db.q("UPDATE $f SET speaker = $s", f=R("face", out[fid]), s=speakers[row["speaker"]])
    return out


def _files(db, cfg, rec, rid, src_name, dst_name):
    """Its report pages and exports move to the new namespace's folders (not over another recording's file)."""
    data = pathlib.Path(cfg["data_dir"])
    old, new = data / "reports" / src_name, data / "reports" / dst_name
    name = f"{render.slug(rec.get('title'))}-{rid}"
    pages = [p for p in [old / f"{name}.html", *old.glob(f"{name}--*.html")] if p.is_file()]
    if pages:
        new.mkdir(parents=True, exist_ok=True)
    for p in pages:
        if not (new / p.name).exists():
            os.replace(p, new / p.name)
    old_url, new_url = f"/reports/{src_name}/", f"/reports/{dst_name}/"
    for o in db.rows("SELECT key, value FROM output WHERE recording = $r", r=rid):
        v = o.get("value")
        if isinstance(v, dict) and str(v.get("url") or "").startswith(old_url):
            v = {**v, "url": new_url + v["url"][len(old_url) :]}
            db.q("UPDATE $o MERGE $d", o=R("output", f"{rid}-{o['key']}"), d={"value": v})
    for o in db.rows("SELECT key, value FROM output WHERE recording = $r AND string::starts_with(key, 'export_')", r=rid):
        f = pathlib.PurePosixPath((o.get("value") or {}).get("file") or "").name
        here, there = data / "exports" / src_name / f, data / "exports" / dst_name / f
        shared = db.values(
            "SELECT VALUE id FROM output WHERE recording != $r AND value.file = $f AND recording IN "
            "(SELECT VALUE record::id(id) FROM recording WHERE space = $s) LIMIT 1",
            r=rid,
            f=f,
            s=rec["space"],
        )
        if f and here.is_file() and not there.exists():
            there.parent.mkdir(parents=True, exist_ok=True)
            (shutil.copy2 if shared else os.replace)(here, there)


def move(db, cfg, rid, dst, rediarize=False, revoke_shares=False, by=None, collection=None):
    """Move a recording to the namespace `dst` (see the module docstring). Returns what the audit log keeps.

    Raises KeyError (no such recording or namespace), ValueError (it's already there; identifying speakers again needs
    audio), Conflict (the new namespace has the same file) and deletion.Running (a job is working on it)."""
    rec = db.one("SELECT title, space, path, fingerprint, remote, source FROM $r", r=R("recording", rid))
    names = store.space_names(db)
    if not rec or dst not in names:
        raise KeyError(rid if not rec else dst)
    src = rec["space"]
    if src == dst:
        raise ValueError(f"It's already in {names[dst]}.")
    home = store.home(db, dst, collection)  # KeyError for a collection of another namespace
    if rediarize and rec.get("source") != "audio":
        raise ValueError("Only recordings with audio can have their speakers identified again.")
    fp = rec.get("fingerprint")
    if fp and db.values("SELECT VALUE id FROM recording WHERE fp_key = $k", k=f"{dst}:{fp}"):
        raise Conflict(f"{names[dst]} already has the same file.")
    deletion.stop_jobs(db, rid, "move")
    pins = _pins(db, cfg, rid, src, dst)
    if pins:
        md.save(db, cfg, rid, pins, user=by)
    old_speakers = set(db.values("SELECT VALUE speaker FROM appearance WHERE recording = $r", r=rid)) | {
        s for s in db.values("SELECT VALUE speaker FROM segment WHERE recording = $r AND speaker > 0", r=rid) if s
    }
    old_faces = {f for f in db.values("SELECT VALUE face FROM face_track WHERE recording = $r AND face > 0", r=rid) if f}
    if rediarize:  # the diarize step finds them again among the new namespace's voices
        speakers = {}
        db.run(
            ["DELETE appearance WHERE recording = $r", "UPDATE segment SET speaker = NONE, local_speaker = NONE WHERE recording = $r"],
            r=rid,
        )
    else:
        speakers = _speakers(db, rid, dst)
        for a, b in speakers.items():
            db.run(
                [
                    "UPDATE segment SET speaker = $b WHERE recording = $r AND speaker = $a",
                    "UPDATE appearance SET speaker = $b WHERE recording = $r AND speaker = $a",
                ],
                r=rid,
                a=a,
                b=b,
            )
    for a, b in _faces(db, rid, dst, speakers).items():
        db.q("UPDATE face_track SET face = $b WHERE recording = $r AND face = $a", r=rid, a=a, b=b)
    db.run(
        [f"UPDATE {t} SET space = $d WHERE recording = $r AND space = $s" for t in SPACED]
        + [
            # entities belong to a namespace: analysis finds them again there, and curated mentions start over
            "DELETE mentions WHERE recording = $r",
            "DELETE term WHERE recording = $r",
            "DELETE entity_override WHERE recording = $r",
            "UPDATE ip_group SET recordings = array::complement(recordings, [$r]) WHERE space = $s AND recordings CONTAINS $r",
            "CREATE $g CONTENT $note",
            "UPDATE $rec SET space = $d, collection = $home" + (", fp_key = $k" if fp else ""),
        ],
        r=rid,
        s=src,
        d=dst,
        g=R("gone_recording", db.next_id("gone_recording")),
        note=deletion.note(rec, rid, "moved", by, moved_to=dst),
        rec=R("recording", rid),
        k=f"{dst}:{fp}",
        home=home,
    )
    deletion.forget(db, dst, fp, rec.get("path"))  # it's in the new namespace now: its scans may find it again
    revoked = auth.revoke_shares(db, rid, by) if revoke_shares else 0
    _files(db, cfg, rec, rid, names[src], names[dst])
    deletion.orphans(db, old_speakers, old_faces)  # unnamed ones nothing else has any more
    job = jobs.enqueue(db, rid, (["diarize"] if rediarize else []) + ["analyze", "report"], by=by)
    md.touched(db, cfg, rid)  # harvesters see an Update: the manifest's collection changed
    return {
        "title": rec.get("title"),
        "from": names[src],
        "to": names[dst],
        "collection": home,
        "pinned": sorted(pins),
        "rediarize": bool(rediarize),
        "shares_revoked": revoked,
        "job": job,
    }
