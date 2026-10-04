"""Custom nodes: a body of nodes saved under a name, used in workflows like any other node (flow.py runs them).

A custom node is made from primitives and other nodes: one node with its settings preset (a primitive extended), or
several joined. Its body starts from `arg` nodes (its input ports) and ends in `return` nodes (its output ports).
Its parameters are settings people fill in where they use it: anywhere in the body, a setting written as
{"$param": name} takes that parameter's value (or its default).

Custom nodes are versioned like workflows; a workflow keeps to the version it was saved with until someone moves it
on. Each has an owner and is seen by its owner only (`private`), by the members of chosen namespaces (`namespace`),
or by everyone signed in (`everyone`); admins see all of them. Only its owner or an admin changes it. Removing one
hides it from the palette; workflows that use it keep running the version they pinned.
"""

from __future__ import annotations

import re

from . import auth, flow, store

R = store.R
VISIBILITY = ("private", "namespace", "everyone")
PARAM_KINDS = ("text", "number", "bool", "json", "choice")
TONES = ("blue", "green", "gold", "red", "purple", "neutral")
ICON_RX = re.compile(r"^[a-z0-9-]{1,40}$")
MAX_PARAMS = 20


def who(user_id, email, admin, roles):
    """Who is asking, as this module needs it."""
    return {"id": user_id, "email": email, "admin": bool(admin), "roles": dict(roles or {})}


def check_value(p, v, where):
    k = p["kind"]
    ok = (
        (k == "text" and isinstance(v, str))
        or (k == "number" and flow._num(v))
        or (k == "bool" and isinstance(v, bool))
        or (k == "choice" and v in (p.get("options") or []))
        or k == "json"
    )
    if not ok:
        raise ValueError(f"{where}: {p['name']} is {'one of ' + ', '.join(map(str, p.get('options') or [])) if k == 'choice' else k}")


def _check_params(params):
    if not isinstance(params, list) or len(params) > MAX_PARAMS:
        raise ValueError(f"parameters are a list of at most {MAX_PARAMS}")
    out, names = [], set()
    for p in params:
        p = dict(p or {})
        if set(p) - {"name", "label", "kind", "default", "options", "help"}:
            raise ValueError("a parameter is {name, label, kind, default, options, help}")
        if not flow.NAME_RX.match(str(p.get("name") or "")) or p["name"] in names:
            raise ValueError("name each parameter differently, with lowercase letters, digits and _")
        names.add(p["name"])
        if p.get("kind", "text") not in PARAM_KINDS:
            raise ValueError(f"parameter {p['name']}: its kind is one of {', '.join(PARAM_KINDS)}")
        p["kind"] = p.get("kind", "text")
        if p["kind"] == "choice":
            opts = p.get("options")
            if not isinstance(opts, list) or not opts or not all(isinstance(o, (str, int, float)) for o in opts):
                raise ValueError(f"parameter {p['name']}: list its choices")
        else:
            p.pop("options", None)
        if "default" not in p:
            raise ValueError(f"parameter {p['name']}: give it a default")
        check_value(p, p["default"], f"parameter {p['name']}")
        for k in ("label", "help"):
            if p.get(k) is not None:
                p[k] = str(p[k]).strip()[:120] or None
        out.append(store.clean(p) | {"default": p["default"]})
    return out


def _types(db, graph, seen=None):
    """(the node types a body uses, all the way down; the scopes of the custom nodes in it)."""
    types, scopes = set(), []
    for n in graph.get("nodes") or []:
        t, c = n.get("type"), n.get("config") or {}
        types.add(t)
        if t in flow.BODIES and isinstance(c.get("body"), dict):
            more, sc = _types(db, c["body"])
            types |= more
            scopes += sc
        if t == "custom":
            try:
                scopes.append(set(get(db, int(c.get("node") or 0), c.get("version"))["scopes"]))
            except (KeyError, TypeError, ValueError):
                pass
    return types, scopes


def _check_body(db, graph, params, self_id=None):
    """(the body cleaned, its params, ports, scopes and whether it keeps something): ValueError naming a problem."""
    from . import workflows

    params = _check_params(params or [])
    if self_id is not None and _uses(graph, self_id):
        raise ValueError("a custom node can't use itself")
    types, inner = _types(db, graph or {})
    scopes = [s for s, kit in workflows.KITS.items() if types <= set(kit.all_types) and all(s in sc for sc in inner)]
    if not scopes:
        raise ValueError("a custom node's nodes all work on recordings, or all on the graph, not both")
    filled = {p["name"]: p["default"] for p in params}
    kit = workflows.KITS[scopes[0]]
    flow.check_graph(db, flow.substitute(graph, filled), kit, "custom")
    for s in scopes[1:]:
        try:
            flow.check_graph(db, flow.substitute(graph, filled), workflows.KITS[s], "custom")
        except ValueError:
            scopes.remove(s)
    clean = flow.check_graph(db, graph, kit, "custom") if not params else _keep_refs(db, graph, kit, filled)
    ins, outs = flow.body_ports(clean)
    if not ins:
        raise ValueError("a custom node needs an arg node, for what comes in")
    if len(ins) > 10 or len(outs) > 10:
        raise ValueError("a custom node has at most 10 inputs and 10 outputs")
    keeps = flow.keeps(db, flow.substitute(clean, filled)["nodes"], kit)
    return clean, params, ins, outs, scopes, keeps


def _keep_refs(db, graph, kit, filled):
    """The body cleaned with its {"$param": name} settings kept as they are (they were checked filled in)."""
    clean = flow.check_graph(db, flow.substitute(graph, filled), kit, "custom")
    refs = {n.get("id"): n.get("config") or {} for n in graph.get("nodes") or []}

    def back(cleaned, raw):
        if isinstance(raw, dict) and set(raw) == {"$param"}:
            return raw
        if isinstance(cleaned, dict) and isinstance(raw, dict):
            return {k: back(v, raw.get(k)) for k, v in cleaned.items()}
        if isinstance(cleaned, list) and isinstance(raw, list) and len(cleaned) == len(raw):
            return [back(a, b) for a, b in zip(cleaned, raw)]
        return cleaned

    for n in clean["nodes"]:
        n["config"] = back(n["config"], refs.get(n["id"]))
    return clean


def _uses(graph, nid):
    for n in (graph or {}).get("nodes") or []:
        c = n.get("config") or {}
        if n.get("type") == "custom" and str(c.get("node")) == str(nid):
            return True
        if n.get("type") in flow.BODIES and isinstance(c.get("body"), dict) and _uses(c["body"], nid):
            return True
    return False


def _check_meta(db, me, meta):
    out = {}
    if "name" in meta:
        if not (meta["name"] or "").strip():
            raise ValueError("give the custom node a name")
        out["name"] = meta["name"].strip()[:60]
    if "description" in meta:
        out["description"] = (meta["description"] or "").strip()[:500] or None
    if meta.get("icon") is not None and not ICON_RX.match(meta["icon"]):
        raise ValueError("an icon is a name like sparkles or git-branch")
    if meta.get("color") is not None and meta["color"] not in TONES:
        raise ValueError(f"the colour is one of {', '.join(TONES)}")
    for k in ("icon", "color"):
        if k in meta:
            out[k] = meta[k]
    if "visibility" in meta or "namespaces" in meta:
        vis = meta.get("visibility") or "private"
        if vis not in VISIBILITY:
            raise ValueError(f"who sees it is one of {', '.join(VISIBILITY)}")
        sids = []
        if vis == "namespace":
            for name in meta.get("namespaces") or []:
                try:
                    sid = store.ns_id(db, name, create=False)
                except KeyError:
                    raise ValueError(f"no namespace {name}") from None
                if not me["admin"] and not auth.allows(me["roles"], sid, "editor"):
                    raise ValueError(f"sharing with {name} needs editor access there")
                sids.append(sid)
            if not sids:
                raise ValueError("choose the namespaces to share it with")
        out["visibility"], out["namespaces"] = vis, sorted(set(sids))
    return out


def create(db, me, name, graph, params=None, description=None, icon=None, color=None, visibility="private", namespaces=None):
    meta = _check_meta(
        db, me, {"name": name, "description": description, "icon": icon, "color": color, "visibility": visibility, "namespaces": namespaces}
    )
    clean, params, ins, outs, scopes, keeps = _check_body(db, graph, params)
    nid, t = db.next_id("custom_node"), store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("custom_node", nid),
        d=store.clean({**meta, "owner": me["id"], "owner_email": me["email"], "current": 1, "created_at": t, "updated_at": t}),
    )
    _save(db, me, nid, 1, clean, params, ins, outs, scopes, keeps, None)
    return nid


def _save(db, me, nid, n, clean, params, ins, outs, scopes, keeps, notes):
    db.q(
        "CREATE $r CONTENT $d",
        r=R("custom_node_version", f"{nid}-{n}"),
        d=store.clean(
            {
                "node": nid,
                "version": n,
                "graph": clean,
                "params": params,
                "inputs": ins,
                "outputs": outs,
                "scopes": scopes,
                "keeps": keeps,
                "notes": notes,
                "created_at": store.now(),
                "created_by": me["email"],
            }
        ),
    )


def _row(db, nid):
    d = db.one(
        "SELECT record::id(id) AS id, name, description, icon, color, visibility, namespaces, owner, owner_email, current, "
        "created_at, updated_at, deleted_at FROM $r",
        r=R("custom_node", nid),
    )
    if not d:
        raise KeyError(nid)
    return d


def can_see(d, me):
    if me["admin"] or d.get("owner") == me["id"] or d.get("visibility") == "everyone":
        return True
    return d.get("visibility") == "namespace" and bool(set(d.get("namespaces") or []) & set(me["roles"]))


def can_edit(d, me):
    return me["admin"] or d.get("owner") == me["id"]


def _mine(db, me, nid):
    d = _row(db, nid)
    if d.get("deleted_at") or not can_see(d, me):
        raise KeyError(nid)
    if not can_edit(d, me):
        raise PermissionError("only its owner or an admin changes a custom node")
    return d


def save_version(db, me, nid, graph, params=None, notes=None):
    _mine(db, me, nid)
    clean, params, ins, outs, scopes, keeps = _check_body(db, graph, params, nid)
    n = max(db.values("SELECT VALUE version FROM custom_node_version WHERE node = $n", n=nid) or [0]) + 1
    _save(db, me, nid, n, clean, params, ins, outs, scopes, keeps, notes)
    db.q("UPDATE $r SET current = $n, updated_at = $t", r=R("custom_node", nid), n=n, t=store.now())
    return n


def update(db, me, nid, **meta):
    d = _mine(db, me, nid)
    meta = {k: v for k, v in meta.items() if v is not None or k in ("description", "icon", "color")}
    if "namespaces" in meta and "visibility" not in meta:
        meta["visibility"] = d.get("visibility") or "private"
    out = _check_meta(db, me, meta)
    if out:
        db.q("UPDATE $r MERGE $d", r=R("custom_node", nid), d={**out, "updated_at": store.now()})


def remove(db, me, nid):
    _mine(db, me, nid)
    db.q("UPDATE $r SET deleted_at = $t", r=R("custom_node", nid), t=store.now())


def get(db, nid, version=None):
    """A custom node at one version (default: the current one), removed ones too (pinned workflows still run them)."""
    d = _row(db, nid)
    v = db.one(
        "SELECT version, graph, params, inputs, outputs, scopes, keeps, notes, created_at, created_by FROM $r",
        r=R("custom_node_version", f"{nid}-{version or d['current']}"),
    )
    if not v:
        raise KeyError(f"{nid} v{version}")
    return {**d, **v, "params": v.get("params") or [], "keeps": bool(v.get("keeps"))}


def history(db, nid):
    return db.rows("SELECT version, notes, created_at, created_by FROM custom_node_version WHERE node = $n ORDER BY version DESC", n=nid)


def visible(db, me, scope=None):
    """The custom nodes this person can use (current versions), named namespaces and whether they may change each."""
    names = store.space_names(db)
    out = []
    for d in db.rows("SELECT record::id(id) AS id FROM custom_node WHERE deleted_at = NONE ORDER BY id"):
        g = get(db, d["id"])
        if not can_see(g, me) or (scope and scope not in g["scopes"]):
            continue
        g["namespaces"] = [names.get(s) for s in g.get("namespaces") or [] if names.get(s)]
        g["editable"] = can_edit(g, me)
        out.append(g)
    return out


def visible_one(db, me, nid, version=None):
    g = get(db, nid, version)
    if not can_see(g, me):
        raise KeyError(nid)
    names = store.space_names(db)
    g["namespaces"] = [names.get(s) for s in g.get("namespaces") or [] if names.get(s)]
    g["editable"] = can_edit(g, me) and not g.get("deleted_at")
    g["history"] = history(db, nid)
    return g
