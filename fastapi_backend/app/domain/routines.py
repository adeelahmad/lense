"""Routines: things the archive does on a schedule, like a cron job. A routine runs its actions in order over its
namespaces (or all of them), at the times its schedule says, or when someone asks it to run now:

- `sync`: pull from sources: scan watched folders now (the routine's namespaces' watches, or the ones it names),
  so new files come in and run their pipelines.
- `pipeline`: queue a pipeline (one chosen, else the one each recording's content type gets: see content_types.py)
  for recordings: new ones (made since the routine last looked), unprocessed ones, all of them, or the ones not yet
  indexed for search by meaning with the current embedding model (to run the embed step on).
- `workflow`: run a workflow. One that runs on recordings is queued on them as a workflow step (as above); one that
  organises the graph (scope graph, see organize.py) runs over the routine's namespaces there and then, proposing or
  making changes to their entities.
- `sensors`: look after stream sensors' data (sensors.py), every stream sensor whatever the routine's namespaces, or
  the ones it names: drop readings and hourly summaries past each sensor's retention, label new log patterns where
  triage is on (sensor_patterns.py), and write the daily digests of sensors that keep them (sensor_digests.py).

Each run is a `routine_run` row with what every action did and a log. A run's graph changes can be undone together.
A routine whose run is still going isn't started again; a run that stops reporting for STALE_MINUTES is taken as dead.
"""

from __future__ import annotations

import datetime as dt
import threading

from . import activity, fedora, jobs, organize, schedule, semantic, sensor_digests, sensor_patterns, sensors, sources, store, telemetry, workflows

R = store.R
ACTIONS = ("sync", "pipeline", "workflow", "sensors")
ACTION_KEYS = {
    "sync": {"type", "watches"},
    "sensors": {"type", "sensors"},
    "pipeline": {"type", "pipeline", "steps", "recordings", "limit"},
    "workflow": {"type", "workflow", "version", "recordings", "limit", "propose_only"},
}
PICKS = ("new", "unprocessed", "all", "unindexed")  # unindexed: not yet searchable by meaning with the current model
UNPROCESSED = ["new", "error", "transcribed", "diarized"]
MAX_ACTIONS, DEFAULT_LIMIT, MAX_LIMIT = 20, 500, 5000
STALE_MINUTES = 180
CHECK_SECONDS = 30  # how often the scheduler looks for routines that are due
MIN_WAIT_SECONDS = 5  # the shortest wait between rounds, whatever sources.check_seconds says
LOG_MAX = 500
FIELDS = (
    "record::id(id) AS id, name, description, enabled, schedule, timezone, namespaces, actions, next_run_at, last_run_at, "
    "last_status, last_run, running_since, run_now, seen_recording, created_at, created_by, updated_at"
)


def _iso(t):
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds") if t else None


def _check(db, d):
    """The routine's settings, checked: ValueError naming the first problem."""
    out = {}
    if "name" in d:
        if not (d["name"] or "").strip():
            raise ValueError("give the routine a name")
        out["name"] = d["name"].strip()[:80]
    if "description" in d:
        out["description"] = (d["description"] or "").strip() or None
    if "enabled" in d:
        out["enabled"] = bool(d["enabled"])
    if "timezone" in d:
        out["timezone"] = d["timezone"] or "UTC"
        schedule.zone(out["timezone"])
    if "schedule" in d:
        out["schedule"] = (d["schedule"] or "").strip() or None
    if out.get("schedule"):
        schedule.check(out["schedule"], out.get("timezone") or d.get("_timezone") or "UTC")
    if "namespaces" in d:
        ns = d["namespaces"]
        if ns is not None:
            if not isinstance(ns, list) or not all(isinstance(x, int) for x in ns):
                raise ValueError("namespaces is a list of namespace ids, or none for all of them")
            known = set(store.space_names(db))
            missing = [x for x in ns if x not in known]
            if missing:
                raise ValueError(f"no namespace {missing[0]}")
            ns = sorted(set(ns))
        out["namespaces"] = ns
    if "actions" in d:
        acts = d["actions"] or []
        if not isinstance(acts, list) or not acts:
            raise ValueError("a routine does at least one thing: sync, pipeline, workflow or sensors")
        if len(acts) > MAX_ACTIONS:
            raise ValueError(f"a routine has at most {MAX_ACTIONS} actions")
        out["actions"] = [_check_action(db, k, a) for k, a in enumerate(acts, 1)]
    return out


def _check_action(db, k, a):
    a = dict(a or {})
    t = a.get("type")
    if t not in ACTIONS:
        raise ValueError(f"action {k}: the type is one of {', '.join(ACTIONS)}")
    extra = sorted(set(a) - ACTION_KEYS[t])
    if extra:
        raise ValueError(f"action {k} ({t}) has no setting {extra[0]}")
    if a.get("recordings", "new") not in PICKS:
        raise ValueError(f"action {k}: recordings is one of {', '.join(PICKS)}")
    lim = a.get("limit")
    if lim is not None and (not isinstance(lim, int) or not 1 <= lim <= MAX_LIMIT):
        raise ValueError(f"action {k}: limit is 1 to {MAX_LIMIT} recordings")
    if t == "sync":
        w = a.get("watches")
        if w is not None:
            if not isinstance(w, list) or not all(isinstance(x, int) for x in w):
                raise ValueError(f"action {k}: watches is a list of watched folder ids, or none for all of them")
            for x in w:
                if not db.one("SELECT id FROM $r", r=R("watch_path", x)):
                    raise ValueError(f"action {k}: no watched folder {x}")
    elif t == "sensors":
        ids = a.get("sensors")
        if ids is not None:
            if not isinstance(ids, list) or not all(isinstance(x, int) for x in ids):
                raise ValueError(f"action {k}: sensors is a list of stream sensor ids, or none for all of them")
            for x in ids:
                try:
                    if sensors.family(sensors.get(db, x)["type"]) != "stream":
                        raise KeyError(x)
                except KeyError:
                    raise ValueError(f"action {k}: no stream sensor {x}") from None
    elif t == "pipeline":
        if a.get("pipeline") is not None:
            if not isinstance(a["pipeline"], int) or not db.one("SELECT id FROM $r", r=R("pipeline", a["pipeline"])):
                raise ValueError(f"action {k}: no pipeline {a['pipeline']}")
        if a.get("steps") is not None:
            if (
                not isinstance(a["steps"], list)
                or not a["steps"]
                or not all((s if isinstance(s, str) else (s or {}).get("type")) in jobs.STEPS for s in a["steps"])
            ):
                raise ValueError(f"action {k}: steps are {', '.join(jobs.STEPS)}")
    elif t == "workflow":
        try:
            w = workflows.get(db, int(a.get("workflow") or 0), a.get("version"))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"action {k}: choose a workflow") from None
        if a.get("propose_only") and w["scope"] != "graph":
            raise ValueError(f"action {k}: propose_only is for workflows that organise the graph")
    return store.clean(a)


# ---------- saving ----------
def create(db, name, actions, schedule_expr=None, timezone="UTC", namespaces=None, description=None, enabled=True, user=None):
    d = _check(
        db,
        {
            "name": name,
            "description": description,
            "enabled": enabled,
            "schedule": schedule_expr,
            "timezone": timezone,
            "namespaces": namespaces,
            "actions": actions,
        },
    )
    rid, t = db.next_id("routine"), store.now()
    nxt = schedule.next_after(d["schedule"], d["timezone"]) if d.get("schedule") else None
    db.q(
        "CREATE $r CONTENT $d",
        r=R("routine", rid),
        d=store.clean(
            {**d, "next_run_at": _iso(nxt), "seen_recording": _last_recording(db), "created_at": t, "updated_at": t, "created_by": user}
        ),
    )
    return rid


def update(db, rid, **changes):
    row = get(db, rid)
    d = _check(db, {**changes, "_timezone": row.get("timezone")})
    sched = d.get("schedule", row.get("schedule")) if "schedule" in d else row.get("schedule")
    tz = d.get("timezone", row.get("timezone") or "UTC")
    if sched and ("schedule" in d or "timezone" in d):
        schedule.check(sched, tz)
    sets = {**d, "updated_at": store.now()}
    if "schedule" in d or "timezone" in d or ("enabled" in d and d["enabled"] and not row.get("enabled")):
        sets["next_run_at"] = _iso(schedule.next_after(sched, tz)) if sched else None
    nulls = [k for k, v in sets.items() if v is None]
    db.q("UPDATE $r MERGE $d", r=R("routine", int(rid)), d={k: v for k, v in sets.items() if v is not None})
    for k in nulls:
        db.q(f"UPDATE $r SET {k} = NONE", r=R("routine", int(rid)))


def remove(db, rid):
    get(db, rid)
    db.run(["DELETE routine_run WHERE routine = $i", "DELETE $r"], i=int(rid), r=R("routine", int(rid)))


def get(db, rid):
    row = db.one(f"SELECT {FIELDS} FROM $r", r=R("routine", int(rid)))
    if not row:
        raise KeyError(rid)
    return _view(db, row)


def _view(db, row):
    names = store.space_names(db)
    return {
        **row,
        "enabled": bool(row.get("enabled")),
        "timezone": row.get("timezone") or "UTC",
        "schedule_text": schedule.describe(row["schedule"]) if row.get("schedule") else "only when run by hand",
        "namespace_names": None if row.get("namespaces") is None else [names.get(s) for s in row["namespaces"]],
        "running": bool(row.get("running_since")),
    }


def list_routines(db):
    return [_view(db, r) for r in db.rows(f"SELECT {FIELDS} FROM routine ORDER BY id")]


def _with_changes(db, rows):
    """Runs with their graph changes counted as they stand now (some may have been undone or accepted since)."""
    ids = [r["id"] for r in rows]
    counts = {}
    for c in db.rows("SELECT run, status FROM graph_change WHERE run IN $ids", ids=ids) if ids else []:
        counts.setdefault(c["run"], {"applied": 0, "proposed": 0}).setdefault(c["status"], 0)
        counts[c["run"]][c["status"]] += 1
    for r in rows:
        got = counts.get(r["id"], {})
        r["changes"] = {"applied": got.get("applied", 0), "proposed": got.get("proposed", 0)}
    return rows


def runs(db, rid, limit=20):
    rows = db.rows(
        "SELECT record::id(id) AS id, routine, trigger, by, status, started_at, finished_at, results, error FROM routine_run "
        "WHERE routine = $r ORDER BY id DESC LIMIT $n",
        r=int(rid),
        n=int(limit),
    )
    return _with_changes(db, rows)


def get_run(db, run_id):
    row = db.one("SELECT *, record::id(id) AS id FROM $r", r=R("routine_run", int(run_id)))
    if not row:
        raise KeyError(run_id)
    return _with_changes(db, [row])[0]


def request_run(db, rid, by=None, propose_only=False):
    """Ask for a run as soon as the scheduler next looks (even when the routine is off)."""
    r = get(db, rid)
    if r["running"]:
        raise ValueError("this routine is running now")
    db.q(
        "UPDATE $r SET run_now = $d",
        r=R("routine", int(rid)),
        d=store.clean({"by": by, "at": store.now(), "propose_only": propose_only or None}),
    )


# ---------- running ----------
def _spaces(db, routine):
    return sorted(routine["namespaces"]) if routine.get("namespaces") is not None else sorted(store.space_names(db))


def _last_recording(db):
    """The newest recording's id (ids only grow), or 0."""
    row = db.one("SELECT record::id(id) AS id FROM recording ORDER BY id DESC LIMIT 1")
    return row["id"] if row else 0


def _recordings(db, spaces, pick, seen, limit, cfg=None):
    """Recording ids an action takes. New ones are those after `seen["since"]` (the newest recording the last run
    covered); how far this action got is left in `seen["pending"]` (the newest it took when it hit its limit, else
    the newest there was), for the run to keep once the action has done its work. Unindexed ones are those whose
    passages aren't embedded with the configured model (semantic.py)."""
    if pick == "unindexed":
        return semantic.unindexed(db, cfg, spaces, limit)
    q, upto = "SELECT record::id(id) AS id FROM recording WHERE space IN $s", _last_recording(db)
    if pick == "new":
        q += " AND record::id(id) > $since AND record::id(id) <= $upto"
    elif pick == "unprocessed":
        q += " AND status IN $u"
    ids = [r["id"] for r in db.rows(q + " ORDER BY id LIMIT $n", s=spaces, since=seen["since"], upto=upto, u=UNPROCESSED, n=limit)]
    if pick == "new":
        seen["pending"] = ids[-1] if len(ids) >= limit else upto
    return ids


def _sync(db, cfg, a, spaces, say):
    ws = a.get("watches")
    rows = db.rows("SELECT record::id(id) AS id, space, enabled FROM watch_path WHERE space IN $s", s=spaces)
    picked = [w["id"] for w in rows if (w["id"] in ws if ws is not None else w.get("enabled", True))]
    total = {"folders": 0, "new": 0, "errors": 0}
    for wid in picked:
        try:
            st = sources.poll_watch(db, cfg, wid, say)
            total["folders"] += 1
            total["new"] += st.get("new", 0)
            total["errors"] += st.get("errors", 0)
            say(f"folder {wid}: {st.get('new', 0)} new")
        except Exception as e:  # noqa: BLE001 - one source being down mustn't stop the rest
            total["errors"] += 1
            db.q("UPDATE $r SET last_error = $e", r=R("watch_path", wid), e=f"{type(e).__name__}: {e}"[:300])
            say(f"folder {wid}: {type(e).__name__}: {e}")
    return total


def _queue(db, rids, steps, by, pipeline=None, add=False, say=print):
    queued, errors = 0, 0
    for rid in rids:
        try:
            if add:
                jobs.add_steps(db, rid, steps, by=by)
            else:
                jobs.enqueue(db, rid, steps, by=by, pipeline=pipeline)
            queued += 1
        except (KeyError, ValueError) as e:
            errors += 1
            say(f"recording {rid}: {e}")
    return {"recordings": len(rids), "queued": queued, "errors": errors}


def _action(db, cfg, routine, run_id, a, seen, propose_only, say):
    spaces, by = _spaces(db, routine), f"routine:{routine['id']}"
    limit = a.get("limit") or DEFAULT_LIMIT
    if a["type"] == "sync":
        return _sync(db, cfg, a, spaces, say)
    if a["type"] == "sensors":
        got = sensors.tidy(db, cfg, a.get("sensors"), say=say)
        got["triaged"] = sensor_patterns.triage(db, cfg, a.get("sensors"), say=say)
        got["digests"] = sensor_digests.write(db, cfg, a.get("sensors"), say=say)
        return got
    if a.get("recordings") == "unindexed" and not semantic.configured(cfg):
        say("search by meaning is off, or has no embeddings server: nothing to index")
        return {"recordings": 0, "queued": 0, "errors": 0}
    if a["type"] == "pipeline":
        rids = _recordings(db, spaces, a.get("recordings", "new"), seen, limit, cfg)
        return _queue(db, rids, a.get("steps"), by, a.get("pipeline"), say=say)
    w = workflows.get(db, int(a["workflow"]), a.get("version"))
    if w["scope"] == "graph":
        origin = {"routine": routine["id"], "run": run_id}
        with activity.scope(db, f"workflow:{w['id']}"):
            done, stats = organize.run(db, cfg, w["id"], spaces, w["version"], say, origin, propose_only or a.get("propose_only", False))
        return {"workflow": w["name"], "version": w["version"], "nodes": sum(v == "done" for v in done.values()), **stats}
    rids = _recordings(db, spaces, a.get("recordings", "new"), seen, limit, cfg)
    step = {"type": "workflow", "workflow": w["id"], "version": w["version"]}
    return {"workflow": w["name"], "version": w["version"], **_queue(db, rids, [step], by, add=True, say=say)}


def _takes_new(db, a):
    """Whether an action works on new recordings (a graph workflow doesn't look at recordings)."""
    if a["type"] in ("sync", "sensors") or a.get("recordings", "new") != "new":
        return False
    if a["type"] == "workflow":
        try:
            return workflows.get(db, int(a["workflow"]), a.get("version"))["scope"] != "graph"
        except (KeyError, TypeError, ValueError):
            return True
    return True


def run(db, cfg, rid, trigger="manual", by=None, propose_only=False, log=None):
    """Run a routine now, here; the run's id. Each action is tried even when one before it failed."""
    with telemetry.span(
        "routine", {"lens.routine.id": str(rid), "lens.routine.trigger": trigger, "lens.routine.propose_only": propose_only}
    ):
        return _run(db, cfg, rid, trigger, by, propose_only, log)


def _run(db, cfg, rid, trigger, by, propose_only, log):
    """A run, with its calls counted for the routine and the run (activity.py); its run row says what it cost."""
    run_id = db.next_id("routine_run")
    refs = [f"routine:{int(rid)}", f"routine_run:{run_id}"]
    with activity.scope(db, *refs, cfg=cfg):
        status = _run_actions(db, cfg, rid, run_id, trigger, by, propose_only, log)
    usd, tokens = activity.run_total(db, refs[1])
    db.q("UPDATE $r SET cost_usd = $u, tokens = $n", r=R("routine_run", run_id), u=usd, n=tokens)
    activity.record("run", f"routine.{status}", refs, cfg, db, cost_usd=usd, tokens_in=tokens, ok=status != "error",
                    detail={"trigger": trigger})
    return run_id


def _run_actions(db, cfg, rid, run_id, trigger, by, propose_only, log):
    routine = get(db, rid)
    since = routine.get("seen_recording") or 0
    seen = {"since": since, "marks": [], "failed": False}
    lines = []
    started = store.now()

    def say(msg):
        lines.append(f"{store.now()[11:19]} {msg}")
        del lines[:-LOG_MAX]
        if log:
            log(f"routine {rid}: {msg}")
        db.q("UPDATE $r SET heartbeat_at = $t", r=R("routine", int(rid)), t=store.now())

    db.q(
        "CREATE $r CONTENT $d",
        r=R("routine_run", run_id),
        d=store.clean({"routine": int(rid), "trigger": trigger, "by": by, "status": "running", "started_at": started, "results": []}),
    )
    results, failed = [], 0
    for k, a in enumerate(routine.get("actions") or [], 1):
        say(f"{k}. {a['type']}")
        seen["pending"] = None
        try:
            with telemetry.span(f"action {a['type']}", {"lens.routine.action": a["type"], "lens.routine.action.index": k}):
                got = _action(db, cfg, routine, run_id, a, seen, propose_only, say)
            results.append({"type": a["type"], "status": "done", "result": got})
            if seen["pending"] is not None:
                seen["marks"].append(seen["pending"])
        except Exception as e:  # noqa: BLE001 - recorded on the run; the next action still runs
            failed += 1
            seen["failed"] = seen["failed"] or _takes_new(db, a)  # its new recordings are new again next time
            results.append({"type": a["type"], "status": "error", "error": f"{type(e).__name__}: {e}"[:300]})
            say(f"{a['type']} failed: {type(e).__name__}: {e}")
        db.q("UPDATE $r SET results = $x, log = $l", r=R("routine_run", run_id), x=results, l=lines)
    changes = {
        s: len(db.values("SELECT VALUE id FROM graph_change WHERE run = $r AND status = $s", r=run_id, s=s))
        for s in ("applied", "proposed")
    }
    status = "done" if not failed else ("error" if failed == len(results) else "partly")
    telemetry.record("lens.routine.runs", 1, {"lens.outcome": status, "lens.routine.trigger": trigger})
    db.q(
        "UPDATE $r MERGE $d",
        r=R("routine_run", run_id),
        d={"status": status, "finished_at": store.now(), "results": results, "log": lines, "changes": changes},
    )
    db.q(
        "UPDATE $r SET last_run_at = $t, last_status = $s, last_run = $i, seen_recording = $seen, running_since = NONE, heartbeat_at = NONE",
        r=R("routine", int(rid)),
        seen=since if seen["failed"] or not seen["marks"] else min(seen["marks"]),
        t=started,
        s=status,
        i=run_id,
    )
    return status


def _claim(db, r, now):
    """Take a routine that is due, so no other process runs it too: the claim, or None when someone else has it."""
    stale = _iso(now - dt.timedelta(minutes=STALE_MINUTES))
    nxt = _iso(schedule.next_after(r["schedule"], r.get("timezone") or "UTC", now)) if r.get("schedule") else None
    got = db.rows(
        "UPDATE $r SET running_since = $t, heartbeat_at = $t, next_run_at = $nxt, run_now = NONE "
        "WHERE (running_since = NONE OR heartbeat_at < $stale) AND next_run_at = $was AND run_now = $req RETURN AFTER",
        r=R("routine", r["id"]),
        t=_iso(now),
        nxt=nxt,
        stale=stale,
        was=r.get("next_run_at"),
        req=r.get("run_now"),
    )
    return got[0] if got else None


def sweep(db, now=None):
    """Runs whose process died: the run says so, and its routine can run again. How many."""
    now = now or dt.datetime.now(dt.timezone.utc)
    stale = _iso(now - dt.timedelta(minutes=STALE_MINUTES))
    dead = db.rows(
        "SELECT record::id(id) AS id FROM routine WHERE running_since != NONE AND (heartbeat_at = NONE OR heartbeat_at < $s)", s=stale
    )
    for r in dead:
        db.q("UPDATE $r SET running_since = NONE, heartbeat_at = NONE, last_status = 'error'", r=R("routine", r["id"]))
    live = set(db.values("SELECT VALUE record::id(id) FROM routine WHERE running_since != NONE"))
    n = 0
    for run in db.rows("SELECT record::id(id) AS id, routine FROM routine_run WHERE status = 'running'"):
        if run["routine"] not in live:
            db.q(
                "UPDATE $r SET status = 'error', error = 'stopped without finishing', finished_at = $t",
                r=R("routine_run", run["id"]),
                t=_iso(now),
            )
            n += 1
    return n


def _idle(db, cfg, r):
    """Whether a routine's every action indexes for search by meaning, and there's nothing to index: it's off, its
    server failed a moment ago, or every recording in its namespaces is indexed."""
    acts = r.get("actions") or []
    if acts and all(a.get("type") == "sensors" for a in acts):
        return not sensors.any_streams(db)
    if not acts or any(a.get("type") != "pipeline" or a.get("recordings") != "unindexed" for a in acts):
        return False
    return not semantic.configured(cfg) or bool(semantic.failing(db, cfg)) or not semantic.unindexed(db, cfg, _spaces(db, r), 1)


def run_due(db, cfg, log=print, now=None):
    """Run every routine that is due (or asked to run now); how many ran."""
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = _iso(now)
    sweep(db, now)
    activity.tidy(db, cfg)  # the activity ledger past activity.keep_days, once an hour
    due = db.rows(
        "SELECT record::id(id) AS id, schedule, timezone, next_run_at, run_now, namespaces, actions FROM routine "
        "WHERE run_now != NONE OR (enabled = true AND next_run_at != NONE AND next_run_at <= $n)",
        n=stamp,
    )
    done = 0
    for r in due:
        if not r.get("run_now") and _idle(db, cfg, r):  # nothing to do: its next time, without a run to show for it
            nxt = _iso(schedule.next_after(r["schedule"], r.get("timezone") or "UTC", now)) if r.get("schedule") else None
            db.q("UPDATE $r SET next_run_at = $nxt WHERE next_run_at = $was", r=R("routine", r["id"]), nxt=nxt, was=r.get("next_run_at"))
            continue
        if not _claim(db, r, now):
            continue
        req = r.get("run_now") or {}
        try:
            run(db, cfg, r["id"], "manual" if req else "schedule", req.get("by"), bool(req.get("propose_only")), log)
            done += 1
        except Exception as e:  # noqa: BLE001 - recorded on the routine; tried again at its next time
            db.q(
                "UPDATE $r SET running_since = NONE, last_status = 'error', last_error = $e",
                r=R("routine", r["id"]),
                e=f"{type(e).__name__}: {e}"[:300],
            )
            if log:
                log(f"routine {r['id']}: {type(e).__name__}: {e}")
    return done


def start(db, cfg_fn, stop, log=None):
    """The scheduling threads, until `stop` is set: watched folders scanned when due (every sources.check_seconds) and
    routines run when due (every CHECK_SECONDS). The API runs them with its background work; `lens worker` runs them too,
    since Docker and the packages run the API without it."""

    def every(seconds, fn, what):
        def loop():
            while not stop.wait(max(MIN_WAIT_SECONDS, seconds())):
                try:
                    fn(db, cfg_fn(), log=log)
                except Exception as e:  # noqa: BLE001 - keep going
                    if log:
                        log(f"{what}: {type(e).__name__}: {e}")

        th = threading.Thread(target=loop, daemon=True, name=what)
        th.start()
        return th

    return [
        every(lambda: cfg_fn()["sources"]["check_seconds"], sources.poll_due, "watched folders"),
        every(lambda: CHECK_SECONDS, run_due, "routines"),
        every(lambda: cfg_fn()["fedora"]["sync_seconds"], fedora.sync_due, "fedora"),
    ]


# ---------- what a fresh archive starts with ----------
INDEX_NAME = "Index for search by meaning"
SENSORS_NAME = "Tidy sensor data"


def seed(db):
    """The graph-organising workflow, and a routine that runs it every night (off until someone turns it on); and a
    routine that indexes, every hour, what isn't yet searchable by meaning (it does nothing while that's off)."""
    if not db.one("SELECT id FROM $r", r=R("seed", "routines")):
        _seed_graph(db)
        db.q("UPSERT $r CONTENT $d", r=R("seed", "routines"), d={"at": store.now()})
    if not db.one("SELECT id FROM $r", r=R("seed", "semantic")):
        create(
            db,
            INDEX_NAME,
            [{"type": "pipeline", "steps": ["embed"], "recordings": "unindexed", "limit": DEFAULT_LIMIT}],
            "20 * * * *",
            description="Embeds the passages of recordings not yet searchable by meaning with the current embedding model "
            "(after the model changes, or for recordings made before search by meaning was set up), 500 an hour.",
            user="lens-archive",
        )
        db.q("UPSERT $r CONTENT $d", r=R("seed", "semantic"), d={"at": store.now()})
    if not db.one("SELECT id FROM $r", r=R("seed", "sensors")):
        create(
            db,
            SENSORS_NAME,
            [{"type": "sensors"}],
            "40 * * * *",
            description="Drops sensor readings and hourly summaries past each sensor's retention, every hour (it does nothing "
            "while there are no stream sensors).",
            user="lens-archive",
        )
        db.q("UPSERT $r CONTENT $d", r=R("seed", "sensors"), d={"at": store.now()})


def _seed_graph(db):
    wid = next((w["id"] for w in workflows.list_workflows(db) if w["name"] == organize.DEFAULT_NAME), None)
    if wid is None:
        wid = workflows.create(db, organize.DEFAULT_NAME, organize.DEFAULT_GRAPH, organize.DEFAULT_DESCRIPTION, "lens-archive", "graph")
    create(
        db,
        "Organise the graph every night",
        [{"type": "workflow", "workflow": wid}],
        "0 3 * * *",
        description="Runs the graph-organising workflow over every namespace at 03:00. Off until you turn it on.",
        enabled=False,
        user="lens-archive",
    )
