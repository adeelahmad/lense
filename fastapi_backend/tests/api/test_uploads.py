"""Uploading audio and video in pieces (docs/api.md, Uploads): chunks stream to disk, a dropped chunk is sent again,
and the last one makes a recording and queues the namespace's pipeline."""

from __future__ import annotations

import pathlib

import pytest

from app.domain import deletion, ingest, jobs, render, store, uploads
from tests.helpers import drain, login, make_user, quiet, write_wav

R = store.R


@pytest.fixture
def env(client, db, cfg, folder):
    wav = folder / "talk.wav"
    write_wav(wav, seconds=2.5)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "ed2@x.io", "editor password 2", roles={"pods": "editor"})
    make_user(db, "view@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "data": wav.read_bytes(),
        "ha": login(client, "root@x.io", "root password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "he2": login(client, "ed2@x.io", "editor password 2"),
        "hv": login(client, "view@x.io", "viewer password 1"),
    }


def _start(client, h, size, name="talk.wav", ns="pods", **more):
    return client.post("/api/v1/uploads", headers=h, json={"namespace": ns, "filename": name, "size": size, **more})


def _send(client, h, uid, offset, chunk):
    return client.put(f"/api/v1/uploads/{uid}?offset={offset}", headers={**h, "Content-Type": "application/octet-stream"}, content=chunk)


def _upload(client, h, data, name="talk.wav", ns="pods", piece=10_000):
    r = _start(client, h, len(data), name, ns)
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    for at in range(0, len(data), piece):
        r = _send(client, h, uid, at, data[at : at + piece])
        assert r.status_code == 200, r.text
    return r.json()


def test_uploading_audio_in_chunks(client, env, db, cfg):
    data, he, hv = env["data"], env["he"], env["hv"]
    lim = client.get("/api/v1/uploads/limits", headers=hv).json()
    assert (lim["max_mb"], lim["chunk_mb"], lim["transcript_mb"]) == (4096, 8, 50) and ".wav" in lim["extensions"]

    # editors only: viewers, API tokens without write, and nobody signed in can't start one
    assert _start(client, hv, len(data)).status_code == 403
    read = client.post("/api/v1/tokens", headers=he, json={"name": "ro", "scope": "read"}).json()["token"]
    assert _start(client, {"Authorization": f"Bearer {read}"}, len(data)).status_code == 403
    assert _start(client, {}, len(data)).status_code == 401

    # the name loses its folders and its extension's capitals; the file's own time is kept
    r = _start(client, he, len(data), name="../../2024-03-05 interview.WAV", title="An interview", modified=1_700_000_000_000)
    assert r.status_code == 201, r.text
    up = r.json()
    uid = up["id"]
    assert (up["filename"], up["offset"], up["state"], up["title"]) == ("2024-03-05 interview.wav", 0, "receiving", "An interview")
    part = pathlib.Path(cfg["data_dir"]) / "uploads" / ".partial" / uid
    assert part.exists()

    # the first chunk; one sent from the wrong place is refused; how far it got survives a dropped connection
    assert _send(client, he, uid, 0, data[:10_000]).json()["offset"] == 10_000
    r = _send(client, he, uid, 0, data[:10_000])
    assert r.status_code == 409 and "10000 bytes" in r.json()["detail"]
    assert client.get(f"/api/v1/uploads/{uid}", headers=he).json()["offset"] == 10_000
    assert [u["id"] for u in client.get("/api/v1/uploads", headers=he).json()] == [uid]
    # someone else's upload isn't theirs to see or send to
    assert client.get(f"/api/v1/uploads/{uid}", headers=env["he2"]).status_code == 404
    assert _send(client, env["he2"], uid, 10_000, data[10_000:]).status_code == 404
    assert client.get("/api/v1/uploads/not-an-upload", headers=he).status_code == 422

    # the rest: a recording, with the namespace's pipeline queued
    r = _send(client, he, uid, 10_000, data[10_000:])
    assert r.status_code == 200, r.text
    done = r.json()
    assert (done["state"], done["offset"], done["duplicate"]) == ("done", len(data), False)
    rid, job = done["recording"], done["job"]
    rec = db.one("SELECT * FROM $r", r=R("recording", rid))
    path = pathlib.Path(rec["path"])
    assert path == pathlib.Path(cfg["data_dir"]) / "uploads" / "pods" / uid / "2024-03-05 interview.wav"
    assert path.read_bytes() == data and not part.exists()
    assert (rec["title"], rec["source"], rec["status"], rec["recorded_at"]) == ("An interview", "audio", "new", "2024-03-05T00:00:00")
    assert 2400 <= rec["duration_ms"] <= 2600 and render.has_audio(db, cfg, rid)
    assert db.one("SELECT status, recording FROM $j", j=R("job", job)) == {"status": "queued", "recording": rid}
    audit = db.rows("SELECT action, target, email, detail FROM audit_log WHERE action = 'upload'")
    assert audit == [
        {
            "action": "upload",
            "target": f"recording:{rid}",
            "email": "ed@x.io",
            "detail": {"file": "2024-03-05 interview.wav", "size": len(data), "namespace": "pods", "duplicate": False, "attached": False},
        }
    ]
    assert client.get("/api/v1/uploads", headers=he).json() == []
    # the same last chunk again (its answer was lost): still done, nothing new
    again = _send(client, he, uid, 10_000, data[10_000:])
    assert again.status_code == 200 and again.json()["recording"] == rid
    assert client.get(f"/api/v1/recordings/{rid}", headers=hv).status_code == 200

    # the same file again finds that recording; the copy goes
    dup = _upload(client, he, data, name="copy.wav")
    assert (dup["recording"], dup["duplicate"], dup["job"]) == (rid, True, None)
    assert not (pathlib.Path(cfg["data_dir"]) / "uploads" / "pods" / dup["id"]).exists()


def test_what_can_be_uploaded(client, env, db, cfg, monkeypatch):
    data, ha, he = env["data"], env["ha"], env["he"]
    r = _start(client, he, 100, name="notes.txt")
    assert r.status_code == 400 and r.json()["detail"].startswith("TXT files can't be uploaded")
    assert _start(client, he, 100, name="noextension").status_code == 400
    assert _start(client, he, 100, ns="Bad Name").status_code == 400
    assert _start(client, he, 0).status_code == 422

    # limits are settings, checked when saved
    put = lambda changes: client.put("/api/v1/settings/uploads", headers=ha, json=changes)  # noqa: E731
    assert put({"extensions": [".exe"]}).status_code == 400
    assert put({"extensions": []}).status_code == 400
    assert put({"chunk_mb": 0}).status_code == 400
    assert put({"max_mb": "big"}).status_code == 400
    assert put({"max_mb": 1, "extensions": ["MP3", "wav"], "expire_hours": 2}).status_code == 200
    lim = client.get("/api/v1/uploads/limits", headers=he).json()
    assert (lim["max_mb"], lim["extensions"]) == (1, [".mp3", ".wav"])
    assert _start(client, he, 100, name="a.m4a").status_code == 400
    r = _start(client, he, 2 * 1024 * 1024)
    assert (r.status_code, r.json()["detail"]) == (413, "files up to 1 MB")

    # a disk without room for it
    monkeypatch.setattr(uploads.shutil, "disk_usage", lambda _p: type("U", (), {"free": 100 * 1024 * 1024})())
    assert _start(client, he, len(data)).status_code == 507
    monkeypatch.undo()

    # more than the size is refused, and nothing of that chunk is kept
    uid = _start(client, he, len(data)).json()["id"]
    r = _send(client, he, uid, 0, data + b"extra")
    assert r.status_code == 400 and client.get(f"/api/v1/uploads/{uid}", headers=he).json()["offset"] == 0

    # cancelled: what arrived goes
    part = pathlib.Path(cfg["data_dir"]) / "uploads" / ".partial" / uid
    _send(client, he, uid, 0, data[:5000])
    assert client.delete(f"/api/v1/uploads/{uid}", headers=he).json() == {"ok": True}
    assert not part.exists() and client.get(f"/api/v1/uploads/{uid}", headers=he).status_code == 404

    # left alone longer than uploads.expire_hours: removed with its partial file
    uid = _start(client, he, len(data)).json()["id"]
    _send(client, he, uid, 0, data[:5000])
    db.q("UPDATE $r SET touched_at = '2020-01-01T00:00:00+00:00'", r=R("upload", uid))
    assert client.get("/api/v1/uploads", headers=he).json() == []
    assert not (pathlib.Path(cfg["data_dir"]) / "uploads" / ".partial" / uid).exists()

    # a new namespace: admins only, created when the upload finishes
    assert _start(client, he, len(data), ns="fresh").status_code == 403
    uid = _start(client, ha, len(data), ns="fresh").json()["id"]
    assert "fresh" not in store.space_names(db).values()
    done = _send(client, ha, uid, 0, data).json()
    assert done["state"] == "done" and "fresh" in store.space_names(db).values()


def test_an_upload_finds_the_recording_it_already_is(client, env, db, cfg, folder):
    """A file scanned before whose copy has gone gets its media back; one deleted before comes back as new."""
    data, he = env["data"], env["he"]
    shelf = folder / "shelf"
    shelf.mkdir()
    (shelf / "old.wav").write_bytes(data)
    cfg["namespaces"]["pods"]["paths"] = [str(shelf)]
    ingest.scan(db, cfg, only="pods", log=quiet)
    rid = db.values("SELECT VALUE record::id(id) FROM recording WHERE path = $p", p=str(shelf / "old.wav"))[0]
    (shelf / "old.wav").unlink()
    done = _upload(client, he, data)
    assert (done["recording"], done["duplicate"]) == (rid, True) and done["job"]
    assert render.has_audio(db, cfg, rid).startswith(str(pathlib.Path(cfg["data_dir"]) / "uploads"))

    # deleted, then uploaded on purpose: a new recording (the note that kept scans from importing it is cleared)
    deletion.delete(db, cfg, rid, {"email": "ed@x.io"})
    assert deletion.gone(db, store.ns_id(db, "pods"))[1]
    again = _upload(client, he, data)
    assert again["recording"] != rid and not again["duplicate"]
    assert not deletion.gone(db, store.ns_id(db, "pods"))[1]


def test_a_chunk_is_whole_or_not_at_all(db, cfg):
    up = uploads.start(db, cfg, "pods", "a.wav", 100, {"id": 1, "email": "a@x.io"})
    part = pathlib.Path(cfg["data_dir"]) / "uploads" / ".partial" / up["id"]
    with pytest.raises(RuntimeError), uploads.Chunk(cfg, up, 0) as c:
        c.write(b"x" * 30)
        raise RuntimeError("the connection dropped")
    assert part.stat().st_size == 0
    with uploads.Chunk(cfg, up, 0) as c:
        c.write(b"x" * 30)
        with pytest.raises(uploads.Busy), uploads.Chunk(cfg, up, 30):
            pass  # another chunk while this one is arriving
        c.keep()
    assert part.stat().st_size == 30
    with pytest.raises(uploads.Mismatch):
        uploads.Chunk(cfg, up, 10).__enter__()
    assert uploads.clean_name("C:\\Users\\me\\..\\.hidden\x00 talk .MP3") == "hidden talk.mp3"
    assert uploads.clean_name("é" * 200 + ".wav") == "é" * 75 + ".wav"
    uploads.cancel(db, cfg, up)
    with pytest.raises(KeyError):
        uploads.received(db, cfg, up["id"])


def _attach(client, h, data, rid, name="talk.wav", **more):
    return client.post("/api/v1/uploads", headers=h, json={"recording": rid, "filename": name, "size": len(data), **more})


def test_attaching_audio_to_a_transcript(client, env, db, cfg):
    data, he, hv = env["data"], env["he"], env["hv"]
    text = "[00:00] Alice: A short clip about the capsid.\n[00:01] Bob: Indeed it is short.\n[00:02] Alice: Bye."
    r = client.post("/api/v1/import", headers=he, json={"namespace": "pods", "text": text, "title": "Clip"})
    rid, imported = r.json()["id"], r.json()["job"]
    assert not render.has_audio(db, cfg, rid)
    calls = ingest.import_text(db, cfg, "calls", "[00:00] Dave: Hello.\n[00:02] Eve: Hi.")

    # editors of its namespace only; the namespace, when named, must be its own
    assert _attach(client, hv, data, rid).status_code == 403
    assert _attach(client, he, data, calls).status_code == 404
    assert _attach(client, he, data, rid, namespace="calls").status_code == 400
    r = _attach(client, he, data, rid, namespace="pods")
    assert r.status_code == 201, r.text
    up = r.json()
    assert (up["attach"], up["namespace"]) == (rid, "pods")

    # its pieces arrive like any upload; the last makes the file the transcript's audio
    for at in range(0, len(data), 30_000):
        r = _send(client, he, up["id"], at, data[at : at + 30_000])
        assert r.status_code == 200, r.text
    done = r.json()
    assert (done["state"], done["recording"], done["duplicate"]) == ("done", rid, False)
    # the import's job, still waiting, does the steps that need media too
    assert done["job"] == imported
    steps = [s["type"] for s in db.one("SELECT steps FROM $j", j=R("job", imported))["steps"]]
    assert steps[-len(uploads.ATTACH_STEPS) :] == uploads.ATTACH_STEPS
    rec = db.one("SELECT * FROM $r", r=R("recording", rid))
    path = pathlib.Path(rec["path"])
    assert path.read_bytes() == data and rec["source"] == "audio" and 2400 <= rec["duration_ms"] <= 2600
    assert rec["fingerprint"] == ingest.fingerprint(path) and rec["fp_key"] == f"{rec['space']}:{rec['fingerprint']}"
    assert db.values("SELECT VALUE detail.attached FROM audit_log WHERE action = 'upload'") == [True]

    # processing keeps the transcript and its speakers, and draws the waveform
    drain(db, cfg)
    assert db.one("SELECT status FROM $j", j=R("job", imported))["status"] == "succeeded"
    player = client.get(f"/api/v1/recordings/{rid}/player", headers=he).json()
    assert player["audio"] and player["envelope"] and [s["text"] for s in player["segments"]][0] == "A short clip about the capsid."
    assert db.one("SELECT diarizer FROM $r", r=R("recording", rid))["diarizer"] == "labels"

    # a recording with audio can't take more; the same file uploaded on its own finds this recording
    assert _attach(client, he, data, rid).status_code == 409
    again = _upload(client, he, data, name="again.wav")
    assert (again["recording"], again["duplicate"]) == (rid, True)


def test_steps_added_while_a_job_runs_are_not_missed(db, cfg, folder, monkeypatch):
    rid = ingest.import_text(db, cfg, "pods", "[00:00] Alice: Hello there.\n[00:02] Bob: Hi.")
    jid = jobs.enqueue(db, rid, ["analyze"])
    analyze = jobs.STEPS["analyze"]

    def analyze_then_more(db_, cfg_, rid_, say, spec=None):
        analyze(db_, cfg_, rid_, say, spec)
        assert jobs.add_steps(db, rid, ["report"]) == jid  # while it runs: added to it

    monkeypatch.setitem(jobs.STEPS, "analyze", analyze_then_more)
    drain(db, cfg)
    job = db.one("SELECT status, step_index, steps, log FROM $j", j=R("job", jid))
    assert job["status"] == "succeeded" and [s["type"] for s in job["steps"]] == ["analyze", "report"] and job["step_index"] == 2
    assert any("report done" in line for line in job["log"])
    # with nothing running, added steps are a job of their own
    assert jobs.add_steps(db, rid, ["report"]) != jid
