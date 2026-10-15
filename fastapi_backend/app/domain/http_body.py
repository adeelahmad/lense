"""Request bodies as the file shares receive them (s3.py, webdav.py): streamed to a file, never held in memory, as
Content-Length bytes, HTTP/1.1 chunked transfer coding, or S3's aws-chunked coding (STREAMING-… payloads, whose
chunk signatures are checked when they're signed). Each body is checked against what the client said about it:
its length, the SHA-256 it signed, its Content-MD5, and an x-amz-checksum-crc32 or -sha256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import zlib
from collections.abc import Iterator

PIECE = 1 << 16
LINE_MAX = 8192
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class BodyError(ValueError):
    """A body that's malformed, cut short, too large, or isn't what was signed. `code` names it as S3 does."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class _Stream:
    """Bytes from an iterator of pieces, read by length or by line."""

    def __init__(self, pieces: Iterator[bytes]):
        self.pieces, self.buf = pieces, b""

    def _fill(self) -> bool:
        piece = next(self.pieces, b"")
        self.buf += piece
        return bool(piece)

    def readline(self) -> bytes:
        while b"\n" not in self.buf:
            if len(self.buf) > LINE_MAX:
                raise BodyError("IncompleteBody", "a chunk's header line is too long")
            if not self._fill():
                raise BodyError("IncompleteBody", "the body ended early")
        line, _, self.buf = self.buf.partition(b"\n")
        return line.rstrip(b"\r")

    def read(self, n: int) -> Iterator[bytes]:
        """Exactly `n` bytes, a piece at a time."""
        while n > 0:
            if not self.buf and not self._fill():
                raise BodyError("IncompleteBody", "the body ended early")
            out, self.buf = self.buf[:n], self.buf[n:]
            n -= len(out)
            yield out


def _fixed(rfile, length: int) -> Iterator[bytes]:
    while length > 0:
        piece = rfile.read(min(PIECE, length))
        if not piece:
            raise BodyError("IncompleteBody", "the body ended before Content-Length bytes")
        length -= len(piece)
        yield piece


def _chunk_size(line: bytes) -> tuple[int, dict[str, str]]:
    head, *params = line.decode("latin-1").split(";")
    try:
        size = int(head.strip(), 16)
    except ValueError:
        raise BodyError("IncompleteBody", "a chunk's size isn't a hex number") from None
    return size, dict(p.strip().split("=", 1) for p in params if "=" in p)


def _http_chunked(rfile) -> Iterator[bytes]:
    s = _Stream(iter(lambda: rfile.read1(PIECE) if hasattr(rfile, "read1") else rfile.read(1), b""))
    while True:
        size, _ = _chunk_size(s.readline())
        if size == 0:
            while s.readline():  # trailers, then the blank line
                pass
            return
        yield from s.read(size)
        if s.readline():
            raise BodyError("IncompleteBody", "a chunk isn't followed by CRLF")


class Signed:
    """What a STREAMING-AWS4-HMAC-SHA256-PAYLOAD body's chunks are signed with: the signing key, the request's time and
    scope, and the seed signature (the request's own)."""

    def __init__(self, key: bytes, amz: str, scope: str, seed: str):
        self.key, self.amz, self.scope, self.prev = key, amz, scope, seed

    def check(self, sig: str, chunk_sha256: str) -> None:
        to_sign = "\n".join(["AWS4-HMAC-SHA256-PAYLOAD", self.amz, self.scope, self.prev, EMPTY_SHA256, chunk_sha256])
        want = hmac.new(self.key, to_sign.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, sig or ""):
            raise BodyError("SignatureDoesNotMatch", "a chunk's signature doesn't match", 403)
        self.prev = want


def _aws_chunked(raw: Iterator[bytes], signed: Signed | None, trailer: dict[str, str]) -> Iterator[bytes]:
    s = _Stream(raw)
    while True:
        size, params = _chunk_size(s.readline())
        h = hashlib.sha256() if signed else None
        for piece in s.read(size):
            if h:
                h.update(piece)
            yield piece
        if signed:
            signed.check(params.get("chunk-signature", ""), h.hexdigest())  # type: ignore[union-attr]
        if size == 0:
            while line := s.readline():  # trailers (a checksum, its signature), then the blank line
                k, _, v = line.decode("latin-1").partition(":")
                trailer[k.strip().lower()] = v.strip()
            return
        if s.readline():
            raise BodyError("IncompleteBody", "a chunk isn't followed by CRLF")


def receive(rfile, headers: dict[str, str], dest, limit: int, signed: Signed | None = None) -> tuple[int, str]:
    """Stream a request's body into the file `dest` (lowercased `headers`): (its size, its MD5 in hex). BodyError when
    it's malformed, over `limit` bytes, or not what the client said it is."""
    te = headers.get("transfer-encoding", "").lower()
    if "chunked" in te:
        raw = _http_chunked(rfile)
    elif headers.get("content-length", "").strip().isdigit():
        length = int(headers["content-length"])
        if length > limit and not headers.get("x-amz-content-sha256", "").startswith("STREAMING-"):
            raise BodyError("EntityTooLarge", "the file is larger than this server takes")
        raw = _fixed(rfile, length)
    else:
        raise BodyError("MissingContentLength", "send Content-Length, or the body chunked", 411)
    payload = headers.get("x-amz-content-sha256", "")
    trailer: dict[str, str] = {}
    if payload.startswith("STREAMING-"):
        if payload.startswith("STREAMING-AWS4-HMAC-SHA256-PAYLOAD") and signed is None:
            raise BodyError("InvalidRequest", "a signed streaming body needs a signed request", 403)
        body = _aws_chunked(raw, signed if payload.startswith("STREAMING-AWS4-HMAC-SHA256-PAYLOAD") else None, trailer)
    else:
        body = raw
    want = {k: headers[k] for k in ("x-amz-checksum-crc32", "x-amz-checksum-sha256") if headers.get(k)}
    named = {t.strip().lower() for t in headers.get("x-amz-trailer", "").split(",") if t.strip()}
    sha = hashlib.sha256() if len(payload) == 64 or "x-amz-checksum-sha256" in want or "x-amz-checksum-sha256" in named else None
    crc = 0 if "x-amz-checksum-crc32" in want or "x-amz-checksum-crc32" in named else None
    md5, size = hashlib.md5(usedforsecurity=False), 0
    with open(dest, "wb") as f:
        for piece in body:
            size += len(piece)
            if size > limit:
                raise BodyError("EntityTooLarge", "the file is larger than this server takes")
            md5.update(piece)
            if sha:
                sha.update(piece)
            if crc is not None:
                crc = zlib.crc32(piece, crc)
            f.write(piece)
    want.update({k: v for k, v in trailer.items() if k in ("x-amz-checksum-crc32", "x-amz-checksum-sha256")})
    if headers.get("x-amz-decoded-content-length", "").isdigit() and int(headers["x-amz-decoded-content-length"]) != size:
        raise BodyError("IncompleteBody", "the body isn't as long as x-amz-decoded-content-length says")
    if len(payload) == 64 and sha and not hmac.compare_digest(sha.hexdigest(), payload.lower()):
        raise BodyError("XAmzContentSHA256Mismatch", "the body isn't what was signed")
    if headers.get("content-md5") and not hmac.compare_digest(base64.b64encode(md5.digest()).decode(), headers["content-md5"].strip()):
        raise BodyError("BadDigest", "the body doesn't match its Content-MD5")
    if "x-amz-checksum-sha256" in want and sha and base64.b64encode(sha.digest()).decode() != want["x-amz-checksum-sha256"]:
        raise BodyError("BadDigest", "the body doesn't match its x-amz-checksum-sha256")
    if (
        "x-amz-checksum-crc32" in want
        and crc is not None
        and base64.b64encode(crc.to_bytes(4, "big")).decode() != want["x-amz-checksum-crc32"]
    ):
        raise BodyError("BadDigest", "the body doesn't match its x-amz-checksum-crc32")
    return size, md5.hexdigest()


def drain(rfile, headers: dict[str, str], most: int = 1 << 20) -> bool:
    """Read and drop a small body (up to `most` bytes) so the connection can carry on; False when it can't be (too
    large, chunked, or the client waits for 100 Continue): close the connection instead."""
    if headers.get("expect", "").lower() == "100-continue" or "chunked" in headers.get("transfer-encoding", "").lower():
        return False
    n = int(headers.get("content-length") or 0) if (headers.get("content-length") or "0").isdigit() else most + 1
    if n > most:
        return False
    rfile.read(n)
    return True
