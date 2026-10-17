"""Automation actions that use the model and the assistant's tools (docs/automations.md, Actions): `ask_model`,
`call_tool` and `ask_agent`.

All three only read (effect `read`): what they pass on goes to the next nodes, and any change goes through the
assistant's own tools, which here ask the person for every one (Box: a change becomes an approval, notes included, and
whatever could carry data out of Lens asks first). The Toolbox acts as the automation (automation:<id>) with its
person's permissions within the automation's namespaces, their extensions, and no server tools; its approvals are the
person's, with the run as their origin.

- `ask_model` asks the model one question (its prompt rendered with what the run read marked as data); with a
  `json_schema` the reply is that object, so later nodes read `{{ steps.n3.date }}`.
- `call_tool` calls one of the assistant's tools with arguments, and passes on what it gave back.
- `ask_agent` hands the situation to the agent: the assistant's tool loop (chat.tool_answer) with the triggering
  activity as context marked as data, limited to the chosen tools (or the person's usual set). The exchange is saved
  as a conversation of the person's, titled "<automation>: <what happened>", so they can open it, approve what it
  proposed there and carry on.

Model calls run inside the run's ledger scope (activity.py) and are refused once a budget the run counts for is used
up (budgets.used_up), as the overall hard limit refuses any call (activity.OverBudget).
"""

from __future__ import annotations

import json

from . import actions, activity, ai_tools, budgets, chat, llm, store, untrusted

SYSTEM = (
    "You do one step of an automation in Lens, for the person it runs for. Do what the prompt asks, briefly and "
    "exactly, from what it gives you; don't invent anything."
)
AGENT_NOTE = (
    "\n\nYou are acting for an automation, “{name}”, that runs for {person}; nobody is reading along now. "
    "Look things up and propose what the instructions call for: every change you propose waits for their approval, and "
    "this exchange is saved as a conversation they can open. Answer with what you found and what you proposed."
)
MAX_STEPS = 12
ABOUT_CHARS = 12000
OUTSIDE = "the activity that started this automation"
KEPT = ("id", "type", "kind", "published", "actor", "object", "target", "tag", "summary", "space")
LEFT_OUT = {"remember", "decline_approval"}  # a namespace assistant's memory is for conversations with it; the person's picks are theirs


def _short(text, n=80):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _model(cfg, p):
    if not llm.configured(cfg):
        raise ValueError("no model is set up: pick one in Settings › Model")
    return chat.check_model(cfg, p.get("model"))


def _budget(db, intent):
    """ValueError when a budget this call counts for is used up: the run's own (the ledger scope it runs in), the
    automation's and its namespaces'."""
    refs = [*activity.current(), *(f"space:{s}" for s in intent["spaces"])]
    if intent.get("automation") is not None:
        refs.append(f"automation:{intent['automation']}")
    why = budgets.used_up(db, refs)
    if why:
        raise ValueError(f"over budget: {why}")


def _skeleton(schema):
    """An empty value of a JSON Schema's shape: what a dry run passes on in place of the model's reply."""
    t = schema.get("type")
    if t == "object":
        return {k: _skeleton(v) for k, v in (schema.get("properties") or {}).items() if isinstance(v, dict)}
    if schema.get("enum"):
        return schema["enum"][0]
    return {"string": "", "integer": 0, "number": 0, "boolean": False, "array": [], "null": None}.get(t, "")


# ---------- ask_model ----------
def _ask_model(db, cfg, intent, user, dry):
    p = intent["params"]
    actions.person(db, intent, user)
    model = _model(cfg, p)
    schema = p.get("json_schema")
    if schema is not None and (not isinstance(schema, dict) or schema.get("type") != "object"):
        raise ValueError("ask_model: json_schema is a JSON Schema of type object")
    prompt = str(p.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("ask_model: the prompt is empty")
    if dry:
        return {
            **(_skeleton(schema) if schema else {"text": ""}),
            "would": f"ask {model or cfg['llm'].get('model')}: {_short(prompt, 200)}",
        }
    _budget(db, intent)
    system = (p.get("system") or SYSTEM) + untrusted.rules(cfg)
    try:
        if schema:
            got = llm.json_out(cfg, system, prompt, schema, model=model)
            if not isinstance(got, dict):
                raise ValueError("ask_model: the model's reply isn't an object")
            return got
        return {"text": llm.chat(cfg, [{"role": "system", "content": system}, {"role": "user", "content": prompt}], model=model)}
    except activity.OverBudget as e:
        raise ValueError(f"over budget: {e}") from None
    except llm.LLMError as e:
        raise ValueError(f"the model couldn't answer: {e}") from None


# ---------- the Toolbox an automation uses ----------
class Box(ai_tools.Toolbox):
    """The assistant's Toolbox for an automation: limited to `only` (tool names, None for every one), and telling the
    model who it acts for (`note`). Nobody reads along while it runs, so every change waits for the person: notes it
    would write become approvals (the chat assistant writes them at once), what could carry data out of Lens asks first
    whatever the run read (wary), and it keeps no memories."""

    only: set | None = None
    note = ""

    def specs(self):
        got = [s for s in super().specs() if s["function"]["name"] not in LEFT_OUT]
        return got if self.only is None else [s for s in got if s["function"]["name"] in self.only]

    def system_note(self):
        return super().system_note() + self.note

    def wary(self):
        return True

    def read_from(self):
        said = "an automation is acting, with nobody reading along"
        return said + (f", and it read text from outside Lens: {', '.join(self.outside[:4])}" if self.outside else "")

    def t_write_note(self, namespace, title, body, summary=None, place=None, parent_id=None, about=None):
        self._space(namespace, edit=True)
        args = store.clean(
            {
                "namespace": namespace,
                "title": title,
                "body": body,
                "summary": summary,
                "place": place,
                "parent_id": parent_id,
                "about": about,
            }
        )
        return self._approval("write_note", args, f"Write the note “{_short(title, 120)}” in {namespace}")

    def t_update_note(self, note_id, title=None, summary=None, place=None, body=None, append=None, parent_id=None):
        p = self._note(note_id, edit=True)
        args = store.clean(
            {"note_id": p["id"], "title": title, "summary": summary, "place": place, "body": body, "append": append, "parent_id": parent_id}
        )
        return self._approval("update_note", args, f"Change the note “{_short(p['title'], 120)}”")


def _untrusted(intent, about):
    return bool(intent.get("untrusted")) or bool(((about or {}).get("lens") or {}).get("untrusted"))


def toolbox(db, cfg, intent, user, about=None, chat_id=None):
    """A Toolbox acting as the intent's automation with its person's permissions now, in its namespaces; marked as
    having read outside text when the run did. ValueError when the person can't read any of them anymore."""
    acc = actions.person(db, intent, user)
    readable, editable = actions.reach(db, intent, acc)
    if intent["spaces"] and not readable:
        raise ValueError(f"{acc['name']} can't read this automation's namespaces anymore")
    names = store.space_names(db)
    scope = {"namespaces": sorted(names[s] for s in readable if s in names)} if intent["spaces"] else None
    who = actions.actor(db, intent)
    box = Box(
        db,
        cfg,
        {"id": who["id"], "email": who["email"]},
        readable,
        editable,
        scope,
        chat_id,
        outside=[OUTSIDE] if _untrusted(intent, about) else [],
        account=acc["id"],
        origin=store.clean({"automation": intent.get("automation"), "run": intent.get("run"), "node": intent["node"]}),
    )
    box.note = AGENT_NOTE.format(name=who["name"], person=acc["name"])
    return box, acc, who


def _offered(box):
    return [s["function"]["name"] for s in box.specs()]


# ---------- call_tool ----------
def _call_tool(db, cfg, intent, user, dry):
    p = intent["params"]
    tool, args = str(p.get("tool") or "").strip(), p.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("call_tool: args are an object")
    box, _, _ = toolbox(db, cfg, intent, user)
    if tool not in _offered(box):
        raise ValueError(f"there's no tool {tool} for this automation's person in its namespaces")
    if dry:
        return {"tool": tool, "result": None, "summary": f"would call {tool}", "approvals": []}
    result, summary = box.call(tool, args)
    try:
        out = json.loads(result)
    except ValueError:
        out = result
    if isinstance(out, dict) and set(out) == {"error"}:
        raise ValueError(f"{tool}: {out['error']}")
    return {"tool": tool, "result": out, "summary": summary, "approvals": [a["id"] for a in box.approvals]}


# ---------- ask_agent ----------
def _about(p):
    a = p.get("about")
    return a if isinstance(a, dict) and (a.get("kind") or a.get("object")) else None


def _context(about, cfg):
    data = json.dumps({k: about[k] for k in KEPT if k in about}, ensure_ascii=False, default=str)
    cut = " (cut short)" if len(data) > ABOUT_CHARS else ""
    return f"\n\nWhat happened, as the automation saw it (an activity in Lens's stream{cut}):\n" + untrusted.wrap(
        data[:ABOUT_CHARS], f"{about.get('kind') or 'activity'} activity", cfg
    )


def _ask_agent(db, cfg, intent, user, dry):
    p = intent["params"]
    instructions = str(p.get("instructions") or "").strip()
    if not instructions:
        raise ValueError("ask_agent: say what the agent should do")
    tools = p.get("tools") or None
    if tools is not None and (not isinstance(tools, list) or not all(isinstance(t, str) for t in tools)):
        raise ValueError("ask_agent: tools are a list of tool names")
    try:
        steps_max = min(max(int(p.get("max_steps") or 6), 1), MAX_STEPS)
    except (TypeError, ValueError):
        raise ValueError(f"ask_agent: max_steps is a number from 1 to {MAX_STEPS}") from None
    about = _about(p)
    box, acc, who = toolbox(db, cfg, intent, user, about)
    if tools:
        missing = sorted(set(tools) - set(_offered(box)))
        if missing:
            raise ValueError(f"there's no tool {missing[0]} for this automation's person in its namespaces")
        box.only = set(tools)
    if dry:
        return {
            "answer": "",
            "chat": None,
            "approvals": [],
            "steps": [],
            "tools": _offered(box),
            "would": f"ask the agent: {_short(instructions, 200)}",
        }
    _model(cfg, {})
    _budget(db, intent)
    happened = (about or {}).get("summary") or (about or {}).get("kind") or f"run {intent.get('run') or 'by hand'}"
    cid = chat.create(db, acc["id"], title=f"{who['name']}: {_short(happened, 100)}", scope=box.scope)
    box.chat = cid
    page = None
    if about:
        url = (about.get("object") or {}).get("url") if isinstance(about.get("object"), dict) else None
        page = {"url": str(url or f"/timeline?activity={about.get('id') or ''}"), "title": _short(happened, 300)}
    chat.add(db, cid, "user", instructions, context=page, origin=box.origin, meta=chat.question_record("automation", tools=tools or None))
    # the answer's id up front: every call made for it names it in the ledger, and its record sums them
    mid = db.next_id("chat_message")
    record, spent = chat.Record(cfg, scope=box.scope, picked=tools or None), f"chat_message:{mid}"
    # reading the activity counts as reading outside text for this conversation from now on (ai_tools.read_before)
    steps = (
        [{"tool": "automation_event", "args": {"id": about.get("id")}, "summary": f"Read what happened: {_short(happened, 200)}"}]
        if about
        else []
    )
    question = instructions + (_context(about, cfg) if about else "")
    text, how = None, "answered"

    def said(words, cited, outcome, **kw):
        """The answer, saved with its record."""
        chat.add(db, cid, "assistant", words or "(no answer)", cited, steps=steps, mid=mid, meta=record.meta(db, outcome, spent), **kw)

    try:
        with activity.scope(db, f"chat:{cid}", spent, cfg=cfg):
            for kind, data in chat.tool_answer(cfg, box, question, (), steps_max, record=record):
                if kind == "step":
                    steps.append(data)
                else:
                    text, how = data, "direct" if kind == "direct" else "answered"
    except llm.ToolsUnsupported:
        said("", [], "error", error="this model can't use tools")
        raise ValueError("this model can't use tools, so the agent can't act: pick one that can in Settings › Model") from None
    except (llm.LLMError, activity.OverBudget) as e:
        said("", [], "error", error=str(e))
        raise ValueError(f"the agent couldn't answer: {e}") from None
    with activity.scope(db, f"chat:{cid}", spent, cfg=cfg):
        box.after_answer(text or "")
    said(text, box.cited(text), how, model=cfg["llm"].get("model"))
    return {
        "answer": text or "",
        "chat": cid,
        "approvals": [a["id"] for a in box.approvals],
        "steps": [{"tool": s.get("tool"), "summary": s.get("summary")} for s in steps],
    }


actions.register(
    actions.ActionSpec(
        name="ask_model",
        label="Ask the model",
        group="AI",
        description="Asks the model one question and passes its reply on: text, or the fields a JSON Schema names.",
        params={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "title": "Prompt", "format": actions.PROMPT, "maxLength": 20000},
                "system": {"type": "string", "title": "Instructions", "maxLength": 4000},
                "json_schema": {"type": "object", "title": "Reply as JSON", "description": "a JSON Schema of type object"},
                "model": {"type": "string", "title": "Model", "maxLength": 200},
            },
        },
        required=("prompt",),
        effect="read",
        output={
            "type": "object",
            "description": "{text}, or the object json_schema describes",
            "properties": {"text": {"type": "string"}},
        },
        perform=_ask_model,
        describe=lambda p: f"Ask the model: “{_short(p.get('prompt'))}”",
    )
)

actions.register(
    actions.ActionSpec(
        name="call_tool",
        label="Use an assistant tool",
        group="AI",
        description="Calls one of the assistant's tools; tools that change something ask you first.",
        params={
            "type": "object",
            "properties": {
                "tool": {"type": "string", "title": "Tool", "maxLength": 100},
                "args": {"type": "object", "title": "Arguments", "default": {}},
            },
        },
        required=("tool",),
        effect="read",
        output={
            "type": "object",
            "properties": {
                "tool": {"type": "string"},
                "result": {},
                "summary": {"type": "string"},
                "approvals": {"type": "array", "items": {"type": "integer"}},
            },
        },
        perform=_call_tool,
        describe=lambda p: f"Use the assistant's {p.get('tool') or 'tool'} tool",
    )
)

actions.register(
    actions.ActionSpec(
        name="ask_agent",
        label="Ask the agent",
        group="AI",
        description=(
            "Hands what happened to the agent with your instructions: it looks things up and proposes what to do; every "
            "change waits for you, and the exchange is saved as a conversation you can open."
        ),
        params={
            "type": "object",
            "properties": {
                "instructions": {"type": "string", "title": "Instructions", "format": actions.PROMPT, "maxLength": 8000},
                "about": {"type": "object", "title": "About", "default": "{{ event }}", "description": "the activity it is about"},
                "tools": {
                    "type": "array",
                    "title": "Tools",
                    "items": {"type": "string"},
                    "description": "only these tools (empty: your usual set)",
                },
                "max_steps": {"type": "integer", "title": "Steps", "default": 6, "description": f"rounds of tool calls, 1 to {MAX_STEPS}"},
            },
        },
        required=("instructions",),
        effect="read",
        output={
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "chat": {"type": "integer"},
                "approvals": {"type": "array", "items": {"type": "integer"}},
                "steps": {"type": "array", "items": {"type": "object"}},
            },
        },
        perform=_ask_agent,
        describe=lambda p: f"Ask the agent: “{_short(p.get('instructions'))}”",
    )
)
