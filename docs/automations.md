# Automations

Automations are the workflow studio: Lens follows what happens in every source as a stream of time-coded activities,
and an automation watches that stream and the clock, tests its conditions, and acts (directly for routine things,
through the agent, or by asking you first). The timeline is the same stream, filtered and reviewed.

Status: in progress. The studio is built in modules; this page labels each part built or planned, and only what is
merged is called built.

| Part | Status |
| --- | --- |
| Match trees: one condition language for triggers, an automation's If and workflow nodes | built |
| Activities, the kinds catalog, the event log, following the stream, timeline reads (`app/domain/events.py`) | built |
| New assets as `asset.added` (the `recordings` source) | built |
| More kinds: emails, files, calendar events, jobs, deletions, comments, notes, graph changes, sensors, chat | planned |
| The timeline API, MCP and assistant tools | planned |
| Automations, triggers, joins, schedules, actions, approvals, variables, the timeline page, the studio | planned |

## Contract

Every part of the studio builds on the shapes below. A change to one of them comes in a small pull request of its
own that changes the code, this section and the tests together.

### Activities

Built (`app/domain/events.py`). Everything that happens in Lens, from every source, is an activity: a plain dict in
the shape of a [W3C Activity Streams 2.0](https://www.w3.org/TR/activitystreams-core/) activity, with a Lens `kind`
that triggers subscribe to. `events.check_activity(a)` checks it, naming the first problem.

```json
{"id": "create-recording-7", "type": "Create", "kind": "asset.added", "published": "2026-10-15T08:12:03.000Z",
 "actor": {"type": "Person", "id": "lens:account/1", "name": "Sam"},
 "object": {"type": "Audio", "id": "lens:recording/7", "name": "Call with the school", "url": "/resources/7",
            "media": "audio"},
 "target": null, "tag": [], "summary": "Sam added “Call with the school”", "space": 1,
 "lens": {"source": "recordings", "pos": ["2026-10-15T08:12:03+00:00", 7], "untrusted": ["object.name"],
          "cause": null}}
```

- `id`: stable, made from what the source reads (`create-recording-7`, `log-12`), so an activity is the same every
  time it is read: 1 to 120 lowercase letters, digits and `_ . : -`.
- `type`: the AS2 verb (Create, Update, Delete, Add, Remove, Join, Leave, Arrive, Announce, Like, Accept, Reject),
  which the kind decides.
- `published`: when it happened, ISO 8601 in UTC with milliseconds, so times from every source sort as text.
- `actor`: who did it, `{type, id, name}`. Sources and storage are `Service`, people `Person`, Lens itself, the agent
  and automations `Application` (`lens:automation/4`; Lens is `lens:app`). Entities are never actors; they go in
  `tag`.
- `object`: what it is about, with the kind's own fields. Ids are `lens:<table>/<id>` references, so actions can act
  on them; `events.ref_id(ref, "recording")` reads the id back.
- `target`: where to, when the verb has one (Add to a collection), else null. `tag`: up to 50 objects such as
  `{"type": "Mention", "id": "lens:entity/9", "name": "St Mary's"}`.
- `summary`: one line of at most 300 characters, made by fixed rules, never by a model.
- `space`: the namespace id, or null for activities of no namespace.
- `lens`: Lens's own fields. `source` and `pos` (the source's position, `[time, id]`) are for the stream machinery.
  `untrusted` lists the dotted paths holding outside text (titles, email bodies, webhook payloads): prompts wrap
  them with `untrusted.wrap`. `cause` is `{automation, run, depth}` when an automation's action caused it. `spaces`
  lists every namespace when there are several. `audience` narrows who may see it: `space` (the default: people who
  read one of its namespaces), `owner` (only the account in `lens.owner`) or `admins`.

### Kinds

Built. `events.kind(name, *, label, source, as_type, object_type, parent=None, fields=None, untrusted=(),
sample=None, noisy=False)` registers a kind: a name like `noun.past_verb`, a label for people, the source that makes
it, its AS2 verb and object type, a parent (a trigger on the parent matches it too), JSON Schemas for its `object`
(and `actor` or `target`) in `fields`, the paths that hold outside text, a realistic sample (checked when it is
registered), and whether it is `noisy` (left out of the timeline unless named). `events.kinds()` lists them,
`events.kind_info(name)` gives one with its children and its whole `schema` (every activity's fields, its parents',
then its own), `events.descendants(name)` the kind and every kind under it, and `events.field_at(kind, path)` the
schema of one field, whose `title` names it in words (`match.describe(tree, kind)` says "the title contains “call”").

| Kind | Parent | Source | Object |
| --- | --- | --- | --- |
| `asset.added` | | `recordings` | `{type: Audio, Video, Image or Document, id: lens:recording/<id>, name: its title, url: /resources/<id>, media: audio, video, image, document or transcript, content_type}` |

### Sources and the event log

Built. A source (`events.Source`, added with `events.register_source`) reads rows Lens already keeps as activities,
so nothing is stored twice:

- `read(db, cfg, pos, spaces, limit, hint)`: activities strictly after `pos` (`[time, id]`, or `[time, null]` for
  after every row at that time), oldest first, at most `limit`, of the namespaces in `spaces` (null: all). `hint`
  is narrowing a source may use.
- `page(db, cfg, before, viewer, flt, limit)`: newest first, strictly before `before` (`key` of the last one shown),
  with what the viewer may see and the filters it can apply cheaply (the stream checks every filter again).
- `get(db, cfg, activity_id)`: one activity by its id, or null; `now(db)`, `back(db, seconds)`: positions of now and of
  `seconds` ago; `key(a)`: the position `page` orders by.

A kind belongs to the source named in its `source`, and a source may list more in `kinds`. Two are built:

- `recordings` (`app/domain/events_content.py`) reads `recording` by `created_at`, the id breaking ties, as
  `asset.added`. The actor is whoever added it: an automation (its first job was queued by `automation:<id>`), the
  storage source a watched file came from (`Service`, `lens:source/<id>`), the person whose upload it was (`Person`,
  `lens:account/<id>`), or Lens (a folder scan).
- `log` reads `event_log`: activities no row of their own records (a deletion, a webhook call), written by
  `events.emit(db, kind, *, actor, obj, space, summary, target=None, tag=None, key=None, published=None, spaces=None,
  audience=None, owner=None, untrusted=None)`, which gives the id (`log-<n>`). Emitting a `key` again changes
  nothing and gives the first id, so a retry never doubles an activity. Inside `with events.caused_by({"automation":
  4, "run": 51, "depth": 1}):` what is emitted carries that cause. It is read by `(logged_at, id)` and paged by
  `published`, and its rows are kept for `events.keep_days` (30).

### Following the stream

Built. The API and `lens worker` are separate processes on one database, so following works the way notifications
do: `events.follow(db, cfg, consumer, kinds=None, spaces=None, limit=500, hint=None)` gives `(activities, commit)`,
the activities since the consumer's last commit, oldest first. Handle them in order, then call `commit()` to save
where the next look starts (`event_cursor:<consumer>`, a position per source).

- A consumer seen for the first time starts at now: nothing that happened before replays, not even through the
  lookback. What happens in the second it starts in isn't seen.
- Only `kinds` (and their descendants; null: every kind) in `spaces` (null: every namespace and none). A source that
  makes none of the kinds wanted isn't read, and its cursor moves to now, so a kind listened to later doesn't replay
  a backlog. `events.idle(db, consumer)` moves every cursor to now.
- A source with more waiting gives `limit` at a time. Once caught up, a look also reads the last 60 seconds again,
  for a row written as the last look ran, minus what this process already handled. So claim each activity before
  acting on it: `events.claim(db, consumer, key)` is true for the one process, and the one time, that claims `key`
  (an activity's id, say), with a `CREATE` of `event_claim:<hash>` only one process wins. `events.unclaimed(db,
  consumer, keys)` lists the keys nobody has claimed yet, in one query. Claims go after a day.
- `events.emit` wakes the followers in its process; a follower waits between looks with `g = events.generation()`
  before the look and `events.wait(events.poll_seconds(cfg), stop, since=g)` after it (`events.poll_seconds`, 5).

### Timeline reads

Built. `events.page(db, cfg, viewer, before=None, flt=None, limit=50)` reads the stream newest first for a viewer
(`events.viewer(account, spaces, admin)`, spaces null for every one): `{"items": [...], "next": ...}`, where `next`
(an opaque cursor, null at the end) goes in `before` for the page after. It merges every source's `page`, applies
who may see what, and when filters thin a page out it reads on, up to five pages from each source.
`events.get(db, cfg, id, viewer)` reads one activity, null when there is none or it isn't the viewer's to see.

- Who sees what: `lens.audience` `owner` is its owner alone; `admins` is admins; `space` is everyone who reads one of
  its namespaces (`space` or `lens.spaces`); admins read every namespace, and an activity of no namespace is for
  admins. People who were given only some collections of a namespace don't see its activities yet.
- Filters: `kinds` (with their descendants; a noisy kind only when named), `actor` (an actor's id, or several),
  `spaces` (namespace ids), `tag` (an entity's id or name), `since` (published after) and `until` (at or before),
  `q` (words in the summary) and `where` (a match tree on the activity, below).
- `events.tidy(db, cfg)` drops event log rows past `events.keep_days` and claims past a day; the routine scheduler
  runs it once an hour.

### Match trees

Built (`app/domain/match.py`). One condition language for a trigger's `where`, an automation's `if`, and the
condition, filter and switch nodes of a workflow:

```
M := {"all": [M, …]} | {"any": [M, …]} | {"not": M} | {"path": "a.b.0", "op": OP, "value": V}
```

- A test (`{path, op, value}`) reads the part of the value at a dotted path, list items by number
  (`object.from.0`); no path tests the value itself. The tests are the workflow engine's: `exists`, `empty`,
  `equals`, `not_equals`, `contains` (text in any case, or an item of a list), `gt` and `lt` (numbers).
- `all` holds when every condition in it does (an empty `all` always holds), `any` when one does (it needs at least
  one), `not` when its condition doesn't.
- Values are literals, never templates, so matching stays cheap and safe.
- A tree nests at most 6 deep and holds at most 50 tests. A problem is named with where it is: `the condition,
  all.1.not: compare with a number`.
- `match.describe(tree)` reads a tree in words: `object.from contains “@stmarys.org” and (object.subject contains
  “trip” or object.subject contains “outing”)`.

In a workflow, a condition, filter or switch case takes either one test, as before, or a tree in `match`; with a
tree, `path` (when given) is the part of the value the tree tests. Graphs saved before keep running unchanged.

```json
{"id": "school", "type": "condition",
 "config": {"path": "object", "match": {"all": [{"path": "from.0", "op": "contains", "value": "@stmarys.org"},
                                                {"not": {"path": "attachments", "op": "gt", "value": 5}}]}}}
```

## Refine later

- More operators: `in`, `matches` (bounded regular expressions), `between`, times against now, `changed`.
- The canvas shows and edits match trees on condition, filter and switch nodes (today it edits one test; a tree
  saved through the API is kept).
- A `?format=as2` JSON-LD view and per-actor outboxes for ActivityPub; SurrealDB `DEFINE EVENT` for Update verbs that
  no row records; pushing instead of polling.
- People given only some collections of a namespace see that namespace's activities about those collections.
- Name an automation actor by its automation's name once automations are stored.
