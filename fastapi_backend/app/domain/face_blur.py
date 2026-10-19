"""Faces blurred for sharing (docs/video.md, Blurring faces for sharing): a copy of a video with the faces found in it
blurred (all of them, the ones nobody has named, or the ones a person picks), to download or to show visitors.

Nothing happens until someone asks (the API, MCP or the People tab): a video keeps playing as it is. Asking queues a
`blur_faces` step after what the asset's job still has to do; it is a heavy step (quiet.py), so Settings › Workers keeps
it to its hours and to so many at a time. ffmpeg draws a blur over each face track's boxes over time. The faces step
saw each face only on the frames it sampled (every sample_ms), so between two samples that saw it the blur covers both
boxes, it starts a sample before the first and ends a sample after the last, and a margin goes round each box: the
copy errs on blurring too much, never too little. A video with many faces has its boxes merged into fewer, larger ones,
so the filter graph stays small. The sound is kept; what the file says about itself (its title, where a phone recorded
it) is left out.

The copy is one of the asset's supplementary files (files.py): an attachment labelled "Faces blurred", in the Files
tab with the rest. The asset keeps which file it is (`face_blur`); asking again replaces it. An owner can have
visitors (public pages, embeds, share links, IIIF, podcast feeds) play the copy instead of the original
(`face_blur.visitors`); members still play the original. Should the copy go missing while that's on, visitors get
nothing rather than the faces; deleting the file (Files tab) takes the choice with it.

Refine later: blurring a document's or an image's pages; following a face between samples by tracking it frame by
frame instead of covering both boxes; a podcast feed's enclosure giving the copy's size; the in-app assistant asking
for a copy.
"""

from __future__ import annotations

import pathlib
import subprocess
import tempfile
import time

from . import faces, files, ingest, quiet, store

R = store.R
STEP = "blur_faces"
WHO = ("all", "unnamed", "chosen")  # every face, the ones nobody has named, or the tracks picked
LABEL = "Faces blurred"
MARGIN = faces.MARGIN  # of a box's size around it, blurred too
SMALLEST = 0.02  # of the picture: a box is never narrower or shorter (ffmpeg crops in whole pairs of pixels)
MOST = 300  # boxes in one filter graph; more are merged two by two (each covering more) until there are no more
SECONDS = 6 * 3600  # the longest ffmpeg may take over one copy
SAY_EVERY = 10  # percent: the job's log says how far it got this often


class Missing(LookupError):
    """Visitors are to play the copy with the faces blurred, and it isn't there."""


# ---------- what to blur ----------
def chosen(db, rid, who="all", tracks=None):
    """The video's face tracks to blur, in the order they first appear: every one, the ones whose face nobody has
    named, or the ones picked (`tracks`, their ids). ValueError for any other `who`."""
    if who not in WHO:
        raise ValueError(f"say whose faces to blur: {', '.join(WHO)}")
    rows = [
        r
        for r in db.rows(
            "SELECT record::id(id) AS id, face, boxes, paged, first_ms FROM face_track WHERE recording = $r ORDER BY first_ms", r=int(rid)
        )
        if not r.get("paged")
    ]
    if who == "unnamed":
        ids = [R("face", t["face"]) for t in rows if t.get("face")]
        named = set(db.values("SELECT VALUE record::id(id) FROM face WHERE id IN $ids AND name != NONE", ids=ids)) if ids else set()
        rows = [t for t in rows if t.get("face") not in named]
    elif who == "chosen":
        want = {str(t) for t in tracks or []}
        rows = [t for t in rows if str(t["id"]) in want]
    return rows


def _run(run, step):
    """One stretch of a track (samples no more than two steps apart, as faces.spans bridges them) as pieces
    [t0, t1, x0, y0, x1, y1] in ms and fractions: each pair of samples covered by both boxes, a step either side."""
    boxes = [(b[0], b[1], b[2], b[1] + b[3], b[2] + b[4]) for b in run]
    if len(boxes) == 1:
        t, x0, y0, x1, y1 = boxes[0]
        return [[t - step, t + step, x0, y0, x1, y1]]
    out = [[a[0], b[0], min(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]), max(a[4], b[4])] for a, b in zip(boxes, boxes[1:])]
    out[0][0] -= step
    out[-1][1] += step
    return out


def _union(pieces):
    return [
        min(p[0] for p in pieces),
        max(p[1] for p in pieces),
        min(p[2] for p in pieces),
        min(p[3] for p in pieces),
        max(p[4] for p in pieces),
        max(p[5] for p in pieces),
    ]


def _pad(p, margin):
    t0, t1, x0, y0, x1, y1 = p
    w, h = x1 - x0, y1 - y0
    x0, y0 = max(0.0, x0 - w * margin), max(0.0, y0 - h * margin)
    x1, y1 = min(1.0, x1 + w * margin), min(1.0, y1 + h * margin)
    w, h = max(SMALLEST, x1 - x0), max(SMALLEST, y1 - y0)
    x0, y0 = min(x0, 1.0 - w), min(y0, 1.0 - h)
    return (round(max(0, t0) / 1000, 3), round(t1 / 1000, 3), round(x0, 4), round(y0, 4), round(w, 4), round(h, 4))


def regions(tracks, step_ms, margin=MARGIN, most=MOST):
    """Where and when to blur: [(from s, to s, x, y, w, h)], the box in fractions of the picture, for these tracks'
    boxes ([t ms, x, y, w, h] each, as the faces step keeps them). At most `most` of them while each track has more
    than one: past that, a track's neighbouring pieces are merged, each covering both."""
    per = []
    for tr in tracks:
        pieces, run = [], []
        for b in sorted((b for b in tr.get("boxes") or [] if len(b) >= 5), key=lambda b: b[0]):
            if run and b[0] - run[-1][0] > step_ms * 2:
                pieces += _run(run, step_ms)
                run = []
            run.append(b)
        if run:
            pieces += _run(run, step_ms)
        per.append(pieces)
    while sum(len(p) for p in per) > most and any(len(p) > 1 for p in per):
        per = [[_union(p[i : i + 2]) for i in range(0, len(p), 2)] for p in per]
    return [_pad(p, margin) for pieces in per for p in pieces]


def graph(regs):
    """The ffmpeg filter graph blurring these regions of input 0's video, its output labelled [v]: one crop, blur and
    overlay each, switched on only for its time."""
    n = len(regs)
    parts = [f"[0:v]format=yuv420p,split={n + 1}[m0]" + "".join(f"[c{i}]" for i in range(1, n + 1))]
    for i, (t0, t1, x, y, w, h) in enumerate(regs, 1):
        on = f"enable='between(t,{t0:.3f},{t1:.3f})'"
        parts.append(
            f"[c{i}]crop=w=iw*{w:.4f}:h=ih*{h:.4f}:x=iw*{x:.4f}:y=ih*{y:.4f},"
            f"boxblur=luma_radius='min(w,h)/3':luma_power=3:chroma_radius='min(cw,ch)/3':chroma_power=3:{on}[b{i}]"
        )
        parts.append(f"[m{i - 1}][b{i}]overlay=x=main_w*{x:.4f}:y=main_h*{y:.4f}:{on}[{'v' if i == n else f'm{i}'}]")
    return ";".join(parts)


# ---------- asking for a copy ----------
def latest(db, rid):
    """The latest job to blur the asset's faces: {id, status, error, step: whether it's on that step now}, or None."""
    for j in db.rows(
        "SELECT record::id(id) AS id, status, steps, step_index, next_step, error FROM job WHERE recording = $r ORDER BY id DESC LIMIT 20",
        r=int(rid),
    ):
        if any(isinstance(s, dict) and s.get("type") == STEP for s in j.get("steps") or []):
            return {"id": j["id"], "status": j["status"], "error": j.get("error"), "on_step": j.get("next_step") == STEP}
    return None


def queue(db, rid, who="all", tracks=None, by=None, by_id=None):
    """Make a copy of the video with these faces blurred, in the background: a `blur_faces` step after what its job
    still has to do (jobs.add_steps), or a new job; the job already blurring them if there is one. ValueError when it
    isn't a video, or has no faces (of those asked for) to blur; KeyError for no such asset."""
    from . import jobs

    rec = db.one("SELECT source, media FROM $r", r=R("recording", int(rid)))
    if rec is None:
        raise KeyError(rid)
    if rec.get("source") != "audio" or (rec.get("media") or {}).get("kind") != "video":
        raise ValueError("Only a video's faces are blurred so far.")
    picked = chosen(db, rid, who, tracks)
    if not picked:
        raise ValueError("There are no faces to blur: pick at least one." if who == "chosen" else "No faces were found to blur.")
    spec = store.clean({"type": STEP, "who": who, "tracks": [t["id"] for t in picked] if who == "chosen" else None, "by": by_id})
    j = latest(db, rid)
    if j and j["status"] in jobs.ACTIVE:
        return j["id"]
    return jobs.add_steps(db, int(rid), [spec], by=by)


# ---------- the job step ----------
def _name(rec):
    stem = pathlib.PurePosixPath(str((rec.get("remote") or {}).get("path") or rec.get("path") or "")).stem
    return files.clean_name(f"{stem or rec.get('title') or 'video'}-faces-blurred.mp4")


def _ffmpeg(src, regs, dest, duration_ms, progress):
    """Run ffmpeg over `src` into `dest` (MP4: H.264 with the faces blurred, AAC sound, no metadata), calling
    progress(percent) as it goes. RuntimeError with ffmpeg's last words when it fails."""
    argv = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-nostats", "-progress", "pipe:1"]
    argv += ["-format_whitelist", ingest.MEDIA_DEMUXERS, "-i", str(src), "-filter_complex", graph(regs)]
    argv += ["-map", "[v]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    argv += ["-c:a", "aac", "-b:a", "160k", "-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart", "-f", "mp4", str(dest)]
    with tempfile.TemporaryFile("w+") as err:
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=err, text=True)
        except OSError as e:  # no ffmpeg
            raise RuntimeError(f"couldn't blur the faces: {e}") from None
        start = time.monotonic()
        assert proc.stdout is not None
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and value.isdigit() and duration_ms:  # both are in microseconds
                progress(min(99, int(int(value) / 10 / duration_ms)))
            if time.monotonic() - start > SECONDS:
                proc.kill()
                break
        code = proc.wait()
        err.seek(0)
        last = (err.read().strip().splitlines() or ["ffmpeg took too long" if code < 0 else "ffmpeg failed"])[-1]
    if code != 0 or not pathlib.Path(dest).is_file():
        raise RuntimeError(f"couldn't blur the faces: {last}")


def step(db, cfg, rid, say, spec=None):
    """The `blur_faces` step: the copy made, kept as the asset's file, and the asset told which file it is."""
    from . import jobs

    spec = spec or {}
    rec = (
        db.one("SELECT space, source, path, remote, media, title, sample_ms, duration_ms, face_blur FROM $r", r=R("recording", int(rid)))
        or {}
    )
    if rec.get("source") != "audio" or (rec.get("media") or {}).get("kind") != "video":
        raise jobs.Skip("only a video's faces are blurred")
    who = spec.get("who") or "all"
    tracks = chosen(db, rid, who, spec.get("tracks"))
    if not tracks:
        raise jobs.Skip("no faces to blur: the faces step found none, or none of those picked are left")
    regs = regions(tracks, rec.get("sample_ms") or 5000)
    say(f"blurring {len(tracks)} face(s) in {len(regs)} place(s)")
    src, tmp = ingest.audio_path(db, cfg, rec), files.incoming(cfg)
    said, kept = [0], [None]

    def progress(pct):
        if pct == kept[0]:  # ffmpeg says where it is twice a second
            return
        kept[0] = pct
        db.q("UPDATE $r SET face_blur_progress = $p", r=R("recording", int(rid)), p=pct)
        if pct >= said[0] + SAY_EVERY:
            said[0] = pct - pct % SAY_EVERY
            say(f"  {said[0]}%")

    try:
        _ffmpeg(src, regs, tmp, rec.get("duration_ms"), progress)
        old = (rec.get("face_blur") or {}).get("file")
        about = _described(db, rid, who, tracks)
        f = files.add(db, cfg, rid, tmp, _name(rec), "attachment", label=LABEL, description=about, by=spec.get("by"))
    finally:
        tmp.unlink(missing_ok=True)
        db.q("UPDATE $r SET face_blur_progress = NONE", r=R("recording", int(rid)))
    blur = {
        "file": f["id"],
        "who": who,
        "tracks": [t["id"] for t in tracks],
        "faces": len(tracks),
        "made_at": store.now(),
        "by": spec.get("by"),
        "visitors": bool((rec.get("face_blur") or {}).get("visitors")),  # an owner's choice outlasts a new copy
    }
    db.q("UPDATE $r SET face_blur = $b", r=R("recording", int(rid)), b=store.clean(blur))
    if old and old != f["id"]:
        try:
            files.delete(db, cfg, rid, old)
        except KeyError:  # deleted already
            pass
    say(f"blurred {len(tracks)} face(s) in a copy kept as {f['name']} ({f['size'] // 1024} KB)")


def _described(db, rid, who, tracks):
    """The copy's description in the Files tab: whose faces are blurred."""
    names = {t["id"]: t["name"] for t in faces.tracks_for(db, int(rid))}
    whose = {"all": "every face found", "unnamed": "the faces nobody has named", "chosen": "the faces picked"}[who]
    listed = ", ".join(names[t["id"]] for t in tracks if t["id"] in names)
    return f"A copy with {whose} blurred ({len(tracks)})" + (f": {listed}." if listed else ".")


# ---------- what there is, and visitors ----------
def status(db, cfg, rid):
    """The copy with the faces blurred, if there is one ({file, name, size, download, faces, who, tracks, made_at,
    visitors}), and the latest job to make one ({id, status, error, progress, waits})."""
    rec = db.one("SELECT face_blur, face_blur_progress FROM $r", r=R("recording", int(rid))) or {}
    blur, copy = rec.get("face_blur") or {}, None
    if blur.get("file"):
        try:
            f = files.get(db, rid, blur["file"])
            copy = {
                **{k: blur.get(k) for k in ("who", "tracks", "faces", "made_at", "visitors")},
                "file": f["id"],
                "name": f["name"],
                "size": f.get("size"),
                "download": f"{store.API}/recordings/{int(rid)}/files/{f['id']}/download",
            }
        except KeyError:
            pass
    job = latest(db, rid)
    if job:
        on_step = job.pop("on_step")
        job["progress"] = rec.get("face_blur_progress") if job["status"] == "running" and on_step else None
        job["waits"] = quiet.why(cfg) if job["status"] == "queued" and STEP in quiet.held(db, cfg) else None
    return {"blurred": copy, "job": job}


def set_visitors(db, rid, on):
    """Have visitors play the copy with the faces blurred instead of the original, or not. ValueError without a copy."""
    blur = (db.one("SELECT face_blur FROM $r", r=R("recording", int(rid))) or {}).get("face_blur") or {}
    if on and not blur.get("file"):
        raise ValueError("Make a copy with the faces blurred first.")
    if blur:
        db.q("UPDATE $r SET face_blur.visitors = $on", r=R("recording", int(rid)), on=bool(on))


def for_visitors(db, cfg, rid):
    """What a visitor plays instead of the original, when an owner chose that: (path, media type); None when they
    didn't. Missing when they did and the copy isn't there: visitors then get nothing rather than the faces."""
    blur = (db.one("SELECT face_blur FROM $r", r=R("recording", int(rid))) or {}).get("face_blur") or {}
    if not blur.get("visitors"):
        return None
    try:
        path = files.path_of(cfg, files.get(db, rid, blur["file"]))
    except KeyError:
        raise Missing(rid) from None
    if not path.is_file():
        raise Missing(rid)
    return str(path), "video/mp4"


def forget_file(db, rid, fid):
    """The copy's file was deleted: the asset no longer has one, and visitors no longer play it."""
    db.q("UPDATE $r SET face_blur = NONE WHERE face_blur.file = $f", r=R("recording", int(rid)), f=int(fid))
