"""Extensions: tools, skills, hooks and plugins for the assistant, kept private or shared like custom nodes."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Acl, Cfg, CurrentUser, Db, Principal, Writer, domain_errors
from app.domain import ai_tools, auth, extensions
from app.schemas.common import Created, Ok
from app.schemas.extensions import (
    Extension,
    ExtensionCreate,
    ExtensionDetail,
    ExtensionTest,
    ExtensionTestResult,
    ExtensionUpdate,
    ExtensionVersionCreate,
    Kind,
    ManifestCheck,
    ManifestChecked,
)
from app.schemas.templates import VersionSaved

router = APIRouter(prefix="/extensions", tags=["extensions"])


def me(user: Principal) -> dict[str, Any]:
    return extensions.who(user.id, user.email, user.admin and user.via != "oauth", user.roles)


@contextlib.contextmanager
def _errors() -> Iterator[None]:
    with domain_errors():
        try:
            yield
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None


def _manifest(body: ExtensionCreate | ExtensionVersionCreate) -> dict[str, Any]:
    if (body.manifest is None) == (body.text is None):
        raise ValueError("send the manifest as an object or as text, one of the two")
    return body.manifest if body.manifest is not None else extensions.parse_manifest(body.text)


@router.get("")
def list_extensions(user: CurrentUser, db: Db, kind: Kind | None = None) -> list[Extension]:
    """The extensions you can see: yours, the ones shared with your namespaces or with everyone (admins: all)."""
    return extensions.visible(db, me(user), kind)


@router.post("/check")
def check_manifest(body: ManifestCheck, user: CurrentUser, db: Db) -> ManifestChecked:
    """Read and check a manifest written as code, without saving it: 400 says what's wrong."""
    with _errors():
        return ManifestChecked(manifest=extensions.check_manifest(extensions.parse_manifest(body.text), me(user), db))


@router.post("")
def create_extension(body: ExtensionCreate, user: Writer, db: Db) -> Created:
    with _errors():
        eid = extensions.create(db, me(user), _manifest(body), body.enabled, body.origin)
    auth.audit(db, user.as_audit(), "extension.create", f"extension:{eid}", {"origin": body.origin})
    return Created(id=eid)


@router.get("/{eid}")
def get_extension(eid: int, user: CurrentUser, db: Db, version: int | None = None) -> ExtensionDetail:
    """One version (default: the current one), as a manifest too, and the list of versions."""
    with _errors():
        return extensions.visible_one(db, me(user), eid, version)


@router.post("/{eid}/versions")
def create_extension_version(eid: int, body: ExtensionVersionCreate, user: Writer, db: Db) -> VersionSaved:
    with _errors():
        n = extensions.save_version(db, me(user), eid, _manifest(body), body.notes, body.origin)
    auth.audit(db, user.as_audit(), "extension.save", f"extension:{eid}", {"version": n, "origin": body.origin})
    return VersionSaved(version=n)


@router.patch("/{eid}")
def update_extension(eid: int, body: ExtensionUpdate, user: Writer, db: Db) -> Ok:
    """Its title, description, who sees it, and whether it's switched on."""
    with _errors():
        extensions.update(db, me(user), eid, **body.model_dump(exclude_unset=True))
    auth.audit(db, user.as_audit(), "extension.update", f"extension:{eid}", body.model_dump(exclude_unset=True))
    return Ok()


@router.delete("/{eid}")
def delete_extension(eid: int, user: Writer, db: Db) -> Ok:
    """Takes it out of the assistant at once."""
    with _errors():
        extensions.remove(db, me(user), eid)
    auth.audit(db, user.as_audit(), "extension.delete", f"extension:{eid}")
    return Ok()


@router.post("/{eid}/test")
def test_extension(eid: int, body: ExtensionTest, user: Writer, acl: Acl, db: Db, cfg: Cfg, request: Request) -> ExtensionTestResult:
    """Try one of its tools with these arguments, switched on or not. A tool that changes something really runs, so it
    needs `confirm`."""
    with _errors():
        g = extensions.visible_one(db, me(user), eid)
        items = g["spec"]["items"] if g["kind"] == "plugin" else [g]
        name = body.tool or (g["name"] if g["kind"] == "tool" else None)
        t = next((it for it in items if it.get("kind") == "tool" and it["name"] == name), None)
        if not t:
            raise ValueError("name one of its tools to try")
        if t["spec"]["effect"] == "change" and not body.confirm:
            raise ValueError("this tool changes something: confirm to really run it")
        # the tools a canvas tool calls are the ones this person has in a conversation
        box = ai_tools.Toolbox(
            db, cfg, user.as_audit(), set(acl.roles), set(acl.editable()), None, None, request.app.state.archive.base, user.admin
        )
        out = extensions.run_tool(db, cfg, t["spec"], extensions.tool_args(t["spec"], body.args), toolbox=box)
        if box.approvals and isinstance(out, dict):  # tools it called that wait for a yes, in Approvals
            out["approvals"] = box.approvals
    auth.audit(db, user.as_audit(), "extension.test", f"extension:{eid}", {"tool": name})
    return ExtensionTestResult(output=out)
