"""Byte-range responses, so players can seek in audio and video."""

from __future__ import annotations

import os
import pathlib
import re
import urllib.parse
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.domain import keyring, render

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


def range_response(size: int, request: Request, ctype: str, body: Callable[[int, int], Iterator[bytes]]) -> Response:
    r = byte_range(size, request)
    if r is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    start, end, status = r
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(start, end), status_code=status, media_type=ctype, headers=headers)


def file_response(
    path: str | os.PathLike[str], request: Request, ctype: str | None = None, db: Any = None, cfg: dict[str, Any] | None = None
) -> Response:
    """A file with byte ranges; an encrypted one (pass the database and configuration) is decrypted as it's sent,
    only the chunks a range touches."""
    encrypted = db is not None and keyring.is_encrypted(path)

    def body(start: int, end: int) -> Iterator[bytes]:
        with keyring.Reader(db, cfg, path) if encrypted else open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    guessed = render.AUDIO_TYPES.get(pathlib.Path(path).suffix.lower(), "application/octet-stream")
    size = keyring.plain_size(db, cfg, path) if encrypted else os.path.getsize(path)
    return range_response(size, request, ctype or guessed, body)


def stored_file(
    db: Any,
    cfg: dict[str, Any],
    path: str | os.PathLike[str],
    ctype: str,
    filename: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """A file to save, like FileResponse; an encrypted one is decrypted as it's sent."""
    if not keyring.is_encrypted(path):
        return FileResponse(path, media_type=ctype, filename=filename, headers=headers)
    size = keyring.plain_size(db, cfg, path)

    def body() -> Iterator[bytes]:
        with keyring.Reader(db, cfg, path) as f:
            while chunk := f.read(CHUNK):
                yield chunk

    out = {**(headers or {}), "Content-Length": str(size)}
    if filename:
        quoted = urllib.parse.quote(filename)
        out["Content-Disposition"] = (
            f"attachment; filename*=utf-8''{quoted}" if quoted != filename else f'attachment; filename="{filename}"'
        )
    return StreamingResponse(body(), media_type=ctype, headers=out)
