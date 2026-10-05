"""Byte-range responses, so players can seek in audio and video."""

from __future__ import annotations

import os
import pathlib
import re
import urllib.parse
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.domain import keyring, render

LOCKED = "this namespace is a locked vault: its owners unlock it with a passkey, on its page in Admin, Namespaces"

CHUNK = 1 << 16


def byte_range(size: int, request: Request) -> tuple[int, int, int] | None:
    """(start, end, status) for the request's Range header; None when it can't be satisfied."""
    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range")
    if rng:
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        if not m or m.groups() == ("", ""):
            return None
        a, b = m.groups()
        if a == "":
            start = max(0, size - int(b))
        else:
            start, end = int(a), min(size - 1, int(b)) if b else size - 1
        if start > end or start >= size:
            return None
        status = 206
    return start, end, status


def range_response(
    size: int, request: Request, ctype: str, body: Callable[[int, int], Iterator[bytes]], headers: dict[str, str] | None = None
) -> Response:
    r = byte_range(size, request)
    if r is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    start, end, status = r
    out = {**(headers or {}), "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
    if status == 206:
        out["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(start, end), status_code=status, media_type=ctype, headers=out)


def _plain_size(db: Any, cfg: dict[str, Any] | None, path: str | os.PathLike[str]) -> int:
    """An encrypted file's plain size; a locked vault or a damaged file says so instead of failing as a 500."""
    try:
        return keyring.plain_size(db, cfg, path)
    except keyring.Locked:
        raise HTTPException(423, LOCKED) from None
    except keyring.Damaged:
        raise HTTPException(500, "this file is damaged on the server and can't be opened") from None


def _encrypted_body(db: Any, cfg: dict[str, Any] | None, path: str | os.PathLike[str]) -> Callable[[int, int], Iterator[bytes]]:
    def body(start: int, end: int) -> Iterator[bytes]:
        with keyring.Reader(db, cfg, path) as f:  # only the chunks the range touches are decrypted
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return body


def file_response(
    path: str | os.PathLike[str], request: Request, ctype: str | None = None, db: Any = None, cfg: dict[str, Any] | None = None
) -> Response:
    """A file with byte ranges; an encrypted one (pass the database and configuration) is decrypted as it's sent."""
    guessed = render.AUDIO_TYPES.get(pathlib.Path(path).suffix.lower(), "application/octet-stream")
    if db is not None and keyring.is_encrypted(path):
        return range_response(_plain_size(db, cfg, path), request, ctype or guessed, _encrypted_body(db, cfg, path))

    def body(start: int, end: int) -> Iterator[bytes]:
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return range_response(os.path.getsize(path), request, ctype or guessed, body)


def stored_file(
    db: Any,
    cfg: dict[str, Any],
    path: str | os.PathLike[str],
    request: Request,
    ctype: str,
    filename: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """A file to save, like FileResponse; an encrypted one is decrypted as it's sent, with byte ranges."""
    if not keyring.is_encrypted(path):
        return FileResponse(path, media_type=ctype, filename=filename, headers=headers)
    out = dict(headers or {})
    if filename:
        quoted = urllib.parse.quote(filename)
        out["Content-Disposition"] = (
            f"attachment; filename*=utf-8''{quoted}" if quoted != filename else f'attachment; filename="{filename}"'
        )
    return range_response(_plain_size(db, cfg, path), request, ctype, _encrypted_body(db, cfg, path), out)


def picture_response(db: Any, cfg: dict[str, Any], path: str | os.PathLike[str], headers: dict[str, str] | None = None) -> Response:
    """A frame or a page image to show (JPEG): an encrypted one decrypted whole (they're small), a locked vault's 423."""
    if not keyring.is_encrypted(path):
        return FileResponse(path, media_type="image/jpeg", headers=headers)
    return Response(plain_bytes(db, cfg, path), media_type="image/jpeg", headers=headers)


def plain_bytes(db: Any, cfg: dict[str, Any] | None, path: str | os.PathLike[str]) -> bytes:
    """A small file's plain bytes; a locked vault (423) or a damaged file (500) says so."""
    try:
        return keyring.read_plain(db, cfg, path)
    except keyring.Locked:
        raise HTTPException(423, LOCKED) from None
    except keyring.Damaged:
        raise HTTPException(500, "this file is damaged on the server and can't be opened") from None
