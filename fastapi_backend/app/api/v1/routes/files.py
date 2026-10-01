"""A resource's files (docs/api.md#files): its primary file, and supplementary transcripts, captions, translations,
indexes, thumbnails and attachments kept beside it.

People who can read the resource list and download its files; editors add, change and delete them, which is audited.
"""

from __future__ import annotations

import errno
import pathlib
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from app.api.deps import Access, Acl, Cfg, Db, Writer, domain_errors
from app.api.media import sign_urls
from app.api.v1.routes.uploads import CHUNK
from app.domain import access as acc
from app.domain import auth, convert, documents, files, render, store, video
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.files import FileLine, FileLines, FileRole, FileUpdate, PrimaryFile, ResourceFile, ResourceFiles

R = store.R
router = APIRouter(prefix="/recordings/{rid}/files", tags=["files"])


def _primary(db: DB, cfg: dict[str, Any], rid: int, rec: dict[str, Any]) -> PrimaryFile | None:
    """The audio, video, document or image, when the resource has one."""
    kind = render.kind(rec)
    if kind == "transcript":
        return None
    local = render.has_audio(db, cfg, rid) if kind in ("audio", "video") else store.resolve_path(cfg, rec.get("path"))
    local = local if local and pathlib.Path(local).is_file() else None
    if not (local or rec.get("remote")):
        return None
    name = pathlib.PurePosixPath((rec.get("remote") or {}).get("path") or rec.get("path") or "").name
    ext = pathlib.PurePosixPath(name).suffix.lower()
    ctype = {"video": video.VIDEO_TYPES.get(ext), "audio": render.AUDIO_TYPES.get(ext)}.get(kind) or documents.content_type(name)
    size = rec.get("size") or (pathlib.Path(local).stat().st_size if local else None)
    pdf = f"{store.API}/recordings/{rid}/pdf" if kind == "document" and convert.rendition_path(cfg, rid).is_file() else None
    return PrimaryFile(name=name or None, kind=kind, size=size, content_type=ctype, download=f"{store.API}/recordings/{rid}/media", pdf=pdf)


def _out(db: DB, rows: list[dict[str, Any]], a: dict[str, Any]) -> list[ResourceFile]:
    people = (
        {
            p["id"]: p
            for p in db.rows(
                "SELECT record::id(id) AS id, email, name FROM account WHERE id IN $ids",
                ids=[R("account", x) for x in {f.get("created_by") for f in rows} if x],
            )
        }
        if rows
        else {}
    )
    return [
        ResourceFile(
            id=f["id"],
            role=f["role"],
            name=f["name"],
            size=f.get("size") or 0,
            content_type=f.get("content_type") or "application/octet-stream",
            language=f.get("language"),
            label=f.get("label"),
            description=f.get("description"),
            lines=f.get("lines"),
            timed=f.get("timed"),
            resource=f.get("resource"),
            public=bool(files.PART[f["role"]] and acc.is_open(a, files.PART[f["role"]])),
            created_at=f.get("created_at"),
            created_by=people.get(f.get("created_by"), {}).get("email"),
            created_by_name=people.get(f.get("created_by"), {}).get("name") or None,
            updated_at=f.get("updated_at"),
            download=f"{store.API}/recordings/{f['recording']}/files/{f['id']}/download",
        )
        for f in rows
    ]


def _signed(out: Any) -> Any:
    """The response with its download links signed."""
    return type(out).model_validate(sign_urls(out.model_dump(), full=True))


def _file(db: DB, rid: int, fid: int) -> dict[str, Any]:
    try:
        return files.get(db, rid, fid)
    except KeyError:
        raise HTTPException(404, "not found") from None


@router.get("")
def list_files(rid: int, acl: Acl, db: Db, cfg: Cfg) -> ResourceFiles:
    """The resource's primary file (its audio or video, if it has one) and its supplementary files, with signed links to
    download them."""
    rec = acl.recording(rid)
    can_change = acl.rank_in(rec["space"], rec.get("collection")) >= auth.ROLES["editor"]
    out = ResourceFiles(
        primary=_primary(db, cfg, rid, rec),
        files=_out(db, files.of(db, rid), acc.of(db, rid)),
        max_mb=cfg["server"]["max_upload_mb"],
        can_change=can_change,
    )
    return _signed(out)


async def _receive(request: Request, cfg: dict[str, Any]) -> pathlib.Path:
    """The request's body, written to a new file (413 past server.max_upload_mb)."""
    most = int(cfg["server"]["max_upload_mb"]) * 2**20
    too_big = HTTPException(413, f"Files can be up to {cfg['server']['max_upload_mb']} MB.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > most:
        raise too_big
    path = files.incoming(cfg)
    out = await run_in_threadpool(path.open, "wb")
    size = 0
    try:
        async for piece in request.stream():
            size += len(piece)
            if size > most:
                raise too_big
            if piece:
                await run_in_threadpool(out.write, piece)
    except ClientDisconnect:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "the file broke off; send it again") from None
    except OSError as e:
        path.unlink(missing_ok=True)
        if e.errno == errno.ENOSPC:
            raise HTTPException(507, "the server doesn't have room for this file") from None
        raise
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await run_in_threadpool(out.close)
    if not size:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "The file is empty.")
    return path


def _editor(acl: Access, rid: int) -> dict[str, Any]:
    return acl.recording(rid, "editor")


@router.post("", openapi_extra=CHUNK)
async def add_file(
    rid: int,
    request: Request,
    acl: Acl,
    user: Writer,
    db: Db,
    cfg: Cfg,
    role: FileRole = Query(description="what it is: transcripts, captions, translations and indexes are read into lines search finds"),
    name: str = Query(min_length=1, max_length=255, description="its file name, with the extension"),
    language: str | None = Query(None, max_length=40, description="what language it's in (en, pt-BR)"),
    label: str | None = Query(None, max_length=200),
) -> ResourceFile:
    """Add a file as the raw request body (application/octet-stream), up to `server.max_upload_mb`. Each role takes
    its own types: transcripts and translations .txt .md .json .jsonl .srt .vtt .docx .doc .pdf, captions .vtt .srt,
    indexes those and OHMS .xml, thumbnails .jpg .png .webp .gif, attachments anything. 400 when its contents can't
    be read as its role. Editors; audited as `file.add`."""
    await run_in_threadpool(_editor, acl, rid)
    path = await _receive(request, cfg)
    with domain_errors():
        f = await run_in_threadpool(files.add, db, cfg, rid, path, name, role, language, label, None, user.id)
    detail = {"file": f["id"], "name": f["name"], "role": f["role"], "size": f["size"]}
    await run_in_threadpool(auth.audit, db, user.as_audit(), "file.add", f"recording:{rid}", detail)
    return _signed(_out(db, [f], acc.of(db, rid))[0])


@router.patch("/{fid}")
def update_file(rid: int, fid: int, body: FileUpdate, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> ResourceFile:
    """Change a file's role, language, label or description (null clears the last three). A new role reads it again,
    so a file can't take a role whose type it isn't. Editors; audited as `file.update`."""
    _editor(acl, rid)
    _file(db, rid, fid)
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if "role" in changes and changes["role"] is None:
        raise HTTPException(400, "a file always has a role")
    with domain_errors():
        f, changed = files.update(db, cfg, rid, fid, changes)
    if changed:
        auth.audit(db, user.as_audit(), "file.update", f"recording:{rid}", {"file": fid, "name": f["name"], "changes": changed})
    return _signed(_out(db, [f], acc.of(db, rid))[0])


@router.delete("/{fid}")
def delete_file(rid: int, fid: int, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> Ok:
    """Delete a file and the lines read from it. Editors; audited as `file.delete`."""
    _editor(acl, rid)
    _file(db, rid, fid)
    f = files.delete(db, cfg, rid, fid)
    auth.audit(db, user.as_audit(), "file.delete", f"recording:{rid}", {"file": fid, "name": f["name"], "role": f["role"]})
    return Ok()


@router.get("/{fid}/download", response_class=FileResponse, responses={200: {"content": {"application/octet-stream": {}}}})
def download_file(rid: int, fid: int, acl: Acl, db: Db, cfg: Cfg) -> FileResponse:
    """The file as it was added, to save. Accepts a bearer token or a signed link (from the list)."""
    acl.recording(rid)
    f = _file(db, rid, fid)
    path = files.path_of(cfg, f)
    if not path.is_file():
        raise HTTPException(404, "the file is missing on the server")
    return FileResponse(path, media_type=files.served_type(f), filename=f["name"], headers=files.HEADERS)


@router.get("/{fid}/lines")
def list_file_lines(
    rid: int,
    fid: int,
    acl: Acl,
    db: Db,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
) -> FileLines:
    """The lines read from a transcript, captions, translation or index, in order."""
    acl.recording(rid)
    f = _file(db, rid, fid)
    rows = files.lines_of(db, fid, offset, limit)
    return FileLines(total=f.get("lines") or 0, lines=[FileLine.model_validate(r) for r in rows])
