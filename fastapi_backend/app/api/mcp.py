"""Lens as an MCP server (docs/mcp.md): agents search, read and cite the archive as the person who signed them in.

One endpoint, ``/mcp``, speaks the Model Context Protocol over Streamable HTTP, without sessions, answering every
request on its own POST as JSON. Both eras of the protocol are served:

* **Handshake era** (2024-11-05 to 2025-11-25): the client starts with ``initialize``, then sends requests with the
  version it got in ``MCP-Protocol-Version``. Notifications and responses get 202; a 2025-03-26 client may batch.
* **Per-request era** (2026-07-28): no handshake. Each request carries its version and the client's capabilities in
  ``params._meta``, and repeats the version, the method and (for ``tools/call``) the tool's name in the
  ``MCP-Protocol-Version``, ``Mcp-Method`` and ``Mcp-Name`` headers, which must agree with the body.
  ``server/discover`` says what this server speaks. Results say ``resultType``; protocol errors have HTTP statuses.

The era is the ``MCP-Protocol-Version`` header's: none or a handshake version is the first, anything else the second
(where an unknown version is refused with the versions there are). Nothing streams from the server, so GET (the
server-to-client stream) and DELETE (ending a session) are 405.

Who is asking comes from the ``Authorization: Bearer`` header, as for the API: an app's OAuth access token (``lo_…``,
what MCP clients get by signing in through app/api/v1/routes/oauth.py), an API token or a session. Without one the
answer is a 401 pointing at ``/.well-known/oauth-protected-resource/mcp`` (RFC 9728), where clients find out how to
sign in. The tools only read (app/api/mcp_tools.py), and only what the person can read through the API.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.api import mcp_tools
from app.api.deps import Access, Db, optional_user
from app.api.v1.routes.oauth import MCP, public_base, web_app_base
from app.config import settings
from app.domain import __version__

log = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False, tags=["mcp"])

PATH = MCP
# newest first: an initialize asking for another version gets the newest (the client then decides whether to go on)
HANDSHAKE = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
PER_REQUEST = ("2026-07-28",)
MAX_BODY = 1_000_000
MAX_BATCH = 20  # messages in one batch (2025-03-26), each of which may search
SERVER_INFO = {"name": "lens", "title": settings.PROJECT_NAME, "version": __version__}
CAPABILITIES = {"tools": {"listChanged": False}}
INSTRUCTIONS = (
    "Lens is a private archive of recorded speech, video, documents and images, with transcripts, speakers, the people, "
    "organisations and things mentioned (entities) and a knowledge graph between them. You see what the person who "
    "signed you in can read. Find moments with search, read a recording with get_recording and get_transcript, and "
    "follow entities and the graph with list_entities, get_entity, explore_graph and find_path. When you quote the "
    "archive, cite each quote with the link given next to it (or ask cite for one): it opens the recording at that moment."
)
# the per-request era's envelope (params._meta) and headers
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER = "io.modelcontextprotocol/serverInfo"
H_VERSION, H_METHOD, H_NAME = "mcp-protocol-version", "mcp-method", "mcp-name"

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
HEADER_MISMATCH, UNSUPPORTED_VERSION = -32020, -32022
# the per-request era answers protocol errors with these statuses (other errors and results are 200)
STATUS = {
    PARSE_ERROR: 400,
    INVALID_REQUEST: 400,
    INVALID_PARAMS: 400,
    HEADER_MISMATCH: 400,
    UNSUPPORTED_VERSION: 400,
    METHOD_NOT_FOUND: 404,
}


class Fault(Exception):
    """A JSON-RPC error to answer with."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data

    def error(self, mid: Any) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return {"jsonrpc": "2.0", "id": mid, "error": err}


def _unsupported(requested: Any) -> Fault:
    return Fault(UNSUPPORTED_VERSION, "Unsupported protocol version", {"supported": list(PER_REQUEST), "requested": requested})


def _origin_ok(request: Request) -> bool:
    """Browsers say where a page that calls us came from: only Lens's own addresses and the configured CORS origins
    may (MCP's guard against DNS rebinding). Clients that aren't browsers send no Origin."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    if origin == "null":
        return False
    mine = {public_base(request), web_app_base(request), settings.FRONTEND_URL.rstrip("/"), *(o.rstrip("/") for o in settings.CORS_ORIGINS)}
    if origin.rstrip("/") in mine:
        return True
    host = (urllib.parse.urlsplit(origin).hostname or "").lower()
    allowed = [h.lower() for h in request.app.state.settings.current()["server"]["allowed_hosts"]]
    return bool(host) and "*" not in allowed and host in allowed


def dispatch(ctx: mcp_tools.Context, method: Any, params: Any, per_request: bool) -> dict[str, Any]:
    """The result of one request, in either era; Fault for a protocol error."""
    if not isinstance(params, dict):
        raise Fault(INVALID_PARAMS, "params is an object")
    if method == "initialize" and not per_request:
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if asked in HANDSHAKE else HANDSHAKE[0],
            "capabilities": CAPABILITIES,
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        }
    if method == "server/discover" and per_request:
        return {"supportedVersions": [*PER_REQUEST, *HANDSHAKE], "capabilities": CAPABILITIES, "instructions": INSTRUCTIONS}
    if method == "ping" and not per_request:  # the per-request era has no ping
        return {}
    if method == "tools/list":
        return {"tools": mcp_tools.listing(ctx.cfg)}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or name not in mcp_tools.TOOLS:
            raise Fault(INVALID_PARAMS, f"unknown tool: {name}")
        if not isinstance(args, dict):
            raise Fault(INVALID_PARAMS, "arguments is an object")
        try:
            return mcp_tools.call(ctx, name, args)
        except Exception:
            log.exception("MCP tool %s failed", name)
            raise Fault(INTERNAL_ERROR, "the tool failed; the server log has the details") from None
    raise Fault(METHOD_NOT_FOUND, f"method not found: {method}")


def handle(message: Any, ctx: mcp_tools.Context) -> dict[str, Any] | None:
    """The handshake era: the answer to one JSON-RPC message, or None for notifications and responses."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return Fault(INVALID_REQUEST, "not a JSON-RPC 2.0 message").error(None)
    if "method" not in message or "id" not in message:
        return None  # a response (we never ask the client anything) or a notification: nothing to say
    mid = message["id"]
    if not isinstance(mid, (str, int)) or isinstance(mid, bool):
        return Fault(INVALID_REQUEST, "id is a string or a number").error(None)
    try:
        return {"jsonrpc": "2.0", "id": mid, "result": dispatch(ctx, message["method"], message.get("params") or {}, False)}
    except Fault as f:
        return f.error(mid)


def _header_value(value: str | None) -> str | None:
    """A header's value, decoding the =?base64?…?= form clients use for what isn't plain ASCII (None if it's broken)."""
    m = re.fullmatch(r"=\?base64\?([A-Za-z0-9+/]*={0,2})\?=", value or "")
    if not m:
        return value
    try:
        raw = base64.b64decode(m.group(1), validate=True)
        return raw.decode() if base64.b64encode(raw).decode() == m.group(1) else None
    except (binascii.Error, UnicodeDecodeError):
        return None


def _envelope(message: dict[str, Any], request: Request) -> None:
    """The per-request era's checks on a request, in the protocol's order: the envelope in params._meta, the
    headers agreeing with the body, then the version."""
    meta = (message.get("params") or {}).get("_meta") if isinstance(message.get("params"), dict) else None
    if not isinstance(meta, dict):
        raise Fault(INVALID_PARAMS, f"params._meta must be an object carrying {META_VERSION!r} and {META_CAPABILITIES!r}")
    missing = [k for k in (META_VERSION, META_CAPABILITIES) if k not in meta]
    if missing:
        raise Fault(INVALID_PARAMS, f"params._meta is missing {', '.join(missing)}")
    for h in (H_VERSION, H_METHOD, H_NAME):
        if len(request.headers.getlist(h)) > 1:
            raise Fault(HEADER_MISMATCH, f"{h} header appears more than once")
    version = meta[META_VERSION]
    if request.headers.get(H_VERSION) != version:
        raise Fault(HEADER_MISMATCH, f"{H_VERSION} header does not match the request envelope's protocol version")
    if request.headers.get(H_METHOD) != message["method"]:
        raise Fault(HEADER_MISMATCH, f"{H_METHOD} header does not match the request body's method")
    if message["method"] == "tools/call":
        name = message["params"].get("name")
        if name is not None and _header_value(request.headers.get(H_NAME)) != name:
            raise Fault(HEADER_MISMATCH, f"{H_NAME} header does not match the request body's 'name' parameter")
    if not isinstance(version, str):
        raise Fault(INVALID_PARAMS, "the protocol-version envelope value must be a string")
    if version not in PER_REQUEST:
        raise _unsupported(version)


def handle_per_request(message: Any, request: Request, ctx: mcp_tools.Context) -> tuple[dict[str, Any] | None, int]:
    """The per-request era: (the answer, its HTTP status), or (None, 202) for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        f = Fault(INVALID_REQUEST, "Body must be a single JSON-RPC request or notification object")
        return f.error(None), 400
    if "id" not in message:
        version = request.headers.get(H_VERSION)
        if version not in PER_REQUEST:
            return _unsupported(version).error(None), 400
        return None, 202  # this era defines no notifications to the server; they're acknowledged and dropped
    mid = message["id"]
    if not isinstance(mid, (str, int)) or isinstance(mid, bool):
        return Fault(INVALID_REQUEST, "id is a string or a number").error(None), 400
    try:
        _envelope(message, request)
        result = dispatch(ctx, message["method"], message.get("params") or {}, True)
    except Fault as f:
        return f.error(mid), STATUS.get(f.code, 200)
    result.setdefault("resultType", "complete")
    if message["method"] in ("tools/list", "server/discover"):
        result.update(ttlMs=0, cacheScope="private")  # what the tools are doesn't change, but who may use them does
    result["_meta"] = {**result.get("_meta", {}), META_SERVER: SERVER_INFO}
    return {"jsonrpc": "2.0", "id": mid, "result": result}, 200


def _audience_ok(request: Request, resource: str | None) -> bool:
    """An app's token that names the server it is for (RFC 8707) must name this one: Lens, or its MCP server, on any
    address Lens is reached at. Tokens that name none (API tokens, apps that didn't say) are Lens's."""
    if resource is None:
        return True
    bases = {public_base(request), web_app_base(request), settings.FRONTEND_URL}
    return resource.rstrip("/") in {b.rstrip("/") + end for b in bases for end in ("", PATH)}


async def _read(request: Request) -> bytes | None:
    """The body, or None when it's more than MAX_BODY: told by Content-Length, or found while reading, so a large one
    is never held whole."""
    try:
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return None
    except ValueError:
        pass  # a malformed length: reading tells
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            return None
    return bytes(body)


@router.post(PATH)
async def post(request: Request, db: Db) -> Response:
    if not _origin_ok(request):
        return JSONResponse(Fault(INVALID_REQUEST, "this origin may not use the MCP server").error(None), status_code=403)
    accept = request.headers.get("accept", "")
    if accept and not any(t in accept for t in ("application/json", "application/*", "*/*")):
        return Response(status_code=406)  # every answer here is JSON
    user = await run_in_threadpool(optional_user, request, db)  # the database blocks
    if not user:
        raise HTTPException(401, "sign in first", headers={"WWW-Authenticate": "Bearer"})
    if not _audience_ok(request, user.resource):
        # a token an app was given for another server (RFC 8707) isn't one to use here; the app asks again for this one
        raise HTTPException(401, "this token was given for another server", headers={"WWW-Authenticate": "Bearer"})
    body = await _read(request)
    if body is None:
        return JSONResponse(Fault(INVALID_REQUEST, "the message is too large").error(None), status_code=413)
    try:
        message = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return JSONResponse(Fault(PARSE_ERROR, "Parse error").error(None), status_code=400)
    ctx = mcp_tools.Context(request, db, request.app.state.settings.current(), Access(request, db, user), web_app_base(request))
    version = request.headers.get(H_VERSION)
    if version is not None and version not in HANDSHAKE:
        answer, status = await run_in_threadpool(handle_per_request, message, request, ctx)
        return JSONResponse(answer, status_code=status) if answer is not None else Response(status_code=status)
    if isinstance(message, list):
        if not message:
            return JSONResponse(Fault(INVALID_REQUEST, "an empty batch").error(None), status_code=400)
        if len(message) > MAX_BATCH:
            return JSONResponse(Fault(INVALID_REQUEST, f"a batch may hold {MAX_BATCH} messages at most").error(None), status_code=400)
        answers = [a for m in message if (a := await run_in_threadpool(handle, m, ctx)) is not None]
        return JSONResponse(answers) if answers else Response(status_code=202)
    answer = await run_in_threadpool(handle, message, ctx)
    return JSONResponse(answer) if answer is not None else Response(status_code=202)


@router.get(PATH)
@router.delete(PATH)
def no_stream() -> Response:
    """No stream from the server and no sessions to end: every answer comes back on its POST."""
    return Response(status_code=405, headers={"Allow": "POST"})
