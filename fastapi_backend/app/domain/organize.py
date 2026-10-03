"""Organising the entity graph with a workflow: graph workflows (scope `graph`) run over namespaces rather than over one
recording, usually from a routine, and propose or make changes to the entities in them.

Their nodes, besides the primitives every workflow has (flow.py: input, pick, condition, switch, merge, set, template,
filter, for each, repeat, group and custom nodes):

- `candidates`: pairs of entities that may be one thing, found by rules (same letters, acronym, spelling, sounds alike,
  one name inside the other). `kind` merge looks inside each namespace; link looks across namespaces whose graph is
  shared (the global graph already joins names that are exactly the same). Pairs someone said are different are left
  out, and so are pairs with a change already proposed.
- `llm_judge`: asks the model, a batch of pairs at a time, whether each pair is the same thing, with lines where each
  name was said; each pair gets a `verdict` {same, confidence, keep, why}.
- `apply_changes`: makes the changes it is given (merges, links), or proposes them for someone to accept. Only pairs
  whose confidence (the model's, else the rules') is at least `apply_above` are made, at most `max_apply` a run;
  without `apply_above` everything is proposed. Every change is recorded and can be undone, one by one or a whole run.

Each change is a `graph_change` row: {kind, a, b, keep, space(s), reason, confidence, verdict, status (proposed,
applied, dismissed, undone), merge (the entity_merge id behind an applied merge), routine, run, workflow}.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from . import entities, llm, store

R = store.R
GRAPH_NODES = ("input", "pick", "condition", "merge", "candidates", "llm_judge", "filter", "apply_changes")
OWN_NODES = ("candidates", "llm_judge", "apply_changes")
CONFIG = {
    "candidates": {"kind", "min_confidence", "limit", "types"},
    "llm_judge": {"model", "instructions", "batch"},
    "filter": {"path", "op", "value"},
    "apply_changes": {"apply_above", "max_apply"},
}
KINDS = ("merge", "link")
STATUSES = ("proposed", "applied", "dismissed", "undone")
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "judgements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pair": {"type": "integer"},
                    "same": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "keep": {"type": "string", "enum": ["a", "b"]},
                    "why": {"type": "string"},
                },
                "required": ["pair", "same", "confidence"],
            },
        }
    },
    "required": ["judgements"],
}
JUDGE_SYSTEM = (
    "You tidy a knowledge graph built from transcripts. For each numbered pair of names, say whether both names mean "
    "the same real thing (the same person, organisation, product, place, event, work or topic), how sure you are "
    "from 0 to 1, which name to keep (the fuller, correctly spelled one), and why in a few words. Say same only when "
    "the lines make it clear: two people who share a first name or a surname are not the same person. Reply with JSON "
    "only."
)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_config(nid, t, cfg):
    """ValueError naming what is wrong with a graph node's settings."""
    from . import workflows

    if t == "candidates":
        if cfg.get("kind", "merge") not in KINDS:
            raise ValueError(f"candidates node {nid}: kind is merge or link")
        mc = cfg.get("min_confidence")
        if mc is not None and (not _num(mc) or not 0 <= mc <= 1):
            raise ValueError(f"candidates node {nid}: min_confidence is between 0 and 1")
        lim = cfg.get("limit")
        if lim is not None and (not isinstance(lim, int) or not 1 <= lim <= 1000):
            raise ValueError(f"candidates node {nid}: limit is 1 to 1000 pairs")
        workflows._check_types(nid, cfg)
    elif t == "llm_judge":
        b = cfg.get("batch")
        if b is not None and (not isinstance(b, int) or not 1 <= b <= 100):
            raise ValueError(f"judge node {nid}: batch is 1 to 100 pairs")
        if cfg.get("instructions") is not None and not isinstance(cfg["instructions"], str):
            raise ValueError(f"judge node {nid}: instructions are text")
    elif t == "filter":
        workflows._check_config(None, nid, "condition", cfg)
    elif t == "apply_changes":
        a = cfg.get("apply_above")
        if a is not None and (not _num(a) or not 0 <= a <= 1):
            raise ValueError(f"apply node {nid}: apply_above is between 0 and 1, or none to only propose")
        m = cfg.get("max_apply")
        if m is not None and (not isinstance(m, int) or not 0 <= m <= 1000):
            raise ValueError(f"apply node {nid}: max_apply is 0 to 1000")


# ---------- candidates ----------
def _pair_key(kind, a, b):
    lo, hi = sorted((int(a), int(b)))
    return f"{kind}:{lo}-{hi}"


def _held(db):
    """Pairs not to suggest again: said to be different, or with a change already proposed."""
    out = {_pair_key("merge", d["a"], d["b"]) for d in db.rows("SELECT a, b FROM entity_distinct")}
    out |= {_pair_key("link", d["a"], d["b"]) for d in db.rows("SELECT a, b FROM entity_distinct")}
    out |= set(db.values("SELECT VALUE pair FROM graph_change WHERE status = 'proposed'"))
    return out


def _side(db, e, n, names):
    samples = [m["text"] for m in entities.mentions(db, e["id"], {e["space"]}, limit=2)["items"] if m.get("text")]
    return {
        "id": e["id"],
        "name": e["name"],
        "type": e["type"],
        "namespace": names.get(e["space"]),
        "space": e["space"],
        "mentions": n,
        "samples": samples,
    }


def candidates(db, spaces, c):
    """Pairs that may be one thing: [{kind, pair, a, b, reason, confidence}], most likely first."""
    kind, floor, limit = c.get("kind") or "merge", c.get("min_confidence", 0.7), c.get("limit") or 100
    types = set(c.get("types") or [])
    if kind == "link":
        spaces = set(db.values("SELECT VALUE record::id(id) FROM space WHERE graph = 'shared' AND record::id(id) IN $s", s=sorted(spaces)))
    if not spaces:
        return []
    ents = [
        e
        for e in db.rows(f"SELECT {entities.FIELDS} FROM entity WHERE space IN $s", s=sorted(spaces))
        if not e.get("hidden") and e["type"] not in entities.QUIET and (not types or e["type"] in types)
    ]
    counts = Counter(db.values("SELECT VALUE entity FROM mentions WHERE space IN $s", s=sorted(spaces)))
    ents = [e for e in ents if counts[e["id"]]]
    info, held, found = {e["id"]: e for e in ents}, _held(db), []
    if kind == "merge":
        groups = defaultdict(list)
        for e in ents:
            groups[e["space"]].append(e)
        pairs = [p for g in groups.values() for p in entities._pairs(g).items()]
    else:
        linked = {(r["a"], r["b"]) for r in db.rows("SELECT a, b FROM entity_link")}
        pairs = [
            ((a, b), why)
            for (a, b), why in entities._pairs(ents).items()
            if info[a]["space"] != info[b]["space"] and info[a]["key"] != info[b]["key"] and (a, b) not in linked
        ]
    for (a, b), (reason, conf) in pairs:
        pk = _pair_key(kind, a, b)
        if conf >= floor and pk not in held:
            found.append((conf, counts[a] + counts[b], a, b, reason, pk))
    found.sort(key=lambda x: (-x[0], -x[1]))
    names = store.space_names(db)
    return [
        {
            "kind": kind,
            "pair": pk,
            "a": _side(db, info[a], counts[a], names),
            "b": _side(db, info[b], counts[b], names),
            "reason": reason,
            "confidence": conf,
        }
        for conf, _n, a, b, reason, pk in found[:limit]
    ]


# ---------- the model's verdict ----------
def _describe(k, item):
    def side(s):
        said = "; ".join(f'"{x[:160]}"' for x in s["samples"]) or "no lines"
        return f'"{s["name"]}" ({s["type"]}, {s["mentions"]} mentions in {s["namespace"]}; said in: {said})'

    return f"{k}. a: {side(item['a'])}\n   b: {side(item['b'])}\n   rules thought: {item['reason']} ({item['confidence']})"


def judge(cfg, items, c, say=print):
    """The items, each with the model's `verdict` (pairs it gave no answer for get none)."""
    size, out = c.get("batch") or 25, []
    for start in range(0, len(items), size):
        batch = items[start : start + size]
        ask = []
        if c.get("instructions"):
            ask.append(c["instructions"].strip())
        ask.append("Pairs:\n" + "\n".join(_describe(k, it) for k, it in enumerate(batch, 1)))
        try:
            reply = llm.json_out(cfg, JUDGE_SYSTEM, "\n\n".join(ask), JUDGE_SCHEMA, c.get("model"))
        except Exception as e:  # noqa: BLE001 - one bad batch leaves its pairs unjudged, not the run failed
            say(f"pairs {start + 1}-{start + len(batch)}: the model failed ({type(e).__name__}: {str(e)[:200]})")
            reply = None
        got = {}
        for d in (reply or {}).get("judgements") or []:
            if isinstance(d, dict) and isinstance(d.get("pair"), int) and isinstance(d.get("same"), bool):
                conf = d.get("confidence") if _num(d.get("confidence")) else 0
                got[d["pair"]] = store.clean(
                    {
                        "same": d["same"],
                        "confidence": round(min(1.0, max(0.0, float(conf))), 2),
                        "keep": d.get("keep") if d.get("keep") in ("a", "b") else None,
                        "why": str(d.get("why") or "")[:300] or None,
                    }
                )
        for k, it in enumerate(batch, 1):
            out.append({**it, **({"verdict": got[k]} if k in got else {})})
        say(f"judged {len(got)} of {len(batch)} pairs")
    return out


# ---------- changes ----------
def _alive(db, eid):
    return bool(db.one("SELECT id FROM $r", r=R("entity", int(eid))))


def _keep(item):
    v = item.get("verdict") or {}
    if v.get("keep") in ("a", "b"):
        return item[v["keep"]]["id"]
    return item["a"]["id"] if item["a"]["mentions"] >= item["b"]["mentions"] else item["b"]["id"]


def _make(db, ch, user):
    """Make one change; the fields to record on it (merge id), or None when it can't be made any more."""
    a, b = ch["a"]["id"], ch["b"]["id"]
    if not (_alive(db, a) and _alive(db, b)):
        return None
    if ch["kind"] == "merge":
        keep = ch.get("keep") or a
        return {"merge": entities.merge(db, keep, [b if keep == a else a], user=user), "keep": keep}
    entities.link(db, a, b, check_spaces=False)
    return {}


def _record(db, item, status, origin, extra=None):
    cid = db.next_id("graph_change")  # a new id also tells the graph cache to rebuild
    row = store.clean(
        {
            "kind": item["kind"],
            "pair": item["pair"],
            "a": item["a"],
            "b": item["b"],
            "spaces": sorted({item["a"]["space"], item["b"]["space"]}),
            "reason": item.get("reason"),
            "confidence": item.get("confidence"),
            "verdict": item.get("verdict"),
            "keep": _keep(item) if item["kind"] == "merge" else None,
            "status": status,
            "created_at": store.now(),
            **(origin or {}),
            **(extra or {}),
        }
    )
    db.q("CREATE $r CONTENT $d", r=R("graph_change", cid), d=row)
    return cid


def apply_changes(db, items, c, origin=None, propose_only=False, say=print):
    """Make the sure ones (up to max_apply), propose the rest: {applied, proposed, skipped}."""
    above, cap = c.get("apply_above"), c.get("max_apply", 50)
    stats = {"applied": 0, "proposed": 0, "skipped": 0}
    user = f"routine:{origin['routine']}" if origin and origin.get("routine") else "workflow"
    for it in items if isinstance(items, list) else []:
        if not (isinstance(it, dict) and it.get("kind") in KINDS and isinstance(it.get("a"), dict) and isinstance(it.get("b"), dict)):
            stats["skipped"] += 1
            continue
        v = it.get("verdict") or {}
        if v.get("same") is False:
            stats["skipped"] += 1
            continue
        sure = v.get("confidence", it.get("confidence") or 0)
        if not propose_only and above is not None and sure >= above and stats["applied"] < cap:
            done = _make(db, {**it, "keep": _keep(it) if it["kind"] == "merge" else None}, user)
            if done is None:
                stats["skipped"] += 1
                continue
            _record(db, it, "applied", origin, {**done, "decided_at": store.now(), "decided_by": user})
            stats["applied"] += 1
        else:
            if db.one("SELECT id FROM graph_change WHERE pair = $p AND status = 'proposed' LIMIT 1", p=it["pair"]):
                stats["skipped"] += 1
                continue
            _record(db, it, "proposed", origin)
            stats["proposed"] += 1
    say(f"{stats['applied']} applied, {stats['proposed']} proposed, {stats['skipped']} skipped")
    return stats


def get_change(db, cid):
    ch = db.one("SELECT *, record::id(id) AS id FROM $r", r=R("graph_change", int(cid)))
    if not ch:
        raise KeyError(cid)
    return ch


def accept(db, cid, user=None, keep=None):
    """Make a proposed change."""
    ch = get_change(db, cid)
    if ch["status"] != "proposed":
        raise ValueError(f"this change is {ch['status']}, not proposed")
    if keep is not None:
        if int(keep) not in (ch["a"]["id"], ch["b"]["id"]):
            raise ValueError("keep one of the two entities")
        ch["keep"] = int(keep)
    done = _make(db, ch, user)
    if done is None:
        db.q(
            "UPDATE $r SET status = 'dismissed', decided_at = $t, decided_by = $u, note = 'an entity is gone'",
            r=R("graph_change", ch["id"]),
            t=store.now(),
            u=user,
        )
        raise ValueError("one of these entities is gone (merged or deleted since)")
    db.q(
        "UPDATE $r MERGE $d",
        r=R("graph_change", ch["id"]),
        d=store.clean({"status": "applied", "decided_at": store.now(), "decided_by": user, **done}),
    )


def dismiss(db, cid, user=None):
    """Say a proposed pair is two things, so it isn't suggested again."""
    ch = get_change(db, cid)
    if ch["status"] != "proposed":
        raise ValueError(f"this change is {ch['status']}, not proposed")
    if _alive(db, ch["a"]["id"]) and _alive(db, ch["b"]["id"]):
        entities.not_same(db, ch["a"]["id"], ch["b"]["id"])
    db.q("UPDATE $r SET status = 'dismissed', decided_at = $t, decided_by = $u", r=R("graph_change", ch["id"]), t=store.now(), u=user)


def undo(db, cid, user=None):
    """Take back an applied change (a merge is undone as the entity page would)."""
    ch = get_change(db, cid)
    if ch["status"] != "applied":
        raise ValueError(f"this change is {ch['status']}, not applied")
    if ch["kind"] == "merge":
        m = db.one("SELECT undone FROM $r", r=R("entity_merge", int(ch["merge"])))
        if m and not m.get("undone"):  # someone may have undone it from the entity page already
            entities.undo_merge(db, ch["merge"])
    else:
        entities.unlink(db, ch["a"]["id"], ch["b"]["id"])
    db.q("UPDATE $r SET status = 'undone', undone_at = $t, undone_by = $u", r=R("graph_change", ch["id"]), t=store.now(), u=user)


def undo_run(db, run_id, user=None, say=None):
    """Take back every change a routine run applied, newest first: (undone, failed). One that can't be undone (an
    entity in it was deleted since) is left as it is and counted."""
    rows = db.rows("SELECT record::id(id) AS id FROM graph_change WHERE run = $r AND status = 'applied' ORDER BY id DESC", r=int(run_id))
    done = failed = 0
    for r in rows:
        try:
            undo(db, r["id"], user)
            done += 1
        except Exception as e:  # noqa: BLE001 - the rest still get undone
            failed += 1
            if say:
                say(f"change {r['id']}: {type(e).__name__}: {e}")
    return done, failed


def list_changes(db, spaces, status=None, run=None, limit=200):
    where, args = ["spaces ANYINSIDE $s"], {"s": sorted(spaces)}
    if status:
        where.append("status = $st")
        args["st"] = status
    if run is not None:
        where.append("run = $rn")
        args["rn"] = int(run)
    rows = db.rows(
        "SELECT *, record::id(id) AS id FROM graph_change WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT $n",
        n=int(limit),
        **args,
    )
    return [r for r in rows if set(r.get("spaces") or []) <= set(spaces)]  # all of a change's namespaces must be yours


# ---------- running a graph workflow ----------
def run(db, cfg, wid, spaces, version=None, say=print, origin=None, propose_only=False):
    """Run one version (default: the current one) of a graph workflow over namespaces `spaces`. Returns
    ({node id: done | skipped}, {applied, proposed, skipped})."""
    from . import flow, workflows

    w = workflows.get(db, wid, version)
    if w.get("scope") != "graph":
        raise ValueError(f"workflow {w['name']} runs on recordings, not on the graph")
    names = store.space_names(db)
    ctx = {"namespaces": [{"id": s, "name": names.get(s)} for s in sorted(spaces)]}
    origin = {**(origin or {}), "workflow": w["id"], "workflow_version": w["version"]}
    r = flow.Run(db, cfg, workflows.KITS["graph"], ctx, say, origin=origin, spaces=set(spaces), propose_only=propose_only)
    r.totals = {"applied": 0, "proposed": 0, "skipped": 0}
    flow.run_graph(r, w["graph"])
    done = {n["id"]: "done" if r.trace.get(n["id"], {}).get("status") == "done" else "skipped" for n in w["graph"]["nodes"]}
    return done, r.totals


def run_node(r, f, node, value, vals):
    """What a graph workflow's own nodes do (flow.py runs the rest): {output port: value}."""
    t, c, name = node["type"], node["config"], node.get("label") or f"{node['type']} {node['id']}"
    if t == "candidates":
        out = candidates(r.db, r.extra["spaces"], c)
        r.say(f"{name}: {len(out)} pairs")
        return {"out": out}
    if t == "llm_judge":
        return {"out": judge(r.cfg, value if isinstance(value, list) else [], c, r.say)}
    if t == "apply_changes":
        if r.dry:
            r.say(f"{name}: would make or propose {len(value) if isinstance(value, list) else 0} changes")
            return {}
        got = apply_changes(r.db, value, c, r.origin, r.extra.get("propose_only"), r.say)
        r.totals = {k: r.totals.get(k, 0) + got[k] for k in ("applied", "proposed", "skipped")}
    return {}


# ---------- the built-in workflow ----------
DEFAULT_NAME = "Organise the entity graph"
DEFAULT_GRAPH = {
    "nodes": [
        {"id": "in", "type": "input", "config": {}, "x": 0, "y": 120},
        {
            "id": "same_ns",
            "type": "candidates",
            "config": {"kind": "merge", "min_confidence": 0.7, "limit": 100},
            "label": "Look-alikes in a namespace",
            "x": 240,
            "y": 40,
        },
        {
            "id": "across",
            "type": "candidates",
            "config": {"kind": "link", "min_confidence": 0.85, "limit": 50},
            "label": "Look-alikes across namespaces",
            "x": 240,
            "y": 200,
        },
        {"id": "all", "type": "merge", "config": {}, "x": 480, "y": 120},
        {"id": "judge", "type": "llm_judge", "config": {"batch": 25}, "label": "Ask the model", "x": 680, "y": 120},
        {
            "id": "same",
            "type": "filter",
            "config": {"path": "verdict.same", "op": "equals", "value": True},
            "label": "Same thing",
            "x": 900,
            "y": 120,
        },
        {
            "id": "apply",
            "type": "apply_changes",
            "config": {"apply_above": 0.95, "max_apply": 25},
            "label": "Merge the sure ones, propose the rest",
            "x": 1120,
            "y": 120,
        },
    ],
    "edges": [
        {"source": "in", "target": "same_ns"},
        {"source": "in", "target": "across"},
        {"source": "same_ns", "target": "all"},
        {"source": "across", "target": "all"},
        {"source": "all", "target": "judge"},
        {"source": "judge", "target": "same"},
        {"source": "same", "target": "apply"},
    ],
}
DEFAULT_DESCRIPTION = (
    "Finds entities that look alike, in each namespace and across shared ones, asks the model whether each pair is "
    "one thing, merges or links the pairs it is sure of (95% or more, 25 a run) and proposes the rest. Every change "
    "can be undone."
)
