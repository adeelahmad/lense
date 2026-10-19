"""Providers' own published API documents (tests/data/openapi/, each trimmed to the calls Lens makes and noting where
and when it was fetched), so a fake provider takes only the requests the real one would, and Lens reads only fields
the real one answers with."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

DATA = Path(__file__).parent / "data" / "openapi"


@cache
def load(name):
    return json.loads((DATA / f"{name}.json").read_text())


def _absolute(x):
    """The schema with its references pointing into the whole document, wherever the schema sits in it."""
    if isinstance(x, dict):
        return {k: ("urn:spec" + v if k == "$ref" and isinstance(v, str) and v.startswith("#") else _absolute(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [_absolute(v) for v in x]
    return x


def _validator(spec, schema):
    doc = Resource.from_contents(spec, default_specification=DRAFT202012)
    return jsonschema.Draft202012Validator(_absolute(schema), registry=Registry().with_resource("urn:spec", doc))


def _deref(spec, x):
    """A response or request body the document names by reference (`#/components/responses/droplet_create`)."""
    while isinstance(x, dict) and "$ref" in x and not x["$ref"].startswith("#/components/schemas/"):
        _, _, kind, name = x["$ref"].split("/", 3)
        x = spec["components"][kind][name]
    return x


def operation(spec, method, path):
    """The operation for a request's path, matched against the document's templates (`/endpoint/{namespace}`), after
    the base path of the document's server (`/v1` of `https://api.hetzner.cloud/v1`)."""
    base = urlparse((spec.get("servers") or [{}])[0].get("url") or "").path.rstrip("/")
    if base and path.startswith(base + "/"):
        path = path[len(base) :]
    for template, ops in spec["paths"].items():
        if re.fullmatch(re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(template)), path) and method.lower() in ops:
            return {**ops[method.lower()], "x-path-parameters": ops.get("parameters") or []}
    raise AssertionError(f"{method} {path} is not in {spec['info'].get('x-lens-source')}")


def check_request(spec, method, path, body, params=None):
    """Fail the test when the provider's document has no such call, or wouldn't take this body or query."""
    op = operation(spec, method, path)
    known = {_deref(spec, x).get("name") for x in (op.get("parameters") or []) + (op.get("x-path-parameters") or [])}
    unknown = set(params or ()) - known
    assert not unknown, f"{method} {path} takes no query {sorted(unknown)}"
    content = (_deref(spec, op.get("requestBody")) or {}).get("content") or {}
    schema = (content.get("application/json") or content.get("application/x-www-form-urlencoded") or {}).get("schema")
    if schema is None:
        assert body is None, f"{method} {path} takes no body"
        return
    if body is None and not _required(spec, schema):  # nothing it needs: a call may send no body
        return
    errors = sorted(_validator(spec, schema).iter_errors(body), key=lambda e: list(e.path))
    assert not errors, f"{method} {path}: " + "; ".join(f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors[:5])


def form(spec, method, path, raw):
    """A form body (`application/x-www-form-urlencoded`) as the fields the operation's schema names, each read as its
    type says (`newid=105` is 105 for an integer, a repeated field is a list), for check_request."""
    from urllib.parse import parse_qs

    op = operation(spec, method, path)
    content = (_deref(spec, op.get("requestBody")) or {}).get("content") or {}
    props = ((content.get("application/x-www-form-urlencoded") or {}).get("schema") or {}).get("properties") or {}
    out = {}
    for k, vs in parse_qs(raw.decode() if isinstance(raw, bytes) else raw or "", keep_blank_values=True).items():
        t = (props.get(k) or {}).get("type") or ("boolean" if "anyOf" in (props.get(k) or {}) else None)
        if t == "array":
            out[k] = vs
            continue
        v = vs[-1]
        try:
            out[k] = int(v) if t in ("integer", "boolean") else float(v) if t == "number" else v
        except ValueError:
            out[k] = v  # not the type: check_request says so
    return out


def check_response(spec, method, path, status, body):
    """Fail the test when a fake answers with something the provider's document says it never would."""
    op = operation(spec, method, path)
    resp = _deref(spec, (op.get("responses") or {}).get(str(status)))
    assert resp is not None, f"{method} {path} never answers {status}"
    schema = ((resp.get("content") or {}).get("application/json") or {}).get("schema")
    if schema is not None:
        errors = sorted(_validator(spec, schema).iter_errors(body), key=lambda e: list(e.path))
        assert not errors, f"{method} {path} answer: " + "; ".join(f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors[:5])


def _props(spec, schema):
    """A schema's properties, following $ref, allOf and the object branches of oneOf/anyOf."""
    if "$ref" in schema:
        return _props(spec, spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]])
    out = dict(schema.get("properties") or {})
    for part in (schema.get("allOf") or []) + (schema.get("oneOf") or []) + (schema.get("anyOf") or []):
        out |= _props(spec, part)
    if schema.get("type") == "array" or "items" in schema:
        out |= _props(spec, schema.get("items") or {})
    return out


def check_fields(spec, schema_name, *dotted):
    """Fail the test unless the named schema has each field Lens reads (`status.url`, `vendors.regions.computes.id`)."""
    _fields(spec, {"$ref": f"#/components/schemas/{schema_name}"}, schema_name, dotted)


def check_answer_fields(spec, method, path, status, *dotted):
    """Fail the test unless the operation's answer has each field Lens reads (`server.public_net.ipv4.ip`)."""
    resp = _deref(spec, operation(spec, method, path)["responses"][str(status)])
    _fields(spec, resp["content"]["application/json"]["schema"], f"{method} {path}", dotted)


def _fields(spec, root, schema_name, dotted):
    for path in dotted:
        schema = root
        for part in path.split("."):
            props = _props(spec, schema)
            assert part in props, f"{schema_name} has no {path!r} in {spec['info'].get('x-lens-source')}"
            schema = props[part]


_SAMPLES = {
    "date-time": "2026-10-18T12:00:00Z",
    "date": "2026-10-18",
    "ipv4": "203.0.113.7",
    "ipv6": "2001:db8::1",
    "uuid": "00000000-0000-4000-8000-000000000000",
    "uri": "https://example.com/",
    "email": "ada@example.com",
}


def _sample(spec, schema, given=None, depth=0):
    """The smallest value the schema allows (required fields only, the first enum value or branch), with `given` over
    it: a field given that the schema doesn't require is filled in the same way."""
    if given is not None and not isinstance(given, (dict, list)):
        return given
    if "$ref" in schema:
        return _sample(spec, spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]], given, depth)
    if "allOf" in schema:
        props = {}
        for part in schema["allOf"]:
            props |= _props(spec, part)
        return _object(spec, props, _required(spec, schema), given, depth)
    for key in ("oneOf", "anyOf"):
        if key in schema:
            branches = [b for b in schema[key] if b.get("type") != "null"] or schema[key]
            return _sample(spec, branches[0], given, depth)
    if "enum" in schema:
        return next((v for v in schema["enum"] if v is not None), None)
    if "const" in schema:
        return schema["const"]
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object" or (t is None and "properties" in schema):
        return _object(spec, schema.get("properties") or {}, schema.get("required") or [], given, depth)
    if t == "array" and isinstance(given, list):
        return [_sample(spec, schema.get("items") or {}, g, depth + 1) for g in given]
    if t == "array":
        return [_sample(spec, schema["items"], None, depth + 1)] if (schema.get("minItems") or 0) > 0 and depth < 8 else []
    if t == "string":
        if schema.get("pattern"):  # the first of a few plain strings it takes
            return next((c for c in ("a", "a1", "1", "x-1", "A") if re.search(schema["pattern"], c)), "a")
        return _SAMPLES.get(schema.get("format"), "x" * (schema.get("minLength") or 0))
    if t in ("integer", "number"):
        return schema.get("minimum", 0)
    if t == "boolean":
        return False
    return given


def _required(spec, schema):
    if "$ref" in schema:
        return _required(spec, spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]])
    return list(schema.get("required") or []) + [r for part in schema.get("allOf") or [] for r in _required(spec, part)]


def _object(spec, props, required, given, depth):
    given = given or {}
    return {k: _sample(spec, props.get(k, {}), given.get(k), depth + 1) for k in dict.fromkeys([*required, *given])}


def answer(spec, method, path, status, fields=None):
    """A whole answer the provider could give, with `fields` over the smallest one its document allows; checked. None
    for a status the document gives no body."""
    op = operation(spec, method, path)
    resp = _deref(spec, (op.get("responses") or {}).get(str(status))) or {}
    schema = ((resp.get("content") or {}).get("application/json") or {}).get("schema")
    if schema is None:  # an answer with no body (204)
        assert fields is None, f"{method} {path} answers {status} with no body"
        return None
    body = _sample(spec, schema, fields or {})
    check_response(spec, method, path, status, body)
    return body


def sample(spec, method, path, status, key, fields=None):
    """One object of an answer (`droplet` of `GET /v2/droplets/{id}`), filled in as `answer` does."""
    return answer(spec, method, path, status, {key: fields or {}})[key]
