"""Tools the chat assistant can call, limited to what the asker may read (and to the conversation's scope).

Read tools answer at once and number every moment they return, so the assistant can cite them as [n]. Tools that
run work or change data don't act: they create an approval the person accepts or declines in the conversation.
"""

from __future__ import annotations

import json

from . import batches, entities, recsets, render, search as searchmod, speakers as spk, store, templates

R = store.R
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
        "propose_entity_change",
        "Propose merging, renaming or retyping an entity. Needs the person's approval.",
        {
            "action": {"type": "string", "enum": ["merge", "rename", "retype"]},
            "entity_id": _I,
            "merge_ids": {"type": "array", "items": _I},
            "new_name": _S,
            "new_type": _S,
        },
        ["action", "entity_id"],
        True,
    ),
]


class Toolbox:
    def __init__(self, db, cfg, user, readable, editable, scope, chat_id):
        self.db, self.cfg, self.user, self.chat = db, cfg, user, chat_id
        self.scope = scope or {}
        self.readable, self.editable = set(readable), set(editable)
        names = store.space_names(db)
        if self.scope.get("namespaces"):
            self.readable = {s for s in self.readable if names.get(s) in self.scope["namespaces"]}
        self.allowed = (
            set(recsets.resolve(db, self.readable, recordings=self.scope["recordings"])) if self.scope.get("recordings") else None
        )
        self.refs, self.reads, self.approvals = [], 0, []

    def specs(self):
        off = set(self.cfg["ai"].get("disabled_tools") or [])
        can_act = bool(self.editable)
        return [
            {
                "type": "function",
                "function": {"name": n, "description": d, "parameters": {"type": "object", "properties": p, "required": req}},
            }
            for n, d, p, req, needs in TOOLS
            if n not in off and (can_act or not needs)
        ]

    def ref(self, rid, t0, text, speaker=None, title=None, source="said"):
        self.refs.append(
            {
                "n": len(self.refs) + 1,
                "recording_id": rid,
                "t0": t0,
                "time": store.tc(t0),
                "speaker": speaker,
                "title": title,
                "text": text,
                "source": source,
            }
        )
        return len(self.refs)

    def _ok(self, rid):
        row = self.db.one("SELECT space, title FROM $r", r=R("recording", int(rid)))
        if not row or row["space"] not in self.readable or (self.allowed is not None and int(rid) not in self.allowed):
            raise ValueError(f"recording {rid} isn't available in this conversation")
        return row

    def call(self, name, args):
        try:
            fn = getattr(self, "t_" + name)
        except AttributeError:
            return json.dumps({"error": f"no tool {name}"}), f"unknown tool {name}"
        try:
            out, summary = fn(**{k: v for k, v in (args or {}).items() if v is not None})
        except (ValueError, KeyError, TypeError) as e:
            return json.dumps({"error": str(e)}), f"{name}: {e}"
        return json.dumps(out, ensure_ascii=False, default=str)[:12000], summary

    # ---- read tools ----
    def t_search_transcripts(self, query, namespace=None, limit=8):
        res = searchmod.search(self.db, query, namespace, limit=min(int(limit), 20), spaces=self.readable)
        hits = [h for h in res["hits"] if self.allowed is None or h["recording_id"] in self.allowed]
        out = []
        for h in hits:
            text = h["snippet"].replace("<mark>", "").replace("</mark>", "")
            n = self.ref(h["recording_id"], h["t0"], text, h.get("speaker"), h.get("title"), h.get("source", "said"))
            out.append(
                {
                    "ref": n,
                    "recording_id": h["recording_id"],
                    "title": h.get("title"),
                    "time": store.tc(h["t0"]),
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
                n = self.ref(int(recording_id), s["t0"], s["text"], names.get(s["s"]), row["title"])
                lines.append(f"[{n}] {store.tc(s['t0'])} {names.get(s['s'], 'Unknown')}: {s['text']}")
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

    def t_propose_entity_change(self, action, entity_id, merge_ids=None, new_name=None, new_type=None):
        e = entities.detail(self.db, int(entity_id), self.readable)
        what = {
            "merge": f"Merge {len(merge_ids or [])} entit{'y' if len(merge_ids or []) == 1 else 'ies'} into {e['name']}",
            "rename": f"Rename {e['name']} to {new_name}",
            "retype": f"Change {e['name']} to {new_type}",
        }[action]
        return self._approval(
            "propose_entity_change",
            {"action": action, "entity_id": e["id"], "merge_ids": merge_ids, "new_name": new_name, "new_type": new_type},
            what,
        )

    def cited(self, text):
        import re

        used = {int(n) for n in re.findall(r"\[(\d+)\]", text or "")}
        return [{**r, "used": r["n"] in used} for r in self.refs if r["n"] in used]


def approve(db, cfg, aid, user, editable, decision="approve"):
    """Carry out an approved action: a batch run (or a sample of it) or an entity change."""
    a = db.one("SELECT record::id(id) AS id, chat, account, tool, args, status FROM $r", r=R("approval", int(aid)))
    if not a or a["status"] != "pending":
        raise ValueError("nothing to approve")
    if decision == "decline":
        db.q(
            "UPDATE $r SET status = 'declined', decided_at = $t, decided_by = $u", r=R("approval", a["id"]), t=store.now(), u=user["email"]
        )
        return {"status": "declined"}
    args = a["args"]
    if a["tool"] == "run_template":
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
    else:
        eid = int(args["entity_id"])
        if (db.one("SELECT space FROM $r", r=R("entity", eid)) or {}).get("space") not in editable:
            raise PermissionError("you can't change entities in that namespace")
        if args["action"] == "merge":
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
