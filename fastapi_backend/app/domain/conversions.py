"""The convert step: new assets made from one (docs/processing.md#converting-assets).

`{type: convert, to: audio}` makes a video's soundtrack an audio asset of its own, as m4a (AAC, copied as it is when
the video's sound is AAC already), mp3, opus or flac (`format`). `{type: convert, to: images}` makes each page a
document's Read step drew (or each frame of a TIFF) an image asset of its own, the JPEG Lens shows for that page.

A new asset lives in the namespace and collection of the one it was made from, says which asset and page it was made
from (converted_from), and is kept encrypted like an upload. It then runs the pipeline its content type gives it, as an
upload does, or the one the step names (`pipeline`), or none (`pipeline: "none"`): it's kept as it is until a pipeline
is run on it. Running the step again keeps the assets it made before (forced, it makes their files again). An asset
made by a convert step isn't converted again, so a pipeline that converts can't feed itself.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess

from . import documents, ingest, jobs, keyring, store, video

R = store.R
TO = ("audio", "images")
# format: (codec that is copied as it is, ffmpeg's encoder and its options)
AUDIO = {
    "m4a": ("aac", ["-c:a", "aac", "-b:a", "128k"]),
    "mp3": ("mp3", ["-c:a", "libmp3lame", "-q:a", "4"]),
    "opus": ("opus", ["-c:a", "libopus", "-b:a", "64k"]),
    "flac": ("flac", ["-c:a", "flac"]),
}
MAX_PAGES = 200  # images made from one document at most


def check(spec):
    """A convert step's settings (pipelines.validate_steps): ValueError naming the first problem."""
    if spec.get("to") not in TO:
        raise ValueError("convert step: convert to audio or images")
    fmt = spec.get("format")
    if fmt is not None and (spec["to"] != "audio" or fmt not in AUDIO):
        if spec["to"] != "audio":
            raise ValueError("convert step: images are JPEGs, with no format to choose")
        raise ValueError(f"convert step: the audio format is one of {', '.join(AUDIO)}")
    p = spec.get("pipeline")
    if p is not None and p != "none" and (isinstance(p, bool) or not isinstance(p, int)):
        raise ValueError('convert step: pipeline is a pipeline\'s id, or "none" to keep the new assets as they are')


def made_from(db, rid, to):
    """The assets a convert step made from this one before: {page or None: id}."""
    rows = db.rows(
        "SELECT record::id(id) AS id, converted_from FROM recording WHERE converted_from.resource = $r AND converted_from.to = $t",
        r=int(rid),
        t=to,
    )
    return {(r["converted_from"] or {}).get("page"): r["id"] for r in rows}


def step(db, cfg, rid, say, spec):
    rec = db.one(
        "SELECT space, collection, path, remote, source, title, recorded_at, duration_ms, converted_from FROM $r",
        r=R("recording", rid),
    )
    if not rec:
        raise KeyError(rid)
    if rec.get("converted_from"):
        raise jobs.Skip("it was made by a convert step itself, so it isn't converted again")
    if spec["to"] == "audio":
        return _audio(db, cfg, rid, rec, say, spec)
    return _images(db, cfg, rid, rec, say, spec)


def _stem(rec, rid):
    stem = pathlib.PurePosixPath(str(rec.get("path") or "")).stem or str(rec.get("title") or "")
    return re.sub(r"[^\w .()-]+", "_", stem).strip(" .")[:100] or f"asset-{int(rid)}"


def _folder(db, cfg, rec, rid):
    ns = store.space_names(db).get(rec["space"]) or str(rec["space"])
    d = pathlib.Path(cfg["data_dir"]) / "uploads" / ns / f"converted-{int(rid)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audio_codec(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-format_whitelist", ingest.MEDIA_DEMUXERS, "-select_streams", "a:0"]
        + ["-show_entries", "stream=codec_name", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    streams = json.loads(out.stdout or "{}").get("streams") or []
    return (streams[0] if streams else {}).get("codec_name")


def _audio(db, cfg, rid, rec, say, spec):
    if rec.get("source") != "audio":
        raise jobs.Skip("only a video has a soundtrack to make into audio")
    path = ingest.audio_path(db, cfg, rec)
    if not path or not os.path.exists(path):
        raise jobs.Skip("there is no media file")
    media = video.probe_media(path)
    if media["kind"] != "video":
        raise jobs.Skip("it's audio already")
    if not media.get("has_audio"):
        raise jobs.Skip("the video has no sound")
    have = made_from(db, rid, "audio").get(None)
    if have and not spec.get("force"):
        return say(f"kept the audio made from it before (asset {have}); force the step to make it again")
    fmt = spec.get("format") or "m4a"
    same, encode = AUDIO[fmt]
    codec = ["-c:a", "copy"] if _audio_codec(path) == same else encode
    dest = _folder(db, cfg, rec, rid) / f"{_stem(rec, rid)}.{fmt}"
    tmp = dest.with_name(f".part-{dest.name}")
    argv = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-format_whitelist", ingest.MEDIA_DEMUXERS, "-i", str(path)]
    # no picture, no tags: the same video makes the same file, byte for byte
    argv += ["-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact", *codec, str(tmp)]
    try:
        out = subprocess.run(argv, capture_output=True, timeout=ingest.DECODE_SECONDS)
        if out.returncode != 0:
            raise RuntimeError("ffmpeg: " + out.stderr.decode(errors="replace")[-300:])
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    keyring.protect(db, cfg, rec["space"], dest)  # encrypted like an upload, where its namespace's files are
    title = f"{rec.get('title') or _stem(rec, rid)} (audio)"
    more = {"duration_ms": media["duration_ms"] or rec.get("duration_ms")}
    new = _keep(db, cfg, rid, rec, dest, "audio", title, {"to": "audio"}, have, spec, more)
    how = "copied" if codec[-1] == "copy" else "encoded"
    say(f"made its soundtrack an audio asset ({fmt}, {how}): asset {new}")


def _images(db, cfg, rid, rec, say, spec):
    if rec.get("source") not in documents.KINDS:
        raise jobs.Skip("only a document's pages (or a TIFF's frames) are made into images")
    pages = [p for p in documents.pages(db, rid) if p.get("image")]
    if not pages:
        raise jobs.Skip("its pages haven't been drawn: put a Read step before this one")
    if rec["source"] == "image" and len(pages) < 2:
        raise jobs.Skip("it's one image already")
    if len(pages) > MAX_PAGES:
        say(f"only its first {MAX_PAGES} of {len(pages)} pages are made into images")
        pages = pages[:MAX_PAGES]
    have = made_from(db, rid, "images")
    d, frames, stem = _folder(db, cfg, rec, rid), video.frames_dir(cfg, rid), _stem(rec, rid)
    made = kept = 0
    for p in pages:
        old = have.get(p["idx"])
        if old and not spec.get("force"):
            kept += 1
            continue
        src = frames / p["image"]
        if not src.exists():
            continue
        dest = d / f"{stem}-page-{p['idx'] + 1:04d}.jpg"
        keyring.keep(db, cfg, rec["space"], dest, keyring.read_plain(db, cfg, src))  # encrypted as it's written, if wanted
        name = p.get("label") or str(p["idx"] + 1)
        title = f"{rec.get('title') or stem}, page {name}"
        _keep(db, cfg, rid, rec, dest, "image", title, {"to": "images", "page": p["idx"]}, old, spec, {"media": {"kind": "image"}})
        made += 1
    say(f"made {made} page(s) into image assets" + (f"; kept the {kept} made before (force the step to make them again)" if kept else ""))


def _keep(db, cfg, rid, rec, dest, source, title, link, old, spec, more):
    """The converted file (kept as it's to be, encrypted or not) as an asset: a new one, or the one made before when
    forced; queued for its pipeline."""
    st, fp = dest.stat(), ingest.fingerprint(dest, db=db, cfg=cfg)
    row = {
        "path": str(dest),
        "source": source,
        "size": keyring.plain_size(db, cfg, dest),
        "mtime": st.st_mtime,
        "fingerprint": fp,
        "fp_key": f"{rec['space']}:{fp}",
        **more,
    }
    if old:
        was = (db.one("SELECT path FROM $r", r=R("recording", old)) or {}).get("path")
        db.q("UPDATE $r MERGE $d", r=R("recording", old), d={**row, "status": "new"})
        if was and was != str(dest) and keyring.owned(cfg, was):  # made again in another format: the old file goes
            pathlib.Path(was).unlink(missing_ok=True)
        new = old
    else:
        known = db.one("SELECT record::id(id) AS id, path FROM recording WHERE fp_key = $k", k=row["fp_key"])
        if known:  # the same file is an asset here already: the copy just made isn't kept
            if known.get("path") != str(dest):
                dest.unlink(missing_ok=True)
            return known["id"]
        new = db.next_id("recording")
        db.q(
            "CREATE $r CONTENT $d",
            r=R("recording", new),
            d=store.clean(
                {
                    **row,
                    "space": rec["space"],
                    "collection": rec.get("collection"),
                    "title": title[:200],
                    "recorded_at": rec.get("recorded_at") or store.now(),
                    "converted_from": {"resource": int(rid), **link},
                    "status": "new",
                    "created_at": store.now(),
                }
            ),
        )
    then = spec.get("pipeline")
    if then != "none":
        jobs.enqueue(db, new, None, by=f"convert:{int(rid)}", pipeline=then)
    return new
