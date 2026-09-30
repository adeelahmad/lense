"""Audio with byte ranges, the job queue (cancel, retry, batch steps), the live event feed and file imports."""

from __future__ import annotations

import base64

from app.domain import ingest, jobs
from tests.helpers import drain, login, make_user, quiet, seed, write_docx, write_wav


def test_audio_jobs_events_imports(client, db, cfg, folder):
    seed(db, cfg, folder)
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text("[00:00] Alice: A short clip about the capsid.\n[00:02] Bob: Indeed it is short.\n[00:02] Alice: Bye.")
    rid = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    make_user(db, "root@x.io", "root password 1", admin=True)
    c = client
    h = login(c, "root@x.io", "root password 1")
    r = c.get(f"/api/v1/recordings/{rid}/audio", headers={**h, "Range": "bytes=10-19"})
    assert (r.status_code, r.content) == (206, wav.read_bytes()[10:20])
    assert c.get(f"/api/v1/recordings/{rid}/audio", headers={**h, "Range": "bytes=999999-"}).status_code == 416
    assert "frame-ancestors 'none'" in c.get("/api/v1/jobs", headers=h).headers["content-security-policy"]  # legacy checked "/"
    jid = c.post("/api/v1/jobs", json={"recordings": [rid], "steps": ["analyze", "report"]}, headers=h).json()["jobs"][0]
    assert c.post(f"/api/v1/jobs/{jid}/cancel", headers=h).status_code == 200
    assert c.get(f"/api/v1/jobs/{jid}", headers=h).json()["status"] == "cancelled"
    assert c.post(f"/api/v1/jobs/{jid}/retry", headers=h).status_code == 200
    drain(db, cfg)
    assert c.get(f"/api/v1/jobs/{jid}", headers=h).json()["status"] == "succeeded"
    p = c.get(f"/api/v1/recordings/{rid}/player", headers=h).json()
    assert p["audio"] and p["envelope"] and len(p["segments"]) == 3
    ev = c.get("/api/v1/events", params={"since": "0", "once": "true"}, headers=h)
    assert "event: job" in ev.text
    assert (ev.headers["cache-control"], ev.headers["x-accel-buffering"]) == ("no-cache, no-transform", "no")
    assert c.post("/api/v1/jobs/steps/report", headers=h).json()["queued"] >= 1  # the web app's batch button
    assert "running" in c.get("/api/v1/jobs", headers=h).json()
    assert c.post("/api/v1/import", headers=h, json={"namespace": "notes", "filename": "x.exe", "data": "AAAA"}).status_code == 400
    dx = folder / "m.docx"
    write_docx(dx, ["Ann: The docx upload works.", "Ben: Good.", "Ann: Done."])
    r = c.post(
        "/api/v1/import", headers=h, json={"namespace": "notes", "filename": "m.docx", "data": base64.b64encode(dx.read_bytes()).decode()}
    )
    assert r.status_code == 200, r.text
    assert c.get(f"/api/v1/recordings/{r.json()['id']}/player", headers=h).json()["segments"][0]["text"] == "The docx upload works."


def test_job_rules(client, new_client, db, cfg, folder):
    a, _b, call = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    hr = login(client, "root@x.io", "root password 1")
    hv = login(client, "vi@x.io", "viewer password 1")
    he = login(client, "ed@x.io", "editor password 1")
    other = jobs.enqueue(db, call, ["analyze"])
    mine = jobs.enqueue(db, a, ["analyze"])
    # jobs in namespaces you can't read are absent; viewers can't change them
    assert client.get(f"/api/v1/jobs/{other}", headers=he).status_code == 404
    assert client.get(f"/api/v1/jobs/{mine}", headers=hv).status_code == 200
    assert client.post(f"/api/v1/jobs/{mine}/cancel", headers=hv).status_code == 403
    assert {j["id"] for j in client.get("/api/v1/jobs", headers=he).json()["jobs"]} == {mine}
    assert {j["id"] for j in client.get("/api/v1/jobs", headers=hr).json()["jobs"]} == {mine, other}
    listed = client.get("/api/v1/jobs", params={"status": "queued", "limit": 5}, headers=hr).json()
    assert listed["running"] and listed["counts"]["queued"] == 2 and listed["returncode"] is None
    assert client.get("/api/v1/jobs", params={"limit": 0}, headers=hr).status_code == 422
    # retrying needs a failed or cancelled job; queueing checks the steps
    assert client.post(f"/api/v1/jobs/{mine}/retry", headers=he).status_code == 400
    assert client.post("/api/v1/jobs", json={"recordings": [a], "steps": ["nope"]}, headers=he).status_code == 400
    assert client.post("/api/v1/jobs", json={"recordings": [call]}, headers=he).status_code == 404
    assert client.post("/api/v1/jobs", json={"namespace": "calls"}, headers=he).status_code == 404
    # batch steps: editors queue within their namespaces; scanning is for admins; unknown steps are listed
    assert client.post("/api/v1/jobs/steps/scan", headers=he).status_code == 403
    r = client.post("/api/v1/jobs/steps/nope", headers=he)
    assert r.status_code == 404 and "summarize" in r.json()["detail"]
    assert client.post("/api/v1/jobs/steps/summarize", headers=hv).json()["queued"] == 0  # viewers edit nothing
    # workers are for admins; the event feed needs a person and only shows readable namespaces
    assert client.get("/api/v1/workers", headers=he).status_code == 403
    assert client.get("/api/v1/workers", headers=hr).status_code == 200
    assert new_client().get("/api/v1/events", params={"once": "true"}).status_code == 401
    feed = client.get("/api/v1/events", params={"since": "0", "once": "true"}, headers=he).text
    assert f'"id": {mine}' in feed and f'"id": {other},' not in feed


def test_read_only_tokens_cannot_queue(client, db, cfg, folder):
    a, _b, _c = seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "ed@x.io", "editor password 1")
    tok = client.post("/api/v1/tokens", json={"name": "ci", "scope": "read"}, headers=h).json()["token"]
    bearer = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/jobs", headers=bearer).status_code == 200
    assert client.post("/api/v1/jobs", json={"recordings": [a]}, headers=bearer).status_code == 403
    assert client.post(f"/api/v1/recordings/{a}/reprocess", headers=bearer).status_code == 403
