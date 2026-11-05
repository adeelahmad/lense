"""Optional NVIDIA Auto-Ontology REST bridge, exposed through Lens's MCP extensions.

The upstream MCP server requires interactive OAuth. This adapter instead uses an API token at the public web
gateway, retaining Lens's private extensions, sealed credentials, approval gates and activity ledger. It never
connects to source databases, ingests Lens data, creates conversations or enables upstream predictive uploads.
"""

from __future__ import annotations

import copy
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

ADAPTER = "auto_ontology"
MAX_BYTES = 2_000_000
MAX_SECONDS = 900
SOCKET_TIMEOUT = 30


class OntologyError(ValueError):
    """A bounded, credential-free error safe to show in a tool result."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never forward the token, even to a redirect on the same origin


def config(server):
    """Validate the admin's fixed public gateway origin and its one API credential."""
    raw_url = str(server.get("url") or "")
    if any(ord(c) < 32 for c in raw_url):
        raise OntologyError("the Auto-Ontology web address must not contain control characters")
    url = raw_url.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        raise OntologyError("give Auto-Ontology's public web address, including its port") from None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or any(c.isspace() or ord(c) < 32 for c in url)
        or "\\" in url
        or port == 0
    ):
        raise OntologyError(
            "use Auto-Ontology's public web origin (for example https://ontology.example.com), without a path or credentials"
        )
    if server.get("command") or server.get("args") or server.get("env") or server.get("keep"):
        raise OntologyError("Auto-Ontology uses its web address, not a program or a kept MCP session")
    headers = server.get("headers") or {}
    if not isinstance(headers, dict) or len(headers) != 1:
        raise OntologyError("Auto-Ontology needs one x-api-key header containing its API token")
    name, token = next(iter(headers.items()))
    if not isinstance(name, str) or name.lower() != "x-api-key" or not isinstance(token, str) or not token.strip():
        raise OntologyError("Auto-Ontology needs one x-api-key header containing its API token")
    if "\r" in token or "\n" in token:
        raise OntologyError("the Auto-Ontology API token must be a single line")
    return url.rstrip("/"), {"x-api-key": token}


def _open(server, path, body=None):
    base, headers = config(server)
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={**headers, "Accept": "application/json, text/event-stream", "Content-Type": "application/json", "User-Agent": "Lens"},
        method="POST" if body is not None else "GET",
    )
    try:
        return urllib.request.build_opener(_NoRedirect()).open(req, timeout=SOCKET_TIMEOUT)
    except urllib.error.HTTPError as e:
        status = e.code
        e.close()
        hint = "check the API token and its permissions" if status in (401, 403) else "check the public web gateway"
        raise OntologyError(f"Auto-Ontology answered HTTP {status}; {hint}", status) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise OntologyError("could not reach Auto-Ontology's public web gateway; no request was retried") from None


def _json(server, path, body=None):
    try:
        with _open(server, path, body) as response:
            raw = b"".join(_chunks(response, time.monotonic()))
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise OntologyError("Auto-Ontology returned an unexpected JSON response")
        return result
    except (UnicodeError, json.JSONDecodeError):
        raise OntologyError("Auto-Ontology returned invalid JSON") from None
    except (OSError, http.client.HTTPException):
        raise OntologyError("Auto-Ontology's response was interrupted; no request was retried") from None


def _text(value, name, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise OntologyError(f"{name} must be nonempty text of at most {limit} characters")
    return value.strip()


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise OntologyError(f"{name} must be a whole number from {minimum} to {maximum}")
    return value


def _tool(name, description, properties=None, required=()):
    return {
        "name": name,
        "description": description,
        "schema": {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False},
        "read_only": True,
    }


QUESTION = {"type": "string", "minLength": 1, "maxLength": 4000, "description": "A question about the connected relational data"}
TOOLS = [
    _tool("check_readiness", "Check Auto-Ontology's semantic compilation and source availability without a model call."),
    _tool(
        "search_terms",
        "Find business terms in Auto-Ontology's external SQL semantic catalog. This does not search the Lens archive.",
        {
            "query": {"type": "string", "maxLength": 500},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "offset": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 0},
        },
    ),
    _tool(
        "get_term",
        "Read a catalog term and its relationships/mapped tables by the exact ID returned by search_terms.",
        {"term_id": {"type": "string", "minLength": 1, "maxLength": 200}},
        ("term_id",),
    ),
    _tool(
        "check_answerable",
        "Ask Auto-Ontology which terms cover a question and which are missing. Uses its models and may cost money; coverage is guidance, not proof.",
        {"question": QUESTION},
        ("question",),
    ),
    _tool(
        "ask_question",
        "Ask Auto-Ontology about its connected relational data. Returns an answer, generated SQL and bounded rows; cite the source and show SQL. Uses its models and may cost money. No prediction or persistent conversation.",
        {"question": QUESTION, "max_rows": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}},
        ("question",),
    ),
]


def discover(server):
    """A cheap authenticated read verifies the deployment; it does not claim the ontology is ready."""
    result = _json(server, "/api/terms?limit=1")
    if not isinstance(result.get("terms"), list):
        raise OntologyError("Auto-Ontology's terms endpoint returned an unexpected response")
    return copy.deepcopy(TOOLS)


def _readiness(server):
    status = _json(server, "/api/semantic-compilation/status")
    if not isinstance(status.get("calculated"), bool) or not isinstance(status.get("running"), bool):
        raise OntologyError("Auto-Ontology returned an invalid compilation status")
    result = {"compiled": status["calculated"], "compiling": status["running"], "connections": None, "catalog_available": None}
    for path, field in (("/api/connections", "connections"), ("/api/datasources/dbs", "catalog_available")):
        try:
            got = _json(server, path)
        except OntologyError as e:
            if e.status == 403:  # a viewer may ask questions but not administer connections
                continue
            raise
        count = got.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise OntologyError("Auto-Ontology returned an invalid source count")
        result[field] = count if field == "connections" else count > 0
    if not result["compiled"] or result["connections"] == 0 or result["catalog_available"] is False:
        result["ready"] = False
    elif result["connections"] is None or result["catalog_available"] is None:
        result["ready"] = None
    else:
        result["ready"] = True
    return result


def _chunks(response, started):
    """Bound time and bytes even when a peer drips bytes without completing a line or response."""
    size = 0
    while True:
        if time.monotonic() - started > MAX_SECONDS:
            raise OntologyError("Auto-Ontology exceeded the question time limit; no request was retried")
        chunk = response.read1(min(65536, MAX_BYTES - size + 1))
        if time.monotonic() - started > MAX_SECONDS:
            raise OntologyError("Auto-Ontology exceeded the response time limit; no request was retried")
        size += len(chunk)
        if size > MAX_BYTES:
            raise OntologyError("Auto-Ontology's response exceeded the size limit")
        if not chunk:
            return
        yield chunk


def _lines(response, started):
    """SSE lines, including the final unterminated event."""
    pending = b""
    for chunk in _chunks(response, started):
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            yield line.rstrip(b"\r") + b"\n"
    if pending:
        yield pending
    yield b""  # flush a final unterminated event


def _result(response, started):
    """Parse bounded SSE events, accepting CRLF, comments, multiline data and the final unterminated event."""
    lines = []
    for line in _lines(response, started):
        if line and line.strip():
            if line.startswith(b"data:"):
                lines.append(line[5:].lstrip().rstrip(b"\r\n"))
            continue
        if lines:
            data, lines = b"\n".join(lines), []
            if data == b"[DONE]":
                break
            try:
                event = json.loads(data)
            except (ValueError, UnicodeError):
                raise OntologyError("Auto-Ontology returned an invalid event stream") from None
            if not isinstance(event, dict):
                raise OntologyError("Auto-Ontology returned an invalid event stream")
            if event.get("type") == "error":
                raise OntologyError("Auto-Ontology could not answer the question; check its deployment logs")
            if event.get("type") == "result":
                answer = event.get("answer")
                if not isinstance(answer, dict) or not isinstance(answer.get("response"), str):
                    raise OntologyError("Auto-Ontology returned an invalid answer")
                return answer
        if not line:
            break
    raise OntologyError("Auto-Ontology ended without an answer; no request was retried")


def _rows(value, limit):
    # Upstream may return JSON records as one string inside a list, a dict, or a list of dicts.
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        value = value[0]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {"rows": [], "row_count": None, "rows_unavailable": True, "truncated": False}
    if isinstance(value, dict):
        value = [value]
    if value is None:
        value = []
    if not isinstance(value, list):
        return {"rows": [], "row_count": None, "rows_unavailable": True, "truncated": False}
    return {"rows": value[:limit], "row_count": len(value), "truncated": len(value) > limit}


def call(server, args):
    """Only the five fixed read operations; never execute model SQL inside Lens or retry a paid request."""
    base, _ = config(server)
    name = server.get("tool")
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None or not isinstance(args, dict) or set(args) - set(tool["schema"]["properties"]):
        raise OntologyError("unknown Auto-Ontology tool or arguments")
    return _call(server, args, base, name)


def _call(server, args, base, name):
    if name == "check_readiness":
        result = _readiness(server)
    elif name == "search_terms":
        query = args.get("query", "")
        if not isinstance(query, str) or len(query) > 500:
            raise OntologyError("query must be text of at most 500 characters")
        params = {
            "query": query,
            "limit": _integer(args.get("limit", 20), "limit", 1, 100),
            "skip": _integer(args.get("offset", 0), "offset", 0, 10000),
        }
        result = _json(server, "/api/terms?" + urllib.parse.urlencode(params))
        if not isinstance(result.get("terms"), list):
            raise OntologyError("Auto-Ontology returned an invalid terms list")
    elif name == "get_term":
        term = _text(args.get("term_id"), "term_id", 200)
        if any(c in term for c in "/\\%?#") or term in (".", ".."):
            raise OntologyError("term_id must be one exact term ID, not a path")
        result = _json(server, "/api/terms/" + urllib.parse.quote(term, safe=""))
        if not isinstance(result.get("data"), dict):
            raise OntologyError("Auto-Ontology returned an invalid term")
    else:
        question = _text(args.get("question"), "question")
        if name == "check_answerable":
            result = _json(server, "/api/question-entity-coverage", {"question": question})
            if not isinstance(result.get("data"), dict):
                raise OntologyError("Auto-Ontology returned invalid question coverage")
        else:
            limit = _integer(args.get("max_rows", 25), "max_rows", 1, 100)
            started = time.monotonic()
            try:
                with _open(server, "/api/chat/completions", {"question": question, "prediction": False}) as response:
                    answer = _result(response, started)
            except (OSError, http.client.HTTPException):
                raise OntologyError("Auto-Ontology's answer was interrupted; no request was retried") from None
            result = {"answer": answer["response"], "sql": answer.get("sql_code"), **_rows(answer.get("sql_response_from_db"), limit)}
    result = {**result, "source": base, "provider": "NVIDIA Auto-Ontology"}
    return {"text": json.dumps(result, ensure_ascii=False), "json": result}
