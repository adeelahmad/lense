"""Providers' own published API documents (tests/data/openapi/, each trimmed to the calls Lens makes and noting where
and when it was fetched), so a fake provider takes only the requests the real one would, and Lens reads only fields
the real one answers with."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

DATA = Path(__file__).parent / "data" / "openapi"


@cache
def load(name):
    return json.loads((DATA / f"{name}.json").read_text())


def _validator(spec, schema):
    doc = Resource.from_contents(spec, default_specification=DRAFT202012)
    registry = Registry().with_resource("urn:spec", doc)
    return jsonschema.Draft202012Validator({"$ref": "urn:spec" + schema["$ref"]} if "$ref" in schema else schema, registry=registry)


def operation(spec, method, path):
    """The operation for a request's path, matched against the document's templates (`/endpoint/{namespace}`)."""
    for template, ops in spec["paths"].items():
        if re.fullmatch(re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(template)), path) and method.lower() in ops:
            return ops[method.lower()]
    raise AssertionError(f"{method} {path} is not in {spec['info'].get('x-lens-source')}")


def check_request(spec, method, path, body):
    """Fail the test when the provider's document has no such call or wouldn't take this body."""
    op = operation(spec, method, path)
    schema = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
    if schema is None:
        assert body is None, f"{method} {path} takes no body"
        return
    errors = sorted(_validator(spec, schema).iter_errors(body), key=lambda e: list(e.path))
    assert not errors, f"{method} {path}: " + "; ".join(f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors[:5])


def check_response(spec, method, path, status, body):
    """Fail the test when a fake answers with something the provider's document says it never would."""
    op = operation(spec, method, path)
    resp = (op.get("responses") or {}).get(str(status))
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
    for path in dotted:
        schema = {"$ref": f"#/components/schemas/{schema_name}"}
        for part in path.split("."):
            props = _props(spec, schema)
            assert part in props, f"{schema_name} has no {path!r} in {spec['info'].get('x-lens-source')}"
            schema = props[part]
