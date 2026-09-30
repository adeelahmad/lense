"""Background work: every long task is a job in SurrealDB, run by workers inside the server or on other machines.

A job carries one recording through a list of steps. A worker only runs the steps it can (a Mac with mlx might
only transcribe); when the next step isn't one of them, the job goes back on the queue for a worker that can.
Workers heartbeat while they run; a job whose worker goes quiet is requeued, up to workers.max_attempts.
"""
from __future__ import annotations

import datetime as dt
import os
import socket
import threading
import time

from . import analyze, ingest, pipelines, render, speakers as spk, store

R = store.R
PIPELINE = ["transcribe", "diarize", "shots", "ocr", "faces", "analyze", "summarize", "report"]
AFTER_IMPORT = ["analyze", "summarize", "report"]
ACTIVE = ["queued", "running"]
FIELDS = ("record::id(id) AS id, recording, space, batch, steps, step_index, next_step, status, worker, error, attempts, created_by, "
          "created_at, started_at, finished_at, updated_at, cancel_requested")


def _source(db, rid):
    return (db.one("SELECT source FROM $r", r=R("recording", rid)) or {}).get("source")


def _spec(s):
    return {"type": s} if isinstance(s, str) else dict(s)


def _transcribe(db, cfg, rid, say, spec=None):
    rec = db.one("SELECT source, engine, status FROM $r", r=R("recording", rid)) or {}
    if rec.get("source") != "audio":
        return say("imported transcript: nothing to transcribe")
    if str(rec.get("engine") or "").startswith("import:") and rec.get("status") != "new" and not (spec or {}).get("force"):
        return say("the transcript came with the media: kept it (force the step to transcribe again)")
    ingest.transcribe_one(db, cfg, rid, say)


def _diarize(db, cfg, rid, say, spec=None):
    rec = db.one("SELECT source, diarizer FROM $r", r=R("recording", rid)) or {}
    if rec.get("source") != "audio" or (rec.get("diarizer") == "labels" and not (spec or {}).get("force")):
        return say("speakers come from the transcript; kept them")
    spk.diarize_one(db, cfg, rid, say)


def _analyze(db, cfg, rid, say, spec=None):
    analyze.analyze_recording(db, cfg, rid)
    say("analysed")


def _summarize(db, cfg, rid, say, spec=None):
    if not (cfg["llm"].get("base_url") and cfg["llm"].get("model")):
        return say("no LLM configured; skipped")
    analyze.summarize_recording(db, cfg, rid)
    say("summarised")


def _report(db, cfg, rid, say, spec=None):
    if spec and spec.get("template"):
        return pipelines.run_report(db, cfg, rid, spec, say)
    rec = db.one("SELECT space FROM $r", r=R("recording", rid))
    name = (db.one("SELECT name FROM $s", s=R("space", rec["space"])) or {}).get("name")
    render.build_reports(db, cfg, ns=name, rid=rid, log=say)
    from . import metadata
    metadata.touched(db, cfg, rid)  # harvesters see an Update for published recordings


def _llm(db, cfg, rid, say, spec=None):
    pipelines.run_llm(db, cfg, rid, spec, say)


def _export(db, cfg, rid, say, spec=None):
    pipelines.run_export(db, cfg, rid, spec, say)


def _shots(db, cfg, rid, say, spec=None):
    from . import video
    video.step_shots(db, cfg, rid, say)


def _ocr(db, cfg, rid, say, spec=None):
    from . import video
    video.step_ocr(db, cfg, rid, say)


def _faces(db, cfg, rid, say, spec=None):
    from . import video
    video.step_faces(db, cfg, rid, say)


STEPS = {"transcribe": _transcribe, "diarize": _diarize, "shots": _shots, "ocr": _ocr, "faces": _faces, "analyze": _analyze,
         "summarize": _summarize, "report": _report, "llm": _llm, "export": _export}


def enqueue(db, rid, steps=None, by=None, priority=0, pipeline=None, batch=None):
    """Queue a recording for these steps (default: its namespace's pipeline); one already queued or running keeps its job."""
    rec = db.one("SELECT space FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    ref = None
    if steps is None:
        steps, ref = pipelines.resolve(db, rec["space"], pipeline)
    steps = [_spec(s) for s in steps]
    if not steps or any(s.get("type") not in STEPS for s in steps):
        raise ValueError(f"steps are {', '.join(STEPS)}")
    live = db.values("SELECT VALUE record::id(id) FROM job WHERE recording = $r AND status IN $a", r=rid, a=ACTIVE)
    if live:
        return live[0]
    jid, t = db.next_id("job"), store.now()
    db.q("CREATE $r CONTENT $d", r=R("job", jid), d=store.clean({"recording": rid, "space": rec["space"], "steps": steps, "step_index": 0,
                                                                  "next_step": steps[0]["type"], "status": "queued", "priority": priority, "created_by": by, "pipeline": ref, "batch": batch,
                                                                  "created_at": t, "updated_at": t, "attempts": 0, "log": []}))
    return jid


def steps_for(rec):
    st, audio = rec.get("status"), rec.get("source") == "audio"
    if st in ("new", "error") and audio:
        return PIPELINE
    if st == "transcribed":
        return (["diarize"] if audio else []) + AFTER_IMPORT
    if st == "diarized":
        return AFTER_IMPORT
    return []


def enqueue_pending(db, space=None, by=None):
    q = "SELECT record::id(id) AS id, status, source FROM recording WHERE status IN ['new', 'error', 'transcribed', 'diarized']"
    return [enqueue(db, r["id"], steps_for(r), by) for r in db.rows(q + (" AND space = $s" if space else ""), s=space) if steps_for(r)]


def claim(db, worker, can):
    for r in db.rows("SELECT record::id(id) AS id, priority, created_at, started_at FROM job WHERE status = 'queued' AND next_step IN $can "
                     "ORDER BY priority DESC, created_at ASC LIMIT 10", can=sorted(can)):
        t = store.now()
        try:
            got = db.rows("UPDATE $j SET status = 'running', worker = $w, started_at = $st, heartbeat_at = $t, updated_at = $t, attempts += 1 "
                          "WHERE status = 'queued' RETURN AFTER", j=R("job", r["id"]), w=worker, st=r.get("started_at") or t, t=t)
        except Exception:  # noqa: BLE001 - another worker won the race
            continue
        if got:
            return {**got[0], "id": r["id"]}
    return None


def run_job(db, cfg_fn, job, worker, can, log=None):
    jid, rid, jr = job["id"], job["recording"], R("job", job["id"])
    steps, i = job["steps"], job.get("step_index") or 0
    lines = list(job.get("log") or [])[-200:]

    def say(*a):
        lines.append(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a).strip())
        del lines[:-200]
        if log:
            log(f"[job {jid}] {lines[-1]}")

    stop = threading.Event()

    def beat():
        while not stop.wait(20):
            try:
                db.q("UPDATE $j SET heartbeat_at = $t, log = $l", j=jr, t=store.now(), l=lines)
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=beat, daemon=True).start()
    try:
        while i < len(steps):
            spec = _spec(steps[i])
            step = spec["type"]
            if (db.one("SELECT cancel_requested FROM $j", j=jr) or {}).get("cancel_requested"):
                say("cancelled")
                db.q("UPDATE $j SET status = 'cancelled', worker = NONE, finished_at = $t, updated_at = $t, log = $l", j=jr, t=store.now(), l=lines)
                return "cancelled"
            if step not in can:
                say(f"handing {step} to a worker that can run it")
                db.q("UPDATE $j SET status = 'queued', step_index = $i, next_step = $s, worker = NONE, updated_at = $t, log = $l",
                     j=jr, i=i, s=step, t=store.now(), l=lines)
                return "handed-off"
            db.q("UPDATE $j SET step_index = $i, next_step = $s, updated_at = $t, heartbeat_at = $t, log = $l", j=jr, i=i, s=step, t=store.now(), l=lines)
            if spec.get("when") and not pipelines.condition_ok(db, rid, spec["when"]):
                say(f"{step} skipped: its condition isn't met")
                i += 1
                continue
            t0 = time.time()
            STEPS[step](db, cfg_fn(), rid, say, spec)
            say(f"{spec.get('name') or step} done in {time.time() - t0:.1f}s")
            i += 1
        db.q("UPDATE $j SET status = 'succeeded', step_index = $i, next_step = NONE, error = NONE, finished_at = $t, updated_at = $t, log = $l",
             j=jr, i=i, t=store.now(), l=lines)
        return "succeeded"
    except Exception as e:  # noqa: BLE001 - recorded on the job
        err = f"{type(e).__name__}: {e}"[:500]
        say(f"{_spec(steps[i])['type']} failed: {err}")
        db.q("UPDATE $j SET status = 'failed', error = $e, step_index = $i, next_step = $s, finished_at = $t, updated_at = $t, log = $l",
             j=jr, e=err, i=i, s=_spec(steps[i])["type"], t=store.now(), l=lines)
        if _spec(steps[i])["type"] == "transcribe":
            db.q("UPDATE $r SET status = 'error', error = $e", r=R("recording", rid), e=err)
        return "failed"
    finally:
        stop.set()


def get(db, jid):
    return db.one(f"SELECT {FIELDS}, log FROM $j", j=R("job", jid))


def cancel(db, jid):
    j = get(db, jid)
    if not j:
        raise KeyError(jid)
    t = store.now()
    if j["status"] == "queued":
        db.q("UPDATE $j SET status = 'cancelled', finished_at = $t, updated_at = $t", j=R("job", jid), t=t)
    elif j["status"] == "running":
        db.q("UPDATE $j SET cancel_requested = true, updated_at = $t", j=R("job", jid), t=t)
    else:
        raise ValueError(f"the job has already {j['status']}")


def retry(db, jid):
    j = get(db, jid)
    if not j:
        raise KeyError(jid)
    if j["status"] not in ("failed", "cancelled"):
        raise ValueError("only failed or cancelled jobs can be retried")
    i = min(j.get("step_index") or 0, len(j["steps"]) - 1)
    db.q("UPDATE $j SET status = 'queued', next_step = $s, step_index = $i, error = NONE, cancel_requested = false, finished_at = NONE, "
         "updated_at = $t", j=R("job", jid), s=_spec(j["steps"][i])["type"], i=i, t=store.now())


def reap(db, stale_minutes=15, max_attempts=3):
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=stale_minutes)).isoformat(timespec="seconds")
    for j in db.rows("SELECT record::id(id) AS id, attempts FROM job WHERE status = 'running' AND heartbeat_at < $c", c=cutoff):
        if (j.get("attempts") or 0) >= max_attempts:
            db.q("UPDATE $j SET status = 'failed', error = 'the worker stopped responding', updated_at = $t", j=R("job", j["id"]), t=store.now())
        else:
            db.q("UPDATE $j SET status = 'queued', worker = NONE, updated_at = $t", j=R("job", j["id"]), t=store.now())


def _decorate(db, rows):
    titles = {r["id"]: r["title"] for r in db.rows("SELECT record::id(id) AS id, title FROM recording WHERE id IN $ids",
                                                   ids=[R("recording", i) for i in {x["recording"] for x in rows}])} if rows else {}
    for r in rows:
        r["title"] = titles.get(r["recording"])
        r["progress"] = round((r.get("step_index") or 0) / max(1, len(r.get("steps") or [])), 3) if r["status"] != "succeeded" else 1.0
    return rows


def list_jobs(db, spaces=None, status=None, recording=None, limit=100):
    where, p = [], {}
    if spaces is not None:
        where.append("space IN $sp")
        p["sp"] = sorted(spaces)
    if status:
        where.append("status IN $st")
        p["st"] = [status] if isinstance(status, str) else list(status)
    if recording:
        where.append("recording = $r")
        p["r"] = int(recording)
    q = f"SELECT {FIELDS} FROM job" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY created_at DESC LIMIT {int(limit)}"
    return _decorate(db, db.rows(q, **p))


def changes(db, since, spaces=None):
    q = f"SELECT {FIELDS} FROM job WHERE updated_at > $s" + (" AND space IN $sp" if spaces is not None else "") + " ORDER BY updated_at LIMIT 200"
    return _decorate(db, db.rows(q, s=since or "", sp=sorted(spaces or [])))


def counts(db, spaces=None):
    q = "SELECT status, count() AS n FROM job" + (" WHERE space IN $sp" if spaces is not None else "") + " GROUP BY status"
    return {r["status"]: r["n"] for r in db.rows(q, sp=sorted(spaces or []))}


class Worker:
    def __init__(self, db, cfg_fn, name=None, steps=None, log=None):
        self.db, self.cfg_fn, self.log = db, cfg_fn, log
        self.name = name or f"{socket.gethostname()}-{os.getpid()}"
        self.can = set(steps or cfg_fn()["workers"]["steps"]) & set(STEPS)

    def register(self, current=None):
        self.db.q("UPSERT $w CONTENT $d", w=R("worker", self.name), d=store.clean({"steps": sorted(self.can), "host": socket.gethostname(),
                                                                                   "heartbeat_at": store.now(), "current": current}))

    def run_once(self):
        job = claim(self.db, self.name, self.can)
        if not job:
            return False
        self.register(job["id"])
        run_job(self.db, self.cfg_fn, job, self.name, self.can, self.log)
        self.register()
        return True

    def drain(self, max_jobs=100000):
        n = 0
        while n < max_jobs and self.run_once():
            n += 1
        return n

    def loop(self, stop):
        last = 0.0
        while not stop.is_set():
            try:
                w = self.cfg_fn()["workers"]
                if time.time() - last > 30:
                    reap(self.db, w["stale_minutes"], w["max_attempts"])
                    self.register()
                    last = time.time()
                if not self.run_once():
                    stop.wait(w["poll_seconds"])
            except Exception as e:  # noqa: BLE001 - keep the worker alive
                if self.log:
                    self.log(f"worker {self.name}: {type(e).__name__}: {e}")
                stop.wait(5)
