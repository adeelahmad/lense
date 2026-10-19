"""The automation action that shares with the person's circles (docs/circles.md, Rules): `share_with_circle`.

It is how a person's agent tells their family what they need to know without being asked each time: an automation
that follows the school's emails, a calendar's events or a sensor shares what came in with a circle, with a note. It
reaches other people (effect `out`), so it asks the person first, unless they set that node to run on its own; that
pick is theirs alone (automations.set_policy). It shares as the automation's person, only what they can see now in the
automation's namespaces, never from a vault, and is audited as by the automation.
"""

from __future__ import annotations

from . import actions, auth, circles, events

EVENT = "{{ event.id }}"


def _short(text, n=60):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _list(v):
    return [v] if isinstance(v, (str, int)) else list(v or [])


def _viewer(db, intent, acc):
    """The timeline as the person reads it within the automation's namespaces (all of theirs when it names none)."""
    if acc["admin"] and not intent["spaces"]:
        return events.viewer(acc["id"], None, admin=True)
    if acc["admin"]:
        return events.viewer(acc["id"], set(intent["spaces"]))
    readable, _ = actions.reach(db, intent, acc)
    return events.viewer(acc["id"], readable)


def _share(db, cfg, intent, user, dry):
    p = intent["params"]
    acc = actions.person(db, intent, user)
    what = {"recording": actions.asset_id(p["asset"])} if p.get("asset") not in (None, "") else {"activity": str(p["activity"])}
    args = {
        **what,
        "circles_": _list(p.get("circles")),
        "people_": _list(p.get("people")),
        "note": p.get("note"),
        "with_item": bool(p.get("with_item")),
        "who": _viewer(db, intent, acc),
    }
    try:
        if dry:
            got = circles.preview(db, cfg, acc, **args)
            return {"would": f"share “{_short(got['object'].get('name'))}” with {', '.join(got['circles'] or ['them'])}", **got}
        out = circles.share(db, cfg, acc, **args, via="automation")
    except KeyError as e:
        missing = str(e.args[0] if e.args else "")
        if missing.startswith("circle "):
            raise ValueError(f"{acc['name']} has no {missing}") from None
        raise ValueError(f"{acc['name']} can't see {missing} in this automation's namespaces") from None
    except (circles.Problem, PermissionError) as e:
        raise ValueError(str(e)) from None
    by = actions.actor(db, intent)
    auth.audit(
        db,
        by,
        "circle.share",
        f"circle_share:{out['id']}",
        {"to": len(out["people"]), "with_item": out["with_item"], "what": what, "for": acc["email"]},
    )
    return {"share": out["id"], "circles": out["circles"], "people": out["people"], "granted": out["granted"]}


def _describe(p):
    whom = ", ".join(map(str, _list(p.get("circles")) + _list(p.get("people")))) or "your circles"
    what = "the asset" if p.get("asset") not in (None, "") else "it"
    note = f": “{_short(p.get('note'))}”" if p.get("note") else ""
    return f"Share {what} with {whom}{note}"


actions.register(
    actions.ActionSpec(
        name="share_with_circle",
        label="Share with a circle",
        group="Messages",
        description=(
            "Shares the activity (or an asset) with your circles and a note: each of them sees it in their own timeline. "
            "It asks you first, unless you let it run on its own."
        ),
        params={
            "type": "object",
            "properties": {
                "circles": {
                    "type": "array",
                    "title": "Circles",
                    "items": {"type": "string"},
                    "description": "your circles, by name or id",
                },
                "people": {
                    "type": "array",
                    "title": "People",
                    "items": {"type": "string"},
                    "description": "people in your circles, by email or account id",
                },
                "note": {"type": "string", "title": "Note", "maxLength": circles.NOTE_MAX},
                "activity": {
                    "type": "string",
                    "title": "Activity",
                    "default": EVENT,
                    "maxLength": 200,
                    "description": "the activity to share: the one the run is on, unless an asset is given",
                },
                "asset": actions.asset_param("Asset (instead of the activity)") | {"default": None},
                "with_item": {
                    "type": "boolean",
                    "title": "With the item",
                    "default": False,
                    "description": "also let them open the asset (needs publish in its namespace)",
                },
            },
        },
        effect="out",
        output={
            "type": "object",
            "properties": {
                "share": {"type": "integer"},
                "circles": {"type": "array", "items": {"type": "string"}},
                "people": {"type": "array", "items": {"type": "object"}},
                "granted": {"type": "integer"},
            },
        },
        perform=_share,
        describe=_describe,
    )
)
