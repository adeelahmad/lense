"""Recordings: list and detail, exports, transcript edits, shares, reprocessing and outputs."""

from __future__ import annotations

from app.domain import ingest, search, store
from tests.helpers import drain, login, make_user, seed


def test_previews_exports_edits(client, db, cfg, folder):
    """The recordings part of the legacy Workflows.test_previews_exports_edits_health (the import preview is in
    test_imports.py; the LLM check, health and reindex parts are in tests/api/test_admin.py)."""
    a, b, call = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    c = client
    h = login(c, "root@x.io", "root password 1")
    before = len(db.values("SELECT VALUE id FROM recording"))
    pv = c.post("/api/v1/import/preview", headers=h, json={"text": "[00:01] Ann: Hi there.\n[00:04] Ben: Hello.\n[00:07] Ann: Bye."}).json()
    assert (pv["format"], pv["segments"], pv["speakers"]) == ("text", 3, ["Ann", "Ben"])
    assert len(db.values("SELECT VALUE id FROM recording")) == before  # preview saves nothing
    srt = c.get(f"/api/v1/recordings/{call}/export.srt", headers=h).text
    assert "00:00:09,000 --> " in srt
    assert "Dave: Thanks" in srt
    rt = ingest.import_text(db, cfg, "pods", c.get(f"/api/v1/recordings/{call}/export.json", headers=h).text, title="Round trip")
    assert len(db.values("SELECT VALUE id FROM segment WHERE recording = $r", r=rt)) == 3
    r = c.patch(f"/api/v1/recordings/{call}/segments/1", headers=h, json={"text": "Thanks, the shipment leaves on Monday."})
    assert r.status_code == 200, r.text
    drain(db, cfg)
    assert search.search(db, "Monday")["total"] == 1
    assert c.get(f"/api/v1/recordings/{call}/edits", headers=h).json()[0]["before"]["text"][:7] == "Thanks,"
    pods_speaker = db.values("SELECT VALUE record::id(id) FROM speaker WHERE space = $s", s=store.ns_id(db, "pods"))[0]
    assert c.patch(f"/api/v1/recordings/{call}/segments/1", headers=h, json={"speaker": pods_speaker}).status_code == 400
    # settings/llm/test, admin/health and admin/reindex: see tests/api/test_admin.py
    c.post(f"/api/v1/recordings/{call}/share", headers=h, json={"days": 2})
    assert c.get(f"/api/v1/recordings/{call}/shares", headers=h).json()[0]["active"]


def test_list_and_detail(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    rows = client.get("/api/v1/recordings", headers=h).json()
    assert {r["id"] for r in rows} == {a, b}
    row = [r for r in rows if r["id"] == a][0]
    assert row["namespace"] == "pods" and row["media_kind"] == "transcript" and row["poster"] is None
    assert set(row["speakers"].split(",")) == {"Alice", "Bob"}
    assert len(client.get("/api/v1/recordings", params={"limit": 1}, headers=h).json()) == 1
    assert client.get("/api/v1/recordings", params={"limit": 0}, headers=h).status_code == 422
    assert client.get("/api/v1/recordings", params={"ns": "calls"}, headers=h).status_code == 404
    assert client.get("/api/v1/recordings", params={"ns": "nope"}, headers=h).status_code == 404
    d = client.get(f"/api/v1/recordings/{a}", headers=h).json()
    assert d["id"] == a and d["role"] == "viewer" and "envelope" not in d
    assert {s["name"] for s in d["speakers"]} == {"Alice", "Bob"}
    assert client.get("/api/v1/recordings/999", headers=h).status_code == 404
    assert client.get(f"/api/v1/recordings/{a}/export.doc", headers=h).status_code == 404
    r = client.get(f"/api/v1/recordings/{a}/export.vtt", headers=h)
    assert r.text.startswith("WEBVTT") and "attachment" in r.headers["content-disposition"]
    assert client.get(f"/api/v1/recordings/{a}/outputs", headers=h).json() == []
    svg = client.get(f"/api/v1/recordings/{a}/wordcloud.svg", headers=h)
    assert svg.headers["content-type"].startswith("image/svg+xml") and "<svg" in svg.text
    assert client.get(f"/api/v1/recordings/{a}/audio", headers=h).status_code == 404  # a transcript has no audio


def test_edits_shares_and_reprocess_need_editors(client, db, cfg, folder):
    a, _b, _c = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    hv, he = login(client, "vi@x.io", "viewer password 1"), login(client, "ed@x.io", "editor password 1")
    assert client.patch(f"/api/v1/recordings/{a}/segments/0", headers=hv, json={"text": "x"}).status_code == 403
    assert client.post(f"/api/v1/recordings/{a}/share", headers=hv).status_code == 403
    assert client.get(f"/api/v1/recordings/{a}/shares", headers=hv).status_code == 403
    assert client.post(f"/api/v1/recordings/{a}/reprocess", headers=hv).status_code == 403
    assert client.patch(f"/api/v1/recordings/{a}/segments/0", headers=he, json={"text": "  "}).status_code == 400
    assert client.patch(f"/api/v1/recordings/{a}/segments/0", headers=he, json={}).status_code == 400
    assert client.patch(f"/api/v1/recordings/{a}/segments/99", headers=he, json={"text": "x"}).status_code == 404
    r = client.patch(f"/api/v1/recordings/{a}/segments/0", headers=he, json={"speaker": None})
    assert r.status_code == 200 and r.json()["job"]
    assert db.one("SELECT speaker FROM $s", s=store.R("segment", a * store.SEG)).get("speaker") is None
    r = client.post(f"/api/v1/recordings/{a}/reprocess", headers=he, json={"steps": ["analyze"]})
    assert r.status_code == 200 and r.json()["ok"]
    assert client.post(f"/api/v1/recordings/{a}/reprocess", headers=he, json={"steps": ["nope"]}).status_code == 400
    share = client.post(f"/api/v1/recordings/{a}/share", headers=he).json()
    assert share["embed"] == f"/embed/{a}?s={share['token']}"
    assert client.post(f"/api/v1/recordings/{a}/share", headers=he, json={"days": 0}).status_code == 422
    assert client.delete(f"/api/v1/recordings/{a}/share", headers=he).status_code == 200
    assert not client.get(f"/api/v1/recordings/{a}/shares", headers=he).json()[0]["active"]
    actions = db.values("SELECT VALUE action FROM audit_log")
    assert {"transcript.edit", "share.create", "share.revoke"} <= set(actions)


def test_audio_from_storage_sources(client, db, cfg, folder, monkeypatch):
    """Audio kept on a storage source: a local folder, a cached copy of a remote file, or streamed by range."""
    from app.domain import sources
    from tests.helpers import write_wav

    inbox = folder / "inbox"
    inbox.mkdir()
    wav = inbox / "remote.wav"
    write_wav(wav)
    data = wav.read_bytes()
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    pods = store.ns_id(db, "pods")

    def recording(remote, **extra):
        rid = db.next_id("recording")
        db.q(
            "CREATE $r CONTENT $d",
            r=store.R("recording", rid),
            d={"space": pods, "source": "audio", "status": "new", "remote": remote, **extra},
        )
        return rid

    local = sources.create(db, cfg, "inbox", "local")
    rid = recording({"source": local, "path": str(wav)})
    r = client.get(f"/api/v1/recordings/{rid}/audio", headers={**h, "Range": "bytes=4-11"})
    assert (r.status_code, r.content, r.headers["content-type"]) == (206, data[4:12], "audio/wav")
    assert client.get(f"/api/v1/recordings/{rid}/player", headers=h).json()["audio"].startswith(f"/api/v1/recordings/{rid}/audio?")
    outside = recording({"source": local, "path": "/etc/passwd"})
    assert client.get(f"/api/v1/recordings/{outside}/audio", headers=h).status_code == 400
    gone = recording({"source": 9999, "path": str(wav)})
    assert client.get(f"/api/v1/recordings/{gone}/audio", headers=h).status_code == 404

    s3 = sources.create(db, cfg, "bucket", "s3")
    cached = recording({"source": s3, "path": "talks/cached.wav"})
    dest = sources.cache_file(cfg, s3, "talks/cached.wav")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    assert client.get(f"/api/v1/recordings/{cached}/audio", headers=h).content == data

    unknown = recording({"source": s3, "path": "talks/unknown.wav"})
    r = client.get(f"/api/v1/recordings/{unknown}/audio", headers=h)
    assert r.status_code == 404 and "size unknown" in r.json()["detail"]

    calls = []

    def stream(db_, cfg_, sid, path, offset=0, count=None, space=None):
        calls.append((sid, path, offset, count))
        yield data[offset : offset + count]

    monkeypatch.setattr(sources, "stream", stream)
    streamed = recording({"source": s3, "path": "talks/streamed.wav"}, size=len(data))
    r = client.get(f"/api/v1/recordings/{streamed}/audio", headers={**h, "Range": "bytes=100-199"})
    assert (r.status_code, r.content, r.headers["content-range"]) == (206, data[100:200], f"bytes 100-199/{len(data)}")
    assert calls == [(s3, "talks/streamed.wav", 100, 100)]
