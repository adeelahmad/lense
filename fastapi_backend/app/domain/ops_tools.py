"""The assistant's tools for running the server: what's set up and what isn't, the settings, namespaces, and files
dropped into the conversation.

Admins get the server tools; anyone gets `import_files` for their own attachments, into namespaces they edit. A change
waits for the person's approval, unless the conversation lets the assistant act (a setup conversation does: the admin
asked it to set things up): then it's made at once, audited, and said in the answer. Telemetry, which sends data
elsewhere, always waits for approval.
"""

from __future__ import annotations

from . import auth, decide, jobs, llm, metadata, settings, setup, store, uploads

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
    "uploads",
    "ai",
    "reports",
    "notifications",
    "workers",
    "decisions",
    "telemetry",
    "sensors",
)
ALWAYS_ASK = {"telemetry", "sensors"}  # sensors open ports on the network

ADMIN_TOOLS = [
    (
        "server_status",
        "What this Lens server has set up and what it still needs: the model provider, search by meaning, namespaces, "
        "sources, recordings, the job queue. Start here when setting up or troubleshooting.",
        {},
        [],
        False,
    ),
    (
        "find_model_servers",
        "Model servers running on this machine or the Docker host (Ollama, LM Studio, llama.cpp, vLLM, LocalAI), with their models.",
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
        "create_namespace",
        "Create a namespace: a separate set of recordings, documents and images with its own people and access. "
        "Name: lowercase letters, digits, - and _. graph: shared (people and places joined across namespaces) or isolated.",
        {"name": _S, "graph": {"type": "string", "enum": ["shared", "isolated"]}},
        ["name"],
        True,
    ),
]
FILE_TOOLS = [
    (
        "import_files",
        "Put files the person attached to the conversation into a namespace, where they're transcribed or read like any "
        "upload. Use the attachment ids from their message. Leave namespace out unless the person named one: it's "
        "chosen for them, and you're told when it's unclear and to ask.",
        {"upload_ids": {"type": "array", "items": _S}, "namespace": _S, "collection_id": {"type": "integer"}},
        ["upload_ids"],
        True,
    ),
]


def status(db, cfg, base):
    """The server at a glance, with what's missing first."""
    view = setup.view(db, base)
    names = store.space_names(db)
    q = jobs.counts(db)
    missing = []
    if not llm.configured(cfg):
        missing.append("a model provider (chat, summaries and this assistant need one): find_model_servers, then change_settings llm")
    if not names:
        missing.append("a namespace to put recordings in: create_namespace")
    emb = cfg.get("embeddings") or {}
    if not (emb.get("model") and (emb.get("base_url") or cfg["llm"].get("base_url"))):
        missing.append("search by meaning: an embedding model (embeddings.model, e.g. nomic-embed-text on Ollama)")
    recordings = db.values("SELECT VALUE count() FROM recording GROUP ALL")
    return {
        "missing": missing,
        "setup_wizard_pending": view["pending"],
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
    }


def apply(db, cfg, base, tool, args, user, editable, admin):
    """Carry out a change (approved, or in a conversation that lets the assistant act). Returns the result."""
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
            up = uploads.place(db, cfg, uid, ns, user["id"], admin, args.get("collection_id"))
            auth.audit(db, user, "upload", f"recording:{up['recording']}", {"file": up["filename"], "namespace": ns, "assistant": True})
            placed.append({"file": up["filename"], "recording": up["recording"], "duplicate": bool(up.get("duplicate"))})
        return {"namespace": ns, "imported": placed}
    raise ValueError(f"no tool {tool}")


def describe_namespace(db, sid, name):
    """A line about a namespace for choosing between them: its description, and what's in it lately."""
    meta = metadata.namespace(db, sid)["meta"]
    about = str(meta.get("description") or meta.get("title") or "").strip()
    rows = db.rows("SELECT id, title FROM recording WHERE space = $s ORDER BY id DESC LIMIT 8", s=sid)
    titles = [r["title"] for r in rows if r.get("title")]
    parts = [about] if about else []
    if titles:
        parts.append("Holds recordings such as: " + "; ".join(str(t)[:80] for t in titles))
    return " ".join(parts) or f"The namespace called {name}; nothing in it yet."


class OpsTools:
    """Mixed into the chat Toolbox: needs db, cfg, base, user, editable, admin, act and _approval."""

    def _change(self, tool, args, summary):
        if self.act and not (tool == "change_settings" and args.get("section") in ALWAYS_ASK):
            out = apply(self.db, self.cfg, self.base, tool, args, self.user, self.editable, self.admin)
            return {"status": "done", **out}, f"{summary}: done"
        return self._approval(tool, args, summary)

    def t_server_status(self):
        return status(self.db, self.cfg, self.base), "Checked what's set up"

    def t_find_model_servers(self):
        found = setup.detect_llm()
        return {"servers": found}, f"Looked for model servers nearby: {len(found)} found"

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
        shown = {k: ("(a secret)" if k in settings.SECRETS.get(section, ()) else v) for k, v in changes.items()}
        what = ", ".join(f"{k} = {v}" for k, v in shown.items())
        return self._change("change_settings", {"section": section, "changes": changes}, f"Set {section}: {what}")

    def t_create_namespace(self, name, graph="shared"):
        name = name.strip().lower()
        if not store.NS_RX.match(name):
            raise ValueError("namespace names use lowercase letters, digits, - and _")
        if name in store.space_names(self.db).values():
            raise ValueError(f"there's already a namespace called {name}")
        return self._change("create_namespace", {"name": name, "graph": graph}, f"Create the namespace {name} ({graph} graph)")

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

    def t_import_files(self, upload_ids, namespace=None, collection_id=None):
        if isinstance(upload_ids, str):
            upload_ids = [upload_ids]
        names = []
        for uid in upload_ids:
            try:
                up = uploads.get(self.db, str(uid))
            except KeyError:
                raise ValueError(f"no attachment {uid}") from None
            if up.get("account") != self.user["id"] or up.get("state") != "held":
                raise ValueError(f"{uid} isn't a file attached here that's waiting to be imported")
            names.append(up["filename"])
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
        args = {"upload_ids": [str(u) for u in upload_ids], "namespace": namespace, "collection_id": collection_id}
        summary = f"Import {', '.join(names)} into {namespace}"
        if chosen and chosen.get("by") != "only option":
            summary += f" (chosen, {round(100 * chosen['confidence'])}% sure)"
        out, said = self._change("import_files", store.clean(args), summary)
        if chosen:
            out = {**out, "namespace_chosen": {"by": chosen["by"], "confidence": chosen["confidence"]}}
        return out, said
