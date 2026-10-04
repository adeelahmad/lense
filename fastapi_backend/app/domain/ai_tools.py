"""Tools the chat assistant can call, limited to what the asker may read (and to the conversation's scope).

Read tools answer at once and number every moment they return, so the assistant can cite them as [n]. Tools that
run work or change data don't act: they create an approval the person accepts or declines in the conversation.
"""

from __future__ import annotations

import json
import logging

from . import (
    batches,
    cypher,
    entities,
    extensions,
    entity_map,
    graph_ask,
    graph_model,
    entity_setup,
    graph_history,
    notebook,
    ops_tools,
    recsets,
    render,
    search as searchmod,
    speakers as spk,
    store,
    templates,
    topics,
)

R = store.R
log = logging.getLogger("lens")
_I = {"type": "integer"}
_S = {"type": "string"}
TOOLS = [
    (
        "search_transcripts",
        "Search what was said (and text shown on screen in videos) across the recordings in scope.",
        {"query": _S, "namespace": _S, "limit": _I},
        ["query"],
        False,
    ),
    (
        "list_recordings",
        "List recordings in scope, newest first, optionally by namespace, dates (YYYY-MM-DD), speaker, entity or media.",
        {"namespace": _S, "from": _S, "to": _S, "speaker_id": _I, "entity_id": _I, "media": _S, "limit": _I},
        [],
        False,
    ),
    (
        "read_transcript",
        "Read part of one recording's transcript, by seconds.",
        {"recording_id": _I, "start_seconds": {"type": "number"}, "end_seconds": {"type": "number"}, "max_lines": _I},
        ["recording_id"],
        False,
    ),
    (
        "recording_outputs",
        "A recording's summary and saved outputs (such as meeting notes).",
        {"recording_id": _I},
        ["recording_id"],
        False,
    ),
    (
        "find_entities",
        "Find people, organisations, products, places and terms mentioned in scope (for subjects, use find_topics).",
        {"query": _S, "type": _S, "namespace": _S, "limit": _I},
        [],
        False,
    ),
    (
        "entity_mentions",
        "Where an entity was mentioned: recording, time, speaker and the line.",
        {"entity_id": _I, "limit": _I},
        ["entity_id"],
        False,
    ),
    ("entity_timeline", "How often an entity was mentioned per month.", {"entity_id": _I}, ["entity_id"], False),
    (
        "find_topics",
        "Find topics: each namespace's controlled vocabulary of what recordings are about, with other labels, a "
        "definition, broader topics and how many recordings are about each.",
        {"query": _S, "namespace": _S, "limit": _I},
        [],
        False,
    ),
    (
        "topic_recordings",
        "One topic: its broader, narrower and related topics and the recordings about it (accepted, and suggested).",
        {"topic_id": _I},
        ["topic_id"],
        False,
    ),
    (
        "suggest_topic",
        "Suggest that recordings are about a topic. It waits on the recording and the topic for someone to accept; "
        "nothing they accepted or dismissed changes.",
        {"topic_id": _I, "recording_ids": {"type": "array", "items": _I}},
        ["topic_id", "recording_ids"],
        True,
    ),
    (
        "graph_neighbours",
        "Who and what is connected to an entity or speaker in the knowledge graph.",
        {"entity_id": _I, "speaker_id": _I, "limit": _I},
        [],
        False,
    ),
    ("speaker_stats", "Speakers in a namespace with talk time and recordings.", {"namespace": _S}, ["namespace"], False),
    (
        "find_notes",
        "Find notes (free notes and the pages of recordings, entities, collections and speakers) in scope by words in "
        "their title, summary or text. Each has an id, title, one-line summary, where it's filed (PARA) and what it's "
        "the page of.",
        {"query": _S, "namespace": _S, "place": _S, "limit": _I},
        [],
        False,
    ),
    (
        "read_note",
        "Read a note's Markdown, with its links and the notes linking to it: by note_id, or the page of a thing "
        '(about, like "recording:12" or "entity:5").',
        {"note_id": _I, "about": _S},
        [],
        False,
    ),
    (
        "write_note",
        'Write a new note, or the page of a thing (about, like "entity:5"; one each). You keep notes as you learn: '
        "a specific title, a one-line summary of what it holds, Markdown text, and where it's filed: project (an "
        "outcome with an end), area (a responsibility kept up), resource (a topic of interest) or archive (done). Link "
        "with @[label](recording:12), @[label](entity:5), @[label](page:3), and topics with #[label](topic:9) (ids "
        "from find_entities, find_topics, find_notes and list_recordings). Put it inside another note with parent_id.",
        {
            "namespace": _S,
            "title": _S,
            "body": _S,
            "summary": _S,
            "place": {"type": "string", "enum": list(notebook.PLACES)},
            "parent_id": _I,
            "about": _S,
        },
        ["namespace", "title", "body"],
        True,
    ),
    (
        "update_note",
        "Change a note: its title, summary, place, or its text (body replaces it; append adds to the end). Move a free "
        "note in the tree with parent_id (0: the top).",
        {
            "note_id": _I,
            "title": _S,
            "summary": _S,
            "place": {"type": "string", "enum": list(notebook.PLACES)},
            "body": _S,
            "append": _S,
            "parent_id": _I,
        },
        ["note_id"],
        True,
    ),
    (
        "graph_schema",
        "What the graph holds (namespaces, collections, recordings, speakers, entities, topics and how they link), with example "
        "Cypher. Read it before graph_query.",
        {"namespace": _S},
        [],
        False,
    ),
    (
        "graph_query",
        "Ask the graph in read-only Cypher, e.g. MATCH (s:Speaker)-[x:SAID]->(e:Organisation) RETURN s.name, e.name, "
        "x.count ORDER BY x.count DESC LIMIT 10. Errors say what to fix. Without a namespace it covers the conversation's.",
        {"query": _S, "namespace": _S, "limit": _I},
        ["query"],
        False,
    ),
    (
        "graph_related",
        "A node's parents, children, ancestors, descendants (recording, collection, namespace...) or neighbours. Nodes "
        "are n<id> namespaces, c<id> collections, r<id> recordings, s<id> speakers, e<id> entities, t<id> topics.",
        {
            "node": _S,
            "relation": {"type": "string", "enum": ["parents", "children", "ancestors", "descendants", "neighbours"]},
            "depth": _I,
            "namespace": _S,
        },
        ["node", "relation"],
        False,
    ),
    (
        "graph_paths",
        "How two nodes connect: the paths between them, shortest first.",
        {"from_node": _S, "to_node": _S, "max_depth": _I, "namespace": _S},
        ["from_node", "to_node"],
        False,
    ),
    (
        "run_template",
        "Run a template (such as meeting notes) on recordings. Needs the person's approval.",
        {"template_id": _I, "recording_ids": {"type": "array", "items": _I}, "collection_id": _I},
        ["template_id"],
        True,
    ),
    (
        "entity_setup",
        "How a namespace organises its entities: its mode (self-organising, a fixed list, or hybrid: the list first, then new entities), the types it keeps, what it's "
        "about, its own entity types, and its defined entities.",
        {"namespace": _S},
        ["namespace"],
        False,
    ),
    (
        "propose_entity_change",
        "Propose a change to the entities, which needs the person's approval: merge others into an entity, rename or "
        "retype it, describe it (a description and the other ways it's said), hide it, or define a new entity on a "
        "namespace's list (define: namespace, new_name, new_type; no entity_id).",
        {
            "action": {"type": "string", "enum": ["merge", "rename", "retype", "describe", "hide", "define"]},
            "entity_id": _I,
            "merge_ids": {"type": "array", "items": _I},
            "new_name": _S,
            "new_type": _S,
            "description": _S,
            "also_said_as": {"type": "array", "items": _S},
            "namespace": _S,
        },
        ["action"],
        True,
    ),
]


# making the assistant's extensions from chat (or voice, which is chat): read what there is, then save a manifest or
# switch one on or off, which always waits for the person's yes (it changes what the assistant does)
AUTHOR_TOOLS = [
    (
        "list_extensions",
        "The tools, skills, hooks and plugins added to this assistant that the person can see: name, kind, whether "
        "it's on, and whether they can change it.",
        {"kind": {"type": "string", "enum": list(extensions.KINDS)}},
        [],
        False,
    ),
    (
        "read_extension",
        "One extension as its manifest (Markdown with YAML frontmatter, or YAML), to show or change it.",
        {"name": _S},
        ["name"],
        False,
    ),
    (
        "save_extension",
        "Add an extension to the assistant, or a new version of one the person can change, from a manifest. Needs the "
        "person's approval. Manifest: YAML frontmatter between --- lines with name (lowercase_with_underscores), kind "
        "(tool, skill, hook or plugin) and description, then for a skill `when` (when to use it) with its instructions "
        "after the frontmatter; for a prompt tool `params` ([{name, kind: text|number|integer|bool|json|list, "
        "required, options, description}]) and `effect` (read, or change: asks first) with the prompt after the "
        "frontmatter, using {{param}}; a web tool sets run: {type: http, method, url, headers, body} instead; a hook sets "
        "event (message, before_tool, after_tool, answer), match {tool, contains} and action {type: context|block|tool, "
        "text|reason|tool, args}. Errors say what to fix.",
        {"manifest": _S, "notes": _S},
        ["manifest"],
        True,
    ),
    (
        "switch_extension",
        "Switch an extension the person can change on or off. Needs the person's approval.",
        {"name": _S, "enabled": {"type": "boolean"}},
        ["name", "enabled"],
        True,
    ),
]


def builtin_names():
    """The assistant's own tool names, which extensions can't take."""
    return {t[0] for t in TOOLS + ops_tools.ADMIN_TOOLS + ops_tools.FILE_TOOLS + AUTHOR_TOOLS} | {"use_skill"}


class Toolbox(ops_tools.OpsTools):
    """`admin` adds the server tools (ops_tools.py), with `base` (archive.yaml's config) to save settings over; `act`
    makes their changes at once instead of asking for approval."""

    def __init__(self, db, cfg, user, readable, editable, scope, chat_id, base=None, admin=False, act=False, said=""):
        self.db, self.cfg, self.user, self.chat, self.said = db, cfg, user, chat_id, said
        self.base, self.admin, self.act = base or cfg, admin, act
        self.scope = scope or {}
        self.readable, self.editable = set(readable), set(editable)
        names = store.space_names(db)
        if self.scope.get("namespaces"):
            self.readable = {s for s in self.readable if names.get(s) in self.scope["namespaces"]}
        self.allowed = recsets.within(db, self.readable, self.scope.get("recordings"), self.scope.get("collections"))
        self.refs, self.reads, self.approvals = [], 0, []
        # the extensions this person switched on or was given (extensions.py); hooks don't run inside hooks
        roles = {s: "editor" if s in self.editable else "viewer" for s in self.readable}
        self.me = extensions.who(user["id"], user.get("email"), admin, roles)
        self.ext = extensions.Active(db, self.me) if cfg["ai"].get("extensions", True) else None
        self.hooking = False

    def system_note(self):
        """What the model is told besides its usual instructions: the skills it can follow, and context that hooks add
        for this question."""
        if not self.ext:
            return ""
        out = self.ext.system_note()
        for h in self.ext.matching("message", text=self.said):
            out += self._hook(h, {"said": self.said})
        return out

    def after_answer(self, text):
        """Run the hooks for an answer that was written."""
        for h in self.ext.matching("answer", text=text) if self.ext else ():
            self._hook(h, {"said": self.said, "answer": text})

    def _hook(self, h, values):
        """Carry out one hook: context it adds (returned), a tool it calls (run like any other, approvals and all)."""
        a = h["spec"]["action"]
        if a["type"] == "context":
            return "\n\n" + extensions.fill(a["text"], values)
        if a["type"] == "tool" and not self.hooking:
            self.hooking = True
            try:
                self.call(a["tool"], extensions.fill_json(a["args"], values))
            finally:
                self.hooking = False
        return ""

    def specs(self):
        off = set(self.cfg["ai"].get("disabled_tools") or [])
        can_act = bool(self.editable)
        tools = [t for t in TOOLS if can_act or not t[4]]
        if self.admin:
            tools += ops_tools.ADMIN_TOOLS
        if can_act or self.admin:
            tools += ops_tools.FILE_TOOLS
        if self.cfg["ai"].get("extensions", True):
            tools += [t for t in AUTHOR_TOOLS if can_act or self.admin or not t[4]]
        if self.ext:
            tools += self.ext.tool_specs(can_act or self.admin)
        return [
            {
                "type": "function",
                "function": {"name": n, "description": d, "parameters": {"type": "object", "properties": p, "required": req}},
            }
            for n, d, p, req, _ in tools
            if n not in off
        ]

    def ref(self, rid, t0, text, speaker=None, title=None, source="said", page=None):
        self.refs.append(
            store.clean(
                {
                    "n": len(self.refs) + 1,
                    "recording_id": rid,
                    "t0": t0,
                    "time": store.tc(t0) if page is None else f"p. {page + 1}",
                    "page": page,
                    "speaker": speaker,
                    "title": title,
                    "text": text,
                    "source": source,
                }
            )
        )
        return len(self.refs)

    def _ok(self, rid):
        row = self.db.one("SELECT space, title FROM $r", r=R("recording", int(rid)))
        if not row or row["space"] not in self.readable or (self.allowed is not None and int(rid) not in self.allowed):
            raise ValueError(f"recording {rid} isn't available in this conversation")
        return row

    def call(self, name, args):
        offered = {s["function"]["name"] for s in self.specs()}
        try:
            if name not in offered:
                raise AttributeError(name)
            fn = getattr(self, "t_" + name, None) or self._ext_tool(name)
        except AttributeError:
            return json.dumps({"error": f"no tool {name}"}), f"unknown tool {name}"
        hooks, extra = self.ext and not self.hooking, ""
        if hooks:
            for h in self.ext.matching("before_tool", name, json.dumps(args or {}, default=str)):
                if h["spec"]["action"]["type"] == "block":
                    why = extensions.fill(h["spec"]["action"]["reason"], {"said": self.said, "tool": name})
                    return json.dumps({"error": f"blocked: {why}"}), f"{name}: blocked ({why})"
                extra += self._hook(h, {"said": self.said, "tool": name})
        result, summary = self._run(name, fn, args)
        if hooks:
            for h in self.ext.matching("after_tool", name, result):
                extra += self._hook(h, {"said": self.said, "tool": name, "result": result[:4000]})
        if extra:  # what hooks add, next to what the tool gave back
            try:
                got = json.loads(result)
            except ValueError:
                got = result
            result = json.dumps({"result": got, "note": extra.strip()}, ensure_ascii=False, default=str)
        return result, summary

    def _ext_tool(self, name):
        """An extension's tool (or use_skill), called like the built-in ones."""
        if name == "use_skill":
            return self.t_use_skill
        t = (self.ext.tools if self.ext else {}).get(name)
        if not t:
            raise AttributeError(name)

        def run(**args):
            args = extensions.tool_args(t["spec"], args)
            what = f"{t['name']}(" + ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)[:60]}" for k, v in args.items()) + ")"
            if t["spec"]["effect"] == "change" and not self.act:
                return self._approval(
                    "extension", {"tool": t["name"], "extension": t["ext"], "version": t["version"], "args": args}, f"Run {what}"
                )
            out = extensions.run_tool(self.db, self.cfg, t["spec"], args, toolbox=self)
            return out, f"Ran {what}"

        return run

    def t_use_skill(self, name):
        s = (self.ext.skills if self.ext else {}).get(name)
        if not s:
            raise ValueError(f"no skill {name}")
        out = {"skill": name, "instructions": s["spec"]["instructions"]}
        if s["spec"].get("tools"):
            out["tools"] = s["spec"]["tools"]
        return out, f"Read the skill {name}"

    # ---- extensions, from chat ----
    def _ext_named(self, name):
        for g in extensions.visible(self.db, self.me):
            if g["name"] == name:
                return g
        raise ValueError(f"no extension called {name} that you can see")

    def t_list_extensions(self, kind=None):
        out = [
            {"name": g["name"], "kind": g["kind"], "description": g.get("description"), "on": g["enabled"], "version": g["version"],
             "yours_to_change": g["editable"], "shared": g["visibility"]}
            for g in extensions.visible(self.db, self.me, kind)
        ]  # fmt: skip
        return {"extensions": out}, f"Listed {len(out)} extension(s)"

    def t_read_extension(self, name):
        g = self._ext_named(name)
        return {"name": name, "manifest": extensions.to_manifest(g)}, f"Read the extension {name}"

    def t_save_extension(self, manifest, notes=None):
        m = extensions.check_manifest(extensions.parse_manifest(manifest), self.me, self.db)
        same = next((g for g in extensions.visible(self.db, self.me) if g["name"] == m["name"]), None)
        if same:
            if not same["editable"]:
                raise ValueError(f"{m['name']} is someone else's: choose another name")
            if same["kind"] != m["kind"]:
                raise ValueError(f"{m['name']} is a {same['kind']}: choose another name for a {m['kind']}")
            what = f"Save version {same['version'] + 1} of the {m['kind']} {m['name']}"
        else:
            extensions._check_names(self.db, m["kind"], m["name"], m["spec"])
            what = f"Add the {m['kind']} {m['name']} to the assistant"
        return self._approval("save_extension", {"manifest": manifest, "notes": notes, "name": m["name"]}, what)

    def t_switch_extension(self, name, enabled):
        g = self._ext_named(name)
        if not g["editable"]:
            raise ValueError(f"{name} is someone else's; only its owner or an admin switches it")
        what = f"Switch {g['kind']} {name} {'on' if enabled else 'off'}"
        return self._approval("switch_extension", {"extension": g["id"], "name": name, "enabled": bool(enabled)}, what)

    def _run(self, name, fn, args):
        try:
            out, summary = fn(**{k: v for k, v in (args or {}).items() if v is not None})
        except KeyError as e:  # an id or name that doesn't exist
            return json.dumps({"error": f"not found: {e.args[0] if e.args else e}"}), f"{name}: not found"
        except (ValueError, TypeError, PermissionError) as e:
            return json.dumps({"error": str(e)}), f"{name}: {e}"
        except Exception as e:  # noqa: BLE001 - the model hears what went wrong instead of the answer breaking off
            log.exception("assistant tool %s failed", name)
            return json.dumps({"error": f"the tool failed: {e}"}), f"{name} failed"
        return json.dumps(out, ensure_ascii=False, default=str)[:12000], summary

    # ---- read tools ----
    def t_search_transcripts(self, query, namespace=None, limit=8):
        # within the conversation's scope in the search itself, so out-of-scope matches don't crowd out the rest
        res = searchmod.search(
            self.db, query, namespace, limit=min(int(limit), 20), spaces=self.readable, recordings=self.allowed, cfg=self.cfg, mode="auto"
        )
        hits = res["hits"]
        out = []
        for h in hits:
            text = h["snippet"].replace("<mark>", "").replace("</mark>", "")
            n = self.ref(h["recording_id"], h["t0"], text, h.get("speaker"), h.get("title"), h.get("source", "said"), h.get("page"))
            where = {"page": h["page"] + 1} if h.get("page") is not None else {"time": store.tc(h["t0"])}
            out.append(
                {
                    "ref": n,
                    "recording_id": h["recording_id"],
                    "title": h.get("title"),
                    **where,
                    "speaker": h.get("speaker"),
                    "source": h.get("source"),
                    "text": text,
                }
            )
        return {"total": res["total"], "results": out}, f'Searched for "{query}": {res["total"]} match(es)'

    def t_list_recordings(self, namespace=None, limit=20, speaker_id=None, entity_id=None, media=None, **dates):
        f = recsets.clean_filter(
            {
                "namespaces": [namespace] if namespace else None,
                "speakers": [speaker_id] if speaker_id else None,
                "entities": [entity_id] if entity_id else None,
                "media": media,
                "from": dates.get("from"),
                "to": dates.get("to"),
            }
        )
        ids = recsets.resolve(self.db, self.readable, f)
        if self.allowed is not None:
            ids = [i for i in ids if i in self.allowed]
        rows = (
            self.db.rows(
                "SELECT record::id(id) AS id, title, recorded_at, duration_ms, space FROM recording WHERE id IN $ids",
                ids=[R("recording", i) for i in ids[: min(int(limit), 50)]],
            )
            if ids
            else []
        )
        names = store.space_names(self.db)
        out = [
            {
                "id": r["id"],
                "title": r["title"],
                "date": (r.get("recorded_at") or "")[:10],
                "duration": store.tc(r.get("duration_ms")),
                "namespace": names.get(r["space"]),
            }
            for r in sorted(rows, key=lambda r: r.get("recorded_at") or "", reverse=True)
        ]
        return {"total": len(ids), "recordings": out}, f"Listed {len(ids)} recording(s)"

    def t_read_transcript(self, recording_id, start_seconds=0, end_seconds=None, max_lines=60):
        cap = self.cfg["ai"].get("max_transcript_reads") or 20
        if self.reads >= cap:
            raise ValueError(f"the limit of {cap} transcript reads per question is reached; answer with what you have")
        row = self._ok(recording_id)
        self.reads += 1
        d = render.player_data(self.db, int(recording_id))
        names = {s["key"]: s["name"] for s in d["speakers"]}
        lo, hi = float(start_seconds) * 1000, float(end_seconds) * 1000 if end_seconds is not None else float("inf")
        lines = []
        for s in d["segments"]:
            if lo <= s["t0"] <= hi and len(lines) < min(int(max_lines), 200):
                page = s.get("p")
                n = self.ref(int(recording_id), s["t0"], s["text"], names.get(s["s"]), row["title"], page=page)
                if page is None:
                    lines.append(f"[{n}] {store.tc(s['t0'])} {names.get(s['s'], 'Unknown')}: {s['text']}")
                else:  # a document's text, by page
                    lines.append(f"[{n}] p. {page + 1}: {s['text']}")
        return {"title": row["title"], "lines": lines}, f"Read {len(lines)} line(s) of {row['title']}"

    def t_recording_outputs(self, recording_id):
        row = self._ok(recording_id)
        rec = self.db.one("SELECT summary FROM $r", r=R("recording", int(recording_id))) or {}
        outs = {o["key"]: o["value"] for o in self.db.rows("SELECT key, value FROM output WHERE recording = $r", r=int(recording_id))}
        return {"title": row["title"], "summary": rec.get("summary"), "outputs": outs}, f"Read the outputs of {row['title']}"

    def t_find_entities(self, query=None, type=None, namespace=None, limit=10):  # noqa: A002
        res = entities.list_entities(
            self.db,
            self.readable,
            q=query,
            types=[type] if type else None,
            namespaces=[namespace] if namespace else None,
            limit=min(int(limit), 30),
        )
        out = [
            {
                "id": e["id"],
                "name": e["name"],
                "type": e["type_label"],
                "namespace": e["namespace"],
                "mentions": e["mentions"],
                "recordings": e["recordings"],
                **({"description": e["description"]} if e.get("description") else {}),
                **({"also_said_as": e["aliases"]} if e.get("aliases") else {}),
                **({"defined": True} if e.get("defined") else {}),
                **({"always_there": e["builtin"]} if e.get("builtin") else {}),
            }
            for e in res["items"]
        ]
        return {"total": res["total"], "entities": out}, f"Found {res['total']} entit{'y' if res['total'] == 1 else 'ies'}"

    def t_entity_mentions(self, entity_id, limit=8):
        res = entities.mentions(self.db, int(entity_id), self.readable, limit=min(int(limit), 30))
        out = [
            {
                "ref": self.ref(m["recording_id"], m["t0"] or 0, m["text"], m.get("speaker"), m.get("title")),
                "title": m["title"],
                "time": m["time"],
                "speaker": m.get("speaker"),
                "text": m["text"],
            }
            for m in res["items"]
            if self.allowed is None or m["recording_id"] in self.allowed
        ]
        return {"total": res["total"], "mentions": out}, f"Read {len(out)} mention(s)"

    def t_entity_timeline(self, entity_id):
        return entities.timeline(self.db, [int(entity_id)], self.readable), "Counted mentions by month"

    def t_find_topics(self, query=None, namespace=None, limit=20):
        spaces = self.readable
        if namespace:
            sid = {v: k for k, v in store.space_names(self.db).items()}.get(namespace)
            if sid not in self.readable:
                raise ValueError(f"no namespace called {namespace} in scope")
            spaces = {sid}
        res = topics.list_topics(self.db, spaces, query or "", limit=max(1, min(int(limit or 20), 50)))
        up = sorted({b for t in res["items"] for b in t["broader"]})
        label = {
            t["id"]: t["label"]
            for t in (
                self.db.rows("SELECT record::id(id) AS id, label FROM topic WHERE id IN $ids", ids=[R("topic", b) for b in up])
                if up
                else []
            )
        }
        out = [
            store.clean(
                {
                    "id": t["id"],
                    "label": t["label"],
                    "namespace": t.get("namespace"),
                    "also": t["alt"] or None,
                    "definition": t.get("definition"),
                    "broader": [label.get(b, b) for b in t["broader"]] or None,
                    "recordings": t["recordings"],
                }
            )
            for t in res["items"]
        ]
        return {"total": res["total"], "topics": out}, f"Found {res['total']} topic(s)"

    def t_topic_recordings(self, topic_id):
        t = topics.detail(self.db, int(topic_id), self.readable)
        about = [a for a in t["about"] if self.allowed is None or a["recording"] in self.allowed]
        out = {
            "id": t["id"],
            "label": t["label"],
            "namespace": t.get("namespace"),
            "definition": t.get("definition"),
            "broader": t["broader"],
            "narrower": t["narrower"],
            "related": t["related"],
            "recordings": [{"recording_id": a["recording"], "title": a["title"], "status": a["status"]} for a in about],
        }
        return out, f"Read the topic {t['label']}"

    def t_suggest_topic(self, topic_id, recording_ids):
        row = self.db.one("SELECT space, label FROM $r", r=R("topic", int(topic_id)))
        if not row or row["space"] not in self.readable:
            raise ValueError(f"no topic {topic_id} in scope")
        if row["space"] not in self.editable:
            raise ValueError("suggesting topics needs editor access to the namespace")
        for rid in recording_ids or []:
            self._ok(rid)
        made = topics.propose(self.db, int(topic_id), recording_ids, "assistant", self.user.get("email"))
        return {"suggested_for": made, "note": "waiting for someone to accept"}, f"Suggested {row['label']} for {len(made)} recording(s)"

    def t_graph_neighbours(self, entity_id=None, speaker_id=None, limit=15):
        if not entity_id and not speaker_id:
            raise ValueError("give an entity_id or a speaker_id")
        focus = f"e{entity_id}" if entity_id else f"s{speaker_id}"
        g = entities.neighbourhood(self.db, focus, self.readable, 1, limit=min(int(limit), 40))
        labels = {n["id"]: n["label"] for n in g["nodes"]}
        out = [
            {"with": labels.get(e["b"] if e["a"] == focus else e["a"]), "kind": e["kind"], "strength": e["weight"]}
            for e in g["edges"]
            if focus in (e["a"], e["b"])
        ]
        return {
            "focus": labels.get(focus),
            "connections": sorted(out, key=lambda x: -x["strength"]),
        }, f"Explored the graph around {labels.get(focus)}"

    def t_speaker_stats(self, namespace):
        sid = store.ns_id(self.db, namespace, create=False)
        if sid not in self.readable:
            raise ValueError(f"no namespace {namespace} here")
        out = [
            {"id": s["id"], "name": s["display"], "talk": store.tc(s["talk_ms"]), "recordings": s["recordings"]}
            for s in spk.list_speakers(self.db, sid)[:30]
        ]
        return {"speakers": out}, f"Read speaker stats for {namespace}"

    # ---- the graph as a property graph, in Cypher (graph_model.py, cypher.py) ----
    def _graph(self, namespace=None):
        """The conversation's graph: one namespace, or the shared ones in scope (only its recordings, when it has some)."""
        if namespace:
            sid = store.space_names(self.db)
            if namespace not in {sid[s] for s in self.readable if s in sid}:
                raise ValueError(f"no namespace called {namespace} in scope")
            scope = f"ns:{namespace}"
        else:
            names = store.space_names(self.db)
            mine = sorted(names[s] for s in self.readable if s in names)
            scope = f"ns:{mine[0]}" if len(mine) == 1 else "global"
        key = (scope, None if self.allowed is None else len(self.allowed))
        cache = getattr(self, "_graphs", None)
        if cache is None:
            cache = self._graphs = {}
        if key not in cache:
            cache[key] = graph_model.build(self.db, scope, self.readable, self.allowed)
        return cache[key]

    def t_graph_schema(self, namespace=None):
        g = self._graph(namespace)
        return {"graph": graph_ask.describe(g), "namespaces": g.namespaces, "examples": graph_ask.EXAMPLES}, "Read what the graph holds"

    def t_graph_query(self, query, namespace=None, limit=50):
        g = self._graph(namespace)
        try:
            out = cypher.run(g, query, max_rows=max(1, min(int(limit or 50), 200)))
        except cypher.CypherError as e:
            raise ValueError(f"query error: {e}") from None
        return {"columns": out["columns"], "rows": out["rows"], "truncated": out["truncated"]}, (
            f"Queried the graph ({len(out['rows'])} row(s))"
        )

    def t_graph_related(self, node, relation, depth=1, namespace=None):
        g = self._graph(namespace)
        try:
            out = graph_model.related(g, node, relation, max(1, min(int(depth or 1), 6)), limit=80)
        except KeyError:
            raise ValueError(f"{node} isn't in this graph") from None
        nodes = [{k: v for k, v in n.items() if k in ("id", "labels", "name", "namespace", "depth", "type")} for n in out["nodes"]]
        return {"start": out["start"], "nodes": nodes, "truncated": out["truncated"]}, f"Found the {relation} of {node}"

    def t_graph_paths(self, from_node, to_node, max_depth=4, namespace=None):
        g = self._graph(namespace)
        try:
            out = graph_model.paths(g, from_node, to_node, max(1, min(int(max_depth or 4), 6)), limit=5)
        except KeyError:
            raise ValueError("one of those nodes isn't in this graph") from None
        names = {n["id"]: n.get("name") for n in out["nodes"]}
        chains = [[names.get(x, x) for x in p["nodes"]] for p in out["paths"]]
        return {"paths": chains, "found": bool(chains)}, f"Looked for paths from {from_node} to {to_node}"

    # ---- tools that need approval ----
    def _approval(self, tool, args, summary, estimate=None):
        aid = self.db.next_id("approval")
        self.db.q(
            "CREATE $r CONTENT $d",
            r=R("approval", aid),
            d=store.clean(
                {
                    "chat": self.chat,
                    "account": self.user["id"],
                    "tool": tool,
                    "args": args,
                    "summary": summary,
                    "estimate": estimate,
                    "status": "pending",
                    "created_at": store.now(),
                }
            ),
        )
        self.approvals.append({"id": aid, "tool": tool, "summary": summary, "estimate": estimate})
        return {"status": "waiting for the person's approval", "approval": aid, "summary": summary}, f"Asked for approval: {summary}"

    def t_run_template(self, template_id, recording_ids=None, collection_id=None):
        t = templates.get(self.db, int(template_id))
        spec = {"collection": collection_id} if collection_id else {"recordings": recording_ids or sorted(self.allowed or [])}
        ids, skipped = batches.select(self.db, spec, self.readable, self.editable)
        steps, label = batches.steps_for(self.db, {"template": t["id"]})
        est = batches.estimate(self.db, self.cfg, ids, steps)
        return self._approval("run_template", {"template_id": t["id"], "recordings": ids}, f"Run {label} on {len(ids)} recording(s)", est)

    # ---- notes (notebook.py): the assistant reads and writes them at once; they're its notebook ----
    def _note(self, note_id, edit=False):
        try:
            p = notebook.get(self.db, int(note_id))
        except KeyError:
            raise ValueError(f"no note {note_id}") from None
        if p["space"] not in (self.editable if edit else self.readable):
            raise ValueError(f"note {note_id} isn't {'yours to change' if edit else 'available'} in this conversation")
        return p

    def _space(self, namespace, edit=False):
        names = {v: k for k, v in store.space_names(self.db).items()}
        sid = names.get(namespace)
        if sid not in (self.editable if edit else self.readable):
            raise ValueError(f"no namespace called {namespace} {'you can write in' if edit else 'in scope'}")
        return sid

    def t_find_notes(self, query=None, namespace=None, place=None, limit=10):
        spaces = [self._space(namespace)] if namespace else sorted(self.readable)
        names = store.space_names(self.db)
        q = " ".join(str(query or "").split()).casefold()
        rows = self.db.rows(
            "SELECT record::id(id) AS id, space, title, summary, place, about, updated_at, text FROM note_page WHERE space IN $s",
            s=spaces,
        )
        hits = [
            r
            for r in rows
            if (not place or r.get("place") == place)
            and (not q or any(q in str(r.get(k) or "").casefold() for k in ("title", "summary", "text")))
        ]
        hits.sort(key=lambda r: (q not in str(r["title"]).casefold(), r.get("updated_at") or ""), reverse=False)
        out = [
            store.clean(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "summary": r.get("summary"),
                    "place": r.get("place"),
                    "page_of": r.get("about"),
                    "namespace": names.get(r["space"]),
                }
            )
            for r in hits[: min(int(limit or 10), 30)]
        ]
        return {"total": len(hits), "notes": out}, f"Found {len(hits)} note(s)"

    def t_read_note(self, note_id=None, about=None):
        if note_id is None and not about:
            raise ValueError("give note_id or about")
        if note_id is None:
            kind, _, key = str(about).partition(":")
            sid = notebook.owner(self.db, kind, int(key)) if key.isdigit() else None
            if sid not in self.readable:
                raise ValueError(f"{about} isn't available in this conversation")
            p = notebook.about(self.db, sid, about)
            if not p:
                return {
                    "page_of": about,
                    "note": None,
                    "hint": "nobody has written its page yet: write_note with about",
                }, f"{about} has no page yet"
        else:
            p = self._note(note_id)
        targets = [f"page:{p['id']}"] + ([p["about"]] if p.get("about") else [])
        out = store.clean(
            {
                "id": p["id"],
                "title": p["title"],
                "summary": p.get("summary"),
                "date": p.get("date"),
                "place": p.get("place"),
                "page_of": p.get("about"),
                "parent_id": p.get("parent"),
                "namespace": store.space_names(self.db).get(p["space"]),
                "written_by": p.get("author"),
                "body": (p.get("body") or "")[:20_000],
                "links": notebook.links(self.db, p["id"]),
                "linked_from": [b for b in notebook.backlinks(self.db, p["space"], targets) if b["page"] != p["id"]],
            }
        )
        return out, f"Read the note {p['title']}"

    def t_write_note(self, namespace, title, body, summary=None, place=None, parent_id=None, about=None):
        sid = self._space(namespace, edit=True)
        pid = notebook.create(self.db, sid, self.user["id"], title, body, summary, None, place, parent_id, about, author="assistant")
        return {"note_id": pid, "url": f"/notes/{pid}"}, f"Wrote the note {title}"

    def t_update_note(self, note_id, title=None, summary=None, place=None, body=None, append=None, parent_id=None):
        p = self._note(note_id, edit=True)
        if append:
            body = ((body if body is not None else p.get("body") or "").rstrip() + "\n\n" + append).strip()
        kw = {}
        if summary is not None:
            kw["summary"] = summary
        if place is not None:
            kw["place"] = place
        notebook.update(self.db, p["id"], self.user["id"], title=title, body=body, author="assistant", **kw)
        if parent_id is not None:
            notebook.move(self.db, p["id"], int(parent_id) or None)
        return {"note_id": p["id"], "url": f"/notes/{p['id']}"}, f"Changed the note {title or p['title']}"

    def t_entity_setup(self, namespace):
        names = {v: k for k, v in store.space_names(self.db).items()}
        sid = names.get(namespace)
        if sid not in self.readable:
            raise ValueError(f"no namespace called {namespace} in scope")
        setup = entity_setup.effective(self.db, sid)
        types = entity_setup.types_of(self.db, sid)
        label = {t["type"]: t["label"] for t in types}
        out = {
            "mode": {"self": "self-organising", "fixed": "fixed list", "hybrid": "fixed list, then self-organising"}.get(
                setup["mode"], setup["mode"]
            ),
            "types_kept": [label.get(t, t) for t in setup["types"]] or "all",
            "about": setup.get("description"),
            "matching": setup["matching"],
            "own_types": [{"type": t["type"], "label": t["label"], "description": t.get("description")} for t in types if not t["builtin"]],
            "defined_entities": [
                {"id": e["id"], "name": e["name"], "type": label.get(e["type"], e["type"])} for e in entity_map.defined(self.db, sid)
            ][:100],
            "collections_with_their_own_setup": len([c for c in entity_setup.scopes(self.db, sid) if c is not None]),
        }
        return out, f"Read how {namespace} organises its entities"

    def t_propose_entity_change(
        self,
        action,
        entity_id=None,
        merge_ids=None,
        new_name=None,
        new_type=None,
        description=None,
        also_said_as=None,
        namespace=None,
    ):
        needs = {
            "merge": ("merge_ids", merge_ids),
            "rename": ("new_name", new_name),
            "retype": ("new_type", new_type),
            "describe": ("description or also_said_as", description is not None or also_said_as is not None),
            "hide": ("entity_id", entity_id),
            "define": ("namespace and new_name", namespace and new_name),
        }
        if action not in needs:
            raise ValueError("action is one of " + ", ".join(needs))
        if not needs[action][1]:
            raise ValueError(f"{action} needs {needs[action][0]}")
        args = {
            "action": action,
            "merge_ids": merge_ids,
            "new_name": new_name,
            "new_type": new_type,
            "description": description,
            "also_said_as": also_said_as,
        }
        if action == "define":
            sid = {v: k for k, v in store.space_names(self.db).items()}.get(namespace)
            if sid not in self.readable:
                raise ValueError(f"no namespace called {namespace} in scope")
            typ = new_type or "TERM"
            if typ not in entity_setup.type_codes(self.db, sid):
                raise ValueError(f"unknown type {typ}")
            return self._approval(
                "propose_entity_change", {**args, "namespace": namespace, "new_type": typ}, f"Add {new_name} to the entities of {namespace}"
            )
        if not entity_id:
            raise ValueError(f"{action} needs entity_id")
        e = entities.detail(self.db, int(entity_id), self.readable)
        what = {
            "merge": f"Merge {len(merge_ids or [])} entit{'y' if len(merge_ids or []) == 1 else 'ies'} into {e['name']}",
            "rename": f"Rename {e['name']} to {new_name}",
            "retype": f"Change {e['name']} to {new_type}",
            "describe": f"Describe {e['name']}"
            + (f" as “{description}”" if description else "")
            + (f" (also said as {', '.join(also_said_as)})" if also_said_as else ""),
            "hide": f"Hide {e['name']}",
        }[action]
        return self._approval("propose_entity_change", {**args, "entity_id": e["id"]}, what)

    def cited(self, text):
        import re

        used = {int(n) for n in re.findall(r"\[(\d+)\]", text or "")}
        return [{**r, "used": r["n"] in used} for r in self.refs if r["n"] in used]


def approve(db, cfg, aid, user, editable, decision="approve", base=None, admin=False, readable=None):
    """Carry out an approved action: a batch run (or a sample of it), an entity change, or a change to the server
    (ops_tools.py: settings, a namespace, importing attached files)."""
    a = db.one("SELECT record::id(id) AS id, chat, account, tool, args, status FROM $r", r=R("approval", int(aid)))
    if not a or a["status"] != "pending":
        raise ValueError("nothing to approve")
    if decision == "decline":
        db.q(
            "UPDATE $r SET status = 'declined', decided_at = $t, decided_by = $u", r=R("approval", a["id"]), t=store.now(), u=user["email"]
        )
        return {"status": "declined"}
    with graph_history.acting(actor=user["email"], via="assistant", approval=a["id"]):
        result = _carry_out(db, cfg, a, user, editable, decision, base, admin, readable)
    db.q(
        "UPDATE $r SET status = 'done', decided_at = $t, decided_by = $u, result = $res",
        r=R("approval", a["id"]),
        t=store.now(),
        u=user["email"],
        res=result,
    )
    return {"status": "done", **result}


def _carry_out(db, cfg, a, user, editable, decision, base, admin, readable):
    args = a["args"]
    if a["tool"] == "extension":
        box = Toolbox(db, cfg, user, readable if readable is not None else editable, editable, None, a["chat"], base, admin)
        result = run_extension(db, cfg, args, user, admin, box.readable, box)
    elif a["tool"] in ("save_extension", "switch_extension"):
        result = author_extension(db, a["tool"], args, user, admin, readable if readable is not None else editable, editable)
    elif a["tool"] in ("change_settings", "create_namespace", "import_files"):
        result = ops_tools.apply(db, cfg, base or cfg, a["tool"], args, user, editable, admin)
    elif a["tool"] == "run_template":
        steps, label = batches.steps_for(db, {"template": args["template_id"]})
        ids = [i for i in args["recordings"] if (db.one("SELECT space FROM $r", r=R("recording", i)) or {}).get("space") in editable]
        bid = batches.create(
            db,
            cfg,
            user["email"],
            ids,
            steps,
            label,
            {"approval": a["id"]},
            sample=3 if decision == "sample" else None,
            confirm=f"RUN {len(ids)}",
        )
        result = {"batch": bid}
    elif args["action"] == "define":
        sid = {v: k for k, v in store.space_names(db).items()}.get(args["namespace"])
        if sid not in editable:
            raise PermissionError("you can't change entities in that namespace")
        eid = entity_map.define(
            db, sid, args["new_name"], args["new_type"], args.get("description"), args.get("also_said_as") or [], user=user["email"]
        )
        result = {"entity": eid}
    else:
        eid = int(args["entity_id"])
        if (db.one("SELECT space FROM $r", r=R("entity", eid)) or {}).get("space") not in editable:
            raise PermissionError("you can't change entities in that namespace")
        if args["action"] == "describe":
            if args.get("also_said_as") is not None:
                entity_map.set_aliases(db, eid, args["also_said_as"], user=user["email"])
            if args.get("description") is not None:
                entities.describe(db, eid, args["description"])
            result = {"entity": eid}
        elif args["action"] == "hide":
            entities.hide(db, eid, True, "the assistant, approved")
            result = {"entity": eid}
        elif args["action"] == "merge":
            result = {"merge": entities.merge(db, eid, args.get("merge_ids") or [], user["email"])}
        elif args["action"] == "rename":
            result = entities.rename(db, eid, args.get("new_name"))
        else:
            entities.retype(db, [eid], args.get("new_type"))
            result = {"type": args.get("new_type")}
    return result


def run_extension(db, cfg, args, user, admin, readable, toolbox=None):
    """An approved extension tool: the version that was proposed, if it's still on and still the person's to use."""
    g = extensions.get(db, int(args["extension"]), args.get("version"))
    me = extensions.who(user["id"], user.get("email"), admin, {s: "viewer" for s in readable})
    if g.get("deleted_at") or not g.get("enabled") or not extensions.can_use(g, me):
        raise ValueError("that extension was switched off or removed")
    items = g["spec"]["items"] if g["kind"] == "plugin" else [g]
    t = next((it for it in items if it.get("kind") == "tool" and it["name"] == args["tool"]), None)
    if not t:
        raise ValueError(f"the extension has no tool {args['tool']} any more")
    out = extensions.run_tool(db, cfg, t["spec"], extensions.tool_args(t["spec"], args.get("args") or {}), toolbox=toolbox)
    return {"output": out}


def author_extension(db, tool, args, user, admin, readable, editable):
    """An approved change to the extensions, made in chat: a manifest saved (new, or a new version), or one switched."""
    me = extensions.who(user["id"], user.get("email"), admin, {s: "editor" if s in editable else "viewer" for s in readable})
    if tool == "switch_extension":
        extensions.update(db, me, int(args["extension"]), enabled=bool(args["enabled"]))
        return {"extension": int(args["extension"]), "enabled": bool(args["enabled"])}
    manifest = extensions.parse_manifest(args["manifest"])
    same = next((g for g in extensions.visible(db, me) if g["name"] == manifest.get("name")), None)
    if same:
        n = extensions.save_version(db, me, same["id"], manifest, args.get("notes"), origin="chat")
        return {"extension": same["id"], "version": n}
    return {"extension": extensions.create(db, me, manifest, origin="chat"), "version": 1}
