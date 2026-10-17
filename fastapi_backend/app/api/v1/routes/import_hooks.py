"""Import webhooks (docs/api.md, Import webhooks): a namespace's owners make and revoke them; other services push
files, web addresses or text to them with the hook's token, which is all they need. A person (or their agent, or the
web clipper signed in as them) pushes to the same address with their own token, naming the namespace."""

from __future__ import annotations

import contextlib
import errno
import json
import pathlib
import tempfile
from collections.abc import Iterator
from email.message import Message
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, import_hooks, store, uploads
from app.schemas.common import Ok
from app.schemas.import_hooks import (
    HookItem,
    HookPush,
    HookPushed,
    ImportHook,
    ImportHookCreate,
    ImportHookCreated,
    ImportHookToken,
    ImportHookUpdate,
    ImportTarget,
)

router = APIRouter(tags=["import hooks"])
PUSH_PATH = "/api/v1/hooks/import"
MAX_JSON = 6 * 1024 * 1024  # text up to 5 MB, with room for JSON's escapes
MAX_FILES = 20
COPY = 1 << 20
FILE_BODY = {
    "requestBody": {
        "content": {
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "array", "items": {"type": "string", "format": "binary"}},
                        "url": {"type": "string"},
                        "text": {"type": "string"},
                        "title": {"type": "string"},
                    },
                }
            },
            "application/json": {"schema": HookPush.model_json_schema()},
        }
    }
}


# ---------- managing them (a namespace's owners) ----------
@router.get("/namespaces/{name}/import-hooks")
def list_import_hooks(name: str, acl: Acl, user: CurrentUser, db: Db) -> list[ImportHook]:
    """The namespace's import webhooks (owners). Their tokens aren't shown again, only their last four characters."""
    sid = acl.namespace(name, "manage")
    return [ImportHook.model_validate(h) for h in import_hooks.hooks(db, sid)]


@router.post("/namespaces/{name}/import-hooks")
def create_import_hook(name: str, body: ImportHookCreate, acl: Acl, user: Writer, db: Db) -> ImportHookCreated:
    """Make an import webhook for the namespace (owners). Its token is in the answer and never shown again. Audited as
    `import_hook.create`."""
    sid = acl.namespace(name, "manage")
    with domain_errors():
        hook, token = import_hooks.create(db, sid, body.name, body.collection, body.pipeline, user.email)
    auth.audit(db, user.as_audit(), "import_hook.create", f"import_hook:{hook['id']}", {"namespace": name, "name": hook["name"]})
    return ImportHookCreated(hook=ImportHook.model_validate(hook), token=token, path=PUSH_PATH)


@router.patch("/namespaces/{name}/import-hooks/{hid}")
def update_import_hook(name: str, hid: int, body: ImportHookUpdate, acl: Acl, user: Writer, db: Db) -> ImportHook:
    """Rename a hook, pause or resume it (`enabled`), or change its collection or pipeline (null for the namespace's
    own) (owners)."""
    sid = acl.namespace(name, "manage")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name") is None:
        changes.pop("name", None)
    if changes.get("enabled") is None:
        changes.pop("enabled", None)
    if not changes:
        raise HTTPException(400, "send name, enabled, collection or pipeline")
    with domain_errors():
        before, after = import_hooks.update(db, hid, sid, changes)
    detail = {
        k: {"from": before.get(k), "to": after.get(k)}
        for k in ("name", "enabled", "collection", "pipeline")
        if before.get(k) != after.get(k)
    }
    auth.audit(db, user.as_audit(), "import_hook.update", f"import_hook:{hid}", detail)
    return ImportHook.model_validate(after)


@router.post("/namespaces/{name}/import-hooks/{hid}/token")
def new_import_hook_token(name: str, hid: int, acl: Acl, user: Writer, db: Db) -> ImportHookToken:
    """A new token for the hook (owners); the old one stops working at once."""
    sid = acl.namespace(name, "manage")
    with domain_errors():
        token = import_hooks.new_token(db, hid, sid)
    auth.audit(db, user.as_audit(), "import_hook.token", f"import_hook:{hid}")
    return ImportHookToken(token=token)


@router.delete("/namespaces/{name}/import-hooks/{hid}")
def delete_import_hook(name: str, hid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    """Delete a hook (owners): its token stops working. What it imported stays."""
    sid = acl.namespace(name, "manage")
    with domain_errors():
        hook = import_hooks.delete(db, hid, sid)
    auth.audit(db, user.as_audit(), "import_hook.delete", f"import_hook:{hid}", {"namespace": name, "name": hook["name"]})
    return Ok()


# ---------- pushing to one (its token) ----------
@contextlib.contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except HTTPException:
        raise
    except uploads.TooLarge as e:
        raise HTTPException(413, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "the hook's collection or namespace has gone") from None
    except OSError as e:
        if e.errno == errno.ENOSPC:
            raise HTTPException(507, "the server doesn't have room for this file") from None
        raise


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization") or ""
    return h[7:].strip() if h.lower().startswith("bearer ") else None


UNKNOWN = {
    import_hooks.TOKEN_PREFIX: "Lens doesn't know this import webhook token: it was replaced or deleted. Make a new one, or "
    "use your own API token instead",
    "la_": "Lens doesn't know this API token: it was revoked, ran out, or is from another Lens",
    "lo_": "this sign-in has ended: sign in again",
}
PERSONAL = ("la_", "lo_")  # a person's own tokens (and the web app's session) go in the header, never the address


def _unknown(raw: str | None) -> HTTPException:
    """401 saying what was wrong with the token, by its kind."""
    if not raw:
        why = "send an import webhook token (lih_...) or your own API token (la_...) as Authorization: Bearer"
    else:
        why = next((m for p, m in UNKNOWN.items() if raw.startswith(p)), "")
        why = why or "Lens doesn't know this token: sign in again, or send an API token (la_...) or an import webhook token"
    return HTTPException(401, why, headers={"WWW-Authenticate": "Bearer"})


def _hook(db: Any, token: str | None) -> dict[str, Any]:
    try:
        return import_hooks.by_token(db, token)
    except KeyError:
        raise _unknown(token) from None
    except import_hooks.Paused as e:
        raise HTTPException(403, str(e)) from None


def _importable(db: Any, acl: Access) -> list[str]:
    """The namespaces this person can import into, by name."""
    names = store.space_names(db)
    return sorted(names[s] for s in acl.spaces() if s in names and acl.can(s, "import"))


def _person(db: Any, acl: Access, raw: str | None, namespace: str | None) -> dict[str, Any]:
    """Where a person's own push goes: the namespace they named, or the only one they can import into."""
    user = acl.user
    if not user:
        raise _unknown(raw)
    if not user.can_write:
        raise HTTPException(403, "this API token is read-only: make one with write access in Settings, API keys")
    if namespace:
        sid = acl.namespace(namespace, "import")
    else:
        able = _importable(db, acl)
        if not able:
            raise HTTPException(403, "you can't import into any namespace: ask one of its owners for the editor role")
        if len(able) > 1:
            raise HTTPException(400, f"name the namespace with ?namespace=: you can import into {', '.join(able)}")
        sid = store.ns_id(db, able[0], create=False)
    return import_hooks.for_person(db, sid, user.as_audit())


def _target(db: Any, acl: Access, raw: str | None, in_path: bool, namespace: str | None) -> dict[str, Any]:
    """What a push goes to: the import webhook its token belongs to, or the person whose token it is."""
    if in_path and raw and raw.startswith(PERSONAL):
        raise HTTPException(401, "only an import webhook's token goes in the address: send your own token as Authorization: Bearer")
    if in_path or (raw or "").startswith(import_hooks.TOKEN_PREFIX):
        hook = _hook(db, raw)
        if namespace and namespace != hook["namespace"]:
            raise HTTPException(400, f"this import webhook imports into {hook['namespace']}: leave out ?namespace=")
        return hook
    return _person(db, acl, raw, namespace)


def _disposition_name(value: str | None) -> str | None:
    if not value:
        return None
    m = Message()
    m["content-disposition"] = value
    return m.get_filename()


def _too_big(cfg: Any) -> HTTPException:
    return HTTPException(413, f"files up to {cfg['uploads']['max_mb']} MB")


async def _spool(cfg: Any, chunks: Any) -> Any:
    """What arrives, written to a file on the uploads disk (at most uploads.max_mb). Returns its path."""
    most = cfg["uploads"]["max_mb"] * uploads.MB
    folder = await run_in_threadpool(uploads.incoming, cfg)
    f = tempfile.NamedTemporaryFile(dir=folder, prefix="hook-", delete=False)  # noqa: SIM115 - closed below
    path = pathlib.Path(f.name)
    try:
        n = 0
        async for piece in chunks:
            n += len(piece)
            if n > most:
                raise _too_big(cfg)
            await run_in_threadpool(f.write, piece)
        f.close()
        return path
    except BaseException:
        f.close()
        path.unlink(missing_ok=True)
        raise


async def _upload_chunks(up: UploadFile) -> Any:
    while piece := await up.read(COPY):
        yield piece


async def _push(
    request: Request,
    db: Any,
    cfg: Any,
    acl: Access,
    token: str | None,
    filename: str | None,
    title: str | None,
    namespace: str | None,
) -> HookPushed:
    hook = await run_in_threadpool(_target, db, acl, token or _bearer(request), token is not None, namespace)
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    items: list[dict[str, Any]] = []
    try:
        with _errors():
            if ctype == "application/json":
                raw = b""
                async for piece in request.stream():
                    raw += piece
                    if len(raw) > MAX_JSON:
                        raise HTTPException(413, "send at most 5 MB of text at a time")
                try:
                    body = HookPush.model_validate(json.loads(raw or b"null"))
                except (ValueError, ValidationError) as e:
                    raise HTTPException(400, f"send {{url}} or {{text}} as JSON: {str(e).splitlines()[0]}") from None
                if not body.url and not (body.text or "").strip():
                    raise HTTPException(400, "send a url or some text (or a file as the body)")
                if body.url:
                    items.append(await run_in_threadpool(import_hooks.accept_url, db, cfg, hook, str(body.url), body.title or title))
                if (body.text or "").strip():
                    items.append(await run_in_threadpool(import_hooks.accept_text, db, cfg, hook, body.text, body.title or title))
            elif ctype == "multipart/form-data":
                form = await request.form(max_files=MAX_FILES, max_fields=20)
                try:
                    form_title = str(form.get("title") or "") or title
                    files = [v for _, v in form.multi_items() if isinstance(v, UploadFile)]
                    url, text = form.get("url"), form.get("text")
                    if not files and not url and not text:
                        raise HTTPException(400, "send a file field (or url or text)")
                    for up in files:
                        if not up.filename:
                            raise HTTPException(400, "each file needs its name")
                        path = await _spool(cfg, _upload_chunks(up))
                        ttl = form_title if len(files) == 1 else None
                        items.append(await run_in_threadpool(import_hooks.accept_file, db, cfg, hook, path, up.filename, ttl))
                    if isinstance(url, str) and url.strip():
                        items.append(await run_in_threadpool(import_hooks.accept_url, db, cfg, hook, url.strip(), form_title))
                    if isinstance(text, str) and text.strip():
                        items.append(await run_in_threadpool(import_hooks.accept_text, db, cfg, hook, text, form_title))
                finally:
                    await form.close()
            else:
                name = filename or request.headers.get("x-filename") or _disposition_name(request.headers.get("content-disposition"))
                if not name:
                    raise HTTPException(400, "name the file: ?filename=report.pdf, or a Content-Disposition or X-Filename header")
                await run_in_threadpool(import_hooks.kind_of, cfg, uploads.clean_name(name))  # before anything is written
                length = request.headers.get("content-length")
                if length and length.isdigit() and int(length) > cfg["uploads"]["max_mb"] * uploads.MB:
                    raise _too_big(cfg)
                path = await _spool(cfg, request.stream())
                items.append(await run_in_threadpool(import_hooks.accept_file, db, cfg, hook, path, name, title))
    except ClientDisconnect:
        raise HTTPException(400, "the upload broke off; send it again") from None
    finally:
        for it in items:
            detail = {"namespace": hook["namespace"], "kind": it["kind"], "name": it["name"]}
            action = "import.push" if hook.get("person") else "import.hook"
            if not hook.get("person"):
                detail = {"hook": hook["name"], **detail}
            await run_in_threadpool(auth.audit, db, import_hooks.actor(hook), action, f"recording:{it['recording']}", detail)
        if items:
            request.app.state.graph_cache.clear()
    return HookPushed(namespace=hook["namespace"], items=[HookItem.model_validate(it) for it in items])


NAMESPACE = Query(
    None,
    max_length=40,
    description="with your own token: the namespace it goes into (default: the only one you can import into)",
)


@router.get("/hooks/import")
def import_target(request: Request, acl: Acl, db: Db) -> ImportTarget:
    """Where pushes with this bearer token go, without importing anything: an import webhook's token goes into its
    namespace; a person's own token (an API token, an app's, or the web app's) into any namespace they can import into,
    named with `?namespace=`. 401 for a token Lens doesn't know, saying which kind it is; 403 while a hook is paused."""
    raw = _bearer(request)
    if (raw or "").startswith(import_hooks.TOKEN_PREFIX):
        hook = _hook(db, raw)
        return ImportTarget(kind="hook", hook=hook["name"], namespace=hook["namespace"], namespaces=[hook["namespace"]], write=True)
    if not acl.user:
        raise _unknown(raw)
    able = _importable(db, acl)
    return ImportTarget(
        kind="person",
        email=acl.user.email,
        namespace=able[0] if len(able) == 1 else None,
        namespaces=able,
        write=acl.user.can_write,
    )


@router.post("/hooks/import", status_code=202, openapi_extra=FILE_BODY)
async def push_to_import_hook(
    request: Request,
    db: Db,
    cfg: Cfg,
    acl: Acl,
    filename: str | None = Query(None, max_length=1000, description="the file's name, for a raw body"),
    title: str | None = Query(None, max_length=200, description="its title (default: the file's name, or the page's)"),
    namespace: str | None = NAMESPACE,
) -> HookPushed:
    """Push something into an import webhook's namespace, with its token as a bearer token, or into one of your own
    namespaces with your own token (an API token with write access, an app's, or the web app's) and `?namespace=`.
    Three ways:

    - a file as the raw body, named by `?filename=`, Content-Disposition or X-Filename: audio, video, documents and
      images become assets and resources as uploads do; .srt, .vtt, .json, .jsonl and .ics are imported as
      transcripts;
    - multipart form files (field `file`, up to 20), with optional `title`, `url` and `text` fields;
    - JSON `{url}` (a page or PDF kept as a document) and/or `{text}` (imported as text), with an optional
      `title`.

    Each runs the hook's pipeline, or the namespace's. 401 for an unknown token, saying which kind it is; 403 while the
    hook is paused, for a read-only token, or without the import permission; 413 over uploads.max_mb. Audited as
    `import.hook` (by the hook) or `import.push` (by the person)."""
    return await _push(request, db, cfg, acl, None, filename, title, namespace)


@router.post("/hooks/import/{token}", status_code=202, openapi_extra=FILE_BODY)
async def push_to_import_hook_token(
    token: str,
    request: Request,
    db: Db,
    cfg: Cfg,
    acl: Acl,
    filename: str | None = Query(None, max_length=1000, description="the file's name, for a raw body"),
    title: str | None = Query(None, max_length=200),
) -> HookPushed:
    """The same, with an import webhook's token in the address, for services that can only be given a URL. Prefer the
    bearer token: addresses end up in logs. A person's own token is never taken here."""
    return await _push(request, db, cfg, acl, token, filename, title, None)
