"""Grounded answers for any app, OpenAI's way: a namespace's own chat completions (docs/api.md, "Chat completions for
any app").

An app that speaks OpenAI's chat completions API (a chat app, an automation tool, a library's OpenAI client) points
its base URL at ``/api/v1/namespaces/<namespace>`` and its key at a Lens token. Each request is answered like a
question in Lens's chat, by the configured model, from that namespace alone:

* the last user message is the question, the user and assistant messages before it the conversation, and system (or
  developer) messages the app's own instructions, read after Lens's rules;
* the namespace's excerpts that best answer it are found (chat.retrieve) and the model answers from them, citing
  each claim as [n]; with no model set up, the best excerpts come back on their own;
* when the namespace's owners offered tools (integrations.tool_choices: the assistant's tools that only read), the
  model may look things up with them first (chat.tool_answer), within the namespace; a model that can't call tools
  answers from the excerpts instead;
* when the namespace's own assistant is on (ns_assistant.py), the answer comes from it: its name and instructions.

What it cites comes back three ways, so any app can show it: [n] in the text with a "Sources" list after it (left
out when the app asked for a response_format), ``citations`` (the n-th link for [n]) and ``lens.sources``.

It only reads. Model calls go through the activity ledger and count for the namespace (space:<id>), and the
overall budget's hard limit stops them as it does any call.

Refine later: the app's own tools (tools in the request, handed back as tool_calls), streaming the tool loop's
answer token by token, max_tokens, what the namespace's assistant remembers, a model per namespace.
"""

from __future__ import annotations

import re

from . import ai_tools, chat, llm, ns_assistant, store

MODEL = "lens"  # the model id apps pick: the configured model, answering from the namespace
ROLES = ("system", "developer", "user", "assistant", "tool")
QUESTION_CHARS = 4000  # as a question in Lens's chat
APP_CHARS = 4000  # of the app's own instructions


def _text(content, i):
    """A message's text: a string, or the text parts of a list of parts (pictures and files are left out)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p["text"] for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text") and isinstance(p.get("text"), str)
        )
    raise ValueError(f"messages[{i}].content is text, or a list of parts")


def read(messages):
    """(question, the conversation before it, the app's instructions) from OpenAI's `messages`. ValueError when there
    is nothing to answer."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages is a list with at least one message")
    app, turns = [], []
    for i, m in enumerate(messages):
        if not isinstance(m, dict) or m.get("role") not in ROLES:
            raise ValueError(f"messages[{i}] needs a role: {', '.join(ROLES)}")
        text = _text(m.get("content"), i).strip()
        if not text:
            continue
        if m["role"] in ("system", "developer"):
            app.append(text)
        elif m["role"] in ("user", "assistant"):
            turns.append({"id": i, "role": m["role"], "content": text})
    if not turns or turns[-1]["role"] != "user" or messages[-1].get("role") != "user":
        raise ValueError("the last message is the question: a user message with some text")
    return turns[-1]["content"][:QUESTION_CHARS], turns[:-1], "\n\n".join(app)[:APP_CHARS]


def models(cfg):
    """The model ids apps may pick: lens (the configured model) and the models people may pick in Lens's chat."""
    return [MODEL, *chat.model_choices(cfg)]


def pick(cfg, asked):
    """The model to answer with: one people may pick in Lens's chat when that's asked for, else the configured one
    (None), whatever an app sends by default."""
    if asked and asked != MODEL and asked != cfg["llm"].get("model") and asked in chat.model_choices(cfg):
        return asked
    return None


def note(db, sid, ns, app):
    """What the model is told besides Lens's own rules: where it answers from, the namespace's assistant (when it is
    on), and the app's own instructions."""
    out = f"\n\nYou answer for another app, through Lens, from the {ns} namespace alone."
    p = ns_assistant.profile(db, sid)
    if p["enabled"]:
        out += f" You are {p['name']}, the assistant of the {ns} namespace."
        if p["instructions"]:
            out += f"\n\nInstructions for this namespace's assistant, from its owners:\n{p['instructions']}"
    if app:
        out += f"\n\nInstructions from the app you answer through (Lens's rules above still hold):\n{app}"
    return out


class Box(ai_tools.Toolbox):
    """The assistant's tools for an app: only the read tools the namespace's owners offered, in that namespace, with
    none of the person's extensions, skills or hooks, and nothing that changes data."""

    def __init__(self, db, cfg, user, sid, ns, tools, said, base, note_text):
        super().__init__(db, cfg, user, {sid}, set(), {"namespaces": [ns]}, None, base, said=said)
        self.only, self.note_text = set(tools), note_text
        self.ext, self.home, self.browser = None, None, None

    def system_note(self):
        return self.note_text

    def specs(self):
        return [s for s in super().specs() if s["function"]["name"] in self.only]


def answer(db, cfg, sid, ns, user, question, history, app="", model=None, tools=(), base=None, temperature=None):
    """Answer one request. Yields ("step", {tool, args, summary}) for each tool call, ("token", text) for the
    answer's pieces, then ("done", {refs, text, model, notice}): what it can cite (numbered, as [n]) and the whole
    answer. Raises llm.LLMError when the model server fails, and activity.OverBudget at the hard limit."""
    scope = {"namespaces": [ns]}
    passages = chat.retrieve(db, question, {sid}, scope, cfg=cfg)
    extra, notice = note(db, sid, ns, app), None
    used = model or cfg["llm"].get("model")
    if not llm.configured(cfg):
        text = chat.fallback(passages)
        yield "token", text
        yield "done", {"refs": passages, "text": text, "model": MODEL, "notice": None}
        return
    if tools and cfg["ai"].get("tools", True):
        box = Box(db, cfg, user, sid, ns, tools, question, base, extra)
        try:
            text = ""
            for kind, data in chat.tool_answer(cfg, box, question, history, cfg["ai"].get("max_steps") or 6, model):
                if kind == "step":
                    yield "step", data
                # a model that skips the tools hasn't seen the namespace: it answers from the excerpts instead
                elif kind == "direct" and passages and not box.cited(data):
                    break
                else:
                    text = data
            else:
                yield "token", text
                yield "done", {"refs": box.refs, "text": text, "model": used, "notice": None}
                return
        except llm.ToolsUnsupported:
            notice = "This model can't use tools, so the answer comes from a search instead."
    msgs = chat.messages_for(question, passages, history, None, cfg)
    msgs[0]["content"] += extra
    text = ""
    for piece in llm.stream_chat(cfg, msgs, model=model, temperature=0.2 if temperature is None else temperature):
        text += piece
        yield "token", piece
    yield "done", {"refs": passages, "text": text, "model": used, "notice": notice}


def link(web, ref):
    """A reference's page in the web app, at its moment (whole seconds, as the page reads `?t=`)."""
    t0 = ref.get("t0") or 0
    return f"{web}/resources/{ref['recording_id']}" + (f"?t={int(t0) // 1000}" if t0 >= 1000 else "")


def sources(web, text, refs):
    """Every numbered reference, with whether the answer cites it ([n])."""
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", text or "")}
    return [
        store.clean(
            {
                "n": r["n"],
                "recording_id": r["recording_id"],
                "title": r.get("title"),
                "time": r.get("time"),
                "speaker": r.get("speaker"),
                "url": link(web, r),
                "cited": r["n"] in cited,
            }
        )
        for r in refs
    ]


def footer(found):
    """The sources the answer cites, as Markdown after it, for apps that show only the text."""
    cited = [s for s in found if s["cited"]]
    if not cited:
        return ""
    lines = [f"[{s['n']}] [{' · '.join(x for x in (s.get('title') or 'Untitled', s.get('time')) if x)}]({s['url']})" for s in cited]
    return "\n\nSources:\n" + "\n".join(lines)
