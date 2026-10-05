"""Files Lens keeps, encrypted at rest (docs/encryption.md): served and processed as their plain bytes, converted by
`lens encrypt`, and switched on for new archives only."""

from __future__ import annotations

import os
import pathlib
import time

import pytest

from app.domain import ingest, keyring, settings, setup, store
from tests.helpers import login, make_user, text_pdf, write_wav

R = store.R


def _upload(client, h, data, name):
    r = client.post("/api/v1/uploads", headers=h, json={"namespace": "pods", "filename": name, "size": len(data)})
    assert r.status_code == 201, r.text
    r = client.put(f"/api/v1/uploads/{r.json()['id']}?offset=0", headers={**h, "Content-Type": "application/octet-stream"}, content=data)
    assert r.status_code == 200, r.text
    return r.json()["recording"]


def _editor(client, db):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return login(client, "ed@x.io", "editor password 1")


def test_a_new_archive_encrypts_and_still_plays_seeks_and_processes(client, db, cfg, folder):
    assert settings.effective(db, cfg)["encryption"]["files"]  # nothing in it yet: on from the start
    he = _editor(client, db)
    wav = folder / "talk.wav"
    write_wav(wav, seconds=3.0)
    data = wav.read_bytes()
    rid = _upload(client, he, data, "talk.wav")
    rec = db.one("SELECT * FROM $r", r=R("recording", rid))
    assert keyring.is_encrypted(rec["path"]) and rec["size"] == len(data)
    assert rec["fingerprint"] == ingest.fingerprint(wav)  # the plain file's, so the same file is found again
    assert 2900 <= rec["duration_ms"] <= 3100

    whole = client.get(f"/api/v1/recordings/{rid}/audio", headers=he)
    assert whole.status_code == 200 and whole.content == data
    part = client.get(f"/api/v1/recordings/{rid}/audio", headers={**he, "Range": "bytes=65500-65599"})  # across two chunks
    assert part.status_code == 206 and part.content == data[65500:65600]
    assert part.headers["content-range"] == f"bytes 65500-65599/{len(data)}"

    # transcription, video and the rest read a plain working copy, which ffmpeg decodes
    work = ingest.audio_path(db, cfg, rec)
    assert work != rec["path"] and pathlib.Path(work).read_bytes() == data
    assert 2.9 <= len(ingest.decode(work)) / 16000 <= 3.1

    # the same file uploaded again is known by its plain bytes
    again = client.post("/api/v1/uploads", headers=he, json={"namespace": "pods", "filename": "again.wav", "size": len(data)}).json()
    done = client.put(
        f"/api/v1/uploads/{again['id']}?offset=0", headers={**he, "Content-Type": "application/octet-stream"}, content=data
    ).json()
    assert (done["recording"], done["duplicate"]) == (rid, True)


def test_documents_download_as_they_were(client, db, cfg):
    he = _editor(client, db)
    pdf = text_pdf(["Harbour report", "The ships arrived at dawn."])
    rid = _upload(client, he, pdf, "harbour.pdf")
    assert keyring.is_encrypted(db.one("SELECT path FROM $r", r=R("recording", rid))["path"])
    r = client.get(f"/api/v1/recordings/{rid}/media", headers=he)
    assert r.status_code == 200 and r.content == pdf
    assert 'filename="harbour.pdf"' in r.headers["content-disposition"]
    r = client.get(f"/api/v1/recordings/{rid}/media", headers={**he, "Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == pdf[10:20]


def test_a_file_that_only_looks_encrypted_is_encrypted_too(client, db, cfg):
    he = _editor(client, db)
    odd = b"LENSE1" + b"\x00" * 40 + b"just text"
    rid = _upload(client, he, odd, "odd.pdf")
    assert client.get(f"/api/v1/recordings/{rid}/media", headers=he).content == odd


def test_lens_encrypt_converts_an_archive_that_has_files(client, db, cfg, folder):
    settings.save(db, cfg, "encryption", {"files": False})
    he = _editor(client, db)
    wav = folder / "talk.wav"
    write_wav(wav, seconds=1.0)
    rid = _upload(client, he, wav.read_bytes(), "talk.wav")
    path = db.one("SELECT path FROM $r", r=R("recording", rid))["path"]
    assert not keyring.is_encrypted(path)
    scanned = folder / "scanned.wav"  # a file in a scanned folder is only ever read
    write_wav(scanned, seconds=1.0)
    db.q("CREATE $r CONTENT $d", r=R("recording", 999), d={"space": store.ns_id(db, "pods"), "path": str(scanned), "source": "audio"})
    mtime = os.stat(path).st_mtime

    assert keyring.encrypt_all(db, cfg, log=lambda *_: None) == 1
    assert keyring.is_encrypted(path) and not keyring.is_encrypted(scanned) and os.stat(path).st_mtime == mtime
    assert keyring.encrypt_all(db, cfg, log=lambda *_: None) == 0  # done already: safe to run again
    assert client.get(f"/api/v1/recordings/{rid}/audio", headers=he).content == wav.read_bytes()
    assert keyring.encrypt_all(db, cfg, decrypt=True, log=lambda *_: None) == 1
    assert pathlib.Path(path).read_bytes() == wav.read_bytes()


def test_an_archive_with_files_keeps_its_setting(db, cfg):
    db.q("DELETE app_setting:encryption")
    db.q("DELETE $r", r=R(*setup.WIZARD))
    db.q("CREATE recording:1 CONTENT {space: 1, path: '/x.wav'}")
    setup.mark_fresh(db, cfg)
    assert not settings.effective(db, cfg)["encryption"]["files"]


def test_working_copies_go_once_unused(db, cfg, folder):
    p = pathlib.Path(cfg["data_dir"]) / "uploads" / "pods" / "a" / "talk.wav"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"RIFF plain")
    assert keyring.working_copy(db, cfg, str(p)) == str(p)  # not encrypted: the file itself
    keyring.encrypt_file(db, cfg, store.ns_id(db, "pods"), p)
    w = keyring.working_copy(db, cfg, str(p))
    assert pathlib.Path(w).name == "talk.wav" and pathlib.Path(w).read_bytes() == b"RIFF plain"
    assert keyring.working_copy(db, cfg, str(p)) == w  # made once
    assert keyring.sweep(cfg) == 0
    old = time.time() - 3600
    os.utime(pathlib.Path(w).parent, (old, old))
    assert keyring.sweep(cfg) == 0  # still held by the job using it
    keyring.release()
    assert keyring.sweep(cfg) == 1 and not os.path.exists(w)


def test_reports_exports_and_renditions_are_kept_encrypted(client, db, cfg, folder):
    from app.domain import analyze, convert, render
    from tests.helpers import quiet

    eff = settings.effective(db, cfg)
    he = _editor(client, db)
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text("[00:00] Alice: A short clip about the capsid.\n[00:02] Bob: Indeed it is short.")
    ingest.import_transcript(db, eff, "pods", tr, audio=wav, log=quiet)
    analyze.analyze_pending(db, eff, log=quiet)

    written = render.build_reports(db, eff, log=quiet)
    assert written and all(keyring.is_encrypted(p) for p in written)
    assert b"capsid" not in written[0].read_bytes()
    page = client.get(f"/reports/pods/{written[0].name}", headers=he)
    assert page.status_code == 200 and "capsid" in page.text
    overview = client.get("/reports/pods/", headers=he)
    assert overview.status_code == 200 and written[0].name in overview.text

    # a document's PDF rendition downloads as its plain bytes
    pdf = text_pdf(["Harbour report"])
    doc = _upload(client, he, b"Harbour notes\n", "notes.txt")
    out = convert.rendition_path(cfg, doc)
    keyring.keep(db, eff, store.ns_id(db, "pods"), out, pdf)
    assert keyring.is_encrypted(out)
    r = client.get(f"/api/v1/recordings/{doc}/pdf", headers=he)
    assert r.status_code == 200 and r.content == pdf and r.headers["content-type"] == "application/pdf"

    # outside data_dir (a folder `lens report` was told to write to), files stay as they are
    plain = keyring.keep(db, eff, store.ns_id(db, "pods"), folder / "out" / "r.html", "<p>hi</p>")
    assert plain.read_text() == "<p>hi</p>"

    # lens encrypt (or making it a vault) finds them too
    keyring.encrypt_all(db, eff, decrypt=True, log=lambda *_: None)
    assert not any(keyring.is_encrypted(p) for _, p in keyring.made_files(db, eff))
    assert keyring.encrypt_all(db, eff, log=lambda *_: None) >= len(written) + 1
    assert all(keyring.is_encrypted(p) for _, p in keyring.made_files(db, eff))


def test_files_cached_from_a_storage_source_are_kept_encrypted(client, db, cfg, folder, monkeypatch):
    from app.domain import sources

    eff = settings.effective(db, cfg)
    wav = folder / "remote.wav"
    write_wav(wav, seconds=1.0)
    monkeypatch.setattr(sources, "get", lambda db, sid: {"id": sid, "type": "s3", "name": "bucket"})

    def fetch(db, cfg, src, args, timeout=None):
        dest = args("remote")[-1]
        pathlib.Path(dest).write_bytes(wav.read_bytes())

    monkeypatch.setattr(sources, "run", fetch)
    sid = store.ns_id(db, "pods")
    db.q(
        "CREATE recording:77 CONTENT $d",
        d={"space": sid, "source": "audio", "remote": {"source": 5, "path": "calls/remote.wav"}, "path": "bucket:calls/remote.wav"},
    )
    rec = db.one("SELECT * FROM recording:77")
    work = ingest.audio_path(db, eff, rec)
    cached = sources.cache_file(eff, 5, "calls/remote.wav", sid)
    assert keyring.is_encrypted(cached) and pathlib.Path(work).read_bytes() == wav.read_bytes()
    assert (sid, str(cached)) in list(keyring.made_files(db, eff))
    he = _editor(client, db)
    r = client.get("/api/v1/recordings/77/audio", headers={**he, "Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == wav.read_bytes()[:10]

    # each namespace keeps its own copy, under its own key
    other = store.ns_id(db, "other")
    assert sources.cache_file(eff, 5, "calls/remote.wav", other) != cached

    # a locked vault fetches nothing, and leaves nothing plain behind
    def locked(*a, **k):
        raise keyring.Locked("locked")

    monkeypatch.setattr(keyring, "data_key", locked)
    with pytest.raises(keyring.Locked):
        sources.cached_copy(db, eff, 5, "calls/other.wav", sid)
    assert not sources.cache_file(eff, 5, "calls/other.wav", sid).exists()
    assert not list(cached.parent.glob("*.part"))


def test_a_moved_recording_keeps_its_files_under_the_new_namespace_key(client, db, cfg, folder):
    eff = settings.effective(db, cfg)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner", "calls": "editor"})
    ho = login(client, "own@x.io", "owner password 1")
    wav = folder / "talk.wav"
    write_wav(wav, seconds=1.0)
    data = wav.read_bytes()
    rid = _upload(client, ho, data, "talk.wav")
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    frames = pathlib.Path(eff["data_dir"]) / "frames" / str(rid)
    kept = keyring.keep(db, eff, pods, frames / "shot-1.jpg", b"a frame")
    plain = frames / "shot-2.jpg"
    plain.write_bytes(b"one left plain")
    files = list(keyring.recording_files(db, eff, rid))
    assert str(kept) in files and str(plain) in files and len(files) == 3

    # the new namespace is a vault nobody has unlocked: nothing moves
    kek = keyring.derive(b"prf output", "lens/passkey")
    keyring.add_wrapper(db, eff, calls, "passkey:phone", kek)
    keyring.remove_wrapper(db, calls, "server")
    keyring.lock(db, calls)
    r = client.post(f"/api/v1/recordings/{rid}/move", headers=ho, json={"namespace": "calls"})
    assert r.status_code == 423, r.text
    assert db.one("SELECT space FROM $r", r=R("recording", rid))["space"] == pods
    assert keyring.Reader(db, eff, kept).space == pods

    keyring.unlock(db, calls, "passkey:phone", kek)
    r = client.post(f"/api/v1/recordings/{rid}/move", headers=ho, json={"namespace": "calls"})
    assert r.status_code == 200, r.text
    for p in keyring.recording_files(db, eff, rid):
        with keyring.Reader(db, eff, p) as f:
            assert f.space == calls
    assert keyring.read_plain(db, eff, kept) == b"a frame" and keyring.read_plain(db, eff, plain) == b"one left plain"
    assert client.get(f"/api/v1/recordings/{rid}/audio", headers=ho).content == data

    # and they open only while the vault they're in is unlocked
    keyring.lock(db, calls)
    assert client.get(f"/api/v1/recordings/{rid}/audio", headers=ho).status_code == 423
