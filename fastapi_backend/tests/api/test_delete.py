"""Deleting a recording (docs/api.md): everything Lens made from it goes, the media file stays, and scans and watched
folders don't import it again."""

from __future__ import annotations

import pathlib

import pytest

from app.domain import analyze, deletion, ingest, jobs, keyring, metadata, render, sources, speakers, store, video
from tests.api.test_iiif import CLIP
from tests.helpers import login, make_user, quiet, seed, write_wav

R = store.R


@pytest.fixture
def env(app, db, cfg, folder, client):
    a, b, call = seed(db, cfg, folder)
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text(CLIP)
    clip = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "guest@x.io", "guest password 1")
    return {
        "app": app,
        "a": a,
        "b": b,
        "call": call,
        "clip": clip,
        "wav": wav,
        "ho": login(client, "own@x.io", "owner password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hg": login(client, "guest@x.io", "guest password 1"),
    }


def _count(db, table, rid):
    return len(db.values(f"SELECT VALUE id FROM {table} WHERE recording = $r", r=rid))


def test_owners_delete_a_recording(client, env, db, cfg):
    clip, ho, he, hg = env["clip"], env["ho"], env["he"], env["hg"]
    url = f"/api/v1/recordings/{clip}"
    title = "Capsid clip QZX"
    db.q("UPDATE $r SET title = $t", r=R("recording", clip), t=title)
    # things that hang off it
    metadata.save(db, cfg, clip, {"access": "public"}, user="own@x.io")
    client.post(f"{url}/permissions", headers=ho, json={"email": "guest@x.io"})
    tok = client.post(f"{url}/share", headers=he).json()["token"]
    client.get(f"/embed/{clip}", params={"s": tok}, headers={"Referer": "https://blog.example.org/", "Sec-Fetch-Dest": "iframe"})
    gid = client.post("/api/v1/namespaces/pods/ip-groups", headers=ho, json={"name": "Lab", "ranges": ["198.51.100.7"]}).json()["groups"][
        0
    ]["id"]
    client.put(f"{url}/ip-groups/{gid}", headers=ho)
    cid = client.post("/api/v1/collections", headers=he, json={"name": "Mine", "recordings": [clip, env["a"]]}).json()["id"]
    chat = client.post("/api/v1/chats", headers=he, json={"title": "About the clip", "scope": {"recordings": [clip, env["a"]]}}).json()[
        "id"
    ]
    render.build_reports(db, cfg, ns="pods", log=quiet)
    reports = pathlib.Path(cfg["data_dir"]) / "reports" / "pods"
    assert (reports / f"{render.slug(title)}-{clip}.html").exists() and (reports / "index.html").exists()
    frames = video.frames_dir(cfg, clip)
    frames.mkdir(parents=True)
    (frames / "shot0001.jpg").write_bytes(b"jpg")
    queued = jobs.enqueue(db, clip, ["report"], by="test")
    assert _count(db, "segment", clip) and _count(db, "appearance", clip) and _count(db, "permission", clip)
    assert _count(db, "share_embed", clip)

    # owners only; the namespace looks absent to people without a role
    assert client.delete(url, headers=he).status_code == 403
    assert client.delete(url, headers=hg).status_code == 404
    r = client.delete(url, headers=ho)
    assert r.status_code == 200, r.text
    assert client.get(url, headers=ho).status_code == 404
    assert client.delete(url, headers=ho).status_code == 404

    # everything Lens made from it is gone
    for table in deletion.OWN:
        assert _count(db, table, clip) == 0, table
    assert not db.values("SELECT VALUE id FROM meta_edit WHERE target = $t", t=f"recording:{clip}")
    assert db.one("SELECT recordings FROM $g", g=R("ip_group", gid))["recordings"] == []
    assert db.one("SELECT recordings FROM $c", c=R("saved_collection", cid))["recordings"] == [env["a"]]
    assert db.one("SELECT scope FROM $c", c=R("chat", chat))["scope"]["recordings"] == [env["a"]]
    assert not frames.exists() and not (reports / f"{render.slug(title)}-{clip}.html").exists()
    assert title.encode() not in keyring.read_plain(db, cfg, reports / "index.html")  # the overview was rewritten
    # its waiting job is cancelled and can't come back
    assert jobs.get(db, queued)["status"] == "cancelled"
    assert client.post(f"/api/v1/jobs/{queued}/retry", headers=ho).status_code == 400
    # the media file stays; harvesters hear it's gone; the audit log keeps what it was
    assert env["wav"].exists()
    assert db.rows("SELECT type, at FROM iiif_activity WHERE recording = $r ORDER BY at", r=clip)[-1]["type"] == "Delete"
    audit = db.rows("SELECT action, target, detail, email FROM audit_log WHERE action = 'recording.delete'")
    assert [(x["target"], x["detail"]["title"], x["detail"]["namespace"], x["email"]) for x in audit] == [
        (f"recording:{clip}", title, "pods", "own@x.io")
    ]
    # the rest of the namespace is untouched
    assert client.get(f"/api/v1/recordings/{env['a']}", headers=ho).status_code == 200
    assert client.get(f"/api/v1/public/recordings/{clip}", headers=hg).status_code == 404


def test_a_running_job_holds_it_back(client, env, db):
    clip, ho = env["clip"], env["ho"]
    jid = jobs.enqueue(db, clip, ["report"], by="test")
    db.q("UPDATE $j SET status = 'running'", j=R("job", jid))
    r = client.delete(f"/api/v1/recordings/{clip}", headers=ho)
    assert r.status_code == 409 and "Cancel it" in r.json()["detail"]
    assert client.get(f"/api/v1/recordings/{clip}", headers=ho).status_code == 200  # nothing happened
    db.q("UPDATE $j SET status = 'succeeded'", j=R("job", jid))
    assert client.delete(f"/api/v1/recordings/{clip}", headers=ho).status_code == 200
    # a worker that finds its recording gone stops
    db.q("UPDATE $j SET status = 'queued', next_step = 'report', step_index = 0", j=R("job", jid))
    job = jobs.claim(db, "w", {"report"})
    assert job and jobs.run_job(db, lambda: None, job, "w", {"report"}) == "cancelled"
    assert "the recording was deleted" in " ".join(jobs.get(db, jid)["log"])


def test_speakers_only_it_had_go_unless_named(client, env, db):
    clip, ho = env["clip"], env["ho"]
    pods = store.ns_id(db, "pods")
    shared = set(db.values("SELECT VALUE speaker FROM appearance WHERE recording = $r", r=clip))  # Alice and Bob talk in ep1 too
    named, unnamed = speakers.new_speaker(db, pods, name="Alice Liddell"), speakers.new_speaker(db, pods)
    for s, label in ((named, "X"), (unnamed, "Y")):
        db.q("CREATE appearance CONTENT {recording: $r, speaker: $s, space: $p, local_label: $l}", r=clip, s=s, p=pods, l=label)
    assert client.delete(f"/api/v1/recordings/{clip}", headers=ho).status_code == 200
    assert db.one("SELECT name FROM $r", r=R("speaker", named))["name"] == "Alice Liddell"  # named: stays
    assert not db.one("SELECT id FROM $r", r=R("speaker", unnamed))  # nobody named it, nothing else had it
    assert shared and all(db.one("SELECT id FROM $r", r=R("speaker", s)) for s in shared)


def test_deleted_files_are_not_imported_again(client, env, db, cfg, folder, monkeypatch):
    ho = env["ho"]
    audio = folder / "audio"
    (audio / "copies").mkdir(parents=True)
    write_wav(audio / "talk.wav", seconds=2.5)  # not the fixture's clip
    cfg["namespaces"]["pods"]["paths"] = [str(audio)]
    assert ingest.scan(db, cfg, only="pods", log=quiet)["new"] == 1
    rid = db.values("SELECT VALUE record::id(id) FROM recording WHERE path = $p", p=str(audio / "talk.wav"))[0]
    assert client.delete(f"/api/v1/recordings/{rid}", headers=ho).status_code == 200
    # a scan skips it, and a copy of it elsewhere
    (audio / "copies" / "talk copy.wav").write_bytes((audio / "talk.wav").read_bytes())
    stats = ingest.scan(db, cfg, only="pods", log=quiet)
    assert (stats["new"], stats["deleted"]) == (0, 2)
    assert not db.values("SELECT VALUE id FROM recording WHERE string::contains(path ?? '', 'talk')")
    # importing it on purpose brings it back, and the scan is fine with that
    tr = folder / "talk.txt"
    tr.write_text("[00:00] Ann: It's back.\n[00:01] Ben: Good.")
    back = ingest.import_transcript(db, cfg, "pods", tr, audio=audio / "talk.wav", log=quiet)
    assert db.one("SELECT id FROM $r", r=R("recording", back))
    assert not deletion.gone(db, store.ns_id(db, "pods"))[1]
    assert ingest.scan(db, cfg, only="pods", log=quiet)["new"] == 0

    # a watched folder skips a remote file whose recording was deleted, even when the file changes
    pods = store.ns_id(db, "pods")
    db.q("CREATE storage_source:1 CONTENT {name: 'drive', type: 'drive', params: {}, created_at: time::now()}")
    db.q(
        "CREATE watch_path:1 CONTENT {source: 1, path: '/talks', space: $s, kinds: 'both', backfill: true, stable_seconds: 0, enabled: true}",
        s=pods,
    )
    listing = [{"path": "/talks/one.mp3", "rel": "one.mp3", "size": 10, "modified": "2026-01-01T00:00:00Z"}]
    monkeypatch.setattr(sources, "list_files", lambda *a, **k: listing)
    assert sources.poll_watch(db, cfg, 1, log=quiet)["new"] == 1
    remote = db.values("SELECT VALUE record::id(id) FROM recording WHERE remote.path = '/talks/one.mp3'")[0]
    assert client.delete(f"/api/v1/recordings/{remote}", headers=ho).status_code == 200
    listing[0].update(size=11, modified="2026-02-01T00:00:00Z")
    stats = sources.poll_watch(db, cfg, 1, log=quiet)
    assert (stats["new"], stats["skipped"]) == (0, 1)
    assert not db.values("SELECT VALUE id FROM recording WHERE remote.path = '/talks/one.mp3'")


def test_batches_and_merges_carry_on_without_it(client, env, db):
    from app.domain import entities

    a, b, clip, ho = env["a"], env["b"], env["clip"], env["ho"]
    # a batch run planned over three recordings, sampled on the first: the others wait
    plan = {"selection": {"recordings": [clip, a, b]}, "run": {"steps": ["report"]}, "sample": 1}
    bid = client.post("/api/v1/batches", headers=ho, json=plan).json()["id"]
    db.q("UPDATE job SET status = 'succeeded' WHERE batch = $b", b=bid)
    started = db.one("SELECT started FROM $b", b=R("batch", bid))["started"]
    waiting = next(r for r in (clip, a, b) if r not in started)
    assert client.delete(f"/api/v1/recordings/{waiting}", headers=ho).status_code == 200
    assert waiting not in db.one("SELECT recordings FROM $b", b=R("batch", bid))["recordings"]
    r = client.post(f"/api/v1/batches/{bid}/continue", headers=ho)
    assert r.status_code == 200 and r.json()["result"] == 1  # the one left

    # an entity merged away before its recording was deleted comes back without that recording's mentions
    rest = a if a != waiting else b
    pods = store.ns_id(db, "pods")
    gone_entity = db.values("SELECT VALUE entity FROM mentions WHERE recording = $r", r=rest)[0]
    keep = db.next_id("entity")
    db.q("CREATE $e CONTENT {space: $s, key: 'zqx', ekey: $k, name: 'Zqx', type: 'ORG'}", e=R("entity", keep), s=pods, k=f"{pods}:zqx")
    mid = entities.merge(db, keep, [gone_entity], "own@x.io")
    assert client.delete(f"/api/v1/recordings/{rest}", headers=ho).status_code == 200
    entities.undo_merge(db, mid)
    assert db.one("SELECT id FROM $e", e=R("entity", gone_entity))  # back
    assert not db.values("SELECT VALUE id FROM mentions WHERE recording = $r", r=rest)  # without the deleted mentions
