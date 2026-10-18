"""The assistant's tools for running the server: what's set up and what isn't, the settings, namespaces, and files
dropped into the conversation.

Admins get the server tools; anyone gets `import_files` for their own attachments and `import_urls` for web pages,
into namespaces they edit, and `import_runs` and `undo_import` for their bulk imports (import_runs.py). A change
waits for the person's approval, unless the conversation lets the assistant act (a setup conversation does: the admin
asked it to set things up): then it's made at once, audited, and said in the answer, as long as the answer hasn't read
text from outside Lens (Toolbox.wary), which could be what asked for it. Telemetry, which sends data
elsewhere, always waits for approval.
"""

from __future__ import annotations

from . import (
    auth,
    blobs,
    content_types,
    convert,
    decide,
    feeds,
    import_runs,
    jobs,
    links,
    llm,
    mesh,
    metadata,
    roles,
    rss,
    scaling,
    secret_providers,
    settings,
    setup,
    sources,
    store,
    uploads,
)

R = store.R
_S = {"type": "string"}
# what the assistant may change: the processing and AI settings. Not the server's hosts, cookies or tokens, which
# could lock people out, and not the IIIF publishing settings.
SECTIONS = (
    "llm",
    "embeddings",
    "search",
    "transcribe",
    "diarize",
    "speakers",
    "analysis",
    "graph",
    "video",
    "enhance",
    "uploads",
    "files",
    "ai",
    "reports",
    "notifications",
    "events",
    "automations",
    "workers",
    "decisions",
    "voice",
    "components",
    "mail",
    "bridge",
    "mailbox",
    "matterbridge",
    "telemetry",
    "analytics",
    "sensors",
    "shares",
    "web",
    "secrets",
    "scaling",
)
# telemetry, analytics and web search send data elsewhere; sensors and shares open ports on the network; files moves
# where new files go; secrets is where Lens signs in to a password manager; scaling turns on what can launch paid
# machines
ALWAYS_ASK = {"telemetry", "analytics", "sensors", "shares", "files", "web", "secrets", "scaling"}
# who gets in always waits for the admin's approval: a person, or a machine joining the mesh; and so does taking a
# machine off it, which stops what runs there; and deleting a whole import
ASK_TOOLS = {"invite_person", "change_person", "add_mesh_node", "remove_mesh_node", "let_go_scaling_stray", "undo_import"}
# within a scaling group's approved terms (its min, max and budget), or a change to the mesh that's undone as easily
# (renaming a node, the namespaces it serves, bringing a removed one back, withdrawing a token), so made at once
# unless the answer read outside text
WITHIN_BOUNDS = {
    "set_scaling_desired",
    "pause_scaling_group",
    "terminate_scaling_unit",
    "change_mesh_node",
    "restore_mesh_node",
    "withdraw_mesh_token",
}
# what a change's result shows the person who approved it once and never keeps (in the approval or the audit log):
# a new node's install line, which holds its join token
SHOWN_ONCE = ("install_line",)
_GROUP = {
    "type": "object",
    "description": "name, provider (its id), template (what each unit is, as the provider's driver asks), min, max, "
    "desired, cooldown_seconds, warmup_seconds, terminate (oldest|newest), rules, at_cap (stop|min), enabled, budget "
    "(USD a month, needed on a provider that charges). Rules: {type: target, metric, target}, {type: step, metric, "
    'above|below, minutes, add|remove (a count or "50%")}, {type: schedule, cron, timezone, min, max, desired}.',
}
SCALING_TOOLS = [
    (
        "scaling",
        "Auto scaling: whether it's on, the providers (where units launch: this machine, clouds, Proxmox, a Linux host, "
        "or model hosts), the scaling groups with their rules in words, budgets and why one launches nothing now, the "
        "units running, and the metrics rules can watch (" + ", ".join(scaling.METRICS) + ").",
        {},
        [],
        False,
    ),
    (
        "propose_scaling_group",
        "Propose a scaling group: units launched on a provider between min and max, by its rules, within its budget. "
        "It launches nothing until the person approves its terms in the web app (an approval of this proposal there "
        "does that); you can't approve them. Read scaling first for the providers and metrics.",
        {"group": _GROUP},
        ["group"],
        True,
    ),
    (
        "change_scaling_group",
        "Change a scaling group (its id from scaling): only the fields given change. A change that widens what the "
        "person approved (another provider or unit, a higher max or budget) waits for their approval again.",
        {"group_id": {"type": "integer"}, "changes": _GROUP},
        ["group_id", "changes"],
        True,
    ),
    (
        "set_scaling_desired",
        "Ask a scaling group for a number of units now, within its min and max; its rules take over again at their next change.",
        {"group_id": {"type": "integer"}, "desired": {"type": "integer", "minimum": 0}},
        ["group_id", "desired"],
        True,
    ),
    (
        "pause_scaling_group",
        "Pause a scaling group (nothing new launches; what runs stays) or resume it (paused false).",
        {"group_id": {"type": "integer"}, "paused": {"type": "boolean"}, "why": _S},
        ["group_id"],
        True,
    ),
    (
        "terminate_scaling_unit",
        "Let one unit of a scaling group go now, by its id from scaling: a worker finishes or hands back its job first.",
        {"unit_id": {"type": "integer"}, "why": _S},
        ["unit_id"],
        True,
    ),
    (
        "let_go_scaling_stray",
        "Ask the person to let a stray go: a unit a provider runs with Lens's tag that Lens has no unit for (a provider's "
        "strays in scaling), terminated at the provider once they approve in the web app.",
        {"provider_id": {"type": "integer"}, "stray_id": _S},
        ["provider_id", "stray_id"],
        True,
    ),
]

_NAMESPACES = {"type": "array", "items": _S, "description": "the namespaces it serves (empty: every one)"}
MESH_TOOLS = [
    (
        "mesh",
        "Lens's private network between the machines it runs on (Settings › Nodes and Network): whether it's on, its "
        "CA's fingerprint and expiry, this machine's address on it, every machine (id, name, role worker or compute, "
        "address, online and last seen, Nebula, its Lens version and machine, the namespaces it serves, whether a "
        "compute node's sandbox gateway runs, its error, when it was removed) and the join tokens not used yet.",
        {},
        [],
        False,
    ),
    (
        "add_mesh_node",
        "Let another machine join the mesh: makes a join token and the line to run on that machine, which the person "
        "sees once they approve (you don't). role worker runs jobs against this install's database; compute runs your "
        "code in sandboxes and gets nothing of Lens's. Turns the mesh on when it's off.",
        {
            "role": {"type": "string", "enum": list(mesh.ROLES)},
            "name": {"type": "string", "description": "the name it shows as (none: the name the machine sends)"},
            "namespaces": _NAMESPACES,
            "hours": {
                "type": "integer",
                "minimum": mesh.TOKEN_HOURS[0],
                "maximum": mesh.TOKEN_HOURS[1],
                "description": "how long the token works (24)",
            },
            "uses": {
                "type": "integer",
                "minimum": mesh.TOKEN_USES[0],
                "maximum": mesh.TOKEN_USES[1],
                "description": "machines that may join with it (1)",
            },
        },
        [],
        True,
    ),
    (
        "change_mesh_node",
        "Rename a machine on the mesh (its id from mesh), or change the namespaces it serves (empty: every one).",
        {"node_id": {"type": "integer"}, "name": _S, "namespaces": _NAMESPACES},
        ["node_id"],
        True,
    ),
    (
        "remove_mesh_node",
        "Take a machine off the mesh (its id from mesh): its certificates are blocked on every machine and its "
        "credentials stop working. restore_mesh_node brings it back while its certificate is good.",
        {"node_id": {"type": "integer"}},
        ["node_id"],
        True,
    ),
    (
        "restore_mesh_node",
        "Bring back a machine taken off the mesh (its id from mesh), while its certificate is still good.",
        {"node_id": {"type": "integer"}},
        ["node_id"],
        True,
    ),
    (
        "withdraw_mesh_token",
        "Withdraw a join token not used up yet (its id from mesh): no machine can join with it any more.",
        {"token_id": {"type": "integer"}},
        ["token_id"],
        True,
    ),
]

ADMIN_TOOLS = [
    (
        "server_status",
        "What this Lens server has set up and what it still needs: the model provider, search by meaning, namespaces, "
        "sources, assets, the job queue. Start here when setting up or troubleshooting.",
        {},
        [],
        False,
    ),
    (
        "find_model_servers",
        "Model servers running on this machine or the Docker host (Ollama, LM Studio, llama.cpp, vLLM, LocalAI), with their "
        "models, and the cloud providers Lens knows (OpenAI, Anthropic, Gemini, ...) with their address and a model: "
        "a cloud one also needs the admin's API key.",
        {},
        [],
        False,
    ),
    (
        "read_settings",
        "The current values of one settings section (secrets show only whether they're set). Sections: " + ", ".join(SECTIONS) + ".",
        {"section": {"type": "string", "enum": list(SECTIONS)}},
        ["section"],
        False,
    ),
    (
        "change_settings",
        "Change settings in one section, e.g. llm {base_url, model, api_key}, embeddings {base_url, model}, transcribe {model}. "
        "Read the section first: keys and types must match it.",
        {"section": {"type": "string", "enum": list(SECTIONS)}, "changes": {"type": "object"}},
        ["section", "changes"],
        True,
    ),
    (
        "file_storage",
        "Where Lens keeps the files it makes its own (notes' attachments): this machine, or a storage connection (S3, "
        "Google Drive, Dropbox, OneDrive, SFTP, SMB, WebDAV) under a folder, optionally wrapped in rclone crypt; and the "
        "connections it could use. check: write, read back and remove a small file there. Change it with change_settings "
        "files {store: local|connection, connection, folder, crypt}; a new connection is added in Settings → Sources.",
        {"check": {"type": "boolean"}},
        [],
        False,
    ),
    (
        "news_feeds",
        "The news feeds (RSS or Atom) Lens follows: each one's id, name and title, whether it could be read last time, "
        "and the namespaces it brings new posts into, with how the last scan went.",
        {},
        [],
        False,
    ),
    (
        "list_secrets",
        "Every secret field in Settings (API keys, tokens, passwords): whether it's set, and the reference it holds when it "
        "comes from a password manager (like vaultwarden://OpenAI/password) with whether that could be read; and the password "
        "managers Lens can read from. Never a value. A secret field takes a reference through change_settings, e.g. llm "
        "{api_key: 'vaultwarden://OpenAI/password'}; the manager is set up with change_settings secrets.",
        {},
        [],
        False,
    ),
    (
        "subscribe_feed",
        "Follow a news feed (RSS or Atom) into a namespace: url is the feed's address, or a page of the site that names "
        "it. It's read first and kept only if it works, named by the feed's title unless name says. Each new post then "
        "comes in as text with its link; backfill also brings in the posts the feed lists now. A feed that needs a "
        "password is added on the Sources page.",
        {"url": _S, "namespace": _S, "name": _S, "backfill": {"type": "boolean"}},
        ["url", "namespace"],
        True,
    ),
    (
        "unsubscribe_feed",
        "Stop following a news feed, by its id from news_feeds. The posts it brought in stay.",
        {"feed": {"type": "integer"}},
        ["feed"],
        True,
    ),
    (
        "vault_items",
        "The items of the password manager Lens may refer to (the folder it reads): each one's name, type, the fields that "
        "hold something and the reference to each, never their values. q: part of a name.",
        {"q": _S, "provider": {"type": "string", "enum": ["vaultwarden"]}},
        [],
        False,
    ),
    (
        "create_namespace",
        "Create a namespace: a separate set of assets (recordings, documents and images) with its own people and access. "
        "Name: lowercase letters, digits, - and _. graph: shared (people and places joined across namespaces) or isolated.",
        {"name": _S, "graph": {"type": "string", "enum": ["shared", "isolated"]}},
        ["name"],
        True,
    ),
    (
        "list_people",
        "The people with an account here: email, name, admin, disabled, whether they have signed in yet, and their role in each namespace.",
        {},
        [],
        False,
    ),
    (
        "invite_person",
        "Add someone by email, optionally with a role in one namespace (viewer, editor or owner). Lens emails them a "
        "sign-in link when mail is set up; otherwise an admin sends one from Admin → People. No passwords.",
        {"email": _S, "name": _S, "namespace": _S, "role": {"type": "string", "description": "viewer, editor, owner or a custom role"}},
        ["email"],
        True,
    ),
    (
        "change_person",
        "Change someone's access: their role in one namespace (role none takes it away), until when it holds, or "
        "disable or enable their account (disabling signs them out everywhere and can be undone). Removing an account "
        "for good is for the web app or the API.",
        {
            "email": _S,
            "namespace": _S,
            "role": {"type": "string", "description": "viewer, editor, owner, a custom role, or none"},
            "until": {
                "type": "string",
                "description": "when the role ends (ISO date and time, UTC unless it says otherwise); empty: no end; "
                "left out: the end it had",
            },
            "disabled": {"type": "boolean"},
        },
        ["email"],
        True,
    ),
]
ADMIN_TOOLS += SCALING_TOOLS + MESH_TOOLS

FILE_TOOLS = [
    (
        "import_files",
        "Put files the person attached to the conversation into a namespace, where they're transcribed or read like any "
        "upload. Use the attachment ids from their message. Leave namespace out unless the person named one: it's "
        "chosen for them, and you're told when it's unclear and to ask. Give content_type (a key from the content "
        "types, of the files' base type) only when the person says what the files are, e.g. podcast or interview: "
        "otherwise it's recognised from each file.",
        {"upload_ids": {"type": "array", "items": _S}, "namespace": _S, "collection_id": {"type": "integer"}, "content_type": _S},
        ["upload_ids"],
        True,
    ),
    (
        "import_urls",
        "Capture web pages as documents in a namespace, each kept as a PDF of the page as it is now and read like any "
        "document: one address, several, or a whole list. Give urls, or text holding them (a pasted list, one per line), "
        "or upload_id: an attached file listing them (a text list, a browser's bookmark export, Chrome's Bookmarks). "
        "Leave namespace out unless the person named one: it's chosen for them, and you're told when it's unclear and to ask. "
        "Give content_type (a text content type's key) only when the person says what the pages are; else it's recognised.",
        {
            "urls": {"type": "array", "items": _S},
            "text": _S,
            "upload_id": _S,
            "namespace": _S,
            "collection_id": {"type": "integer"},
            "content_type": _S,
        },
        [],
        True,
    ),
    (
        "import_runs",
        "Recent bulk imports (a folder scan, an archive's files, a list of links or bookmarks, a source's chosen "
        "files): each run's id, kind, namespace, what it read, who started it and when, how many assets it made and how "
        "many are still here. undo_import undoes one.",
        {"limit": {"type": "integer", "description": "how many, newest first (default 20)"}},
        [],
        False,
    ),
    (
        "undo_import",
        "Undo a bulk import whole: every asset it made that is still here is deleted, as deleting each one would. "
        "It needs owner rights on each, always waits for the person's yes, and can't itself be undone (importing the "
        "files again brings them back).",
        {"run": {"type": "integer", "description": "the run's id, from import_runs"}},
        ["run"],
        True,
    ),
]
LIST_BYTES = 5 * 2**20  # an attached list of links is read up to this


def status(db, cfg, base):
    """The server at a glance, with what's missing first."""
    view = setup.view(db, base)
    names = store.space_names(db)
    q = jobs.counts(db)
    missing = []
    if not llm.configured(cfg):
        missing.append("a model provider (chat, summaries and this assistant need one): find_model_servers, then change_settings llm")
    if not names:
        missing.append("a namespace to put assets in: create_namespace")
    emb = cfg.get("embeddings") or {}
    if not (emb.get("model") and (emb.get("base_url") or cfg["llm"].get("base_url"))):
        missing.append("search by meaning: an embedding model (embeddings.model, e.g. nomic-embed-text on Ollama)")
    recordings = db.values("SELECT VALUE count() FROM recording GROUP ALL")
    fetching = {}
    for w in jobs.workers(db):
        for cid, st in (w.get("components") or {}).items():
            if st.get("state") != "ready":
                fetching.setdefault(cid, {})[w["name"]] = {k: v for k, v in st.items() if v}
    return {
        "missing": missing,
        "setup_wizard_pending": view["pending"],
        # what the wizard still asks: llm (the model provider), budget (for a paid model)
        "setup_todo": view["todo"],
        "model_provider": {k: v for k, v in view["llm"]["values"].items() if k != "api_key"}
        | {"api_key_set": bool((view["llm"]["values"].get("api_key") or {}).get("set")), "locked_by_env": view["llm"]["locked"]},
        "namespaces": sorted(names.values()),
        "namespaces_locked_by_config": view["namespace"]["locked"],
        "recordings": recordings[0] if recordings else 0,
        "sources": len(db.values("SELECT VALUE id FROM storage_source")),
        "watched_folders": view["storage"]["watches"],
        "local_folders_allowed": view["storage"]["local_roots"],
        "max_upload_mb": view["storage"]["max_upload_mb"],
        "jobs": q,
        "telemetry_on": view["telemetry"]["enabled"],
        "google_analytics_on": view["telemetry"]["analytics"]["enabled"],
        # engines and models the workers are still fetching for themselves, or couldn't (Settings → Components)
        "components_not_ready": fetching,
    }


def apply(db, cfg, base, tool, args, user, editable, admin, via="agent"):
    """Carry out a change (approved, or in a conversation that lets the assistant act). Returns the result. `via` is
    where it was approved (web, chat, or agent when the assistant acts on its own): only web approves a scaling
    group's terms."""
    if tool in scaling.TOOLS:
        if not admin:
            raise PermissionError("only admins manage scaling")
        out = scaling.apply_tool(db, cfg, tool, args, user, via)
        table, what = next(
            ((t, out[k]) for k, t in (("group", "scale_group"), ("unit", "scale_node"), ("provider", "scale_provider")) if k in out)
        )
        auth.audit(db, user, f"scaling.{tool}", f"{table}:{what.get('id')}", {"via": via, "assistant": True})
        return out
    if tool in MESH:
        if not admin:
            raise PermissionError("only admins manage the mesh")
        return _mesh(db, cfg, base, tool, args, user)
    if tool == "change_settings":
        if not admin:
            raise PermissionError("only admins change settings")
        section, changes = args["section"], dict(args.get("changes") or {})
        changes = {k: v for k, v in changes.items() if k not in settings.locked(section)}
        if changes:
            settings.save(db, base, section, changes, user["email"])
            auth.audit(db, user, "settings.save", section, sorted(changes) + ["assistant"])
        return {"saved": sorted(changes)}
    if tool == "create_namespace":
        if not admin:
            raise PermissionError("only admins create namespaces")
        sid = setup.save_namespace(db, args["name"], args.get("graph") or "shared")
        auth.audit(db, user, "namespace.create", args["name"].strip().lower(), ["assistant"])
        return {"namespace": args["name"].strip().lower(), "id": sid}
    if tool == "import_files":
        ns = args["namespace"].strip().lower()
        try:
            sid = store.ns_id(db, ns, create=False)
        except KeyError:
            if not admin:
                raise PermissionError(f"there's no namespace {ns}") from None
            sid = None
        if sid is not None and not admin and sid not in editable:
            raise PermissionError(f"you can't add to {ns}")
        placed = []
        for uid in args["upload_ids"]:
            up = uploads.place(db, cfg, uid, ns, user["id"], admin, args.get("collection_id"), args.get("content_type"))
            auth.audit(db, user, "upload", f"recording:{up['recording']}", {"file": up["filename"], "namespace": ns, "assistant": True})
            placed.append({"file": up["filename"], "recording": up["recording"], "duplicate": bool(up.get("duplicate"))})
        return {"namespace": ns, "imported": placed}
    if tool == "subscribe_feed":
        if not admin:
            raise PermissionError("only admins add news feeds")
        out = rss.subscribe(db, cfg, args["url"], args["namespace"], args.get("name"), bool(args.get("backfill")), by=user["email"])
        if out["added"]:
            auth.audit(db, user, "source.create", f"storage_source:{out['feed']}", ["assistant"])
        if not out["already"]:
            auth.audit(db, user, "watch.create", f"watch_path:{out['watch']}", ["assistant"])
        return {k: out[k] for k in ("feed", "watch", "namespace", "posts", "added", "already")}  # not the feed's own words
    if tool == "unsubscribe_feed":
        if not admin:
            raise PermissionError("only admins remove news feeds")
        rss.unsubscribe(db, args["feed"])
        auth.audit(db, user, "source.delete", f"storage_source:{args['feed']}", ["assistant"])
        return {"unsubscribed": args["feed"]}
    if tool in ("invite_person", "change_person"):
        if not admin:
            raise PermissionError("only admins manage people")
        return _people(db, cfg, tool, args, user)
    if tool == "undo_import":
        run = _import_run(db, user, admin, args["run"])
        if import_runs.refused(db, run["id"], import_runs.may_remove(db, {"id": user.get("id"), "admin": admin})):
            raise PermissionError("undoing it needs an owner: some of its assets aren't yours to delete")
        out = import_runs.undo(db, cfg, run["id"], {"id": user.get("id"), "email": user.get("email")})
        return {"run": run["id"], "deleted": len(out["deleted"]), "busy": len(out["busy"])}
    if tool == "import_urls":
        ns = args["namespace"].strip().lower()
        try:
            sid = store.ns_id(db, ns, create=False)
        except KeyError:
            if not admin:
                raise PermissionError(f"there's no namespace {ns}") from None
            sid = store.ns_id(db, ns)
        if not admin and sid not in editable:
            raise PermissionError(f"you can't add to {ns}")
        if args.get("collection_id") is not None:
            store.home(db, sid, args["collection_id"])
        out = links.queue(
            db,
            cfg,
            sid,
            args["links"],
            user["email"],
            args.get("collection_id"),
            content_type=args.get("content_type"),
            source="the assistant's list",
        )
        queued = sum(r["status"] == "queued" for r in out)
        auth.audit(db, user, "import.web", f"space:{sid}", {"links": queued, "namespace": ns, "assistant": True})
        return {"namespace": ns, "pages": out}
    raise ValueError(f"no tool {tool}")


MESH = {t[0] for t in MESH_TOOLS if t[4]}


def kept(result):
    """A change's result as it's kept (in its approval and the audit log): without what's shown only once."""
    return {k: v for k, v in result.items() if k not in SHOWN_ONCE}


def _mesh(db, cfg, base, tool, args, user):
    """A change to the mesh (approved, or one made at once), audited as the web app's are."""
    by, nid = user["email"], args.get("node_id")
    try:
        if tool == "add_mesh_node":
            role = args.get("role") or "worker"
            terms = {k: args[k] for k in ("name", "namespaces", "hours", "uses") if args.get(k) is not None}
            made = mesh.add(db, cfg, base, by, role, url=args.get("url"), **terms)
            if made["turned_on"]:
                auth.audit(db, user, "settings.save", "mesh", ["enabled", "assistant"])
            tid = made["token"]["id"]
            auth.audit(db, user, "mesh.token.create", f"mesh_token:{tid}", {"role": role, "uses": made["token"]["uses"], "assistant": True})
            return {"token": made["token"], "install_line": made["install_line"], "turned_on": made["turned_on"]}
        if tool == "change_mesh_node":
            changes = {k: args[k] for k in ("name", "namespaces") if args.get(k) is not None}
            node = mesh.update(db, nid, **changes)
            auth.audit(db, user, "mesh.node.change", f"mesh_node:{nid}", {**changes, "assistant": True})
        elif tool == "remove_mesh_node":
            node = mesh.remove(db, nid, by=by)
            auth.audit(db, user, "mesh.node.remove", f"mesh_node:{nid}", ["assistant"])
        elif tool == "restore_mesh_node":
            node = mesh.undo_remove(db, nid, cfg)
            auth.audit(db, user, "mesh.node.restore", f"mesh_node:{nid}", ["assistant"])
        else:
            mesh.revoke_token(db, args["token_id"])
            auth.audit(db, user, "mesh.token.withdraw", f"mesh_token:{args['token_id']}", ["assistant"])
            return {"withdrawn": args["token_id"]}
    except mesh.MeshError as e:
        raise ValueError(str(e)) from None
    return {"node": {k: node[k] for k in ("id", "name", "role", "ip", "namespaces", "revoked_at")}}


def people(db):
    """Everyone with an account, for the assistant."""
    names = store.space_names(db)
    roles = {}
    for m in db.rows("SELECT account, space, role, expires_at FROM membership"):
        if not auth.live(m):  # an ended role opens nothing
            continue
        roles.setdefault(m["account"], {})[names.get(m["space"], str(m["space"]))] = m["role"]
    rows = db.rows(
        "SELECT record::id(id) AS id, email, name, admin, disabled, last_login_at FROM account WHERE removed_at = NONE ORDER BY id"
    )
    return [
        {
            "email": r["email"],
            "name": r.get("name"),
            "admin": bool(r.get("admin")),
            "disabled": bool(r.get("disabled")),
            "signed_in_yet": bool(r.get("last_login_at")),
            "roles": roles.get(r["id"], {}),
        }
        for r in rows
    ]


def _owned(db, user, admin):
    """The namespaces where this person may delete assets."""
    return [s for s, p in auth.permissions(db, {"id": user.get("id"), "admin": admin}).items() if "remove" in p]


def _import_run(db, user, admin, run):
    """A run this person may see (theirs, one in a namespace where they may delete, or any for admins)."""
    try:
        r = import_runs.get(db, int(run))
    except (KeyError, TypeError, ValueError):
        raise ValueError("there's no import run with that id: import_runs lists them") from None
    if not (admin or r.get("created_by") == user.get("email") or r.get("space") in _owned(db, user, admin)):
        raise ValueError("there's no import run with that id: import_runs lists them")
    return r


def _space(db, ns):
    try:
        return store.ns_id(db, (ns or "").strip().lower(), create=False)
    except KeyError:
        raise ValueError(f"there's no namespace {ns}") from None


def _people(db, cfg, tool, args, user):
    """Invite someone or change their access (approved)."""
    from app import email
    from . import passkeys

    out = {}
    if tool == "invite_person":
        sid = _space(db, args["namespace"]) if args.get("namespace") else None
        uid = auth.create_account(db, args["email"], None, args.get("name"))
        auth.audit(db, user, "user.create", f"account:{uid}", ["assistant"])
        out = {"invited": args["email"].strip().lower()}
        if sid is not None and args.get("role"):
            auth.set_role(db, uid, sid, args["role"])
            auth.audit(db, user, "member.set", f"space:{sid}", {"account": uid, "role": args["role"], "assistant": True})
        if email.mail_enabled(cfg):
            raw = passkeys.create_link(db, uid, by=user["email"])
            link = passkeys.link_url(raw, email.app_url(cfg))
            email.send_soon(
                email.send_invite_email, cfg, out["invited"], args.get("name"), user.get("name") or user["email"], link, passkeys.LINK_HOURS
            )
            auth.audit(db, user, "user.signin_link", f"account:{uid}", ["assistant", "emailed"])
            out["emailed"] = True
        else:
            out["emailed"] = False
            out["note"] = "Email isn't set up: send them a sign-in link from Admin → People."
        return out
    u = auth.find_account(db, args["email"])
    if not u or u.get("removed_at"):
        raise ValueError(f"there's no account {args['email']}")
    if u["id"] == user["id"] and args.get("disabled"):
        raise ValueError("you can't disable yourself")
    if args.get("namespace") and args.get("role"):
        sid = _space(db, args["namespace"])
        role = None if args["role"] == "none" else args["role"]
        end = auth.until(args.get("until")) if "until" in args else auth.KEEP
        auth.set_role(db, u["id"], sid, role, end)
        detail = {"account": u["id"], "role": role, "assistant": True} | ({} if end is auth.KEEP else {"expires_at": end})
        auth.audit(db, user, "member.set", f"space:{sid}", detail)
        out["role"] = {args["namespace"]: role}
        if role and end is not auth.KEEP:
            out["until"] = end
    if args.get("disabled") is not None:
        auth.update_account(db, u["id"], disabled=bool(args["disabled"]))
        auth.audit(db, user, "user.update", f"account:{u['id']}", {"disabled": bool(args["disabled"]), "assistant": True})
        out["disabled"] = bool(args["disabled"])
    return out


def describe_namespace(db, sid, name):
    """A line about a namespace for choosing between them: its description, and what's in it lately."""
    meta = metadata.namespace(db, sid)["meta"]
    about = str(meta.get("description") or meta.get("title") or "").strip()
    rows = db.rows("SELECT id, title FROM recording WHERE space = $s ORDER BY id DESC LIMIT 8", s=sid)
    titles = [r["title"] for r in rows if r.get("title")]
    parts = [about] if about else []
    if titles:
        parts.append("Holds assets such as: " + "; ".join(str(t)[:80] for t in titles))
    return " ".join(parts) or f"The namespace called {name}; nothing in it yet."


class OpsTools:
    """Mixed into the chat Toolbox: needs db, cfg, base, user, editable, admin, act (and act_on), wary, why and
    _approval."""

    def _change(self, tool, args, summary):
        if (
            (self.act or self._acts_on(tool, args) or tool in WITHIN_BOUNDS)
            and tool not in ASK_TOOLS
            and not (tool == "change_settings" and args.get("section") in ALWAYS_ASK)
        ):
            if self.wary():  # it read text from outside Lens, which may be what asked for this
                return self._approval(tool, args, summary + self.why())
            out = apply(self.db, self.cfg, self.base, tool, args, self.user, self.editable, self.admin)
            return {"status": "done", **out}, f"{summary}: done"
        return self._approval(tool, args, summary)

    def _acts_on(self, tool, args):
        """Whether a change is one the conversation makes at once (act_on); importing only into a namespace there is."""
        if tool not in getattr(self, "act_on", ()):
            return False
        if tool == "import_files":
            try:
                store.ns_id(self.db, args["namespace"], create=False)
            except KeyError:
                return False  # a new namespace waits for approval, as create_namespace does
        return True

    def t_scaling(self):
        v = scaling.overview(self.db, self.cfg)
        out = {
            "enabled": v["enabled"],
            "settings": v["settings"],
            "providers": [{k: p.get(k) for k in ("id", "name", "driver", "kind", "priced", "price", "check")} for p in v["providers"]],
            "groups": v["groups"],
            "units": v["units"],
            "metrics": v["metrics"],
            "drivers": [{k: d[k] for k in ("id", "kind", "label", "priced", "ready")} for d in v["drivers"]],
        }
        if not v["enabled"]:
            out["note"] = "scaling is off: an admin turns it on in Settings › Auto scaling (change_settings scaling {enabled: true})"
        running = sum(u["state"] == "running" for u in v["units"])
        return out, f"Read auto scaling: {len(v['groups'])} groups, {running} units running"

    def _scaling_group(self, gid):
        try:
            return scaling.get_group(self.db, int(gid))
        except (KeyError, TypeError, ValueError):
            raise ValueError("there's no scaling group with that id: scaling lists them") from None

    def _terms_said(self, row, budget):
        p = scaling._provider(self.db, row["provider"])
        cost = f", up to ${budget:g} a month" if budget is not None else ""
        return f"on {p['name']}: {row['min']} to {row['max']} {row['kind']} units{cost}"

    def t_propose_scaling_group(self, group):
        row, budget, _p, _d = scaling._check_group_body(self.db, group)  # said now, not after it waits for approval
        summary = f"Make the scaling group {row['name']} {self._terms_said(row, budget)}"
        return self._change("propose_scaling_group", {"group": group}, summary)

    def t_change_scaling_group(self, group_id, changes):
        g = self._scaling_group(group_id)
        cur = {**g, "budget": (g.get("budget") or {}).get("usd")}
        row, budget, _p, _d = scaling._check_group_body(self.db, changes, cur)
        what = ", ".join(sorted(changes)) or "nothing"
        summary = f"Change the scaling group {g['name']} ({what}): {self._terms_said(row, budget)}"
        return self._change("change_scaling_group", {"group_id": g["id"], "changes": changes}, summary)

    def t_set_scaling_desired(self, group_id, desired):
        g = self._scaling_group(group_id)
        n = int(desired)
        if not g["min"] <= n <= g["max"]:
            raise ValueError(f"desired is from {g['min']} to {g['max']} for {g['name']}")
        return self._change("set_scaling_desired", {"group_id": g["id"], "desired": n}, f"Ask {g['name']} for {n} units")

    def t_pause_scaling_group(self, group_id, paused=True, why=None):
        g = self._scaling_group(group_id)
        args = store.clean({"group_id": g["id"], "paused": bool(paused), "why": why})
        return self._change("pause_scaling_group", args, f"{'Pause' if paused else 'Resume'} the scaling group {g['name']}")

    def t_terminate_scaling_unit(self, unit_id, why=None):
        try:
            n = scaling._node(self.db, int(unit_id))
        except (KeyError, TypeError, ValueError):
            raise ValueError("there's no scaling unit with that id: scaling lists them") from None
        args = store.clean({"unit_id": n["id"], "why": why})
        return self._change("terminate_scaling_unit", args, f"Let the unit {n['name']} go")

    def t_let_go_scaling_stray(self, provider_id, stray_id):
        try:
            p = scaling._provider(self.db, int(provider_id))
        except (KeyError, TypeError, ValueError):
            raise ValueError("there's no scaling provider with that id: scaling lists them") from None
        s = next((x for x in p.get("strays") or [] if x.get("id") == str(stray_id)), None)
        if s is None:
            raise ValueError(f"{p['name']} has no stray with that id: scaling lists each provider's strays")
        args = {"provider_id": p["id"], "stray_id": s["id"]}
        return self._change("let_go_scaling_stray", args, f"Let {s.get('name') or s['id']} go at {p['name']}")

    def t_mesh(self):
        v = mesh.agent_view(self.db, self.cfg)
        if not v["enabled"]:
            v["note"] = "the mesh is off: add_mesh_node turns it on with the first node"
        on = sum(n["online"] and n["role"] != "primary" and not n["removed"] for n in v["nodes"])
        return v, f"Read the mesh: {on} machine{'' if on == 1 else 's'} online besides this one"

    def _mesh_node(self, node_id):
        try:
            n = mesh.node(self.db, int(node_id))
        except (KeyError, TypeError, ValueError):
            raise ValueError("there's no machine on the mesh with that id: mesh lists them") from None
        return n

    def t_add_mesh_node(self, role="worker", name=None, namespaces=None, hours=24, uses=1):
        from app.email import app_url

        role, hours, uses = role or "worker", 24 if hours is None else hours, 1 if uses is None else uses
        name, namespaces = mesh.check_token(self.db, role, name, namespaces, hours, uses)  # said now, not once approved
        what = f" named {name}" if name else ""
        serves = f", serving {', '.join(namespaces)}" if namespaces else ""
        times = "once" if uses == 1 else f"{uses} times"
        on = "" if mesh.conf(self.cfg)["enabled"] else ", and turn the mesh on"
        summary = f"Let a new {role} node{what} join the mesh{serves}: a join token that works {times}, for {hours} hours{on}"
        url = (getattr(self, "web_app", None) or app_url(self.cfg, self.db)).rstrip("/")  # where the machine joins
        args = store.clean({"role": role, "name": name, "namespaces": namespaces or None, "hours": hours, "uses": uses, "url": url})
        return self._change("add_mesh_node", args, summary)

    def t_change_mesh_node(self, node_id, name=None, namespaces=None):
        n = self._mesh_node(node_id)
        if name is None and namespaces is None:
            raise ValueError("say what to change: name or namespaces")
        parts = [f"rename it {name.strip()}"] if name is not None else []
        if namespaces is not None:
            parts.append(f"serve {', '.join(sorted(set(namespaces)))}" if namespaces else "serve every namespace")
        args = store.clean({"node_id": n["id"], "name": name, "namespaces": None if namespaces is None else list(namespaces)})
        return self._change("change_mesh_node", args, f"{n['name']} on the mesh: {', '.join(parts)}")

    def t_remove_mesh_node(self, node_id):
        n = self._mesh_node(node_id)
        if n["role"] == "primary":
            raise ValueError("that's this machine, the mesh's primary: the mesh is turned off in Settings › Network")
        if n["revoked_at"]:
            raise ValueError(f"{n['name']} is already off the mesh")
        return self._change("remove_mesh_node", {"node_id": n["id"]}, f"Take {n['name']} ({n['role']}, {n['ip']}) off the mesh")

    def t_restore_mesh_node(self, node_id):
        n = self._mesh_node(node_id)
        return self._change("restore_mesh_node", {"node_id": n["id"]}, f"Bring {n['name']} back onto the mesh")

    def t_withdraw_mesh_token(self, token_id):
        t = next((t for t in mesh.tokens(self.db) if str(t["id"]) == str(token_id)), None)
        if not t:
            raise ValueError("there's no join token not used up with that id: mesh lists them")
        return self._change("withdraw_mesh_token", {"token_id": t["id"]}, f"Withdraw the {t['role']} join token #{t['id']}")

    def t_server_status(self):
        return status(self.db, self.cfg, self.base), "Checked what's set up"

    def t_find_model_servers(self):
        found = setup.detect_llm()
        return {"servers": found, "cloud": list(setup.CLOUD_PROVIDERS)}, f"Looked for model servers nearby: {len(found)} found"

    def t_read_settings(self, section):
        if section not in SECTIONS:
            raise ValueError(f"{section} isn't one of {', '.join(SECTIONS)}")
        sec = settings.view(self.db, self.base)[section]
        return {"values": sec["values"], "locked_by_env": sec["locked"]}, f"Read the {section} settings"

    def t_change_settings(self, section, changes):
        if section not in SECTIONS:
            raise ValueError(f"{section} isn't one of {', '.join(SECTIONS)}")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes is an object of the settings to change")
        shown = {
            k: (v if secret_providers.is_reference(v) else "(a secret)") if k in settings.SECRETS.get(section, ()) else v
            for k, v in changes.items()
        }
        what = ", ".join(f"{k} = {v}" for k, v in shown.items())
        return self._change("change_settings", {"section": section, "changes": changes}, f"Set {section}: {what}")

    def t_list_secrets(self):
        if not self.admin:
            raise PermissionError("only admins see the secrets settings")
        return settings.secrets_view(self.db, self.base), "Listed the secret settings and where each comes from"

    def t_vault_items(self, q=None, provider="vaultwarden"):
        if not self.admin:
            raise PermissionError("only admins see the password manager's items")
        p = secret_providers.PROVIDERS.get(provider)
        if p is None or not p.scheme:
            raise ValueError("provider is vaultwarden")
        if not p.configured(self.cfg):
            raise ValueError(f"{p.label} isn't set up yet: Settings → Secrets, or change_settings secrets")
        items = p.items(self.cfg, q)
        return {"provider": p.id, "items": items}, f"Listed {len(items)} item(s) Lens may refer to in {p.label}"

    def t_file_storage(self, check=False):
        f = self.cfg.get("files") or {}
        out = {
            "store": f.get("store", "local"),
            "connection": f.get("connection"),
            "folder": f.get("folder"),
            "crypt": bool(f.get("crypt")),
            "connections": [
                {"id": s["id"], "name": s["name"], "type": s["type"]} for s in sources.list_sources(self.db) if not feeds.handles(s)
            ],
        }
        if check:
            out["check"] = blobs.test(self.db, self.cfg)
        return out, "Checked where files are kept" + (": " + ("it works" if out["check"]["ok"] else "it failed") if check else "")

    def t_news_feeds(self):
        found = rss.subscriptions(self.db)
        return {"feeds": found}, f"Listed {len(found)} news feeds"

    def t_subscribe_feed(self, url, namespace, name=None, backfill=False):
        try:  # checked here, so a wrong address is said before anything waits for approval
            shown = rss.address(url)
        except RuntimeError as e:
            raise ValueError(str(e)) from None
        ns = str(namespace or "").strip().lower()
        if not store.NS_RX.match(ns):
            raise ValueError("a namespace's name uses lowercase letters, digits, - and _")
        args = store.clean({"url": shown, "namespace": ns, "name": name, "backfill": bool(backfill) or None})
        return self._change(
            "subscribe_feed", args, f"Follow the news feed {shown} into {ns}" + (" with its current posts" if backfill else "")
        )

    def t_unsubscribe_feed(self, feed):
        try:
            src = sources.get(self.db, int(feed))
        except (KeyError, TypeError, ValueError):
            raise ValueError("there's no news feed with that id: news_feeds lists them") from None
        if src["type"] != "rss":
            raise ValueError("that source isn't a news feed")
        return self._change("unsubscribe_feed", {"feed": src["id"]}, f"Stop following the news feed {src['name']}")

    def t_create_namespace(self, name, graph="shared"):
        name = name.strip().lower()
        if not store.NS_RX.match(name):
            raise ValueError("namespace names use lowercase letters, digits, - and _")
        if name in store.space_names(self.db).values():
            raise ValueError(f"there's already a namespace called {name}")
        return self._change("create_namespace", {"name": name, "graph": graph}, f"Create the namespace {name} ({graph} graph)")

    def t_list_people(self):
        found = people(self.db)
        return {"people": found}, f"Listed {len(found)} people"

    def t_invite_person(self, email, name=None, namespace=None, role=None):
        email = (email or "").strip().lower()
        if not auth.EMAIL.match(email):
            raise ValueError("that isn't an email address")
        if auth.find_account(self.db, email):
            raise ValueError(f"{email} already has an account: change_person gives them a role")
        if namespace:
            _space(self.db, namespace)
        if role and not roles.exists(self.db, role):
            raise ValueError(f"there's no role called {role}")
        where = f" as {role or 'viewer'} of {namespace}" if namespace else ""
        args = store.clean({"email": email, "name": name, "namespace": namespace, "role": (role or "viewer") if namespace else None})
        return self._change("invite_person", args, f"Invite {email}{where}")

    def t_change_person(self, email, namespace=None, role=None, disabled=None, until=None):
        u = auth.find_account(self.db, email)
        if not u:
            raise ValueError(f"there's no account {email}")
        if (namespace is None) != (role is None) and disabled is None:
            raise ValueError("give a namespace and a role, or disabled")
        if namespace:
            _space(self.db, namespace)
        if role and role != "none" and not roles.exists(self.db, role):
            raise ValueError(f"there's no role called {role}")
        end = auth.until(until) if until is not None else None  # a date that has passed fails now, not on approval
        parts = [f"{'no role' if role == 'none' else role} in {namespace}"] if namespace and role else []
        if parts and until is not None and role != "none":
            parts[0] += f" until {end[:10]}" if end else ", without an end"
        if disabled is not None:
            parts.append("disable the account" if disabled else "enable the account")
        args = store.clean({"email": u["email"], "namespace": namespace, "role": role, "disabled": disabled, "until": until})
        return self._change("change_person", args, f"{u['email']}: {', '.join(parts)}")

    def _where_to(self, files):
        """The namespace these files go in, chosen for the person, or (None, why) when it's for them to say."""
        names = store.space_names(self.db)
        mine = {n: s for s, n in names.items() if self.admin or s in self.editable}
        if not mine:
            raise ValueError("there's no namespace to put these in yet" + (": create_namespace first" if self.admin else ""))
        options = {n: describe_namespace(self.db, s, n) for n, s in sorted(mine.items())}
        try:
            d = decide.choose(
                self.cfg,
                "Which namespace should these files go in? Namespaces keep separate parts of someone's life or work apart.",
                options,
                {"files": files, "their_message": (getattr(self, "said", "") or "")[:4000]},
            )
        except decide.Undecided:
            return None, {"options": list(options)}
        if decide.sure(self.cfg, d):
            return d["choice"], d
        return None, d

    def t_import_files(self, upload_ids, namespace=None, collection_id=None, content_type=None):
        if isinstance(upload_ids, str):
            upload_ids = [upload_ids]
        content_type = (content_type or "").strip() or None
        names = []
        for uid in upload_ids:
            try:
                up = uploads.get(self.db, str(uid))
            except KeyError:
                raise ValueError(f"no attachment {uid}") from None
            if up.get("account") != self.user["id"] or up.get("state") != "held":
                raise ValueError(f"{uid} isn't a file attached here that's waiting to be imported")
            names.append(up["filename"])
            if content_type:  # before anything is asked or placed: one that doesn't fit any of them stops all
                content_types.check(self.db, content_type, content_types.base_of_file(up["filename"]))
        chosen = None
        if not (namespace or "").strip():
            files = [{"file": n, "title": (uploads.get(self.db, str(u)).get("title") or "")} for n, u in zip(names, upload_ids)]
            namespace, chosen = self._where_to(files)
            if namespace is None:
                ranked = chosen.get("ranked") or [{"option": o} for o in chosen["options"]]
                return (
                    {"status": "ask", "note": "It isn't clear where these go: ask the person, suggesting the first.", "namespaces": ranked},
                    f"Not sure where to put {', '.join(names)}",
                )
        namespace = namespace.strip().lower()
        args = {
            "upload_ids": [str(u) for u in upload_ids],
            "namespace": namespace,
            "collection_id": collection_id,
            "content_type": content_type,
        }
        summary = f"Import {', '.join(names)} into {namespace}" + (
            f" as {content_types.get(self.db, content_type)['label']}" if content_type else ""
        )
        if chosen and chosen.get("by") != "only option":
            summary += f" (chosen, {round(100 * chosen['confidence'])}% sure)"
        out, said = self._change("import_files", store.clean(args), summary)
        if chosen:
            out = {**out, "namespace_chosen": {"by": chosen["by"], "confidence": chosen["confidence"]}}
        return out, said

    def t_import_runs(self, limit=20):
        mine = _owned(self.db, self.user, self.admin)
        runs = import_runs.listing(self.db, None if self.admin else self.user.get("email"), mine, min(max(int(limit or 20), 1), 100))
        keep = ("id", "kind", "namespace", "source", "created_by", "created_at", "assets", "left", "status")
        return {"runs": [{k: r.get(k) for k in keep} for r in runs]}, f"Listed {len(runs)} import run(s)"

    def t_undo_import(self, run):
        r = _import_run(self.db, self.user, self.admin, run)
        if not r["left"]:
            return {"run": r["id"], "status": r["status"], "left": 0}, f"Import run {r['id']} has nothing left to undo"
        if import_runs.refused(self.db, r["id"], import_runs.may_remove(self.db, {"id": self.user.get("id"), "admin": self.admin})):
            raise PermissionError("undoing it needs an owner: some of its assets aren't yours to delete")
        what = f" ({r['source']})" if r.get("source") else ""
        return self._change("undo_import", {"run": r["id"]}, f"Undo import run {r['id']}{what}: delete its {r['left']} asset(s)")

    def t_import_urls(self, urls=None, text=None, upload_id=None, namespace=None, collection_id=None, content_type=None):
        if isinstance(urls, str):
            urls = [urls]
        content_type = (content_type or "").strip() or None
        if content_type:
            content_types.check(self.db, content_type, "text")
        given = [bool(urls), bool((text or "").strip()), bool(upload_id)]
        if sum(given) != 1:
            raise ValueError("give the addresses as urls, as text, or as an attached file's upload_id: one of them")
        if not convert.web_capture(self.cfg):
            raise ValueError("capturing web pages needs Chromium on the server (the lens:full image)")
        if upload_id:
            try:
                up = uploads.get(self.db, str(upload_id))
            except KeyError:
                raise ValueError(f"no attachment {upload_id}") from None
            if up.get("account") != self.user["id"] or up.get("state") != "held":
                raise ValueError(f"{upload_id} isn't a file attached here")
            raw = uploads.held_bytes(self.cfg, up, LIST_BYTES)
        else:
            raw = "\n".join(str(u) for u in urls) if urls else text
        found = links.read(raw)
        if not found:
            raise ValueError("there are no http:// or https:// links in it")
        if len(found) > links.MAX:
            raise ValueError(f"import {links.MAX} links at most at once; this has {len(found)}")
        shown = found[0]["url"] if len(found) == 1 else f"{len(found)} web pages"
        chosen = None
        if not (namespace or "").strip():
            pages = [{"url": f["url"], "title": f["title"] or ""} for f in found[:20]]
            namespace, chosen = self._where_to(pages)
            if namespace is None:
                ranked = chosen.get("ranked") or [{"option": o} for o in chosen["options"]]
                return (
                    {"status": "ask", "note": "It isn't clear where these go: ask the person, suggesting the first.", "namespaces": ranked},
                    f"Not sure where to put {shown}",
                )
        namespace = namespace.strip().lower()
        args = {"links": found, "namespace": namespace, "collection_id": collection_id, "content_type": content_type}
        summary = f"Capture {shown} into {namespace}" + (f" as {content_types.get(self.db, content_type)['label']}" if content_type else "")
        if chosen and chosen.get("by") != "only option":
            summary += f" (chosen, {round(100 * chosen['confidence'])}% sure)"
        out, said = self._change("import_urls", store.clean(args), summary)
        if chosen:
            out = {**out, "namespace_chosen": {"by": chosen["by"], "confidence": chosen["confidence"]}}
        return out, said
