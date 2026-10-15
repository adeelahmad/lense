"""A namespace's own ways in for other apps (docs/mcp.md, "One namespace's MCP server").

* ``mcp``: an MCP server that sees this namespace alone, at ``/mcp/<namespace>`` (app/api/mcp.py). An agent connected
  to it searches, reads and cites this namespace and nothing else, whatever else the person who signed it in can read;
  a token an app was given for it (its RFC 8707 resource) opens nothing but it.

Off until an owner of the namespace turns it on (Admin → Namespaces → the namespace → Connect apps, or PATCH
/namespaces/{name}/integrations). Turned on, it opens nothing new: apps still sign in as a person (OAuth, or an API
token) and see what that person sees in this namespace, never more.
"""

from __future__ import annotations

from . import store

R = store.R


def profile(db, sid):
    """The namespace's integrations as stored, with defaults: every one off. KeyError for no such namespace."""
    row = db.one("SELECT integrations FROM $r", r=R("space", int(sid)))
    if row is None:
        raise KeyError(sid)
    i = row.get("integrations") or {}
    return {"mcp": bool(i.get("mcp")), "updated_at": i.get("updated_at"), "updated_by": i.get("updated_by")}


def save(db, sid, mcp=None, user=None):
    """Change what is given; the rest stays."""
    i = profile(db, sid)
    if mcp is not None:
        i["mcp"] = bool(mcp)
    i.update(updated_at=store.now(), updated_by=user)
    db.q("UPDATE $r SET integrations = $i", r=R("space", int(sid)), i=store.clean(i))
    return profile(db, sid)


def mcp_on(db, sid):
    """Whether the namespace's own MCP server answers."""
    try:
        return profile(db, sid)["mcp"]
    except KeyError:
        return False
