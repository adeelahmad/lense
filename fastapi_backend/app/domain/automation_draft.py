"""Describe an automation in words (docs/automations.md, Describe it in words): one model call turns a sentence such as
"when a new recording is added to pods, log its title" into a draft automation, with the kinds of activity, the
match operators and the actions as its vocabulary. The draft is checked piece by piece: what doesn't fit is dropped
and said, and what the model couldn't map is listed. Nothing is saved; the person (or agent) reads it, changes it and
saves it.

The call counts in the activity ledger for the person and the namespaces it would work in, and isn't made once a
budget they count for (or everything's) is used up.
"""

from __future__ import annotations

import json

from . import actions, activity, automation_spec, automations, budgets, events, llm, match, store

TEXT_MAX = 2000
SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "triggers": {"type": "array", "items": {"type": "object"}},
        "if": {"type": "object"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"action": {"type": "string"}, "params": {"type": "object"}},
                "required": ["action"],
            },
        },
        "unmapped": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "triggers", "actions"],
}
SYSTEM = """You turn a person's sentence into an automation for Lens, an archive of their documents, recordings and
messages. An automation has triggers (when), an optional If (a test) and actions (then). Reply with JSON only:
{"name": short name, "description": one sentence, "triggers": [...], "if": a match tree or omit it,
 "actions": [{"action": name, "params": {...}}], "unmapped": [each part of the sentence you couldn't express]}.

Triggers: {"as": a short lowercase name, "on": "event", "kinds": [kinds below], "where": a match tree on the
activity (paths like object.name)}, {"as": a short lowercase name, "on": "webhook"} (an address another service or
device calls; Lens gives it when the automation is saved), or {"as": "manual", "on": "manual"}. TIMES

A match tree is {"all": [...]}, {"any": [...]} or {"not": tree} around tests {"path": ..., "op": ..., "value": ...};
op is one of OPS. The If's paths start with the run context's keys (event, events, trigger, automation, namespace,
user, now), like event.object.name.

An action's params may hold templates over the run context, like "New: {{ event.object.name }}". Use only the kinds,
actions and params listed; anything else goes in unmapped. Never invent values the sentence doesn't give."""


TIMES = {
    "schedule": '{"as": a short lowercase name, "on": "schedule", "cron": five-field cron like "0 7 * * 1-5", "timezone": an '
    'IANA zone like "Europe/London"}',
    "at": '{"as": a short lowercase name, "on": "at", "at": a local time like "2026-10-20T09:00", "timezone": an IANA zone}',
    "relative": '{"as": a short lowercase name, "on": "relative", "kinds": [kinds below], "where": a match tree, "field": '
    'the time in the activity it counts from, like object.startTime, "offset": like "-30m" (before) or "2h" (after)}',
}


def _times():
    """What the model is told about triggers in time: the ones built, and the rest put in unmapped."""
    have = [on for on in TIMES if not _soon(on)]
    if not have:
        return "Schedules and times aren't available yet: put those parts in unmapped."
    said = "Triggers in time: " + ", ".join(TIMES[on] for on in have) + ". With no time zone in the sentence, use UTC."
    if len(have) < len(TIMES):
        said += f" Triggers on {' or '.join(on for on in TIMES if on not in have)} aren't available yet: put those in unmapped."
    return said


def _fields(schema, prefix="", depth=0):
    """The dotted paths of an activity's fields with their titles, to name in tests."""
    out = []
    for k, v in ((schema or {}).get("properties") or {}).items():
        path = f"{prefix}{k}"
        if v.get("type") == "object" and depth < 1:
            out += _fields(v, path + ".", depth + 1)
        elif v.get("title"):
            out.append(f"{path} ({v['title']})")
    return out


def vocabulary():
    """What the model may use: the kinds of activity with their fields, the operators and the actions."""
    kinds = [
        {"kind": k["name"], "label": k["label"], "fields": _fields(k.get("schema") or {})} for k in events.kinds() if not k.get("noisy")
    ]
    acts = [
        {
            "action": a["name"],
            "label": a["label"],
            "description": a.get("description") or "",
            "params": a["params"],
            "required": a.get("required") or [],
        }
        for a in actions.catalog()
    ]
    return {"kinds": kinds, "ops": list(match.WORDS), "actions": acts}


def _soon(on):
    """Whether a trigger needs a part of the studio that isn't here yet (automations.SOON)."""
    need = automations.SOON.get(on) if isinstance(on, str) else None
    return need is not None and automations.provided(need[0], need[1]) is None


def _triggers(raw, dropped, unmapped):
    out, seen = [], set()
    for n, t in enumerate(raw if isinstance(raw, list) else [], 1):
        if isinstance(t, dict) and _soon(t.get("on")):
            unmapped.append(f"a trigger on {t['on']} (coming soon)")
            continue
        try:
            got = automation_spec.check_trigger(t, n)
        except ValueError as e:
            where = isinstance(t, dict) and t.pop("where", None)
            try:
                got = automation_spec.check_trigger(t, n) if where else None
            except ValueError:
                got = None
            if got is None:
                dropped.append(str(e))
                continue
            dropped.append(f"{e} (its where was left out)")
        if got["as"] in seen:
            continue
        seen.add(got["as"])
        out.append(got)
    if not out:
        out = [{"as": "manual", "on": "manual"}]
        dropped.append("no trigger fit, so it runs when you run it")
    return out[: automation_spec.MAX_TRIGGERS]


def _actions(raw, dropped):
    out = []
    for n, a in enumerate(raw if isinstance(raw, list) else [], 1):
        name = a.get("action") if isinstance(a, dict) else None
        try:
            spec = actions.get(str(name))
        except KeyError:
            dropped.append(f"action {n}: there's no action {name}")
            continue
        try:
            params = actions.check_params(spec, a.get("params") or {})
        except ValueError as e:
            dropped.append(f"action {n}: {e}")
            continue
        out.append((spec, params))
    return out


def _graph(acts):
    """The actions in order after the input node; one that reaches outside Lens (or may) is a leaf off the one
    before it, as the leaf rule has it, and the rest go on from there."""
    nodes, edges, last = [{"id": "input", "type": "input", "config": {}}], [], "input"
    for n, (spec, params) in enumerate(acts, 1):
        nid = f"a{n}"
        nodes.append({"id": nid, "type": "action", "config": {"action": spec.name, "params": params}})
        edges.append({"source": last, "target": nid})
        if isinstance(spec.effect, str) and spec.effect != "out":  # one worked out from its params may reach out
            last = nid
    return {"nodes": nodes, "edges": edges}


def draft(db, cfg, text, user, spaces=None):
    """{draft, dropped, unmapped}: `draft` is what POST /automations takes (switched off), `dropped` the parts of the
    model's reply that didn't fit and why, `unmapped` the parts of the sentence it couldn't express."""
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("say what the automation should do")
    if len(text) > TEXT_MAX:
        raise ValueError(f"say it in at most {TEXT_MAX} characters")
    account = automations._account(db, (user or {}).get("id"))
    if not account:
        raise ValueError("an automation acts for a person; sign in")
    can = automations.editable(db, account)
    if not can:
        raise ValueError(f"{account.get('name') or account['email']} can't edit any namespace, so an automation couldn't work anywhere")
    spaces = [int(s) for s in spaces] if spaces else None
    if spaces and not set(spaces) <= can:
        raise ValueError("you can make automations only in namespaces you can edit")
    if not llm.configured(cfg):
        raise ValueError("no language model is configured (Settings → LLM provider)")
    refs = [f"account:{account['id']}", *(f"space:{s}" for s in spaces or [])]
    why = budgets.used_up(db, refs)
    if why:
        raise ValueError(f"over budget: {why}")
    vocab = vocabulary()
    system = SYSTEM.replace("OPS", ", ".join(vocab["ops"])).replace("TIMES", _times())
    ask = (
        f"Kinds of activity and their fields:\n{json.dumps(vocab['kinds'])}\n\nActions:\n{json.dumps(vocab['actions'])}\n\nSentence: {text}"
    )
    with activity.scope(db, *refs, cfg=cfg):
        try:
            reply = llm.json_out(cfg, system, ask, SCHEMA)
        except llm.LLMError as e:
            raise ValueError(f"the model couldn't draft it: {e}") from None
    dropped, unmapped = [], [str(u)[:300] for u in reply.get("unmapped") or [] if str(u).strip()][:20]
    out = {
        "name": " ".join(str(reply.get("name") or "").split())[:200] or "New automation",
        "description": " ".join(str(reply.get("description") or "").split())[:2000] or None,
        "enabled": False,
        "triggers": _triggers(reply.get("triggers"), dropped, unmapped),
    }
    if spaces:
        out["spaces"] = spaces
    if reply.get("if"):
        try:
            out["if"] = automation_spec.check_if(reply["if"])
        except ValueError as e:
            dropped.append(f"the If: {e}")
    acts = _actions(reply.get("actions"), dropped)
    if acts:
        out["graph"] = _graph(acts)
    else:
        dropped.append("no action fit; add one before saving")
    if acts:
        try:
            automations._check(db, dict(out), None, account)
        except ValueError as e:
            dropped.append(f"as a whole: {e}")
    return {"draft": store.clean(out), "dropped": dropped, "unmapped": unmapped}
