"""Starter automations (docs/automations.md, Starters): ready-made drafts a person opens in the studio, changes and
saves. Nothing is stored or switched on: each is what POST /api/v1/automations takes, with `enabled` false, so picking
one changes nothing that runs until the person saves it and turns it on. Each starter's checks run in the tests, so a
change to the contract that breaks one fails there."""

from __future__ import annotations

import copy
from typing import Any


def _node(nid: str, t: str, **config: Any) -> dict[str, Any]:
    return {"id": nid, "type": t, "config": config}


def _edge(a: str, b: str) -> dict[str, str]:
    return {"source": a, "target": b}


def _graph(*steps: dict[str, Any], chain: bool = False) -> dict[str, Any]:
    """What happened, then each step: in a row when `chain`, else each straight after it."""
    nodes = [_node("in", "input"), *steps]
    edges = [_edge(nodes[i].get("id") if chain else "in", s["id"]) for i, s in enumerate(steps)]
    return {"nodes": nodes, "edges": edges}


def _notify(nid: str, text: str, to: str = "me") -> dict[str, Any]:
    return _node(nid, "action", action="notify", params={"to": to, "text": text})


STARTERS: list[dict[str, Any]] = [
    {
        "id": "school-emails",
        "name": "Tell me about school emails",
        "description": "An email from the school's address tells you in your chat app, and posting it to the family "
        "room waits for your yes. Change “school” to the school's address.",
        "draft": {
            "triggers": [
                {
                    "as": "mail",
                    "on": "event",
                    "kinds": ["email.received"],
                    "where": {"all": [{"path": "object.from.0", "op": "contains", "value": "school"}]},
                }
            ],
            "graph": _graph(
                _notify("tell", "School email: {{ event.object.subject }}"),
                _notify("family", "From school: {{ event.object.subject }}", to="gateway:family"),
            ),
        },
    },
    {
        "id": "router-alerts",
        "name": "Router alerts",
        "description": "An alert in the router's log tells you, at most once an hour. Change “router” to your sensor's name.",
        "draft": {
            "triggers": [
                {
                    "as": "alert",
                    "on": "event",
                    "kinds": ["sensor.alert"],
                    "where": {"all": [{"path": "actor.name", "op": "contains", "value": "router"}]},
                }
            ],
            "limits": {"max_per_hour": 1},
            "graph": _graph(_notify("tell", "{{ event.summary }}")),
        },
    },
    {
        "id": "morning-digest",
        "name": "Morning digest",
        "description": "Every weekday at 7:00 the agent sums up yesterday's timeline items you haven't reviewed, and tells you.",
        "draft": {
            "triggers": [{"as": "morning", "on": "schedule", "cron": "0 7 * * 1-5", "timezone": "UTC"}],
            "graph": _graph(
                _node(
                    "digest",
                    "action",
                    action="ask_agent",
                    params={
                        "instructions": "Read yesterday's timeline items I haven't reviewed and sum them up in a few "
                        "short lines, the ones that need me first."
                    },
                ),
                _notify("tell", "Your morning digest:\n{{ steps.digest.answer }}"),
                chain=True,
            ),
        },
    },
    {
        "id": "no-reply",
        "name": "No reply in two days",
        "description": "An email that asks you something, with no email sent from your mailbox within two days, "
        "reminds you. Any email you send in that time calls it off.",
        "draft": {
            "triggers": [
                {
                    "as": "mail",
                    "on": "event",
                    "kinds": ["email.received"],
                    "where": {"all": [{"path": "object.snippet", "op": "contains", "value": "?"}]},
                },
                {
                    "as": "sent",
                    "on": "event",
                    "kinds": ["email.received"],
                    "where": {"all": [{"path": "object.mailbox", "op": "contains", "value": "Sent"}]},
                },
            ],
            "join": {"mode": "absent", "after": "mail", "missing": "sent", "within": "2d"},
            "graph": _graph(_notify("remind", "No reply yet to {{ event.object.from.0 }}: {{ event.object.subject }}")),
        },
    },
    {
        "id": "webhook-note",
        "name": "Webhook to note",
        "description": "A call to its webhook writes a note with what was sent. Its address shows once you save it.",
        "draft": {
            "triggers": [{"as": "hook", "on": "webhook"}],
            "graph": _graph(
                _node(
                    "note",
                    "action",
                    action="write_note",
                    params={"text": "{{ event.object }}", "title": "From the webhook, {{ now_local }}"},
                )
            ),
        },
    },
]


def starters() -> list[dict[str, Any]]:
    """Every starter, each with its draft named and switched off, as POST /api/v1/automations takes it."""
    out = []
    for s in STARTERS:
        draft = {"name": s["name"], "enabled": False, "join": {"mode": "any"}, **copy.deepcopy(s["draft"])}
        out.append({"id": s["id"], "name": s["name"], "description": s["description"], "draft": draft})
    return out
