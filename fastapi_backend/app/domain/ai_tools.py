"""Tools the chat assistant can call, limited to what the asker may read (and to the conversation's scope).

Read tools answer at once and number every moment they return, so the assistant can cite them as [n]. Tools that
run work or change data don't act: they create an approval the person accepts or declines in the conversation.
"""

from __future__ import annotations

import json
import logging

from . import (
    batches,
    entities,
    extensions,
    entity_map,
    entity_setup,
    ops_tools,
    recsets,
    render,
    search as searchmod,
    speakers as spk,
    store,
    templates,
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
        "Find people, organisations, products, places and topics mentioned in scope.",
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
        "graph_neighbours",
        "Who and what is connected to an entity or speaker in the knowledge graph.",
        {"entity_id": _I, "speaker_id": _I, "limit": _I},
        [],
        False,
    ),
    ("speaker_stats", "Speakers in a namespace with talk time and recordings.", {"namespace": _S}, ["namespace"], False),
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


def builtin_names():
    """The assistant's own tool names, which extensions can't take."""
    return {t[0] for t in TOOLS + ops_tools.ADMIN_TOOLS + ops_tools.FILE_TOOLS} | {"use_skill"}


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
        me = extensions.who(user["id"], user.get("email"), admin, {s: "viewer" for s in readable})
        self.ext = extensions.Active(db, me) if cfg["ai"].get("extensions", True) else None
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
            out = extensions.run_tool(self.db, self.cfg, t["spec"], args)
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


def approve(db, cfg, aid, user, editable, decision="approve", base=None, admin=False):
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
    args = a["args"]
    if a["tool"] == "extension":
        result = run_extension(db, cfg, args, user, admin, editable)
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
    db.q(
        "UPDATE $r SET status = 'done', decided_at = $t, decided_by = $u, result = $res",
        r=R("approval", a["id"]),
        t=store.now(),
        u=user["email"],
        res=result,
    )
    return {"status": "done", **result}


def run_extension(db, cfg, args, user, admin, editable):
    """An approved extension tool: the version that was proposed, if it's still on and still the person's to use."""
    g = extensions.get(db, int(args["extension"]), args.get("version"))
    me = extensions.who(user["id"], user.get("email"), admin, {s: "editor" for s in editable})
    if g.get("deleted_at") or not g.get("enabled") or not extensions.can_see(g, me):
        raise ValueError("that extension was switched off or removed")
    items = g["spec"]["items"] if g["kind"] == "plugin" else [g]
    t = next((it for it in items if it.get("kind") == "tool" and it["name"] == args["tool"]), None)
    if not t:
        raise ValueError(f"the extension has no tool {args['tool']} any more")
    out = extensions.run_tool(db, cfg, t["spec"], extensions.tool_args(t["spec"], args.get("args") or {}))
    return {"output": out}
