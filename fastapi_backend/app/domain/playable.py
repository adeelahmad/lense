"""Playable copies: media that every browser plays, made of what phones and cameras record (`video.playable_copies`).

Phones and camcorders write files many browsers can't play: HEVC video in a .mov, AVI, MPEG transport streams (.mts),
AMR voice memos, Windows Media. With the setting on (off by default: transcoding a long video takes a small machine a
while), the transcribe step makes a copy of such a recording's media once, next to its other files
(data_dir/playable/<recording>.mp4 or .m4a, encrypted like the rest when the namespace is): H.264 video at most
`playable_height` lines high and AAC sound in MP4, or AAC alone for sound. The player then gets the copy; the original
stays as it was, and downloads of the original still give it. A file a browser already plays (H.264, VP8, VP9 or AV1
video; AAC, MP3, Opus, Vorbis, FLAC or PCM sound, in MP4, WebM, Ogg, MP3, FLAC or WAV) gets no copy.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

from . import ingest, keyring, store

R = store.R
VIDEO = {"h264", "vp8", "vp9", "av1"}
AUDIO = {"aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_u8", "pcm_f32le"}
CONTAINERS = {".mp4", ".m4a", ".m4v", ".webm", ".mp3", ".ogg", ".oga", ".opus", ".wav", ".flac", ".aac"}
SECONDS = 6 * 3600  # the longest ffmpeg may take over one copy


def on(cfg):
    return bool((cfg.get("video") or {}).get("playable_copies"))


def codecs(path):
    """(the video codec or None, the audio codec or None) of a media file, by ffprobe; (None, None) when unreadable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-format_whitelist", ingest.MEDIA_DEMUXERS, "-show_streams", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        streams = json.loads(out.stdout or "{}").get("streams") or []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, None
    video = next(
        (s.get("codec_name") for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    audio = next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), None)
    return video, audio


def needed(path, name=None):
    """Whether a browser may fail to play this file: its kind of file, or a codec in it, isn't one they all play."""
    video, audio = codecs(path)
    if video is None and audio is None:
        return False  # nothing ffprobe can read: nothing ffmpeg could copy either
    suffix = pathlib.PurePosixPath(str(name or path)).suffix.lower()
    return suffix not in CONTAINERS or (video is not None and video not in VIDEO) or (audio is not None and audio not in AUDIO)


def path_of(cfg, rid, video=True):
    return pathlib.Path(cfg["data_dir"]) / "playable" / f"{int(rid)}{'.mp4' if video else '.m4a'}"


def copies(cfg, rid):
    """Both places a recording's copy may be, for deletion.py."""
    return [path_of(cfg, rid, True), path_of(cfg, rid, False)]


def copy_of(db, cfg, rid):
    """The playable copy of a recording's media, when one was made and is still there: (its path, its media type), else
    None."""
    rec = db.one("SELECT playable FROM $r", r=R("recording", rid)) or {}
    got = rec.get("playable") or {}
    if not got.get("type"):
        return None
    p = path_of(cfg, rid, video=got["type"].startswith("video/"))
    return (str(p), got["type"]) if p.is_file() else None


def ensure(db, cfg, rid, say=print, force=False):
    """Make a recording's playable copy when the setting is on and its media needs one; the copy's path, else None."""
    if not on(cfg):
        return None
    have = copy_of(db, cfg, rid)
    if have and not force:
        return have[0]
    rec = db.one("SELECT path, remote, space, source FROM $r", r=R("recording", rid)) or {}
    if rec.get("source") != "audio":
        return None
    name = (rec.get("remote") or {}).get("path") or rec.get("path")
    src = ingest.audio_path(db, cfg, rec)
    if not needed(src, name):
        return None
    video, _ = codecs(src)
    dest = path_of(cfg, rid, video=video is not None)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name("." + dest.name)
    height = int((cfg.get("video") or {}).get("playable_height") or 720)
    args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-vf", f"scale=-2:'min({height},ih)'"]
    argv = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-format_whitelist", ingest.MEDIA_DEMUXERS, "-i", str(src), "-map", "0:a:0?"]
    argv += (["-map", "0:v:0", *args] if video else ["-vn"]) + [
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(tmp),
    ]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=SECONDS)
        if done.returncode != 0 or not tmp.is_file():
            say(f"  couldn't make a playable copy: {((done.stderr or '').strip().splitlines() or ['ffmpeg failed'])[-1]}")
            return None
        tmp.replace(dest)
    except (OSError, subprocess.TimeoutExpired) as e:  # no ffmpeg, or a copy taking too long: the original still plays
        say(f"  couldn't make a playable copy: {e}")
        return None
    finally:
        tmp.unlink(missing_ok=True)
    keyring.protect(db, cfg, rec.get("space"), dest)
    db.q(
        "UPDATE $r SET playable = $p",
        r=R("recording", rid),
        p={"type": "video/mp4" if video else "audio/mp4", "made_at": store.now()},
    )
    say(f"  made a copy every browser plays ({'video' if video else 'sound'}, {dest.stat().st_size // 1024} KB)")
    return str(dest)
