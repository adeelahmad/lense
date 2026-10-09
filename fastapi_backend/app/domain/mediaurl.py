"""Videos and podcasts by their address (docs/api.md#videos-and-podcasts): someone gives the address of a video or an
episode (a video site, a podcast's page, a media file); Lens downloads its media with yt-dlp (github.com/yt-dlp/yt-dlp,
Unlicense) and transcribes it like an upload.

Off by default (documents.video_urls): it fetches media from sites, and the yt-dlp program on first use. yt-dlp is a
single program that components.py downloads, checked against its release checksum (or one on PATH). It runs with no
config file, no plugins, no cache, one video (never a playlist), and netguard's proxy as its only way out: public
addresses only (and documents.web_networks), on the web's own ports, at most uploads.max_mb coming back. It picks one
file holding both sound and picture, 720 lines at most, so nothing needs joining.

Downloading happens in the resource's transcribe step, once: the media is the resource's file,
data_dir/media/<resource>/<title>.<ext>, and a resource transcribed again keeps it.
"""

from __future__ import annotations

import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from . import ingest, keyring, netguard, render, store, webcapture

R = store.R
VERSION = "2026.08.19"
RELEASES = "https://github.com/yt-dlp/yt-dlp/releases/download"
# its standalone builds, by (system, machine), with the sha256 its release published (SHA2-256SUMS)
BINARIES = {
    ("Linux", "x86_64"): ("yt-dlp_linux", "58162f9bfdc27458ea47bfcb311cf47028f17d8154a8bf7d689861d46399230a"),
    ("Linux", "aarch64"): ("yt-dlp_linux_aarch64", "b16e4dab368a816cd05d477d698a605a6ae87ccee1c8ffd38fa21d7254141fcc"),
    ("Darwin", "x86_64"): ("yt-dlp_macos", "0f192b7ec147ab6288885d6351d9ab67367640029b4377576ef46dd79cf7b202"),
    ("Darwin", "arm64"): ("yt-dlp_macos", "0f192b7ec147ab6288885d6351d9ab67367640029b4377576ef46dd79cf7b202"),
}
# anywhere else: the Python zipapp, run by this Python
ZIPAPP = ("yt-dlp", "1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6")
MACHINES = {"amd64": "x86_64", "arm64": "arm64", "aarch64": "aarch64"}
FORMAT = "b[height<=720]/b"  # one file with both sound and picture (or sound alone): nothing to join with ffmpeg
SECONDS = 4 * 3600  # the longest a download may take
QUIET = ["--ignore-config", "--no-plugin-dirs", "--no-cache-dir", "--no-playlist", "--no-progress", "--no-mtime"]


class Unavailable(RuntimeError):
    """Importing videos by address is off, or yt-dlp can't be had."""


def enabled(cfg):
    return bool((cfg.get("documents") or {}).get("video_urls"))


def build(system=None, machine=None):
    """This machine's yt-dlp: (file name, sha256). The zipapp where there's no standalone build."""
    system, machine = system or platform.system(), machine or platform.machine()
    return BINARIES.get((system, MACHINES.get(machine.lower(), machine))) or ZIPAPP


def fetched_path(cfg):
    return pathlib.Path(cfg["data_dir"]) / "models" / f"yt-dlp-{VERSION}" / build()[0]


def program(cfg):
    """How to run yt-dlp here (an argv to start with), or None: the one downloaded, else one on PATH."""
    got = fetched_path(cfg)
    if got.is_file():
        return [sys.executable, str(got)] if got.name == ZIPAPP[0] else [str(got)]
    found = shutil.which("yt-dlp")
    return [found] if found else None


def fetch(cfg, say=None):
    """Download this machine's yt-dlp and check it against its checksum."""
    import hashlib

    name, sha = build()
    dest = fetched_path(cfg)
    if dest.is_file():
        return str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if say:
        say(f"downloading yt-dlp {VERSION}")
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        part, h = pathlib.Path(tmp) / name, hashlib.sha256()
        with urllib.request.urlopen(f"{RELEASES}/{VERSION}/{name}", timeout=60) as r, open(part, "wb") as f:
            while chunk := r.read(1 << 20):
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest() != sha:
            raise RuntimeError(f"{name} didn't match its checksum")
        part.chmod(0o755)
        part.replace(dest)
    return str(dest)


def ready(cfg, say=None):
    """yt-dlp's argv, fetched on first use. Unavailable when importing by address is off."""
    if not enabled(cfg):
        raise Unavailable("importing videos and podcasts by address is off (Settings → Documents)")
    argv = program(cfg)
    if not argv:
        fetch(cfg, say)
        argv = program(cfg)
    if not argv:
        raise Unavailable("yt-dlp isn't here")
    return argv


def create(db, sid, url, title=None, collection=None, by=None):
    """A resource for the video or episode at `url` in namespace `sid`, its media downloaded when its pipeline runs.
    Returns its id."""
    rid = db.next_id("recording")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("recording", rid),
        d=store.clean(
            {
                "space": sid,
                "collection": store.home(db, sid, collection),
                "source": "audio",
                "web": {"url": url, "media": True},
                "title": (title or "").strip()[:200] or webcapture.placeholder(url),
                "recorded_at": store.now(),
                "status": "new",
                "created_at": store.now(),
                "created_by": by,
            }
        ),
    )
    return rid


def folder(cfg, rid):
    return pathlib.Path(cfg["data_dir"]) / "media" / str(int(rid))


def _why(err):
    lines = [x.strip() for x in (err or "").splitlines() if x.strip()]
    last = next((x for x in reversed(lines) if x.startswith("ERROR:")), lines[-1] if lines else "")
    return last.removeprefix("ERROR:").strip()[:300] or "it ended without saying why"


def download(cfg, url, into, say=None):
    """The media at `url`, downloaded into the folder `into`: (its path, what yt-dlp said of it). ValueError when it
    can't be had."""
    argv = ready(cfg, say)
    most = cfg["uploads"]["max_mb"]
    into.mkdir(parents=True, exist_ok=True)
    with netguard.Guard(forward=True, networks=webcapture.networks(cfg), max_bytes=most * 2**20) as guard:
        env = netguard.nowhere_env(os.environ)
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            env[k] = guard.url
        cmd = [
            *argv,
            *QUIET,
            "--proxy",
            guard.url,
            "--max-filesize",
            f"{most}M",
            "-f",
            FORMAT,
            "--hls-prefer-native",
            "--write-info-json",
            "--no-write-comments",
            "-o",
            "media.%(ext)s",
            "--",
            url,
        ]
        try:
            run = subprocess.run(cmd, cwd=into, env=env, capture_output=True, text=True, timeout=SECONDS)
        except subprocess.TimeoutExpired:
            raise ValueError(f"downloading it took longer than {SECONDS // 3600} hours") from None
        refused = f"; refused: {guard.refused[0]}" if guard.refused else ""
    got = [p for p in into.glob("media.*") if p.suffix not in (".json", ".part", ".ytdl") and p.is_file()]
    if run.returncode or not got:
        raise ValueError(f"its media couldn't be downloaded ({_why(run.stderr)}){refused}")
    info = {}
    meta = into / "media.info.json"
    if meta.is_file():
        try:
            info = json.loads(meta.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            info = {}
        meta.unlink()
    return got[0], info if isinstance(info, dict) else {}


def ensure(db, cfg, rid, rec, say):
    """A video's or episode's media: downloaded now if it hasn't been (its transcribe step), else the file kept."""
    have = store.resolve_path(cfg, rec.get("path"))
    if have and pathlib.Path(have).is_file():
        return have
    web = rec.get("web") or {}
    d = folder(cfg, rid)
    d.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=d.parent, prefix="download-") as tmp:
        path, info = download(cfg, web["url"], pathlib.Path(tmp), say)
        title = str(info.get("title") or "").strip()[:200] or None
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{render.slug(title or webcapture.placeholder(web['url']))}{path.suffix.lower()}"
        shutil.move(str(path), out)
    st = out.stat()
    dur, ch = ingest.probe(out)
    patch = store.clean(
        {
            "path": str(out),
            "size": st.st_size,
            "mtime": st.st_mtime,
            "fingerprint": ingest.fingerprint(out),
            "duration_ms": dur,
            "channels": ch,
            "web": store.clean(
                {
                    **web,
                    "final": str(info.get("webpage_url") or web["url"])[:2000],
                    "site": str(info.get("extractor_key") or "")[:100] or None,
                    "by": str(info.get("uploader") or info.get("channel") or "")[:200] or None,
                    "captured_at": store.now(),
                }
            ),
        }
    )
    if title and rec.get("title") == webcapture.placeholder(web["url"]):  # still called by its address: its own title
        patch["title"] = title
    keyring.protect(db, cfg, rec["space"], out)
    db.q("UPDATE $r MERGE $d", r=R("recording", rid), d=patch)
    say(f"downloaded {patch['web']['final']}")
    return str(out)
