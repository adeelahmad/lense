"""Content types: four base types, a vocabulary of subtypes people can change, how a resource gets one, and which
pipeline that runs."""

from __future__ import annotations

import pytest

from app.domain import content_types, ingest, jobs, pipelines, store
from tests.helpers import login, make_user


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def test_content_types_choose_the_pipeline(client, new_client, db, cfg):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "root@x.io", "root password 1")
    he = login(new_client(), "ed@x.io", "editor password 1")

    cat = client.get("/api/v1/content-types", headers=he).json()
    assert cat["bases"] == ["video", "audio", "image", "text"]
    general = [t["key"] for t in cat["types"] if t["general"]]
    assert general == ["video", "audio", "image", "text"]
    assert {"podcast", "interview", "screen_tutorial", "transcript", "document"} <= {t["key"] for t in cat["types"]}
    assert all(t["pipeline"] is None for t in cat["types"])  # nothing changes until someone sets one

    # the vocabulary is the admins' to change
    new = {"base": "audio", "label": "Sermon", "rules": {"pattern": "sermon|homily"}}
    assert client.post("/api/v1/content-types", headers=he, json=new).status_code == 403
    assert client.post("/api/v1/content-types", headers=h, json={**new, "base": "smell"}).status_code == 422
    assert client.post("/api/v1/content-types", headers=h, json={**new, "rules": {"pattern": "("}}).status_code == 400
    r = client.post("/api/v1/content-types", headers=h, json=new)
    assert r.status_code == 200, r.text
    assert (r.json()["key"], r.json()["builtin"], r.json()["rules"]) == ("sermon", False, {"pattern": "sermon|homily"})
    assert client.post("/api/v1/content-types", headers=h, json=new).status_code == 400  # taken
    r = client.patch("/api/v1/content-types/podcast", headers=h, json={"label": "Show", "rules": {"extensions": ["MP3"]}})
    assert (r.json()["label"], r.json()["rules"]) == ("Show", {"extensions": [".mp3"]})
    assert client.patch("/api/v1/content-types/audio", headers=h, json={"label": "Sound"}).json()["label"] == "Sound"
    assert client.delete("/api/v1/content-types/audio", headers=h).status_code == 400  # a general one stays
    assert client.patch("/api/v1/content-types/nope", headers=h, json={"label": "x"}).status_code == 404

    # how a resource gets its subtype: recognised from the file, else the general one, else as chosen
    rid = ingest.import_text(db, cfg, "pods", "Alice: Hi.\nBob: Hello.", title="Weekly sync")
    db.q("UPDATE $r SET path = '/in/weekly.srt'", r=store.R("recording", rid))
    got = client.get(f"/api/v1/recordings/{rid}/content-type", headers=he).json()
    assert (got["content_type"]["key"], got["chosen"]) == ("transcript", False)
    db.q("UPDATE $r SET path = '/in/weekly.txt'", r=store.R("recording", rid))
    assert content_types.of_recording(db, rid)[0]["key"] == "text"
    assert client.put(f"/api/v1/recordings/{rid}/content-type", headers=he, json={"content_type": "podcast"}).status_code == 400
    r = client.put(f"/api/v1/recordings/{rid}/content-type", headers=he, json={"content_type": "document"})
    assert (r.json()["content_type"]["key"], r.json()["chosen"]) == ("document", True)

    # which pipeline runs: the namespace's override, else the subtype's, else the namespace default, else standard
    a = pipelines.create(db, "Docs", ["analyze"])
    b = pipelines.create(db, "Pods docs", ["analyze", "report"])
    c = pipelines.create(db, "Default", ["analyze", "summarize"])
    sid = store.ns_id(db, "pods")

    def runs():
        jid = jobs.enqueue(db, rid, by="test")
        name = db.one("SELECT pipeline FROM $j", j=store.R("job", jid))["pipeline"]["name"]
        db.q("DELETE $j", j=store.R("job", jid))
        return name

    assert runs() == "Standard"
    client.patch("/api/v1/namespaces/pods", headers=h, json={"pipeline": c})
    assert runs() == "Default"
    client.patch("/api/v1/content-types/document", headers=h, json={"pipeline": a})
    assert runs() == "Docs"
    assert client.patch("/api/v1/namespaces/pods", headers=h, json={"pipelines": {"document": b}}).status_code == 200
    assert runs() == "Pods docs"
    listed = {p["id"]: p for p in client.get("/api/v1/pipelines", headers=h).json()["pipelines"]}
    assert listed[a]["subtypes"] == ["document"] and listed[b]["content_types"] == [{"namespace": "pods", "content_type": "document"}]
    assert pipelines.resolve(db, sid, a)[1]["name"] == "Docs"  # one chosen for the run wins

    # removing a subtype: resources that had it are recognised again, overrides for it go
    client.post("/api/v1/content-types", headers=h, json={"base": "text", "label": "Minutes"})
    client.put(f"/api/v1/recordings/{rid}/content-type", headers=he, json={"content_type": "minutes"})
    client.patch("/api/v1/namespaces/pods", headers=h, json={"pipelines": {"minutes": a}})
    assert client.delete("/api/v1/content-types/minutes", headers=h).status_code == 200
    assert content_types.of_recording(db, rid)[1] is False
    assert db.one("SELECT pipelines FROM $s", s=store.R("space", sid))["pipelines"] == {"document": b}


def test_base_types_and_default_rules(db, cfg):
    types = content_types.all_types(db)

    def sub(path, media=None, source="audio"):
        rec = {"source": source, "path": path, "title": None, "media": media}
        return content_types.base_of(rec), content_types.recognise(db, rec, types)["key"]

    # uploads and scans start as audio; a video file not probed yet is still video
    assert sub("/in/zoom meeting.mp4") == ("video", "meeting_video")
    assert sub("/in/zoom meeting.mp4", {"kind": "audio"}) == ("audio", "meeting_audio")  # probed: no picture in it
    assert sub("/in/lecture.mp4", {"kind": "video"}) == ("video", "video")
    # default patterns match words, not parts of words; _ separates words
    assert sub("/in/team_call.mp3")[1] == "meeting_audio"
    assert sub("/in/total recall.mp3")[1] == "audio"
    assert sub("/in/async notes.mp3")[1] == "audio"
    assert sub("/in/meetup 2024.mp4")[1] == "video"
    assert sub("/in/My Podcast Ep 12.mp3")[1] == "podcast"
    assert sub("/in/invite.ics", source="document")[1] == "calendar_event"
    assert sub("/in/re: contract.eml", source="document")[1] == "email"


def test_seeding_adds_new_defaults_and_keeps_removals(db):
    content_types.seed(db)
    db.q("DELETE content_type:email")  # an archive from before email was a default
    assert content_types.delete(db, "podcast") is None
    content_types.seed(db)
    keys = {t["key"] for t in content_types.all_types(db)}
    assert "email" in keys and "podcast" not in keys  # a removed default stays removed
    with pytest.raises(KeyError):
        content_types.get(db, "podcast")
    assert content_types.create(db, "audio", "Podcast") == "podcast"  # its key can be used again
    t = content_types.get(db, "podcast")
    assert (t["builtin"], t["general"], t["base"]) == (False, False, "audio")
    assert len([t for t in content_types.all_types(db) if t["general"]]) == 4


def test_scanned_files_run_their_content_types_pipeline(db, cfg):
    rid = ingest.import_text(db, cfg, "pods", "Alice: Hi.", title="Weekly sync")
    db.q("UPDATE $r SET source = 'audio', status = 'new', path = '/in/standup.mp3'", r=store.R("recording", rid))
    content_types.update(db, "meeting_audio", {"pipeline": pipelines.create(db, "Meetings", ["transcribe", "analyze"])})
    (jid,) = jobs.enqueue_pending(db, by="test")
    job = db.one("SELECT pipeline, steps FROM $j", j=store.R("job", jid))
    assert job["pipeline"]["name"] == "Meetings" and [s["type"] for s in job["steps"]] == ["transcribe", "analyze"]
