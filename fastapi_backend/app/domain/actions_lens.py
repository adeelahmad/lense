"""Automation actions that change an asset or write a note (docs/automations.md, Actions): `set_field`,
`add_to_collection` and `write_note`.

They change something inside Lens (effect `lens`): on the asset the activity is about, in the automation's namespaces,
they run without asking; on anything else they ask first. A private note on an asset is only the person's (effect
`self`). Each needs the automation's person to be allowed it now (edit, or read for a private note), and each is
recorded as by the automation: a field's change in the asset's metadata history (so it can be reverted), the audit
log, a notebook page's writer.

Moving an asset into a collection also shows it to the people given a role on that collection: an automation never
does that on its own (it fails, saying who would newly see it), only once the person approved it.
"""

from __future__ import annotations

from . import actions, auth, fields, hierarchy, metadata, notebook, notes, store

ON = ("page", "asset")


def _short(text, n=80):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------- set_field ----------
def _field(db, rec, ref):
    """The custom field `ref` (an id, or a label) among those that apply to the asset, and the asset's row."""
    defs, row = fields.resource_fields(db, rec["id"])
    key = str("" if ref is None else ref).strip()
    f = next((d for d in defs if str(d["id"]) == key), None) or next((d for d in defs if d["label"].casefold() == key.casefold()), None)
    if not f:
        raise ValueError(f"no field {key or '(none named)'} describes asset {rec['id']}")
    return f, defs, row


def _set_field(db, cfg, intent, user, dry):
    p = intent["params"]
    acc = actions.person(db, intent, user)
    rec = actions.asset_for(db, intent, acc, actions.asset_id(p.get("asset")), "edit")
    f, defs, row = _field(db, rec, p.get("field"))
    before = row.get("fields") or {}
    after = fields.merged(row, defs, {f["id"]: p.get("value")})
    value = after.get(fields.slot(f["id"]))
    out = {"asset": rec["id"], "field": f["id"], "label": f["label"], "value": value, "changed": after != before}
    if dry or not out["changed"]:
        return {**out, "would": f"set {f['label']} on {rec.get('title') or rec['id']}"} if dry else out
    who = actions.actor(db, intent)
    metadata.save_fields(db, cfg, rec["id"], after, who["email"])
    auth.audit(db, who, "fields.save", f"recording:{rec['id']}", {"fields": [f["label"]], "for": acc["email"]})
    return out


# ---------- add_to_collection ----------
def _newcomers(db, rec, cid):
    """The people who would see the asset only through the collection it moves into: a role on it (or on one it's
    inside) and none in the namespace, on its collection now, or as admins."""
    gets = {m["account"]: m for m in hierarchy.members(db, cid)}
    if not gets:
        return []
    have = set(db.values("SELECT VALUE account FROM membership WHERE space = $s", s=rec["space"]))
    if rec.get("collection") is not None:
        have |= {m["account"] for m in hierarchy.members(db, rec["collection"])}
    admins = set(db.values("SELECT VALUE record::id(id) FROM account WHERE admin = true"))
    return [m for a, m in gets.items() if a not in have | admins]


def _add_to_collection(db, cfg, intent, user, dry):
    p = intent["params"]
    acc = actions.person(db, intent, user)
    rec = actions.asset_for(db, intent, acc, actions.asset_id(p.get("asset")), "edit")
    try:
        c = hierarchy.get(db, int(p["collection"]))
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"there's no collection {p.get('collection')}") from None
    if c["space"] != rec["space"]:
        raise ValueError(f"the collection {c['name']} is in another namespace than asset {rec['id']}")
    out = {"asset": rec["id"], "collection": c["id"], "name": c["name"], "moved": rec.get("collection") != c["id"]}
    new = _newcomers(db, rec, c["id"]) if out["moved"] else []
    if new:
        out["newly_see"] = [m.get("name") or m.get("email") for m in new]
    if dry:
        return {**out, "would": f"move {rec.get('title') or rec['id']} into {c['name']}"}
    if new and intent.get("risk") != "ask":
        raise ValueError(
            f"moving it into {c['name']} would show it to {', '.join(out['newly_see'][:5])}, who can't see it now: an "
            "automation does that only once you approve it"
        )
    if out["moved"]:
        hierarchy.place(db, [rec["id"]], c["id"])
        metadata.touched(db, cfg, rec["id"])
        auth.audit(
            db, actions.actor(db, intent), "recording.collection", f"collection:{c['id']}", {"recordings": [rec["id"]], "name": c["name"]}
        )
    return out


# ---------- write_note ----------
def _on(p):
    on = str(p.get("on") or "page")
    if on not in ON:
        raise ValueError("write_note: a note goes on a page or on the asset")
    return on


def _about(p):
    """A page's thing ('recording', 12), or None."""
    a = str(p.get("about") or "").strip()
    if not a:
        return None
    m = actions.ASSET_RX.match(a)
    if m:
        return "recording", int(m.group(1))
    kind, _, rest = a.removeprefix("lens:").replace("/", ":", 1).partition(":")
    if kind not in notebook.ABOUT or not rest.isdigit():
        raise ValueError(f"write_note: a page is about an asset, entity, topic, collection or speaker, not {a}")
    return kind, int(rest)


def _note_effect(p):
    on = str(p.get("on") or "page")
    return "self" if on == "asset" and not p.get("shared") else "lens"


def _note_subject(p):
    if str(p.get("on") or "page") == "asset":
        return actions.asset_subject(p)
    try:
        about = _about(p)
    except ValueError:
        return None
    return f"lens:{about[0]}/{about[1]}" if about else None


def _space_for(db, intent, acc, p, about):
    """The namespace a page goes in: its thing's, else the one named, else the automation's only one."""
    names = store.space_names(db)
    if about:
        try:
            sid = notebook.owner(db, *about)
        except KeyError:
            raise ValueError(f"there's no {about[0]} {about[1]}") from None
    elif p.get("namespace"):
        sid = {v: k for k, v in names.items()}.get(str(p["namespace"]))
        if sid is None:
            raise ValueError(f"there's no namespace {p['namespace']}")
    elif len(intent["spaces"]) == 1:
        sid = intent["spaces"][0]
    else:
        raise ValueError("write_note: name the namespace the page goes in")
    if intent["spaces"] and sid not in intent["spaces"]:
        raise ValueError(f"{names.get(sid, sid)} isn't one of this automation's namespaces")
    if not actions.allowed(db, acc, sid, "edit"):
        raise ValueError(f"{acc['name']} can't write pages in {names.get(sid, sid)}")
    return sid, names.get(sid)


def _write_note(db, cfg, intent, user, dry):
    p = intent["params"]
    acc = actions.person(db, intent, user)
    text = str(p.get("text") or "").strip()
    if not text:
        raise ValueError("write_note: the note is empty")
    if _on(p) == "asset":
        shared = bool(p.get("shared"))
        rec = actions.asset_for(db, intent, acc, actions.asset_id(p.get("asset")), "edit" if shared else "read")
        out = {"on": "asset", "asset": rec["id"], "shared": shared}
        if dry:
            return {**out, "note": None, "would": f"note on {rec.get('title') or rec['id']}: “{_short(text)}”"}
        # a note on an asset is its writer's: the person's, so they see it (and others only once shared)
        nid = notes.create(db, rec["id"], rec["space"], acc["id"], text, shared=shared)
        return {**out, "note": nid}
    about = _about(p)
    sid, ns = _space_for(db, intent, acc, p, about)
    who = actions.actor(db, intent)
    title = str(p.get("title") or "").strip() or _short(text.splitlines()[0], 80)
    have = (notebook.about(db, sid, f"{about[0]}:{about[1]}") or {}).get("id") if about else None
    out = {"on": "page", "namespace": ns, "about": f"{about[0]}:{about[1]}" if about else None}
    if dry:
        return {**out, "page": have, "would": (f"add to the page {have}" if have else f"write the page “{title}”") + f" in {ns}"}
    if have:  # a thing has one page: what the automation writes goes at its end, the page's history keeps the rest
        page = notebook.get(db, int(have))
        notebook.update(db, page["id"], who["id"], body=(page.get("body") or "").rstrip() + "\n\n" + text, author="assistant")
        return {**out, "page": page["id"], "added": True, "url": f"/notes/{page['id']}"}
    pid = notebook.create(db, sid, who["id"], title, text, about_thing=f"{about[0]}:{about[1]}" if about else None, author="assistant")
    return {**out, "page": pid, "added": False, "url": f"/notes/{pid}"}


actions.register(
    actions.ActionSpec(
        name="set_field",
        label="Set a field",
        group="Assets",
        description="Sets one of an asset's custom fields (kept in its history, so it can be put back).",
        params={
            "type": "object",
            "properties": {
                "field": {"title": "Field", "description": "the field's id or label"},
                "value": {"title": "Value", "description": "empty clears it"},
                "asset": actions.asset_param(),
            },
        },
        required=("field",),
        effect="lens",
        subject=actions.asset_subject,
        output={
            "type": "object",
            "properties": {
                "asset": {"type": "integer"},
                "field": {"type": "integer"},
                "label": {"type": "string"},
                "value": {},
                "changed": {"type": "boolean"},
            },
        },
        perform=_set_field,
        describe=lambda p: f"Set {p.get('field')} on {actions.on_asset(p)} to “{_short(p.get('value'), 60)}”",
    )
)

actions.register(
    actions.ActionSpec(
        name="add_to_collection",
        label="Move to a collection",
        group="Assets",
        description="Moves an asset into a collection of its namespace; never on its own when that shows it to new people.",
        params={
            "type": "object",
            "properties": {"collection": {"type": "integer", "title": "Collection"}, "asset": actions.asset_param()},
        },
        required=("collection",),
        effect="lens",
        subject=actions.asset_subject,
        output={
            "type": "object",
            "properties": {
                "asset": {"type": "integer"},
                "collection": {"type": "integer"},
                "name": {"type": "string"},
                "moved": {"type": "boolean"},
                "newly_see": {"type": "array", "items": {"type": "string"}},
            },
        },
        perform=_add_to_collection,
        describe=lambda p: f"Move {actions.on_asset(p)} into collection {p.get('collection')}",
    )
)

actions.register(
    actions.ActionSpec(
        name="write_note",
        label="Write a note",
        group="Lens",
        description=(
            "Writes a notebook page (or adds to the page of the thing it's about), or a note on an asset: a private one "
            "only you see runs at once."
        ),
        params={
            "type": "object",
            "properties": {
                "text": {"type": "string", "title": "Note", "maxLength": 20000, "description": "Markdown"},
                "on": {"type": "string", "title": "On", "enum": list(ON), "default": "page"},
                "title": {"type": "string", "title": "Title", "maxLength": 200, "description": "pages: empty takes the first line"},
                "about": {"type": "string", "title": "About", "description": "pages: lens:recording/<id>, entity:<id>, … (one page each)"},
                "namespace": {"type": "string", "title": "Namespace", "description": "pages: empty takes the automation's"},
                "asset": actions.asset_param(),
                "shared": {
                    "type": "boolean",
                    "title": "Shared",
                    "default": False,
                    "description": "asset notes: everyone who reads it sees it",
                },
            },
        },
        required=("text",),
        effect=_note_effect,
        subject=_note_subject,
        output={
            "type": "object",
            "properties": {
                "on": {"type": "string"},
                "page": {"type": "integer"},
                "note": {"type": "integer"},
                "added": {"type": "boolean"},
                "url": {"type": "string"},
            },
        },
        perform=_write_note,
        describe=lambda p: (
            (f"Note on {actions.on_asset(p)}" if p.get("on") == "asset" else "Write a page") + f": “{_short(p.get('text'), 60)}”"
        ),
    )
)
