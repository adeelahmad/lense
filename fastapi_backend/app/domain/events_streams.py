"""The machine side of Lens as activities (docs/automations.md, Kinds): what reaches Lens from outside that isn't a
file. Every kind here has its source named in the catalog; those read from the event log (`log`) are emitted where the
thing happens.

- `webhook.received`: a call to an automation's webhook address (automation_hooks.py), emitted by the route that
  takes it. Its payload and query are outside text.
"""

from __future__ import annotations

from . import events


def _s(title, typ="string", **more):
    return {"type": typ, "title": title, **more}


# ---------- webhooks ----------
events.kind(
    "webhook.received",
    label="Webhook called",
    source="log",
    as_type="Announce",
    object_type="Note",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "hook": _s("Webhook", "integer"),
                "payload": {"title": "What was sent"},
                "content_type": _s("Content type"),
                "query": {"type": "object", "title": "Query"},
            },
        }
    },
    untrusted=("object.payload", "object.query", "object.content_type"),
    sample={
        "id": "log-31",
        "type": "Announce",
        "kind": "webhook.received",
        "published": "2026-10-15T18:02:11.000Z",
        "actor": {"type": "Service", "id": "lens:automation_hook/2", "name": "Webhook for Front door"},
        "object": {
            "type": "Note",
            "hook": 2,
            "payload": {"event": "doorbell", "camera": "front"},
            "content_type": "application/json",
            "query": {},
        },
        "target": None,
        "tag": [],
        "summary": "Webhook for Front door was called",
        "space": 3,
        "lens": {
            "source": "log",
            "pos": ["2026-10-15T18:02:11+00:00", 31],
            "untrusted": ["object.payload", "object.query", "object.content_type"],
            "cause": None,
        },
    },
)
