"""Batch runs: a pipeline, some steps or a template over many recordings. An estimate comes first (recordings, time,
LLM calls and tokens, cost if prices are set, outputs that would be replaced); big runs need typed confirmation; a run
can start with a sample and continue after review; it can be paused, resumed, cancelled and have failures retried; and
its outputs can be read and exported as one table."""

from __future__ import annotations

import csv
import io
import json
import re

from . import jobs, llm, pipelines, recsets, store, templates

R = store.R
SECONDS = {"transcribe": 0.12, "diarize": 0.05, "shots": 0.03}  # per second of media
PER_FRAME = {"ocr": 0.4, "faces": 0.2, "objects": 0.2}  # per sampled frame
PER_RECORDING = {"analyze": 1.0, "report": 0.5, "export": 1.0}


def steps_for(db, run):
    run = dict(run or {})
    if run.get("template"):
        t = templates.get(db, int(run["template"]))
        key = run.get("key") or re.sub(r"\W+", "_", t["name"].lower()).strip("_")[:40] or "output"
        step = {
            "prompt": {"type": "llm", "template": t["id"], "key": key},
            "report": {"type": "report", "template": t["id"]},
            "export": {"type": "export", "template": t["id"], "filename": run.get("filename") or "{{ recording.title }}.md"},
        }[t["kind"]]
        return pipelines.validate_steps(db, [step]), f"{t['name']} ({t['kind']})"
    if run.get("pipeline"):
        p = pipelines.get(db, int(run["pipeline"]))
        return p["steps"], p["name"]
    steps = pipelines.validate_steps(db, run.get("steps"))
    return steps, ", ".join(s["type"] for s in steps)


def select(db, spec, readable, editable):
    """(recording ids the person can run things on, how many matched but they can't change)."""
    spec = dict(spec or {})
    if spec.get("collection"):
        c = recsets.get(db, spec["collection"])
        ids = recsets.members(db, c, readable)
    elif spec.get("recordings"):
        ids = recsets.resolve(db, readable, recordings=spec["recordings"])
    else:
        f = dict(spec.get("filter") or {})
        if spec.get("namespace"):
            f["namespaces"] = [spec["namespace"]]
        if spec.get("entity"):
            f["entities"] = [int(spec["entity"])]
        if spec.get("speaker"):
            f["speakers"] = [int(spec["speaker"])]
        ids = recsets.resolve(db, readable, recsets.clean_filter(f))
    spaces = (
        {
            r["id"]: r["space"]
            for r in db.rows("SELECT record::id(id) AS id, space FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in ids])
        }
        if ids
        else {}
    )
    ok = [i for i in ids if spaces.get(i) in editable]
    return ok, len(ids) - len(ok)


def estimate(db, cfg, ids, steps):
    recs = (
        db.rows(
            "SELECT record::id(id) AS id, duration_ms, media, source, stats FROM recording WHERE id IN $ids",
            ids=[R("recording", i) for i in ids],
        )
        if ids
        else []
    )
    every = cfg["video"]["sample_seconds"]
    secs, calls, tin, tout = 0.0, 0, 0, 0
    kinds = {}
    for r in recs:
        dur = (r.get("duration_ms") or 0) / 1000
        kind = (r.get("media") or {}).get("kind") or ("audio" if r.get("source") == "audio" else "transcript")
        kinds[kind] = kinds.get(kind, 0) + 1
        chars = min(((r.get("stats") or {}).get("words") or dur * 2.5) * 6, cfg["llm"].get("max_chars") or 24000)
        for s in steps:
            t = s["type"]
            if t in SECONDS and kind != "transcript" and (t != "shots" or kind == "video"):
                secs += SECONDS[t] * dur
            elif t in PER_FRAME and kind == "video":
                secs += PER_FRAME[t] * dur / every
            elif t in ("llm", "summarize"):
                calls += 1
                tin += int(chars / 4) + 400
                tout += 600
            else:
                secs += PER_RECORDING.get(t, 0.5)
    secs += tout / 40  # ~40 tokens a second from a local model
    price_in, price_out = cfg["ai"].get("price_in"), cfg["ai"].get("price_out")
    cost = round(tin / 1e6 * price_in + tout / 1e6 * price_out, 2) if price_in is not None and price_out is not None else None
    keys = [s["key"] for s in steps if s["type"] == "llm"]
    replaced = len(db.values("SELECT VALUE id FROM output WHERE recording IN $r AND key IN $k", r=list(ids), k=keys)) if keys and ids else 0
    n = len(ids)
    over = n > (cfg["ai"].get("confirm_over_recordings") or 10**9) or (
        cost is not None and cfg["ai"].get("confirm_over_cost") is not None and cost > cfg["ai"]["confirm_over_cost"]
    )
    return {
        "recordings": n,
        "by_kind": kinds,
        "hours": round(sum((r.get("duration_ms") or 0) for r in recs) / 3.6e6, 2),
        "seconds": int(secs),
        "llm": {"calls": calls, "input_tokens": tin, "output_tokens": tout, "cost": cost, "model": cfg["llm"].get("model")},
        "would_replace": replaced,
        "needs_confirmation": over,
        "confirm_text": f"RUN {n}" if over else None,
    }


def create(db, cfg, user, ids, steps, label, selection=None, sample=None, confirm=None, skipped=0):
    if not ids:
        raise ValueError("nothing to run: no recordings you can change match")
    est = estimate(db, cfg, ids, steps)
    if est["needs_confirmation"] and not sample and (confirm or "").strip() != est["confirm_text"]:
        raise PermissionError(f"type {est['confirm_text']} to confirm a run this size")
    first = ids[: int(sample)] if sample else ids
    bid = db.next_id("batch")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("batch", bid),
        d=store.clean(
            {
                "label": label,
                "steps": steps,
                "selection": selection,
                "recordings": ids,
                "started": first,
                "status": "sample" if sample else "running",
                "skipped": skipped,
                "estimate": est,
                "created_by": user,
                "created_at": store.now(),
            }
        ),
    )
    for rid in first:
        jobs.enqueue(db, rid, steps, by=user, batch=bid)
    return bid


def get(db, bid):
    b = db.one(
        "SELECT record::id(id) AS id, label, steps, selection, recordings, started, status, skipped, estimate, created_by, created_at, report FROM $r",
        r=R("batch", int(bid)),
    )
    if not b:
        raise KeyError(bid)
    counts = {}
    for j in db.rows("SELECT status FROM job WHERE batch = $b", b=b["id"]):
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    done = counts.get("succeeded", 0) + counts.get("failed", 0) + counts.get("cancelled", 0)
    b["progress"] = {
        "counts": counts,
        "done": done,
        "total": len(b.get("started") or []),
        "remaining": len(b.get("recordings") or []) - len(b.get("started") or []),
    }
    if b["status"] in ("running", "sample") and done >= len(b.get("started") or []) and not counts.get("running"):
        b["status"] = "sample done" if b["status"] == "sample" else "finished"
    return b


def continue_run(db, bid, user):
    b = get(db, bid)
    rest = [i for i in b["recordings"] if i not in set(b["started"])]
    for rid in rest:
        try:
            jobs.enqueue(db, rid, b["steps"], by=user, batch=b["id"])
        except KeyError:  # deleted since the run was planned
            continue
    db.q("UPDATE $r SET started = $s, status = 'running'", r=R("batch", b["id"]), s=b["recordings"])
    return len(rest)


def pause(db, bid):
    db.q("UPDATE job SET status = 'paused', updated_at = $t WHERE batch = $b AND status = 'queued'", b=int(bid), t=store.now())
    db.q("UPDATE $r SET status = 'paused'", r=R("batch", int(bid)))


def resume(db, bid):
    db.q("UPDATE job SET status = 'queued', updated_at = $t WHERE batch = $b AND status = 'paused'", b=int(bid), t=store.now())
    db.q("UPDATE $r SET status = 'running'", r=R("batch", int(bid)))


def cancel(db, bid):
    t = store.now()
    db.q(
        "UPDATE job SET status = 'cancelled', finished_at = $t, updated_at = $t WHERE batch = $b AND status IN ['queued', 'paused']",
        b=int(bid),
        t=t,
    )
    db.q("UPDATE job SET cancel_requested = true, updated_at = $t WHERE batch = $b AND status = 'running'", b=int(bid), t=t)
    db.q("UPDATE $r SET status = 'cancelled'", r=R("batch", int(bid)))


def retry_failed(db, bid):
    ids = db.values("SELECT VALUE record::id(id) FROM job WHERE batch = $b AND status = 'failed'", b=int(bid))
    done = 0
    for jid in ids:
        try:
            jobs.retry(db, jid)
            done += 1
        except ValueError:  # its recording was deleted
            continue
    return done


def results(db, bid, key=None):
    """One table of a batch's outputs: list-valued fields become one row per item (e.g. every action item)."""
    b = get(db, bid)
    key = key or next((s.get("key") for s in b["steps"] if s["type"] == "llm"), None)
    if not key:
        return {"key": None, "columns": [], "rows": []}
    recs = {
        r["id"]: r
        for r in db.rows(
            "SELECT record::id(id) AS id, title, recorded_at FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in b["started"]]
        )
    }
    rows, cols = [], ["recording", "title", "date"]
    for o in db.rows("SELECT recording, value FROM output WHERE recording IN $r AND key = $k", r=b["started"], k=key):
        v, base = (
            o["value"],
            {
                "recording": o["recording"],
                "title": recs.get(o["recording"], {}).get("title"),
                "date": (recs.get(o["recording"], {}).get("recorded_at") or "")[:10],
            },
        )
        lists = (
            [k for k, x in v.items() if isinstance(x, list) and x and all(isinstance(i, dict) for i in x)] if isinstance(v, dict) else []
        )
        if lists:
            for item in v[lists[0]]:
                rows.append({**base, **{k: x for k, x in item.items() if not isinstance(x, (list, dict))}})
        else:
            rows.append(
                {
                    **base,
                    **(
                        {k: (json.dumps(x) if isinstance(x, (list, dict)) else x) for k, x in v.items()}
                        if isinstance(v, dict)
                        else {"value": v}
                    ),
                }
            )
    for r in rows:
        cols += [k for k in r if k not in cols]
    return {"key": key, "columns": cols, "rows": sorted(rows, key=lambda r: (r["date"], r["recording"]), reverse=True)}


def export(table, fmt="csv"):
    cols = table["columns"]
    if fmt == "md":
        esc = lambda x: str("" if x is None else x).replace("|", "\\\\|").replace("\\n", " ")  # noqa: E731
        return (
            "| "
            + " | ".join(cols)
            + " |\\n|"
            + "---|" * len(cols)
            + "\\n"
            + "".join("| " + " | ".join(esc(r.get(c)) for c in cols) + " |\\n" for r in table["rows"])
        )
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in table["rows"]:
        w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})
    return out.getvalue()


def combine(db, cfg, bid, instructions=None, key=None):
    """A collection report: one overview written from every recording's result, citing each as [n]."""
    table = results(db, bid, key)
    rows = table["rows"]
    if not rows:
        raise ValueError("there are no results to combine yet")
    lines = [
        f"[{i}] {r['title']} ({r['date']}): "
        + "; ".join(f"{k}: {v}" for k, v in r.items() if k not in ("recording", "title", "date") and v not in (None, ""))
        for i, r in enumerate(rows, 1)
    ]
    ask = instructions or "Write one overview of these results: the main themes, notable points, and anything that changed over time."
    text = llm.chat(
        cfg,
        [
            {
                "role": "system",
                "content": "You write concise overviews across many recordings. Use only the numbered results and cite them as [n].",
            },
            {"role": "user", "content": f"{ask}\n\nResults:\n" + "\n".join(lines)[: cfg["llm"].get("max_chars") or 24000]},
        ],
    )
    used = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    report = {
        "text": text,
        "instructions": ask,
        "key": table["key"],
        "at": store.now(),
        "refs": [
            {"n": i, "recording_id": r["recording"], "title": r["title"], "date": r["date"]} for i, r in enumerate(rows, 1) if i in used
        ],
    }
    db.q("UPDATE $r SET report = $p", r=R("batch", int(bid)), p=report)
    return report
