"""An MCP server for agents (docs/mcp.md): ``POST /mcp`` speaks the Model Context Protocol over streamable HTTP, so
that an assistant such as Claude or Cursor can search the archive, read what's in it, find its way through namespaces
and collections, and import and process things, as the person who connected it.

It authenticates like the API (``Authorization: Bearer``: an OAuth access token, an API key or a session's token) and
answers 401 with where to sign in (RFC 9728) otherwise. Every tool is a request to the API's own routes, made inside
this process with the caller's token: what a tool may see and do is exactly what the HTTP API allows that caller,
roles on namespaces and collections, read-only tokens and all, and is audited the same way.

The server keeps no session: each request stands alone (JSON in, JSON out; no server-sent stream), which the
protocol allows and which works behind any proxy and with several API processes.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from app.api.deps import Principal, _principal, get_db
from app.api.v1.routes.oauth import public_base
from app.domain import __version__, store

router = APIRouter(include_in_schema=False, tags=["mcp"])

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # the newest first; a client asking for another gets the newest
URI = "lens://resource/"
INSTRUCTIONS = (
    "Lens is an archive of recordings (audio and video with transcripts), documents and images, organised in "
    "namespaces and, inside them, collections. Start with list_namespaces, find things with search (it finds what "
    "was said, shown or written; with meaning=true also passages that say the same in other words, where the archive "
    "has that switched on) or list_resources, then read them with get_transcript (recordings) or get_pages "
    "(documents and images). Times are seconds from the start; pages count from 1. You act as the person who "
    "connected you and see only what they may see."
)
Args = dict[str, Any]
_S, _I, _B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}


class ToolError(Exception):
    """Something the caller can act on (a wrong id, no permission): reported in the tool's result, as MCP asks."""


def _tc(ms: int | float | None) -> str:
    return store.tc(int(ms or 0))


def _plain(snippet: str) -> str:
    return html.unescape(re.sub(r"</?mark>", "", snippet or ""))


class Api:
    """The API, as this caller: requests to its own routes inside this process, with the caller's token."""

    def __init__(self, request: Request):
        headers = {"authorization": request.headers.get("authorization", "")}
        for name in ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"):
            if request.headers.get(name):
                headers[name] = request.headers[name]
        peer = (request.client.host, request.client.port) if request.client else ("127.0.0.1", 0)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=request.app, client=peer),
            base_url=f"{request.url.scheme}://{request.headers.get('host', 'localhost')}/api/v1",
            headers=headers,
        )

    async def call(self, method: str, path: str, params: Args | None = None, body: Args | None = None) -> httpx.Response:
        clean = {k: v for k, v in (params or {}).items() if v not in (None, "", [])}
        r = await self.client.request(method, path, params=clean, json=body)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            if isinstance(detail, list):
                detail = "; ".join(f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail)
            raise ToolError(f"{detail or r.text[:300] or 'the request failed'} ({r.status_code})")
        return r

    async def get(self, path: str, **params: Any) -> Any:
        return (await self.call("GET", path, params)).json()

    async def post(self, path: str, body: Args) -> Any:
        return (await self.call("POST", path, body={k: v for k, v in body.items() if v is not None})).json()


# ---------- tools ----------
async def t_list_namespaces(api: Api, a: Args) -> Any:
    rows = await api.get("/namespaces")
    keys = ("name", "role", "recordings", "speakers", "partial")
    return {"namespaces": [{k: n.get(k) for k in keys} | {"resources": n.get("recordings")} for n in rows]}


async def t_list_collections(api: Api, a: Args) -> Any:
    rows = await api.get(f"/namespaces/{a['namespace']}/collections")
    keys = ("id", "name", "description", "parent", "path", "default", "role")
    return {"collections": [{k: c.get(k) for k in keys} | {"resources": c.get("recordings"), "with_inside": c.get("total")} for c in rows]}


def _row(r: Args) -> Args:
    out = {k: r.get(k) for k in ("id", "title", "namespace", "collection", "collection_name", "recorded_at", "status", "tags", "language")}
    out["kind"] = r.get("media_kind") or r.get("source")
    if r.get("pages"):
        out["pages"] = r["pages"]
    if r.get("duration_ms"):
        out["duration"] = _tc(r["duration_ms"])
    if r.get("speakers"):
        out["speakers"] = r["speakers"]
    return {k: v for k, v in out.items() if v not in (None, [], "")}


async def t_list_resources(api: Api, a: Args) -> Any:
    r = await api.call(
        "GET",
        "/resources",
        {
            "ns": a.get("namespace"),
            "collection": a.get("collection"),
            "q": a.get("title_contains"),
            "media": a.get("kind"),
            "tag": a.get("tag"),
            "from": a.get("from"),
            "to": a.get("to"),
            "limit": min(int(a.get("limit") or 25), 100),
            "offset": int(a.get("offset") or 0),
        },
    )
    rows = r.json()
    return {"total": int(r.headers.get("x-total-count") or len(rows)), "resources": [_row(x) for x in rows]}


async def t_search(api: Api, a: Args) -> Any:
    res = await api.get(
        "/search",
        q=a["query"],
        ns=a.get("namespace"),
        speaker=a.get("speaker_id"),
        emotion=a.get("emotion"),
        recording=a.get("resource_id"),
        object=a.get("object"),
        semantic=bool(a.get("meaning", True)),
        limit=min(int(a.get("limit") or 20), 50),
        offset=int(a.get("offset") or 0),
    )
    hits = []
    for h in res["hits"]:
        paged = h.get("page") is not None
        hit = {
            "resource_id": h["recording_id"],
            "title": h.get("title"),
            "namespace": h.get("namespace"),
            "at": f"p. {h['page'] + 1}" if paged else (_tc(h["t0"]) if h.get("t0") is not None else None),
            "seconds": None if paged or h.get("t0") is None else round(h["t0"] / 1000, 1),
            "page": h["page"] + 1 if paged else None,
            "speaker": h.get("speaker"),
            "text": _plain(h["snippet"]),
            "found": h.get("source"),
            "match": h.get("match"),
        }
        hits.append({k: v for k, v in hit.items() if v is not None})
    by_meaning = (res.get("semantic") or {}).get("meaning")
    return {"query": res.get("query"), "total": res["total"], "hits": hits} | (
        {"found_by_meaning": by_meaning} if by_meaning is not None else {}
    )


async def t_get_resource(api: Api, a: Args) -> Any:
    rid = int(a["id"])
    d = await api.get(f"/resources/{rid}")
    p = await api.get(f"/resources/{rid}/player")
    out = {k: d.get(k) for k in ("id", "title", "namespace", "status", "tags", "access", "role", "recorded_at", "language", "source")}
    out["collection"] = [c["name"] for c in d.get("collection_path") or []]
    out["kind"] = (d.get("media") or {}).get("kind") or d.get("source")
    out["duration"] = _tc(p.get("duration_ms")) if p.get("duration_ms") else None
    out["pages"] = len(p["pages"]) if p.get("pages") else None
    out["speakers"] = [s.get("name") for s in d.get("speakers") or []]
    out["summary"] = p.get("summary") or d.get("summary")
    out["chapters"] = [
        {"title": s.get("title"), "at": _tc(s.get("t0")), "seconds": round((s.get("t0") or 0) / 1000, 1)} for s in p.get("sections") or []
    ]
    out["entities"] = [{"name": e.get("name"), "type": e.get("type")} for e in (p.get("entities") or [])[:40]]
    out["lines"] = len(p.get("segments") or [])
    return {k: v for k, v in out.items() if v not in (None, [], "")}


async def t_get_transcript(api: Api, a: Args) -> Any:
    rid = int(a["id"])
    p = await api.get(f"/resources/{rid}/player")
    names = {s["key"]: s.get("name") for s in p.get("speakers") or []}
    t0, t1 = float(a.get("from_seconds") or 0) * 1000, (float(a["to_seconds"]) * 1000 if a.get("to_seconds") is not None else None)
    most = min(int(a.get("max_lines") or 200), 1000)
    segs = [s for s in p.get("segments") or [] if s.get("t1", 0) > t0 and (t1 is None or s.get("t0", 0) < t1)]
    lines = [
        {
            k: v
            for k, v in {
                "at": _tc(s["t0"]),
                "seconds": round(s["t0"] / 1000, 1),
                "speaker": names.get(s.get("s")),
                "text": s["text"],
                "page": s["p"] + 1 if s.get("p") is not None else None,
            }.items()
            if v is not None
        }
        for s in segs[:most]
    ]
    out = {"id": rid, "title": p.get("title"), "lines": lines, "total_lines": len(segs)}
    if len(segs) > most:
        out["more_from_seconds"] = round(segs[most]["t0"] / 1000, 1)
    return out


async def t_get_pages(api: Api, a: Args) -> Any:
    rid = int(a["id"])
    p = await api.get(f"/resources/{rid}/player")
    if not p.get("pages"):
        raise ToolError("this resource has no pages: it isn't a document or an image (use get_transcript)")
    first, last = max(1, int(a.get("from_page") or 1)), int(a.get("to_page") or 0) or len(p["pages"])
    last = min(last, first + 49, len(p["pages"]))
    text: dict[int, list[str]] = {}
    for s in p.get("segments") or []:
        if s.get("p") is not None:
            text.setdefault(s["p"] + 1, []).append(s["text"])
    shows = {d["idx"] + 1: d.get("text") for d in p.get("descriptions") or [] if d.get("paged")}
    pages = [
        {k: v for k, v in {"page": n, "text": "\n\n".join(text.get(n, [])), "shows": shows.get(n)}.items() if v is not None}
        for n in range(first, last + 1)
    ]
    return {"id": rid, "title": p.get("title"), "pages": pages, "total_pages": len(p["pages"])}


async def t_import_text(api: Api, a: Args) -> Any:
    r = await api.post(
        "/import", {"namespace": a["namespace"], "text": a["text"], "title": a.get("title"), "collection": a.get("collection")}
    )
    return {"resource_id": r["id"], "job": r["job"], "note": "queued for its namespace's pipeline; get_job says how it's going"}


async def t_import_web_page(api: Api, a: Args) -> Any:
    r = await api.post(
        "/import/web", {"url": a["url"], "namespace": a["namespace"], "title": a.get("title"), "collection": a.get("collection")}
    )
    return {
        "resource_id": r.get("id"),
        "job": r.get("job"),
        "note": "the page is captured as a document by its job; get_job says how it's going",
    }


async def t_process(api: Api, a: Args) -> Any:
    r = await api.post("/jobs", {"recordings": [int(x) for x in a["resource_ids"]], "steps": a.get("steps") or None})
    return {"jobs": r["jobs"]}


async def t_get_job(api: Api, a: Args) -> Any:
    j = await api.get(f"/jobs/{int(a['id'])}")
    steps = [s.get("type") if isinstance(s, dict) else s for s in j.get("steps") or []]
    out = {
        "id": j.get("id"),
        "resource_id": j.get("recording"),
        "status": j.get("status"),
        "steps": steps,
        "step": j.get("next_step"),
        "error": j.get("error"),
        "log": (j.get("log") or [])[-8:],
    }
    return {k: v for k, v in out.items() if v not in (None, [], "")}


def _obj(props: Args, required: list[str] | None = None) -> Args:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


_D = lambda t, d: {**t, "description": d}  # noqa: E731
# name: (title, description, input schema, changes things, the function)
TOOLS: dict[str, tuple[str, str, Args, bool, Callable[[Api, Args], Awaitable[Any]]]] = {
    "list_namespaces": (
        "List namespaces",
        "The namespaces you can see, with your role in each and how many resources it has. A namespace marked partial is one you see only some collections of.",
        _obj({}),
        False,
        t_list_namespaces,
    ),
    "list_collections": (
        "List a namespace's collections",
        "The collections of a namespace (they nest: `parent` and `path` say where each is), with how many resources each holds. Use list_resources with a collection id to see what's in one.",
        _obj({"namespace": _S}, ["namespace"]),
        False,
        t_list_collections,
    ),
    "list_resources": (
        "List resources",
        "Resources (recordings, documents, images) you can read, newest first: in a namespace, in a collection (and the ones inside it), of a kind, with a tag, with words in the title, or recorded between two days.",
        _obj(
            {
                "namespace": _S,
                "collection": _D(_I, "a collection id from list_collections"),
                "title_contains": _D(_S, "words that must all appear in the title, the namespace or a speaker's name"),
                "kind": {"type": "string", "enum": ["audio", "video", "transcript", "document", "image"]},
                "tag": _S,
                "from": _D(_S, "YYYY-MM-DD"),
                "to": _D(_S, "YYYY-MM-DD"),
                "limit": _D(_I, "at most 100; default 25"),
                "offset": _I,
            }
        ),
        False,
        t_list_resources,
    ),
    "search": (
        "Search the archive",
        'Find moments by what was said, shown on screen or written on a page. Every word must appear in the same line; "quoted phrases" as written; OR between alternatives. With meaning (the default), passages that say the same in other words are found too where the archive has search by meaning on; each hit says how it matched.',
        _obj(
            {
                "query": _S,
                "namespace": _S,
                "resource_id": _D(_I, "only in this resource"),
                "speaker_id": _I,
                "emotion": _S,
                "object": _D(_S, "only resources this kind of object is seen in (person, car …)"),
                "meaning": _D(_B, "also search by meaning; default true"),
                "limit": _D(_I, "at most 50; default 20"),
                "offset": _I,
            },
            ["query"],
        ),
        False,
        t_search,
    ),
    "get_resource": (
        "Get a resource",
        "One resource's details: what it is, where it lives, its speakers, its summary and chapters when it has them, and how many lines or pages it has.",
        _obj({"id": _I}, ["id"]),
        False,
        t_get_resource,
    ),
    "get_transcript": (
        "Read a transcript",
        "The lines of a recording's transcript with who said them and when, all of it or between two times (seconds). Long transcripts come in parts: `more_from_seconds` says where to carry on.",
        _obj(
            {
                "id": _I,
                "from_seconds": {"type": "number"},
                "to_seconds": {"type": "number"},
                "max_lines": _D(_I, "at most 1000; default 200"),
            },
            ["id"],
        ),
        False,
        t_get_transcript,
    ),
    "get_pages": (
        "Read a document's pages",
        "The text of a document's or an image's pages (and what each page shows, where that was described), up to 50 pages at a time; pages count from 1.",
        _obj({"id": _I, "from_page": _I, "to_page": _I}, ["id"]),
        False,
        t_get_pages,
    ),
    "get_job": (
        "Check a job",
        "How a processing job is going: its status, the step it's on, and the end of its log.",
        _obj({"id": _I}, ["id"]),
        False,
        t_get_job,
    ),
    "import_text": (
        "Import text",
        "Add a transcript or notes as a new resource in a namespace you edit (optionally into one of its collections); it's then processed by the namespace's pipeline.",
        _obj({"namespace": _S, "text": _S, "title": _S, "collection": _I}, ["namespace", "text"]),
        True,
        t_import_text,
    ),
    "import_web_page": (
        "Capture a web page",
        "Keep a public web page (or a PDF at a link) as a document in a namespace you edit, where the server can capture pages.",
        _obj({"url": _S, "namespace": _S, "title": _S, "collection": _I}, ["url", "namespace"]),
        True,
        t_import_web_page,
    ),
    "process": (
        "Process resources",
        "Queue resources you edit for processing: their namespace's pipeline, or the steps you name (transcribe, diarize, shots, ocr, faces, objects, describe, embed, analyze, summarize, report). `embed` and `analyze` index them for search.",
        _obj(
            {"resource_ids": {"type": "array", "items": _I, "minItems": 1, "maxItems": 50}, "steps": {"type": "array", "items": _S}},
            ["resource_ids"],
        ),
        True,
        t_process,
    ),
}


def _tools(user: Principal) -> list[Args]:
    return [
        {
            "name": name,
            "title": title,
            "description": description,
            "inputSchema": schema,
            "annotations": {
                "title": title,
                "readOnlyHint": not writes,
                "destructiveHint": False,
                "openWorldHint": name == "import_web_page",
            },
        }
        for name, (title, description, schema, writes, _fn) in TOOLS.items()
        if user.can_write or not writes  # a read-only token isn't offered what it can't do
    ]


async def _call_tool(api: Api, user: Principal, params: Args) -> Args:
    name, args = params.get("name"), params.get("arguments") or {}
    tool = TOOLS.get(str(name))
    if not tool or not isinstance(args, dict):
        raise _Rpc(-32602, f"unknown tool: {name}")
    schema = tool[2]
    missing = [k for k in schema["required"] if args.get(k) in (None, "")]
    extra = sorted(set(args) - set(schema["properties"]))
    try:
        if missing or extra:
            raise ToolError(f"{name} needs {', '.join(missing)}" if missing else f"{name} doesn't take {', '.join(extra)}")
        out = await tool[4](api, args)
    except ToolError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except (TypeError, ValueError, KeyError) as e:
        return {"content": [{"type": "text", "text": f"{name}: a wrong argument ({e})"}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}], "structuredContent": out}


# ---------- resources: what's in the archive, as text ----------
async def _list_resources(api: Api, params: Args) -> Args:
    offset = int(params.get("cursor") or 0) if str(params.get("cursor") or "0").isdigit() else 0
    r = await api.call("GET", "/resources", {"limit": 50, "offset": offset})
    rows, total = r.json(), int(r.headers.get("x-total-count") or 0)
    out: Args = {
        "resources": [
            {
                "uri": f"{URI}{x['id']}",
                "name": x.get("title") or f"Resource {x['id']}",
                "description": " · ".join(
                    str(v) for v in (x.get("namespace"), x.get("media_kind"), (x.get("recorded_at") or "")[:10]) if v
                ),
                "mimeType": "text/markdown",
            }
            for x in rows
        ]
    }
    if offset + len(rows) < total:
        out["nextCursor"] = str(offset + len(rows))
    return out


async def _read_resource(api: Api, params: Args) -> Args:
    uri = str(params.get("uri") or "")
    if not re.fullmatch(re.escape(URI) + r"\d+", uri):
        raise _Rpc(-32002, f"no such resource: {uri}")
    try:
        p = await api.get(f"/resources/{uri[len(URI) :]}/player")
    except ToolError as e:
        raise _Rpc(-32002, f"no such resource: {uri} ({e})") from None
    names = {s["key"]: s.get("name") for s in p.get("speakers") or []}
    lines, page = [f"# {p.get('title') or 'Untitled'}", ""], None
    for s in p.get("segments") or []:
        if s.get("p") is not None:
            if s["p"] != page:
                page = s["p"]
                lines += [f"## Page {page + 1}", ""]
            lines += [s["text"], ""]
        else:
            who = names.get(s.get("s"))
            lines.append(f"[{_tc(s['t0'])}] {who + ': ' if who else ''}{s['text']}")
    return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": "\n".join(lines)}]}


# ---------- the protocol ----------
class _Rpc(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


async def _handle(api: Api, user: Principal, msg: Any) -> Args | None:
    """One JSON-RPC message: its response, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return {
            "jsonrpc": "2.0",
            "id": msg.get("id") if isinstance(msg, dict) else None,
            "error": {"code": -32600, "message": "not a JSON-RPC 2.0 request"},
        }
    method, params, mid = msg["method"], msg.get("params") or {}, msg.get("id")
    if "id" not in msg:  # a notification (notifications/initialized, cancelled …): nothing to answer
        return None
    try:
        if method == "initialize":
            asked = params.get("protocolVersion")
            result: Args = {
                "protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False, "subscribe": False}},
                "serverInfo": {"name": "lens", "title": "Lens", "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": _tools(user)}
        elif method == "tools/call":
            result = await _call_tool(api, user, params)
        elif method == "resources/list":
            result = await _list_resources(api, params)
        elif method == "resources/templates/list":
            result = {
                "resourceTemplates": [
                    {
                        "uriTemplate": URI + "{id}",
                        "name": "A resource's text",
                        "description": "A recording's transcript or a document's text, by its id",
                        "mimeType": "text/markdown",
                    }
                ]
            }
        elif method == "resources/read":
            result = await _read_resource(api, params)
        else:
            raise _Rpc(-32601, f"method not found: {method}")
    except _Rpc as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": e.message}}
    except ToolError as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


@router.post("/mcp")
async def mcp(request: Request) -> Response:
    """The MCP endpoint (streamable HTTP): one JSON-RPC message in, its answer out as JSON."""
    user = await run_in_threadpool(_principal, request, get_db(request))
    if not user:
        where = f"{public_base(request)}/.well-known/oauth-protected-resource/mcp"
        return JSONResponse(
            {"error": "unauthorized", "error_description": "sign in: this server takes OAuth access tokens and API keys"},
            status_code=401,
            headers={"WWW-Authenticate": f'Bearer resource_metadata="{where}"'},
        )
    try:
        body = json.loads(await request.body())
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "the body isn't JSON"}}, status_code=400)
    api = Api(request)
    try:
        if isinstance(body, list):  # a batch, as older versions of the protocol allow
            answers = [a for a in [await _handle(api, user, m) for m in body[:50]] if a is not None]
            return JSONResponse(answers) if answers else Response(status_code=202)
        answer = await _handle(api, user, body)
    finally:
        await api.client.aclose()
    return JSONResponse(answer) if answer is not None else Response(status_code=202)


@router.get("/mcp")
@router.delete("/mcp")
def mcp_no_stream() -> Response:
    """No server-sent stream and no sessions to end: the protocol's way of saying so."""
    return Response(status_code=405, headers={"Allow": "POST"})
