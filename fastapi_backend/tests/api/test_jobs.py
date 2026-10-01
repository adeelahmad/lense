"""Audio with byte ranges, the job queue (cancel, retry, batch steps), the live event feed and file imports."""

from __future__ import annotations

import base64
import json
import time

from app.domain import ingest, jobs, store
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


def test_a_runs_whole_log(client, new_client, db, cfg, folder, monkeypatch):
    a, _b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hv = login(client, "vi@x.io", "viewer password 1")
    analyze = jobs.STEPS["analyze"]

    def chatty(db_, cfg_, rid, say, spec=None):
        for n in range(450):
            say(f"line {n}")
        analyze(db_, cfg_, rid, say, spec)

    monkeypatch.setitem(jobs.STEPS, "analyze", chatty)
    jid = jobs.enqueue(db, a, ["analyze"])
    drain(db, cfg)
    job = client.get(f"/api/v1/jobs/{jid}", headers=hv).json()
    assert len(job["log"]) == 200 and job["log_total"] >= 452  # the run keeps its last 200; all are kept apart
    whole = client.get(f"/api/v1/jobs/{jid}/log", headers=hv).json()
    assert (whole["start"], whole["total"], whole["more"]) == (0, job["log_total"], False)
    assert [x.split(" ", 1)[1] for x in whole["lines"][:2]] == ["line 0", "line 1"] and whole["lines"][-200:] == job["log"]
    page = client.get(f"/api/v1/jobs/{jid}/log", params={"after": 100, "limit": 50}, headers=hv).json()
    assert page["lines"] == whole["lines"][100:150] and page["more"]
    end = client.get(f"/api/v1/jobs/{jid}/log", params={"after": whole["total"]}, headers=hv).json()
    assert (end["lines"], end["more"]) == ([], False)

    # the event stream follows one job, with its log lines
    ev = client.get("/api/v1/events", params={"since": "0", "once": "true", "logs": jid}, headers=hv)
    events = [block for block in ev.text.split("\n\n") if block.startswith("event:")]
    assert [b.split("\n")[0] for b in events] == ["event: job", "event: log"]
    payload = json.loads(events[1].split("data: ", 1)[1])
    assert payload["job"] == jid and payload["start"] == whole["total"] - 200 and payload["lines"] == whole["lines"][-200:]
    # not for a job in a namespace you can't read
    other = jobs.enqueue(db, call, ["analyze"])
    assert client.get(f"/api/v1/jobs/{other}/log", headers=hv).status_code == 404
    assert client.get("/api/v1/events", params={"once": "true", "logs": other}, headers=hv).status_code == 404

    # a run from before whole logs were kept: its last 200 lines
    db.q("UPDATE $j SET log = ['old 1', 'old 2'], log_total = NONE", j=store.R("job", other))
    db.q("DELETE job_log WHERE job = $j", j=other)
    make_user(db, "root@x.io", "root password 1", admin=True)
    root = login(new_client(), "root@x.io", "root password 1")
    assert client.get(f"/api/v1/jobs/{other}/log", headers=root).json()["lines"] == ["old 1", "old 2"]


def test_a_run_keeps_at_most_so_many_lines(db, cfg, folder, monkeypatch):
    a = seed(db, cfg, folder)[0]
    monkeypatch.setattr(jobs, "LOG_MAX", 30)
    monkeypatch.setitem(jobs.STEPS, "analyze", lambda db_, cfg_, rid, say, spec=None: [say(f"n{n}") for n in range(50)])
    jid = jobs.enqueue(db, a, ["analyze"])
    drain(db, cfg)
    lines, total = jobs.log_lines(db, jid, 0, 1000)
    assert total == 30 and lines[0].endswith("n0")
    assert "analyze done" in db.one("SELECT log FROM $j", j=store.R("job", jid))["log"][-1]  # the run's own tail goes on


def test_a_run_records_each_step(client, db, cfg, folder, monkeypatch):
    from app.domain import pipelines

    a, b, _call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    hv = login(client, "vi@x.io", "viewer password 1")
    he = login(client, "ed@x.io", "editor password 1")
    analyze = jobs.STEPS["analyze"]

    def noted(db_, cfg_, rid, say, spec=None):
        say("thinking")
        pipelines.save_output(db_, rid, "notes", {"text": "x"}, {"template": 7, "version": 2, "model": "m"})
        analyze(db_, cfg_, rid, say, spec)

    def broken(*_a, **_k):
        raise RuntimeError("the disk is full")

    monkeypatch.setitem(jobs.STEPS, "analyze", noted)
    monkeypatch.setitem(jobs.STEPS, "report", broken)
    # no LLM is configured, and a transcript has no media for shots: both skip
    jid = jobs.enqueue(db, a, ["analyze", "summarize", "shots", "report"], by="ed@x.io")
    drain(db, cfg)
    job = client.get(f"/api/v1/jobs/{jid}", headers=hv).json()
    assert (job["status"], job["title"], job["pipeline"]) == ("failed", "ep1", None)
    done, llm, shots, report = job["step_runs"]
    assert (done["outcome"], done["note"], done["worker"] is not None, done["seconds"] >= 0) == ("done", "analysed", True, True)
    assert done["outputs"] == [{"key": "notes", "template": 7, "version": 2, "model": "m"}]
    assert (llm["outcome"], llm["note"], llm["outputs"]) == ("skipped", "no LLM is configured", [])
    assert (shots["outcome"], shots["note"]) == ("skipped", "there is no media file")
    assert (report["outcome"], report["note"]) == ("failed", "RuntimeError: the disk is full")
    # each step's part of the whole log
    lines = [x.split(" ", 1)[1] for x in client.get(f"/api/v1/jobs/{jid}/log", headers=hv).json()["lines"]]
    assert lines[done["log_from"] : done["log_to"]][0] == "thinking" and lines[done["log_to"] - 1].startswith("analyze done in")
    assert lines[llm["log_from"] : llm["log_to"]] == ["summarize skipped: no LLM is configured"]
    assert lines[report["log_from"] : report["log_to"]] == ["report failed: RuntimeError: the disk is full"]
    # how long steps usually take: from the steps that finished (a failure doesn't count)
    assert job["estimates"][:3] == [done["seconds"], llm["seconds"], shots["seconds"]] and job["estimates"][3] is None
    assert job["eta_seconds"] is None  # finished

    # retrying runs the failed step afresh; nothing to go by for it yet, so no time left either
    assert client.post(f"/api/v1/jobs/{jid}/retry", headers=he).status_code == 200
    job = client.get(f"/api/v1/jobs/{jid}", headers=hv).json()
    assert (job["status"], len(job["step_runs"]), job["eta_seconds"]) == ("queued", 3, None)
    monkeypatch.setitem(jobs.STEPS, "report", lambda db_, cfg_, rid, say, spec=None: say("wrote the report"))
    drain(db, cfg)
    job = client.get(f"/api/v1/jobs/{jid}", headers=hv).json()
    assert job["status"] == "succeeded" and [r["outcome"] for r in job["step_runs"]] == ["done", "skipped", "skipped", "done"]
    # the next run of the same steps has an estimate for each, and about as long to go as they add up to
    nxt = jobs.enqueue(db, b, ["analyze", "summarize", "shots", "report"])
    est = client.get(f"/api/v1/jobs/{nxt}", headers=hv).json()
    assert None not in est["estimates"] and est["eta_seconds"] == round(sum(est["estimates"]), 1)


def test_estimates(db, cfg, folder):
    a = seed(db, cfg, folder)[0]
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav, seconds=3.0)
    tr.write_text("[00:00] Alice: A short clip.\n[00:01] Bob: Indeed.\n[00:02] Alice: Bye.")
    audio = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    t = [[60.0, 1.0, False], [120.0, 2.0, False], [30.0, 1.0, False], [1.0, 1.0, True]]
    for kind, samples in {
        "transcribe": t,
        "shots": [[0.2, 1.0, True], [0.4, 1.0, True]],
        "llm:template": [[4.0, None, False]],
        "llm:9": [[10.0, None, False]],
        "report:template": [[0.5, None, False]],
    }.items():
        db.q("UPSERT $s SET kind = $k, samples = $x", s=store.R("step_stat", kind), k=kind, x=samples)
    steps = [
        "transcribe",
        "shots",
        {"type": "llm", "template": 9},
        {"type": "llm", "template": 3},
        "report",
        {"type": "report", "template": 4},
    ]
    # transcribing takes about a minute per minute of audio (3 s here); a template's own timings win over any
    # template's; the namespace's report pages are nothing like a report template
    assert jobs.estimates(db, {"steps": steps, "recording": audio}) == [3.0, 0.3, 10.0, 4.0, None, 0.5]
    # media steps skip a recording without media
    assert jobs.estimates(db, {"steps": steps[:2], "recording": a}) == [1.0, 0.3]

    now = jobs.dt.datetime(2026, 1, 1, 12, 0, 10, tzinfo=jobs.dt.timezone.utc)
    running = {
        "status": "running",
        "step_index": 1,
        "step_runs": [{"outcome": "done"}, {"outcome": "running", "started_at": "2026-01-01T12:00:00+00:00"}],
    }
    assert jobs.eta(running, [5.0, 30.0, 20.0], now) == 40.0
    assert jobs.eta(running, [5.0, 8.0, 20.0], now) == 20.0  # over its usual time: nothing left of it
    assert jobs.eta({**running, "status": "queued"}, [5.0, 30.0, 20.0], now) == 50.0
    assert jobs.eta(running, [5.0, 30.0, None], now) is None


def test_a_silent_worker_fails_its_step(db, cfg, folder):
    a = seed(db, cfg, folder)[0]
    jid = jobs.enqueue(db, a, ["analyze"])
    job = jobs.claim(db, "gone", {"analyze"})
    db.q(
        "UPDATE $j SET heartbeat_at = '2000-01-01T00:00:00+00:00', step_runs = [{outcome: 'running', started_at: '2000-01-01T00:00:00+00:00'}]",
        j=store.R("job", job["id"]),
    )
    jobs.reap(db, stale_minutes=15, max_attempts=1)
    j = jobs.get(db, jid)
    assert (j["status"], j["finished_at"] is not None) == ("failed", True)
    assert (j["step_runs"][0]["outcome"], j["step_runs"][0]["note"]) == ("failed", "the worker stopped responding")


def test_pausing_draining_and_resuming_workers(client, db, cfg, folder, monkeypatch):
    a, b, _call = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    hr = login(client, "root@x.io", "root password 1")
    he = login(client, "ed@x.io", "editor password 1")
    mac = jobs.Worker(db, lambda: cfg, name="mac", steps=["analyze", "report"])
    gpu = jobs.Worker(db, lambda: cfg, name="gpu", steps=["analyze", "report"])
    mac.register()
    gpu.register()
    assert client.post("/api/v1/workers/mac/pause", headers=he).status_code == 403  # admins only
    assert client.post("/api/v1/workers/nobody/pause", headers=hr).status_code == 404
    assert client.post("/api/v1/workers/mac/juggle", headers=hr).status_code == 422

    # paused: it takes no new runs, and stays paused when it checks in again (as after a restart)
    w = client.post("/api/v1/workers/mac/pause", headers=hr).json()
    assert (w["paused"], w["draining"], w["paused_by"]) == (True, False, "root@x.io")
    mac.register()
    jid = jobs.enqueue(db, a, ["analyze"])
    assert mac.drain() == 0 and jobs.get(db, jid)["status"] == "queued"
    assert client.post("/api/v1/workers/mac/resume", headers=hr).json()["paused"] is False
    assert mac.drain() == 1 and jobs.get(db, jid)["status"] == "succeeded"
    audit = db.values("SELECT VALUE action FROM audit_log WHERE string::starts_with(action, 'worker.')")
    assert sorted(audit) == ["worker.pause", "worker.resume"]

    # draining: the run it has goes back on the queue after the step it's on, for another worker; it stays paused
    def admin_drains(db_, cfg_, rid, say, spec=None):
        client.post("/api/v1/workers/mac/drain", headers=hr)
        say("analysed")

    monkeypatch.setitem(jobs.STEPS, "analyze", admin_drains)
    jid = jobs.enqueue(db, b, ["analyze", "report"])
    assert mac.drain() == 1
    j = jobs.get(db, jid)
    assert (j["status"], j["step_index"], j["next_step"], j.get("worker")) == ("queued", 1, "report", None)
    assert "mac is draining: handing report to another worker" in " ".join(j["log"])
    w = next(x for x in client.get("/api/v1/workers", headers=hr).json() if x["name"] == "mac")
    assert (w["paused"], w["draining"], w["current"]) == (True, False, None)
    assert gpu.drain() == 1 and jobs.get(db, jid)["status"] == "succeeded"
    # an idle worker has nothing to hand back: draining it just pauses it
    assert client.post("/api/v1/workers/gpu/drain", headers=hr).json()["draining"] is False
    # asked to drain just as its run ended: the flag doesn't outlive the run
    db.q("UPDATE worker:gpu SET drain = true")
    assert gpu.drain() == 0 and not db.one("SELECT drain FROM worker:gpu")["drain"]

    # load: the machine's, with each heartbeat, and the steps each finished in the last hour
    listed = {x["name"]: x for x in client.get("/api/v1/workers", headers=hr).json()}
    assert listed["mac"]["steps_last_hour"] == 2 and listed["gpu"]["steps_last_hour"] == 1
    assert listed["mac"]["cpus"] >= 1 and (listed["mac"]["load"] is None or listed["mac"]["load"] >= 0)


def test_a_busy_worker_keeps_its_heartbeat(db, cfg, folder, monkeypatch):
    a = seed(db, cfg, folder)[0]
    monkeypatch.setattr(jobs, "WORKER_BEAT", 0)
    seen = []

    def slow(db_, cfg_, rid, say, spec=None):
        db_.q("UPDATE worker:slow SET heartbeat_at = '2000-01-01T00:00:00+00:00'")
        time.sleep(2.5)  # longer than the heartbeat thread's tick
        seen.append(db_.one("SELECT heartbeat_at FROM worker:slow")["heartbeat_at"])

    monkeypatch.setitem(jobs.STEPS, "analyze", slow)
    jobs.enqueue(db, a, ["analyze"])
    assert jobs.Worker(db, lambda: cfg, name="slow", steps=["analyze"]).drain() == 1
    assert seen[0] > "2000-01-01T00:00:00+00:00"  # it beat during the step


def test_jobs_of_a_namespace_or_batch(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    hr = login(client, "root@x.io", "root password 1")
    he = login(client, "ed@x.io", "editor password 1")
    in_batch = [jobs.enqueue(db, a, ["analyze"], batch=7), jobs.enqueue(db, call, ["analyze"], batch=7)]
    alone = jobs.enqueue(db, b, ["report"])
    jobs.cancel(db, alone)

    pods = client.get("/api/v1/jobs", params={"namespace": "pods"}, headers=he).json()
    assert {j["id"] for j in pods["jobs"]} == {in_batch[0], alone}
    assert pods["counts"] == {"queued": 1, "cancelled": 1} and pods["namespaces"] == {"pods": 2}
    # counts stay those of the namespace whatever the status filter
    queued = client.get("/api/v1/jobs", params={"namespace": "pods", "status": "queued"}, headers=he).json()
    assert [j["id"] for j in queued["jobs"]] == [in_batch[0]] and queued["counts"] == pods["counts"]
    assert client.get("/api/v1/jobs", params={"namespace": "calls"}, headers=he).status_code == 404  # no role there
    assert client.get("/api/v1/jobs", params={"namespace": "nope"}, headers=hr).status_code == 404

    # a batch run's jobs, in the namespaces you can read
    assert {j["id"] for j in client.get("/api/v1/jobs", params={"batch": 7}, headers=hr).json()["jobs"]} == set(in_batch)
    mine = client.get("/api/v1/jobs", params={"batch": 7}, headers=he).json()
    assert [j["id"] for j in mine["jobs"]] == [in_batch[0]] and mine["counts"] == {"queued": 1}
    everyone = client.get("/api/v1/jobs", headers=hr).json()
    assert everyone["namespaces"] == {"pods": 2, "calls": 1} and sum(everyone["counts"].values()) == 3
    assert client.get("/api/v1/jobs", params={"batch": 8}, headers=hr).json()["jobs"] == []
