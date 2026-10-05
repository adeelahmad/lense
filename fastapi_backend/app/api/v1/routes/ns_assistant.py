"""Each namespace's own assistant (ns_assistant.py): owners turn it on and give it a name and instructions; people who
edit the namespace read, write, pin and forget what it remembers, and those who read it see the memories."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, Db, Writer
from app.domain import auth, ns_assistant
from app.schemas.common import Created, Ok
from app.schemas.ns_assistant import (
    AssistantMemory,
    AssistantMemoryCreate,
    AssistantMemoryUpdate,
    NamespaceAssistant,
    NamespaceAssistantUpdate,
)

router = APIRouter(prefix="/namespaces", tags=["namespaces"])


def _view(db: Db, sid: int) -> NamespaceAssistant:
    return NamespaceAssistant(**ns_assistant.profile(db, sid), memories=ns_assistant.count(db, sid))


@router.get("/{name}/assistant")
def get_namespace_assistant(name: str, acl: Acl, db: Db) -> NamespaceAssistant:
    """The namespace's assistant: whether it's on, its name and instructions, and how much it remembers."""
    return _view(db, acl.namespace(name))


@router.patch("/{name}/assistant")
def update_namespace_assistant(name: str, body: NamespaceAssistantUpdate, acl: Acl, user: Writer, db: Db) -> NamespaceAssistant:
    """Owners: turn the assistant on or off, name it, or change its instructions."""
    sid = acl.namespace(name, "owner")
    try:
        ns_assistant.save_profile(db, sid, body.enabled, body.name, body.instructions, user.email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "namespace.assistant", name, body.model_dump(exclude_unset=True))
    return _view(db, sid)


@router.get("/{name}/assistant/memories")
def list_assistant_memories(
    name: str, acl: Acl, db: Db, q: str | None = Query(None, description="only memories with these words, best first")
) -> list[AssistantMemory]:
    """What the namespace's assistant remembers: pinned first, then newest first (or by `q`)."""
    sid = acl.namespace(name)
    rows = ns_assistant.recall(db, sid, q, 200) if q else ns_assistant.memories(db, sid)
    ok = set(acl.roles)
    spaces = {
        r["id"]: r["space"]
        for r in db.rows(
            "SELECT record::id(id) AS id, space FROM recording WHERE id IN $ids",
            ids=[ns_assistant.R("recording", m["recording"]) for m in rows if m.get("recording") is not None],
        )
    }
    # a memory from a recording you can't read (moved since) is left out, like a citation would be
    rows = [m for m in rows if m.get("recording") is None or spaces.get(m["recording"], sid) in ok]
    return [AssistantMemory.model_validate(m) for m in ns_assistant.labelled(db, rows)]


@router.post("/{name}/assistant/memories")
def add_assistant_memory(name: str, body: AssistantMemoryCreate, acl: Acl, user: Writer, db: Db) -> Created:
    """Editors: tell the assistant something to remember."""
    sid = acl.namespace(name, "editor")
    try:
        mid = ns_assistant.remember(db, sid, body.text, user.id, author="person")
        if body.pinned:
            ns_assistant.update(db, sid, mid, pinned=True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return Created(id=mid)


@router.patch("/{name}/assistant/memories/{mid}")
def update_assistant_memory(name: str, mid: int, body: AssistantMemoryUpdate, acl: Acl, user: Writer, db: Db) -> AssistantMemory:
    """Editors: correct a memory, or pin it so it's read with every question."""
    sid = acl.namespace(name, "editor")
    try:
        m = ns_assistant.update(db, sid, mid, body.text, body.pinned)
    except KeyError:
        raise HTTPException(404, "not found") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return AssistantMemory.model_validate(ns_assistant.labelled(db, [m])[0])


@router.delete("/{name}/assistant/memories/{mid}")
def forget_assistant_memory(name: str, mid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    """Editors: forget one memory."""
    sid = acl.namespace(name, "editor")
    try:
        ns_assistant.forget(db, sid, mid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    return Ok()


@router.delete("/{name}/assistant/memories")
def forget_all_assistant_memories(name: str, acl: Acl, user: Writer, db: Db) -> Ok:
    """Owners: forget everything the assistant remembers."""
    sid = acl.namespace(name, "owner")
    ns_assistant.forget_all(db, sid)
    auth.audit(db, user.as_audit(), "namespace.assistant.forget_all", name)
    return Ok()
