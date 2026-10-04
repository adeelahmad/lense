"""Files Lens keeps, encrypted at rest (docs/encryption.md): served and processed as their plain bytes, converted by
`lens encrypt`, and switched on for new archives only."""

from __future__ import annotations

import os
import pathlib
import time

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
