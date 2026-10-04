"""Background work: every long task is a job in SurrealDB, run by workers inside the server or on other machines.

A job carries one recording through a list of steps. A worker only runs the steps it can (a Mac with mlx might
only transcribe); when the next step isn't one of them, the job goes back on the queue for a worker that can.
Workers heartbeat while they run; a job whose worker goes quiet is requeued, up to workers.max_attempts.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import socket
import threading
import time

from . import activity, analyze, ingest, keyring, pipelines, render, speakers as spk, store, telemetry

R = store.R
PIPELINE = ["transcribe", "diarize", "shots", "ocr", "faces", "objects", "describe", "analyze", "embed", "summarize", "report"]
AFTER_IMPORT = ["analyze", "embed", "summarize", "report"]
ACTIVE = ["queued", "running"]
FIELDS = (
    "record::id(id) AS id, recording, space, batch, pipeline, steps, step_index, next_step, status, worker, error, attempts, "
    "created_by, created_at, started_at, finished_at, updated_at, cancel_requested, log_total, cost_usd, tokens, cost_estimate"
)
MEDIA_STEPS = {"transcribe", "diarize", "shots", "ocr", "faces", "objects", "describe"}  # they take longer the longer the recording
FILED = ("audio", "document", "image")  # sources with a file of their own for the steps to work on
TIMINGS = 25  # recent timings kept per kind of step, for estimates


class Skip(Exception):
    """Raised by a step with nothing to do for this recording, with the reason; the run goes on to the next step."""


def _source(db, rid):
    return (db.one("SELECT source FROM $r", r=R("recording", rid)) or {}).get("source")


def _spec(s):
    return {"type": s} if isinstance(s, str) else dict(s)


def _transcribe(db, cfg, rid, say, spec=None):
    rec = db.one("SELECT source, engine, status, envelope FROM $r", r=R("recording", rid)) or {}
    if rec.get("source") in ("document", "image"):
        from . import documents

        return documents.transcribe(db, cfg, rid, say)
    if rec.get("source") != "audio":
        raise Skip("an imported transcript has nothing to transcribe")
    if str(rec.get("engine") or "").startswith("import:") and rec.get("status") != "new" and not (spec or {}).get("force"):
        if not rec.get("envelope"):  # its audio was attached after the transcript was imported
            ingest.add_envelope(db, cfg, rid)
            return say("drew the waveform of the attached media; kept the transcript that came with it")
        raise Skip("the transcript came with the media; kept it (force the step to transcribe again)")
    ingest.transcribe_one(db, cfg, rid, say)


def _diarize(db, cfg, rid, say, spec=None):
    rec = db.one("SELECT source, diarizer FROM $r", r=R("recording", rid)) or {}
    if rec.get("source") in ("document", "image"):
        raise Skip(f"{'an image' if rec['source'] == 'image' else 'a document'} has no voices")
    if rec.get("source") != "audio" or (rec.get("diarizer") == "labels" and not (spec or {}).get("force")):
        raise Skip("the speakers came with the transcript; kept them")
    spk.diarize_one(db, cfg, rid, say)


def _analyze(db, cfg, rid, say, spec=None):
    analyze.analyze_recording(db, cfg, rid)
    say("analysed")


def _embed(db, cfg, rid, say, spec=None):
    from . import semantic

    if not semantic.configured(cfg):
        raise Skip("search by meaning is off, or has no embeddings server (Settings → Search)")
    try:
        made, kept = semantic.index_recording(db, cfg, rid, say)
    except semantic.EmbedError as e:  # the server is down or lacks the model: the routine indexes it later
        raise Skip(f"couldn't index it for search by meaning: {e}") from None
    if not made and not kept:
        return say("no text to index for search by meaning")
    say(f"indexed for search by meaning: {made} passage(s) embedded" + (f", {kept} unchanged" if kept else ""))


def _summarize(db, cfg, rid, say, spec=None):
    if not (cfg["llm"].get("base_url") and cfg["llm"].get("model")):
        raise Skip("no LLM is configured")
    if not db.values("SELECT VALUE id FROM segment WHERE recording = $r LIMIT 1", r=rid):
        raise Skip("there's no text to summarise")
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


def _workflow(db, cfg, rid, say, spec=None):
    from . import workflows

    with activity.scope(db, f"workflow:{int(spec['workflow'])}"):
        done = workflows.run(db, cfg, rid, int(spec["workflow"]), spec.get("version"), say)
    ran = sum(1 for v in done.values() if v == "done") - 1  # not counting the input
    say(f"workflow ran {ran} of {len(done) - 1} nodes")


def _shots(db, cfg, rid, say, spec=None):
    from . import video

    video.step_shots(db, cfg, rid, say)


def _ocr(db, cfg, rid, say, spec=None):
    from . import video

    video.step_ocr(db, cfg, rid, say)


def _faces(db, cfg, rid, say, spec=None):
    from . import video

    video.step_faces(db, cfg, rid, say)


def _objects(db, cfg, rid, say, spec=None):
    from . import objects

    objects.step_objects(db, cfg, rid, say)


def _describe(db, cfg, rid, say, spec=None):
    from . import descriptions

    descriptions.step_describe(db, cfg, rid, say)


VIDEO_STEPS = {"shots", "ocr", "faces", "objects", "describe"}  # added after workers.steps lists were first written
STEPS = {
    "transcribe": _transcribe,
    "diarize": _diarize,
    "shots": _shots,
    "ocr": _ocr,
    "faces": _faces,
    "objects": _objects,
    "describe": _describe,
    "analyze": _analyze,
    "embed": _embed,
    "summarize": _summarize,
    "report": _report,
    "llm": _llm,
    "export": _export,
    "workflow": _workflow,
}


def enqueue(db, rid, steps=None, by=None, priority=0, pipeline=None, batch=None):
    """Queue a recording for these steps (default: its namespace's pipeline); one already queued or running keeps its job."""
    rec = db.one("SELECT space FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    ref = None
    if steps is None:
        from . import content_types

        steps, ref = pipelines.resolve(db, rec["space"], pipeline, None if pipeline else content_types.of_recording(db, rid)[0])
    steps = [_spec(s) for s in steps]
    if not steps or any(s.get("type") not in STEPS for s in steps):
        raise ValueError(f"steps are {', '.join(STEPS)}")
    live = db.values("SELECT VALUE record::id(id) FROM job WHERE recording = $r AND status IN $a", r=rid, a=ACTIVE)
    if live:
        return live[0]
    jid, t = db.next_id("job"), store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("job", jid),
        d=store.clean(
            {
                "recording": rid,
                "space": rec["space"],
                "steps": steps,
                "step_index": 0,
                "next_step": steps[0]["type"],
                "status": "queued",
                "priority": priority,
                "created_by": by,
                "pipeline": ref,
                "batch": batch,
                "created_at": t,
                "updated_at": t,
                "attempts": 0,
                "log": [],
            }
        ),
    )
    return jid


def add_steps(db, rid, steps, by=None):
    """Run these steps on a recording after what its queued or running job still has to do, or as a new job when it
    has none. Workers read a job's steps again before each step, and only finish a job whose steps are all done, so
    steps added while it runs aren't missed."""
    specs = [_spec(s) for s in steps]
    for jid in db.values("SELECT VALUE record::id(id) FROM job WHERE recording = $r AND status IN $a", r=rid, a=ACTIVE):
        if db.rows(
            "UPDATE $j SET steps = array::concat(steps, $m), updated_at = $t WHERE status IN $a RETURN AFTER",
            j=R("job", jid),
            m=specs,
            a=ACTIVE,
            t=store.now(),
        ):
            return jid
    return enqueue(db, rid, steps, by)


def steps_for(rec):
    st, audio = rec.get("status"), rec.get("source") == "audio"
    if st in ("new", "error") and rec.get("source") in FILED:
        return PIPELINE
    if st == "transcribed":
        return (["diarize"] if audio else []) + AFTER_IMPORT
    if st == "diarized":
        return AFTER_IMPORT
    return []


def enqueue_pending(db, space=None, by=None):
    """Queue what scans and imports left waiting. A new file runs the pipeline its content type and namespace resolve
    to, as an upload does; a transcript imported part-way runs what's left after import."""
    q = "SELECT record::id(id) AS id, status, source FROM recording WHERE status IN ['new', 'error', 'transcribed', 'diarized']"
    out = []
    for r in db.rows(q + (" AND space = $s" if space else ""), s=space):
        steps = steps_for(r)
        if steps:
            out.append(enqueue(db, r["id"], None if steps is PIPELINE else steps, by))
    return out


def claim(db, worker, can):
    locked = keyring.locked_vaults(db)  # a vault's work waits until someone unlocks it here
    for r in db.rows(
        "SELECT record::id(id) AS id, space, priority, created_at, started_at FROM job WHERE status = 'queued' AND next_step IN $can "
        "AND space NOTINSIDE $locked ORDER BY priority DESC, created_at ASC LIMIT 10",
        can=sorted(can),
        locked=sorted(locked),
    ):
        t = store.now()
        try:
            got = db.rows(
                "UPDATE $j SET status = 'running', worker = $w, started_at = $st, heartbeat_at = $t, updated_at = $t, attempts += 1 "
                "WHERE status = 'queued' RETURN AFTER",
                j=R("job", r["id"]),
                w=worker,
                st=r.get("started_at") or t,
                t=t,
            )
        except Exception:  # noqa: BLE001 - another worker won the race
            continue
        if got:
            return {**got[0], "id": r["id"]}
    return None


LOG_TAIL = 200  # lines a run keeps on itself, for a quick look; job_log has all of them
LOG_MAX = 100_000  # lines kept per run


class RunLog:
    """A run's log as it's written. Every line goes to job_log in chunks (`start` is the number of the chunk's first
    line); the job keeps its last LOG_TAIL lines (`log`) and how many there are (`log_total`), so the event feed
    can send what's new."""

    def __init__(self, db, jid, space, tail=None, total=None):
        self.db, self.jid, self.space = db, jid, space
        self.tail, self.total, self.pending = list(tail or [])[-LOG_TAIL:], int(total or 0), []
        self.lock = threading.Lock()

    def say(self, line):
        with self.lock:
            self.tail.append(line)
            del self.tail[:-LOG_TAIL]
            if self.total + len(self.pending) < LOG_MAX:
                self.pending.append(line)
        return line

    def mark(self):
        """The number the next line gets."""
        with self.lock:
            return self.total + len(self.pending)

    def flush(self):
        """Write the new lines; the parameters for the job's `log = $l, log_total = $n`."""
        with self.lock:
            chunk, start = self.pending, self.total
            self.pending, self.total = [], self.total + len(chunk)
            tail, total = list(self.tail), self.total
        if chunk:
            self.db.q(
                "CREATE job_log CONTENT $d",
                d={"job": self.jid, "space": self.space, "start": start, "lines": chunk, "at": store.now()},
            )
        return {"l": tail, "n": total}


def log_lines(db, jid, after=0, limit=1000):
    """Lines of a run's log from line number `after` (0-based), at most `limit`: (lines, total). Runs from before
    job_log have only the last LOG_TAIL lines kept on the job."""
    chunks = db.rows("SELECT start, lines FROM job_log WHERE job = $j AND start + array::len(lines) > $a ORDER BY start", j=jid, a=after)
    if not chunks:
        j = db.one("SELECT log, log_total FROM $j", j=R("job", jid)) or {}
        if j.get("log_total") is None:  # an old run
            old = list(j.get("log") or [])
            return old[after : after + limit], len(old)
        return [], j["log_total"]
    lines = [x for c in chunks for x in c["lines"]][after - chunks[0]["start"] :]
    last = db.one("SELECT start, array::len(lines) AS n FROM job_log WHERE job = $j ORDER BY start DESC LIMIT 1", j=jid)
    return lines[:limit], last["start"] + last["n"]


def _saved(db, rid):
    """A recording's outputs, by key: the ones a step saves are those whose time changes while it runs."""
    return {r["key"]: r for r in db.rows("SELECT key, created_at, origin FROM output WHERE recording = $r", r=rid)}


def _outputs(before, after):
    return [
        store.clean({"key": k, **{f: (r.get("origin") or {}).get(f) for f in ("template", "version", "model")}})
        for k, r in sorted(after.items())
        if (before.get(k) or {}).get("created_at") != r.get("created_at")
    ]


def _kinds(spec):
    """What a step's timings are kept under, most general first: its type, or for a step that runs a template
    `<type>:template` (any template) and `<type>:<template id>` (one template's prompt can take far longer than another's,
    and a report template is nothing like the namespace's report pages)."""
    t = spec["type"]
    return [t] if spec.get("template") is None else [f"{t}:template", f"{t}:{spec['template']}"]


def _timed(db, rid, spec, seconds, skipped):
    """Keep how long a step took, whether it skipped, and the recording's length for steps that work through its media.
    Only estimates use these, so a failure to save them doesn't fail the run."""
    try:
        minutes = None
        if spec["type"] in MEDIA_STEPS:
            ms = (db.one("SELECT duration_ms FROM $r", r=R("recording", rid)) or {}).get("duration_ms")
            minutes = round(ms / 60000, 3) if ms else None
        for k in _kinds(spec):
            db.q(
                "UPSERT $s SET kind = $k, samples = array::slice(array::append(samples ?? [], $x), "
                "math::max([0, array::len(samples ?? []) + 1 - $cap]))",
                s=R("step_stat", k),
                k=k,
                x=[seconds, minutes, skipped],
                cap=TIMINGS,
            )
    except Exception:  # noqa: BLE001
        pass


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2) if n else None


def estimates(db, job):
    """How long each of a run's steps usually takes, in seconds (None with nothing to go by): the median of the last
    TIMINGS times it ran (of its template, when that has run before), scaled to this recording's length for steps
    that work through the media. Steps that only ever skipped count their skips; media steps skip on a recording
    without media."""
    specs = [_spec(s) for s in job.get("steps") or []]
    kinds = sorted({k for s in specs for k in _kinds(s)})
    stats = {r["kind"]: r.get("samples") or [] for r in db.rows("SELECT kind, samples FROM step_stat WHERE kind IN $k", k=kinds)}
    rec = db.one("SELECT duration_ms, source FROM $r", r=R("recording", job.get("recording") or 0)) or {}
    ms, media = rec.get("duration_ms"), rec.get("source") in FILED
    out = []
    for s in specs:
        samples = next((stats[k] for k in reversed(_kinds(s)) if stats.get(k)), [])
        ran = [x for x in samples if not x[2]]
        skips = [x[0] for x in samples if x[2]]
        if s["type"] in MEDIA_STEPS and not media:
            typical = _median(skips) or 0.0
        elif not ran:
            typical = _median(skips)
        else:
            per_minute = [sec / m for sec, m, _ in ran if m] if s["type"] in MEDIA_STEPS and ms else []
            typical = _median(per_minute) * ms / 60000 if per_minute else _median([x[0] for x in ran])
        out.append(None if typical is None else round(typical, 1))
    return out


def eta(job, est, now=None):
    """About how many seconds until an active run finishes: what its later steps usually take, and what's left of the
    current one's (nothing once it has run over). None when a step has nothing to go by."""
    i = job.get("step_index") or 0
    if job.get("status") not in ACTIVE or i >= len(est) or any(e is None for e in est[i:]):
        return None
    left = est[i]
    cur = (job.get("step_runs") or [])[i : i + 1]
    if job["status"] == "running" and cur and (cur[0] or {}).get("outcome") == "running":
        started = dt.datetime.fromisoformat(cur[0]["started_at"])
        left = max(0.0, left - ((now or dt.datetime.now(dt.timezone.utc)) - started).total_seconds())
    return round(left + sum(est[i + 1 :]), 1)


FINAL = ("succeeded", "failed", "cancelled")


def job_refs(job):
    """The resources a job's calls count for: it, its recording, namespace and pipeline, and the routine that queued it."""
    pipe = job.get("pipeline") if isinstance(job.get("pipeline"), dict) else {}
    by = job.get("created_by")
    return [
        f"job:{job['id']}",
        f"recording:{job['recording']}",
        f"space:{job['space']}" if job.get("space") is not None else None,
        f"pipeline:{pipe['id']}" if pipe.get("id") is not None else None,
        by if isinstance(by, str) and by.startswith("routine:") else None,
    ]


def run_job(db, cfg_fn, job, worker, can, log=None):
    """Run a job's steps from where it is, with its calls counted for it (activity.py); when it ends, a run row with
    what it cost in all, kept on the job too (cost_usd, tokens)."""
    refs = job_refs(job)
    with activity.scope(db, *refs):
        outcome = _run_job(db, cfg_fn, job, worker, can, log)
    if outcome in FINAL:
        usd, tokens, rough = activity.run_total(db, refs[0])
        try:
            db.q("UPDATE $j SET cost_usd = $u, tokens = $n, cost_estimate = $e", j=R("job", job["id"]), u=usd, n=tokens, e=rough or None)
        except Exception:  # noqa: BLE001 - the ledger never breaks the work
            pass
        activity.record(
            "run",
            f"job.{outcome}",
            refs,
            cfg_fn(),
            db,
            cost_usd=usd,
            tokens_in=tokens,
            ok=outcome != "failed",
            detail={"estimate": rough} if rough else None,
        )
    return outcome


def _run_job(db, cfg_fn, job, worker, can, log=None):
    jid, rid, jr, wr = job["id"], job["recording"], R("job", job["id"]), R("worker", worker)
    steps, i = job["steps"], job.get("step_index") or 0
    out = RunLog(db, jid, job.get("space"), job.get("log"), job.get("log_total"))
    # one record per step, in step order: how its latest run went (when, how long, how it ended, its last message,
    # its part of the log and the outputs it saved)
    runs = list(job.get("step_runs") or [])
    said = [None]  # the running step's last message

    def say(*a):
        said[0] = " ".join(str(x) for x in a).strip()
        line = out.say(time.strftime("%H:%M:%S ") + said[0])
        if log:
            log(f"[job {jid}] {line}")

    def save(sets, where="", **p):
        """Update the job, with its new log lines and step records; [] when `where` didn't match."""
        return db.rows(
            f"UPDATE $j SET {sets}, updated_at = $t, log = $l, log_total = $n, step_runs = $sr {where} RETURN id",
            j=jr,
            t=store.now(),
            sr=runs,
            **out.flush(),
            **p,
        )

    def started(k):
        runs.extend([None] * (k + 1 - len(runs)))
        runs[k] = {"started_at": store.now(), "outcome": "running", "worker": worker, "log_from": out.mark()}
        said[0] = None
        return time.time()

    def finished(k, outcome, note, t0, outputs=None):
        telemetry.record("lens.job.step.duration", time.time() - t0, {"lens.step": _spec(steps[k])["type"], "lens.step.outcome": outcome})
        runs[k] = store.clean(
            {
                **runs[k],
                "finished_at": store.now(),
                "seconds": round(time.time() - t0, 1),
                "outcome": outcome,
                "note": note,
                "log_to": out.mark(),
                "outputs": outputs or None,
            }
        )

    stop = threading.Event()

    def beat():
        # new lines go out every couple of seconds (the event feed sends them on); a heartbeat at least every 20, for the
        # job and for its worker, however long the step it's on takes
        last = worker_beat = time.monotonic()
        while not stop.wait(2):
            if time.monotonic() - worker_beat >= WORKER_BEAT:
                worker_beat = time.monotonic()
                try:
                    db.q("UPDATE $w SET heartbeat_at = $t, load = $l", w=wr, t=store.now(), l=machine_load())
                except Exception:  # noqa: BLE001
                    pass
            if not out.pending and time.monotonic() - last < 20:
                continue
            last = time.monotonic()
            try:
                db.q(
                    "UPDATE $j SET heartbeat_at = $t, updated_at = $t, log = $l, log_total = $n "
                    "WHERE status = 'running' AND (log_total ?? 0) <= $n",
                    j=jr,
                    t=store.now(),
                    **out.flush(),
                )
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=beat, daemon=True).start()
    t0 = None  # when the step in hand started
    try:
        while True:
            steps = (db.one("SELECT steps FROM $j", j=jr) or {}).get("steps") or steps  # with any added meanwhile (add_steps)
            if i >= len(steps):
                if save(
                    "status = 'succeeded', step_index = $i, next_step = NONE, error = NONE, finished_at = $t",
                    "WHERE array::len(steps) <= $i",
                    i=i,
                ):
                    return "succeeded"
                continue  # steps were added just now
            spec = _spec(steps[i])
            step = spec["type"]
            gone = not db.one("SELECT id FROM $r", r=R("recording", rid))
            if gone or (db.one("SELECT cancel_requested FROM $j", j=jr) or {}).get("cancel_requested"):
                say("the recording was deleted" if gone else "cancelled")
                save("status = 'cancelled', worker = NONE, finished_at = $t")
                return "cancelled"
            if (db.one("SELECT drain FROM $w", w=wr) or {}).get("drain"):
                say(f"{worker} is draining: handing {step} to another worker")
                save("status = 'queued', step_index = $i, next_step = $s, worker = NONE", i=i, s=step)
                db.q("UPDATE $w SET drain = false", w=wr)  # it stays paused
                return "drained"
            if step not in can:
                say(f"handing {step} to a worker that can run it")
                save("status = 'queued', step_index = $i, next_step = $s, worker = NONE", i=i, s=step)
                return "handed-off"
            t0 = started(i)
            save("step_index = $i, next_step = $s, heartbeat_at = $t", i=i, s=step)
            if spec.get("when") and not pipelines.condition_ok(db, rid, spec["when"]):
                say(f"{step} skipped: its condition isn't met")
                finished(i, "skipped", "its condition isn't met", t0)
                _timed(db, rid, spec, runs[i]["seconds"], True)
                i, t0 = i + 1, None
                continue
            before = _saved(db, rid)
            with telemetry.span(f"step {step}", {"lens.step": step, "lens.step.index": i, "lens.job.id": str(jid)}) as sp:
                try:
                    STEPS[step](db, cfg_fn(), rid, say, spec)
                except Skip as e:
                    say(f"{step} skipped: {e}")
                    finished(i, "skipped", str(e), t0, _outputs(before, _saved(db, rid)))
                else:
                    note = said[0]
                    say(f"{spec.get('name') or step} done in {time.time() - t0:.1f}s")
                    finished(i, "done", note, t0, _outputs(before, _saved(db, rid)))
                sp.set_attribute("lens.step.outcome", runs[i]["outcome"])
            _timed(db, rid, spec, runs[i]["seconds"], runs[i]["outcome"] == "skipped")
            i, t0 = i + 1, None
    except (Exception, SystemExit) as e:  # noqa: BLE001 - recorded on the job; SystemExit too (a missing engine says so)
        err = f"{type(e).__name__}: {e}"[:500]
        failed = _spec(steps[i])["type"] if i < len(steps) else None
        say(f"{failed or 'finishing'} failed: {err}")
        if t0 is not None:
            finished(i, "failed", err, t0)
        save("status = 'failed', error = $e, step_index = $i, next_step = $s, finished_at = $t", e=err, i=i, s=failed)
        if failed == "transcribe":
            db.q("UPDATE $r SET status = 'error', error = $e", r=R("recording", rid), e=err)
        return "failed"
    finally:
        stop.set()
        keyring.release()  # the plain working copies this job read may go once unused
        with contextlib.suppress(Exception):
            keyring.sweep(cfg_fn())


def get(db, jid):
    """One job, with its step records and its last log lines."""
    j = db.one(f"SELECT {FIELDS}, step_runs, log FROM $j", j=R("job", jid))
    return _decorate(db, [j])[0] if j else None


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
    if not db.one("SELECT id FROM $r", r=R("recording", j["recording"])):
        raise ValueError("its recording was deleted")
    i = min(j.get("step_index") or 0, len(j["steps"]) - 1)
    db.q(
        "UPDATE $j SET status = 'queued', next_step = $s, step_index = $i, error = NONE, cancel_requested = false, finished_at = NONE, "
        "step_runs = $sr, updated_at = $t",
        j=R("job", jid),
        s=_spec(j["steps"][i])["type"],
        i=i,
        sr=(j.get("step_runs") or [])[:i],  # the steps it runs again start over
        t=store.now(),
    )


def reap(db, stale_minutes=15, max_attempts=3):
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=stale_minutes)).isoformat(timespec="seconds")
    q = "SELECT record::id(id) AS id, attempts, step_index, step_runs FROM job WHERE status = 'running' AND heartbeat_at < $c"
    for j in db.rows(q, c=cutoff):
        if (j.get("attempts") or 0) >= max_attempts:
            runs, k, t = list(j.get("step_runs") or []), j.get("step_index") or 0, store.now()
            if k < len(runs) and (runs[k] or {}).get("outcome") == "running":
                runs[k] = {**runs[k], "outcome": "failed", "note": "the worker stopped responding", "finished_at": t}
            db.q(
                "UPDATE $j SET status = 'failed', error = 'the worker stopped responding', step_runs = $sr, finished_at = $t, updated_at = $t",
                j=R("job", j["id"]),
                sr=runs,
                t=t,
            )
        else:
            db.q("UPDATE $j SET status = 'queued', worker = NONE, updated_at = $t", j=R("job", j["id"]), t=store.now())


def _decorate(db, rows):
    titles = (
        {
            r["id"]: r["title"]
            for r in db.rows(
                "SELECT record::id(id) AS id, title FROM recording WHERE id IN $ids",
                ids=[R("recording", i) for i in {x["recording"] for x in rows}],
            )
        }
        if rows
        else {}
    )
    for r in rows:
        r["title"] = titles.get(r["recording"])
        r["progress"] = round((r.get("step_index") or 0) / max(1, len(r.get("steps") or [])), 3) if r["status"] != "succeeded" else 1.0
    return rows


def _where(spaces=None, status=None, recording=None, batch=None):
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
    if batch is not None:
        where.append("batch = $b")
        p["b"] = int(batch)
    return (" WHERE " + " AND ".join(where) if where else ""), p


def list_jobs(db, spaces=None, status=None, recording=None, limit=100, batch=None):
    """The newest jobs (in these namespaces, with these statuses, of this recording or batch run)."""
    where, p = _where(spaces, status, recording, batch)
    return _decorate(db, db.rows(f"SELECT {FIELDS} FROM job{where} ORDER BY created_at DESC LIMIT {int(limit)}", **p))


def changes(db, since, spaces=None, only=None):
    """Jobs changed after `since` (in these namespaces; only job `only`, when given), oldest change first."""
    q = (
        f"SELECT {FIELDS} FROM job WHERE updated_at > $s"
        + (" AND space IN $sp" if spaces is not None else "")
        + (" AND id = $j" if only is not None else "")
        + " ORDER BY updated_at LIMIT 200"
    )
    return _decorate(db, db.rows(q, s=since or "", sp=sorted(spaces or []), j=R("job", only or 0)))


def counts(db, spaces=None, batch=None):
    """How many jobs there are of each status (in these namespaces, of this batch run)."""
    where, p = _where(spaces, batch=batch)
    return {r["status"]: r["n"] for r in db.rows(f"SELECT status, count() AS n FROM job{where} GROUP BY status", **p)}


def per_space(db, spaces=None, batch=None):
    """How many jobs each namespace has (id -> count)."""
    where, p = _where(spaces, batch=batch)
    return {r["space"]: r["n"] for r in db.rows(f"SELECT space, count() AS n FROM job{where} GROUP BY space", **p) if r.get("space")}


def machine_load():
    """The machine's 1-minute load average per CPU (1.0: every CPU busy), or None where the system doesn't say."""
    try:
        return round(os.getloadavg()[0] / (os.cpu_count() or 1), 2)
    except (AttributeError, OSError):
        return None


WORKER_BEAT = 15  # seconds between a busy worker's heartbeats
WORKER_FIELDS = (
    "record::id(id) AS name, steps, host, heartbeat_at, current, paused, drain, paused_by, paused_at, load, cpus, components, machine"
)


def workers(db, now=None):
    """Every worker that has checked in, with how many steps each finished (done or skipped) in the last hour."""
    since = ((now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(hours=1)).isoformat(timespec="seconds")
    done = {}
    for j in db.rows("SELECT step_runs FROM job WHERE updated_at > $s", s=since):
        for r in j.get("step_runs") or []:
            if r and r.get("worker") and r.get("outcome") in ("done", "skipped") and (r.get("finished_at") or "") > since:
                done[r["worker"]] = done.get(r["worker"], 0) + 1
    rows = db.rows(f"SELECT {WORKER_FIELDS} FROM worker ORDER BY name")
    return [{**w, "steps_last_hour": done.get(w["name"], 0)} for w in rows]


def control(db, name, action, by=None):
    """Pause a worker (it takes no new runs; the one it has carries on to the end), drain it (also hand that run back
    to the queue after the step it's on, for another worker to carry on; it stays paused) or resume it."""
    w = R("worker", name)
    row = db.one("SELECT current FROM $w", w=w)
    if not row:
        raise KeyError(name)
    if action == "resume":
        db.q("UPDATE $w SET paused = false, drain = false, paused_by = NONE, paused_at = NONE", w=w)
    elif action in ("pause", "drain"):
        drain = action == "drain" and row.get("current") is not None  # an idle worker has nothing to hand back
        db.q("UPDATE $w SET paused = true, drain = $d, paused_by = $by, paused_at = $t", w=w, d=drain, by=by, t=store.now())
    else:
        raise ValueError("pause, drain or resume")
    return next(x for x in workers(db) if x["name"] == name)


class Worker:
    def __init__(self, db, cfg_fn, name=None, steps=None, log=None):
        self.db, self.cfg_fn, self.log = db, cfg_fn, log
        cfg_fn()  # reads the saved settings now, so telemetry (if on) is set up before the first job's span
        self.name = name or f"{socket.gethostname()}-{os.getpid()}"
        self.can = set(steps or cfg_fn()["workers"]["steps"]) & set(STEPS)
        if "llm" in self.can:  # a workflow needs what an llm step needs; lists written before workflows existed run them too
            self.can.add("workflow")
        if not steps and "transcribe" in self.can and not self.can & VIDEO_STEPS:
            # a workers.steps list written before the video steps existed (archive.yaml copied from an older example):
            # without them every import would wait for a worker that can run shots
            self.can |= VIDEO_STEPS
        if self.can & {"analyze", "summarize"}:  # embedding needs what they need; lists written before it existed run it too
            self.can.add("embed")
        if not steps and log and (missing := sorted(set(STEPS) - self.can)):
            log(f"worker {self.name}: workers.steps leaves out {', '.join(missing)}; jobs with those steps wait for another worker")
        self.was_paused = False
        self.keeper = None  # fetches what this machine needs (components.py), once the loop starts

    def register(self, current=None):
        # SET, not CONTENT: being paused (from the app) outlasts restarts
        self.db.q(
            "UPSERT $w SET steps = $s, host = $h, heartbeat_at = $t, current = $c, load = $l, cpus = $n",
            w=R("worker", self.name),
            s=sorted(self.can),
            h=socket.gethostname(),
            t=store.now(),
            c=current,
            l=machine_load(),
            n=os.cpu_count(),
        )

    def paused(self):
        w = self.db.one("SELECT paused, drain FROM $w", w=R("worker", self.name)) or {}
        if w.get("drain"):  # asked to drain as its run ended: between runs there's nothing to hand back
            self.db.q("UPDATE $w SET drain = false", w=R("worker", self.name))
        p = bool(w.get("paused"))
        if p != self.was_paused and self.log:
            self.log(f"worker {self.name}: {'paused from the app; waiting to be resumed' if p else 'resumed'}")
        self.was_paused = p
        return p

    def run_once(self):
        if self.paused():
            return False
        # steps whose engine or model is still being fetched wait for it
        can = self.can - self.keeper.blocked() if self.keeper else self.can
        job = claim(self.db, self.name, can) if can else None
        if not job:
            return False
        self.register(job["id"])
        pipe = job.get("pipeline") if isinstance(job.get("pipeline"), dict) else {}
        attrs = {
            "lens.job.id": str(job["id"]),
            "lens.recording.id": str(job["recording"]),
            "lens.pipeline.id": str(pipe["id"]) if pipe.get("id") is not None else ("standard" if pipe else None),
            "lens.pipeline.version": pipe.get("version"),
        }
        with telemetry.span("job", attrs) as sp:
            outcome = run_job(self.db, self.cfg_fn, job, self.name, self.can, self.log)
            sp.set_attribute("lens.job.outcome", outcome)
        telemetry.record("lens.jobs", 1, {"lens.job.outcome": outcome})
        self.register()
        return True

    def drain(self, max_jobs=100000):
        """Run queued jobs until there are none it can run (tests, `lens worker --once`)."""
        n = 0
        while n < max_jobs and self.run_once():
            n += 1
        return n

    def loop(self, stop):
        from . import components

        self.keeper = components.Keeper(self.db, self.cfg_fn, self.name, self.can, self.log)
        self.register()
        self.keeper.start(stop)
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
