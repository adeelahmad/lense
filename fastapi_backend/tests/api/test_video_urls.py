"""Videos and podcasts by their address (mediaurl.py, POST /import/video): off by default; on, a resource whose
transcribe step downloads its media with yt-dlp (here a fake one), through netguard's proxy, then transcribes it."""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import sys

import pytest

from app.domain import components, ingest, jobs, mediaurl, store
from tests.helpers import login, make_user

R = store.R

FAKE = r"""#!@PYTHON@
import json, os, sys, wave
args = sys.argv[1:]
with open(@LOG@, "a") as f:
    f.write(json.dumps({"args": args, "https_proxy": os.environ.get("https_proxy")}) + "\n")
url = args[-1]
if "nothing" in url:
    print("ERROR: [generic] Unsupported URL: " + url, file=sys.stderr)
    sys.exit(1)
with wave.open("media.wav", "wb") as w:
    w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(b"\0\0" * 16000)
json.dump({"title": "Harbour talk: episode 4", "webpage_url": url, "extractor_key": "Generic", "uploader": "Harbour FM"}, open("media.info.json", "w"))
"""


def fake(cfg, tmp_path):
    log = tmp_path / "yt-dlp.log"
    exe = mediaurl.fetched_path(cfg)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(FAKE.replace("@PYTHON@", sys.executable).replace("@LOG@", repr(str(log))))
    exe.chmod(0o755)
    return log


@pytest.fixture
def app(cfg, db, monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(mediaurl, "build", lambda *a: ("yt-dlp_linux", "0" * 64))  # the fake is a script on its own
    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "view@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "ha": login(client, "root@x.io", "root password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "view@x.io", "viewer password 1"),
    }


def test_off_by_default(client, env, cfg):
    assert store.DEFAULTS["documents"]["video_urls"] is False
    assert not components.YtDlp("yt-dlp", "yt-dlp", "").needed(cfg, {})
    r = client.post("/api/v1/import/video", headers=env["he"], json={"namespace": "pods", "url": "https://8.8.8.8/watch?v=1"})
    assert r.status_code == 400 and "off" in r.json()["detail"]


def test_a_video_by_its_address_is_downloaded_then_transcribed(client, env, cfg, db, tmp_path, monkeypatch):
    r = client.put("/api/v1/settings/documents", headers=env["ha"], json={"video_urls": True})
    assert r.status_code == 200, r.text
    cfg["documents"]["video_urls"] = True  # for the steps run here, as the worker's settings would have it
    log = fake(cfg, tmp_path)
    url = "/api/v1/import/video"
    assert client.post(url, headers=env["hv"], json={"namespace": "pods", "url": "https://8.8.8.8/watch?v=1"}).status_code == 403
    r = client.post(url, headers=env["he"], json={"namespace": "pods", "url": "http://127.0.0.1/admin"})
    assert r.status_code == 400 and "isn't a public address" in r.json()["detail"]
    r = client.post(url, headers=env["he"], json={"namespace": "pods", "url": "https://8.8.8.8/watch?v=1"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    rec = db.one("SELECT source, web, title, path, space FROM $r", r=R("recording", rid))
    assert (rec["source"], rec["web"], rec["title"], rec.get("path")) == (
        "audio",
        {"url": "https://8.8.8.8/watch?v=1", "media": True},
        "8.8.8.8/watch?v=1",
        None,
    )
    audit = db.one("SELECT detail FROM audit_log WHERE action = 'import.video'")
    assert audit["detail"]["url"] == "https://8.8.8.8/watch?v=1"

    heard = []
    monkeypatch.setattr(ingest, "transcribe_one", lambda db, cfg, rid, say: heard.append(rid))
    said = []
    jobs._transcribe(db, cfg, rid, said.append)
    assert heard == [rid]
    rec = db.one("SELECT path, title, web, duration_ms, fingerprint FROM $r", r=R("recording", rid))
    assert pathlib.Path(rec["path"]).name == "harbour-talk-episode-4.wav" and pathlib.Path(rec["path"]).is_file()
    assert rec["title"] == "Harbour talk: episode 4" and rec["duration_ms"] == 1000 and rec["fingerprint"]
    assert rec["web"]["by"] == "Harbour FM" and rec["web"]["captured_at"]
    assert said == ["downloaded https://8.8.8.8/watch?v=1"]
    run = [json.loads(x) for x in log.read_text().splitlines()][-1]
    args = run["args"]
    for flag in ("--ignore-config", "--no-plugin-dirs", "--no-playlist", "--no-cache-dir", "--hls-prefer-native"):
        assert flag in args
    proxy = args[args.index("--proxy") + 1]
    assert proxy.startswith("http://127.0.0.1:") and run["https_proxy"] == proxy
    assert args[args.index("--max-filesize") + 1] == f"{cfg['uploads']['max_mb']}M" and args[-2:] == ["--", "https://8.8.8.8/watch?v=1"]

    jobs._transcribe(db, cfg, rid, said.append)  # again: the media is kept, not downloaded twice
    assert len(log.read_text().splitlines()) == 1 and heard == [rid, rid]


def test_what_it_cant_download_says_why(cfg, db, tmp_path, monkeypatch):
    monkeypatch.setattr(mediaurl, "build", lambda *a: ("yt-dlp_linux", "0" * 64))
    cfg["documents"]["video_urls"] = True
    fake(cfg, tmp_path)
    with pytest.raises(ValueError, match=r"couldn.t be downloaded \(\[generic\] Unsupported URL"):
        mediaurl.download(cfg, "https://8.8.8.8/nothing", tmp_path / "into")
    cfg["documents"]["video_urls"] = False
    with pytest.raises(mediaurl.Unavailable, match="off"):
        mediaurl.download(cfg, "https://8.8.8.8/a", tmp_path / "into")


def test_yt_dlp_is_fetched_only_when_it_matches_its_checksum(cfg, monkeypatch):
    real = mediaurl.build
    assert real("Linux", "aarch64")[0] == "yt-dlp_linux_aarch64" and real("Plan9", "mips") == mediaurl.ZIPAPP
    body = b"#!/bin/sh\necho yt-dlp\n"
    monkeypatch.setattr(mediaurl.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(body))
    monkeypatch.setattr(mediaurl, "build", lambda *a: ("yt-dlp_linux", "f" * 64))
    with pytest.raises(RuntimeError, match="checksum"):
        mediaurl.fetch(cfg)
    assert not mediaurl.fetched_path(cfg).exists()
    monkeypatch.setattr(mediaurl, "build", lambda *a: ("yt-dlp_linux", hashlib.sha256(body).hexdigest()))
    got = mediaurl.fetch(cfg)
    assert pathlib.Path(got).read_bytes() == body and mediaurl.program(cfg) == [got]
