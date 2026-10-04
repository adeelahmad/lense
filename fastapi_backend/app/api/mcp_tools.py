"""The MCP server's tools (app/api/mcp.py, docs/mcp.md): searching, reading and citing the archive.

Every tool only reads, and reads through the API's own checks (app/api/deps.Access and the route functions), so an
agent sees exactly what the person who signed it in sees: their namespaces, the collections they were given a role on,
and the graphs those open. Results are JSON objects (``structuredContent``, and the same as text for clients that only
read text). Moments carry ``url``, a link to the web app's page that opens the recording there, so answers can cite.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request

from app.api.deps import Access, Principal
from app.api.v1.routes import entities as entity_routes
from app.api.v1.routes import namespaces as namespace_routes
from app.api.v1.routes import recordings as recording_routes
from app.api.v1.routes import search as search_routes
from app.domain import analyze, graph_history, library, rdf, render, store
from app.domain import entities as ents
from app.domain import speakers as spk
from app.domain.store import DB
from app.schemas.entities import EntityList
from app.schemas.search import SearchResults

R = store.R
FETCH_CHARS = 100_000  # the most of a transcript fetch returns; get_transcript pages through the rest
TEXT_CHARS = 2000  # a line longer than this is cut (documents can have long blocks)


@dataclass
class Context:
    """One MCP request: who is asking (through `acl`), the database and configuration, and the web app's address."""

    request: Request
    db: DB
    cfg: dict[str, Any]
    acl: Access
    web: str

    @property
    def user(self) -> Principal:
        assert self.acl.user is not None  # app/api/mcp.py answers 401 before any tool runs
        return self.acl.user

    def link(self, rid: int, ms: int | None = None) -> str:
        """The recording's page in the web app, at a moment (whole seconds, as the page reads `?t=`)."""
        return f"{self.web}/resources/{rid}" + (f"?t={int(ms) // 1000}" if ms and ms >= 1000 else "")


class ToolError(Exception):
    """A problem the agent can fix (a wrong argument, something it may not see): said to it as the tool's result."""


@dataclass(frozen=True)
class Arg:
    name: str
    type: str  # string, integer, number, boolean, or string[]
    description: str
    required: bool = False
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    enum: tuple[str, ...] | None = None
    max_length: int | None = None

    def schema(self) -> dict[str, Any]:
        if self.type == "string[]":
            s: dict[str, Any] = {"type": "array", "items": {"type": "string", **({"enum": list(self.enum)} if self.enum else {})}}
        else:
            s = {"type": self.type}
            if self.enum:
                s["enum"] = list(self.enum)
        s["description"] = self.description
        if self.minimum is not None:
            s["minimum"] = self.minimum
        if self.maximum is not None:
            s["maximum"] = self.maximum
        if self.max_length is not None:
            s["maxLength"] = self.max_length
        if self.default is not None:
            s["default"] = self.default
        return s

    def parse(self, value: Any) -> Any:
        if value is None:
            if self.required:
                raise ToolError(f"{self.name} is required")
            return self.default
        t = self.type
        if t == "integer":
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            elif isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
                value = int(value)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ToolError(f"{self.name} is a whole number")
        elif t == "number":
            if isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    raise ToolError(f"{self.name} is a number") from None
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ToolError(f"{self.name} is a number")
        elif t == "boolean":
            if not isinstance(value, bool):
                raise ToolError(f"{self.name} is true or false")
        elif t == "string":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = str(value)
            if not isinstance(value, str):
                raise ToolError(f"{self.name} is a string")
            value = value.strip()
            if self.max_length is not None and len(value) > self.max_length:
                raise ToolError(f"{self.name} is at most {self.max_length} characters")
            if not value:
                if self.required:
                    raise ToolError(f"{self.name} is required")
                return self.default
        elif t == "string[]":
            if isinstance(value, str):
                value = [x for x in value.split(",")]
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ToolError(f"{self.name} is a list of strings")
            value = [x.strip() for x in value if x.strip()]
            if self.enum:
                bad = [x for x in value if x not in self.enum]
                if bad:
                    raise ToolError(f"{self.name}: {', '.join(bad)} isn't one of {', '.join(self.enum)}")
            return value or self.default
        if self.enum and t != "string[]" and value not in self.enum:
            raise ToolError(f"{self.name} is one of {', '.join(self.enum)}")
        if self.minimum is not None and value < self.minimum:
            raise ToolError(f"{self.name} is at least {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise ToolError(f"{self.name} is at most {self.maximum}")
        return value


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    args: tuple[Arg, ...]
    run: Callable[..., dict[str, Any]] = field(compare=False)

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {a.name: a.schema() for a in self.args},
                "required": [a.name for a in self.args if a.required],
                "additionalProperties": False,
            },
            "annotations": {
                "title": self.title,
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }


TOOLS: dict[str, Tool] = {}


def tool(name: str, title: str, description: str, *args: Arg) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    def register(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        TOOLS[name] = Tool(name, title, description, args, fn)
        return fn

    return register


def listing() -> list[dict[str, Any]]:
    return [t.definition() for t in TOOLS.values()]


def _text(out: dict[str, Any], error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}], "isError": error}
    if not error:
        result["structuredContent"] = out
    return result


def call(ctx: Context, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run a tool. What the agent can fix (bad arguments, something not found or not theirs) comes back as a result
    with isError, as MCP has it, so the model sees it and can try again."""
    t = TOOLS[name]
    known = {a.name for a in t.args}
    try:
        unknown = sorted(set(arguments) - known)
        if unknown:
            raise ToolError(f"unknown argument {', '.join(unknown)}; {name} takes {', '.join(a.name for a in t.args) or 'none'}")
        values = {a.name: a.parse(arguments.get(a.name)) for a in t.args}
        with graph_history.acting(via="mcp", tool=name):
            return _text(t.run(ctx, **values))
    except ToolError as e:
        return _text({"error": str(e)}, error=True)
    except HTTPException as e:
        if e.status_code == 404:
            return _text({"error": "not found, or not something you can read"}, error=True)
        return _text({"error": str(e.detail)}, error=True)
    except KeyError:
        return _text({"error": "not found, or not something you can read"}, error=True)
    except ValueError as e:
        return _text({"error": str(e)}, error=True)


# ---------- shared shapes ----------
def _plain(snippet: str) -> str:
    """A search snippet (escaped HTML with <mark> around matches) as text, with the matches in **bold**."""
    return html.unescape(snippet.replace("<mark>", "**").replace("</mark>", "**"))


def _cut(text: str | None, n: int = TEXT_CHARS) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _seconds(ms: int | None) -> float | None:
    return None if ms is None else round(ms / 1000, 1)


def _day(value: str | None, name: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        raise ToolError(f"{name} is a date like 2024-05-31") from None


def _names(db: DB, ids: list[int | None]) -> dict[int, str]:
    return render.speaker_names(db, [i for i in ids if i])


def _line(ctx: Context, rid: int, s: dict[str, Any], names: dict[int, str]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "line": s["idx"],
        "at": store.tc(s.get("t0")),
        "seconds": _seconds(s.get("t0")),
        "end_seconds": _seconds(s.get("t1")),
        "speaker": names.get(s["speaker"]) if s.get("speaker") else None,
        "text": _cut(s.get("text")),
        "url": ctx.link(rid, s.get("t0")),
    }
    if s.get("page") is not None:
        out["page"] = s["page"] + 1
    return out


def _segments(db: DB, rid: int, where: str = "", limit: int | None = None, **params: Any) -> list[dict[str, Any]]:
    sql = f"SELECT idx, t0, t1, speaker, text, page FROM segment WHERE recording = $r{where} ORDER BY idx"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return db.rows(sql, r=rid, **params)


def _recording(ctx: Context, rid: int) -> dict[str, Any]:
    """The recording row, if this person may read it (404 otherwise)."""
    return ctx.acl.recording(rid)


# ---------- searching ----------
@tool(
    "search",
    "Search the archive",
    "Full-text search for moments in the archive: what was said in recordings (transcripts), text shown on screen in "
    "videos or written in documents and images, supplementary files, and kinds of object seen. Words are all required "
    '(matched after stemming), "quoted phrases" must appear as written, OR separates alternatives. Results are the best '
    "moments first, each with the recording, who said it, when in the recording, the matching text (matches in **bold**) "
    "and a url that opens the recording at that moment. Use get_transcript for the lines around a moment.",
    Arg("query", "string", 'what to look for: words, "a phrase", or alternatives separated by OR', required=True, max_length=500),
    Arg("namespace", "string", "only this namespace (see list_namespaces)"),
    Arg("speaker_id", "integer", "only what this speaker said (ids from list_speakers or get_recording)"),
    Arg("recording_id", "integer", "only within this recording"),
    Arg("limit", "integer", "how many moments", default=10, minimum=1, maximum=50),
    Arg("offset", "integer", "skip this many (for the next page)", default=0, minimum=0, maximum=10000),
)
def search(
    ctx: Context, query: str, namespace: str | None, speaker_id: int | None, recording_id: int | None, limit: int, offset: int
) -> dict[str, Any]:
    if recording_id is not None:
        _recording(ctx, recording_id)
    res = search_routes.search_transcripts(
        ctx.acl,
        ctx.user,
        ctx.db,
        ctx.cfg,
        q=query,
        ns=namespace,
        speaker=speaker_id,
        emotion=None,
        recording=recording_id,
        object=None,
        limit=limit,
        offset=offset,
        facets=False,
        mode="auto",  # by meaning too, when it's set up
    )
    found = SearchResults.model_validate(res)  # the route hands back the dict it signed
    results = []
    for h in found.hits:
        hit: dict[str, Any] = {
            "id": str(h.recording_id),
            "recording_id": h.recording_id,
            "title": h.title,
            "namespace": h.namespace,
            "recorded_at": h.recorded_at,
            "speaker": h.speaker,
            "speaker_id": h.speaker_id,
            "found": h.source,
            "at": store.tc(h.t0) if h.t0 is not None else None,
            "seconds": _seconds(h.t0),
            "line": h.idx,
            "page": h.page + 1 if h.page is not None else None,
            "file": h.file_label,
            "text": _plain(h.snippet),
            "url": ctx.link(h.recording_id, h.t0),
        }
        results.append({k: v for k, v in hit.items() if v is not None})
    out: dict[str, Any] = {"query": found.query, "total": found.total, "results": results}
    if found.capped:
        out["capped"] = True
    if offset + len(results) < found.total:
        out["next_offset"] = offset + len(results)
    return out


# ---------- browsing ----------
@tool(
    "list_namespaces",
    "List namespaces",
    "The namespaces (separate parts of the archive) you can read, with how many recordings and hours each has and your "
    "role there. `partial` ones you see only some collections of.",
)
def list_namespaces(ctx: Context) -> dict[str, Any]:
    rows = namespace_routes.list_namespaces(ctx.acl, ctx.user, ctx.db)
    return {
        "namespaces": [
            {
                "name": n.name,
                "role": n.role,
                "partial": n.partial,
                "recordings": n.recordings,
                "hours": round((n.ms or 0) / 3_600_000, 1),
                "speakers": n.speakers,
                "graph": n.graph,
            }
            for n in rows
        ]
    }


SORTS = ("-date", "date", "title", "-title", "duration", "-duration")
MEDIA = ("audio", "video", "transcript", "document", "image")


@tool(
    "list_recordings",
    "List recordings",
    "Recordings (and documents, images and transcripts) you can read, newest first by default, with filters. `query` "
    "matches words in the title, the namespace or a speaker's name; use search for what is said in them.",
    Arg("namespace", "string", "only this namespace"),
    Arg("query", "string", "words that must all appear in the title, the namespace or a speaker's name", max_length=200),
    Arg("date_from", "string", "recorded on or after this day (YYYY-MM-DD)"),
    Arg("date_to", "string", "recorded on or before this day (YYYY-MM-DD)"),
    Arg("speaker_id", "integer", "only recordings this speaker is in"),
    Arg("tag", "string", "only recordings with this tag", max_length=100),
    Arg("media", "string", "only this kind", enum=MEDIA),
    Arg("sort", "string", "order: date, title or duration; - first for descending", default="-date", enum=SORTS),
    Arg("limit", "integer", "how many", default=20, minimum=1, maximum=100),
    Arg("offset", "integer", "skip this many (for the next page)", default=0, minimum=0),
)
def list_recordings(
    ctx: Context,
    namespace: str | None,
    query: str | None,
    date_from: str | None,
    date_to: str | None,
    speaker_id: int | None,
    tag: str | None,
    media: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    spaces, within = ctx.acl.scope(namespace)
    rows, total = library.list_recordings(
        ctx.db,
        spaces,
        within=within,
        sort=sort,
        limit=limit,
        offset=offset,
        q=query,
        speakers=[speaker_id] if speaker_id is not None else None,
        date_from=_day(date_from, "date_from"),
        date_to=_day(date_to, "date_to"),
        media=media,
        tags=[tag] if tag else None,
        cfg=ctx.cfg,
    )
    out: dict[str, Any] = {
        "total": total,
        "recordings": [
            {
                "recording_id": r["id"],
                "title": r.get("title"),
                "namespace": r.get("namespace"),
                "recorded_at": r.get("recorded_at"),
                "duration": store.tc(r.get("duration_ms")) if r.get("duration_ms") else None,
                "media": r.get("media_kind"),
                "status": r.get("status"),
                "speakers": r.get("speakers") or None,
                "tags": r.get("tags") or None,
                "language": r.get("language"),
                "url": ctx.link(r["id"]),
            }
            for r in rows
        ],
    }
    if offset + len(rows) < total:
        out["next_offset"] = offset + len(rows)
    return out


@tool(
    "list_speakers",
    "List speakers",
    "The speakers (voices) of the namespaces you have a role in, the ones who talk most first: their ids filter search "
    "and list_recordings.",
    Arg("namespace", "string", "only this namespace"),
    Arg("query", "string", "only speakers whose name contains this", max_length=100),
    Arg("limit", "integer", "how many", default=50, minimum=1, maximum=500),
)
def list_speakers(ctx: Context, namespace: str | None, query: str | None, limit: int) -> dict[str, Any]:
    spaces = [ctx.acl.namespace(namespace)] if namespace else ctx.acl.spaces()
    names = store.space_names(ctx.db)
    rows = [
        {
            "speaker_id": s["id"],
            "name": s["display"],
            "named": bool(s.get("name")),
            "namespace": names.get(sid),
            "recordings": s["recordings"],
            "talk_minutes": round((s.get("talk_ms") or 0) / 60000, 1),
        }
        for sid in spaces
        for s in spk.list_speakers(ctx.db, sid)
        if s["recordings"] and (not query or query.lower() in (s["display"] or "").lower())
    ]
    rows.sort(key=lambda s: -s["talk_minutes"])
    return {"total": len(rows), "speakers": rows[:limit]}


# ---------- reading ----------
@tool(
    "get_recording",
    "Get a recording",
    "One recording: what it is, when it was recorded, its speakers, summary, chapters (sections), the entities and "
    "keywords most mentioned in it, and a url to it. Read what was said with get_transcript.",
    Arg("recording_id", "integer", "the recording", required=True),
)
def get_recording(ctx: Context, recording_id: int) -> dict[str, Any]:
    rec = recording_routes.get_recording(recording_id, ctx.acl, ctx.db, ctx.cfg)
    raw = (
        ctx.db.one("SELECT recorded_at, duration_ms, source, media, language, pages, analyzed_at FROM $r", r=R("recording", recording_id))
        or {}
    )
    sections = ctx.db.rows("SELECT idx, t0, t1, title FROM section WHERE recording = $r ORDER BY idx", r=recording_id)
    found = _entities(ctx, None, None, None, recording_id, "mentions", 30, 0)  # counted as the entity index counts them for this person
    summary = rec.summary or {}
    out: dict[str, Any] = {
        "recording_id": recording_id,
        "title": rec.title,
        "namespace": rec.namespace,
        "collection": " / ".join(c.name for c in rec.collection_path) or None,
        "recorded_at": raw.get("recorded_at"),
        "duration": store.tc(raw["duration_ms"]) if raw.get("duration_ms") else None,
        "media": render.kind(raw),
        "pages": raw.get("pages"),
        "language": raw.get("language"),
        "status": rec.status,
        "tags": rec.tags or None,
        "your_role": rec.role,
        "speakers": [{"speaker_id": s.id, "name": s.name} for s in rec.speakers],
        "summary": summary.get("summary"),
        "key_points": summary.get("key_points") or None,
        "topics": summary.get("topics") or None,
        "action_items": summary.get("action_items") or None,
        "sections": [
            {
                "title": s.get("title"),
                "at": store.tc(s.get("t0")),
                "seconds": _seconds(s.get("t0")),
                "url": ctx.link(recording_id, s.get("t0")),
            }
            for s in sections
        ]
        or None,
        "entities": [{"entity_id": e["id"], "name": e["name"], "type": e["type_label"], "mentions": e["mentions"]} for e in found.items]
        or None,
        "keywords": [k[0] if isinstance(k, (list, tuple)) else k.get("word", k) for k in analyze.keywords(ctx.db, recording_id, 20)]
        if raw.get("analyzed_at")
        else None,
        "url": ctx.link(recording_id),
    }
    if rec.email:
        out["email"] = rec.email.model_dump(exclude_none=True)
    if rec.web:
        out["web_page"] = rec.web.model_dump(exclude_none=True)
    return {k: v for k, v in out.items() if v is not None}


@tool(
    "get_transcript",
    "Read a transcript",
    "The lines of a recording's transcript (or a document's or image's text), in order: who said what, when, each with "
    "a url that opens the recording at that line. Read a long one in pages: from_line (next_line of the last page) or "
    "a time window in seconds.",
    Arg("recording_id", "integer", "the recording", required=True),
    Arg("from_line", "integer", "start at this line (0 is the first)", minimum=0),
    Arg("start_seconds", "number", "start at the line being said at this second", minimum=0),
    Arg("end_seconds", "number", "stop before the lines that start after this second", minimum=0),
    Arg("limit", "integer", "the most lines", default=200, minimum=1, maximum=1000),
)
def get_transcript(
    ctx: Context, recording_id: int, from_line: int | None, start_seconds: float | None, end_seconds: float | None, limit: int
) -> dict[str, Any]:
    rec = _recording(ctx, recording_id)
    where, params = "", {}
    if from_line is not None:
        where += " AND idx >= $from"
        params["from"] = from_line
    if start_seconds is not None:
        where += " AND t1 > $a"
        params["a"] = int(start_seconds * 1000)
    if end_seconds is not None:
        where += " AND t0 <= $b"
        params["b"] = int(end_seconds * 1000)
    segs = _segments(ctx.db, recording_id, where, limit + 1, **params)
    more, segs = len(segs) > limit, segs[:limit]
    names = _names(ctx.db, [s.get("speaker") for s in segs])
    total = ctx.db.values("RETURN array::len((SELECT VALUE id FROM segment WHERE recording = $r))", r=recording_id)
    out: dict[str, Any] = {
        "recording_id": recording_id,
        "title": rec.get("title"),
        "lines_in_all": int(total[0]) if total else 0,
        "lines": [_line(ctx, recording_id, s, names) for s in segs],
    }
    if more:
        out["next_line"] = segs[-1]["idx"] + 1
    return out


@tool(
    "fetch",
    "Fetch a recording's text",
    "A recording's whole text at once, as timestamped lines ([m:ss] Speaker: words), with its title and url; ids come "
    "from search or list_recordings. A very long one is cut, and says where get_transcript can go on from.",
    Arg("id", "string", "the recording id", required=True, max_length=40),
)
def fetch(ctx: Context, id: str) -> dict[str, Any]:
    m = re.match(r"\s*(\d+)", id)
    if not m:
        raise ToolError("id is a recording id, like 12")
    rid = int(m.group(1))
    rec = _recording(ctx, rid)
    segs = _segments(ctx.db, rid)
    names = _names(ctx.db, [s.get("speaker") for s in segs])
    parts, size, cut_at = [], 0, None
    for s in segs:
        who = names.get(s["speaker"]) if s.get("speaker") else None
        where = f"p. {s['page'] + 1}" if s.get("page") is not None else store.tc(s.get("t0"))
        line = f"[{where}] " + (f"{who}: " if who else "") + (s.get("text") or "")
        if size + len(line) > FETCH_CHARS:
            cut_at = s["idx"]
            break
        parts.append(line)
        size += len(line) + 1
    space = store.space_names(ctx.db).get(rec["space"])
    meta: dict[str, Any] = {
        "namespace": space,
        "recorded_at": rec.get("recorded_at"),
        "duration": store.tc(rec.get("duration_ms")) if rec.get("duration_ms") else None,
        "lines": len(segs),
    }
    if cut_at is not None:
        meta["truncated"] = True
        meta["next_line"] = cut_at
    return {
        "id": str(rid),
        "title": rec.get("title") or f"Recording {rid}",
        "text": "\n".join(parts),
        "url": ctx.link(rid),
        "metadata": {k: v for k, v in meta.items() if v is not None},
    }


@tool(
    "cite",
    "Cite a moment",
    "A citation for a moment of a recording: the exact words (one line, or a few from there), who said them, the "
    "recording, when it was recorded, the time in it, a url that opens it there, and the whole citation as Markdown. "
    "Name the moment by line (from search or get_transcript) or by second.",
    Arg("recording_id", "integer", "the recording", required=True),
    Arg("line", "integer", "the line (from search or get_transcript)", minimum=0),
    Arg("seconds", "number", "or the moment, in seconds from the start", minimum=0),
    Arg("lines", "integer", "how many lines to quote from there", default=1, minimum=1, maximum=10),
)
def cite(ctx: Context, recording_id: int, line: int | None, seconds: float | None, lines: int) -> dict[str, Any]:
    if line is None and seconds is None:
        raise ToolError("give line or seconds")
    rec = _recording(ctx, recording_id)
    if line is not None:
        segs = _segments(ctx.db, recording_id, " AND idx >= $i", lines, i=line)
        if not segs or segs[0]["idx"] != line:
            raise ToolError(f"recording {recording_id} has no line {line}")
    else:
        ms = int((seconds or 0) * 1000)
        # the line being said then, else the next one
        segs = _segments(ctx.db, recording_id, " AND t1 > $ms", lines, ms=ms)
        if not segs:
            raise ToolError(f"nothing is said in recording {recording_id} at or after {store.tc(ms)}")
    names = _names(ctx.db, [s.get("speaker") for s in segs])
    first = segs[0]
    who = list(dict.fromkeys(names[s["speaker"]] for s in segs if s.get("speaker") and s["speaker"] in names))
    quote = " ".join((s.get("text") or "").strip() for s in segs)
    title = rec.get("title") or f"Recording {recording_id}"
    space = store.space_names(ctx.db).get(rec["space"])
    when = (rec.get("recorded_at") or "")[:10] or None
    page = first.get("page")
    at = f"p. {page + 1}" if page is not None else store.tc(first.get("t0"))
    url = ctx.link(recording_id, first.get("t0"))
    credit = ", ".join(x for x in (" and ".join(who) if who else None, f"*{title}*", when) if x)
    return {
        "quote": quote,
        "speakers": who,
        "recording_id": recording_id,
        "title": title,
        "namespace": space,
        "recorded_at": rec.get("recorded_at"),
        "line": first["idx"],
        "at": at,
        "seconds": _seconds(first.get("t0")),
        "url": url,
        "markdown": f"> {quote}\n>\n> — {credit}, [{at}]({url})",
    }


# ---------- entities and the graph ----------
ENTITY_TYPES = tuple(ents.TYPES)


def _entity_row(e: dict[str, Any]) -> dict[str, Any]:
    out = {
        "entity_id": e["id"],
        "name": e["name"],
        "type": e["type"],
        "namespace": e.get("namespace"),
        "aliases": e.get("aliases") or None,
        "mentions": e.get("mentions"),
        "recordings": e.get("recordings"),
        "first": e.get("first"),
        "last": e.get("last"),
    }
    return {k: v for k, v in out.items() if v is not None}


@tool(
    "list_entities",
    "Find entities",
    "Entities: the people, organisations, products, places, events, works and topics mentioned in recordings, the most "
    "mentioned first. Find one by name with query; its entity_id opens it in get_entity and the graph.",
    Arg("query", "string", "a name, or part of one", max_length=200),
    Arg("types", "string[]", "only these types", enum=ENTITY_TYPES),
    Arg("namespace", "string", "only this namespace"),
    Arg("recording_id", "integer", "only those mentioned in this recording"),
    Arg("sort", "string", "order", default="mentions", enum=("mentions", "recordings", "recent", "rising", "name")),
    Arg("limit", "integer", "how many", default=25, minimum=1, maximum=100),
    Arg("offset", "integer", "skip this many (for the next page)", default=0, minimum=0),
)
def list_entities(
    ctx: Context,
    query: str | None,
    types: list[str] | None,
    namespace: str | None,
    recording_id: int | None,
    sort: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    res = _entities(ctx, query, types, namespace, recording_id, sort, limit, offset)
    out: dict[str, Any] = {"total": res.total, "entities": [_entity_row(e) for e in res.items]}
    if offset + len(res.items) < res.total:
        out["next_offset"] = offset + len(res.items)
    return out


def _entities(
    ctx: Context,
    query: str | None,
    types: list[str] | None,
    namespace: str | None,
    recording_id: int | None,
    sort: str,
    limit: int,
    offset: int,
) -> EntityList:
    if namespace:
        ctx.acl.namespace(namespace)
    return entity_routes.list_entities(
        ctx.user,
        ctx.acl,
        ctx.db,
        q=query or "",
        types=",".join(types or []),
        namespaces=namespace or "",
        speaker=None,
        recording=recording_id,
        date_from="",
        date_to="",
        min_mentions=1,
        hidden=False,
        sort=sort,
        limit=limit,
        offset=offset,
        group=False,
    )


@tool(
    "get_entity",
    "Get an entity",
    "One entity: its names, how often and when it is mentioned, the entities mentioned together with it, who mentions "
    "it most, and the lines that mention it (newest first), each with a url to that moment.",
    Arg("entity_id", "integer", "the entity (from list_entities, get_recording or the graph)", required=True),
    Arg("mentions", "integer", "how many mentioning lines", default=10, minimum=0, maximum=100),
    Arg("mentions_offset", "integer", "skip this many lines (for the next page)", default=0, minimum=0, maximum=10000),
    Arg("sort", "string", "the lines' order", default="newest", enum=("newest", "oldest", "most")),
)
def get_entity(ctx: Context, entity_id: int, mentions: int, mentions_offset: int, sort: str) -> dict[str, Any]:
    d = entity_routes.get_entity(entity_id, ctx.user, ctx.acl, ctx.db)
    con = entity_routes.get_entity_connections(entity_id, ctx.user, ctx.acl, ctx.db)
    out: dict[str, Any] = {
        "entity_id": d.id,
        "name": d.name,
        "type": d.type,
        "type_label": d.type_label,
        "namespace": d.namespace,
        "aliases": d.aliases or None,
        "mentions": d.mentions,
        "recordings": d.recordings,
        "speakers": d.speakers,
        "first": d.first,
        "last": d.last,
        "same_as": [{"entity_id": x.id, "name": x.name, "namespace": x.namespace} for x in d.links] or None,
        "related": [
            {"entity_id": x["id"], "name": x["name"], "type": x["type"], "together": x["together"]}
            for x in (con.get("entities") or [])[:15]
        ]
        or None,
        "mentioned_most_by": [
            {"speaker_id": x["id"], "name": x["name"], "mentions": x["mentions"]} for x in (con.get("speakers") or [])[:10]
        ]
        or None,
    }
    if mentions:
        page = entity_routes.list_entity_mentions(
            entity_id,
            ctx.user,
            ctx.acl,
            ctx.db,
            speaker=None,
            recording=None,
            date_from="",
            date_to="",
            sort=sort,
            limit=mentions,
            offset=mentions_offset,
        )
        out["lines"] = [
            {
                k: v
                for k, v in {
                    "recording_id": m["recording_id"],
                    "title": m.get("title"),
                    "namespace": m.get("namespace"),
                    "recorded_at": m.get("recorded_at"),
                    "line": m.get("idx"),
                    "at": m.get("time"),
                    "seconds": _seconds(m.get("t0")),
                    "speaker": m.get("speaker"),
                    "text": _cut(m.get("text")),
                    "url": ctx.link(m["recording_id"], m.get("t0")),
                }.items()
                if v is not None
            }
            for m in page.items
        ]
        if mentions_offset + len(page.items) < page.total:
            out["next_mentions_offset"] = mentions_offset + len(page.items)
    return {k: v for k, v in out.items() if v is not None}


def _node(n: str | None, entity_id: int | None, speaker_id: int | None, name: str) -> str:
    if n:
        if not re.fullmatch(r"[es]\d+", n):
            raise ToolError(f"{name} is a node id: e<entity id> or s<speaker id>, like e12")
        return n
    if entity_id is not None:
        return f"e{entity_id}"
    if speaker_id is not None:
        return f"s{speaker_id}"
    raise ToolError(f"give {name}, an entity_id or a speaker_id")


def _scope(namespace: str | None) -> str:
    return f"ns:{namespace}" if namespace else "all"


@tool(
    "explore_graph",
    "Explore the knowledge graph",
    "The knowledge graph around an entity or a speaker: what and who it is connected to (mentioned together, or "
    "mentioned by), the strongest first, one or two steps out. Without a namespace it covers the namespaces that share "
    "their graph; a namespace with an isolated graph is explored on its own (namespace=...).",
    Arg("node", "string", "the node: e<entity id> or s<speaker id>", max_length=20),
    Arg("entity_id", "integer", "or the entity"),
    Arg("speaker_id", "integer", "or the speaker"),
    Arg("namespace", "string", "only this namespace's graph"),
    Arg("depth", "integer", "how many steps out", default=1, minimum=1, maximum=2),
    Arg("types", "string[]", "only entities of these types", enum=ENTITY_TYPES),
    Arg("limit", "integer", "the most nodes", default=40, minimum=1, maximum=200),
)
def explore_graph(
    ctx: Context,
    node: str | None,
    entity_id: int | None,
    speaker_id: int | None,
    namespace: str | None,
    depth: int,
    types: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    focus = _node(node, entity_id, speaker_id, "node")
    g = entity_routes.explore_graph(
        ctx.user,
        ctx.acl,
        ctx.db,
        focus=focus,
        scope=_scope(namespace),
        depth=depth,
        types=",".join(types or []),
        kinds="",
        min_weight=1,
        limit=limit,
    )
    return {
        "focus": g["focus"],
        "nodes": [{k: v for k, v in n.items() if v is not None} for n in g["nodes"]],
        "edges": g["edges"],
        "truncated": g.get("truncated", False),
    }


@tool(
    "find_path",
    "Find how two things connect",
    "The shortest chain of links between two entities or speakers in the knowledge graph, with lines from the "
    "recordings that show each link (each with a url to that moment).",
    Arg("from_node", "string", "one end: e<entity id> or s<speaker id>", required=True, max_length=20),
    Arg("to_node", "string", "the other end", required=True, max_length=20),
    Arg("namespace", "string", "only this namespace's graph"),
)
def find_path(ctx: Context, from_node: str, to_node: str, namespace: str | None) -> dict[str, Any]:
    a, b = _node(from_node, None, None, "from_node"), _node(to_node, None, None, "to_node")
    res = entity_routes.find_graph_path(ctx.user, ctx.acl, ctx.db, a=a, b=b, scope=_scope(namespace))
    for link in res.get("links", []):
        link["evidence"] = [
            {
                "recording_id": e["recording_id"],
                "at": e.get("time"),
                "seconds": _seconds(e.get("t0")),
                "text": _cut(e.get("text")),
                "url": ctx.link(e["recording_id"], e.get("t0")),
            }
            for e in link.get("evidence", [])
        ]
    return res


@tool(
    "sparql",
    "Query the archive with SPARQL",
    "A read-only SPARQL query (SELECT, ASK, CONSTRUCT or DESCRIBE) over one namespace's linked data: recordings "
    "described with Dublin Core (dcterms:title, creator, subject, created, references the entities they mention, …), "
    "collections, entities (skos:Concept with skos:prefLabel) and speakers (foaf:Person). Prefixes dcterms, dcmitype, "
    "foaf, skos, owl, rdf, rdfs, xsd and lens are known. SELECT and ASK give SPARQL JSON results; CONSTRUCT and "
    "DESCRIBE give Turtle.",
    Arg("namespace", "string", "the namespace to query", required=True),
    Arg("query", "string", "the SPARQL query", required=True, max_length=20000),
)
def sparql(ctx: Context, namespace: str, query: str) -> dict[str, Any]:
    sid = ctx.acl.namespace(namespace)
    base = (ctx.cfg["iiif"].get("base_url") or ctx.web).rstrip("/")
    try:
        kind, out = rdf.sparql(rdf.namespace_graph(ctx.db, ctx.cfg, base, sid), query, base)
    except rdf.QueryProblem as e:
        raise ToolError(str(e)) from None
    if kind == "results":
        return out
    return {"turtle": rdf.serialize(out, "turtle").decode()[:200000]}
