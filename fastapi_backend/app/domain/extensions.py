"""Extensions: what people add to the assistant. Tools it can call, skills it can follow, hooks that run on what happens in
a conversation, and plugins that bundle them.

Every extension is one versioned artifact, however it was made: a manifest written as code (Markdown with frontmatter,
or YAML: `parse_manifest`, `to_manifest`), a graph drawn on the canvas, or a draft the assistant wrote in chat. It has
an owner and is seen by its owner only (`private`), by the members of chosen namespaces (`namespace`), or by everyone
signed in (`everyone`), as custom nodes are; only its owner or an admin changes it, and switching it off or removing it
takes it out of the assistant at once.

- A tool has parameters, an effect and a body. A `read` tool answers at once; a `change` tool waits for the person's
  approval, like the built-in tools that change data (a setup conversation, which acts, runs it at once). Bodies:
  `prompt` (the model, with the parameters written into a template) and `http` (a web request, to public addresses
  only, unless an admin allowed a private network for web pages), `graph` (drawn on the canvas: tool_nodes.py) and
  `python` (code with a `run(**args)` function, in a process of its own: code_tools.py). Only admins write Python
  tools, and one runs only while an admin owns its extension.
- A skill is instructions with a line saying when to use them. The assistant sees each skill's name and that line, and
  reads the instructions (`use_skill`) only when the skill applies.
- A hook runs on an event: `message` (a question arrives), `before_tool`, `after_tool`, `answer` (an answer was
  written). It adds context for the model, blocks a tool (before_tool), or calls a tool.
- A plugin is a list of tools, skills and hooks that's installed, shared, versioned and switched off as one.

Nothing here is on until someone makes an extension, so the assistant works as before.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import activity, auth, store

R = store.R
KINDS = ("tool", "skill", "hook", "plugin")
VISIBILITY = ("private", "namespace", "everyone")
NAME_RX = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
PARAM_RX = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
PARAM_KINDS = {"text": "string", "number": "number", "integer": "integer", "bool": "boolean", "json": "object", "list": "array"}
EFFECTS = ("read", "change")
RUNS = ("prompt", "http", "graph", "python")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
EVENTS = ("message", "before_tool", "after_tool", "answer")
ACTIONS = {
    "message": ("context", "tool"),
    "before_tool": ("context", "block", "tool"),
    "after_tool": ("context", "tool"),
    "answer": ("tool",),
}
HOOK_VARS = {"message": {"said"}, "before_tool": {"said", "tool"}, "after_tool": {"said", "tool", "result"}, "answer": {"said", "answer"}}
VAR_RX = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
MAX_TEXT = 20000
MAX_PARAMS = 20
MAX_ITEMS = 50
MAX_REPLY = 200_000  # the most of a web reply that is read
RESULT_CHARS = 8000  # the most of a tool's result the model reads


def who(user_id, email, admin, roles):
    """Who is asking, as this module needs it."""
    return {"id": user_id, "email": email, "admin": bool(admin), "roles": dict(roles or {})}


# ---------- checking ----------
def _text(v, what, limit=500, required=False):
    v = (v if isinstance(v, str) else "" if v is None else str(v)).strip()
    if required and not v:
        raise ValueError(f"give {what}")
    if len(v) > limit:
        raise ValueError(f"{what} is at most {limit} characters")
    return v or None


def _vars(template):
    return set(VAR_RX.findall(template or ""))


def _check_params(params):
    if params is None:
        return []
    if not isinstance(params, list) or len(params) > MAX_PARAMS:
        raise ValueError(f"a tool's parameters are a list of at most {MAX_PARAMS}")
    out, names = [], set()
    for p in params:
        if not isinstance(p, dict) or set(p) - {"name", "kind", "description", "required", "options", "default"}:
            raise ValueError("a parameter is {name, kind, description, required, options, default}")
        name = str(p.get("name") or "")
        if not PARAM_RX.match(name) or name in names:
            raise ValueError("name each parameter differently, with lowercase letters, digits and _")
        names.add(name)
        kind = p.get("kind") or "text"
        if kind not in PARAM_KINDS:
            raise ValueError(f"parameter {name}: its kind is one of {', '.join(PARAM_KINDS)}")
        q = {"name": name, "kind": kind, "description": _text(p.get("description"), f"parameter {name}'s description", 300)}
        if p.get("options") is not None:
            opts = p["options"]
            if kind != "text" or not isinstance(opts, list) or not opts or not all(isinstance(o, str) for o in opts):
                raise ValueError(f"parameter {name}: options are a list of words, for a text parameter")
            q["options"] = opts[:50]
        if p.get("required"):
            q["required"] = True
        if "default" in p:
            q["default"] = p["default"]
        out.append(store.clean(q))
    return out


def _check_run(run, params, me, db=None):
    if not isinstance(run, dict) or run.get("type") not in RUNS:
        raise ValueError(f"a tool runs one of: {', '.join(RUNS)}")
    known = {p["name"] for p in params}
    t = run["type"]
    if t == "graph":  # drawn on the canvas (tool_nodes.py)
        from . import tool_nodes

        if set(run) - {"type", "graph"}:
            raise ValueError("a canvas tool is {type, graph}")
        if db is None:
            raise ValueError("a canvas tool is checked when it's saved")
        return {"type": t, "graph": tool_nodes.check(db, run.get("graph") or {}, params)}
    if t == "python":  # code, run apart from the server (code_tools.py)
        from . import code_tools

        if set(run) - {"type", "code", "seconds", "network"}:
            raise ValueError("a Python tool is {type, code, seconds, network}")
        if not me["admin"]:
            raise ValueError("only admins write Python tools; draw it on the canvas or use a prompt or web tool instead")
        code = _text(run.get("code"), "the tool's code", MAX_TEXT * 5, required=True)
        try:
            code_tools.check(code)
        except code_tools.CodeError as e:
            raise ValueError(str(e)) from None
        seconds = run.get("seconds", 20)
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= code_tools.MAX_SECONDS:
            raise ValueError(f"seconds is a whole number from 1 to {code_tools.MAX_SECONDS}")
        if not isinstance(run.get("network", False), bool):
            raise ValueError("network is true or false")
        return {"type": t, "code": code, "seconds": seconds, "network": bool(run.get("network"))}
    if t == "prompt":
        if set(run) - {"type", "system", "prompt"}:
            raise ValueError("a prompt tool is {type, system, prompt}")
        prompt = _text(run.get("prompt"), "the tool's prompt", MAX_TEXT, required=True)
        system = _text(run.get("system"), "the tool's system prompt", MAX_TEXT)
        used = _vars(prompt) | _vars(system)
        out = {"type": t, "prompt": prompt, "system": system}
    else:
        if set(run) - {"type", "method", "url", "headers", "body"}:
            raise ValueError("a web tool is {type, method, url, headers, body}")
        method = str(run.get("method") or "GET").upper()
        if method not in METHODS:
            raise ValueError(f"the method is one of {', '.join(METHODS)}")
        url = _text(run.get("url"), "the tool's web address", 2000, required=True)
        if not re.match(r"^https?://[^/?#{}\s]+(?:[/?#]|$)", url):
            raise ValueError("the web address starts with http:// or https:// and a host (parameters go after it)")
        headers = run.get("headers") or {}
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise ValueError("headers are names and text values")
        body = run.get("body")
        if body is not None and not isinstance(body, (str, dict, list)):
            raise ValueError("the body is text or JSON")
        used = _vars(url) | {x for v in headers.values() for x in _vars(v)} | _vars(json.dumps(body) if body is not None else "")
        out = {"type": t, "method": method, "url": url, "headers": headers or None, "body": body}
    unknown = used - known
    if unknown:
        raise ValueError(f"the tool uses {', '.join('{{' + u + '}}' for u in sorted(unknown))}, which aren't its parameters")
    return store.clean(out)


def _check_tool(spec, me, db=None):
    if set(spec) - {"params", "effect", "run"}:
        raise ValueError("a tool is {params, effect, run}")
    params = _check_params(spec.get("params"))
    effect = spec.get("effect") or "read"
    if effect not in EFFECTS:
        raise ValueError("a tool's effect is read (answers at once) or change (asks first)")
    return {"params": params, "effect": effect, "run": _check_run(spec.get("run"), params, me, db)}


def _check_skill(spec, me, db=None):
    if set(spec) - {"when", "instructions", "tools"}:
        raise ValueError("a skill is {when, instructions, tools}")
    tools = spec.get("tools") or []
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ValueError("a skill's tools are a list of tool names")
    return store.clean(
        {
            "when": _text(spec.get("when"), "when to use the skill", 500, required=True),
            "instructions": _text(spec.get("instructions"), "the skill's instructions", MAX_TEXT, required=True),
            "tools": tools[:30] or None,
        }
    )


def _check_hook(spec, me, db=None):
    if set(spec) - {"event", "match", "action"}:
        raise ValueError("a hook is {event, match, action}")
    event = spec.get("event")
    if event not in EVENTS:
        raise ValueError(f"a hook runs on one of: {', '.join(EVENTS)}")
    match = spec.get("match") or {}
    if not isinstance(match, dict) or set(match) - {"tool", "contains"}:
        raise ValueError("a hook's match is {tool, contains}")
    if match.get("tool") and event not in ("before_tool", "after_tool"):
        raise ValueError("only before_tool and after_tool hooks match a tool")
    action = spec.get("action") or {}
    kind = action.get("type") if isinstance(action, dict) else None
    if kind not in ACTIONS[event]:
        raise ValueError(f"a {event} hook can: {', '.join(ACTIONS[event])}")
    if kind == "context":
        if set(action) - {"type", "text"}:
            raise ValueError("a context action is {type, text}")
        text = _text(action.get("text"), "the context to add", MAX_TEXT, required=True)
        out = {"type": kind, "text": text}
        used = _vars(text)
    elif kind == "block":
        if set(action) - {"type", "reason"}:
            raise ValueError("a block action is {type, reason}")
        out = {"type": kind, "reason": _text(action.get("reason"), "why the tool is blocked", 500, required=True)}
        used = _vars(out["reason"])
    else:
        if set(action) - {"type", "tool", "args"}:
            raise ValueError("a tool action is {type, tool, args}")
        tool = str(action.get("tool") or "")
        if not NAME_RX.match(tool):
            raise ValueError("name the tool the hook calls")
        args = action.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError("a hook's tool arguments are an object")
        out = {"type": kind, "tool": tool, "args": args}
        used = _vars(json.dumps(args))
    unknown = used - HOOK_VARS[event]
    if unknown:
        raise ValueError(f"a {event} hook can use {', '.join('{{' + v + '}}' for v in sorted(HOOK_VARS[event]))}")
    return store.clean(
        {
            "event": event,
            "match": store.clean({"tool": match.get("tool"), "contains": _text(match.get("contains"), "the text to match", 200)}) or None,
            "action": out,
        }
    )


def _check_plugin(spec, me, db=None):
    if set(spec) - {"items"}:
        raise ValueError("a plugin is {items}")
    items = spec.get("items")
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise ValueError(f"a plugin has 1 to {MAX_ITEMS} items: tools, skills and hooks")
    out, names = [], set()
    for it in items:
        if not isinstance(it, dict) or it.get("kind") not in ("tool", "skill", "hook"):
            raise ValueError("a plugin's items are tools, skills and hooks, each {kind, name, description, ...}")
        m = check_manifest({**it}, me, db)
        if m["name"] in names:
            raise ValueError(f"the plugin has two items called {m['name']}")
        names.add(m["name"])
        out.append({k: m[k] for k in ("kind", "name", "description", "spec") if m.get(k) is not None})
    return {"items": out}


CHECKS = {"tool": _check_tool, "skill": _check_skill, "hook": _check_hook, "plugin": _check_plugin}


def check_spec(kind, spec, me, db=None):
    if kind not in KINDS:
        raise ValueError(f"an extension is one of: {', '.join(KINDS)}")
    if not isinstance(spec, dict):
        raise ValueError(f"a {kind}'s settings are an object")
    return CHECKS[kind](spec, me, db)


def check_manifest(m, me, db=None):
    """A manifest ({name, kind, description, spec}, or the spec's keys at the top level), checked and tidied."""
    if not isinstance(m, dict):
        raise ValueError("a manifest is an object: name, kind, description and the extension's settings")
    m = dict(m)
    head = {k: m.pop(k, None) for k in ("name", "kind", "description", "title", "visibility", "namespaces", "version")}
    spec = m.pop("spec", None)
    if spec is None:
        spec = m
    elif m:
        raise ValueError(f"unknown keys next to spec: {', '.join(sorted(m))}")
    name = str(head["name"] or "")
    if not NAME_RX.match(name):
        raise ValueError("an extension's name is 2 to 41 lowercase letters, digits and _, starting with a letter")
    return store.clean(
        {
            "name": name,
            "kind": head["kind"],
            "title": _text(head["title"], "the title", 80),
            "description": _text(head["description"], "the description", 1000, required=head["kind"] != "hook"),
            "visibility": head["visibility"],
            "namespaces": head["namespaces"],
            "spec": check_spec(head["kind"], spec, me, db),
        }
    )


# ---------- manifests as code ----------
BODY_KEY = {"skill": ("instructions",), "tool": ("run", "prompt")}


def parse_manifest(text):
    """A manifest written as code: YAML or JSON, or Markdown with YAML frontmatter, whose body is a skill's
    instructions or a prompt tool's prompt. ValueError when it can't be read."""
    text = (text or "").lstrip("﻿")
    body = None
    fm = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n(.*))?$", text, re.S)
    try:
        head = yaml.safe_load(fm.group(1) if fm else text)
    except yaml.YAMLError as e:
        raise ValueError(f"the manifest isn't valid YAML: {e}") from None
    if fm:
        body = (fm.group(2) or "").strip() or None
    if not isinstance(head, dict):
        raise ValueError("a manifest starts with its name, kind and description")
    head = json.loads(json.dumps(head, default=str))  # dates and the like YAML reads, as text
    if body is not None:
        path = BODY_KEY.get(head.get("kind"))
        if not path:
            raise ValueError("only a skill or a prompt tool has text after its frontmatter")
        if path[0] == "run":
            run = head.setdefault("run", {"type": "prompt"})
            if not isinstance(run, dict) or run.setdefault("type", "prompt") != "prompt":
                raise ValueError("text after the frontmatter is a prompt tool's prompt")
            run["prompt"] = body
        else:
            head["instructions"] = body
    return head


class _Dumper(yaml.SafeDumper):
    """YAML with text over several lines (a Python tool's code) written as a block, as people write it."""


_Dumper.add_representer(
    str,
    lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None),
)


def to_manifest(ext):
    """An extension as a manifest people can read, change and import again: Markdown with frontmatter for skills and
    prompt tools, YAML for the rest."""
    spec = json.loads(json.dumps(ext["spec"]))
    head = store.clean({"name": ext["name"], "kind": ext["kind"], "title": ext.get("title"), "description": ext.get("description")})
    body = None
    if ext["kind"] == "skill":
        body = spec.pop("instructions")
    elif ext["kind"] == "tool" and spec["run"]["type"] == "prompt":
        body = spec["run"].pop("prompt")
    text = yaml.dump({**head, **spec}, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)
    return f"---\n{text}---\n\n{body}\n" if body is not None else text


# ---------- saving ----------
def _taken(db, names, skip=None):
    """Names other extensions already have, as tools or skills (theirs or their plugins' items)."""
    out = set()
    for d in db.rows("SELECT record::id(id) AS id FROM extension WHERE deleted_at = NONE"):
        if d["id"] == skip:
            continue
        out |= {n for n in provides(get(db, d["id"])) if n in names}
    return out


def provides(ext):
    """The tool and skill names an extension gives the assistant: its own, or its plugin items'."""
    if ext["kind"] == "plugin":
        return {it["name"] for it in ext["spec"]["items"] if it["kind"] != "hook"} | {ext["name"]}
    return {ext["name"]}


def _check_names(db, kind, name, spec, skip=None):
    from . import ai_tools

    names = provides({"kind": kind, "name": name, "spec": spec})
    builtin = names & ai_tools.builtin_names()
    if builtin:
        raise ValueError(f"{', '.join(sorted(builtin))} is one of the assistant's own tools: choose another name")
    taken = _taken(db, names, skip)
    if taken:
        raise ValueError(f"another extension is already called {', '.join(sorted(taken))}")


def _check_share(db, me, visibility, namespaces):
    vis = visibility or "private"
    if vis not in VISIBILITY:
        raise ValueError(f"who sees it is one of {', '.join(VISIBILITY)}")
    if vis == "everyone" and not me["admin"]:
        raise ValueError("only admins share an extension with everyone; share it with your namespaces instead")
    if namespaces is not None and (not isinstance(namespaces, list) or not all(isinstance(n, str) for n in namespaces)):
        raise ValueError("namespaces are a list of names")
    sids = []
    if vis == "namespace":
        for name in namespaces or []:
            try:
                sid = store.ns_id(db, name, create=False)
            except KeyError:
                raise ValueError(f"no namespace {name}") from None
            if not me["admin"] and not auth.allows(me["roles"], sid, "editor"):
                raise ValueError(f"sharing with {name} needs editor access there")
            sids.append(sid)
        if not sids:
            raise ValueError("choose the namespaces to share it with")
    return vis, sorted(set(sids))


def create(db, me, manifest, enabled=True, origin="code"):
    """A new extension from a manifest; its id. `origin` says how it was made: code, canvas or chat."""
    m = check_manifest(manifest, me, db)
    _check_names(db, m["kind"], m["name"], m["spec"])
    vis, sids = _check_share(db, me, m.get("visibility"), m.get("namespaces"))
    eid, t = db.next_id("extension"), store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("extension", eid),
        d=store.clean(
            {
                "name": m["name"],
                "kind": m["kind"],
                "title": m.get("title"),
                "description": m.get("description"),
                "visibility": vis,
                "namespaces": sids,
                "enabled": bool(enabled),
                "owner": me["id"],
                "owner_email": me["email"],
                "current": 1,
                "created_at": t,
                "updated_at": t,
            }
        ),
    )
    _save(db, me, eid, 1, m["spec"], None, origin)
    return eid


def _save(db, me, eid, n, spec, notes, origin):
    db.q(
        "CREATE $r CONTENT $d",
        r=R("extension_version", f"{eid}-{n}"),
        d=store.clean(
            {
                "extension": eid,
                "version": n,
                "spec": spec,
                "notes": notes,
                "origin": origin,
                "created_at": store.now(),
                "created_by": me["email"],
            }
        ),
    )


def _row(db, eid):
    d = db.one(
        "SELECT record::id(id) AS id, name, kind, title, description, visibility, namespaces, enabled, owner, owner_email, "
        "current, created_at, updated_at, deleted_at FROM $r",
        r=R("extension", int(eid)),
    )
    if not d:
        raise KeyError(eid)
    return d


def can_see(d, me):
    if me["admin"] or d.get("owner") == me["id"] or d.get("visibility") == "everyone":
        return True
    return d.get("visibility") == "namespace" and bool(set(d.get("namespaces") or []) & set(me["roles"]))


def can_use(d, me):
    """Whether an extension is in this person's assistant: theirs, shared with everyone, or with one of their
    namespaces. Admins see every extension, but only these run for them."""
    if d.get("owner") == me["id"] or d.get("visibility") == "everyone":
        return True
    return d.get("visibility") == "namespace" and bool(set(d.get("namespaces") or []) & set(me["roles"]))


def can_edit(d, me):
    return me["admin"] or d.get("owner") == me["id"]


def _mine(db, me, eid):
    d = _row(db, eid)
    if d.get("deleted_at") or not can_see(d, me):
        raise KeyError(eid)
    if not can_edit(d, me):
        raise PermissionError("only its owner or an admin changes an extension")
    return d


def save_version(db, me, eid, manifest, notes=None, origin="code"):
    """A new version from a manifest (its name and kind stay); the version's number."""
    d = _mine(db, me, eid)
    m = check_manifest({**manifest, "name": d["name"], "kind": d["kind"]}, me, db)
    _check_names(db, d["kind"], d["name"], m["spec"], skip=d["id"])
    n = max(db.values("SELECT VALUE version FROM extension_version WHERE extension = $e", e=d["id"]) or [0]) + 1
    _save(db, me, d["id"], n, m["spec"], _text(notes, "the notes", 500), origin)
    meta = {k: m[k] for k in ("title", "description") if k in m}
    db.q("UPDATE $r MERGE $d", r=R("extension", d["id"]), d={**meta, "current": n, "updated_at": store.now()})
    return n


def update(db, me, eid, **meta):
    """Its title, description, who sees it, and whether it's on."""
    d = _mine(db, me, eid)
    out = {}
    if "title" in meta:
        out["title"] = _text(meta["title"], "the title", 80)
    if "description" in meta and meta["description"] is not None:
        out["description"] = _text(meta["description"], "the description", 1000)
    if meta.get("enabled") is not None:
        out["enabled"] = bool(meta["enabled"])
    if meta.get("visibility") is not None or meta.get("namespaces") is not None:
        out["visibility"], out["namespaces"] = _check_share(
            db, me, meta.get("visibility") or d.get("visibility"), meta.get("namespaces") if meta.get("namespaces") is not None else []
        )
    if out:
        db.q("UPDATE $r MERGE $d", r=R("extension", d["id"]), d={**out, "updated_at": store.now()})


def remove(db, me, eid):
    _mine(db, me, eid)
    db.q("UPDATE $r SET deleted_at = $t, enabled = false", r=R("extension", int(eid)), t=store.now())


def get(db, eid, version=None):
    """An extension at one version (default: the current one)."""
    d = _row(db, eid)
    v = db.one(
        "SELECT version, spec, notes, origin, created_at, created_by FROM $r",
        r=R("extension_version", f"{d['id']}-{version or d['current']}"),
    )
    if not v:
        raise KeyError(f"{eid} v{version}")
    return {**d, **v, "enabled": bool(d.get("enabled"))}


def history(db, eid):
    return db.rows(
        "SELECT version, notes, origin, created_at, created_by FROM extension_version WHERE extension = $e ORDER BY version DESC",
        e=int(eid),
    )


def _shown(db, g, me, names=None):
    names = names if names is not None else store.space_names(db)
    g["namespaces"] = [names.get(s) for s in g.get("namespaces") or [] if names.get(s)]
    g["editable"] = can_edit(g, me) and not g.get("deleted_at")
    return g


def visible(db, me, kind=None):
    """The extensions this person can see (current versions), newest change first."""
    names, out = store.space_names(db), []
    for d in db.rows("SELECT record::id(id) AS id, updated_at FROM extension WHERE deleted_at = NONE ORDER BY updated_at DESC"):
        g = get(db, d["id"])
        if can_see(g, me) and (not kind or g["kind"] == kind):
            out.append(_shown(db, g, me, names))
    return out


def visible_one(db, me, eid, version=None):
    g = get(db, eid, version)
    if g.get("deleted_at") or not can_see(g, me):
        raise KeyError(eid)
    g = _shown(db, g, me)
    g["history"] = history(db, eid)
    g["manifest"] = to_manifest(g)
    return g


# ---------- in a conversation ----------
def _owner_admin(db, g):
    return bool((db.one("SELECT admin FROM $r", r=R("account", g.get("owner"))) or {}).get("admin"))


class Active:
    """The extensions switched on for one person, plugins opened up: tools and skills by name, hooks by event. Each
    item knows the extension (and version) it comes from."""

    def __init__(self, db, me):
        self.tools, self.skills, self.hooks = {}, {}, {e: [] for e in EVENTS}
        for d in db.rows("SELECT record::id(id) AS id FROM extension WHERE deleted_at = NONE AND enabled = true ORDER BY id"):
            g = get(db, d["id"])
            if not can_use(g, me):
                continue
            # hooks see what's said and what tools give back, so only one's own run, or those an admin shared
            trusted = g.get("owner") == me["id"] or _owner_admin(db, g)
            items = g["spec"]["items"] if g["kind"] == "plugin" else [g]
            for it in items:
                if it["kind"] == "hook" and not trusted:
                    continue
                if it["kind"] == "tool" and it["spec"]["run"]["type"] == "python" and not _owner_admin(db, g):
                    continue  # code runs only while an admin owns it
                item = {
                    "ext": g["id"],
                    "version": g["version"],
                    "name": it["name"],
                    "description": it.get("description"),
                    "spec": it["spec"],
                }
                if it["kind"] == "tool":
                    self.tools.setdefault(it["name"], item)
                elif it["kind"] == "skill":
                    self.skills.setdefault(it["name"], item)
                else:
                    self.hooks[it["spec"]["event"]].append(item)

    def __bool__(self):
        return bool(self.tools or self.skills or any(self.hooks.values()))

    def tool_specs(self, can_change=True):
        out = []
        for name, t in self.tools.items():
            if t["spec"]["effect"] == "change" and not can_change:
                continue
            props, req = {}, []
            for p in t["spec"]["params"]:
                s = {"type": PARAM_KINDS[p["kind"]]}
                if p.get("description"):
                    s["description"] = p["description"]
                if p.get("options"):
                    s["enum"] = p["options"]
                props[p["name"]] = s
                if p.get("required"):
                    req.append(p["name"])
            desc = (t["description"] or name) + (" Needs the person's approval." if t["spec"]["effect"] == "change" else "")
            out.append((name, desc, props, req, t["spec"]["effect"] == "change"))
        if self.skills:
            out.append(
                (
                    "use_skill",
                    "Read a skill's instructions before doing what it's for. Skills: "
                    + "; ".join(f"{n} ({s['spec']['when']})" for n, s in self.skills.items()),
                    {"name": {"type": "string", "enum": list(self.skills)}},
                    ["name"],
                    False,
                )
            )
        return out

    def system_note(self):
        if not self.skills:
            return ""
        lines = "\n".join(f"- {n}: {s['spec']['when']}" for n, s in self.skills.items())
        return "\n\nSkills you can follow (call use_skill with the name before doing what one is for):\n" + lines

    def matching(self, event, tool=None, text=""):
        for h in self.hooks[event]:
            m = h["spec"].get("match") or {}
            if m.get("tool") and m["tool"] != tool:
                continue
            if m.get("contains") and m["contains"].lower() not in (text or "").lower():
                continue
            yield h


def fill(template, values, quote=None):
    """A template with {{name}} replaced by its value (JSON for what isn't text; `quote` applied, e.g. for a URL)."""

    def one(m):
        v = values.get(m.group(1))
        s = "" if v is None else v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        return quote(s) if quote else s

    return VAR_RX.sub(one, template or "")


def fill_json(value, values):
    if isinstance(value, str):
        whole = VAR_RX.fullmatch(value.strip())
        return values.get(whole.group(1)) if whole else fill(value, values)
    if isinstance(value, dict):
        return {k: fill_json(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [fill_json(v, values) for v in value]
    return value


def tool_args(spec, args):
    """A tool's arguments, checked against its parameters, with defaults filled in."""
    out = {}
    params = {p["name"]: p for p in spec["params"]}
    for k in args:
        if k not in params:
            raise ValueError(f"the tool has no parameter {k}")
    for name, p in params.items():
        v = args.get(name, p.get("default"))
        if v is None:
            if p.get("required"):
                raise ValueError(f"the tool needs {name}")
            continue
        if p.get("options") and v not in p["options"]:
            raise ValueError(f"{name} is one of {', '.join(p['options'])}")
        out[name] = v
    return out


def run_tool(db, cfg, spec, args, model=None, toolbox=None):
    """Run a tool's body with checked arguments; what it gave back, for the model to read."""
    from . import llm

    run = spec["run"]
    if run["type"] == "prompt":
        if not llm.configured(cfg):
            raise ValueError("no language model is set up, so a prompt tool can't run")
        msgs = ([{"role": "system", "content": fill(run["system"], args)}] if run.get("system") else []) + [
            {"role": "user", "content": fill(run["prompt"], args)}
        ]
        return {"text": llm.chat(cfg, msgs, model=model)}
    if run["type"] == "graph":
        from . import tool_nodes

        return {"result": tool_nodes.run(db, cfg, run["graph"], args, toolbox)}
    if run["type"] == "python":
        from . import code_tools

        try:
            return code_tools.run(run["code"], args, seconds=run.get("seconds"), network=run.get("network"))
        except code_tools.CodeError as e:
            raise ValueError(str(e)) from None
    return http_call(cfg, run, args)


def http_call(cfg, run, args):
    """A web tool's request, through the same guard as web pages that are captured: public addresses on the web's
    ports only (or networks an admin allowed for web pages)."""
    from . import feeds, netguard, webcapture

    url = fill(run["url"], args, quote=lambda s: urllib.parse.quote(s, safe=""))
    if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(run["url"]).netloc:
        raise ValueError("the tool's arguments can't change where its request goes")
    webcapture.check_url(cfg, url)
    data = None
    headers = {"User-Agent": "Lens assistant tool", **{k: fill(v, args) for k, v in (run.get("headers") or {}).items()}}
    if run.get("body") is not None:
        body = run["body"]
        if isinstance(body, str):
            data = fill(body, args).encode("utf-8")
        else:
            data = json.dumps(fill_json(body, args)).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=run.get("method") or "GET", headers=headers)
    host = urllib.parse.urlsplit(url).hostname
    with (
        activity.call("tool.http", cfg, detail={"host": host, "method": req.get_method()}),
        netguard.Guard(forward=True, networks=webcapture.networks(cfg), max_bytes=MAX_REPLY + 65536) as guard,
    ):
        try:
            with feeds._opener(guard).open(req, timeout=30) as r:
                status, ctype, raw = r.status, r.headers.get("Content-Type", ""), r.read(MAX_REPLY)
        except urllib.error.HTTPError as e:
            return {"status": e.code, "error": str(e.reason)}
        except (urllib.error.URLError, OSError) as e:
            refused = f"; refused: {guard.refused[0]}" if guard.refused else ""
            raise ValueError(f"the request failed: {getattr(e, 'reason', e)}{refused}") from None
    text = raw.decode("utf-8", "replace")
    if "json" in ctype:
        try:
            return {"status": status, "json": json.loads(text)}
        except ValueError:
            pass
    return {"status": status, "text": text[:RESULT_CHARS]}
