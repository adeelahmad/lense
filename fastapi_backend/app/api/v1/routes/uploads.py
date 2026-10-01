"""Uploading audio and video in pieces (docs/api.md, Uploads): start one, send it in chunks, carry on after a dropped
connection. The chunk with the last byte turns it into a recording and queues the namespace's pipeline."""

from __future__ import annotations

import contextlib
import errno
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer
from app.domain import auth, store, uploads
from app.schemas.common import Ok
from app.schemas.uploads import Upload, UploadLimits, UploadStart

router = APIRouter(prefix="/uploads", tags=["uploads"])
UploadId = Annotated[str, Path(pattern="^[0-9a-f]{24}$")]
CHUNK = {"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}}


@contextlib.contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except HTTPException:
        raise
    except uploads.TooLarge as e:
        raise HTTPException(413, str(e)) from None
    except (uploads.Mismatch, uploads.Busy) as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "not found") from None
    except OSError as e:
        if e.errno == errno.ENOSPC:
            raise HTTPException(507, "the server doesn't have room for this file") from None
        raise


def _theirs(db: Any, uid: str, user: Principal) -> dict[str, Any]:
    """An upload, for the person sending it (or an admin); anyone else gets 404."""
    try:
        row: dict[str, Any] = uploads.get(db, uid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if row["account"] != user.id and not user.admin:
        raise HTTPException(404, "not found")
    return row


def _may_add(acl: Access, db: Any, user: Principal, ns: str) -> None:
    """Editors of the namespace; admins may name one that doesn't exist yet."""
    try:
        sid = store.ns_id(db, ns, create=False)
    except KeyError:
        if not user.admin:
            raise HTTPException(403, "only admins can create namespaces") from None
        return
    acl.need(sid, "editor")


def _finished(db: Any, uid: str) -> dict[str, Any] | None:
    try:
        row: dict[str, Any] = uploads.get(db, uid)
    except KeyError:
        return None
    return row if row.get("state") == "done" else None


@router.get("/limits")
def upload_limits(user: CurrentUser, cfg: Cfg) -> UploadLimits:
    """What can be uploaded: the audio and video types, the largest file, the chunk size the web app sends, and the
    largest transcript file for POST /import."""
    return UploadLimits(**uploads.limits(cfg))


@router.post("", status_code=201)
def start_upload(body: UploadStart, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> Upload:
    """Start uploading an audio or video file into a namespace (editors; admins may name a new one). Then send the file
    with PUT /uploads/{uid}. 400 for a type not in uploads.extensions, 413 over uploads.max_mb, 507 when the server's
    disk can't hold it."""
    ns = body.namespace.strip()
    if not store.NS_RX.match(ns):
        raise HTTPException(400, "choose a namespace: lowercase letters, digits, - and _")
    _may_add(acl, db, user, ns)
    with _errors():
        row = uploads.start(db, cfg, ns, body.filename, body.size, user.as_audit(), body.title, body.modified)
    return Upload(**uploads.view(cfg, row))


@router.get("")
def list_uploads(user: CurrentUser, db: Db, cfg: Cfg) -> list[Upload]:
    """Your uploads that haven't finished, newest first: sending the same file again carries on where it stopped."""
    uploads.sweep(db, cfg)
    return [Upload(**uploads.view(cfg, r)) for r in uploads.mine(db, user.id)]


@router.get("/{uid}")
def get_upload(uid: UploadId, user: CurrentUser, db: Db, cfg: Cfg) -> Upload:
    """How much of an upload has arrived (`offset`), or the recording it became."""
    return Upload(**uploads.view(cfg, _theirs(db, uid, user)))


@router.put("/{uid}", openapi_extra=CHUNK)
async def send_chunk(
    uid: UploadId,
    request: Request,
    acl: Acl,
    user: Writer,
    db: Db,
    cfg: Cfg,
    offset: int = Query(ge=0, description="where this chunk starts in the file: the upload's `offset`"),
) -> Upload:
    """The next chunk of the file as the raw request body (application/octet-stream), starting at `offset`; it streams
    to disk. A chunk that breaks off is dropped whole. 409 when `offset` isn't where the upload has got to (GET it and
    send from its `offset`), or while another chunk of it is arriving. The chunk with the last byte returns the upload
    done, with its recording and job; audited as `upload`."""
    row = await run_in_threadpool(_theirs, db, uid, user)
    if row.get("state") == "done":
        return Upload(**uploads.view(cfg, row))
    await run_in_threadpool(_may_add, acl, db, user, row["namespace"])
    try:
        with _errors(), uploads.Chunk(cfg, row, offset) as chunk:
            async for piece in request.stream():
                if piece:
                    await run_in_threadpool(chunk.write, piece)
            chunk.keep()
            up = await run_in_threadpool(uploads.received, db, cfg, uid, user.admin)
    except ClientDisconnect:
        raise HTTPException(400, "the chunk broke off; send it again") from None
    except HTTPException as e:
        done = await run_in_threadpool(_finished, db, uid) if e.status_code in (404, 409) else None
        if done:  # the same last chunk, sent twice: it's finished already
            return Upload(**uploads.view(cfg, done))
        raise
    if up.get("state") == "done":
        detail = {"file": up["filename"], "size": up["size"], "namespace": up["namespace"], "duplicate": bool(up.get("duplicate"))}
        await run_in_threadpool(auth.audit, db, user.as_audit(), "upload", f"recording:{up['recording']}", detail)
        request.app.state.graph_cache.clear()
    return Upload(**uploads.view(cfg, up))


@router.delete("/{uid}")
def cancel_upload(uid: UploadId, user: Writer, db: Db, cfg: Cfg) -> Ok:
    """Stop an upload and throw away what has arrived. A finished one is only forgotten: its recording stays."""
    uploads.cancel(db, cfg, _theirs(db, uid, user))
    return Ok()
