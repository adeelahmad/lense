"""Automation actions that run Lens's own work (docs/automations.md, Actions): `run_workflow`, `run_pipeline`,
`run_routine` and `enhance`.

Each changes something inside Lens (effect `lens`): a workflow or a pipeline on the asset the run is about, in the
automation's namespaces, runs without asking; on any other asset, and a routine always, it asks first. The work is
queued by the automation (its jobs and runs are by "automation: <name>"), with its person's permission to process
assets in that namespace; a routine is an admin's to run, as in Settings.

`asset` takes the activity's object id (`lens:recording/<id>`, the default `{{ event.object.id }}`) or an asset id.
"""

from __future__ import annotations

from . import actions, budgets, enhance, jobs, pipelines, routines, store, workflows


def _int(p, key, what):
    try:
        return int(p[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{what} is an id") from None


# ---------- run_workflow ----------
def _run_workflow(db, cfg, intent, user, dry):
    p = intent["params"]
    acc = actions.person(db, intent, user)
    wid = _int(p, "workflow", "the workflow")
    rec = actions.asset_for(db, intent, acc, actions.asset_id(p.get("asset")), "process")
    try:
        w = workflows.get(db, wid, p.get("version"))
    except KeyError:
        raise ValueError(f"there's no workflow {wid}" + (f" version {p['version']}" if p.get("version") else "")) from None
    if w["scope"] != "recording":
        raise ValueError(f"the workflow {w['name']} organises the graph; run it with a routine")
    out = {"workflow": wid, "version": w["version"], "asset": rec["id"]}
    if dry:
        return {**out, "job": None, "would": f"run the workflow {w['name']} on {rec.get('title') or rec['id']}"}
    step = {"type": "workflow", "workflow": wid, "version": w["version"]}
    return {**out, "job": jobs.add_steps(db, rec["id"], [step], by=actions.actor(db, intent)["email"])}


# ---------- run_pipeline ----------
def _run_pipeline(db, cfg, intent, user, dry):
    p = intent["params"]
    acc = actions.person(db, intent, user)
    rec = actions.asset_for(db, intent, acc, actions.asset_id(p.get("asset")), "process")
    pid = _int(p, "pipeline", "the pipeline") if p.get("pipeline") is not None else None
    name = "its namespace's pipeline"
    if pid is not None:
        try:
            name = f"the pipeline {pipelines.get(db, pid)['name']}"
        except KeyError:
            raise ValueError(f"there's no pipeline {pid}") from None
    out = {"pipeline": pid, "asset": rec["id"]}
    if dry:
        return {**out, "job": None, "would": f"run {name} on {rec.get('title') or rec['id']}"}
    try:
        jid = jobs.enqueue(db, rec["id"], None, by=actions.actor(db, intent)["email"], pipeline=pid)
    except KeyError:
        raise ValueError(f"there's no pipeline {pid}") from None
    return {**out, "job": jid}


# ---------- enhance ----------
def _enhance(db, cfg, intent, user, dry):
    p = intent["params"]
    acc = actions.person(db, intent, user)
    rec = actions.asset_for(db, intent, acc, actions.asset_id(p.get("asset")), "process")
    source = (db.one("SELECT source FROM $r", r=store.R("recording", rec["id"])) or {}).get("source")
    if source not in jobs.ENHANCED:
        raise ValueError(f"{rec.get('title') or rec['id']} isn't an image, audio or video: only those are enhanced")
    out = {"asset": rec["id"]}
    if dry:
        again = "read it again" if source == "image" else "transcribe it again"
        return {**out, "job": None, "would": f"enhance {rec.get('title') or rec['id']} and {again}"}
    return {**out, "job": enhance.queue(db, rec["id"], actions.actor(db, intent)["email"])}


# ---------- run_routine ----------
def _run_routine(db, cfg, intent, user, dry):
    p = intent["params"]
    acc = actions.person(db, intent, user)
    rid = _int(p, "routine", "the routine")
    try:
        r = routines.get(db, rid)
    except KeyError:
        raise ValueError(f"there's no routine {rid}") from None
    if not acc["admin"]:
        raise ValueError(f"routines are run by admins, and {acc['name']} isn't one")
    spaces = {int(s.split(":")[1]) for s in routines.refs(db, r) if s.startswith("space:")}
    if intent["spaces"] and not spaces <= set(intent["spaces"]):
        raise ValueError(f"the routine {r['name']} works in namespaces outside this automation's")
    propose = bool(p.get("propose_only"))
    out = {"routine": rid, "propose_only": propose}
    if dry:
        return {**out, "requested": False, "would": f"run the routine {r['name']}" + (", proposing its changes" if propose else "")}
    verdict = budgets.check(db, cfg, routines.refs(db, r))
    if not verdict["go"]:
        raise ValueError(verdict["why"])
    try:
        routines.request_run(db, rid, actions.actor(db, intent)["email"], propose)
    except ValueError as e:
        raise ValueError(f"the routine {r['name']}: {e}") from None
    return {**out, "requested": True}


actions.register(
    actions.ActionSpec(
        name="run_workflow",
        label="Run a workflow",
        group="Run",
        description="Runs a workflow on an asset as a job: at once on the asset the activity is about, else asking first.",
        params={
            "type": "object",
            "properties": {
                "workflow": {"type": "integer", "title": "Workflow"},
                "version": {"type": "integer", "title": "Version", "description": "empty: the published one"},
                "asset": actions.asset_param(),
            },
        },
        required=("workflow",),
        effect="lens",
        subject=actions.asset_subject,
        output={
            "type": "object",
            "properties": {
                "job": {"type": "integer"},
                "workflow": {"type": "integer"},
                "version": {"type": "integer"},
                "asset": {"type": "integer"},
            },
        },
        perform=_run_workflow,
        describe=lambda p: f"Run workflow {p.get('workflow')} on {actions.on_asset(p)}",
    )
)

actions.register(
    actions.ActionSpec(
        name="run_pipeline",
        label="Run a pipeline",
        group="Run",
        description="Processes an asset again with a pipeline (or its namespace's own), keeping its outputs.",
        params={
            "type": "object",
            "properties": {
                "pipeline": {"type": "integer", "title": "Pipeline", "description": "empty: the one its namespace uses"},
                "asset": actions.asset_param(),
            },
        },
        effect="lens",
        subject=actions.asset_subject,
        output={
            "type": "object",
            "properties": {"job": {"type": "integer"}, "pipeline": {"type": "integer"}, "asset": {"type": "integer"}},
        },
        perform=_run_pipeline,
        describe=lambda p: f"Run {'pipeline ' + str(p['pipeline']) if p.get('pipeline') else 'its pipeline'} on {actions.on_asset(p)}",
    )
)

actions.register(
    actions.ActionSpec(
        name="enhance",
        label="Enhance an image or a recording",
        group="Run",
        description="Makes an enhanced copy of an image (bigger, sharper, less noisy) and reads it again from that copy, "
        "or of a recording's sound (less noise, an even level, clipping repaired) and transcribes it again; the original "
        "stays.",
        params={"type": "object", "properties": {"asset": actions.asset_param("Image, audio or video")}},
        effect="lens",
        subject=actions.asset_subject,
        output={"type": "object", "properties": {"job": {"type": "integer"}, "asset": {"type": "integer"}}},
        perform=_enhance,
        describe=lambda p: f"Enhance {actions.on_asset(p)}",
    )
)

actions.register(
    actions.ActionSpec(
        name="run_routine",
        label="Run a routine",
        group="Run",
        description="Asks the scheduler to run a routine now (an admin's); always asks you first.",
        params={
            "type": "object",
            "properties": {
                "routine": {"type": "integer", "title": "Routine"},
                "propose_only": {"type": "boolean", "title": "Only propose its changes", "default": False},
            },
        },
        required=("routine",),
        effect="lens",
        subject=lambda p: None,
        output={
            "type": "object",
            "description": "the scheduler starts the run within half a minute; its id shows in the routine's runs",
            "properties": {"routine": {"type": "integer"}, "requested": {"type": "boolean"}, "propose_only": {"type": "boolean"}},
        },
        perform=_run_routine,
        describe=lambda p: f"Run routine {p.get('routine')}" + (", proposing its changes" if p.get("propose_only") else ""),
    )
)
