"""Import webhooks (docs/api.md, Import webhooks): a namespace's owners make and revoke them; other services push
files, web addresses or text to them with the hook's token, which is all they need."""

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

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, import_hooks, uploads
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
    sid = acl.namespace(name, "owner")
    return [ImportHook.model_validate(h) for h in import_hooks.hooks(db, sid)]


@router.post("/namespaces/{name}/import-hooks")
def create_import_hook(name: str, body: ImportHookCreate, acl: Acl, user: Writer, db: Db) -> ImportHookCreated:
    """Make an import webhook for the namespace (owners). Its token is in the answer and never shown again. Audited as
    `import_hook.create`."""
    sid = acl.namespace(name, "owner")
    with domain_errors():
        hook, token = import_hooks.create(db, sid, body.name, body.collection, body.pipeline, user.email)
    auth.audit(db, user.as_audit(), "import_hook.create", f"import_hook:{hook['id']}", {"namespace": name, "name": hook["name"]})
    return ImportHookCreated(hook=ImportHook.model_validate(hook), token=token, path=PUSH_PATH)


@router.patch("/namespaces/{name}/import-hooks/{hid}")
def update_import_hook(name: str, hid: int, body: ImportHookUpdate, acl: Acl, user: Writer, db: Db) -> ImportHook:
    """Rename a hook, pause or resume it (`enabled`), or change its collection or pipeline (null for the namespace's
    own) (owners)."""
    sid = acl.namespace(name, "owner")
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
    sid = acl.namespace(name, "owner")
    with domain_errors():
        token = import_hooks.new_token(db, hid, sid)
    auth.audit(db, user.as_audit(), "import_hook.token", f"import_hook:{hid}")
    return ImportHookToken(token=token)


@router.delete("/namespaces/{name}/import-hooks/{hid}")
def delete_import_hook(name: str, hid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    """Delete a hook (owners): its token stops working. What it imported stays."""
    sid = acl.namespace(name, "owner")
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


def _hook(db: Any, token: str | None) -> dict[str, Any]:
    try:
        return import_hooks.by_token(db, token)
    except KeyError:
        raise HTTPException(401, "that token isn't an import hook's") from None
    except import_hooks.Paused as e:
        raise HTTPException(403, str(e)) from None


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


async def _push(request: Request, db: Any, cfg: Any, token: str | None, filename: str | None, title: str | None) -> HookPushed:
    hook = await run_in_threadpool(_hook, db, token or _bearer(request))
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
            detail = {"hook": hook["name"], "namespace": hook["namespace"], "kind": it["kind"], "name": it["name"]}
            await run_in_threadpool(auth.audit, db, import_hooks.actor(hook), "import.hook", f"recording:{it['recording']}", detail)
        if items:
            request.app.state.graph_cache.clear()
    return HookPushed(namespace=hook["namespace"], items=[HookItem.model_validate(it) for it in items])


@router.post("/hooks/import", status_code=202, openapi_extra=FILE_BODY)
async def push_to_import_hook(
    request: Request,
    db: Db,
    cfg: Cfg,
    filename: str | None = Query(None, max_length=1000, description="the file's name, for a raw body"),
    title: str | None = Query(None, max_length=200, description="its title (default: the file's name, or the page's)"),
) -> HookPushed:
    """Push something into an import webhook's namespace, with its token as a bearer token. Three ways:

    - a file as the raw body, named by `?filename=`, Content-Disposition or X-Filename: audio, video, documents and
      images become recordings and resources as uploads do; .srt, .vtt, .json, .jsonl and .ics are imported as
      transcripts;
    - multipart form files (field `file`, up to 20), with optional `title`, `url` and `text` fields;
    - JSON `{url}` (a page or PDF kept as a document) and/or `{text}` (imported as a transcript), with an optional
      `title`.

    Each runs the hook's pipeline, or the namespace's. 401 for an unknown token, 403 while the hook is paused, 413 over
    uploads.max_mb. Audited as `import.hook`."""
    return await _push(request, db, cfg, None, filename, title)


@router.post("/hooks/import/{token}", status_code=202, openapi_extra=FILE_BODY)
async def push_to_import_hook_token(
    token: str,
    request: Request,
    db: Db,
    cfg: Cfg,
    filename: str | None = Query(None, max_length=1000, description="the file's name, for a raw body"),
    title: str | None = Query(None, max_length=200),
) -> HookPushed:
    """The same, with the token in the address, for services that can only be given a URL. Prefer the bearer token:
    addresses end up in logs."""
    return await _push(request, db, cfg, token, filename, title)
