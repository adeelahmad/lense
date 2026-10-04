"""Moving a recording to another namespace (docs/api.md): it keeps its transcript, media, permissions and share links,
its IIIF manifest stays as it was, and its speakers, faces and entities belong to the new namespace."""

from __future__ import annotations

import pathlib

import pytest

from app.domain import analyze, auth, deletion, ingest, jobs, metadata, pipelines, render, speakers, store
from tests.api.test_iiif import CLIP
from tests.helpers import drain, login, make_user, quiet, seed, write_wav

R = store.R
CC_BY = "http://creativecommons.org/licenses/by/4.0/"


@pytest.fixture
def env(app, db, cfg, folder, client):
    a, b, call = seed(db, cfg, folder)
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text(CLIP)
    clip = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner", "calls": "editor"})
    make_user(db, "boss@x.io", "boss password 1", roles={"pods": "owner", "calls": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "editor"})
    make_user(db, "guest@x.io", "guest password 1")
    store.ns_id(db, "other")  # nobody has a role there
    return {
        "app": app,
        "a": a,
        "b": b,
        "call": call,
        "clip": clip,
        "wav": wav,
        "pods": store.ns_id(db, "pods"),
        "calls": store.ns_id(db, "calls"),
        "ho": login(client, "own@x.io", "owner password 1"),
        "hb": login(client, "boss@x.io", "boss password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hr": login(client, "root@x.io", "root password 1"),
    }


def _move(client, rid, headers, **body):
    return client.post(f"/api/v1/recordings/{rid}/move", headers=headers, json={"namespace": "calls", **body})


def test_who_moves_what(client, env, db, cfg, folder):
    clip, a = env["clip"], env["a"]
    assert _move(client, clip, env["he"]).status_code == 403  # an editor where it is
    assert _move(client, clip, env["hb"]).status_code == 403  # an owner where it is, but only a viewer there
    assert _move(client, clip, env["ho"], namespace="other").status_code == 404  # no role there: it looks absent
    assert _move(client, clip, env["ho"], namespace="nowhere").status_code == 404
    assert _move(client, clip, env["ho"], namespace="pods").status_code == 400  # already there
    assert _move(client, a, env["ho"], rediarize=True).status_code == 400  # a transcript has no voices to identify
    jid = jobs.enqueue(db, clip, ["report"], by="test")
    db.q("UPDATE $j SET status = 'running'", j=R("job", jid))
    r = _move(client, clip, env["ho"])
    assert r.status_code == 409 and "then move it" in r.json()["detail"]
    db.q("UPDATE $j SET status = 'succeeded'", j=R("job", jid))
    # the same file is already there
    ingest.import_transcript(db, cfg, "calls", folder / "ep1.txt", log=quiet)
    r = _move(client, a, env["ho"])
    assert r.status_code == 409 and "already has the same file" in r.json()["detail"]
    assert db.one("SELECT space FROM $r", r=R("recording", a))["space"] == env["pods"]  # nothing moved


def test_moving_a_recording(client, new_client, env, db, cfg, folder):
    clip, pods, calls, ho = env["clip"], env["pods"], env["calls"], env["ho"]
    url = f"/api/v1/recordings/{clip}"
    # what it has from pods: public with the transcript open, and a rights statement
    metadata.save_namespace(db, pods, profile={"default_access": "public", "default_open": ["transcript"], "defaults": {"rights": CC_BY}})
    assert metadata.effective(db, cfg, clip)["rights"] == CC_BY
    share = auth.create_share(db, clip, 1, 30)["token"]
    client.post(f"{url}/permissions", headers=ho, json={"email": "guest@x.io"})
    gid = client.post("/api/v1/namespaces/pods/ip-groups", headers=ho, json={"name": "Lab", "ranges": ["198.51.100.7"]}).json()["groups"][
        0
    ]["id"]
    client.put(f"{url}/ip-groups/{gid}", headers=ho)
    alone = speakers.new_speaker(db, pods)  # an unnamed speaker only this recording has
    db.q("CREATE appearance CONTENT {recording: $r, speaker: $s, space: $p, local_label: 'Z'}", r=clip, s=alone, p=pods)
    render.build_reports(db, cfg, ns="pods", log=quiet)
    title = db.one("SELECT title FROM $r", r=R("recording", clip))["title"]
    reports = pathlib.Path(cfg["data_dir"]) / "reports"
    assert (reports / "pods" / f"{render.slug(title)}-{clip}.html").exists()
    before = sorted(s["text"] for s in db.rows("SELECT text FROM segment WHERE recording = $r", r=clip))
    notes = reports / "pods" / f"{render.slug(title)}-{clip}--notes.html"  # a template's report, and an export
    notes.write_text("<p>notes</p>")
    pipelines.save_output(db, clip, "report_notes", {"url": f"/reports/pods/{notes.name}"})
    exports = pathlib.Path(cfg["data_dir"]) / "exports"
    (exports / "pods").mkdir(parents=True)
    (exports / "pods" / "clip notes.md").write_text("notes")
    pipelines.save_output(db, clip, "export_clip_notes_md", {"file": "clip notes.md"})

    r = _move(client, clip, ho)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["namespace"], d["pinned"], d["shares_revoked"]) == ("calls", ["access", "open", "rights"], 0)

    # it's in calls, with everything it had
    assert db.one("SELECT space FROM $r", r=R("recording", clip))["space"] == calls
    assert set(db.values("SELECT VALUE space FROM segment WHERE recording = $r", r=clip)) == {calls}
    assert sorted(s["text"] for s in db.rows("SELECT text FROM segment WHERE recording = $r", r=clip)) == before
    assert client.get(url, headers=env["he"]).json()["namespace"] == "calls"
    assert auth.share_ok(db, share, clip)  # share links keep working
    assert client.get(f"{url}/permissions", headers=ho).status_code == 403  # the mover is an editor in calls
    assert [p["email"] for p in client.get(f"{url}/permissions", headers=env["hr"]).json()] == ["guest@x.io"]
    assert db.one("SELECT recordings FROM $g", g=R("ip_group", gid))["recordings"] == []  # pods' IP groups don't open it
    # its manifest stays as it was: public, the transcript open, the same rights
    a = client.get(f"{url}/access", headers=env["hr"]).json()
    assert (a["access"], a["open"], a["inherited"]) == ("public", ["transcript"], False)
    assert metadata.effective(db, cfg, clip)["rights"] == CC_BY
    assert new_client().get(f"/api/v1/public/recordings/{clip}").json()["namespace"] == "calls"
    assert db.rows("SELECT type, at FROM iiif_activity WHERE recording = $r ORDER BY at", r=clip)[-1]["type"] == "Update"
    # speakers are matched by name there: calls has an Alice; Bob starts there; the unnamed one only it had is gone
    names = {s["id"]: s.get("name") for s in db.rows("SELECT record::id(id) AS id, name, space FROM speaker WHERE space = $s", s=calls)}
    used = set(db.values("SELECT VALUE speaker FROM appearance WHERE recording = $r", r=clip))
    assert used <= set(names) and {names[s] for s in used} >= {"Alice", "Bob"}
    assert not db.one("SELECT id FROM $s", s=R("speaker", alone))
    assert db.values("SELECT VALUE id FROM speaker WHERE space = $s AND name = 'Bob'", s=pods)  # Bob still talks in ep1
    # its report page moved with it; analysis runs again there
    assert (reports / "calls" / f"{render.slug(title)}-{clip}.html").exists()
    assert not (reports / "pods" / f"{render.slug(title)}-{clip}.html").exists()
    assert (reports / "calls" / notes.name).exists() and not notes.exists()
    assert db.one("SELECT * FROM $o", o=R("output", f"{clip}-report_notes"))["value"]["url"] == f"/reports/calls/{notes.name}"
    assert (exports / "calls" / "clip notes.md").exists() and not (exports / "pods" / "clip notes.md").exists()
    assert [s["type"] for s in jobs.get(db, d["job"])["steps"]] == ["analyze", "embed", "report"]
    drain(db, cfg)
    assert jobs.get(db, d["job"])["status"] == "succeeded"
    audit = db.rows("SELECT detail FROM audit_log WHERE action = 'recording.move'")
    assert [(x["detail"]["from"], x["detail"]["to"]) for x in audit] == [("pods", "calls")]

    # pods' folder scan doesn't bring it back there
    cfg["namespaces"]["pods"]["paths"] = [str(folder)]
    stats = ingest.scan(db, cfg, only="pods", log=quiet)
    assert (stats["new"], stats["deleted"]) == (0, 1)
    assert deletion.gone(db, pods)[0] and not deletion.gone(db, calls)[0]
    # moved back, pods may find it again, and calls won't
    assert _move(client, clip, env["hr"], namespace="pods").status_code == 200
    assert not deletion.gone(db, pods)[0] and deletion.gone(db, calls)[0]


def test_moving_again_from_its_voices(client, env, db):
    clip, ho = env["clip"], env["ho"]
    raw = auth.create_share(db, clip, 1, 30)["token"]
    r = _move(client, clip, ho, rediarize=True, revoke_shares=True)
    assert r.status_code == 200, r.text
    assert r.json()["shares_revoked"] == 1 and not auth.share_ok(db, raw, clip)
    # the diarize step identifies the speakers again among the new namespace's voices
    assert not db.values("SELECT VALUE id FROM appearance WHERE recording = $r", r=clip)
    assert not [s for s in db.values("SELECT VALUE speaker FROM segment WHERE recording = $r", r=clip) if s]
    assert [s["type"] for s in jobs.get(db, r.json()["job"])["steps"]] == ["diarize", "analyze", "embed", "report"]
