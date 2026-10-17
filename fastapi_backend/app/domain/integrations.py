"""A namespace's own ways in for other apps (docs/mcp.md, "One namespace's MCP server"; docs/api.md, "Chat
completions for any app").

* ``mcp``: an MCP server that sees this namespace alone, at ``/mcp/<namespace>`` (app/api/mcp.py). An agent connected
  to it searches, reads and cites this namespace and nothing else, whatever else the person who signed it in can read;
  a token an app was given for it (its RFC 8707 resource) opens nothing but it.
* ``chat``: OpenAI's chat completions API at ``/api/v1/namespaces/<namespace>`` (``/chat/completions`` and
  ``/models``, completions.py), so any app that speaks it gets answers from the configured model, grounded in this
  namespace and citing it. ``tools``: the assistant's read tools the model may also use there (none by default: one
  search, one model call).

Each is off until an owner of the namespace turns it on (Admin → Namespaces → the namespace → Connect apps, or PATCH
/namespaces/{name}/integrations). Turned on, it opens nothing new: apps still sign in as a person (OAuth, or an API
token) and see what that person sees in this namespace, never more.
"""

from __future__ import annotations

from . import ai_tools, store

R = store.R
NOT_OFFERED = {"entity_setup"}  # reads, but is about setting the namespace up, not about what it holds


def tool_choices():
    """The tools a namespace's chat completions may offer the model: the assistant's own tools that only read, as
    (name, what it does)."""
    return [(t[0], t[1]) for t in ai_tools.TOOLS if not t[4] and t[0] not in NOT_OFFERED]


def profile(db, sid):
    """The namespace's integrations as stored, with defaults: every one off. KeyError for no such namespace."""
    row = db.one("SELECT integrations FROM $r", r=R("space", int(sid)))
    if row is None:
        raise KeyError(sid)
    i = row.get("integrations") or {}
    known = {n for n, _ in tool_choices()}
    return {
        "mcp": bool(i.get("mcp")),
        "chat": bool(i.get("chat")),
        "tools": [t for t in i.get("tools") or [] if t in known],
        "updated_at": i.get("updated_at"),
        "updated_by": i.get("updated_by"),
    }


def save(db, sid, mcp=None, chat=None, tools=None, user=None):
    """Change what is given; the rest stays. ValueError for a tool that isn't one of tool_choices."""
    i = profile(db, sid)
    if mcp is not None:
        i["mcp"] = bool(mcp)
    if chat is not None:
        i["chat"] = bool(chat)
    if tools is not None:
        known = [n for n, _ in tool_choices()]
        bad = [t for t in tools if t not in known]
        if bad:
            raise ValueError(f"{', '.join(bad)} isn't one of the tools offered here: {', '.join(known)}")
        i["tools"] = [t for t in known if t in set(tools)]
    i.update(updated_at=store.now(), updated_by=user)
    db.q("UPDATE $r SET integrations = $i", r=R("space", int(sid)), i=store.clean(i))
    return profile(db, sid)


def mcp_on(db, sid):
    """Whether the namespace's own MCP server answers."""
    try:
        return profile(db, sid)["mcp"]
    except KeyError:
        return False
