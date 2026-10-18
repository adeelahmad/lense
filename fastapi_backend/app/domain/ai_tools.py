"""Tools the chat assistant can call, limited to what the asker may read (and to the conversation's scope).

Read tools answer at once and number every moment they return, so the assistant can cite them as [n]. Tools that
run work or change data don't act: they create an approval the person accepts or declines in the conversation.
"""

from __future__ import annotations

import html
import json
import logging

from . import (
    auth,
    batches,
    classify as classifymod,
    connection_tools,
    cypher,
    dates,
    decide,
    entities,
    event_marks,
    extensions,
    entity_map,
    events,
    find as findmod,
    graph_ask,
    graph_model,
    entity_setup,
    graph_history,
    llm,
    notebook,
    ns_assistant,
    ops_tools,
    recsets,
    render,
    research as researchmod,
    sandbox as sandboxmod,
    search as searchmod,
    browser as browsermod,
    similar,
    speakers as spk,
    specs,
    store,
    templates,
    topics,
    untrusted,
    uploads,
    web as webmod,
    webcapture,
    wiki,
)

R = store.R
log = logging.getLogger("lens")
_I = {"type": "integer"}
_S = {"type": "string"}
_LIST = {"type": "array", "items": {"type": ["string", "integer"]}}
TOOLS = [
    (
        "search_transcripts",
        "Search the text of the assets in scope: transcripts of speech, document text, and text shown on screen in videos.",
        {"query": _S, "namespace": _S, "limit": _I},
        ["query"],
        False,
    ),
    (
        "find_similar",
        "Find the assets (recordings, documents and images) in scope most like a text, an asset, a topic or an entity (give "
        "one): close in meaning (when search by meaning is set up), sharing key words, about the same topics, mentioning "
        "the same entities; each with the moments that show it.",
        {"text": _S, "recording_id": _I, "topic_id": _I, "entity_id": _I, "limit": _I},
        [],
        False,
    ),
    (
        "list_recordings",
        "List assets (recordings, documents, images and text) in scope, newest first, optionally by namespace, dates (YYYY-MM-DD), person, entity or media.",
        {"namespace": _S, "from": _S, "to": _S, "speaker_id": _I, "entity_id": _I, "media": _S, "limit": _I},
        [],
        False,
    ),
    (
        "find_resources",
        "Find assets (recordings, videos, documents and images) in scope by any attribute: who is seen in them (faces) or heard "
        "(speakers), entities they mention, topics they're about, their type (audio, video, transcript, document, image), "
        "content type (podcast, interview, scan, photo, email …), dates (a day, month or year), tags, language, objects "
        "seen, emotions heard, length, and words said in them (text). Faces, speakers, entities and topics take ids or "
        "names. Each result says why it matched, with numbered moments to cite.",
        {
            "text": _S,
            "faces": _LIST,
            "speakers": _LIST,
            "entities": _LIST,
            "topics": _LIST,
            "types": _LIST,
            "content_types": _LIST,
            "date_from": _S,
            "date_to": _S,
            "min_duration": {"type": "number"},
            "max_duration": {"type": "number"},
            "tags": _LIST,
            "languages": _LIST,
            "objects": _LIST,
            "emotions": _LIST,
            "namespace": _S,
            "limit": _I,
        },
        [],
        False,
    ),
    (
        "timeline",
        "What happened in Lens, newest first (new assets, and every other kind of activity there is): who did it, what "
        "it is about, when, and a one-line summary. Optionally by kinds (such as asset.added), namespace, entity tag, "
        "time (since, until: ISO 8601), words in the summary, unreviewed (those the person hasn't marked reviewed); "
        "give next as before for more.",
        {
            "kinds": {"type": "array", "items": _S},
            "namespace": _S,
            "tag": _S,
            "since": _S,
            "until": _S,
            "q": _S,
            "unreviewed": {"type": "boolean"},
            "before": _S,
            "limit": _I,
        },
        [],
        False,
    ),
    (
        "classify",
        "Classify an asset (or a text): which of some labels fits best, how sure, and every label ranked. Give "
        'labels (names, or "name: what it means"), or a vocabulary: content_type (Lens\'s content types, such '
        "as podcast, interview, scan, email) or topic (the namespace's topics). It changes nothing; sure says whether "
        "the answer is sure enough to act on.",
        {
            "recording_id": _I,
            "text": _S,
            "labels": {"type": "array", "items": _S},
            "vocabulary": {"type": "string", "enum": list(classifymod.VOCABULARIES)},
            "namespace": _S,
            "question": _S,
        },
        [],
        False,
    ),
    (
        "mark_activities",
        "Mark activities of the timeline (by their ids from timeline) reviewed, important or hidden, or clear those "
        "marks (false), for the person alone; marks left out stay as they are.",
        {
            "ids": {"type": "array", "items": _S},
            "reviewed": {"type": "boolean"},
            "important": {"type": "boolean"},
            "hidden": {"type": "boolean"},
        },
        ["ids"],
        False,
    ),
    (
        "approvals",
        "What waits for the person's pick, newest first: automations' actions that ask first and your own proposals in "
        "this conversation, each with what it would do, why (the activity that caused it), when it expires and where "
        "the person decides it. One by id, or by status (pending, the default, or any), origin (automation or chat) or "
        "automation; give next as before for more. Only the person approves, in Lens or their chat app: you can list "
        "and decline.",
        {
            "id": _I,
            "status": {"type": "string", "enum": ["pending", "done", "failed", "declined", "expired", "any"]},
            "origin": {"type": "string", "enum": ["automation", "chat"]},
            "automation": _I,
            "before": _I,
            "limit": _I,
        },
        [],
        False,
    ),
    (
        "decline_approval",
        "Decline something waiting for the person's pick, by its id from approvals: nothing is done, and an "
        "automation's run records that it was declined.",
        {"id": _I},
        ["id"],
        False,
    ),
    (
        "automations",
        "The person's automations (when something happens in Lens, if a test passes, do something, as them): each "
        "one's triggers, whether it's on, its last run and what waits for their pick. With automation_id, one in full "
        "(its If, its graph, its policy) with its latest runs; with run_id, one run with what its actions did.",
        {"automation_id": _I, "run_id": _I},
        [],
        False,
    ),
    (
        "test_automation",
        "Try an automation without keeping, sending or counting anything: a saved one (automation_id) or a draft (what "
        "save_automation takes, with if for its condition), on an activity (its id, from timeline) or a kind's sample "
        "(default: its first trigger's kind). Says whether its If passes and what each action would do.",
        {"automation_id": _I, "draft": {"type": "object"}, "activity": _S, "kind": _S},
        [],
        False,
    ),
    (
        "read_transcript",
        "Read part of one asset's text (its transcript, for audio and video), by seconds.",
        {"recording_id": _I, "start_seconds": {"type": "number"}, "end_seconds": {"type": "number"}, "max_lines": _I},
        ["recording_id"],
        False,
    ),
    (
        "recording_outputs",
        "An asset's summary and saved outputs (such as meeting notes).",
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
        "Where an entity was mentioned: asset, time, person and the line.",
        {"entity_id": _I, "limit": _I},
        ["entity_id"],
        False,
    ),
    ("entity_timeline", "How often an entity was mentioned per month.", {"entity_id": _I}, ["entity_id"], False),
    (
        "find_dates",
        'What was said about a day or a range: dates said in assets, read as days on the calendar ("next Tuesday" '
        "in a recording is worked out from its date), each with its line. start and end are YYYY-MM-DD, YYYY-MM or YYYY.",
        {"start": _S, "end": _S, "namespace": _S, "recording_id": _I, "query": _S, "limit": _I},
        [],
        False,
    ),
    (
        "find_topics",
        "Find topics: each namespace's controlled vocabulary of what assets are about, with other labels, a "
        "definition, broader topics and how many assets are about each.",
        {"query": _S, "namespace": _S, "limit": _I},
        [],
        False,
    ),
    (
        "topic_recordings",
        "One topic: its broader, narrower and related topics and the assets about it (accepted, and suggested).",
        {"topic_id": _I},
        ["topic_id"],
        False,
    ),
    (
        "suggest_topic",
        "Suggest that assets are about a topic. It waits on the asset and the topic for someone to accept; "
        "nothing they accepted or dismissed changes.",
        {"topic_id": _I, "recording_ids": {"type": "array", "items": _I}},
        ["topic_id", "recording_ids"],
        True,
    ),
    (
        "graph_neighbours",
        "Who and what is connected to an entity or person in the knowledge graph.",
        {"entity_id": _I, "speaker_id": _I, "limit": _I},
        [],
        False,
    ),
    (
        "speaker_stats",
        "The people in a namespace (known individuals, recognised by voice or face) with their talk time and the assets they appear in.",
        {"namespace": _S},
        ["namespace"],
        False,
    ),
    (
        "find_notes",
        "Find notes (free notes and the pages of assets, entities, collections and people) in scope by words in "
        "their title, summary or text. Each has an id, title, one-line summary, where it's filed (PARA) and what it's "
        "the page of.",
        {"query": _S, "namespace": _S, "place": _S, "limit": _I},
        [],
        False,
    ),
    (
        "read_note",
        "Read a note's Markdown, with its links, the notes linking to it, the notes it's inside (path) and the notes "
        "inside it: by note_id, or the page of a thing "
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
        "note in the tree with parent_id (0: the top); read_note lists could_go_in, the projects and areas it fits under.",
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
        "What the graph holds (namespaces, collections, assets as Recording nodes, people as Speaker nodes, entities, topics and how they link), with example "
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
        "A node's parents, children, ancestors, descendants (asset, collection, namespace...) or neighbours. Nodes "
        "are n<id> namespaces, c<id> collections, r<id> assets, s<id> people, e<id> entities, t<id> topics.",
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
        "Run a template (such as meeting notes) on assets. Needs the person's approval.",
        {"template_id": _I, "recording_ids": {"type": "array", "items": _I}, "collection_id": _I},
        ["template_id"],
        True,
    ),
    (
        "write_spec",
        "Write a technical spec from an asset's transcript (a voice session, a meeting, a memo) as a note linked to it: "
        'every named decision, every no and every "not that, this", every constraint the speaker stated, in their own '
        "words with when they said them, and the gaps they left to decide later, flagged. It runs in the background with "
        "the Spec from transcript template; writing it again updates the same note. Needs the person's approval.",
        {"recording_id": _I},
        ["recording_id"],
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
        "retype it, describe it (a description and the other names it goes by), hide it, or define a new entity on a "
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
    (
        "propose_topic_change",
        "Propose a change to the topics, which needs the person's approval: merge other topics into a topic (their "
        "labels become its other labels, their assets are about it), rename it, describe it (a definition and its "
        "other labels), or delete it. Merges and deletes can be undone afterwards.",
        {
            "action": {"type": "string", "enum": ["merge", "rename", "describe", "delete"]},
            "topic_id": _I,
            "merge_ids": {"type": "array", "items": _I},
            "new_label": _S,
            "definition": _S,
            "other_labels": {"type": "array", "items": _S},
        },
        ["action", "topic_id"],
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

# automations (automations.py): saving one is made at once, switched off; switching one on or running it now acts, so
# it waits for the person's approval
AUTOMATION_TOOLS = [
    (
        "save_automation",
        "Make an automation for the person, or change one of theirs (automation_id): when something happens in Lens, "
        "if a test passes, do something, as them, in namespaces they edit. Saved switched off; enabled true then asks "
        "the person to switch it on. For a new one give name, triggers ([{as: name, on: event, kinds: [asset.added], "
        "where?: a match tree}] or [{as, on: manual}]) and graph (a workflow of scope event: an input node, then action "
        "nodes {id, type: action, config: {action: log_line or notify, params: {text: 'New: {{ event.object.name }}'}}}, "
        "and edges); condition is its If, a match tree over the run context ({} drops it). Try it with test_automation.",
        {
            "automation_id": _I,
            "name": _S,
            "description": _S,
            "triggers": {"type": "array", "items": {"type": "object"}},
            "condition": {"type": "object"},
            "graph": {"type": "object"},
            "namespaces": {"type": "array", "items": _S},
            "enabled": {"type": "boolean"},
        },
        [],
        True,
    ),
    (
        "run_automation",
        "Run one of the person's automations now, on an activity (its id, from timeline), else the newest one its "
        "triggers listen for. Needs the person's approval.",
        {"automation_id": _I, "activity": _S},
        ["automation_id"],
        True,
    ),
]


# a namespace's own assistant (ns_assistant.py), in conversations scoped to that namespace while it's on: remembering
# is routine and made at once (by people who edit the namespace), forgetting waits for the person's yes
MEMORY_TOOLS = [
    (
        "remember",
        "Keep a short fact about this namespace for later conversations: a decision, a preference, a plan or a fact "
        "people will ask about again. source_ref: the [n] of the excerpt it came from, so it stays cited to that moment.",
        {"fact": _S, "source_ref": _I},
        ["fact"],
        True,
    ),
    (
        "recall",
        "Find what you remember about this namespace beyond the memories you were given, by words.",
        {"query": _S, "limit": _I},
        ["query"],
        False,
    ),
    (
        "forget",
        "Forget something you remember (by its memory id from recall), when it's wrong or no longer true. Needs the person's approval.",
        {"memory_id": _I},
        ["memory_id"],
        True,
    ),
]


def builtin_names():
    """The assistant's own tool names, which extensions can't take."""
    every = (
        TOOLS
        + ops_tools.ADMIN_TOOLS
        + ops_tools.FILE_TOOLS
        + AUTHOR_TOOLS
        + MEMORY_TOOLS
        + AUTOMATION_TOOLS
        + browsermod.TOOLS
        + sandboxmod.TOOLS
        + researchmod.TOOLS
        + connection_tools.TOOLS
    )
    return {t[0] for t in every + webmod.TOOLS} | {"use_skill"}


# the built-in tools that carry what the model gives them to another host (the words searched for, the address read):
# once an answer has read text from outside Lens, they ask first (Toolbox.wary)
SENDS_OUT = set(webmod.NAMES)


# tools whose results the model may take as Lens's own word: the server's state and settings, the skills the person
# chose, what the graph holds, and what the built-in tools that change things report (ids, what waits for approval).
# What every other tool gives back is third-party text, marked as data (untrusted.py).
OWN_RESULTS = {
    "server_status",
    "find_model_servers",
    "read_settings",
    "file_storage",
    "graph_schema",
    "use_skill",
    "mark_activities",
    "decline_approval",
    "connections",
} | {
    t[0]
    for t in TOOLS
    + ops_tools.ADMIN_TOOLS
    + ops_tools.FILE_TOOLS
    + AUTHOR_TOOLS
    + MEMORY_TOOLS
    + AUTOMATION_TOOLS
    + sandboxmod.TOOLS
    + researchmod.TOOLS
    + connection_tools.TOOLS
    if t[4]
}

# The side panel's browser tools have their own guard (browser.py): the ones that act on a web site are labelled as
# sending data out (BROWSER_OUT) in the tool picker, and ask as browser.py says.
BROWSER_OUT = {"browser_open", "browser_click", "browser_type", "browser_press"}

# The groups of tools a question can be given (POST /chats/{cid}/messages `tools`; docs/assistant.md, "Choosing tools,
# namespaces and a limit"), in the order the web app shows them, each built-in tool in one: a new tool joins one here
# (test_chat_tools checks). Extensions add their own: mcp:<name> for an MCP server's tools, ext:<name> for another
# extension's (extensions.Active.groups).
GROUPS = {
    "archive": {
        "label": "Archive",
        "tools": [
            "search_transcripts",
            "find_resources",
            "find_similar",
            "list_recordings",
            "read_transcript",
            "recording_outputs",
            "classify",
        ],
    },
    "graph": {
        "label": "Graph",
        "tools": [
            "find_entities",
            "entity_mentions",
            "entity_timeline",
            "find_dates",
            "find_topics",
            "topic_recordings",
            "graph_neighbours",
            "speaker_stats",
            "graph_schema",
            "graph_query",
            "graph_related",
            "graph_paths",
        ],
    },
    "web": {"label": "The web", "tools": [t[0] for t in webmod.TOOLS]},
    "connected": {"label": "Mail, calendars and files", "tools": [t[0] for t in connection_tools.TOOLS]},
    "notes": {"label": "Notes", "tools": ["find_notes", "read_note", "write_note", "update_note", "write_spec"]},
    "memory": {"label": "Namespace memory", "tools": [t[0] for t in MEMORY_TOOLS]},
    "activity": {
        "label": "Timeline and automations",
        "tools": ["timeline", "mark_activities", "approvals", "decline_approval", "automations", "test_automation"]
        + [t[0] for t in AUTOMATION_TOOLS],
    },
    "changes": {
        "label": "Proposed changes",
        "tools": ["suggest_topic", "propose_entity_change", "propose_topic_change", "entity_setup", "run_template"],
    },
    "files": {"label": "Importing files", "tools": [t[0] for t in ops_tools.FILE_TOOLS]},
    "code": {"label": "Code sandbox", "tools": [t[0] for t in sandboxmod.TOOLS]},
    "research": {"label": "Deep research", "tools": [t[0] for t in researchmod.TOOLS]},
    "extensions": {"label": "Extensions and skills", "tools": [t[0] for t in AUTHOR_TOOLS] + ["use_skill"]},
    "server": {"label": "The server", "tools": [t[0] for t in ops_tools.ADMIN_TOOLS]},
    "browser": {"label": "Your browser", "tools": [t[0] for t in browsermod.TOOLS]},
}


def _effect(changes, out):
    """What a tool does, as the tool picker labels it: asks (it waits for the person's yes), out (it sends what it's
    given to another host) or read."""
    return "asks" if changes else "out" if out else "read"


def _why_missing(group, cfg, admin=False):
    """Why none of a built-in group's tools is offered to this person: in words, and the setting that would change it
    (for admins), if one would."""
    off = set(cfg["ai"].get("disabled_tools") or [])
    if group == "browser" and not browsermod.enabled(cfg):
        return "switched off by an admin", "ai.browser"
    if group == "web" and not webmod.enabled(cfg):
        return "no web search is set up", "web.search"
    if group == "code" and not sandboxmod.enabled(cfg):
        return "the code sandbox is off", "sandbox.enabled"
    if group == "code" and sandboxmod.settings(cfg)["who"] == "admins":
        return "for admins", "sandbox.who"
    if group == "research" and researchmod.unavailable(cfg):
        return researchmod.unavailable(cfg)
    if group == "extensions" and not cfg["ai"].get("extensions", True):
        return "switched off by an admin", "ai.extensions"
    if all(t in off for t in GROUPS[group]["tools"]):
        return "switched off by an admin", "ai.disabled_tools"
    if group == "server" or (group == "connected" and not admin):
        return "for admins", None
    if group == "connected":
        return "nothing is connected yet: add a connection in Settings › Sources", None
    if group == "memory":
        return "in a conversation about one namespace whose own assistant is on", None
    return "needs editor access to a namespace", None


def _group(name, label, tools, why=None, setting=None):
    return {
        "name": name,
        "label": label,
        "tools": tools,
        "asks": any(t["effect"] == "asks" for t in tools),
        "out": any(t["out"] for t in tools),
        "available": any(t["available"] for t in tools),
        "why": why,
        "setting": setting,
    }


def catalog(box):
    """The tool groups a question can be given, for the person `box` is for (GET /chats/tools): the built-in ones, then
    their extensions'. Each has its label and its tools: what each does (read, asks first or out: sends data to
    another host), whether it sends data out, and whether this person has it now. A group with none has why, and the
    setting that changes it. The browser's tools count as there while an admin allows them: a question has them while
    the browser's Lens side panel it's asked from is connected."""
    offered = {s["function"]["name"] for s in box.specs()}
    off = set(box.cfg["ai"].get("disabled_tools") or [])
    if browsermod.enabled(box.cfg):
        offered |= {t for t in GROUPS["browser"]["tools"] if t not in off}
    if sandboxmod.offered(box.cfg, box.admin, bool(box.editable)):  # every conversation of theirs has its sandbox
        offered |= {t for t in GROUPS["code"]["tools"] if t not in off}
    if researchmod.available(box.cfg):  # every conversation of theirs can start research
        offered |= {t for t in GROUPS["research"]["tools"] if t not in off}
    # code that can reach another host can carry out what it's given
    runs_out = {"run_python", "run_bash"} if sandboxmod.sends_out(box.cfg) else set()
    built = {
        t[0]: t[4]
        for t in TOOLS
        + ops_tools.ADMIN_TOOLS
        + ops_tools.FILE_TOOLS
        + AUTHOR_TOOLS
        + MEMORY_TOOLS
        + AUTOMATION_TOOLS
        + sandboxmod.TOOLS
        + webmod.TOOLS
        + researchmod.TOOLS
        + connection_tools.TOOLS
    }
    out = []
    for name, g in GROUPS.items():
        tools = []
        for t in g["tools"]:
            sends = t in SENDS_OUT or t in BROWSER_OUT or t in runs_out
            tools.append({"name": t, "effect": _effect(built.get(t, False), sends), "out": sends, "available": t in offered})
        why = () if any(t["available"] for t in tools) else _why_missing(name, box.cfg, box.admin)
        out.append(_group(name, g["label"], tools, *why))
    for name, g in box.ext.groups.items() if box.ext else ():
        tools = []
        for t in g["tools"]:
            spec = box.ext.tools[t]["spec"]
            sends = extensions.sends_out(spec)
            tools.append({"name": t, "effect": _effect(spec["effect"] == "change", sends), "out": sends, "available": t in offered})
        out.append(_group(name, g["label"], tools, None if any(t["available"] for t in tools) else "needs editor access to a namespace"))
    return out


def read_before(history, summary=None):
    """Whether earlier answers in a conversation read text from outside Lens (excerpts, or tools other than Lens's own),
    as a Toolbox's `outside`: the model reads those answers again, word for word or in the summary. After /clear it
    reads only what came since."""
    since = (summary or {}).get("upto") or 0 if summary is not None and not summary.get("text") else 0
    for m in history:
        if m["id"] <= since or m["role"] != "assistant":
            continue
        tools = {s.get("tool") for s in m.get("steps") or [] if s} - OWN_RESULTS - {None, "choose_namespace"}
        if m.get("passages") or tools:
            return ["earlier answers"]
    return []


class Toolbox(ops_tools.OpsTools, connection_tools.ConnectionTools):
    """`admin` adds the server tools (ops_tools.py), with `base` (archive.yaml's config) to save settings over; `act`
    makes their changes at once instead of asking for approval, `act_on` only those of these tools (a chat channel's:
    channels.Channel.acts). `browser`: the person's connected side panel (browser.py), whose browser the assistant can
    then use. The conversation's code sandbox (sandbox.py) is there when an admin turned it on and the person may use it,
    and an admin has the tools for the mail, calendars and files connected to Lens (connection_tools.py).
    `account`: the person an automation acts for, when `user` is the automation itself (automation:<id>,
    the by of what it writes): their approvals, extensions, view of the timeline and marks on it; `origin` ({automation, run,
    node}) is kept on the approvals it asks for. `only`: the groups (GROUPS, an extension's mcp:<name> or ext:<name>)
    and tools the question was given (None: every tool this person may use; an admin's switch still wins).
    `allowed_out`: tools that send data out of Lens and don't ask first even after the answer read outside text,
    because the person already approved them for this work (an approved research plan's web search, research.py)."""

    def __init__(
        self,
        db,
        cfg,
        user,
        readable,
        editable,
        scope,
        chat_id,
        base=None,
        admin=False,
        act=False,
        said="",
        outside=(),
        browser=None,
        act_on=(),
        account=None,
        origin=None,
        only=None,
        allowed_out=(),
    ):
        self.db, self.cfg, self.user, self.chat, self.said = db, cfg, user, chat_id, said
        self.only = None if only is None else set(only)
        self.allowed_out = set(allowed_out or ())
        self.account, self.origin = user["id"] if account is None else account, origin
        # where this answer has read text from outside Lens so far (`outside`: before any tool ran, such as the page it
        # was asked from or a chat room's message): once it has, tools that send data out of Lens or change the server
        # ask first, even where they'd act at once (wary)
        self.outside = list(dict.fromkeys(outside))
        self.links = set()  # web addresses the tools gave back, as written (untrusted.defang)
        self.detail = None  # what the last tool adds to its step for the person (a run's output, a file to download)
        self.base, self.admin, self.act, self.act_on = base or cfg, admin, act, set(act_on or ())
        self.scope = scope or {}
        self.readable, self.editable = set(readable), set(editable)
        names = store.space_names(db)
        if self.scope.get("namespaces"):
            self.readable = {s for s in self.readable if names.get(s) in self.scope["namespaces"]}
        self.allowed = recsets.within(db, self.readable, self.scope.get("recordings"), self.scope.get("collections"))
        self.refs, self.reads, self.approvals = [], 0, []
        self.flagged, self.failed = [], False  # tools whose results read like instructions to an AI; the last call failed
        # the extensions this person switched on or was given (extensions.py); hooks don't run inside hooks
        roles = {s: "editor" if s in self.editable else "viewer" for s in self.readable}
        self.me = extensions.who(self.account, user.get("email"), admin, roles)
        self.ext = extensions.Active(db, self.me) if cfg["ai"].get("extensions", True) else None
        self.hooking = False
        # the namespace's own assistant, when the conversation is about that namespace alone and its assistant is on
        home = ns_assistant.for_scope(db, self.scope)
        self.home = home if home and home[0] in self.readable else None
        # the browser the person is talking from, while its side panel is connected (and an admin hasn't turned it off)
        on = browser and browsermod.enabled(cfg) and browsermod.live(db, browser, user["id"])
        self.browser = str(browser) if on else None
        # Python and bash in the conversation's own sandbox, once an admin turned it on (sandbox.enabled, sandbox.who)
        self.sandbox = chat_id is not None and sandboxmod.offered(cfg, admin, bool(self.editable))
        self.web = webmod.enabled(cfg)  # web search and page reading, once an admin picked a provider (web.py)
        self.linked = connection_tools.offered(db, admin)  # the connections' tools, for the kinds connected (admins)

    def system_note(self):
        """What the model is told besides its usual instructions: the skills it can follow, and context that hooks add
        for this question."""
        out = self._home_note() + (browsermod.NOTE if self.browser else "")
        if self.sandbox:
            out += sandboxmod.NOTE.format(network=sandboxmod.network_note(self.cfg))
        given = {s["function"]["name"] for s in self.specs()}
        if self.web and webmod.NAMES & given:
            out += webmod.NOTE
        if self.linked & given:
            out += connection_tools.NOTE
        if not self.ext:
            return out
        out += self.ext.system_note()
        for h in self.ext.matching("message", text=self.said):
            out += self._hook(h, {"said": self.said})
        return out

    def _home_note(self):
        """Who the assistant is in this namespace and what it remembers; memories from a moment are numbered like
        excerpts. One from a recording the asker can't read is left out."""
        if not self.home:
            return ""
        sid, name, p = self.home
        rows = ns_assistant.labelled(self.db, ns_assistant.memories(self.db, sid, ns_assistant.PROMPT_MAX))
        if rows:
            self.read_outside("memory")
        spaces = {
            r["id"]: r["space"]
            for r in self.db.rows(
                "SELECT record::id(id) AS id, space FROM recording WHERE id IN $ids",
                ids=[R("recording", m["recording"]) for m in rows if m.get("recording") is not None],
            )
        }
        lines = []
        for m in rows:
            day = (m.get("created_at") or "")[:10]
            rid = m.get("recording")
            if rid is not None and rid in spaces:
                if spaces[rid] not in self.readable:
                    continue
                n = self.ref(rid, m.get("t0") or 0, m["text"], title=m.get("title"), source="memory")
                lines.append(f"[{n}] {m['text']} (memory {m['id']}, kept {day}, from {m.get('title') or 'an asset'} at {m.get('time')})")
            else:
                told = "told in a conversation" if m.get("author") == "assistant" else "written by a person"
                lines.append(f"- {m['text']} (memory {m['id']}, {told}, {day})")
        return ns_assistant.prompt(name, p, lines, self.cfg)

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
        if can_act or self.admin:
            tools += AUTOMATION_TOOLS
        if self.home:
            tools += [t for t in MEMORY_TOOLS if self.home[0] in self.editable or not t[4]]
        if self.browser:
            tools += browsermod.TOOLS
        if self.sandbox:
            tools += sandboxmod.TOOLS
        if self.web:
            tools += webmod.TOOLS
        if self.chat is not None and researchmod.available(self.cfg):
            tools += researchmod.TOOLS
        tools += [t for t in connection_tools.TOOLS if t[0] in self.linked]
        if self.ext:
            tools += self.ext.tool_specs(can_act or self.admin)
        keep = self.chosen()
        return [
            {
                "type": "function",
                "function": {"name": n, "description": d, "parameters": {"type": "object", "properties": p, "required": req}},
            }
            for n, d, p, req, _ in tools
            if n not in off and (keep is None or n in keep)
        ]

    def chosen(self):
        """The tool names `only` keeps (None: every tool): the tools it names, and those of the groups it names."""
        if self.only is None:
            return None
        groups = self.ext.groups if self.ext else {}
        out = set()
        for n in self.only:
            out |= set(GROUPS[n]["tools"] if n in GROUPS else groups[n]["tools"] if n in groups else [n])
        return out

    def unknown(self, names):
        """The names in a choice of tools that are no group or tool this person has (built-in, or of their extensions)."""
        known = builtin_names() | set(GROUPS) | set(self.ext.groups if self.ext else ()) | set(self.ext.tools if self.ext else ())
        return [n for n in names if n not in known]

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
            raise ValueError(f"asset {rid} isn't available in this conversation")
        return row

    def untrusted(self, name):
        """Whether what a tool gives back is text from outside Lens (recordings, documents, web pages, other servers),
        for the model to read as data."""
        return name not in OWN_RESULTS

    def read_outside(self, source):
        if source not in self.outside:
            self.outside.append(source)

    def wary(self):
        """Whether this answer has read text from outside Lens, so that what could carry its instructions out (a web
        tool, another server's tool) or change the server asks first (ai.untrusted_asks, on by default)."""
        return bool(self.outside) and self.cfg["ai"].get("untrusted_asks", True)

    def read_from(self):
        """What made this answer wary, in words: where it read text from outside Lens."""
        return f"this answer read text from outside Lens: {', '.join(self.outside[:4])}"

    def why(self):
        """Why something that would have run at once waits for a yes, said with it."""
        return f" (asked first: {self.read_from()})"

    def said_it(self, text):
        """Whether the person wrote `text` themselves in what they asked (never a chat room's message open to anyone),
        so it carries nothing the answer read elsewhere."""
        if "chat room" in self.outside:
            return False
        words = " ".join(str(text or "").split()).casefold()
        return bool(words) and words in " ".join(str(self.said or "").split()).casefold()

    def take_detail(self):
        """What the last tool added to its step for the person ({} when nothing), once."""
        got, self.detail = self.detail or {}, None
        return got

    def call(self, name, args, model=False):
        """A tool's result (JSON) and the step's summary. `model`: the result as the model reads it, third-party text
        marked as data (untrusted), with what hooks add after it, unmarked."""
        offered = {s["function"]["name"] for s in self.specs()}
        self.detail = None
        self.failed = True  # until it runs: a tool that's unknown or blocked failed
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
        asked = len(self.approvals)
        result, summary = self._run(name, fn, args)
        self.failed = result.startswith('{"error"')
        self.links |= untrusted.links(result)
        if self.untrusted(name) and len(self.approvals) == asked:  # it read something, rather than asking for a yes
            self.read_outside(f"tool {name}")
        if hooks:
            for h in self.ext.matching("after_tool", name, result):
                extra += self._hook(h, {"said": self.said, "tool": name, "result": result[:4000]})
        if model:
            if self.untrusted(name):
                why = untrusted.flags(result, self.cfg, model=True)
                if why:  # the person sees it on the step, the model on the marker, admins in the audit log
                    summary = f"{summary} (reads like instructions to an AI: {'; '.join(why)})"
                    self.flagged.append(name)
                    auth.audit(self.db, self.user, "assistant.injection_flagged", f"chat:{self.chat}", {"tool": name, "why": why})
                result = untrusted.wrap(result, f"tool {name}", self.cfg, why=why)
            return result + (f"\n\n{extra.strip()}" if extra else ""), summary
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
            change = t["spec"]["effect"] == "change"
            # a tool that reaches another host carries what the model put in its arguments there: after reading text
            # from outside Lens, which may have put it there, it asks first
            asks = change and not self.act
            wary = not asks and (change or extensions.sends_out(t["spec"])) and self.wary()
            if asks or wary:  # the reason said only where it asks because of what it read
                return self._approval(
                    "extension",
                    {"tool": t["name"], "extension": t["ext"], "version": t["version"], "args": args},
                    f"Run {what}" + (self.why() if wary else ""),
                )
            out = extensions.run_tool(self.db, self.cfg, t["spec"], args, toolbox=self)
            return out, f"Ran {what}"

        return run

    def t_use_skill(self, name, file=None):
        s = (self.ext.skills if self.ext else {}).get(name)
        if not s:
            raise ValueError(f"no skill {name}")
        files = s["spec"].get("files") or {}
        if file:
            if file not in files:
                raise ValueError(f"the skill {name} has no file {file}; it has: {', '.join(files) or 'none'}")
            return {"skill": name, "file": file, "text": files[file]}, f"Read {file} from the skill {name}"
        out = {"skill": name, "instructions": s["spec"]["instructions"]}
        if s["spec"].get("tools"):
            out["tools"] = s["spec"]["tools"]
        if files:
            out["files"] = list(files)
        return out, f"Read the skill {name}"

    # ---- the person's browser, through its Lens side panel (browser.py): the extension does each action ----
    def _browse(self, tool, **args):
        args = store.clean(args)
        # an address to open or download, or text to type, can carry what the answer read out of Lens: once it has read
        # text from outside Lens, which may have put it there, the browser asks the person first (`confirm`, why), on
        # sites they allowed too, unless they wrote it themselves
        risky = args.get("text") if tool == "type" else args.get("url") if tool in ("open", "import") else None
        if risky and self.wary() and not self.said_it(risky):
            args["confirm"] = self.read_from()
        try:
            out = browsermod.run(self.db, self.browser, self.user["id"], tool, args, chat=self.chat)
        except browsermod.BrowserError as e:
            raise ValueError(str(e)) from None
        return out, browsermod.summary(tool, args, out)

    def t_browser_tabs(self):
        return self._browse("tabs")

    def t_browser_open(self, url, tab_id=None, new_tab=None):
        return self._browse("open", url=str(url), tab_id=tab_id, new_tab=new_tab)

    def t_browser_read(self, tab_id=None, find=None, max_chars=None):
        return self._browse("read", tab_id=tab_id, find=find, max_chars=max_chars)

    def t_browser_click(self, ref, tab_id=None, consequential=None):
        return self._browse("click", ref=str(ref), tab_id=tab_id, consequential=consequential)

    def t_browser_type(self, ref, text, submit=None, tab_id=None, consequential=None):
        return self._browse("type", ref=str(ref), text=str(text), submit=submit, tab_id=tab_id, consequential=consequential)

    def t_browser_press(self, key, tab_id=None):
        return self._browse("press", key=str(key), tab_id=tab_id)

    def t_browser_import(self, ref=None, url=None, tab_id=None, page=None):
        if not (ref or url or page):
            raise ValueError("give a link's ref, a file's url, or page: true")
        out, said = self._browse("import", ref=ref, url=url, tab_id=tab_id, page=page)
        # the browser attached it as one of the person's held uploads: check it's theirs and waiting to be placed
        uid = str((out or {}).get("upload_id") or "")
        try:
            up = uploads.get(self.db, uid)
        except KeyError:
            raise ValueError("the browser didn't attach the file") from None
        if up.get("account") != self.user["id"] or up.get("state") != "held":
            raise ValueError("the browser didn't attach the file")
        attached = {"upload_id": uid, "filename": up["filename"], "size": up["size"], "next": "import_files puts it in a namespace"}
        return attached, f"Attached {up['filename']} from the browser"

    # ---- the conversation's code sandbox (sandbox.py): Python and bash on the OpenShell gateway ----
    def _sandbox(self, fn, *args):
        try:
            return fn(self.db, self.cfg, self.chat, *args)
        except sandboxmod.SandboxError as e:
            raise ValueError(str(e)) from None

    def _sandbox_run(self, kind, source, files):
        source = str(source or "")
        files = [str(f) for f in ([files] if isinstance(files, str) else files or [])]
        if sandboxmod.sends_out(self.cfg) and self.wary() and f"run_{kind}" not in self.allowed_out:
            # code that reaches another host can carry out what the answer read: after text from outside Lens, which
            # may have put it there, a run asks first
            first = next((x.strip() for x in source.splitlines() if x.strip()), "")[:80]
            args = store.clean({"code" if kind == "python" else "command": source, "files": files or None})
            lang = "Python" if kind == "python" else "bash"
            where = sandboxmod.reaches(self.cfg)
            return self._approval(f"run_{kind}", args, f"Run {lang} in the code sandbox, which can reach {where}: {first}" + self.why())
        out = self._sandbox(sandboxmod.run, self.account, kind, source, files)
        self.detail = sandboxmod.shown(out)  # the output under the code, on the step the person sees
        return out, sandboxmod.summary(kind, source, out)

    def t_run_python(self, code, files=None):
        return self._sandbox_run("python", code, files)

    def t_run_bash(self, command, files=None):
        return self._sandbox_run("bash", command, files)

    def t_sandbox_files(self):
        out = self._sandbox(sandboxmod.listing, self.account)
        n = len(out.get("files") or []) + int(out.get("more") or 0)
        return out, f"Looked at the sandbox's files ({n})"

    def t_share_sandbox_file(self, path):
        got = self._sandbox(sandboxmod.share, self.account, path)
        self.detail = {"file": got}  # a download on the answer
        return (
            {"shared": got["name"], "size": got["size"], "note": "the person downloads it from your answer"},
            f"Shared {got['name']} to download",
        )

    def t_import_sandbox_file(self, path, namespace=None):
        # out of the sandbox as one of the person's held uploads, then imported as a file they attached would be
        up = self._sandbox(sandboxmod.hold, self.user, path)
        return self.t_import_files([up["id"]], namespace)

    # ---- the web (web.py): searching it and reading pages, once an admin picked a search provider ----
    def _web(self, tool, args, what, own):
        """A web search or page read, as (what it found, None), or (None, the approval it waits for): the words or the
        address leave the machine, so once this answer has read text from outside Lens, which may have put them there,
        it asks first, unless the person wrote them (`own`), already approved the same call in this conversation, or
        approved this tool for the work it's doing (allowed_out: an approved research plan's)."""
        if own or not self.wary() or tool in self.allowed_out:
            return webmod.run(self.cfg, tool, args), None
        rows = (
            self.db.rows(
                "SELECT id, args, result FROM approval WHERE chat = $c AND account = $a AND tool = $t AND status = 'done' ORDER BY id DESC LIMIT 20",
                c=self.chat,
                a=self.account,
                t=tool,
            )
            if self.chat is not None
            else []
        )
        for a in rows:
            got = a.get("result")
            if webmod.same_call(tool, a.get("args"), args) and isinstance(got, dict) and "output" in got:
                return got["output"], None
        return None, self._approval(tool, args, what + self.why())

    def web_ref(self, url, title, site, text, fetched_at=None, source="web"):
        """A web page or search result to cite as [n] (web.py), or a connection's message or event (`source` mail or
        event, connection_tools.py: `fetched_at` is when it was sent or happens, and `site` the connection's name): its
        address instead of an asset's moment. The same address keeps its number, with the page's own text once it's
        read."""
        when = "fetched_at" if source == "web" else "recorded_at"
        for r in self.refs:
            if r.get("source") == source and r.get("url") == url:
                r.update(store.clean({"title": title or r.get("title"), "text": text or r.get("text"), when: fetched_at or r.get(when)}))
                return r["n"]
        n = len(self.refs) + 1
        self.refs.append(store.clean({"n": n, "source": source, "url": url, "title": title, "site": site, when: fetched_at, "text": text}))
        return n

    def t_web_search(self, query, limit=None, time_range=None):
        args = store.clean({"query": " ".join(str(query or "").split()), "limit": limit, "time_range": time_range})
        out, asked = self._web("web_search", args, f"Search the web for “{args['query'][:120]}”", self.said_it(args["query"]))
        if asked:
            return asked
        when = store.now()
        found = [
            {"ref": self.web_ref(r["url"], r.get("title"), r.get("site"), r.get("snippet") or r.get("title"), when), **r}
            for r in out["results"]
        ]
        return {"query": out["query"], "results": found}, webmod.summary("web_search", args, out)

    def t_web_read(self, url, find=None):
        args = store.clean({"url": str(url or "").strip(), "find": find})
        own = args["url"] in self.links or self.said_it(args["url"])  # an address a tool gave back carries nothing it read
        out, asked = self._web("web_read", args, f"Read the web page {webcapture.placeholder(args['url'])[:160]}", own)
        if asked:
            return asked
        excerpt = " ".join(out.get("text", "").split())[:600]
        return {"ref": self.web_ref(out["url"], out.get("title"), out.get("site"), excerpt, out.get("fetched_at")), **out}, webmod.summary(
            "web_read", args, out
        )

    # ---- deep research (research.py): a plan to approve, then a run in the background ----
    def t_start_research(self, goal):
        goal = " ".join(str(goal or "").split())
        if not goal:
            raise ValueError("give the goal to research")
        if self.chat is None:
            raise ValueError("research runs in a conversation")
        model = researchmod.model_for(self.cfg, (self.db.one("SELECT model FROM $r", r=R("chat", self.chat)) or {}).get("model"))
        r = researchmod.propose(self.db, self.cfg, self, goal, model)
        out = {
            "status": "waiting for the person's approval",
            "research": r["id"],
            "approval": r["approval"],
            "plan": r["plan"],
            "questions": r["questions"],
            "estimate": r["estimate"],
        }
        return out, f"Planned {researchmod.summary(r['plan'])}; it waits for the person's approval"

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

    # ---- the namespace's memory (ns_assistant.py) ----
    def t_remember(self, fact, source_ref=None):
        sid, name, _ = self.home
        rid = t0 = None
        if source_ref is not None:
            n = int(source_ref)
            if not 1 <= n <= len(self.refs):
                raise ValueError(f"there's no excerpt [{n}] in this conversation")
            r = self.refs[n - 1]
            rid, t0 = r["recording_id"], r.get("t0")
        mid = ns_assistant.remember(self.db, sid, fact, self.user["id"], rid, t0, self.chat)
        return {"memory_id": mid, "remembered": fact}, f"Remembered: {fact[:80]}"

    def t_recall(self, query, limit=10):
        sid, name, _ = self.home
        rows = ns_assistant.labelled(self.db, ns_assistant.recall(self.db, sid, query, min(int(limit or 10), 30)))
        out = []
        for m in rows:
            item = {"memory_id": m["id"], "fact": m["text"], "kept": (m.get("created_at") or "")[:10]}
            if m.get("recording") is not None:
                try:
                    self._ok(m["recording"])
                except ValueError:
                    continue
                item["ref"] = self.ref(m["recording"], m.get("t0") or 0, m["text"], title=m.get("title"), source="memory")
            out.append(item)
        return {"memories": out}, f'Recalled "{query}": {len(out)} memor{"y" if len(out) == 1 else "ies"}'

    def t_forget(self, memory_id):
        sid, name, _ = self.home
        m = ns_assistant.get(self.db, sid, int(memory_id))
        return self._approval("forget_memory", {"namespace": name, "memory_id": m["id"]}, f"Forget “{m['text'][:120]}”")

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
        return json.dumps(out, ensure_ascii=False, default=str)[: self._most(name)], summary

    def _most(self, name):
        """The most of a tool's result the model reads: a web page read takes what web.read_chars allows."""
        return max(12000, int(webmod.read_chars(self.cfg) * 1.2) + 4000) if name == "web_read" else 12000

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

    def t_find_similar(self, text=None, recording_id=None, topic_id=None, entity_id=None, limit=5):
        if recording_id is not None:
            self._ok(recording_id)
        try:
            res = similar.similar(
                self.db,
                self.cfg,
                text=text,
                recording=recording_id,
                topic=topic_id,
                entity=entity_id,
                spaces=self.readable,
                within=self.allowed,
                limit=min(int(limit or 5), 20),
            )
        except KeyError:
            raise ValueError("that topic or entity isn't available in this conversation") from None
        out = []
        for x in res["items"]:
            moments = []
            for c in x["cites"]:
                said = html.unescape(c["snippet"].replace("<mark>", "").replace("</mark>", ""))
                n = self.ref(x["recording_id"], c["t0"] or 0, said, title=x["title"], source=c["source"], page=c["page"])
                moments.append(
                    {"ref": n, **({"page": c["page"] + 1} if c["page"] is not None else {"time": store.tc(c["t0"] or 0)}), "text": said}
                )
            out.append(
                store.clean(
                    {
                        "recording_id": x["recording_id"],
                        "title": x["title"],
                        "namespace": x["namespace"],
                        "alike_by": x["match"],
                        "moments": moments,
                        "words": x["words"] or None,
                        "topics": [t["label"] for t in x["topics"]] or None,
                        "entities": [e["name"] for e in x["entities"]] or None,
                    }
                )
            )
        like = res["label"] or ""
        return {"like": like, "by": res["mode"], "total": res["total"], "results": out}, f'Found {res["total"]} like "{like[:60]}"'

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
        return {"total": len(ids), "recordings": out}, f"Listed {len(ids)} asset(s)"

    def _viewer(self):
        # admins see what is for admins alone too, unless the conversation is about some namespaces
        return events.viewer(self.account, self.readable, admin=bool(self.admin) and not self.scope.get("namespaces"))

    def _about_allowed(self, a):
        """Whether an activity is about the assets of a conversation about some assets (any, in one that isn't)."""
        return self.allowed is None or events.ref_id((a.get("object") or {}).get("id"), "recording") in self.allowed

    def t_timeline(self, kinds=None, namespace=None, tag=None, since=None, until=None, q=None, unreviewed=None, before=None, limit=20):
        names = store.space_names(self.db)
        spaces = self.readable
        if namespace:
            spaces = {s for s in self.readable if names.get(s) == namespace}
            if not spaces:
                raise ValueError(f"namespace {namespace} isn't in this conversation")
        flt = {"kinds": kinds or None, "tag": tag, "since": since, "until": until, "q": q, "spaces": sorted(spaces) if namespace else None}
        keep = event_marks.keeper(self.db, self.account, {"reviewed": False} if unreviewed else None)
        page = events.page(self.db, self.cfg, self._viewer(), before, {k: v for k, v in flt.items() if v}, min(int(limit or 20), 50), keep)
        items = [a for a in page["items"] if self._about_allowed(a)]  # a conversation about some assets: only what is about them
        marks = event_marks.of(self.db, self.account, [a["id"] for a in items])
        out = [
            {
                "id": a["id"],
                "kind": a["kind"],
                "published": a["published"],
                "summary": a["summary"],
                "actor": a["actor"]["name"],
                "object": a["object"],
                "namespace": names.get(a.get("space")),
                **({"marked": [f for f in event_marks.FLAGS if marks[a["id"]][f]]} if a["id"] in marks else {}),
            }
            for a in items
        ]
        return {"activities": out, "next": page["next"]}, f"Read {len(out)} activit{'y' if len(out) == 1 else 'ies'} from the timeline"

    def t_mark_activities(self, ids, reviewed=None, important=None, hidden=None):
        flags = event_marks.check({"reviewed": reviewed, "important": important, "hidden": hidden})
        ids = [str(i) for i in ([ids] if isinstance(ids, str) else ids or [])]
        if not ids or len(ids) > event_marks.MAX_IDS:
            raise ValueError(f"give the ids of 1 to {event_marks.MAX_IDS} activities, from timeline")
        seen = {i: a for i, a in events.get_many(self.db, self.cfg, ids, self._viewer()).items() if self._about_allowed(a)}
        known, unknown = [i for i in dict.fromkeys(ids) if i in seen], [i for i in dict.fromkeys(ids) if i not in seen]
        said = ", ".join(f"{'' if v else 'not '}{k}" for k, v in flags.items())
        what = f"{len(known)} activit{'y' if len(known) == 1 else 'ies'} {said}"
        if not known:
            return {"marks": {}, "unknown": unknown}, "Marked no activities: none of those can be seen here"
        # what it read from outside Lens (the timeline's own titles too) may have asked it to take things out of the
        # person's sight: hiding them or marking them reviewed asks first then; the other marks only bring things back
        if self.wary() and (flags.get("hidden") or flags.get("reviewed")):
            return self._approval("mark_activities", {"ids": known, **flags}, f"Mark {what}" + self.why())
        marks = event_marks.mark(self.db, self.account, known, flags)
        return {"marks": marks, "unknown": unknown}, f"Marked {what}"

    # ---- approvals (approvals.py): the person's own, read and declined; approving is theirs ----
    def _approval_seen(self, a):
        """An approval of the person's this conversation may see: an automation's about a namespace it reads (or none),
        or one the assistant asked for here."""
        if not a or a.get("account") != self.account:
            return False
        if a.get("origin") or a.get("intent"):
            return a.get("space") is None or a["space"] in self.readable
        return a.get("chat") == self.chat

    def t_approvals(self, id=None, status=None, origin=None, automation=None, before=None, limit=20):
        from . import approvals

        web = approvals.link(self.cfg, self.db, 0).removesuffix("/approvals?id=0")
        if id is not None:
            a = approvals.get(self.db, int(id))
            if not self._approval_seen(a):
                raise ValueError(f"no approval {id} of the person's here")
            return {"approval": approvals.brief(a, web)}, f"Read the approval “{a.get('summary')}”"
        n = max(1, min(int(limit or 20), 50))
        st = None if status == "any" else status or "pending"
        rows = approvals.listing(self.db, self.account, st, origin, automation, None, before, n)
        out = [approvals.brief(a, web) for a in rows if self._approval_seen(a)]
        nxt = rows[-1]["id"] if len(rows) >= n else None
        return {"approvals": out, "next": nxt}, f"Read {len(out)} approval{'' if len(out) == 1 else 's'}"

    def t_decline_approval(self, id):
        from . import approvals

        a = approvals.get(self.db, int(id))
        if not self._approval_seen(a):
            raise ValueError(f"no approval {id} of the person's here")
        if a["status"] != "pending":
            raise ValueError(f"approval {id} is {a['status']}, not waiting")
        # what it read from outside Lens may have asked it to throw away what the person would pick: it asks first then
        if self.wary():
            return self._approval("decline_approval", {"approval": a["id"]}, f"Decline “{a.get('summary')}”" + self.why())
        out = approve(self.db, self.cfg, a["id"], self.user, self.editable, "decline", via="agent")
        return {"approval": a["id"], "status": out.get("status")}, f"Declined “{a.get('summary')}”"

    # ---- automations (automations.py) ----
    def _automation(self, automation_id):
        """One of the person's automations (ValueError when there's none they manage)."""
        try:
            row = automationsmod.get(self.db, int(automation_id))
        except KeyError:
            row = None
        me = {"id": self.user["id"], "admin": bool(self.admin)}
        if not row or not automationsmod.manages(row, automationsmod.editable(self.db, me), bool(self.admin)):
            raise ValueError(f"there's no automation {automation_id} of yours")
        return row

    @staticmethod
    def _automation_line(a):
        return store.clean(
            {
                "automation_id": a["id"],
                "name": a["name"],
                "enabled": a["enabled"],
                "triggers": a["triggers"],
                "last_run_at": a.get("last_run_at"),
                "last_status": a.get("last_status"),
                "waiting": a.get("waiting") or None,
            }
        )

    def t_automations(self, automation_id=None, run_id=None):
        if run_id is not None:
            r = automationsmod.get_run(self.db, int(run_id))
            self._automation(r["automation"])
            return r, f"Read run {run_id} of automation {r['automation']}"
        if automation_id is not None:
            a = self._automation(automation_id)
            a["runs"] = automationsmod.runs(self.db, a["id"], limit=5)
            return a, f"Read automation {a['name']}"
        me = {"id": self.user["id"], "admin": bool(self.admin)}
        rows = automationsmod.list_automations(self.db, None if self.admin else automationsmod.editable(self.db, me))
        out = [self._automation_line(a) for a in automationsmod.extras(self.db, rows)]
        return {"automations": out}, f"Listed {len(out)} automation(s)"

    def t_test_automation(self, automation_id=None, draft=None, activity=None, kind=None):
        if (automation_id is None) == (draft is None):
            raise ValueError("give automation_id or draft")
        a = None
        if activity:
            a = events.get(self.db, self.cfg, str(activity), self._viewer())
            if a is None:
                raise ValueError(f"there's no activity {activity} you can see")
        elif kind:
            try:
                a = events.kind_info(kind)["sample"]
            except KeyError:
                raise ValueError(f"there's no kind {kind} (such as asset.added)") from None
        if automation_id is not None:
            got = automationsmod.test(self.db, self.cfg, self._automation(automation_id)["id"], activity=a)
        else:
            got = automationsmod.test(self.db, self.cfg, draft={**dict(draft), "enabled": False}, activity=a, user=self.user)
        return {k: got[k] for k in ("status", "why", "intents", "log", "error")}, f"Tried it: {got['status']}"

    def t_save_automation(
        self, automation_id=None, name=None, description=None, triggers=None, condition=None, graph=None, namespaces=None, enabled=None
    ):
        names = {v: k for k, v in store.space_names(self.db).items()}
        d = {"name": name, "description": description, "triggers": triggers, "graph": graph}
        d = {k: v for k, v in d.items() if v is not None}
        if namespaces:
            unknown = [n for n in namespaces if n not in names]
            if unknown:
                raise ValueError(f"there's no namespace {unknown[0]}")
            d["spaces"] = [names[n] for n in namespaces]
        if condition is not None:
            d["if"] = condition or None
        if automation_id is None:
            aid = automationsmod.create(self.db, self.cfg, {**d, "enabled": False}, self.user)["id"]
            what = f"Saved automation {d.get('name')}, switched off"
        else:
            row = self._automation(automation_id)
            aid = row["id"]
            if d:
                automationsmod.update(self.db, self.cfg, aid, d, self.user["email"])
            if enabled is False:
                automationsmod.enable(self.db, self.cfg, aid, False, self.user["email"])
            what = f"Saved automation {row['name']}"
        row = automationsmod.get(self.db, aid)
        out = {"automation_id": aid, "enabled": row["enabled"]}
        if enabled and not row["enabled"]:
            kinds = sorted({k for t in row["triggers"] for k in t.get("kinds") or []})
            does = sorted(_actions_in(row["graph"]))
            on = f"when {', '.join(kinds)} happens" if kinds else "when it's run by hand"
            asked, _ = self._approval(
                "switch_automation",
                {"automation_id": aid, "name": row["name"]},
                f"Switch on automation {row['name']}: {on}, it does {', '.join(does)}",
            )
            out["switching_on"] = asked
        return out, what

    def t_run_automation(self, automation_id, activity=None):
        row = self._automation(automation_id)
        if activity and events.get(self.db, self.cfg, str(activity), self._viewer()) is None:
            raise ValueError(f"there's no activity {activity} you can see")
        on = f" on {activity}" if activity else ""
        return self._approval("run_automation", {"automation_id": row["id"], "activity": activity}, f"Run automation {row['name']} now{on}")

    def t_find_resources(self, namespace=None, limit=10, **spec):
        spaces = sorted(self.readable)
        if namespace:
            spaces = [s for s in spaces if store.space_names(self.db).get(s) == namespace]
            if not spaces:
                raise ValueError(f"namespace {namespace} isn't available in this conversation")
        res = findmod.find(self.db, self.cfg, spaces, None, spec, limit=min(int(limit or 10), 25), ids=self.allowed)
        out = []
        for x in res["items"]:
            why = []
            for w in x["why"]:
                moments = []
                for m in w.get("moments") or []:
                    text = m.get("text") or f"{w['name']} ({w['by']})"
                    n = self.ref(
                        x["id"], m.get("t0") or 0, text, m.get("speaker"), x.get("title"), m.get("source") or "said", m.get("page")
                    )
                    where = {"page": m["page"] + 1} if m.get("page") is not None else {"time": store.tc(m.get("t0"))}
                    moments.append(store.clean({"ref": n, **where, "text": m.get("text")}))
                why.append(store.clean({"by": w["by"], "name": w.get("name"), "count": w.get("count"), "moments": moments or None}))
            out.append(
                store.clean(
                    {
                        "recording_id": x["id"],
                        "title": x.get("title"),
                        "date": (x.get("recorded_at") or "")[:10] or None,
                        "type": x.get("media_kind"),
                        "namespace": x.get("namespace"),
                        "speakers": x.get("speakers") or None,
                        "why": why,
                    }
                )
            )
        return {"total": res["total"], "resolved": res["resolved"] or None, "results": out}, f"Found {res['total']} asset(s)"

    def t_classify(self, recording_id=None, text=None, labels=None, vocabulary=None, namespace=None, question=None):
        space = None
        if recording_id is not None:
            self._ok(recording_id)
        elif namespace:
            space = self._space(namespace)
        try:
            res = classifymod.classify(
                self.db, self.cfg, rid=recording_id, text=text, labels=labels, vocab=vocabulary, question=question, space=space
            )
        except decide.Undecided as e:
            raise ValueError(f"nothing could answer: {e}") from None
        out = {k: res[k] for k in ("choice", "confidence", "sure", "ranked", "by", "key", "id") if res.get(k) is not None}
        return out, f"Classified as {res['choice']} ({round(res['confidence'] * 100)}% sure)"

    def t_read_transcript(self, recording_id, start_seconds=0, end_seconds=None, max_lines=60):
        cap = self.cfg["ai"].get("max_transcript_reads") or 20
        if self.reads >= cap:
            raise ValueError(f"the limit of {cap} text reads per question is reached; answer with what you have")
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
                "page": wiki.path(e["namespace"], e["name"]),
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

    def t_find_dates(self, start=None, end=None, namespace=None, recording_id=None, query=None, limit=10):
        spaces = {s for s in self.readable if namespace is None or store.space_names(self.db).get(s) == namespace}
        res = dates.said(
            self.db,
            spaces,
            day_from=dates.day_bound(start),
            day_to=dates.day_bound(end, last=True),
            recording=recording_id,
            q=(query or "").strip() or None,
            limit=200,
        )
        out = [
            {
                "ref": self.ref(d["recording_id"], d.get("t0") or 0, d.get("text") or d["said"], d.get("speaker"), d.get("title")),
                "day": d["day"] if d["until"] == d["day"] else f"{d['day']} to {d['until']}",
                "said": d["said"],
                **({"worked_out_from_the_asset_date": d.get("recorded_at")} if d.get("relative") else {}),
                "title": d.get("title"),
                "time": d.get("time"),
                "text": d.get("text"),
            }
            for d in res["items"]
            if self.allowed is None or d["recording_id"] in self.allowed
        ][: min(int(limit), 30)]
        if not out and not (self.cfg.get("analysis") or {}).get("dates"):
            return {"dates": [], "note": "reading dates is off (Settings → Analysis → Read dates)"}, "Dates aren't read here"
        return {"dates": out}, f"Found {len(out)} date(s)"

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
        return {"suggested_for": made, "note": "waiting for someone to accept"}, f"Suggested {row['label']} for {len(made)} asset(s)"

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
        return {"speakers": out}, f"Read the people in {namespace}"

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
                    "account": self.account,
                    "origin": self.origin,
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
        return self._approval("run_template", {"template_id": t["id"], "recordings": ids}, f"Run {label} on {len(ids)} asset(s)", est)

    def t_write_spec(self, recording_id):
        row = self._ok(recording_id)
        if row["space"] not in self.editable:
            raise ValueError(f"asset {recording_id} isn't yours to write a spec for")
        if not llm.configured(self.cfg):
            raise ValueError("no language model is configured (Settings → LLM provider)")
        est = batches.estimate(self.db, self.cfg, [int(recording_id)], [{"type": "spec"}])
        title = row.get("title") or f"asset {recording_id}"
        return self._approval("write_spec", {"recording_id": int(recording_id)}, f"Write a spec from {title}", est)

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
        total, hits = notebook.find(self.db, spaces, query, place, min(int(limit or 10), 30))
        out = [
            store.clean(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "summary": r.get("summary"),
                    "place": r.get("place"),
                    "page_of": r.get("about"),
                    "namespace": names.get(r["space"]),
                    "inside": r.get("parent"),
                    "snippet": r.get("snippet"),
                }
            )
            for r in hits
        ]
        return {"total": total, "notes": out}, f"Found {total} note(s)"

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
                "path": [{"id": x["id"], "title": x["title"]} for x in notebook.path(self.db, p)] or None,
                "notes_inside": [{"id": x["id"], "title": x["title"]} for x in notebook.children(self.db, p)] or None,
                "could_link": [f"{x['sign']}[{x['label']}]({x['target']})" for x in notebook.suggest_links(self.db, p)] or None,
                "files": [{"name": f.get("name"), "type": f["type"], "size": f["size"]} for f in notebook.files(self.db, p["id"])] or None,
                "could_go_in": [
                    {"parent_id": x["page"], "title": x["title"], "place": x["place"], "why": x["why"]}
                    for x in notebook.suggest_homes(self.db, p)
                ]
                or None,
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
            + (f" (also known as {', '.join(also_said_as)})" if also_said_as else ""),
            "hide": f"Hide {e['name']}",
        }[action]
        return self._approval("propose_entity_change", {**args, "entity_id": e["id"]}, what)

    def t_propose_topic_change(self, action, topic_id, merge_ids=None, new_label=None, definition=None, other_labels=None):
        needs = {
            "merge": ("merge_ids", merge_ids),
            "rename": ("new_label", new_label),
            "describe": ("definition or other_labels", definition is not None or other_labels is not None),
            "delete": ("topic_id", True),
        }
        if action not in needs:
            raise ValueError("action is one of " + ", ".join(needs))
        if not needs[action][1]:
            raise ValueError(f"{action} needs {needs[action][0]}")
        t = self.db.one("SELECT record::id(id) AS id, space, label FROM $r", r=R("topic", int(topic_id)))
        if not t or t["space"] not in self.readable:
            raise ValueError(f"no topic {topic_id} in scope")
        if t["space"] not in self.editable:
            raise ValueError("changing topics needs editor access to the namespace")
        others = []
        if action == "merge":
            ids = [int(i) for i in dict.fromkeys(merge_ids) if int(i) != t["id"]]
            rows = self.db.rows("SELECT record::id(id) AS id, space, label FROM topic WHERE id IN $ids", ids=[R("topic", i) for i in ids])
            if not ids or len(rows) != len(ids) or any(o["space"] != t["space"] for o in rows):
                raise ValueError("merge_ids are other topics of the same namespace")
            others = [o["label"] for o in rows]
        args = {
            "action": action,
            "topic_id": t["id"],
            "merge_ids": merge_ids,
            "new_label": new_label,
            "definition": definition,
            "other_labels": other_labels,
        }
        what = {
            "merge": f"Merge {', '.join(others)} into the topic {t['label']}",
            "rename": f"Rename the topic {t['label']} to {new_label}",
            "describe": f"Describe the topic {t['label']}"
            + (f" as “{definition}”" if definition else "")
            + (f" (also {', '.join(other_labels)})" if other_labels else ""),
            "delete": f"Delete the topic {t['label']}",
        }[action]
        return self._approval("propose_topic_change", store.clean(args), what)

    def cited(self, text):
        import re

        used = {int(n) for n in re.findall(r"\[(\d+)\]", text or "")}
        return [{**r, "used": r["n"] in used} for r in self.refs if r["n"] in used]


def approve(db, cfg, aid, user, editable, decision="approve", base=None, admin=False, readable=None, params=None, via="web"):
    """Carry out an approved action: a batch run (or a sample of it), an entity change, a note an automation's agent
    proposed, or a change to the server (ops_tools.py: settings, a namespace, importing attached files or web pages,
    following a news feed).
    An automation's action (an approval with an intent) is decided by approvals.decide: `params` are an edit's, `via`
    where the pick was made (web, chat, token, oauth or agent; only the person, signed in or in their own chat,
    approves it)."""
    from . import approvals  # it carries actions out, and actions load the assistant's tools

    a = approvals.get(db, aid)
    if not a or a["status"] != "pending":
        raise ValueError("nothing to approve")
    if a["tool"] == "research":  # a research plan: approved (as it is, or with `params` its edits) or declined
        return researchmod.decide(db, cfg, a, user, decision, params, via)
    if via == "agent" and decision != "decline":
        raise PermissionError("approving is the person's own pick: an agent can only decline")
    if a.get("intent"):
        return approvals.decide(db, cfg, a, user, decision, params, via)
    if decision in ("edit", "always"):
        raise ValueError("only an automation's action can be edited or allowed from now on")
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
    elif a["tool"] in ("switch_automation", "run_automation"):
        result = _automation_approved(db, cfg, a["tool"], args, user, admin)
    elif a["tool"] in ("run_python", "run_bash"):  # a run that can reach another host, asked first (Toolbox._sandbox_run)
        if a.get("chat") is None or not sandboxmod.offered(cfg, admin, bool(editable)):
            raise PermissionError("the code sandbox is off, or not yours to use")
        kind = a["tool"].removeprefix("run_")
        source = args.get("code") if kind == "python" else args.get("command")
        try:
            result = {"result": sandboxmod.run(db, cfg, a["chat"], a.get("account") or user["id"], kind, source, args.get("files") or [])}
        except sandboxmod.SandboxError as e:
            raise ValueError(str(e)) from None
    elif a["tool"] in (
        "change_settings",
        "create_namespace",
        "import_files",
        "import_urls",
        "invite_person",
        "change_person",
        "subscribe_feed",
        "unsubscribe_feed",
    ):
        result = ops_tools.apply(db, cfg, base or cfg, a["tool"], args, user, editable, admin)
    elif a["tool"] == "decline_approval":  # the person's yes to declining another of their approvals
        from . import approvals

        b = approvals.get(db, int(args["approval"]))
        if not b or b.get("account") != a.get("account"):
            raise ValueError("that approval is gone")
        if b["status"] != "pending":
            result = {"approval": b["id"], "status": b["status"]}  # decided meanwhile: nothing to decline
        else:
            result = {"approval": b["id"], **approve(db, cfg, b["id"], user, editable, "decline", via="agent")}
    elif a["tool"] == "mark_activities":  # the person's own marks, on activities they could see when it was asked
        flags = {k: v for k, v in args.items() if k in event_marks.FLAGS}
        result = {"marked": len(event_marks.mark(db, user["id"], args["ids"], flags))}
    elif a["tool"] == "import_from_connection":
        result = connection_tools.apply(db, cfg, args, user, admin)
    elif a["tool"] in webmod.NAMES:  # the person's yes to a search or page read after the answer read outside text
        result = {"output": webmod.run(cfg, a["tool"], args)}
    elif a["tool"] in ("write_note", "update_note"):  # proposed by an automation's agent (actions_ai.Box)
        box = Toolbox(db, cfg, user, readable if readable is not None else editable, editable, None, a["chat"], base, admin)
        result = getattr(box, "t_" + a["tool"])(**args)[0]
    elif a["tool"] == "forget_memory":
        sid = {v: k for k, v in store.space_names(db).items()}.get(args["namespace"])
        if sid not in editable:
            raise PermissionError("you can't change what that namespace's assistant remembers")
        ns_assistant.forget(db, sid, args["memory_id"])
        result = {"forgot": args["memory_id"]}
    elif a["tool"] == "write_spec":
        rid = int(args["recording_id"])
        if (db.one("SELECT space FROM $r", r=R("recording", rid)) or {}).get("space") not in editable:
            raise PermissionError("you can't write a spec for that asset")
        try:
            result = {"job": specs.queue(db, cfg, rid, by=user["email"])}
        except specs.Problem as e:
            raise ValueError(str(e)) from None
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
    elif a["tool"] == "propose_topic_change":
        result = _topic_change(db, args, user, editable)
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


def _topic_change(db, args, user, editable):
    """An approved change to the topics; a merge or delete says how to undo it."""
    tid = int(args["topic_id"])
    if (db.one("SELECT space FROM $r", r=R("topic", tid)) or {}).get("space") not in editable:
        raise PermissionError("you can't change topics in that namespace")
    act, by = args["action"], user["email"]
    if act == "merge":
        cid = topics.merge(db, tid, args.get("merge_ids") or [], user=by)
        return {"topic": tid, "undo": cid}
    if act == "delete":
        _, cid = topics.delete(db, tid, user=by)
        return {"deleted": tid, "undo": cid}
    if act == "rename":
        topics.update(db, tid, label=args["new_label"], user=by)
    else:
        topics.update(db, tid, alt=args.get("other_labels"), definition=args.get("definition"), user=by)
    return {"topic": tid}


def _actions_in(graph):
    """The actions an event workflow's nodes take, bodies included."""
    out = set()
    for n in (graph or {}).get("nodes") or []:
        cfg = n.get("config") or {}
        if n.get("type") == "action" and cfg.get("action"):
            out.add(str(cfg["action"]))
        if isinstance(cfg.get("body"), dict):
            out |= _actions_in(cfg["body"])
    return out


def _automation_approved(db, cfg, tool, args, user, admin):
    """An approved switch-on or run of an automation, if the person still manages it."""
    aid = int(args["automation_id"])
    me = {"id": user["id"], "admin": bool(admin)}
    try:
        row = automationsmod.get(db, aid)
    except KeyError:
        row = None
    if not row or not automationsmod.manages(row, automationsmod.editable(db, me), bool(admin)):
        raise PermissionError("that automation was deleted, or isn't yours to change any more")
    if tool == "switch_automation":
        automationsmod.enable(db, cfg, aid, True, user["email"])
        return {"automation": aid, "enabled": True}
    act = None
    if args.get("activity"):
        viewer = automationsmod._viewer(db, automationsmod._account(db, user["id"]) or {"id": user["id"]})
        act = events.get(db, cfg, args["activity"], viewer)
        if act is None:
            raise PermissionError("that activity is gone, or isn't one you can see any more")
    return automationsmod.run_now(db, cfg, aid, user, activity=act)


def run_extension(db, cfg, args, user, admin, readable, toolbox=None):
    """An approved extension tool: the version that was proposed, if it's still on and still the person's to use."""
    g = extensions.get(db, int(args["extension"]), args.get("version"))
    me = extensions.who(user["id"], user.get("email"), admin, {s: "viewer" for s in readable})
    if g.get("deleted_at") or not g.get("enabled") or not extensions.can_use(g, me):
        raise ValueError("that extension was switched off or removed")
    t = next((it for it in extensions.tool_items(g) if it["name"] == args["tool"]), None)
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


# last, because automations imports the actions, and the agent's action (actions_ai.Box) builds on Toolbox above
from . import automations as automationsmod  # noqa: E402
