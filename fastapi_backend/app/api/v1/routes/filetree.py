"""Assets as folders and files (docs/file-tree.md): browse the tree the S3, WebDAV, SMB and NFS servers serve, and
read its files. Everyone sees what the web app shows them; the tree only reads."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.api.deps import Cfg, CurrentUser, Db, Principal
from app.api.streaming import LOCKED, byte_range
from app.domain import filetree, keyring
from app.domain.store import DB
from app.schemas.filetree import TreeEntry, TreeFolder

router = APIRouter(prefix="/tree", tags=["tree"])
PATH = Query("", max_length=4000, description="a folder or file, from the top: namespace/collection/…")


def tree_of(db: DB, cfg: dict[str, Any], user: Principal) -> filetree.Tree:
    return filetree.Tree(db, cfg, filetree.Viewer(user.admin, user.roles, user.collections))


@router.get("")
def list_folder(user: CurrentUser, db: Db, cfg: Cfg, path: str = PATH) -> TreeFolder:
    """A folder's files and folders: '' lists your namespaces, then their collections, each holding its assets'
    files. A file answers 400 (read it at /tree/file)."""
    t = tree_of(db, cfg, user)
    try:
        entries = t.list(path)
    except filetree.NotFound:
        raise HTTPException(404, "not found") from None
    except filetree.NotAFolder:
        raise HTTPException(400, "that's a file: read it at /tree/file") from None
    return TreeFolder(path="/".join(t.split(path)), entries=[TreeEntry(**e.out()) for e in entries])


@router.get(
    "/file",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}, 206: {"description": "a byte range"}},
)
def read_file(request: Request, user: CurrentUser, db: Db, cfg: Cfg, path: str = PATH) -> Response:
    """A file of the tree, with byte ranges."""
    t = tree_of(db, cfg, user)
    try:
        e = t.stat(path)
    except filetree.NotFound:
        raise HTTPException(404, "not found") from None
    if e.folder:
        raise HTTPException(400, "that's a folder: list it at /tree")
    r = byte_range(e.size, request) if e.size else (0, -1, 200)
    if r is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{e.size}"})
    start, end, status = r
    try:
        if e.encrypted:  # a locked vault says so now, not halfway through the body
            keyring.plain_size(db, cfg, e.path)
    except keyring.Locked:
        raise HTTPException(423, LOCKED) from None
    headers = {"Accept-Ranges": "bytes", "ETag": f'"{e.version}"', "Content-Length": str(max(0, end - start + 1))}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{e.size}"
    return StreamingResponse(e.read(db, cfg, start, end), status_code=status, media_type=e.content_type, headers=headers)
